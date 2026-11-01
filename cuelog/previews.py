"""30-second song previews from Deezer's public API (no account or key needed).

Used to hear which version a candidate match really is. A preview is only
returned when it is the same recording (title, artist and version match, and the
length agrees when known), so you never hear a different remix by mistake.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from . import http
from .matching import Candidate, rank

API = "https://api.deezer.com/search"
MIN_SCORE = 0.8
MAX_LENGTH_DIFF_MS = 12_000


def _safe(url: str) -> bool:
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    return p.scheme == "https" and (p.hostname or "").endswith(".dzcdn.net")


def find_preview(artist: str, title: str, duration_ms: int | None = None) -> dict | None:
    """Return {'url', 'title', 'artist', 'source'} for the same recording, or None."""
    data = http.get_json(API, params={"q": f"{artist} {title}".strip(), "limit": 15})
    if isinstance(data, dict) and data.get("error"):
        raise http.HttpError(f"Deezer: {data['error'].get('message', 'error')}")
    cands = []
    for t in (data or {}).get("data") or []:
        url = t.get("preview") or ""
        if not _safe(url):
            continue
        cands.append(Candidate("deezer", t.get("title", ""), [(t.get("artist") or {}).get("name", "")],
                               (t.get("album") or {}).get("title", ""), duration_ms=int(t.get("duration") or 0) * 1000 or None,
                               url=t.get("link", ""), raw={"preview": url}))
    for r in rank(artist, title, cands):
        if r.score < MIN_SCORE:
            break
        c = r.candidate
        if duration_ms and c.duration_ms and abs(c.duration_ms - duration_ms) > MAX_LENGTH_DIFF_MS:
            continue
        return {"url": c.raw["preview"], "title": c.title, "artist": c.main_artist, "source": "Deezer"}
    return None
