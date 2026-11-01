"""Tracklist parsing: numbered, timestamped and free-form DJ tracklists."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from .urls import spotify_track_id

_DASHES = "‐‑‒–—―−"
_TS = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?::\d{2})?"     # [h:]mm:ss, optionally :frames (00:16:48:24)
_TS_RE = re.compile(rf"^[\[\(]?\s*{_TS}\s*[\]\)]?\s*(?:[-|:.]\s+)?")
_NUM_RE = re.compile(r"^(?:#\s*(\d{1,3})\s*[.):\-]?\s+|(\d{1,3})\s*(?:[.)\]:]|\s-\s)\s*|(0\d{1,3})\s+)")
_BARE_NUM = re.compile(r"^\s*(\d{1,3})\s+\S")
_BULLET_RE = re.compile(r"^[\-\*•●▸►>]+\s+")
_SEP_RE = re.compile(r"\s+-\s+|\s-(?=\S)|(?<=\S)-\s")
_LABEL_TAIL = re.compile(r"\s*\[([^\]]+)\]\s*$")
_VERSION_WORDS = re.compile(r"\b(remix|mix|edit|rmx|bootleg|vip|version|cut|rework|remaster|anthem|dub|flip|extended|radio|live)\b", re.I)
_HEADER = re.compile(r"^(track\s*list|tracklist|tracks|setlist|playlist)\s*[:\-]?\s*$", re.I)
_ID_ONLY = re.compile(r"^\s*id\s*$", re.I)


@dataclass
class TrackEntry:
    position: int
    raw_line: str
    artist: str = ""
    title: str = ""
    start_s: int | None = None
    spotify_id: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def is_id(self) -> bool:
        return bool(_ID_ONLY.match(self.title or "")) or bool(_ID_ONLY.match(self.artist or "") and _ID_ONLY.match(self.title or "id"))


def parse_timestamp(text: str) -> int | None:
    m = re.fullmatch(_TS, (text or "").strip())
    if not m:
        return None
    h, mnt, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    if s >= 60 or (m.group(1) and mnt >= 60):
        return None
    return h * 3600 + mnt * 60 + s


def format_timestamp(seconds: int | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _normalise_dashes(s: str) -> str:
    for d in _DASHES:
        s = s.replace(d, "-")
    return s


def split_artist_title(text: str) -> tuple[str, str] | None:
    text = _normalise_dashes(text).strip()
    parts = _SEP_RE.split(text, maxsplit=1)
    if len(parts) != 2:
        return None
    artist, title = parts[0].strip(" \t\"'"), parts[1].strip(" \t\"'")
    if not artist or not title:
        return None
    return artist, title


def _strip_label(title: str) -> str:
    m = _LABEL_TAIL.search(title)
    if m and not _VERSION_WORDS.search(m.group(1)):
        return title[:m.start()].rstrip()
    return title


def _bare_numbering(lines: list[str]) -> bool:
    """True when most lines start with an increasing bare number ("1 Artist - Title", "001 Artist - Title")."""
    texts = [ln.strip() for ln in lines if ln.strip()]
    nums = [int(m.group(1)) for m in (_BARE_NUM.match(t) for t in texts) if m]
    return len(nums) >= 3 and len(nums) >= 0.6 * len(texts) and all(b > a for a, b in zip(nums, nums[1:]))


def parse_line(line: str, position: int = 0, numbered: bool = False) -> TrackEntry | None:
    """Parse one tracklist line. Returns None for blank/header lines.

    ``numbered``: the whole list uses bare leading numbers, so strip one from this line.
    """
    raw = line.rstrip("\r\n")
    text = raw.strip()
    if not text or _HEADER.match(text):
        return None
    sid = spotify_track_id(text)
    if sid:
        return TrackEntry(position, raw, spotify_id=sid)
    text = _BULLET_RE.sub("", _normalise_dashes(text))
    if numbered:
        text = re.sub(r"^\d{1,3}\s+", "", text, count=1)
    start = None
    # "01. 03:12 Artist - Title" or "03:12 01. Artist - Title"
    for _ in range(2):
        m = _TS_RE.match(text)
        if m and start is None:
            h, mn, sec = m.group(1), m.group(2), m.group(3)
            start = parse_timestamp(f"{h + ':' if h else ''}{mn}:{sec}")
            text = text[m.end():].lstrip()
            continue
        m = _NUM_RE.match(text)
        if m and not _TS_RE.match(text):
            rest = text[m.end():]
            if rest and (split_artist_title(rest) or _TS_RE.match(rest)):
                text = rest.lstrip()
                continue
        break
    # trailing timestamp: "Artist - Title (03:12)" / "Artist - Title 1:03:12"
    if start is None:
        m = re.search(rf"[\[\(]?\s*({_TS})\s*[\]\)]?\s*$", text)
        if m and text[:m.start()].strip(" -|"):
            start = parse_timestamp(m.group(1))
            text = text[:m.start()].rstrip(" -|")
    pair = split_artist_title(text)
    if not pair:
        entry = TrackEntry(position, raw, title=_strip_label(text.strip()), start_s=start)
        entry.problems.append("No artist found: add one, or matching uses the title only")
        return entry
    artist, title = pair
    return TrackEntry(position, raw, artist=artist, title=_strip_label(title), start_s=start)


def parse_tracklist(text: str) -> list[TrackEntry]:
    entries: list[TrackEntry] = []
    lines = (text or "").splitlines()
    numbered = _bare_numbering(lines)
    for line in lines:
        e = parse_line(line, len(entries) + 1, numbered)
        if e is not None:
            entries.append(e)
    return entries


def extract_from_description(description: str) -> list[TrackEntry]:
    """Pick out tracklist-looking lines from a free-text SoundCloud description.

    Only lines that are numbered, timestamped or Spotify links count, plus plain
    "Artist - Title" lines when they form a block of at least three.
    """
    lines = (description or "").splitlines()
    numbered = _bare_numbering(lines)
    strong: list[TrackEntry] = []
    block: list[TrackEntry] = []
    plain_blocks: list[list[TrackEntry]] = []
    for line in lines:
        t = _BULLET_RE.sub("", _normalise_dashes(line.strip()))
        if not t:
            if len(block) >= 3:
                plain_blocks.append(block)
            block = []
            continue
        is_marked = bool(numbered or _TS_RE.match(t) or (_NUM_RE.match(t) and split_artist_title(_NUM_RE.sub("", t, 1))) or spotify_track_id(t))
        e = parse_line(line, 0, numbered)
        if e is None or e.problems:
            if len(block) >= 3:
                plain_blocks.append(block)
            block = []
            continue
        if is_marked:
            strong.append(e)
        elif len(t) < 160 and "http" not in t.lower():
            block.append(e)
    if len(block) >= 3:
        plain_blocks.append(block)
    chosen = strong if len(strong) >= 2 else (max(plain_blocks, key=len) if plain_blocks else [])
    for i, e in enumerate(chosen, 1):
        e.position = i
    return chosen


def validate_timeline(entries: list[TrackEntry], duration_s: int | None) -> list[str]:
    problems = []
    starts = [(e.position, e.start_s) for e in entries if e.start_s is not None]
    for (pa, a), (pb, b) in zip(starts, starts[1:]):
        if b < a:   # equal starts are fine: layered songs / mashups
            problems.append(f"Track {pb} starts at {format_timestamp(b)}, before track {pa} ({format_timestamp(a)})")
    if duration_s and starts and starts[-1][1] >= duration_s:
        problems.append(f"Track {starts[-1][0]} starts after the end of the mix")
    return problems


CSV_FIELDS = ["position", "start", "artist", "title", "spotify_url", "raw_line"]


def entries_to_csv(entries: list[TrackEntry]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    for e in entries:
        w.writerow({"position": e.position, "start": format_timestamp(e.start_s), "artist": e.artist, "title": e.title,
                    "spotify_url": f"https://open.spotify.com/track/{e.spotify_id}" if e.spotify_id else "",
                    "raw_line": e.raw_line})
    return buf.getvalue()


def entries_from_csv(text: str) -> list[TrackEntry]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = {f.strip().lower(): f for f in (reader.fieldnames or [])}
    if not ({"artist", "title"} <= set(fields) or "raw_line" in fields or "line" in fields):
        raise ValueError("CSV needs 'artist' and 'title' columns (or a 'line' column). "
                         f"Found: {', '.join(reader.fieldnames or []) or 'no header'}")
    out = []
    for row in reader:
        g = lambda k: (row.get(fields[k]) or "").strip() if k in fields else ""
        artist, title = g("artist"), g("title")
        raw = g("raw_line") or g("line") or (f"{artist} - {title}" if artist else title)
        sid = spotify_track_id(g("spotify_url")) if g("spotify_url") else None
        if not artist and not title:
            e = parse_line(raw, len(out) + 1)
            if e is None:
                continue
        else:
            e = TrackEntry(len(out) + 1, raw, artist, title)
        start_text = g("start") or g("start_seconds") or g("timestamp")
        if start_text:
            e.start_s = int(start_text) if start_text.isdigit() else parse_timestamp(start_text)
        if sid:
            e.spotify_id = sid
        out.append(e)
    return out
