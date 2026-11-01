"""Now-playing context from Last.fm: song and artist stats, and friends. Cached, read-only."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .lastfm import LastfmClient, LastfmError
from .matching import norm

_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: tuple, ttl: float, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def song(client: LastfmClient, username: str | None, artist: str, track: str) -> dict:
    """Track + artist info for the song that's playing, including your own play counts."""
    def load():
        out: dict = {"track": None, "artist": None}
        try:
            out["track"] = client.track_info(artist, track, username)
        except LastfmError:
            pass
        try:
            out["artist"] = client.artist_info(artist, username)
        except LastfmError:
            pass
        return out
    return _cached(("song", norm(artist), norm(track), username), 600, load)


def friends(client: LastfmClient, username: str) -> list[dict]:
    return _cached(("friends", username), 300, lambda: client.friends(username))


def friends_who_played(client: LastfmClient, username: str, artist: str, track: str, max_friends: int = 12) -> list[dict]:
    """Which of your friends have played this song (and how often). One lookup per friend, so capped."""
    def load():
        people = friends(client, username)[:max_friends]

        def one(f):
            try:
                info = client.track_info(artist, track, f["name"])
            except LastfmError:
                return None
            n = info.get("userplaycount") or 0
            return {**f, "plays": n} if n else None

        with ThreadPoolExecutor(max_workers=4) as pool:
            found = [r for r in pool.map(one, people) if r]
        return sorted(found, key=lambda r: -r["plays"])
    return _cached(("friend-plays", username, norm(artist), norm(track)), 900, load)


# ---- account statistics for Home ------------------------------------------------------------
def overview(client: LastfmClient, username: str) -> dict:
    return _cached(("overview", username), 300, lambda: client.overview(username))


def daily_counts(client: LastfmClient, username: str, days: int = 30) -> list[tuple[str, int]]:
    """Scrobbles per local day for the last ``days`` days, oldest first (one tiny request per day)."""
    import time as _t
    from datetime import datetime, timedelta

    def load():
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        spans = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            start = int(_t.mktime(d.timetuple()))
            end = int(_t.mktime((d + timedelta(days=1)).timetuple())) - 1
            spans.append((d.date().isoformat(), start, end))
        with ThreadPoolExecutor(max_workers=4) as pool:
            counts = list(pool.map(lambda s: client.count_between(username, s[1], s[2]), spans))
        return [(d, n) for (d, _, _), n in zip(spans, counts)]
    return _cached(("daily", username, days), 600, load)


def hours(client: LastfmClient, username: str, days: int = 7) -> list[int]:
    import time as _t
    from datetime import datetime

    def load():
        now = int(_t.time())
        out = [0] * 24
        for ts in client.timestamps_between(username, now - days * 86400, now):
            out[datetime.fromtimestamp(ts).hour] += 1
        return out
    return _cached(("hours", username, days), 900, load)


def top_artists(client: LastfmClient, username: str, period: str) -> list[tuple[str, int]]:
    return _cached(("top-artists", username, period), 900, lambda: client.top_artists(username, period))


def top_tracks(client: LastfmClient, username: str, period: str) -> list[tuple[str, str, int]]:
    return _cached(("top-tracks", username, period), 900, lambda: client.top_tracks(username, period))


def recent(client: LastfmClient, username: str) -> list[dict]:
    return _cached(("recent", username), 60, lambda: client.recent(username, 8))


def forget(username: str | None = None) -> None:
    """Drop cached account stats (e.g. after an import) so Home shows fresh numbers."""
    with _lock:
        for k in [k for k in _cache if k[0] in ("overview", "daily", "recent", "hours") and (not username or username in k)]:
            _cache.pop(k, None)


def top_artists_full(client: LastfmClient, username: str, period: str) -> list[dict]:
    return _cached(("top-artists-full", username, period), 900, lambda: client.top_artists_full(username, period, 18))


def top_albums(client: LastfmClient, username: str, period: str) -> list[dict]:
    return _cached(("top-albums", username, period), 900, lambda: client.top_albums(username, period, 18))
