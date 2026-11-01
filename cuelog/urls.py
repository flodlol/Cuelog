"""URL validation and normalisation for SoundCloud and Spotify links."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit


class UrlError(ValueError):
    """A URL that Cuelog refuses to fetch or does not understand."""


SC_HOSTS = {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
SC_SHORT_HOSTS = {"on.soundcloud.com", "soundcloud.app.goo.gl"}
SC_API_HOSTS = {"api.soundcloud.com"}
SPOTIFY_HOSTS = {"open.spotify.com", "play.spotify.com"}

# First path segments on soundcloud.com that are never a user profile.
_SC_RESERVED = {"discover", "search", "stream", "you", "upload", "charts", "pages", "settings",
                "messages", "notifications", "terms-of-use", "jobs", "imprint", "signin", "logout"}
_SLUG = re.compile(r"^[A-Za-z0-9_\-\.%]+$")
_SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{22}$")


@dataclass(frozen=True)
class SoundCloudUrl:
    url: str            # canonical https://soundcloud.com/... URL (or a short link to resolve)
    kind: str           # track | playlist | user | short | api
    needs_resolve: bool = False


def _split(raw: str):
    raw = (raw or "").strip()
    if not raw:
        raise UrlError("Paste a SoundCloud link first.")
    if len(raw) > 2048:
        raise UrlError("That link is too long to be a SoundCloud URL.")
    if any(c.isspace() for c in raw):
        raw = raw.split()[0]
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:(?!//)", raw) and not re.match(r"^[^/:]+:\d+(/|$)", raw):
        raise UrlError("Only http(s) links are supported.")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as err:
        raise UrlError("That link is malformed.") from err
    if parts.scheme.lower() not in ("http", "https"):
        raise UrlError("Only http(s) links are supported.")
    if parts.username or parts.password or port not in (None, 80, 443):
        raise UrlError("Links with credentials or custom ports are not allowed.")
    return parts


def parse_soundcloud(raw: str) -> SoundCloudUrl:
    parts = _split(raw)
    host = (parts.hostname or "").lower()
    if host in SC_SHORT_HOSTS:
        path = parts.path.rstrip("/")
        if not path or not _SLUG.match(path.strip("/").replace("/", "")):
            raise UrlError("That short link looks incomplete.")
        return SoundCloudUrl(urlunsplit(("https", host, path, "", "")), "short", True)
    if host in SC_API_HOSTS:
        m = re.match(r"^/(tracks|playlists)/(\d+)/?$", parts.path)
        if not m:
            raise UrlError("Unsupported api.soundcloud.com link.")
        return SoundCloudUrl(f"https://api.soundcloud.com/{m.group(1)}/{m.group(2)}",
                             "track" if m.group(1) == "tracks" else "playlist", True)
    if host not in SC_HOSTS:
        raise UrlError("That is not a soundcloud.com link.")
    segs = [s for s in unquote(parts.path).split("/") if s]
    if not segs:
        raise UrlError("That link points to the SoundCloud homepage, not a track or playlist.")
    if segs[0].lower() in _SC_RESERVED:
        raise UrlError("That SoundCloud page is not a track or playlist.")
    for s in segs:
        if not _SLUG.match(quote(s, safe="")):
            raise UrlError("The link contains unexpected characters.")
    # Drop share/tracking suffixes such as /s-AbCdE secret tokens are kept, query is dropped.
    if len(segs) == 1:
        kind = "user"
    elif len(segs) >= 3 and segs[1] == "sets":
        kind = "playlist"
        segs = segs[:4] if len(segs) >= 4 and segs[3].startswith("s-") else segs[:3]
    elif segs[1] in ("likes", "tracks", "sets", "reposts", "albums", "popular-tracks", "followers", "following"):
        kind = "user"
        segs = segs[:2]
    else:
        kind = "track"
        segs = segs[:3] if len(segs) >= 3 and segs[2].startswith("s-") else segs[:2]
    path = "/" + "/".join(quote(s, safe="") for s in segs)
    return SoundCloudUrl(urlunsplit(("https", "soundcloud.com", path, "", "")), kind)


def is_allowed_soundcloud_redirect(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
        return urlsplit(url).scheme == "https" and (host in SC_HOSTS or host in SC_API_HOSTS)
    except ValueError:
        return False


def spotify_track_id(raw: str) -> str | None:
    """Return the track id for open.spotify.com/track/<id> or spotify:track:<id> links, else None."""
    raw = (raw or "").strip()
    m = re.match(r"^spotify:track:([A-Za-z0-9]{22})$", raw)
    if m:
        return m.group(1)
    if "spotify.com" not in raw:
        return None
    try:
        parts = _split(raw)
    except UrlError:
        return None
    if (parts.hostname or "").lower() not in SPOTIFY_HOSTS:
        return None
    segs = [s for s in parts.path.split("/") if s]
    if segs and segs[0].startswith("intl-"):
        segs = segs[1:]
    if len(segs) >= 2 and segs[0] == "track" and _SPOTIFY_ID.match(segs[1]):
        return segs[1]
    return None


def spotify_track_url(track_id: str) -> str:
    if not _SPOTIFY_ID.match(track_id or ""):
        raise UrlError("Invalid Spotify track id")
    return f"https://open.spotify.com/track/{track_id}"


def safe_external_url(url: str | None) -> str | None:
    """Only allow https links to known music sites to be opened from the UI."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    allowed = SC_HOSTS | SPOTIFY_HOSTS | {"www.last.fm", "last.fm", "musicbrainz.org", "secure.soundcloud.com",
                                         "developer.spotify.com", "soundcloud.com"}
    if parts.scheme == "https" and host in allowed:
        return url
    return None


def query_param(url: str, name: str) -> str | None:
    values = parse_qs(urlsplit(url).query).get(name)
    return values[0] if values else None
