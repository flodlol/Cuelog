"""Artist photos (Last.fm no longer serves them): Spotify when configured, otherwise Deezer. Cached 30 days."""
from __future__ import annotations

from . import history, http
from .config import Credentials
from .matching import norm


def artist_image(name: str, creds: Credentials) -> str:
    key = "artimg:" + norm(name)
    hit = history._cache_get(key, 30 * 86400)
    if hit is not None:
        return hit
    url = ""
    if creds.spotify:
        try:
            from .spotify import SpotifyClient
            url = SpotifyClient(creds).artist_image(name)
        except Exception:
            url = ""
    if not url:
        try:
            data = http.get_json("https://api.deezer.com/search/artist", params={"q": name, "limit": 5})
            items = data.get("data") or []
            best = next((a for a in items if norm(a.get("name", "")) == norm(name)), items[0] if items else None)
            url = (best or {}).get("picture_xl") or (best or {}).get("picture_big") or ""
            if url and "/images/artist//" in url:      # Deezer's placeholder for artists without a photo
                url = ""
        except Exception:
            url = ""
    history._cache_put(key, url)
    return url


def track_image(artist: str, title: str) -> str:
    """Cover for a song when Last.fm has none (Deezer, no key needed). Cached 30 days."""
    key = "trkimg:" + norm(artist) + "|" + norm(title)
    hit = history._cache_get(key, 30 * 86400)
    if hit is not None:
        return hit
    url = ""
    try:
        data = http.get_json("https://api.deezer.com/search", params={"q": f'artist:"{artist}" track:"{title}"', "limit": 5})
        items = data.get("data") or []
        if not items:
            data = http.get_json("https://api.deezer.com/search", params={"q": f"{artist} {title}", "limit": 5})
            items = data.get("data") or []
        best = next((t for t in items if norm((t.get("artist") or {}).get("name", "")) == norm(artist)), items[0] if items else None)
        url = ((best or {}).get("album") or {}).get("cover_xl") or ((best or {}).get("album") or {}).get("cover_big") or ""
    except Exception:
        url = ""
    history._cache_put(key, url)
    return url
