"""One shared audio player for 30-second song previews, plus the button that drives it."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .. import previews
from .widgets import IconTextButton
from . import theme as T
from .workers import run


class PreviewPlayer(QObject):
    """States per key: idle | loading | playing | missing | error."""
    changed = Signal(str, str)

    _inst: "PreviewPlayer | None" = None

    @classmethod
    def instance(cls) -> "PreviewPlayer":
        if cls._inst is None:
            cls._inst = PreviewPlayer()
        return cls._inst

    def __init__(self):
        super().__init__()
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.output.setVolume(0.8)
        self.player.setAudioOutput(self.output)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.errorOccurred.connect(lambda *_: self._set(self.key, "error"))
        self.key: str | None = None
        self.states: dict[str, str] = {}
        self.cache: dict[str, tuple[float, dict | None]] = {}   # preview links are signed and expire

    def state(self, key: str) -> str:
        return self.states.get(key, "idle")

    def _set(self, key: str | None, state: str):
        if key is None:
            return
        self.states[key] = state
        self.changed.emit(key, state)

    def stop(self):
        if self.key:
            self.player.stop()
            if self.state(self.key) in ("playing", "loading"):
                self._set(self.key, "idle")
        self.key = None

    def toggle(self, key: str, artist: str, title: str, duration_ms: int | None):
        if self.key == key and self.state(key) in ("playing", "loading"):
            self.stop()
            return
        self.stop()
        self.key = key
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < 1800:
            self._play(key, hit[1])
            return
        self._set(key, "loading")
        run(lambda: previews.find_preview(artist, title, duration_ms),
            lambda res: (self.cache.__setitem__(key, (time.time(), res)), self._play(key, res)),
            lambda _err: self._set(key, "error") if self.key == key else None)

    def _play(self, key: str, res: dict | None):
        if self.key != key:
            return
        if not res:
            self._set(key, "missing")
            self.key = None
            return
        self.player.setSource(QUrl(res["url"]))
        self.player.play()
        self._set(key, "playing")

    def _status(self, status):
        if status == QMediaPlayer.EndOfMedia and self.key:
            self._set(self.key, "idle")
            self.key = None


class PreviewButton(IconTextButton):
    LABELS = {"idle": ("Preview", "play"), "loading": ("Loading…", "volume"), "playing": ("Stop", "stop"),
              "missing": ("No preview", "x"), "error": ("Preview failed", "x")}

    def __init__(self, key: str, artist: str, title: str, duration_ms: int | None, compact: bool = False):
        super().__init__("")
        self.key, self.artist, self.title_, self.duration_ms, self.compact = key, artist, title, duration_ms, compact
        self.setObjectName("Ghost")
        self.setToolTip("Play a 30-second preview of this exact recording (from Deezer)")
        self.clicked.connect(lambda: PreviewPlayer.instance().toggle(self.key, self.artist, self.title_, self.duration_ms))
        PreviewPlayer.instance().changed.connect(self._changed)
        self._apply(PreviewPlayer.instance().state(key))

    def _changed(self, key: str, state: str):
        if key == self.key:
            self._apply(state)

    def _apply(self, state: str):
        text, ic = self.LABELS.get(state, self.LABELS["idle"])
        color = T.ACCENT if state == "playing" else T.MUTED if state in ("missing", "error") else T.TEXT_2
        self.setIcon(T.icon(ic, color, 16))
        self.setText("" if self.compact and state in ("idle", "playing") else text)
        self.setEnabled(state not in ("missing",))
        if state == "missing":
            self.setToolTip("No preview of this exact version is available")
