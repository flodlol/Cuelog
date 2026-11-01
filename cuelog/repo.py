"""Data access: sets, tracklists, songs, sessions and scrobbles."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid as uuidlib
from dataclasses import dataclass

from . import db, scheduler
from .matching import Candidate, norm, query_key
from .scheduler import PlannedScrobble, Segment, TimestampError

PENDING_STATES = ("pending", "queued", "submitting", "uncertain")
PROBLEM_STATES = ("ignored", "failed", "expired")


def _uuid() -> str:
    return uuidlib.uuid4().hex


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


# ---- songs -----------------------------------------------------------------------------------
def upsert_song(c: Candidate, con=None) -> int:
    con = con or db.get()
    artists = json.dumps([a for a in c.artists if a], ensure_ascii=False)
    raw = json.dumps(c.raw, ensure_ascii=False) if c.raw else None
    if c.spotify_id:
        row = con.execute("SELECT id FROM songs WHERE spotify_id = ?", (c.spotify_id,)).fetchone()
        if row:
            con.execute("""UPDATE songs SET title=?, artists_json=?, album=COALESCE(NULLIF(?, ''), album),
                           duration_ms=COALESCE(?, duration_ms), release_date=COALESCE(NULLIF(?, ''), release_date),
                           url=?, art_url=COALESCE(NULLIF(?, ''), art_url), art_source=COALESCE(NULLIF(?, ''), art_source),
                           isrc=COALESCE(?, isrc), source='spotify' WHERE id=?""",
                        (c.title, artists, c.album, c.duration_ms, c.release_date, c.url, c.art_url, c.art_source,
                         c.isrc, row[0]))
            return row[0]
    elif c.mbid:
        row = con.execute("SELECT id FROM songs WHERE mbid = ? AND spotify_id IS NULL", (c.mbid,)).fetchone()
        if row:
            return row[0]
    cur = con.execute(
        """INSERT INTO songs(source, spotify_id, mbid, isrc, title, artists_json, album, duration_ms, release_date,
                             url, art_url, art_source, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (c.source, c.spotify_id, c.mbid, c.isrc, c.title, artists, c.album, c.duration_ms, c.release_date,
         c.url, c.art_url, c.art_source or ("none" if not c.art_url else ""), raw))
    return cur.lastrowid


def get_song(song_id: int) -> dict | None:
    r = _row(db.get().execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone())
    if r:
        r["artists"] = json.loads(r["artists_json"] or "[]")
    return r


def update_song_art(song_id: int, art_url: str, source: str) -> None:
    db.get().execute("UPDATE songs SET art_url = ?, art_source = ? WHERE id = ?", (art_url, source, song_id))


def songs_missing_art() -> list[dict]:
    return [dict(r) for r in db.get().execute(
        "SELECT * FROM songs WHERE (art_url IS NULL OR art_url = '') AND spotify_id IS NOT NULL")]


# ---- sets ------------------------------------------------------------------------------------
def create_set(*, title: str, source_url: str | None = None, uploader: str = "", artwork_url: str = "",
               duration_s: int | None = None, description: str = "", tracklist_raw: str = "",
               raw_meta: dict | None = None, kind: str = "track", legacy_ref: str | None = None, con=None) -> int:
    con = con or db.get()
    if not (title or "").strip():
        raise ValueError("A set needs a title")
    with db.tx(con):
        resource_id = None
        if source_url:
            con.execute(
                """INSERT INTO sc_resources(url, kind, title, uploader, artwork_url, duration_ms, description, raw_json)
                   VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,
                   uploader=excluded.uploader, artwork_url=excluded.artwork_url,
                   duration_ms=COALESCE(excluded.duration_ms, sc_resources.duration_ms),
                   description=COALESCE(NULLIF(excluded.description, ''), sc_resources.description),
                   raw_json=excluded.raw_json, fetched_at=datetime('now')""",
                (source_url, kind, title, uploader, artwork_url, duration_s * 1000 if duration_s else None,
                 description, json.dumps(raw_meta or {}, ensure_ascii=False)))
            resource_id = con.execute("SELECT id FROM sc_resources WHERE url = ?", (source_url,)).fetchone()[0]
        cur = con.execute(
            """INSERT INTO sets(uuid, resource_id, source_url, title, uploader, artwork_url, duration_s, description,
                                tracklist_raw, legacy_ref) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (_uuid(), resource_id, source_url, title.strip(), uploader, artwork_url, duration_s, description,
             tracklist_raw, legacy_ref))
        return cur.lastrowid


def find_set_by_url(url: str) -> dict | None:
    return _row(db.get().execute("SELECT * FROM sets WHERE source_url = ?", (url,)).fetchone())


SET_STATS_SQL = """
SELECT s.*,
  (SELECT COUNT(*) FROM set_tracks t WHERE t.set_id = s.id) AS track_count,
  (SELECT COUNT(*) FROM set_tracks t WHERE t.set_id = s.id AND t.match_status IN ('unmatched','ambiguous')) AS review_count,
  (SELECT COUNT(*) FROM sessions x WHERE x.set_id = s.id AND x.cancelled = 0) AS listen_count,
  (SELECT MAX(COALESCE(x.real_started_at, CASE WHEN x.reconstructed = 0 THEN x.started_at END))
     FROM sessions x WHERE x.set_id = s.id AND x.cancelled = 0) AS last_listened,
  (SELECT COUNT(*) FROM scrobbles c JOIN sessions x ON x.id = c.session_id
     WHERE x.set_id = s.id AND c.status = 'accepted') AS accepted_count,
  (SELECT COUNT(*) FROM scrobbles c JOIN sessions x ON x.id = c.session_id
     WHERE x.set_id = s.id AND x.cancelled = 0 AND c.status IN ('pending','queued','submitting','uncertain')) AS pending_count
FROM sets s
"""


def get_set(set_id: int) -> dict | None:
    return _row(db.get().execute(SET_STATS_SQL + " WHERE s.id = ?", (set_id,)).fetchone())


def list_sets(search: str = "", sort: str = "recent", favorites_only: bool = False, needs_review: bool = False) -> list[dict]:
    rows = [dict(r) for r in db.get().execute(SET_STATS_SQL)]
    if search:
        q = norm(search)
        rows = [r for r in rows if q in norm(f"{r['title']} {r['uploader'] or ''}")]
    if favorites_only:
        rows = [r for r in rows if r["favorite"]]
    if needs_review:
        rows = [r for r in rows if r["review_count"]]
    keys = {
        "recent": lambda r: -(r["last_listened"] or 0),
        "added": lambda r: r["created_at"],
        "title": lambda r: norm(r["title"]),
        "listens": lambda r: -r["listen_count"],
        "duration": lambda r: -(r["duration_s"] or 0),
    }
    rows.sort(key=keys.get(sort, keys["recent"]))
    if sort == "added":
        rows.reverse()
    return rows


def update_set(set_id: int, **fields) -> None:
    allowed = {"title", "uploader", "artwork_url", "duration_s", "description", "tracklist_raw", "favorite", "notes",
               "source_url"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Cannot update {bad}")
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.get().execute(f"UPDATE sets SET {sets}, updated_at = datetime('now') WHERE id = ?", (*fields.values(), set_id))
    if "duration_s" in fields:
        recompute_offsets(set_id)


def delete_set(set_id: int) -> None:
    con = db.get()
    accepted = con.execute("""SELECT COUNT(*) FROM scrobbles c JOIN sessions s ON s.id = c.session_id
                              WHERE s.set_id = ? AND c.status = 'accepted'""", (set_id,)).fetchone()[0]
    if accepted:
        raise ValueError(f"This set has {accepted} accepted scrobbles in its history; it cannot be deleted. "
                         "(Export a backup first if you really want to remove it.)")
    con.execute("DELETE FROM sets WHERE id = ?", (set_id,))


# ---- tracklists --------------------------------------------------------------------------------
TRACKS_SQL = """
SELECT st.*, s.title AS song_title, s.artists_json, s.album AS song_album, s.duration_ms, s.art_url, s.art_source,
       s.url AS song_url, s.spotify_id, s.source AS song_source, s.release_date, s.mbid
FROM set_tracks st LEFT JOIN songs s ON s.id = st.song_id
"""


def _decorate(r: dict) -> dict:
    artists = json.loads(r.get("artists_json") or "[]")
    r["artists"] = artists
    # Critical rule: Last.fm gets only the FIRST credited artist, Spotify's own title and album.
    r["lf_artist"] = artists[0] if artists else ""
    r["lf_track"] = r.get("song_title") or ""
    r["lf_album"] = r.get("song_album") or ""
    return r


def tracks_for_set(set_id: int) -> list[dict]:
    return [_decorate(dict(r)) for r in db.get().execute(TRACKS_SQL + " WHERE st.set_id = ? ORDER BY st.position", (set_id,))]


def get_track(track_id: int) -> dict | None:
    r = db.get().execute(TRACKS_SQL + " WHERE st.id = ?", (track_id,)).fetchone()
    return _decorate(dict(r)) if r else None


@dataclass
class TrackInput:
    raw_line: str
    artist: str
    title: str
    start_s: int | None = None
    start_source: str | None = None   # tracklist | manual
    spotify_id: str | None = None
    song_id: int | None = None
    match_status: str | None = None
    match_confidence: float | None = None
    match_note: str | None = None


def replace_tracklist(set_id: int, items: list[TrackInput], *, tracklist_raw: str | None = None) -> None:
    """Replace a set's tracklist, keeping existing matches for unchanged songs and reusing verified matches."""
    con = db.get()
    with db.tx(con):
        old = [dict(r) for r in con.execute("SELECT * FROM set_tracks WHERE set_id = ?", (set_id,))]
        by_key = {}
        for o in old:
            by_key.setdefault(query_key(o["parsed_artist"], o["parsed_title"]), o)
        con.execute("DELETE FROM set_tracks WHERE set_id = ?", (set_id,))
        for pos, it in enumerate(items, 1):
            key = query_key(it.artist, it.title)
            prev = by_key.get(key) if (it.artist or it.title) else None
            song_id, status, conf, note, cands = it.song_id, it.match_status, it.match_confidence, it.match_note, None
            if song_id is None and prev and prev["song_id"]:
                song_id, status, conf, note, cands = (prev["song_id"], prev["match_status"], prev["match_confidence"],
                                                      prev["match_note"], prev["candidates_json"])
            if song_id is None and not status and prev and prev["match_status"] == "no_match":
                status, note = "no_match", prev["match_note"]
            if song_id is None and not status:
                cached = con.execute("SELECT song_id FROM match_cache WHERE query_key = ? AND verified = 1", (key,)).fetchone()
                if cached:
                    song_id, status, conf, note = cached[0], "confirmed", 1.0, "Reused a match you verified earlier"
            status = status or ("unmatched" if song_id is None else "auto")
            con.execute(
                """INSERT INTO set_tracks(set_id, position, raw_line, parsed_artist, parsed_title, start_s, start_source,
                                          song_id, match_status, match_confidence, match_note, candidates_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (set_id, pos, it.raw_line, it.artist, it.title, it.start_s,
                 it.start_source or ("tracklist" if it.start_s is not None else "estimated"),
                 song_id, status, conf, note, cands))
        if tracklist_raw is not None:
            con.execute("UPDATE sets SET tracklist_raw = ?, updated_at = datetime('now') WHERE id = ?", (tracklist_raw, set_id))
        recompute_offsets(set_id, con=con)


def recompute_offsets(set_id: int, con=None) -> None:
    """Fill estimated start offsets around fixed (tracklist/manual) ones."""
    con = con or db.get()
    s = con.execute("SELECT duration_s FROM sets WHERE id = ?", (set_id,)).fetchone()
    rows = [dict(r) for r in con.execute(
        """SELECT st.id, st.start_s, st.start_source, s.duration_ms FROM set_tracks st
           LEFT JOIN songs s ON s.id = st.song_id WHERE st.set_id = ? ORDER BY st.position""", (set_id,))]
    if not rows:
        return
    duration = (s["duration_s"] if s and s["duration_s"] else None) or max(
        [r["start_s"] or 0 for r in rows] + [len(rows) * 180]) + 180
    known = {i: r["start_s"] for i, r in enumerate(rows) if r["start_source"] != "estimated" and r["start_s"] is not None}
    weights = [(r["duration_ms"] or 0) / 1000 or None for r in rows]
    if not all(weights):
        weights = None
    offsets = scheduler.estimate_offsets(len(rows), duration, known, weights)
    for r, off in zip(rows, offsets):
        if r["start_source"] == "estimated" or r["start_s"] is None:
            con.execute("UPDATE set_tracks SET start_s = ?, start_source = 'estimated' WHERE id = ?", (off, r["id"]))


def set_track_start(track_id: int, start_s: int, source: str = "manual") -> None:
    con = db.get()
    r = con.execute("SELECT set_id FROM set_tracks WHERE id = ?", (track_id,)).fetchone()
    con.execute("UPDATE set_tracks SET start_s = ?, start_source = ? WHERE id = ?", (int(start_s), source, track_id))
    recompute_offsets(r["set_id"])


def reset_estimates(set_id: int) -> None:
    """Drop manual position edits; timestamps that came from the tracklist itself are kept."""
    db.get().execute("UPDATE set_tracks SET start_source = 'estimated' WHERE set_id = ? AND start_source = 'manual'", (set_id,))
    recompute_offsets(set_id)


def set_candidates(track_id: int, ranked: list[dict]) -> None:
    db.get().execute("UPDATE set_tracks SET candidates_json = ? WHERE id = ?",
                     (json.dumps(ranked, ensure_ascii=False), track_id))


def assign_match(track_id: int, song_id: int | None, status: str, confidence: float | None = None,
                 note: str | None = None, remember: bool | None = None) -> None:
    """Set a track's match. Confirmed/manual matches are remembered for future sets."""
    if status not in ("unmatched", "ambiguous", "auto", "confirmed", "manual", "no_match"):
        raise ValueError(status)
    con = db.get()
    with db.tx(con):
        con.execute("UPDATE set_tracks SET song_id = ?, match_status = ?, match_confidence = ?, match_note = ? WHERE id = ?",
                    (song_id, status, confidence, note, track_id))
        t = con.execute("SELECT set_id, parsed_artist, parsed_title FROM set_tracks WHERE id = ?", (track_id,)).fetchone()
        if remember is None:
            remember = status in ("confirmed", "manual")
        if remember and song_id and (t["parsed_artist"] or t["parsed_title"]):
            con.execute("""INSERT INTO match_cache(query_key, song_id, verified) VALUES (?, ?, 1)
                           ON CONFLICT(query_key) DO UPDATE SET song_id = excluded.song_id, verified = 1,
                           updated_at = datetime('now')""", (query_key(t["parsed_artist"], t["parsed_title"]), song_id))
        recompute_offsets(t["set_id"], con=con)


def cached_match(artist: str, title: str) -> int | None:
    r = db.get().execute("SELECT song_id FROM match_cache WHERE query_key = ? AND verified = 1",
                         (query_key(artist, title),)).fetchone()
    return r[0] if r else None


def manual_song(artist: str, title: str, album: str = "", extra_artists: list[str] | None = None) -> int:
    if not artist.strip() or not title.strip():
        raise ValueError("Artist and title are required")
    return upsert_song(Candidate("manual", title.strip(), [artist.strip()] + [a for a in extra_artists or [] if a],
                                 album.strip()))


def segments_for_set(set_id: int) -> list[Segment]:
    s = get_set(set_id)
    rows = tracks_for_set(set_id)
    duration = s["duration_s"] or ((rows[-1]["start_s"] or 0) + 240 if rows else 0)
    return scheduler.segments_from_rows(rows, duration)


# ---- sessions ----------------------------------------------------------------------------------
def busy_intervals(exclude_session: int | None = None) -> list[tuple[int, int]]:
    return [(r[0], r[1]) for r in db.get().execute(
        "SELECT started_at, ended_at FROM sessions WHERE cancelled = 0 AND id IS NOT ?", (exclude_session,))]


def flexible_sessions() -> dict[int, tuple[int, int]]:
    """Reconstructed sessions with nothing submitted yet. Their times were generated, so a real
    listen may displace them (they get re-planned to a free slot)."""
    return {r[0]: (r[1], r[2]) for r in db.get().execute(
        """SELECT x.id, x.started_at, x.ended_at FROM sessions x WHERE x.cancelled = 0 AND x.reconstructed = 1
           AND x.real_started_at IS NULL AND NOT EXISTS (SELECT 1 FROM scrobbles c WHERE c.session_id = x.id
           AND c.status IN ('accepted','submitting','uncertain','queued'))""")}


def _conflict(start: int, end: int, exclude: int | None = None) -> dict | None:
    r = db.get().execute(
        """SELECT x.*, s.title FROM sessions x JOIN sets s ON s.id = x.set_id
           WHERE x.cancelled = 0 AND x.started_at < ? AND x.ended_at > ? AND x.id IS NOT ? LIMIT 1""",
        (end, start, exclude)).fetchone()
    return _row(r)


def _settings():
    s = db.all_settings()
    return float(s["max_age_days"]), int(float(s["recent_buffer_minutes"]) * 60), bool(s["send_duration"])


def _insert_session(set_id: int, kind: str, planned: list[PlannedScrobble], started: int, ended: int, *,
                    reconstructed: bool = False, real_started_at: int | None = None, partial: bool = False,
                    note: str | None = None, legacy_ref: str | None = None, original_offset: int = 0,
                    send_duration: bool = True, con=None) -> int:
    con = con or db.get()
    su = _uuid()
    cur = con.execute(
        """INSERT INTO sessions(uuid, set_id, kind, started_at, ended_at, real_started_at, reconstructed, partial,
                                note, legacy_ref) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (su, set_id, kind, started, ended, real_started_at, int(reconstructed), int(partial), note, legacy_ref))
    sid = cur.lastrowid
    for n, p in enumerate(planned, 1):
        seg = p.segment
        con.execute(
            """INSERT INTO scrobbles(uuid, session_id, set_track_id, song_id, artist, track, album, duration_s,
                                     timestamp, original_timestamp, reconstructed, status)
               VALUES (?,?,?,(SELECT song_id FROM set_tracks WHERE id = ?),?,?,?,?,?,?,?, 'pending')""",
            (f"{su}:{seg.position:03d}:{n:03d}", sid, seg.set_track_id, seg.set_track_id, seg.artist, seg.track,
             seg.album or None, seg.song_duration_s if send_duration else None, p.timestamp,
             p.timestamp + original_offset if reconstructed and real_started_at else None, int(reconstructed)))
    return sid


def start_live_session(set_id: int, started: int, span: int, now: int | None = None) -> int:
    """Open a session for live scrobbling from the Cuelog player. Scrobbles are added as songs qualify."""
    now = now or int(time.time())
    con = db.get()
    with db.tx(con):
        flexible = flexible_sessions()
        clash = next((r for r in con.execute(
            """SELECT x.id, x.started_at, s.title FROM sessions x JOIN sets s ON s.id = x.set_id
               WHERE x.cancelled = 0 AND x.started_at < ? AND x.ended_at > ?""", (max(now, started + 1), started))
            if r["id"] not in flexible), None)
        if clash:
            raise TimestampError(f"Overlaps with your listen of “{clash['title']}” at {scheduler.fmt_local(clash['started_at'])}.")
        displaced = [sid for sid, (bs, be) in flexible.items() if scheduler.overlaps(started, started + span, bs, be)]
        cur = con.execute(
            """INSERT INTO sessions(uuid, set_id, kind, started_at, ended_at, partial, note)
               VALUES (?, ?, 'player', ?, ?, 1, 'Live-scrobbled with the Cuelog player')""",
            (_uuid(), set_id, started, max(now, started + 1)))
        sid = cur.lastrowid
        if displaced:
            replan_sessions(displaced, now)
        return sid


def add_live_scrobbles(session_id: int, planned: list[PlannedScrobble], now: int | None = None) -> list[int]:
    now = now or int(time.time())
    _, _, send_dur = _settings()
    con = db.get()
    ids = []
    with db.tx(con):
        su = con.execute("SELECT uuid FROM sessions WHERE id = ?", (session_id,)).fetchone()[0]
        for p in planned:
            if p.timestamp > now:
                continue
            seg = p.segment
            cur = con.execute(
                """INSERT OR IGNORE INTO scrobbles(uuid, session_id, set_track_id, song_id, artist, track, album, duration_s,
                                                   timestamp, status)
                   VALUES (?,?,?,(SELECT song_id FROM set_tracks WHERE id = ?),?,?,?,?,?, 'pending')""",
                (f"{su}:{seg.position:03d}:live", session_id, seg.set_track_id, seg.set_track_id, seg.artist, seg.track,
                 seg.album or None, seg.song_duration_s if send_dur else None, p.timestamp))
            if cur.rowcount:
                ids.append(cur.lastrowid)
        con.execute("UPDATE sessions SET ended_at = MAX(ended_at, ?) WHERE id = ?", (now, session_id))
    return ids


def finish_live_session(session_id: int, complete: bool, now: int | None = None) -> None:
    now = now or int(time.time())
    con = db.get()
    n = con.execute("SELECT COUNT(*) FROM scrobbles WHERE session_id = ?", (session_id,)).fetchone()[0]
    if n == 0:
        con.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return
    con.execute("UPDATE sessions SET ended_at = MAX(ended_at, ?), partial = ? WHERE id = ?",
                (now, 0 if complete else 1, session_id))


def preview_listen(set_id: int, start_ts: int, plays: int = 1, first: int | None = None, last: int | None = None,
                   reconstruct: bool = False, now: int | None = None) -> dict:
    """Validate a planned listen without writing anything. Raises TimestampError on hard problems."""
    now = now or int(time.time())
    max_age, buffer_s, _ = _settings()
    segs = segments_for_set(set_id)
    if not segs:
        raise TimestampError("This set has no tracklist yet. Add tracks first.")
    span = scheduler.listen_span(segs, first, last)
    if span <= 0:
        raise TimestampError("Choose at least one track.")
    if plays < 1 or plays > 50:
        raise TimestampError("Number of plays must be between 1 and 50.")
    chosen = [s for s in segs if (first is None or s.position >= first) and (last is None or s.position <= last)]
    skipped = [s for s in chosen if not s.scrobblable]
    blocks = []
    busy_all = busy_intervals()
    flexible = flexible_sessions()
    flex_spans = set(flexible.values())
    busy = [b for b in busy_all if b not in flex_spans]
    displaced: set[int] = set()
    for p in range(plays):
        start = start_ts + p * span
        end = start + span
        real = None
        if end > now:
            raise TimestampError("That listen would end in the future. Pick an earlier start time.")
        if start < now - int(max_age * 86400):
            if not reconstruct:
                raise TimestampError(
                    f"Last.fm only accepts scrobbles from the last {int(max_age)} days. Turn on "
                    "'reconstructed timestamp' to log it now with the real date kept locally.")
            real = start
            start = scheduler.latest_free_start(span, now=now, buffer_s=buffer_s, busy=busy_all)
            end = start + span
        clash = next(((bs, be) for bs, be in busy if scheduler.overlaps(start, end, bs, be)), None)
        if clash:
            c = _conflict(start, end)
            raise TimestampError(f"Overlaps with your listen of “{c['title'] if c else 'another set'}” at "
                                 f"{scheduler.fmt_local(clash[0])}. You cannot hear two sets at once.")
        if not real:
            displaced |= {sid for sid, (bs, be) in flexible.items() if scheduler.overlaps(start, end, bs, be)}
        busy.append((start, end))
        busy_all.append((start, end))
        blocks.append({"start": start, "end": end, "real_start": real,
                       "scrobbles": scheduler.plan_listen(segs, start, first, last)})
    return {"blocks": blocks, "span": span, "skipped": skipped, "total": sum(len(b["scrobbles"]) for b in blocks),
            "partial": first not in (None, segs[0].position) or last not in (None, segs[-1].position),
            "displaced": sorted(displaced)}


def create_listen(set_id: int, start_ts: int, plays: int = 1, first: int | None = None, last: int | None = None,
                  reconstruct: bool = False, note: str | None = None, now: int | None = None, kind: str = "listen") -> list[int]:
    con = db.get()
    _, _, send_dur = _settings()
    with db.tx(con):
        pv = preview_listen(set_id, start_ts, plays, first, last, reconstruct, now)
        if pv["total"] == 0:
            raise TimestampError("None of the selected tracks can be scrobbled yet. Review the Spotify matches first.")
        ids = []
        for b in pv["blocks"]:
            ids.append(_insert_session(set_id, kind, b["scrobbles"], b["start"], b["end"],
                                       reconstructed=b["real_start"] is not None, real_started_at=b["real_start"],
                                       partial=pv["partial"], note=note, send_duration=send_dur,
                                       original_offset=(b["real_start"] - b["start"]) if b["real_start"] else 0, con=con))
        if pv["displaced"]:
            # Generated (reconstructed) sessions yield to a real listen: move them to free slots.
            replan_sessions(pv["displaced"], now)
        return ids


def preview_backfill(set_id: int, starts: list[int]) -> dict:
    segs = segments_for_set(set_id)
    span = scheduler.listen_span(segs)
    return {"span": span, "per_session": sum(1 for s in segs if s.scrobblable),
            "skipped": [s for s in segs if not s.scrobblable],
            "blocks": [{"start": t, "end": t + span, "scrobbles": scheduler.plan_listen(segs, t)} for t in starts]}


def create_backfill(set_id: int, starts: list[int], note: str | None = None, now: int | None = None) -> list[int]:
    """Reconstructed sessions (no real playback history). Clearly labelled as such."""
    now = now or int(time.time())
    max_age, _, send_dur = _settings()
    con = db.get()
    with db.tx(con):
        pv = preview_backfill(set_id, starts)
        busy = busy_intervals()
        ids = []
        for b in pv["blocks"]:
            if b["end"] > now:
                raise TimestampError("A planned session ends in the future.")
            if b["start"] < now - int(max_age * 86400):
                raise TimestampError("A planned session is older than Last.fm accepts.")
            if any(scheduler.overlaps(b["start"], b["end"], bs, be) for bs, be in busy):
                raise TimestampError(f"Session at {scheduler.fmt_local(b['start'])} overlaps another listen.")
            busy.append((b["start"], b["end"]))
            ids.append(_insert_session(set_id, "backfill", b["scrobbles"], b["start"], b["end"], reconstructed=True,
                                       note=note or "Backfilled listen: timestamp reconstructed, not real playback history",
                                       send_duration=send_dur, con=con))
        return ids


def create_player_session(set_id: int, planned: list[PlannedScrobble], started: int, ended: int,
                          partial: bool, note: str | None = None) -> int:
    _, _, send_dur = _settings()
    if not planned:
        raise TimestampError("No track was played long enough to scrobble.")
    con = db.get()
    with db.tx(con):
        c = _conflict(started, ended)
        if c:
            raise TimestampError(f"Overlaps with your listen of “{c['title']}”.")
        return _insert_session(set_id, "player", planned, started, ended, partial=partial,
                               note=note or "Recorded with the in-app SoundCloud player", send_duration=send_dur, con=con)


def replan_session(session_id: int, new_start: int, now: int | None = None) -> None:
    """Move the not-yet-submitted scrobbles of a session to a new start (reconstructed timestamps).

    Accepted scrobbles never move. The first original timestamp is kept as
    ``original_timestamp`` so the real/older date is not lost.
    """
    now = now or int(time.time())
    con = db.get()
    with db.tx(con):
        s = con.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if con.execute("SELECT COUNT(*) FROM scrobbles WHERE session_id = ? AND status IN ('accepted','submitting','uncertain')",
                       (session_id,)).fetchone()[0]:
            raise TimestampError("This session already has accepted or in-flight scrobbles; it cannot be moved.")
        delta = new_start - s["started_at"]
        end = s["ended_at"] + delta
        if end > now:
            raise TimestampError("The new time would end in the future.")
        if _conflict(new_start, end, exclude=session_id):
            raise TimestampError("The new time overlaps another listen.")
        con.execute("""UPDATE scrobbles SET original_timestamp = COALESCE(original_timestamp, timestamp),
                       timestamp = timestamp + ?, reconstructed = 1, status = 'pending', ignored_code = NULL,
                       ignored_message = NULL, job_id = NULL
                       WHERE session_id = ? AND status IN ('pending','expired','failed','queued')""", (delta, session_id))
        con.execute("""UPDATE sessions SET real_started_at = COALESCE(real_started_at, CASE WHEN reconstructed = 0
                       THEN started_at END), started_at = ?, ended_at = ?, reconstructed = 1 WHERE id = ?""",
                    (new_start, end, session_id))


def replan_sessions(session_ids: list[int], now: int | None = None) -> int:
    """Re-distribute several unsubmitted sessions evenly over the currently accepted window."""
    now = now or int(time.time())
    st = db.all_settings()
    max_age = float(st["max_age_days"])
    con = db.get()
    sessions = [dict(r) for r in con.execute(
        f"SELECT * FROM sessions WHERE cancelled = 0 AND id IN ({','.join('?' * len(session_ids))}) ORDER BY started_at",
        session_ids)]
    if not sessions:
        return 0
    busy = busy_intervals_excluding([s["id"] for s in sessions])
    durations = {s["ended_at"] - s["started_at"] for s in sessions}
    span = max(durations)
    days = scheduler.window_days(now, max_age - 0.5)
    buffer_s = int(float(st["recent_buffer_minutes"]) * 60)
    cap = scheduler.day_capacity(days, now=now, max_age_days=max_age - 0.5, day_start=st["backfill_day_start"],
                                 day_end=st["backfill_day_end"], buffer_s=buffer_s)
    free = {d: max(0, c - sum(max(0, min(be, scheduler.local_epoch(d, st["backfill_day_end"]))
                                     - max(bs, scheduler.local_epoch(d, st["backfill_day_start"]))) for bs, be in busy))
            for d, c in cap.items()}
    counts = scheduler.distribute_evenly(len(sessions), days, free, span)
    if sum(counts.values()) < len(sessions):
        raise TimestampError(f"Not enough free listening time in the last {max_age - 0.5:g} days for "
                             f"{len(sessions)} session(s). Widen the backfill hours in Settings or re-plan fewer.")
    starts = scheduler.backfill_starts(counts, span, now=now, max_age_days=max_age - 0.5,
                                       day_start=st["backfill_day_start"], day_end=st["backfill_day_end"],
                                       buffer_s=buffer_s, busy=busy)
    with db.tx(con):
        # move everything far away first so intermediate states never overlap each other
        for s in sessions:
            con.execute("UPDATE sessions SET cancelled = cancelled + 2 WHERE id = ?", (s["id"],))
        for s, t in zip(sessions, starts):
            con.execute("UPDATE sessions SET cancelled = cancelled - 2 WHERE id = ?", (s["id"],))
            replan_session(s["id"], t, now)
    return len(sessions)


def busy_intervals_excluding(ids: list[int]) -> list[tuple[int, int]]:
    q = f"SELECT started_at, ended_at FROM sessions WHERE cancelled = 0 AND id NOT IN ({','.join('?' * len(ids))})"
    return [(r[0], r[1]) for r in db.get().execute(q, ids)]


def cancel_session(session_id: int) -> int:
    """Cancel a session's unsubmitted scrobbles. Accepted scrobbles stay accepted."""
    con = db.get()
    with db.tx(con):
        if con.execute("SELECT COUNT(*) FROM scrobbles WHERE session_id = ? AND status IN ('submitting','uncertain')",
                       (session_id,)).fetchone()[0]:
            raise ValueError("Some scrobbles are being submitted right now. Wait for the import to finish.")
        n = con.execute("""UPDATE scrobbles SET status = 'skipped', job_id = NULL WHERE session_id = ?
                           AND status IN ('pending','queued','failed','expired','ignored')""", (session_id,)).rowcount
        accepted = con.execute("SELECT COUNT(*) FROM scrobbles WHERE session_id = ? AND status = 'accepted'",
                               (session_id,)).fetchone()[0]
        if not accepted:
            con.execute("UPDATE sessions SET cancelled = 1 WHERE id = ?", (session_id,))
        return n


SESSION_SQL = """
SELECT x.*, s.title AS set_title, s.artwork_url, s.uploader,
  COUNT(c.id) AS n_total,
  SUM(c.status = 'accepted') AS n_accepted,
  SUM(c.status IN ('pending','queued','submitting','uncertain')) AS n_pending,
  SUM(c.status IN ('queued','submitting')) AS n_queued,
  SUM(c.status = 'uncertain') AS n_uncertain,
  SUM(c.status IN ('ignored','failed','expired')) AS n_problem,
  SUM(c.status IN ('duplicate','skipped')) AS n_skipped,
  MIN(CASE WHEN c.status IN ('pending','queued','failed','expired') THEN c.timestamp END) AS oldest_pending
FROM sessions x JOIN sets s ON s.id = x.set_id LEFT JOIN scrobbles c ON c.session_id = x.id
"""


def session_status(r: dict, now: int | None = None, max_age_days: float | None = None) -> str:
    if r["cancelled"]:
        return "cancelled"
    total, acc = r["n_total"] or 0, r["n_accepted"] or 0
    pending, problem, queued = r["n_pending"] or 0, r["n_problem"] or 0, r["n_queued"] or 0
    if r.get("n_uncertain"):
        return "needs_review"
    if queued:
        return "queued"
    if total and acc + (r["n_skipped"] or 0) == total:
        return "completed" if acc else "skipped"
    if acc and (pending or problem):
        return "partial"
    if problem and not pending:
        return "needs_review"
    if pending:
        now = now or int(time.time())
        max_age = max_age_days if max_age_days is not None else float(db.setting("max_age_days"))
        if r.get("oldest_pending") and r["oldest_pending"] < now - int(max_age * 86400):
            return "expired"
        return "draft"
    return "draft"


def list_sessions(set_id: int | None = None, status: str | None = None, limit: int = 2000) -> list[dict]:
    where, args = ["1=1"], []
    if set_id:
        where.append("x.set_id = ?")
        args.append(set_id)
    rows = [dict(r) for r in db.get().execute(
        SESSION_SQL + " WHERE " + " AND ".join(where) + " GROUP BY x.id ORDER BY x.started_at DESC LIMIT ?",
        (*args, limit))]
    now, max_age = int(time.time()), float(db.setting("max_age_days"))
    for r in rows:
        r["status"] = session_status(r, now, max_age)
    if status:
        rows = [r for r in rows if r["status"] == status]
    return rows


def get_session(session_id: int) -> dict | None:
    r = db.get().execute(SESSION_SQL + " WHERE x.id = ? GROUP BY x.id", (session_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["status"] = session_status(d)
    return d


def scrobbles_for_session(session_id: int) -> list[dict]:
    return [dict(r) for r in db.get().execute(
        """SELECT c.*, COALESCE(NULLIF(s.art_url, ''), (SELECT g.art_url FROM set_tracks t JOIN songs g ON g.id = t.song_id
                                                        WHERE t.id = c.set_track_id)) AS art_url
           FROM scrobbles c LEFT JOIN songs s ON s.id = c.song_id
           WHERE c.session_id = ? ORDER BY c.timestamp""", (session_id,))]


# ---- statistics --------------------------------------------------------------------------------
def stats() -> dict:
    con = db.get()
    one = lambda q, *a: con.execute(q, a).fetchone()[0] or 0
    return {
        "sets": one("SELECT COUNT(*) FROM sets"),
        "sessions": one("SELECT COUNT(*) FROM sessions WHERE cancelled = 0"),
        "accepted": one("SELECT COUNT(*) FROM scrobbles WHERE status = 'accepted'"),
        "pending": one("""SELECT COUNT(*) FROM scrobbles c JOIN sessions x ON x.id = c.session_id
                          WHERE x.cancelled = 0 AND c.status IN ('pending','queued','submitting','uncertain')"""),
        "problems": one("""SELECT COUNT(*) FROM scrobbles c JOIN sessions x ON x.id = c.session_id
                           WHERE x.cancelled = 0 AND c.status IN ('ignored','failed','expired')"""),
        "songs": one("SELECT COUNT(*) FROM songs"),
        "top_sets": [dict(r) for r in con.execute(
            """SELECT s.id, s.title, s.artwork_url, COUNT(x.id) AS n FROM sets s JOIN sessions x ON x.set_id = s.id
               WHERE x.cancelled = 0 GROUP BY s.id ORDER BY n DESC LIMIT 5""")],
        "top_artists": [dict(r) for r in con.execute(
            """SELECT c.artist, COUNT(*) AS n FROM scrobbles c JOIN sessions x ON x.id = c.session_id
               WHERE x.cancelled = 0 AND c.status NOT IN ('skipped','duplicate')
               GROUP BY lower(c.artist) ORDER BY n DESC LIMIT 8""")],
        "accepted_today": one("SELECT COUNT(*) FROM scrobbles WHERE status = 'accepted' AND accepted_at >= datetime('now','-1 day')"),
        "per_day": [dict(r) for r in con.execute(
            """SELECT date(t, 'unixepoch', 'localtime') AS day, COUNT(*) AS n FROM (
                 SELECT COALESCE(real_started_at, CASE WHEN reconstructed = 0 THEN started_at END) AS t
                 FROM sessions WHERE cancelled = 0)
               WHERE t >= CAST(strftime('%s','now','-30 days') AS INTEGER) GROUP BY day ORDER BY day""")],
    }


def recent_scrobbles(limit: int = 8) -> list[dict]:
    """Most recent scrobbles Last.fm accepted from Cuelog."""
    return [dict(r) for r in db.get().execute(
        """SELECT c.artist, c.track, c.timestamp, s.title AS set_title, s.id AS set_id, g.art_url
           FROM scrobbles c JOIN sessions x ON x.id = c.session_id JOIN sets s ON s.id = x.set_id
           LEFT JOIN songs g ON g.id = c.song_id
           WHERE c.status = 'accepted' ORDER BY c.timestamp DESC LIMIT ?""", (limit,))]


def week_summary(now: int | None = None) -> dict:
    now = now or int(time.time())
    since = now - 7 * 86400
    con = db.get()
    listens = con.execute("""SELECT COUNT(*) FROM sessions WHERE cancelled = 0 AND
                             COALESCE(real_started_at, CASE WHEN reconstructed = 0 THEN started_at END) >= ?""",
                          (since,)).fetchone()[0]
    sent = con.execute("SELECT COUNT(*) FROM scrobbles WHERE status = 'accepted' AND accepted_at >= datetime(?, 'unixepoch')",
                       (since,)).fetchone()[0]
    return {"listens": listens, "sent": sent}


def scrobbles_per_day(days: int = 30, now: int | None = None) -> list[tuple[str, int]]:
    """Songs accepted by Last.fm per local day (by listening time), oldest first, zero-filled."""
    from datetime import timedelta
    now = now or int(time.time())
    rows = dict(db.get().execute(
        """SELECT date(timestamp, 'unixepoch', 'localtime') AS d, COUNT(*) FROM scrobbles
           WHERE status = 'accepted' AND timestamp >= ? GROUP BY d""", (now - days * 86400,)).fetchall())
    today = scheduler.local_date(now)
    return [((today - timedelta(days=i)).isoformat(), rows.get((today - timedelta(days=i)).isoformat(), 0))
            for i in range(days - 1, -1, -1)]


def scrobbles_by_hour() -> list[int]:
    """Accepted scrobbles per hour of the day (local time)."""
    rows = dict(db.get().execute(
        """SELECT CAST(strftime('%H', timestamp, 'unixepoch', 'localtime') AS INTEGER), COUNT(*)
           FROM scrobbles WHERE status = 'accepted' GROUP BY 1""").fetchall())
    return [rows.get(h, 0) for h in range(24)]


def listens_per_set(limit: int = 6) -> list[tuple[str, int]]:
    return [(r[0], r[1]) for r in db.get().execute(
        """SELECT s.title, COUNT(x.id) FROM sets s JOIN sessions x ON x.set_id = s.id WHERE x.cancelled = 0
           GROUP BY s.id ORDER BY 2 DESC LIMIT ?""", (limit,))]
