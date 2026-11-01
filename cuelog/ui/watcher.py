"""Detect SoundCloud sets playing anywhere on this PC and scrobble them live.

* A set from your library is playing → Cuelog follows its position and scrobbles each song once heard.
* A long SoundCloud upload that isn't in your library → a pop-up offers to add it.
"""
from __future__ import annotations

import time
from urllib.parse import quote

from PySide6.QtCore import QObject, QThread, Qt, Signal

from .. import db, media, repo
from ..lastfm import LastfmClient
from ..live import LiveSession
from .state import app_state
from .widgets import button, label, open_url
from .workers import run

POLL_S = 3.0
IDLE_FINISH_S = 20 * 60        # a set paused this long is considered finished
GONE_FINISH_S = 90             # the player disappeared


class MediaPoller(QThread):
    snapshot = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            if db.setting("detect_soundcloud"):
                try:
                    self.snapshot.emit(media.sessions())
                except RuntimeError:
                    return
            end = time.time() + POLL_S
            while time.time() < end and not self._stop:
                time.sleep(0.2)


class SoundCloudWatcher(QObject):
    status = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.live: LiveSession | None = None
        self.live_key = ""
        self.last_seen = 0.0
        self.last_playing = 0.0
        self.prompted: set[str] = set()
        self._dialog = None
        self.poller = MediaPoller(self)
        self.poller.snapshot.connect(self.on_snapshot, Qt.QueuedConnection)
        if media.available():
            self.poller.start()

    def stop(self):
        self.poller.stop()
        self.poller.wait(3000)
        self._finish()

    # ---- main loop --------------------------------------------------------------------------
    def on_snapshot(self, items: list):
        now = time.time()
        own = [np for np in items if not any(x in np.app.lower() for x in ("spotify", "python", "cuelog"))]
        sets = [s for s in repo.list_sets() if s["track_count"]]
        ordered = sorted(own, key=lambda np: not np.playing)
        for np in ordered:
            s = media.match_set(np, sets)
            if s:
                self._follow(np, s, now)
                return
        if self.live and now - self.last_seen > GONE_FINISH_S:
            self._finish()
        for np in ordered:
            if np.playing and media.looks_like_a_set(np):
                self._offer(np)
                break

    def _follow(self, np: media.NowPlaying, s: dict, now: float):
        user = app_state().lastfm_user() if app_state().creds.lastfm else None
        if not user or not db.setting("live_scrobble"):
            self.status.emit(f"Playing on SoundCloud: {s['title']}")
            return
        if self.live is None or self.live.set_id != s["id"]:
            self._finish()
            client = LastfmClient(app_state().creds)

            def now_playing(seg):
                run(lambda: client.update_now_playing(seg.artist, seg.track, seg.album, seg.song_duration_s),
                    lambda _: app_state().lastfm_activity.emit(), lambda _e: None)
            self.live = LiveSession(s["id"], label="SoundCloud", now_playing=now_playing)
            self.live_key = np.key
            app_state().toast(f"Following “{s['title']}” on SoundCloud. Songs are scrobbled as you listen.", "success")
        self.last_seen = now
        if np.playing:
            self.last_playing = now
        n = self.live.update(np.position, np.playing)
        if n:
            app_state().wake_runner()
            app_state().changed()
        seg = self.live.current()
        what = f"{seg.artist} – {seg.track}" if seg and seg.track else s["title"]
        state = "Scrobbling from SoundCloud" if np.playing else "Paused on SoundCloud"
        self.status.emit(f"{state} · {what} · {len(self.live.done)} sent")
        if not np.playing and now - self.last_playing > IDLE_FINISH_S:
            self._finish()

    def _finish(self):
        if self.live is not None:
            n = self.live.finish()
            if n:
                app_state().wake_runner()
            if self.live.done:
                app_state().toast(f"Finished following “{self.live.s['title']}”: {len(self.live.done)} songs scrobbled.",
                                  "success")
            app_state().changed()
        self.live = None
        self.live_key = ""
        self.status.emit("")

    # ---- unknown set ------------------------------------------------------------------------
    def _offer(self, np: media.NowPlaying):
        key = np.key
        dismissed = set(db.setting("sc_dismissed") or [])
        if key in self.prompted or key in dismissed or self._dialog is not None:
            return
        self.prompted.add(key)
        from .dialogs.base import Dialog
        d = Dialog(self.window, "Playing on SoundCloud", f"{np.title}" + (f" · {np.artist}" if np.artist else ""),
                   icon="headphones", width=460)
        d.setWindowModality(Qt.NonModal)
        d.body.addWidget(label("This set isn't in your library yet. Add it and Cuelog scrobbles its songs while you "
                               "listen, now and every time you play it again.", "Secondary", wrap=True))

        def never():
            db.set_setting("sc_dismissed", sorted(dismissed | {key}))
            d.reject()

        def add():
            d.accept()
            open_url("https://soundcloud.com/search/sounds?q=" + quote(np.title))
            app_state().go("discover", {"hint": np.title})

        d.footer_left.addWidget(button("Never for this set", None, "Ghost", never))
        d.add_actions("Add set", add, cancel="Not now")
        d.finished.connect(lambda _r: setattr(self, "_dialog", None))
        self._dialog = d
        d.show()
        d.raise_()
