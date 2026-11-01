"""MusicBrainz recording search + Cover Art Archive artwork (no key needed).

Used when Spotify credentials are not configured. MusicBrainz asks for at most
one request per second and a descriptive User-Agent; both are enforced here.
"""
from __future__ import annotations

import threading
import time

from . import http
from .matching import Candidate

API = "https://musicbrainz.org/ws/2"
_lock = threading.Lock()
_last = 0.0


def _throttle():
    global _last
    with _lock:
        wait = 1.1 - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()


_TYPOGRAPHY = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "’": "'",
                             "‘": "'", "“": '"', "”": '"', "…": "..."})


def plain(s: str) -> str:
    """MusicBrainz uses typographic punctuation (e.g. U+2010 hyphens); Last.fm/Spotify use ASCII."""
    return (s or "").translate(_TYPOGRAPHY)


def _lucene(s: str) -> str:
    return "".join("\\" + c if c in '+-&|!(){}[]^"~*?:\\/' else c for c in s)


def search(artist: str, title: str, limit: int = 8) -> list[Candidate]:
    q = f'recording:"{_lucene(title)}"'
    if artist:
        q += f' AND artist:"{_lucene(artist)}"'
    _throttle()
    data = http.get_json(f"{API}/recording", params={"query": q, "limit": limit, "fmt": "json"})
    out = []
    for r in data.get("recordings") or []:
        credits = r.get("artist-credit") or []
        artists = [c.get("name") or (c.get("artist") or {}).get("name", "") for c in credits if isinstance(c, dict)]
        releases = r.get("releases") or []
        rel = releases[0] if releases else {}
        art = f"https://coverartarchive.org/release/{rel['id']}/front-250" if rel.get("id") else ""
        out.append(Candidate(
            source="musicbrainz",
            title=plain(r.get("title", "")),
            artists=[plain(a) for a in artists if a],
            album=plain(rel.get("title", "")),
            duration_ms=r.get("length"),
            release_date=rel.get("date", "") or r.get("first-release-date", ""),
            url=f"https://musicbrainz.org/recording/{r['id']}",
            art_url=art,
            art_source="coverartarchive" if art else "",
            mbid=r.get("id"),
            isrc=(r.get("isrcs") or [None])[0],
        ))
    return out
