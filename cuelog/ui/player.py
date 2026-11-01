"""Official SoundCloud player (Widget API) with listened-segment tracking.

Playback runs in Cuelog's own player window (Edge WebView2, see player_proc.py,
because Qt WebEngine cannot decode SoundCloud's AAC streams). Only that window
is tracked, via the documented Widget API events (PLAY, PAUSE, SEEK,
PLAY_PROGRESS, FINISH). Skipped-over parts of a mix never count. Nothing is
monitored outside the app. Qt WebEngine is still used invisibly to read
metadata (duration, description) from the widget.
"""
from __future__ import annotations

import json
import urllib.parse

import sys

from PySide6.QtCore import QFile, QIODevice, QObject, QProcess, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QVBoxLayout, QWidget)

from .. import config, db, insights, jobs, repo, scheduler
from ..live import PlaybackTracker  # noqa: F401  (re-exported for older imports)
from ..scheduler import TimestampError
from ..urls import UrlError, parse_soundcloud
from . import theme as T
from .actions import ensure_lastfm
from .components import ElidedLabel
from .state import app_state
from .timeline import Timeline
from .widgets import (Artwork, EmptyState, Toggle, button, card, clear_layout, fmt_duration, label, open_url,
                      page_header, scroll_page)
from .dialogs.base import Dialog
from .workers import run
from ..lastfm import LastfmClient

try:
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    WEBENGINE = True
except ImportError:  # pragma: no cover
    WEBENGINE = False


# ---- pure tracking logic (unit tested) -------------------------------------------------------
# ---- web plumbing ------------------------------------------------------------------------------
_profile = None


def profile():
    global _profile
    if _profile is None:
        _profile = QWebEngineProfile("cuelog")
        _profile.setPersistentStoragePath(str(config.data_dir() / "web"))
        _profile.setCachePath(str(config.cache_dir() / "web"))
        _profile.setHttpUserAgent(_profile.httpUserAgent())
    return _profile


def _channel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.ReadOnly):
        return bytes(f.readAll()).decode("utf-8")
    return ""


def widget_html(embed_url: str, visual: bool = True, auto_play: bool = False) -> str:
    try:
        if not embed_url.startswith("https://api.soundcloud.com/"):
            embed_url = parse_soundcloud(embed_url).url
    except UrlError:
        embed_url = "about:blank"
    src = ("https://w.soundcloud.com/player/?" + urllib.parse.urlencode({
        "url": embed_url, "auto_play": "true" if auto_play else "false", "visual": "true" if visual else "false",
        "show_comments": "false", "hide_related": "true", "show_reposts": "false", "show_teaser": "false",
        "color": "#ff5a1f"}))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;height:100%;background:{T.BG};overflow:hidden}}iframe{{border:0;width:100%;height:100%}}</style>
<script>{_channel_js()}</script>
<script src="https://w.soundcloud.com/player/api.js"></script></head><body>
<iframe id="sc" allow="autoplay; encrypted-media" src="{src}"></iframe>
<script>
var w = null, bridge = null;
function start() {{
  if (typeof SC === 'undefined') {{ bridge && bridge.error('The SoundCloud player script could not load.'); return; }}
  w = SC.Widget(document.getElementById('sc'));
  w.bind(SC.Widget.Events.READY, function() {{
    w.getCurrentSound(function(s) {{ bridge.sound(JSON.stringify(s || {{}})); }});
    w.getSounds(function(s) {{ bridge.sounds(JSON.stringify(s || [])); }});
    w.getDuration(function(d) {{ bridge.duration(d || 0); }});
    bridge.ready();
  }});
  ['PLAY','PAUSE','FINISH','SEEK'].forEach(function(n) {{
    w.bind(SC.Widget.Events[n], function(e) {{ bridge.event(n, (e && e.currentPosition) || 0); }});
  }});
  w.bind(SC.Widget.Events.PLAY_PROGRESS, function(e) {{ bridge.event('PROGRESS', (e && e.currentPosition) || 0); }});
  w.bind(SC.Widget.Events.ERROR, function() {{ bridge.error('SoundCloud reported a playback error (private, removed or region-blocked?)'); }});
}}
new QWebChannel(qt.webChannelTransport, function(ch) {{ bridge = ch.objects.bridge; start(); }});
window.cuelogSeek = function(ms) {{ if (w) w.seekTo(ms); }};
window.cuelogPlay = function() {{ if (w) w.play(); }};
window.cuelogPause = function() {{ if (w) w.pause(); }};
</script></body></html>"""


class Bridge(QObject):
    readySig = Signal()
    soundSig = Signal(dict)
    soundsSig = Signal(list)
    durationSig = Signal(float)
    eventSig = Signal(str, float)
    errorSig = Signal(str)

    @Slot()
    def ready(self):
        self.readySig.emit()

    @Slot(str)
    def sound(self, payload: str):
        try:
            d = json.loads(payload)
        except ValueError:
            d = {}
        self.soundSig.emit(d if isinstance(d, dict) else {})

    @Slot(str)
    def sounds(self, payload: str):
        try:
            d = json.loads(payload)
        except ValueError:
            d = []
        self.soundsSig.emit(d if isinstance(d, list) else [])

    @Slot(float)
    def duration(self, ms: float):
        self.durationSig.emit(float(ms))

    @Slot(str, float)
    def event(self, name: str, pos_ms: float):
        self.eventSig.emit(name, float(pos_ms))

    @Slot(str)
    def error(self, msg: str):
        self.errorSig.emit(msg)


class WidgetScanner(QObject):
    """Loads the widget invisibly to read duration, description and artwork (keyless)."""
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.data: dict = {}
        self.done = False

    def start(self):
        if not WEBENGINE:
            self.failed.emit("Embedded player components are missing; enter the duration manually.")
            return
        self.page = QWebEnginePage(profile(), self)
        self.page.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, True)
        self.page.setAudioMuted(True)
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel)
        self.bridge.soundSig.connect(self._sound)
        self.bridge.soundsSig.connect(self._sounds)
        self.bridge.durationSig.connect(lambda ms: self.data.setdefault("duration", int(ms) or None))
        self.bridge.errorSig.connect(self._fail)
        self.page.setHtml(widget_html(self.url, visual=False), QUrl("https://cuelog.local/"))
        QTimer.singleShot(25000, lambda: self._fail("SoundCloud's player did not respond; duration unknown for now."))

    def _sound(self, s: dict):
        self.data.update({"duration": s.get("duration") or self.data.get("duration"), "description": s.get("description") or "",
                          "artwork_url": s.get("artwork_url") or "", "title": s.get("title") or ""})
        QTimer.singleShot(400, self._finish)

    def _sounds(self, items: list):
        if len(items) > 1:
            self.data["sounds"] = items

    def _finish(self):
        if not self.done:
            self.done = True
            self.finished.emit(self.data)
            self.page.deleteLater()

    def _fail(self, msg: str):
        if not self.done:
            self.done = True
            self.failed.emit(msg)


# ---- player page -----------------------------------------------------------------------------
class ReviewDialog(Dialog):
    def __init__(self, set_id: int, planned, started: int, ended: int, partial: bool, parent=None):
        self.set_id, self.planned, self.started, self.ended, self.partial = set_id, planned, started, ended, partial
        s = repo.get_set(set_id)
        super().__init__(parent, "Save this session", f"{scheduler.fmt_local(started)} – {scheduler.fmt_local(ended, '%H:%M')}"
                         f" · {len(planned)} songs played long enough to count", s["artwork_url"] or "", width=560)
        self.resize(580, 620)
        self.body.addWidget(label("Untick anything you didn't actually hear.", "Muted"))
        self.list = QListWidget()
        self.list.setSpacing(1)
        for p in planned:
            it = QListWidgetItem(f"{scheduler.fmt_local(p.timestamp, '%H:%M')}    {p.segment.artist} – {p.segment.track}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setSizeHint(QSize(400, 32))
            self.list.addItem(it)
        self.body.addWidget(self.list, 1)
        user = app_state().lastfm_user()
        self.submit = Toggle("Send to Last.fm now", bool(user))
        self.submit.setEnabled(bool(user))
        self.footer_left.addWidget(self.submit)
        self.add_actions("Save session", self.save, cancel="Back")
        self.session_id = None

    def save(self):
        chosen = [p for i, p in enumerate(self.planned) if self.list.item(i).checkState() == Qt.Checked]
        try:
            self.session_id = repo.create_player_session(self.set_id, chosen, self.started, self.ended,
                                                         self.partial or len(chosen) < len(self.planned))
        except TimestampError as err:
            from .dialogs.base import notify
            notify(self, "Can't save this listen", str(err), kind="error")
            return
        if self.submit.isChecked() and ensure_lastfm(self):
            jobs.create_job(session_ids=[self.session_id], label="In-app listen")
            app_state().wake_runner()
        app_state().toast("Listening session saved", "success")
        app_state().changed()
        self.accept()


class _Stat(QWidget):
    def __init__(self, caption: str):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.value = label("—", "H2")
        self.caption = label(caption, "Small")
        v.addWidget(self.value)
        v.addWidget(self.caption)

    def set(self, value, caption: str | None = None):
        self.value.setText(value if isinstance(value, str) else (f"{value:,}" if value is not None else "—"))
        if caption is not None:
            self.caption.setText(caption)


def _tag(text: str) -> QLabel:
    t = QLabel(text)
    t.setStyleSheet(f"color:{T.TEXT_2}; border:1px solid {T.BORDER_STRONG}; border-radius: 3px; padding:1px 7px; font-size:11px;")
    return t


class PlayerPage(QWidget):
    """Plays a saved set in the player window and follows along.

    Shows the song that's playing with your Last.fm stats for it, what's next, and which friends
    have played it. With live scrobbling on, each song is sent to Last.fm as "now playing" and
    scrobbled once you've heard enough; otherwise you review the session at the end.
    """

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.set_id: int | None = None
        self.tracker = PlaybackTracker()
        self.duration = 0
        self.position = 0.0
        self.segs = []
        self.proc: QProcess | None = None
        self._buf = b""
        self._shown_pos: int | None = None
        self._reset_live()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, _, lay = scroll_page()
        outer.addWidget(area)
        self.picker = QComboBox()
        self.picker.setMinimumWidth(300)
        self.picker.setAccessibleName("Choose a set to play")
        self.picker.activated.connect(lambda _: self.load(self.picker.currentData()))
        head, _ = page_header("Player", None, self.picker)
        lay.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(T.S5)
        lay.addLayout(self.body)
        lay.addStretch(1)
        self.tick = QTimer(self, interval=1000, timeout=self._update_ui)

    def _reset_live(self):
        self.live_session: int | None = None
        self.live_job: int | None = None
        self.live_done: set[int] = set()
        self.now_playing_pos: int | None = None

    # ---- lifecycle ---------------------------------------------------------------------------
    def has_unsaved(self) -> bool:
        return self.tracker.heard_seconds() > 30 and not self.live_session

    def open(self, arg=None):
        sets = [s for s in repo.list_sets(sort="recent") if s["source_url"]]
        self.picker.blockSignals(True)
        self.picker.clear()
        self.picker.addItem("Choose a set…", None)
        for s in sets:
            self.picker.addItem(s["title"], s["id"])
        self.picker.blockSignals(False)
        target = arg.get("set_id") if isinstance(arg, dict) else None
        seek = arg.get("seek") if isinstance(arg, dict) else None
        if target and target != self.set_id:
            self.load(target, seek)
            return
        if target and seek is not None:
            self.seek(seek)
        if self.set_id:
            self.picker.setCurrentIndex(max(0, self.picker.findData(self.set_id)))
            self._sync_live_toggle()
        else:
            self._empty(sets)

    def refresh(self):
        pass

    def _empty(self, sets=None):
        clear_layout(self.body)
        self.picker.setCurrentIndex(0)
        sets = sets if sets is not None else [s for s in repo.list_sets(sort="recent") if s["source_url"]]
        if not sets:
            self.body.addWidget(EmptyState("headphones", "Nothing to play yet",
                                           "Add a SoundCloud set first, then play it here. Cuelog scrobbles each song "
                                           "while you listen."))
            return
        self.body.addWidget(label("Pick a set to play. Cuelog follows along and scrobbles each song once you've heard "
                                  "enough of it.", "Muted", wrap=True))
        box, bl = card()
        bl.setContentsMargins(T.S3, T.S3, T.S3, T.S3)
        bl.setSpacing(0)
        for s in sets[:12]:
            row = QHBoxLayout()
            row.setContentsMargins(T.S2, T.S2, T.S2, T.S2)
            row.setSpacing(T.S3)
            row.addWidget(Artwork(48, 6, s["artwork_url"] or "", icon="disc"))
            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(ElidedLabel(s["title"], "H3"))
            col.addWidget(ElidedLabel(" · ".join(b for b in (s["uploader"] or "", fmt_duration(s["duration_s"]),
                                                            f"{s['track_count']} songs") if b), "Small"))
            row.addLayout(col, 1)
            row.addWidget(button("Play", "play", None, lambda sid=s["id"]: self.load(sid)))
            w = QWidget()
            w.setLayout(row)
            bl.addWidget(w)
        self.body.addWidget(box)

    def load(self, set_id, seek: int | None = None):
        if not set_id:
            return
        if set_id != self.set_id and not self._confirm_leave():
            self.picker.setCurrentIndex(max(0, self.picker.findData(self.set_id)))
            return
        s = repo.get_set(set_id)
        if not s or not s["source_url"]:
            return
        self._end_live(final=True)
        self.stop_player()
        self.set_id = set_id
        self.s = s
        self.tracker.reset()
        self._reset_live()
        self._shown_pos = None
        self.duration = s["duration_s"] or 0
        self.position = float(seek or 0)
        self.segs = repo.segments_for_set(set_id)
        self.picker.setCurrentIndex(max(0, self.picker.findData(set_id)))
        clear_layout(self.body)

        cols = QHBoxLayout()
        cols.setSpacing(T.S5)
        left = QVBoxLayout()
        left.setSpacing(T.S5)
        right = QVBoxLayout()
        right.setSpacing(T.S5)
        cols.addLayout(left, 5)
        cols.addLayout(right, 3)
        self.body.addLayout(cols)

        # Now playing
        now, nl = card()
        nl.setContentsMargins(T.S5, T.S5, T.S5, T.S5)
        top = QHBoxLayout()
        top.setSpacing(T.S5)
        self.np_art = Artwork(176, 10)
        top.addWidget(self.np_art, 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(T.S1)
        self.np_where = label("", "Small")
        info.addWidget(self.np_where)
        self.np_title = label("", "Display", wrap=True)
        info.addWidget(self.np_title)
        self.np_artist = label("", "Secondary", wrap=True)
        self.np_artist.setStyleSheet(f"color:{T.TEXT_2}; font-size:14px;")
        info.addWidget(self.np_artist)
        self.np_album = label("", "Small", wrap=True)
        info.addWidget(self.np_album)
        self.np_tags = QHBoxLayout()
        self.np_tags.setSpacing(T.S1 + 2)
        tag_host = QWidget()
        tag_host.setLayout(self.np_tags)
        self.np_tags.setContentsMargins(0, T.S1, 0, 0)
        info.addWidget(tag_host)
        info.addStretch(1)
        stats = QHBoxLayout()
        stats.setSpacing(T.S6)
        self.st_song, self.st_artist, self.st_listeners = _Stat("your plays"), _Stat("your plays of the artist"), _Stat("listeners on Last.fm")
        for w in (self.st_song, self.st_artist, self.st_listeners):
            stats.addWidget(w)
        stats.addStretch(1)
        info.addLayout(stats)
        top.addLayout(info, 1)
        nl.addLayout(top)
        left.addWidget(now)

        # The set and playback
        play, pl = card()
        row = QHBoxLayout()
        row.setSpacing(T.S3)
        row.addWidget(Artwork(44, 6, s["artwork_url"] or "", icon="disc"))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(ElidedLabel(s["title"], "H3"))
        self.player_state = ElidedLabel("Starting the player window…", "Small")
        col.addWidget(self.player_state)
        row.addLayout(col, 1)
        self.window_btn = button("Show player", "headphones", None, self.show_player)
        row.addWidget(self.window_btn)
        sc = button("", "external", "Ghost", lambda: open_url(s["source_url"]), tooltip="Open on SoundCloud")
        row.addWidget(sc)
        pl.addLayout(row)
        self.timeline = Timeline(editable=False, height=72)
        self.timeline.seekRequested.connect(self.seek)
        pl.addWidget(self.timeline)
        foot = QHBoxLayout()
        foot.setSpacing(T.S3)
        self.live_toggle = Toggle("Scrobble while I listen")
        self.live_toggle.toggled.connect(self._live_toggled)
        foot.addWidget(self.live_toggle)
        self.live_state = ElidedLabel("", "Small")
        foot.addWidget(self.live_state, 1)
        self.np_stats = label("", "Small")
        foot.addWidget(self.np_stats)
        self.finish_btn = button("Finish", None, "Primary", self.finish)
        foot.addWidget(self.finish_btn)
        foot.addWidget(button("Discard", None, "Ghost", self.discard))
        pl.addLayout(foot)
        left.addWidget(play)
        left.addStretch(1)

        # Up next
        nxt, xl = card()
        xl.addWidget(label("Up next", "H2"))
        self.next_lay = QVBoxLayout()
        self.next_lay.setSpacing(T.S2)
        xl.addLayout(self.next_lay)
        right.addWidget(nxt)

        # About the artist
        self.about_card, al = card()
        self.about_title = label("", "H2")
        al.addWidget(self.about_title)
        self.about_text = label("", "Muted", wrap=True)
        self.about_text.setMaximumHeight(96)
        al.addWidget(self.about_text)
        self.about_similar = label("", "Small", wrap=True)
        al.addWidget(self.about_similar)
        self.about_link = button("More on Last.fm", "external", "Ghost", lambda: None)
        al.addWidget(self.about_link, 0, Qt.AlignLeft)
        self.about_card.setVisible(False)
        right.addWidget(self.about_card)

        # Friends
        self.friends_card, fl = card()
        fl.addWidget(label("Friends who've played this", "H2"))
        self.friends_lay = QVBoxLayout()
        self.friends_lay.setSpacing(T.S2)
        fl.addLayout(self.friends_lay)
        self.friends_card.setVisible(False)
        right.addWidget(self.friends_card)
        right.addStretch(1)

        self._sync_live_toggle()
        self._refresh_timeline()
        self._update_ui()
        self.tick.start()
        self.start_player(seek or 0)

    def _confirm_leave(self) -> bool:
        if not self.has_unsaved():
            return True
        from .dialogs.base import confirm
        return confirm(self, "Switch sets?", "What you've heard of the current set hasn't been saved and will be lost.",
                       "Switch", danger=True)

    # ---- now playing details -----------------------------------------------------------------
    def _current_segment(self):
        seg = next((sg for sg in self.segs if sg.start_s <= self.position < sg.end_s), None)
        return seg or (self.segs[0] if self.segs else None)

    def _show_song(self, seg):
        """Fill the now-playing card for ``seg`` and fetch its Last.fm details in the background."""
        if seg is None or seg.position == self._shown_pos:
            return
        self._shown_pos = seg.position
        t = repo.get_track(seg.set_track_id) if seg.set_track_id else None
        title = seg.track or (t["parsed_title"] if t else "")
        artist = seg.artist or (t["parsed_artist"] if t else "")
        others = (t["artists"][1:] if t else [])
        self.np_art.set_url((t or {}).get("art_url") or "")
        self.np_title.setText(title or "Unknown song")
        self.np_artist.setText(artist + (f"  ·  with {', '.join(others)}" if others else ""))
        self.np_album.setText(" · ".join(x for x in ((t or {}).get("lf_album") or "", ((t or {}).get("release_date") or "")[:4]) if x))
        self.np_where.setText(f"Song {seg.position} of {len(self.segs)} · {fmt_duration(seg.start_s) if seg.start_s else '0:00'} "
                              "in the mix")
        clear_layout(self.np_tags)
        for w in (self.st_song, self.st_artist, self.st_listeners):
            w.set(None)
        self.st_artist.set(None, f"your plays of {artist}" if artist else "your plays of the artist")

        clear_layout(self.next_lay)
        upcoming = [s for s in self.segs if s.start_s > seg.start_s][:4]
        if not upcoming:
            self.next_lay.addWidget(label("This is the last song.", "Muted"))
        for s in upcoming:
            tr = repo.get_track(s.set_track_id) if s.set_track_id else None
            row = QHBoxLayout()
            row.setSpacing(T.S3)
            row.addWidget(Artwork(36, 4, (tr or {}).get("art_url") or ""))
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(ElidedLabel(s.track or (tr["parsed_title"] if tr else ""), None))
            col.addWidget(ElidedLabel(s.artist or (tr["parsed_artist"] if tr else ""), "Small"))
            row.addLayout(col, 1)
            jump = button(fmt_duration(s.start_s) if s.start_s else "0:00", None, "Ghost",
                          lambda at=s.start_s: self.seek(at), tooltip="Jump to this song")
            row.addWidget(jump)
            w = QWidget()
            w.setLayout(row)
            self.next_lay.addWidget(w)

        self.about_card.setVisible(False)
        self.friends_card.setVisible(False)
        user = app_state().lastfm_user()
        client = LastfmClient(app_state().creds)
        if not (client.configured and artist and title):
            return
        key = seg.position

        def show(res):
            if self._shown_pos != key:
                return
            tr, ar = res.get("track") or {}, res.get("artist") or {}
            if tr.get("image") and not (t or {}).get("art_url"):
                self.np_art.set_url(tr["image"])
            self.st_song.set(tr.get("userplaycount") if user else None)
            self.st_artist.set(ar.get("userplaycount") if user else None)
            self.st_listeners.set(tr.get("listeners") or ar.get("listeners"),
                                  "listeners on Last.fm" if tr.get("listeners") else "listen to this artist")
            clear_layout(self.np_tags)
            for name in (tr.get("tags") or ar.get("tags") or [])[:5]:
                self.np_tags.addWidget(_tag(name))
            self.np_tags.addStretch(1)
            if ar.get("bio") or ar.get("similar"):
                self.about_title.setText(f"About {ar.get('name') or artist}")
                bio = ar.get("bio") or ""
                self.about_text.setText(bio[:320] + ("…" if len(bio) > 320 else ""))
                self.about_text.setVisible(bool(bio))
                self.about_similar.setText("Similar: " + ", ".join(ar["similar"]) if ar.get("similar") else "")
                url = ar.get("url") or ""
                try:
                    self.about_link.clicked.disconnect()
                except (RuntimeError, TypeError):
                    pass
                self.about_link.clicked.connect(lambda: open_url(url))
                self.about_card.setVisible(True)

        run(lambda: insights.song(client, user, artist, title), show, lambda _err: None)
        if user:
            def friends_done(found):
                if self._shown_pos != key:
                    return
                clear_layout(self.friends_lay)
                if not found:
                    self.friends_lay.addWidget(label("None of your Last.fm friends have played this yet.", "Muted", wrap=True))
                for f in found[:6]:
                    row = QHBoxLayout()
                    row.setSpacing(T.S3)
                    row.addWidget(Artwork(28, 14, f.get("avatar") or "", icon="user"))
                    row.addWidget(ElidedLabel(f["name"], None), 1)
                    row.addWidget(label(f"{f['plays']:,} play{'s' if f['plays'] != 1 else ''}", "Small"))
                    w = QWidget()
                    w.setLayout(row)
                    self.friends_lay.addWidget(w)
                self.friends_card.setVisible(True)
            run(lambda: insights.friends_who_played(client, user, artist, title), friends_done, lambda _err: None)

    # ---- live scrobbling ---------------------------------------------------------------------
    def _sync_live_toggle(self):
        if not hasattr(self, "live_toggle"):
            return
        user = app_state().lastfm_user() if app_state().creds.lastfm else None
        self.live_toggle.blockSignals(True)
        self.live_toggle.setEnabled(bool(user) and not self.live_session)
        self.live_toggle.setChecked(bool(user) and bool(db.setting("live_scrobble")))
        self.live_toggle.blockSignals(False)
        if not user:
            self.live_toggle.setToolTip("Connect Last.fm in Settings to scrobble while you listen")
        self._update_finish_label()

    def _live_toggled(self, on: bool):
        db.set_setting("live_scrobble", bool(on))
        self._update_finish_label()

    def _live(self) -> bool:
        return hasattr(self, "live_toggle") and self.live_toggle.isChecked()

    def _update_finish_label(self):
        if hasattr(self, "finish_btn"):
            self.finish_btn.setText("Finish" if self._live() else "Finish && review")

    def _live_step(self, final: bool = False):
        """Scrobble every song that has qualified and hasn't been sent yet."""
        if not self._live() or not self.set_id:
            return
        min_frac = float(db.setting("min_segment_fraction"))
        planned = [p for p in scheduler.plan_from_player(self.segs, self.tracker.all_intervals(), min_frac)
                   if p.segment.position not in self.live_done]
        if not final:
            cur = next((sg for sg in self.segs if sg.start_s <= self.position < sg.end_s), None)
            planned = [p for p in planned if not (cur and p.segment.position == cur.position and self.tracker.playing
                                                  and self.position - cur.start_s < min(cur.length * min_frac, 240))]
        if not planned:
            return
        try:
            if self.live_session is None:
                started = int(self.tracker.first_wall or min(p.timestamp for p in planned))
                self.live_session = repo.start_live_session(self.set_id, started, scheduler.listen_span(self.segs))
                self.live_toggle.setEnabled(False)
            ids = repo.add_live_scrobbles(self.live_session, planned)
        except TimestampError as err:
            self.live_toggle.setChecked(False)
            self.live_state.set_full(str(err))
            return
        self.live_done |= {p.segment.position for p in planned}
        if ids:
            if self.live_job is None:
                self.live_job = jobs.create_job(scrobble_ids=ids, label=f"Live: {self.s['title']}")
            else:
                jobs.append_to_job(self.live_job, ids)
            app_state().wake_runner()
            app_state().changed()

    def _now_playing(self, seg):
        if not self._live() or not seg or not seg.scrobblable or seg.position == self.now_playing_pos:
            return
        self.now_playing_pos = seg.position
        client = LastfmClient(app_state().creds)
        run(lambda: client.update_now_playing(seg.artist, seg.track, seg.album, seg.song_duration_s),
            lambda _: app_state().lastfm_activity.emit(),
            lambda err: self.live_state.set_full(f"Couldn't update “now playing”: {err}"))

    def _end_live(self, final: bool):
        if self.live_session is not None:
            self._live_step(final=True)
            complete = len(self.live_done) >= sum(1 for s in self.segs if s.scrobblable)
            repo.finish_live_session(self.live_session, complete)
            app_state().changed()
        self._reset_live()
        if hasattr(self, "live_toggle"):
            self._sync_live_toggle()

    # ---- player process ----------------------------------------------------------------------
    def start_player(self, seek: int = 0):
        self.stop_player()
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._proc_finished)
        self.proc.errorOccurred.connect(lambda e: self._state(f"Couldn't open the player ({e.name})."))
        self.proc.setWorkingDirectory(str(config.ROOT))
        self.proc.start(sys.executable, ["-m", "cuelog.player_proc", self.s["source_url"], str(int(seek)), self.s["title"][:80]])
        self.window_btn.setText("Show player")

    def stop_player(self):
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.finished.disconnect()
            self._send({"op": "close"})
            if not self.proc.waitForFinished(1500):
                self.proc.kill()
        self.proc = None

    def show_player(self):
        if self.proc and self.proc.state() == QProcess.Running:
            self._send({"op": "focus"})
        else:
            self.start_player(int(self.position))

    def _send(self, obj: dict):
        if self.proc and self.proc.state() == QProcess.Running:
            self.proc.write((json.dumps(obj) + "\n").encode())

    def _state(self, text: str):
        if hasattr(self, "player_state"):
            self.player_state.set_full(text)

    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            t = msg.get("type")
            if t == "event":
                self._event(msg.get("name", ""), float(msg.get("pos", 0)))
            elif t == "duration":
                self._duration(float(msg.get("ms", 0)))
            elif t == "error":
                self._state(msg.get("message", "Player error"))
            elif t == "started":
                self._state("Player open. Press play there; Cuelog follows along.")

    def _proc_finished(self, *_):
        if self.tracker.playing:
            self.tracker.pause()
        self._live_step()
        self._state("Player closed. Reopen it to continue, or finish below.")
        if hasattr(self, "window_btn"):
            self.window_btn.setText("Reopen player")

    # ---- events --------------------------------------------------------------------------------
    def _duration(self, ms: float):
        if ms > 0:
            d = int(ms / 1000)
            if not self.s["duration_s"]:
                repo.update_set(self.set_id, duration_s=d)
                self.s = repo.get_set(self.set_id)
                self.segs = repo.segments_for_set(self.set_id)
                app_state().changed()
            self.duration = d
            self._refresh_timeline()

    def _event(self, name: str, pos_ms: float):
        pos = pos_ms / 1000
        t = self.tracker
        if name == "READY":
            self._state("Ready. Press play in the player.")
            return
        self.position = pos
        if name == "PLAY":
            t.play(pos)
            self._state("Playing")
        elif name == "PROGRESS":
            if t.playing:
                t.progress(pos)
        elif name == "PAUSE":
            t.pause(pos)
            self._state("Paused")
            self._live_step()
        elif name == "SEEK":
            t.seek(pos)
        elif name == "FINISH":
            t.finish(self.duration or pos)
            self._state("Finished")
            self._live_step(final=True)

    def seek(self, seconds: int):
        if self.proc and self.proc.state() == QProcess.Running:
            self._send({"op": "seek", "ms": int(seconds * 1000)})
        elif self.set_id:
            self.start_player(int(seconds))
        self.position = float(seconds)
        self._update_ui()

    def _refresh_timeline(self):
        if not hasattr(self, "timeline"):
            return
        self.timeline.set_data([{"id": sg.set_track_id, "pos": sg.position, "start": sg.start_s, "end": sg.end_s,
                                 "title": sg.track or "(unmatched)", "estimated": False} for sg in self.segs],
                               self.duration or (self.segs[-1].end_s if self.segs else 1))

    def _update_ui(self):
        if not self.set_id or not hasattr(self, "timeline"):
            return
        self.timeline.set_heard(self.tracker.heard(), self.position)
        seg = self._current_segment()
        self._show_song(seg)
        if seg and self.tracker.playing and seg.start_s <= self.position < seg.end_s:
            self._now_playing(seg)
        if self.tracker.playing:
            self._live_step()
        min_frac = float(db.setting("min_segment_fraction"))
        n = len(scheduler.plan_from_player(self.segs, self.tracker.all_intervals(), min_frac))
        heard = int(self.tracker.heard_seconds())
        self.np_stats.setText(f"Heard {fmt_duration(heard) if heard >= 1 else '0:00'} · {n} song{'s' if n != 1 else ''}")
        if self._live():
            sent = len(self.live_done)
            self.live_state.set_full(f"{sent} scrobbled" if sent else "")
        elif not self.live_state.full.startswith("Overlaps"):
            self.live_state.set_full("You'll review the songs when you finish")

    def finish(self):
        if self.tracker.playing:
            self._send({"op": "pause"})
            self.tracker.pause()
        if self._live() or self.live_session is not None:
            had = self.live_session is not None or bool(self.live_done)
            self._live_step(final=True)
            n = len(self.live_done)
            self._end_live(final=True)
            if not had and not n:
                from .dialogs.base import notify
                notify(self, "Nothing to save yet", "No song has played long enough to count.")
                return
            self.tracker.reset()
            self._update_ui()
            app_state().toast(f"Session finished: {n} song{'s' if n != 1 else ''} scrobbled", "success")
            return
        intervals = self.tracker.all_intervals()
        min_frac = float(db.setting("min_segment_fraction"))
        planned = scheduler.plan_from_player(self.segs, intervals, min_frac)
        if not planned:
            from .dialogs.base import notify
            notify(self, "Nothing to save yet", "No song has played long enough to count: at least "
                   f"{int(min_frac * 100)}% of its part of the mix, or 4 minutes.")
            return
        started = int(min(w for _, _, w in intervals))
        ended = int(max(w + (b - a) for a, b, w in intervals))
        partial = len(planned) < sum(1 for s in self.segs if s.scrobblable)
        d = ReviewDialog(self.set_id, planned, started, ended, partial, self.window())
        if d.exec():
            self.tracker.reset()
            self._update_ui()

    def discard(self):
        """Stop the session: close the player, forget what was heard, cancel anything not yet sent."""
        if not self.set_id:
            return
        heard = self.tracker.heard_seconds()
        if self.live_session is not None:
            text = (f"Stop this session? {len(self.live_done)} song(s) already scrobbled stay on Last.fm; "
                    "anything not sent yet is cancelled.")
        else:
            text = "Discard this session? Nothing will be saved." if heard >= 1 else "Close the player for this set?"
        from .dialogs.base import confirm
        if not confirm(self, "Stop this session?" if self.live_session is not None else "Discard this session?", text,
                       "Stop" if self.live_session is not None else "Discard", danger=True):
            return
        self.stop_player()
        if self.live_session is not None:
            if self.live_job:
                jobs.cancel_job(self.live_job)
            try:
                repo.cancel_session(self.live_session)
            except ValueError:
                pass
            repo.finish_live_session(self.live_session, False)
            app_state().changed()
        self.tick.stop()
        self.tracker.reset()
        self._reset_live()
        self.set_id = None
        self._empty()

    def shutdown(self):
        self._end_live(final=True)
        self.stop_player()
