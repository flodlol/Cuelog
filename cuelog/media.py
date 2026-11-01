"""What's playing on this PC, via Windows' media controls (System Media Transport Controls).

Browsers and SoundCloud desktop apps publish the current track there (title, uploader, position,
length). This is the documented Windows API behind the media overlay; Cuelog only reads it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from difflib import SequenceMatcher

from .matching import norm

PLAYING = 4


@dataclass
class NowPlaying:
    app: str
    title: str
    artist: str
    position: float      # seconds, extrapolated to "now" while playing
    duration: float      # seconds (0 when unknown)
    playing: bool

    @property
    def is_soundcloud_app(self) -> bool:
        return "soundcloud" in self.app.lower()

    @property
    def is_browser(self) -> bool:
        a = self.app.lower()
        return any(b in a for b in ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc"))

    @property
    def key(self) -> str:
        return f"{norm(self.title)}|{norm(self.artist)}"


def available() -> bool:
    try:
        import winrt.windows.media.control as _m  # noqa: F401
        del _m
        return True
    except Exception:
        return False


async def _read() -> list[NowPlaying]:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    mgr = await Manager.request_async()
    out = []
    for s in mgr.get_sessions():
        try:
            info = await s.try_get_media_properties_async()
            tl = s.get_timeline_properties()
            pb = s.get_playback_info()
        except Exception:
            continue
        playing = int(pb.playback_status) == PLAYING
        pos = tl.position.total_seconds() if tl.position else 0.0
        if playing and tl.last_updated_time:
            age = time.time() - tl.last_updated_time.timestamp()
            if 0 < age < 6 * 3600:
                pos += age
        dur = tl.end_time.total_seconds() if tl.end_time else 0.0
        if dur:
            pos = min(pos, dur)
        out.append(NowPlaying(s.source_app_user_model_id or "", info.title or "", info.artist or "", pos, dur, playing))
    return out


def sessions() -> list[NowPlaying]:
    """Current media sessions on this PC (empty when unavailable)."""
    import asyncio
    try:
        return asyncio.run(_read())
    except Exception:
        return []


def match_set(np: NowPlaying, sets: list[dict]) -> dict | None:
    """The saved set this media session is playing, if any (title similarity, uploader, length)."""
    t = norm(np.title)
    if len(t) < 4:
        return None
    best, best_score = None, 0.0
    for s in sets:
        st = norm(s["title"])
        if not st:
            continue
        score = SequenceMatcher(None, t, st).ratio()
        if len(t) >= 10 and (t in st or st in t):
            score = max(score, 0.9)
        if np.artist and s.get("uploader") and norm(np.artist) == norm(s["uploader"]):
            score += 0.1
        if np.duration and s.get("duration_s"):
            if abs(np.duration - s["duration_s"]) <= max(20, s["duration_s"] * 0.02):
                score += 0.15
            elif abs(np.duration - s["duration_s"]) > s["duration_s"] * 0.2:
                score -= 0.3
        if score > best_score:
            best, best_score = s, score
    return best if best_score >= 0.85 else None


def looks_like_a_set(np: NowPlaying) -> bool:
    """A long SoundCloud upload: worth offering to add."""
    return np.is_soundcloud_app and np.duration >= 15 * 60
