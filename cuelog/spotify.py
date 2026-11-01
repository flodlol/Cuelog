"""Spotify Web API (client-credentials flow) plus the keyless oEmbed fallback.

Client credentials are enough for /v1/search and /v1/tracks; no user login is
needed. Without credentials, pasted Spotify track links still resolve through
the public oEmbed endpoint (title + official album artwork only).
"""
from __future__ import annotations

import base64
import threading
import time
import urllib.parse

from . import http
from .config import Credentials
from .matching import Candidate
from .urls import spotify_track_url

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"


class SpotifyError(RuntimeError):
    pass


def candidate_from_track(t: dict) -> Candidate:
    images = (t.get("album") or {}).get("images") or []
    # Spotify lists images largest first; prefer ~300px for lists.
    art = ""
    if images:
        art = sorted(images, key=lambda i: abs((i.get("width") or 300) - 300))[0].get("url", "")
    return Candidate(
        source="spotify",
        title=t.get("name", ""),
        artists=[a.get("name", "") for a in t.get("artists") or [] if a.get("name")],
        album=(t.get("album") or {}).get("name", ""),
        duration_ms=t.get("duration_ms"),
        release_date=(t.get("album") or {}).get("release_date", ""),
        url=(t.get("external_urls") or {}).get("spotify") or spotify_track_url(t["id"]),
        art_url=art,
        art_source="spotify" if art else "",
        spotify_id=t.get("id"),
        isrc=(t.get("external_ids") or {}).get("isrc"),
        popularity=t.get("popularity"),
        raw={"id": t.get("id"), "album_id": (t.get("album") or {}).get("id")},
    )


class SpotifyClient:
    def __init__(self, creds: Credentials):
        self.creds = creds
        self._token: str | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.creds.spotify

    def _auth(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires - 60:
                return self._token
            if not self.configured:
                raise SpotifyError("Spotify credentials are not configured (Settings → Accounts).")
            basic = base64.b64encode(f"{self.creds.spotify_id}:{self.creds.spotify_secret}".encode()).decode()
            try:
                data = http.post_json(TOKEN_URL, data={"grant_type": "client_credentials"},
                                      headers={"Authorization": f"Basic {basic}"})
            except http.HttpError as err:
                if err.status in (400, 401):
                    raise SpotifyError("Spotify rejected the client ID/secret. Check them in Settings.") from err
                raise SpotifyError(f"Spotify login failed ({err})") from err
            self._token = data["access_token"]
            self._expires = time.time() + int(data.get("expires_in", 3600))
            return self._token

    def _get(self, path: str, params: dict | None = None, _retry: int = 0):
        try:
            return http.get_json(API + path, params=params, headers={"Authorization": f"Bearer {self._auth()}"})
        except http.HttpError as err:
            if err.status == 401 and _retry == 0:
                self._token = None
                return self._get(path, params, 1)
            if err.status == 429 and _retry < 3:
                time.sleep(min(30.0, err.retry_after or 2 ** (_retry + 1)))
                return self._get(path, params, _retry + 1)
            raise SpotifyError(f"Spotify API error: {err} {err.body[:200]}") from err

    def verify(self) -> bool:
        self._auth()
        return True

    def track(self, track_id: str) -> Candidate:
        return candidate_from_track(self._get(f"/tracks/{urllib.parse.quote(track_id)}"))

    def search(self, artist: str, title: str, limit: int = 10) -> list[Candidate]:
        queries = []
        if artist:
            queries.append(f'track:"{_q(title)}" artist:"{_q(artist.split(" ft")[0].split(" feat")[0])}"')
        queries.append(f"{artist} {title}".strip())
        out: list[Candidate] = []
        seen = set()
        for q in queries:
            data = self._get("/search", {"q": q, "type": "track", "limit": limit})
            for t in (data.get("tracks") or {}).get("items") or []:
                if t and t.get("id") not in seen:
                    seen.add(t["id"])
                    out.append(candidate_from_track(t))
            if len(out) >= limit:
                break
        return out

    def artist_image(self, name: str) -> str:
        """URL of the best-matching artist's photo ('' if none)."""
        from .matching import norm
        data = self._get("/search", {"q": name, "type": "artist", "limit": 5})
        items = (data.get("artists") or {}).get("items") or []
        best = next((a for a in items if norm(a.get("name", "")) == norm(name)), items[0] if items else None)
        imgs = (best or {}).get("images") or []
        return sorted(imgs, key=lambda i: abs((i.get("width") or 640) - 640))[0]["url"] if imgs else ""

    def search_isrc(self, isrc: str) -> list[Candidate]:
        data = self._get("/search", {"q": f"isrc:{isrc}", "type": "track", "limit": 5})
        return [candidate_from_track(t) for t in (data.get("tracks") or {}).get("items") or [] if t]


def _q(s: str) -> str:
    return (s or "").replace('"', " ").strip()


def oembed(track_id: str) -> dict:
    """Public, keyless: returns {'title', 'thumbnail_url'} for a Spotify track."""
    url = spotify_track_url(track_id)
    return http.get_json("https://open.spotify.com/oembed", params={"url": url})
