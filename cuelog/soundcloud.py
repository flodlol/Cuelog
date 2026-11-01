"""SoundCloud: keyless oEmbed metadata plus optional official OAuth 2.1 (PKCE) API.

* oEmbed (no key): title, uploader, artwork, embeddable track id.
* Widget API (no key, see ui/player.py): duration, description, playback events.
* Official API (needs an approved app): /resolve, /me/playlists, /me/likes/...

SoundCloud's public API does not expose a listening history ("recently
played"), so Cuelog never claims or invents one.
"""
from __future__ import annotations

import base64
import hashlib
import html
import http.server
import re
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field

from . import db, http as nethttp
from .config import Credentials
from .secretstore import seal, unseal
from .urls import UrlError, is_allowed_soundcloud_redirect, parse_soundcloud

API = "https://api.soundcloud.com"
AUTHORIZE_URL = "https://secure.soundcloud.com/authorize"
TOKEN_URL = "https://secure.soundcloud.com/oauth/token"


class SoundCloudError(RuntimeError):
    pass


@dataclass
class ScMeta:
    url: str
    kind: str = "track"
    title: str = ""
    uploader: str = ""
    uploader_url: str = ""
    artwork_url: str = ""
    duration_ms: int | None = None
    description: str = ""
    embed_url: str = ""          # api.soundcloud.com/tracks/<id> for the widget
    track_count: int | None = None
    items: list[dict] = field(default_factory=list)   # playlist items when known
    raw: dict = field(default_factory=dict)


def hi_res_artwork(url: str | None) -> str:
    if not url:
        return ""
    return re.sub(r"-(large|t\d+x\d+|crop|small|badge|tiny|mini)\.(jpg|png)$", r"-t500x500.\2", url)


def resolve_link(raw: str) -> tuple[str, str]:
    """Normalise a pasted link; follows short links (validating every redirect)."""
    parsed = parse_soundcloud(raw)
    if parsed.kind == "short":
        final = nethttp.resolve_redirects(parsed.url, is_allowed_soundcloud_redirect)
        parsed = parse_soundcloud(final)
    if parsed.kind == "user":
        raise UrlError("That is a profile link. Paste a link to a specific track, mix or playlist.")
    return parsed.url, parsed.kind


def oembed(url: str) -> ScMeta:
    try:
        data = nethttp.get_json("https://soundcloud.com/oembed", params={"format": "json", "url": url})
    except nethttp.HttpError as err:
        if err.status in (403, 404):
            raise SoundCloudError("SoundCloud could not find that track. It may be private, deleted, "
                                  "or not available in your country.") from err
        raise SoundCloudError(f"SoundCloud oEmbed failed ({err})") from err
    title = data.get("title") or ""
    author = data.get("author_name") or ""
    if author and title.endswith(" by " + author):
        title = title[: -len(" by " + author)]
    src = ""
    m = re.search(r'src="([^"]+)"', data.get("html") or "")
    if m:
        player = html.unescape(m.group(1))
        inner = urllib.parse.parse_qs(urllib.parse.urlsplit(player).query).get("url", [""])[0]
        if re.match(r"^https://api\.soundcloud\.com/(tracks|playlists)/\d+$", inner):
            src = inner
    kind = "playlist" if "/playlists/" in src else "track"
    return ScMeta(url=url, kind=kind, title=title.strip(), uploader=author,
                  uploader_url=data.get("author_url") or "", artwork_url=hi_res_artwork(data.get("thumbnail_url")),
                  description=data.get("description") or "", embed_url=src, raw={"oembed": data})


def meta_from_api(obj: dict) -> ScMeta:
    kind = obj.get("kind", "track")
    user = obj.get("user") or {}
    items = []
    if kind == "playlist":
        for t in obj.get("tracks") or []:
            items.append({"title": t.get("title", ""), "uploader": (t.get("user") or {}).get("username", ""),
                          "duration_ms": t.get("duration"), "url": t.get("permalink_url", "")})
    return ScMeta(url=obj.get("permalink_url", ""), kind=kind, title=obj.get("title", ""),
                  uploader=user.get("username", ""), uploader_url=user.get("permalink_url", ""),
                  artwork_url=hi_res_artwork(obj.get("artwork_url") or user.get("avatar_url")),
                  duration_ms=obj.get("duration"), description=obj.get("description") or "",
                  embed_url=f"{API}/{'playlists' if kind == 'playlist' else 'tracks'}/{obj.get('id')}" if obj.get("id") else "",
                  track_count=obj.get("track_count"), items=items, raw={"api": {k: obj.get(k) for k in (
                      "id", "kind", "title", "duration", "permalink_url", "genre", "created_at", "track_count")}})


class SoundCloudClient:
    """Official API access. Works only with an approved SoundCloud app."""

    def __init__(self, creds: Credentials):
        self.creds = creds
        self._app_token: tuple[str, float] | None = None

    @property
    def configured(self) -> bool:
        return self.creds.soundcloud

    # ---- tokens -------------------------------------------------------------------------
    def _basic(self) -> str:
        return base64.b64encode(f"{self.creds.soundcloud_id}:{self.creds.soundcloud_secret}".encode()).decode()

    def _token_request(self, data: dict) -> dict:
        try:
            return nethttp.post_json(TOKEN_URL, data=data, headers={"Authorization": f"Basic {self._basic()}"})
        except nethttp.HttpError as err:
            raise SoundCloudError(f"SoundCloud token request failed ({err}: {err.body[:200]})") from err

    def app_token(self) -> str:
        if self._app_token and time.time() < self._app_token[1] - 60:
            return self._app_token[0]
        data = self._token_request({"grant_type": "client_credentials"})
        self._app_token = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
        return self._app_token[0]

    def user_token(self) -> str | None:
        row = db.get().execute("SELECT * FROM accounts WHERE provider = 'soundcloud'").fetchone()
        if not row:
            return None
        if row["expires_at"] and time.time() > row["expires_at"] - 60 and row["refresh"]:
            data = self._token_request({"grant_type": "refresh_token", "refresh_token": unseal(row["refresh"]),
                                        "client_id": self.creds.soundcloud_id,
                                        "client_secret": self.creds.soundcloud_secret})
            self._store(row["username"], data)
            return data["access_token"]
        return unseal(row["token"])

    def _store(self, username: str, data: dict) -> None:
        db.get().execute(
            "INSERT INTO accounts(provider, username, token, refresh, expires_at) VALUES ('soundcloud', ?, ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET username=excluded.username, token=excluded.token, "
            "refresh=excluded.refresh, expires_at=excluded.expires_at",
            (username, seal(data["access_token"]), seal(data["refresh_token"]) if data.get("refresh_token") else None,
             int(time.time()) + int(data.get("expires_in", 3600))))

    def _get(self, path: str, params: dict | None = None, *, user: bool = False):
        token = self.user_token() if user else (self.user_token() or self.app_token())
        if not token:
            raise SoundCloudError("Connect your SoundCloud account first.")
        try:
            return nethttp.get_json(API + path, params=params, headers={"Authorization": f"OAuth {token}"})
        except nethttp.HttpError as err:
            if err.status == 404:
                raise SoundCloudError("SoundCloud says this resource does not exist or is private.") from err
            if err.status == 429:
                raise SoundCloudError("SoundCloud rate limit reached. Try again later.") from err
            raise SoundCloudError(f"SoundCloud API error ({err})") from err

    # ---- resources ----------------------------------------------------------------------
    def resolve(self, url: str) -> ScMeta:
        return meta_from_api(self._get("/resolve", {"url": url}))

    def me(self) -> dict:
        return self._get("/me", user=True)

    def _collection(self, path: str, limit: int = 50) -> list[dict]:
        data = self._get(path, {"limit": limit, "linked_partitioning": "true"}, user=True)
        items = data.get("collection", data) if isinstance(data, dict) else data
        return [i for i in items if isinstance(i, dict)]

    def my_playlists(self) -> list[ScMeta]:
        return [meta_from_api(p) for p in self._collection("/me/playlists")]

    def liked_tracks(self) -> list[ScMeta]:
        return [meta_from_api(t) for t in self._collection("/me/likes/tracks")]

    def liked_playlists(self) -> list[ScMeta]:
        return [meta_from_api(p) for p in self._collection("/me/likes/playlists")]

    # ---- OAuth (authorization code + PKCE, loopback redirect) ---------------------------
    def connect(self, timeout: float = 300, open_url=webbrowser.open) -> str:
        if not self.configured:
            raise SoundCloudError("Add your SoundCloud client ID and secret in Settings first.")
        redirect = urllib.parse.urlsplit(self.creds.soundcloud_redirect)
        if redirect.hostname not in ("127.0.0.1", "localhost") or redirect.scheme != "http":
            raise SoundCloudError("The redirect URI must be a local http://127.0.0.1:<port>/... address.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        result: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parts = urllib.parse.urlsplit(self.path)
                if parts.path != redirect.path:
                    self.send_error(404)
                    return
                q = urllib.parse.parse_qs(parts.query)
                if q.get("state", [""])[0] != state:
                    result["error"] = "State mismatch: possible forged request. Try connecting again."
                elif "error" in q:
                    result["error"] = q.get("error_description", q["error"])[0]
                else:
                    result["code"] = q.get("code", [""])[0]
                body = ("<html><body style='font-family:Segoe UI;background:#0b0b0c;color:#eee;padding:40px'>"
                        "<h2>Cuelog</h2><p>" + html.escape(result.get("error") or "SoundCloud connected. You can close this tab.")
                        + "</p></body></html>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer((redirect.hostname, redirect.port or 80), Handler)
        server.timeout = 1
        params = {"client_id": self.creds.soundcloud_id, "redirect_uri": self.creds.soundcloud_redirect,
                  "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256", "state": state}
        open_url(AUTHORIZE_URL + "?" + urllib.parse.urlencode(params))
        deadline = time.time() + timeout
        try:
            while not result and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if "error" in result:
            raise SoundCloudError(result["error"])
        if not result.get("code"):
            raise SoundCloudError("Timed out waiting for SoundCloud authorization.")
        data = self._token_request({"grant_type": "authorization_code", "code": result["code"],
                                    "redirect_uri": self.creds.soundcloud_redirect, "code_verifier": verifier,
                                    "client_id": self.creds.soundcloud_id,
                                    "client_secret": self.creds.soundcloud_secret})
        token = data["access_token"]
        me = nethttp.get_json(API + "/me", headers={"Authorization": f"OAuth {token}"})
        self._store(me.get("username", "SoundCloud user"), data)
        return me.get("username", "")

    @staticmethod
    def disconnect() -> None:
        db.get().execute("DELETE FROM accounts WHERE provider = 'soundcloud'")


def fetch_metadata(raw_url: str, creds: Credentials) -> ScMeta:
    """Best available metadata for a pasted link: official API when configured, else oEmbed."""
    url, kind = resolve_link(raw_url)
    client = SoundCloudClient(creds)
    meta = None
    if client.configured:
        try:
            meta = client.resolve(url)
        except SoundCloudError:
            meta = None
    if meta is None:
        meta = oembed(url)
    meta.url = meta.url or url
    if kind == "playlist":
        meta.kind = "playlist"
    return meta
