"""Last.fm API client: desktop auth, recent tracks, track.scrobble."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from dataclasses import dataclass

from . import db, http
from .config import Credentials
from .secretstore import seal, unseal

API = "https://ws.audioscrobbler.com/2.0/"
AUTH_URL = "https://www.last.fm/api/auth/"
BATCH_MAX = 50

# https://www.last.fm/api/errorcodes
RETRYABLE = {8, 11, 16, 29}
AUTH_ERRORS = {4, 9, 10, 13, 14, 15, 26}
IGNORED_CODES = {
    0: "Accepted",
    1: "Artist was ignored by Last.fm",
    2: "Track was ignored by Last.fm",
    3: "Timestamp too old",
    4: "Timestamp too new (in the future)",
    5: "Daily scrobble limit exceeded",
}


class LastfmError(RuntimeError):
    def __init__(self, code: int | None, message: str):
        super().__init__(f"Last.fm error {code}: {message}" if code else message)
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    @property
    def auth_problem(self) -> bool:
        return self.code in AUTH_ERRORS

    def friendly(self) -> str:
        return {
            4: "Authentication failed. Reconnect Last.fm in Settings.",
            9: "Your Last.fm session expired or was revoked. Reconnect Last.fm in Settings.",
            10: "The Last.fm API key is invalid. Check it in Settings.",
            13: "Invalid request signature. Check the Last.fm shared secret in Settings.",
            14: "Waiting for you to approve Cuelog on Last.fm.",
            15: "The Last.fm authorization token expired. Start connecting again.",
            26: "This Last.fm API key was suspended.",
            29: "Last.fm rate limit reached. Cuelog will slow down and retry.",
            11: "Last.fm is temporarily offline. Cuelog will retry.",
            16: "Last.fm had a temporary error. Cuelog will retry.",
        }.get(self.code, str(self))


class MalformedResponse(RuntimeError):
    pass


@dataclass
class ScrobbleResult:
    accepted: bool
    code: int
    message: str
    corrected_artist: str = ""
    corrected_track: str = ""


def signature(params: dict, secret: str) -> str:
    text = "".join(f"{k}{v}" for k, v in sorted(params.items()) if k not in {"format", "callback", "api_sig"})
    return hashlib.md5((text + secret).encode("utf-8")).hexdigest()


def _plain(html_text: str) -> str:
    """Last.fm summaries are HTML with a trailing 'Read more on Last.fm' link."""
    import re
    t = re.sub(r"<a [^>]*>.*?</a>", "", html_text or "", flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _text(v) -> str:
    if isinstance(v, dict):
        return v.get("#text", "") or v.get("name", "")
    return v or ""


def parse_scrobble_response(data: dict, expected: int) -> list[ScrobbleResult]:
    """Validate a track.scrobble JSON response and return one result per submitted item."""
    if not isinstance(data, dict) or "scrobbles" not in data:
        raise MalformedResponse("Response has no 'scrobbles' element")
    block = data["scrobbles"]
    items = block.get("scrobble") if isinstance(block, dict) else None
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or len(items) != expected:
        raise MalformedResponse(f"Expected {expected} scrobble results, got {len(items) if isinstance(items, list) else 'none'}")
    out = []
    for it in items:
        msg = it.get("ignoredMessage", {})
        try:
            code = int(msg.get("code", 0)) if isinstance(msg, dict) else int(msg or 0)
        except (TypeError, ValueError) as err:
            raise MalformedResponse(f"Unreadable ignoredMessage code: {msg!r}") from err
        text = _text(msg) if isinstance(msg, dict) else ""
        out.append(ScrobbleResult(code == 0, code, text or IGNORED_CODES.get(code, f"Ignored (code {code})"),
                                  _text(it.get("artist")), _text(it.get("track"))))
    return out


PLACEHOLDER_IMAGE = "2a96cbd8b46e442fc41c2b86b821562f"   # Last.fm's grey star "no image" picture


def _real_image(url) -> str:
    return "" if not url or PLACEHOLDER_IMAGE in url else url


class LastfmClient:
    def __init__(self, creds: Credentials, transport=None):
        self.creds = creds
        self._transport = transport or self._http

    @property
    def configured(self) -> bool:
        return self.creds.lastfm

    @staticmethod
    def _http(method: str, params: dict, post: bool) -> tuple[int, str]:
        if post:
            status, body, _ = http.request("POST", API, data=params, timeout=40)
        else:
            status, body, _ = http.request("GET", API, params=params, timeout=40)
        return status, body.decode("utf-8", errors="replace")

    def call(self, method: str, params: dict | None = None, *, post: bool = False, session_key: str | None = None,
             signed: bool = False) -> dict:
        if not self.configured:
            raise LastfmError(None, "Last.fm API key and secret are not configured (Settings → Accounts).")
        p = {"method": method, "api_key": self.creds.lastfm_key, **(params or {})}
        if session_key:
            p["sk"] = session_key
        if session_key or signed:
            p["api_sig"] = signature(p, self.creds.lastfm_secret)
        p["format"] = "json"
        try:
            status, text = self._transport(method, p, post)
        except http.HttpError as err:
            # Last.fm returns JSON error bodies with 4xx/5xx status codes.
            try:
                data = json.loads(err.body)
                raise LastfmError(int(data.get("error", 0)), data.get("message", "")) from err
            except (ValueError, TypeError):
                raise LastfmError(None, f"Last.fm HTTP {err.status}") from err
        try:
            data = json.loads(text)
        except ValueError as err:
            raise MalformedResponse("Last.fm returned a non-JSON response") from err
        if isinstance(data, dict) and "error" in data:
            raise LastfmError(int(data["error"]), data.get("message", ""))
        return data

    # ---- auth (desktop flow) ---------------------------------------------------------
    def get_token(self) -> str:
        return self.call("auth.getToken", signed=True)["token"]

    def auth_url(self, token: str) -> str:
        return AUTH_URL + "?" + urllib.parse.urlencode({"api_key": self.creds.lastfm_key, "token": token})

    def get_session(self, token: str) -> tuple[str, str]:
        s = self.call("auth.getSession", {"token": token}, signed=True)["session"]
        return s["name"], s["key"]

    def wait_for_session(self, token: str, timeout: float = 300, interval: float = 3, cancelled=lambda: False):
        deadline = time.time() + timeout
        while time.time() < deadline and not cancelled():
            try:
                return self.get_session(token)
            except LastfmError as err:
                if err.code != 14:   # 14 = token not yet authorized
                    raise
            time.sleep(interval)
        raise LastfmError(15, "Timed out waiting for Last.fm approval")

    # ---- account storage --------------------------------------------------------------
    @staticmethod
    def store_session(username: str, key: str) -> None:
        db.get().execute(
            "INSERT INTO accounts(provider, username, token) VALUES ('lastfm', ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET username=excluded.username, token=excluded.token, "
            "connected_at=datetime('now')", (username, seal(key)))

    @staticmethod
    def account() -> tuple[str, str] | None:
        row = db.get().execute("SELECT username, token FROM accounts WHERE provider = 'lastfm'").fetchone()
        return (row["username"], unseal(row["token"])) if row else None

    @staticmethod
    def disconnect() -> None:
        db.get().execute("DELETE FROM accounts WHERE provider = 'lastfm'")

    def user_info(self, username: str) -> dict:
        return self.call("user.getInfo", {"user": username})["user"]

    def verify(self) -> dict:
        acct = self.account()
        if not acct:
            raise LastfmError(9, "Not connected")
        # A signed call proves the session key still works.
        info = self.call("user.getInfo", session_key=acct[1])["user"]
        return info

    # ---- reading history --------------------------------------------------------------
    def recent_tracks(self, username: str, start: int, end: int, max_pages: int = 60) -> list[tuple[str, str, int]]:
        out = []
        page = 1
        while True:
            data = self.call("user.getRecentTracks", {"user": username, "from": start, "to": end, "limit": 200, "page": page})
            rt = data.get("recenttracks") or {}
            items = rt.get("track") or []
            if isinstance(items, dict):
                items = [items]
            for t in items:
                if not isinstance(t, dict) or "date" not in t:
                    continue   # "now playing" entry has no date
                out.append((_text(t.get("artist")), t.get("name", ""), int(t["date"]["uts"])))
            total = int((rt.get("@attr") or {}).get("totalPages", 1) or 1)
            if page >= total:
                return out
            if page >= max_pages:
                raise LastfmError(None, "Too much Last.fm history in this range to check for duplicates safely")
            page += 1
            time.sleep(0.25)

    def profile_summary(self, username: str) -> dict:
        """Total scrobbles, avatar and the current / most recent track of a user (read-only)."""
        info = self.user_info(username)
        images = {i.get("size"): _real_image(i.get("#text")) for i in info.get("image") or [] if isinstance(i, dict)}
        out = {"name": info.get("name", username), "playcount": int(info.get("playcount") or 0),
               "url": info.get("url") or f"https://www.last.fm/user/{urllib.parse.quote(username)}",
               "avatar": images.get("large") or images.get("medium") or "", "track": None}
        data = self.call("user.getRecentTracks", {"user": username, "limit": 1})
        items = (data.get("recenttracks") or {}).get("track") or []
        if isinstance(items, dict):
            items = [items]
        if items:
            t = items[0]
            art = {i.get("size"): _real_image(i.get("#text")) for i in t.get("image") or [] if isinstance(i, dict)}
            out["track"] = {"artist": _text(t.get("artist")), "name": t.get("name", ""),
                            "now": (t.get("@attr") or {}).get("nowplaying") == "true",
                            "uts": int(t["date"]["uts"]) if isinstance(t.get("date"), dict) else None,
                            "art": art.get("extralarge") or art.get("large") or art.get("medium") or ""}
        return out

    def track_info(self, artist: str, track: str, username: str | None = None) -> dict:
        """Global + personal stats for a track (read-only)."""
        params = {"artist": artist, "track": track, "autocorrect": 1}
        if username:
            params["username"] = username
        t = self.call("track.getInfo", params).get("track") or {}
        album = t.get("album") or {}
        images = {i.get("size"): _real_image(i.get("#text")) for i in album.get("image") or [] if isinstance(i, dict)}
        upc = str(t.get("userplaycount", ""))
        return {
            "name": t.get("name", track), "artist": _text(t.get("artist")) or artist, "url": t.get("url", ""),
            "listeners": int(t.get("listeners") or 0), "playcount": int(t.get("playcount") or 0),
            "userplaycount": int(upc) if upc.isdigit() else None,
            "loved": t.get("userloved") == "1", "album": album.get("title", ""),
            "image": images.get("extralarge") or images.get("large") or "",
            "tags": [x.get("name", "") for x in ((t.get("toptags") or {}).get("tag") or []) if isinstance(x, dict)][:5],
            "summary": _plain((t.get("wiki") or {}).get("summary", "")),
        }

    def artist_info(self, artist: str, username: str | None = None) -> dict:
        params = {"artist": artist, "autocorrect": 1}
        if username:
            params["username"] = username
        a = self.call("artist.getInfo", params).get("artist") or {}
        stats = a.get("stats") or {}
        upc = str(stats.get("userplaycount", ""))
        return {
            "name": a.get("name", artist), "url": a.get("url", ""),
            "listeners": int(stats.get("listeners") or 0), "playcount": int(stats.get("playcount") or 0),
            "userplaycount": int(upc) if upc.isdigit() else None,
            "tags": [x.get("name", "") for x in ((a.get("tags") or {}).get("tag") or []) if isinstance(x, dict)][:5],
            "similar": [x.get("name", "") for x in ((a.get("similar") or {}).get("artist") or []) if isinstance(x, dict)][:5],
            "bio": _plain((a.get("bio") or {}).get("summary", "")),
        }

    def friends(self, username: str, limit: int = 30) -> list[dict]:
        """Friends with what they're playing now / last played (read-only)."""
        try:
            data = self.call("user.getFriends", {"user": username, "recenttracks": 1, "limit": limit})
        except LastfmError as err:
            if err.code == 6:      # Last.fm reports "no friends" as an error
                return []
            raise
        users = (data.get("friends") or {}).get("user") or []
        if isinstance(users, dict):
            users = [users]
        out = []
        for u in users:
            images = {i.get("size"): _real_image(i.get("#text")) for i in u.get("image") or [] if isinstance(i, dict)}
            rt = u.get("recenttrack") or {}
            out.append({"name": u.get("name", ""), "url": u.get("url", ""), "avatar": images.get("medium") or "",
                        "track": rt.get("name", ""), "artist": _text(rt.get("artist")),
                        "now": (rt.get("@attr") or {}).get("nowplaying") == "true"})
        return out

    # ---- account statistics (read-only) ------------------------------------------------
    @staticmethod
    def _images(obj) -> dict:
        return {i.get("size"): _real_image(i.get("#text")) for i in (obj or {}).get("image") or [] if isinstance(i, dict)}

    def overview(self, username: str) -> dict:
        u = self.user_info(username)
        num = lambda k: int(u.get(k) or 0)
        reg = u.get("registered") or {}
        return {"playcount": num("playcount"), "artists": num("artist_count"), "tracks": num("track_count"),
                "albums": num("album_count"), "registered": int(reg.get("unixtime") or reg.get("#text") or 0),
                "name": u.get("name", username)}

    def count_between(self, username: str, start: int, end: int) -> int:
        """Number of scrobbles in [start, end] using the 'total' attribute (one cheap request)."""
        data = self.call("user.getRecentTracks", {"user": username, "from": start, "to": end, "limit": 1})
        return int(((data.get("recenttracks") or {}).get("@attr") or {}).get("total") or 0)

    def recent(self, username: str, limit: int = 10) -> list[dict]:
        data = self.call("user.getRecentTracks", {"user": username, "limit": limit, "extended": 0})
        items = (data.get("recenttracks") or {}).get("track") or []
        if isinstance(items, dict):
            items = [items]
        out = []
        for t in items:
            img = self._images(t)
            out.append({"artist": _text(t.get("artist")), "track": t.get("name", ""), "album": _text(t.get("album")),
                        "art": img.get("medium") or img.get("large") or "",
                        "now": (t.get("@attr") or {}).get("nowplaying") == "true",
                        "uts": int(t["date"]["uts"]) if isinstance(t.get("date"), dict) else None, "url": t.get("url", "")})
        return out[:limit]

    def timestamps_between(self, username: str, start: int, end: int, max_pages: int = 15) -> list[int]:
        out, page = [], 1
        while page <= max_pages:
            data = self.call("user.getRecentTracks", {"user": username, "from": start, "to": end, "limit": 200, "page": page})
            rt = data.get("recenttracks") or {}
            items = rt.get("track") or []
            if isinstance(items, dict):
                items = [items]
            out += [int(t["date"]["uts"]) for t in items if isinstance(t, dict) and isinstance(t.get("date"), dict)]
            if page >= int((rt.get("@attr") or {}).get("totalPages") or 1):
                break
            page += 1
        return out

    def top_artists(self, username: str, period: str = "1month", limit: int = 8) -> list[tuple[str, int]]:
        data = self.call("user.getTopArtists", {"user": username, "period": period, "limit": limit})
        items = (data.get("topartists") or {}).get("artist") or []
        if isinstance(items, dict):
            items = [items]
        return [(a.get("name", ""), int(a.get("playcount") or 0)) for a in items]

    def top_albums(self, username: str, period: str = "1month", limit: int = 8) -> list[dict]:
        data = self.call("user.getTopAlbums", {"user": username, "period": period, "limit": limit})
        items = (data.get("topalbums") or {}).get("album") or []
        if isinstance(items, dict):
            items = [items]
        out = []
        for a in items:
            img = self._images(a)
            out.append({"name": a.get("name", ""), "artist": _text(a.get("artist")), "plays": int(a.get("playcount") or 0),
                        "image": img.get("extralarge") or img.get("large") or "", "url": a.get("url", "")})
        return out

    def top_artists_full(self, username: str, period: str = "1month", limit: int = 8) -> list[dict]:
        data = self.call("user.getTopArtists", {"user": username, "period": period, "limit": limit})
        items = (data.get("topartists") or {}).get("artist") or []
        if isinstance(items, dict):
            items = [items]
        return [{"name": a.get("name", ""), "plays": int(a.get("playcount") or 0), "url": a.get("url", "")} for a in items]

    def top_tracks(self, username: str, period: str = "1month", limit: int = 8) -> list[tuple[str, str, int]]:
        data = self.call("user.getTopTracks", {"user": username, "period": period, "limit": limit})
        items = (data.get("toptracks") or {}).get("track") or []
        if isinstance(items, dict):
            items = [items]
        return [(t.get("name", ""), _text(t.get("artist")), int(t.get("playcount") or 0)) for t in items]

    def update_now_playing(self, artist: str, track: str, album: str | None = None, duration: int | None = None,
                           session_key: str | None = None) -> dict:
        """track.updateNowPlaying: shows the song as 'scrobbling now' on Last.fm. Does not scrobble."""
        sk = session_key or (self.account() or (None, None))[1]
        if not sk:
            raise LastfmError(9, "Not connected")
        params = {"artist": artist, "track": track}
        if album:
            params["album"] = album
        if duration:
            params["duration"] = str(int(duration))
        return self.call("track.updateNowPlaying", params, post=True, session_key=sk)

    # ---- scrobbling -------------------------------------------------------------------
    def scrobble(self, items: list[dict], session_key: str) -> list[ScrobbleResult]:
        """items: dicts with artist, track, timestamp and optional album, duration."""
        if not 1 <= len(items) <= BATCH_MAX:
            raise ValueError(f"track.scrobble accepts 1..{BATCH_MAX} items")
        body = {}
        for i, it in enumerate(items):
            if not (it.get("artist") or "").strip() or not (it.get("track") or "").strip():
                raise ValueError("Every scrobble needs an artist and a track title")
            body[f"artist[{i}]"] = it["artist"]
            body[f"track[{i}]"] = it["track"]
            body[f"timestamp[{i}]"] = str(int(it["timestamp"]))
            if it.get("album"):
                body[f"album[{i}]"] = it["album"]
            if it.get("duration"):
                body[f"duration[{i}]"] = str(int(it["duration"]))
        data = self.call("track.scrobble", body, post=True, session_key=session_key)
        return parse_scrobble_response(data, len(items))
