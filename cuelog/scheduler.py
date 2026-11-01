"""DJ-set timelines and scrobble timestamp planning.

All timestamps are UTC epoch seconds. Local-time conversion uses the operating
system's timezone rules (``time.mktime`` / ``datetime.fromtimestamp``) so
daylight-saving changes are handled without an IANA database.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

LASTFM_MAX_AGE_DAYS = 14   # documented: older timestamps are ignored (code 3)
MIN_SEGMENT_S = 30         # Last.fm ignores tracks of 30 seconds or less


class TimestampError(ValueError):
    pass


@dataclass
class Segment:
    set_track_id: int | None
    position: int
    start_s: int
    end_s: int
    artist: str           # Last.fm artist (first credited artist)
    track: str
    album: str
    song_duration_s: int | None
    scrobblable: bool
    reason: str = ""

    @property
    def length(self) -> int:
        return self.end_s - self.start_s


@dataclass
class PlannedScrobble:
    segment: Segment
    timestamp: int


# ---- timelines -----------------------------------------------------------------------------
def estimate_offsets(count: int, duration_s: int, known: dict[int, int] | None = None,
                     weights: list[float] | None = None) -> list[int]:
    """Start offsets for ``count`` tracks in a mix of ``duration_s`` seconds.

    ``known`` maps 0-based index -> fixed start (from a timestamped tracklist or
    manual edits). Unknown starts between two anchors are spread in proportion
    to ``weights`` (e.g. the recordings' lengths), which only estimates order of
    magnitude: a mix rarely plays a full recording.
    """
    if count <= 0:
        return []
    known = dict(known or {})
    known.setdefault(0, 0)
    weights = [w if w and w > 0 else 1.0 for w in (weights or [1.0] * count)]
    anchors = sorted(known.items())
    offsets: list[float | None] = [None] * count
    for i, s in anchors:
        offsets[i] = s
    bounds = anchors + [(count, duration_s)]
    for (i0, s0), (i1, s1) in zip(bounds, bounds[1:]):
        span = max(0, s1 - s0)
        w = weights[i0:i1]
        total = sum(w) or 1
        acc = 0.0
        for k in range(i0, i1):
            offsets[k] = s0 + span * acc / total if k != i0 else s0
            acc += weights[k]
    return [int(round(o)) for o in offsets]   # type: ignore[arg-type]


def timeline_problems(starts: list[int], duration_s: int | None) -> list[str]:
    problems = []
    if starts and starts[0] != 0:
        problems.append("The first track should start at 0:00")
    for i, (a, b) in enumerate(zip(starts, starts[1:]), 2):
        if b < a:
            problems.append(f"Track {i} must not start before track {i - 1}")
    if duration_s and starts and starts[-1] >= duration_s:
        problems.append("The last track starts after the end of the mix")
    return problems


def segments_from_rows(rows: list[dict], duration_s: int) -> list[Segment]:
    """rows: ordered set_tracks joined with song metadata (see repo.tracks_for_set)."""
    segs = []
    for i, r in enumerate(rows):
        start = int(r["start_s"] or 0)
        # Songs that start together (layered / mashed up) share the stretch until the next new start.
        end = next((int(n["start_s"]) for n in rows[i + 1:] if n["start_s"] is not None and int(n["start_s"]) > start),
                   max(duration_s, start + 1))
        artist, track, album = r.get("lf_artist") or "", r.get("lf_track") or "", r.get("lf_album") or ""
        ok, reason = True, ""
        if r.get("match_status") in ("unmatched", "ambiguous"):
            ok, reason = False, "Match not reviewed"
        elif r.get("match_status") == "no_match":
            ok, reason = False, "Marked as not scrobbled"
        elif not artist or not track:
            ok, reason = False, "Missing artist or title"
        elif r.get("duration_ms") and r["duration_ms"] <= MIN_SEGMENT_S * 1000:
            # Last.fm's 30-second rule is about the recording's length, not how long it plays in a mix.
            ok, reason = False, "Recording is 30 s or shorter"
        segs.append(Segment(r.get("id"), r["position"], start, end, artist, track, album,
                            int(r["duration_ms"] / 1000) if r.get("duration_ms") else None, ok, reason))
    return segs


# ---- sessions ------------------------------------------------------------------------------
def plan_listen(segments: list[Segment], start_ts: int, first: int | None = None, last: int | None = None) -> list[PlannedScrobble]:
    """A continuous listen beginning at ``start_ts`` with track ``first`` (positions, inclusive)."""
    chosen = [s for s in segments if (first is None or s.position >= first) and (last is None or s.position <= last)]
    if not chosen:
        return []
    base = chosen[0].start_s
    return _distinct([PlannedScrobble(s, start_ts + (s.start_s - base)) for s in chosen if s.scrobblable])


def _distinct(planned: list[PlannedScrobble]) -> list[PlannedScrobble]:
    """Layered songs start together; give each scrobble its own second so none collide on Last.fm."""
    out, last = [], None
    for p in sorted(planned, key=lambda p: (p.timestamp, p.segment.position)):
        if last is not None and p.timestamp <= last:
            p = PlannedScrobble(p.segment, last + 1)
        out.append(p)
        last = p.timestamp
    return out


def listen_span(segments: list[Segment], first: int | None = None, last: int | None = None) -> int:
    chosen = [s for s in segments if (first is None or s.position >= first) and (last is None or s.position <= last)]
    return (chosen[-1].end_s - chosen[0].start_s) if chosen else 0


def plan_from_player(segments: list[Segment], intervals: list[tuple[float, float, float]],
                     min_fraction: float = 0.5) -> list[PlannedScrobble]:
    """Scrobbles from in-app playback.

    ``intervals``: (mix_start_s, mix_end_s, wall_clock_start_epoch) for each stretch
    that actually played. A segment counts when at least ``min_fraction`` of it
    (or 4 minutes) was heard; skipped-over portions never count.
    """
    out = []
    for seg in segments:
        first_wall = None
        parts = []
        for a, b, wall in sorted(intervals, key=lambda x: x[2]):
            lo, hi = max(a, seg.start_s), min(b, seg.end_s)
            if hi > lo:
                parts.append((lo, hi))
                if first_wall is None:
                    first_wall = wall + (lo - a)
        # replaying the same part twice must not count twice
        heard, cur_lo, cur_hi = 0.0, None, None
        for lo, hi in sorted(parts):
            if cur_hi is None or lo > cur_hi:
                if cur_hi is not None:
                    heard += cur_hi - cur_lo
                cur_lo, cur_hi = lo, hi
            else:
                cur_hi = max(cur_hi, hi)
        if cur_hi is not None:
            heard += cur_hi - cur_lo
        need = max(min(seg.length * min_fraction, 240), min(5, seg.length))
        if first_wall is not None and heard >= need and seg.scrobblable:
            out.append(PlannedScrobble(seg, int(first_wall)))
    return _distinct(out)


def eligibility(ts: int, now: int, max_age_days: float, buffer_s: int = 0) -> str | None:
    if ts > now:
        return "future"
    if ts < now - int(max_age_days * 86400):
        return "too_old"
    return None


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


# ---- local time helpers --------------------------------------------------------------------
def local_epoch(d: date, hhmm: str | tuple[int, int]) -> int:
    h, m = (map(int, hhmm.split(":")) if isinstance(hhmm, str) else hhmm)
    if h == 24:
        return int(time.mktime((d + timedelta(days=1)).timetuple()))
    return int(time.mktime(datetime(d.year, d.month, d.day, h, m).timetuple()))


def fmt_local(ts: int | None, fmt: str = "%a %d %b %Y, %H:%M") -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts)).strftime(fmt)


def local_date(ts: int) -> date:
    return datetime.fromtimestamp(int(ts)).date()


# ---- backfill ------------------------------------------------------------------------------
def window_days(now: int, max_age_days: float) -> list[date]:
    """Calendar days (local) that fall entirely or partly inside the accepted window."""
    first = local_date(now - int(max_age_days * 86400) + 3600)
    last = local_date(now)
    days, d = [], first
    while d <= last:
        days.append(d)
        d += timedelta(days=1)
    return days


def day_capacity(days: list[date], *, now: int, max_age_days: float, day_start: str, day_end: str,
                 buffer_s: int = 120) -> dict[date, int]:
    """Seconds of allowed listening time per day (inside the hours and Last.fm's window)."""
    earliest = now - int(max_age_days * 86400) + 600
    latest_end = now - buffer_s
    out = {}
    for d in days:
        lo = max(local_epoch(d, day_start), earliest)
        hi = min(local_epoch(d, day_end), latest_end)
        out[d] = max(0, hi - lo)
    return out


def free_capacity(days: list[date], *, now: int, max_age_days: float, day_start: str, day_end: str,
                  buffer_s: int = 120, busy: list[tuple[int, int]] | None = None) -> dict[date, int]:
    """Like day_capacity, minus the time already taken by other listens."""
    cap = day_capacity(days, now=now, max_age_days=max_age_days, day_start=day_start, day_end=day_end, buffer_s=buffer_s)
    earliest = now - int(max_age_days * 86400) + 600
    out = {}
    for d, c in cap.items():
        lo = max(local_epoch(d, day_start), earliest)
        hi = min(local_epoch(d, day_end), now - buffer_s)
        taken = sum(max(0, min(be, hi) - max(bs, lo)) for bs, be in (busy or []))
        out[d] = max(0, c - taken)
    return out


def distribute_evenly(n: int, days: list[date], capacity: dict[date, int] | None = None,
                      duration_s: int | None = None) -> dict[date, int]:
    """Spread ``n`` sessions over ``days``; with ``capacity`` in proportion to each day's free hours
    (and never more sessions than fit on a day)."""
    if not days or n <= 0:
        return {d: 0 for d in days}
    if not capacity:
        counts = {d: 0 for d in days}
        for i in range(n):
            counts[days[int((i + 0.5) * len(days) / n)]] += 1
        return counts
    # one minute of slack per session for snapping start times to whole minutes
    limit = {d: (capacity.get(d, 0) // (duration_s + 60) if duration_s else n) for d in days}
    total = sum(capacity.get(d, 0) for d in days if limit[d] > 0) or 1
    shares = {d: n * capacity.get(d, 0) / total if limit[d] > 0 else 0 for d in days}
    counts = {d: min(limit[d], int(shares[d])) for d in days}
    # Hand out the remainder spread across the window (not bunched at the start), largest remainders first.
    for _ in range(3):
        left = n - sum(counts.values())
        open_days = [d for d in days if counts[d] < limit[d]]
        if left <= 0 or not open_days:
            break
        frac = {d: shares[d] - int(shares[d]) for d in open_days}
        cutoff = sorted(frac.values(), reverse=True)[min(left, len(open_days)) - 1] - 1e-9
        eligible = [d for d in open_days if frac[d] >= cutoff]       # includes every tie, in date order
        picks = [eligible[int((i + 0.5) * len(eligible) / left)] for i in range(min(left, len(eligible)))]
        for d in picks:
            counts[d] += 1
    return counts


def backfill_starts(counts: dict[date, int], duration_s: int, *, now: int, max_age_days: float,
                    day_start: str = "10:00", day_end: str = "23:30", buffer_s: int = 120,
                    busy: list[tuple[int, int]] | None = None, grid_s: int = 60) -> list[int]:
    """Non-overlapping session start times, ``counts[day]`` sessions per local day.

    Every session ends before ``now - buffer_s``, starts inside the accepted
    window, and avoids ``busy`` intervals (existing sessions).
    """
    busy = sorted(busy or [])
    earliest = now - int(max_age_days * 86400) + 600
    latest_end = now - buffer_s
    starts: list[int] = []
    for d in sorted(counts):
        k = counts[d]
        if k <= 0:
            continue
        lo = max(local_epoch(d, day_start), earliest)
        hi = min(local_epoch(d, day_end), latest_end)
        if hi - lo < duration_s * k:
            raise TimestampError(
                f"{d:%a %d %b}: not enough listening hours for {k} session(s) of {duration_s // 60} min "
                f"between {day_start} and {day_end} inside Last.fm's accepted window.")
        # leave one grid step of headroom so snapping to whole minutes never pushes past the end
        step = (hi - grid_s - lo - duration_s) / (k - 1) if k > 1 else 0
        cursor = lo
        for i in range(k):
            target = int(lo + i * step) if k > 1 else int(lo + (hi - lo - duration_s) / 2)
            t = max(target, cursor)
            t = ((t + grid_s - 1) // grid_s) * grid_s
            while True:
                clash = next(((bs, be) for bs, be in busy if overlaps(t, t + duration_s, bs, be)), None)
                if not clash:
                    break
                t = ((clash[1] + grid_s - 1) // grid_s) * grid_s
            if t + duration_s > hi:
                raise TimestampError(f"{d:%a %d %b}: existing sessions leave no room for {k} more listen(s).")
            starts.append(t)
            busy.append((t, t + duration_s))
            busy.sort()
            cursor = t + duration_s
    return starts


def latest_free_start(duration_s: int, *, now: int, buffer_s: int, busy: list[tuple[int, int]]) -> int:
    """The most recent start such that [start, start+duration] ends before now and avoids busy."""
    t = now - buffer_s - duration_s
    for bs, be in sorted(busy, key=lambda x: -x[1]):
        if overlaps(t, t + duration_s, bs, be):
            t = bs - duration_s - 60
    return t
