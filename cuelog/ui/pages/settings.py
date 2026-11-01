"""Settings, split into sections: Last.fm, Spotify, SoundCloud, Scrobbling, Data, About."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTime, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
                               QTimeEdit, QVBoxLayout, QWidget)

from ... import APP_NAME, __version__, config, db, exporter, legacy
from ...lastfm import LastfmClient
from ...soundcloud import SoundCloudClient
from ...spotify import SpotifyClient
from .. import theme as T
from ..state import app_state
from ..widgets import Badge, Segmented, StatusLabel, Toggle, button, clear_layout, divider, label, open_url, page_header
from ..dialogs.base import Dialog, confirm, notify
from ..workers import friendly, run

FIELD_W = 300


class LastfmConnectDialog(Dialog):
    """Desktop auth flow: open Last.fm, wait for approval, store the session key."""

    def __init__(self, parent=None):
        super().__init__(parent, "Connect Last.fm", None, icon="user", width=460)
        self.cancelled = False
        self.text = label("Opening Last.fm in your browser… Click “Yes, allow access” there and come back; this "
                          "continues on its own.", "Secondary", wrap=True)
        self.body.addWidget(self.text)
        self.reopen = button("Open the page again", "external", "Ghost")
        self.reopen.setEnabled(False)
        self.footer_left.addWidget(self.reopen)
        self.add_actions(None, cancel="Cancel")
        self.client = LastfmClient(app_state().creds)
        run(self.client.get_token, self._got_token, self._fail)

    def _got_token(self, token: str):
        url = self.client.auth_url(token)
        QDesktopServices.openUrl(QUrl(url))
        self.reopen.setEnabled(True)
        self.reopen.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        run(lambda: self.client.wait_for_session(token, cancelled=lambda: self.cancelled), self._done, self._fail)

    def _done(self, result):
        name, key = result
        LastfmClient.store_session(name, key)
        app_state().reload_credentials()
        app_state().toast(f"Connected to Last.fm as {name}", "success")
        self.accept()

    def _fail(self, err):
        if self.cancelled:
            return
        self.text.setText(friendly(err))
        self.text.setStyleSheet(f"color:{T.RED};")

    def reject(self):
        self.cancelled = True
        super().reject()


# ---- building blocks ---------------------------------------------------------------------------
def group(title: str | None = None, description: str | None = None) -> tuple[QWidget, QVBoxLayout]:
    """A titled block of setting rows in one bordered container. Returns (host, rows layout)."""
    host = QWidget()
    v = QVBoxLayout(host)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(T.S2)
    if title:
        v.addWidget(label(title, "H2"))
    if description:
        v.addWidget(label(description, "Muted", wrap=True))
    box = QFrame()
    box.setObjectName("Card")
    rows = QVBoxLayout(box)
    rows.setContentsMargins(0, 0, 0, 0)
    rows.setSpacing(0)
    v.addWidget(box)
    return host, rows


def add_row(rows: QVBoxLayout, title: str, description: str | None, control: QWidget | None) -> QWidget:
    """Title and description on the left, the control on the right."""
    if rows.count():
        rows.addWidget(divider())
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(T.S5, 14, T.S4, 14)
    h.setSpacing(T.S5)
    col = QVBoxLayout()
    col.setSpacing(2)
    t = label(title)
    t.setStyleSheet(f"color:{T.TEXT}; font-weight:500;")
    col.addWidget(t)
    if description:
        col.addWidget(label(description, "Small", wrap=True))
    h.addLayout(col, 1)
    if control is not None:
        h.addWidget(control, 0, Qt.AlignVCenter)
    rows.addWidget(w)
    return w


def footer(*widgets) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(0, T.S1, 0, 0)
    h.setSpacing(T.S2)
    for w in widgets:
        if w is None:
            h.addStretch(1)
        else:
            h.addWidget(w)
    return h


def field(value: str, placeholder: str, secret: bool = False) -> QLineEdit:
    e = QLineEdit(value)
    e.setPlaceholderText(placeholder)
    e.setFixedWidth(FIELD_W)
    if secret:
        e.setEchoMode(QLineEdit.Password)
    return e


def scroll(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


class Panel(QWidget):
    """One settings section: a column with a comfortable maximum width."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, T.S4, T.S6)
        col = QWidget()
        col.setMaximumWidth(760)
        self.lay = QVBoxLayout(col)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(T.S6)
        outer.addWidget(col, 4)       # grows up to 760 px, the rest stays empty on the right
        outer.addStretch(1)

    def refresh(self):
        pass


# ---- sections ----------------------------------------------------------------------------------
class LastfmPanel(Panel):
    def __init__(self):
        super().__init__()
        self.account_host = QVBoxLayout()
        self.lay.addLayout(self.account_host)
        g, rows = group("API key", "Cuelog uses your own free Last.fm API key. Any application name works; no callback "
                                   "address is needed.")
        self.key = field("", "API key")
        self.secret = field("", "Shared secret", secret=True)
        add_row(rows, "API key", None, self.key)
        add_row(rows, "Shared secret", None, self.secret)
        self.saved = StatusLabel("Small")
        g.layout().addLayout(footer(button("Get an API key", "external", "Ghost",
                                           lambda: open_url("https://www.last.fm/api/account/create")),
                                    None, self.saved, button("Save", None, None, self.save)))
        self.lay.addWidget(g)
        self.lay.addStretch(1)

    def refresh(self):
        creds = app_state().creds
        self.key.setText(creds.lastfm_key)
        self.secret.setText(creds.lastfm_secret)
        clear_layout(self.account_host)
        g, rows = group("Account")
        user = app_state().lastfm_user()
        if user:
            add_row(rows, f"Connected as {user}", "Your scrobbles go to this account.",
                    button("Open profile", "external", "Ghost", lambda: open_url(f"https://www.last.fm/user/{user}")))
            add_row(rows, "Check the connection", "Makes a test request to Last.fm.", button("Check", None, None, self.verify))
            sw = QWidget()
            sw.setLayout(footer(button("Reconnect", None, None, self.connect_),
                                button("Disconnect", None, "Danger", self.disconnect)))
            add_row(rows, "Switch account", "Disconnecting only removes the login from this PC.", sw)
        else:
            b = button("Connect", None, "Primary", self.connect_)
            b.setEnabled(creds.lastfm)
            add_row(rows, "Not connected", "Add your API key below first." if not creds.lastfm else
                    "Opens Last.fm in your browser so you can approve Cuelog.", b)
        self.account_host.addWidget(g)

    def save(self):
        try:
            config.save_credentials({"LASTFM_API_KEY": self.key.text().strip(),
                                     "LASTFM_API_SECRET": self.secret.text().strip()})
        except ValueError as err:
            self.saved.setText(str(err))
            return
        app_state().reload_credentials()
        self.saved.setText("Saved")
        QTimer.singleShot(2500, lambda: self.saved.setText(""))
        self.refresh()

    def connect_(self):
        if LastfmConnectDialog(self.window()).exec():
            self.refresh()

    def verify(self):
        client = LastfmClient(app_state().creds)
        run(client.verify, lambda info: app_state().toast(
            f"Working: {info.get('name')} has {int(info.get('playcount', 0)):,} scrobbles.", "success"),
            lambda err: app_state().toast(friendly(err), "error"))

    def disconnect(self):
        from ..dialogs.base import confirm
        if confirm(self, "Disconnect Last.fm?", "This removes the Last.fm login from this PC. Your library and history "
                   "stay.", "Disconnect", danger=True):
            LastfmClient.disconnect()
            app_state().reload_credentials()
            self.refresh()


class SpotifyPanel(Panel):
    def __init__(self):
        super().__init__()
        g, rows = group("Spotify", "Optional. With your own Spotify app, songs are matched against Spotify's catalogue "
                                   "with official artwork. No Spotify login needed. Without it, matching uses MusicBrainz.")
        self.status = Badge("draft")
        add_row(rows, "Status", None, self.status)
        self.cid = field("", "Client ID")
        self.secret = field("", "Client secret", secret=True)
        add_row(rows, "Client ID", None, self.cid)
        add_row(rows, "Client secret", None, self.secret)
        self.note = StatusLabel("Small")
        self.test_btn = button("Test", None, None, self.test)
        g.layout().addLayout(footer(button("Spotify developer dashboard", "external", "Ghost",
                                           lambda: open_url("https://developer.spotify.com/dashboard")),
                                    None, self.note, self.test_btn, button("Save", None, None, self.save)))
        self.lay.addWidget(g)
        self.lay.addStretch(1)

    def refresh(self):
        c = app_state().creds
        self.cid.setText(c.spotify_id)
        self.secret.setText(c.spotify_secret)
        self.status.set_status("completed" if c.spotify else "draft", "Set up" if c.spotify else "Not set up")
        self.test_btn.setEnabled(c.spotify)

    def save(self):
        config.save_credentials({"SPOTIFY_CLIENT_ID": self.cid.text().strip(), "SPOTIFY_CLIENT_SECRET": self.secret.text().strip()})
        app_state().reload_credentials()
        self.note.setText("Saved")
        self.refresh()

    def test(self):
        self.note.setText("Testing…")
        run(SpotifyClient(app_state().creds).verify, lambda _: self.note.setText("Spotify works"),
            lambda err: self.note.setText(friendly(err)))


class SoundCloudPanel(Panel):
    def __init__(self):
        super().__init__()
        g, rows = group("SoundCloud", "Optional. Public links and the player work without this. An approved SoundCloud "
                                      "app adds richer metadata and lets you browse your playlists and likes.")
        self.status = Badge("draft")
        add_row(rows, "Status", None, self.status)
        self.cid = field("", "Client ID")
        self.secret = field("", "Client secret", secret=True)
        self.redirect = field("", "Redirect URI")
        add_row(rows, "Client ID", None, self.cid)
        add_row(rows, "Client secret", None, self.secret)
        add_row(rows, "Redirect URI", "Register exactly this address in your SoundCloud app.", self.redirect)
        self.note = StatusLabel("Small")
        self.connect_btn = button("Connect", None, None, self.connect_)
        g.layout().addLayout(footer(button("SoundCloud apps", "external", "Ghost",
                                           lambda: open_url("https://soundcloud.com/you/apps")),
                                    None, self.note, self.connect_btn, button("Save", None, None, self.save)))
        self.lay.addWidget(g)
        self.lay.addStretch(1)

    @staticmethod
    def _account():
        return db.get().execute("SELECT username FROM accounts WHERE provider = 'soundcloud'").fetchone()

    def refresh(self):
        c = app_state().creds
        self.cid.setText(c.soundcloud_id)
        self.secret.setText(c.soundcloud_secret)
        self.redirect.setText(c.soundcloud_redirect)
        acct = self._account()
        self.status.set_status("completed" if acct else "draft",
                               f"Connected as {acct['username']}" if acct else ("Set up" if c.soundcloud else "Not set up"))
        self.connect_btn.setText("Disconnect" if acct else "Connect")
        self.connect_btn.setEnabled(c.soundcloud)

    def save(self):
        try:
            config.save_credentials({"SOUNDCLOUD_CLIENT_ID": self.cid.text().strip(),
                                     "SOUNDCLOUD_CLIENT_SECRET": self.secret.text().strip(),
                                     "SOUNDCLOUD_REDIRECT_URI": self.redirect.text().strip()})
        except ValueError as err:
            self.note.setText(str(err))
            return
        app_state().reload_credentials()
        self.note.setText("Saved")
        self.refresh()

    def connect_(self):
        if self._account():
            SoundCloudClient.disconnect()
            app_state().reload_credentials()
            self.refresh()
            return
        self.note.setText("Finish the login in your browser…")
        run(SoundCloudClient(app_state().creds).connect,
            lambda name: (self.note.setText(f"Connected as {name}"), app_state().reload_credentials(), self.refresh()),
            lambda err: self.note.setText(friendly(err)))


class ScrobblingPanel(Panel):
    """Every control saves the moment it changes."""

    def __init__(self):
        super().__init__()
        self._loading = False
        self.saved = StatusLabel("Small")
        self.lay.addWidget(self.saved)

        def spin(lo, hi, suffix="", step=1, decimals=None):
            w = QDoubleSpinBox() if decimals is not None else QSpinBox()
            if decimals is not None:
                w.setDecimals(decimals)
            w.setRange(lo, hi)
            w.setSingleStep(step)
            w.setSuffix(suffix)
            w.setFixedWidth(120)
            w.setAlignment(Qt.AlignRight)
            return w

        g, rows = group("Timing")
        self.max_age = spin(1, 14, " days")
        add_row(rows, "Oldest listen to send", "Last.fm ignores scrobbles older than 14 days.", self.max_age)
        self.buffer = spin(0, 120, " min")
        add_row(rows, "Gap before now", "Generated times always end at least this long ago.", self.buffer)
        hours = QHBoxLayout()
        hours.setContentsMargins(0, 0, 0, 0)
        hours.setSpacing(T.S2)
        self.day_start, self.day_end = QTimeEdit(), QTimeEdit()
        for w in (self.day_start, self.day_end):
            w.setDisplayFormat("HH:mm")
            w.setFixedWidth(76)
            w.setAlignment(Qt.AlignCenter)
        hours.addWidget(self.day_start)
        hours.addWidget(label("–", "Muted"))
        hours.addWidget(self.day_end)
        hw = QWidget()
        hw.setLayout(hours)
        add_row(rows, "Backfill listening hours", "When backfilled listens are placed during the day.", hw)
        self.lay.addWidget(g)

        g, rows = group("Sending")
        self.delay = spin(0.5, 60, " s", 0.5, 1)
        add_row(rows, "Pause between batches", "Each batch holds up to 50 songs.", self.delay)
        self.cap = spin(50, 3000, "", 50)
        add_row(rows, "Daily limit", "Last.fm accepts roughly 2,800–3,000 scrobbles per account per day, from all apps "
                "together (Spotify too). Cuelog pauses when it has sent this many in the last 24 hours and carries on "
                "by itself later. Nothing is lost.", self.cap)
        self.retries = spin(1, 10)
        add_row(rows, "Retries", "For temporary Last.fm or network errors.", self.retries)
        self.send_dur = Toggle()
        add_row(rows, "Send song length", "Helps Last.fm identify the exact recording.", self.send_dur)
        self.lay.addWidget(g)

        g, rows = group("Duplicates")
        self.check_hist = Toggle()
        add_row(rows, "Check my Last.fm history first", "Skips songs already scrobbled at about the same time.", self.check_hist)
        self.tol = spin(10, 600, " s", 10)
        add_row(rows, "Same-time window", "How close two plays must be to count as the same one.", self.tol)
        self.lay.addWidget(g)

        g, rows = group("Player")
        self.detect = Toggle()
        add_row(rows, "Detect SoundCloud on this PC", "When a set from your library plays in the SoundCloud app or a "
                "browser, Cuelog follows along and scrobbles it. New long uploads get a pop-up to add them.", self.detect)
        self.live = Toggle()
        add_row(rows, "Scrobble while I listen", "Sends each song as soon as you've heard enough of it.", self.live)
        self.min_frac = spin(10, 100, " %", 5)
        add_row(rows, "Heard enough", "Share of a song's part in the mix you need to hear (never more than 4 minutes).",
                self.min_frac)
        self.lay.addWidget(g)
        self.lay.addStretch(1)

        for w in (self.max_age, self.buffer, self.delay, self.cap, self.retries, self.tol, self.min_frac):
            w.valueChanged.connect(self.save)
        for w in (self.send_dur, self.check_hist, self.live, self.detect):
            w.toggled.connect(self.save)
        for w in (self.day_start, self.day_end):
            w.timeChanged.connect(self.save)

    def refresh(self):
        self._loading = True
        s = db.all_settings()
        self.max_age.setValue(int(s["max_age_days"]))
        self.buffer.setValue(int(s["recent_buffer_minutes"]))
        self.delay.setValue(float(s["batch_delay_seconds"]))
        self.cap.setValue(int(s["daily_cap"]))
        self.retries.setValue(int(s["max_retries"]))
        self.check_hist.setChecked(bool(s["check_lastfm_history"]))
        self.tol.setValue(int(s["duplicate_tolerance_seconds"]))
        self.send_dur.setChecked(bool(s["send_duration"]))
        self.live.setChecked(bool(s["live_scrobble"]))
        self.detect.setChecked(bool(s["detect_soundcloud"]))
        self.min_frac.setValue(int(float(s["min_segment_fraction"]) * 100))
        self.day_start.setTime(QTime.fromString(s["backfill_day_start"], "HH:mm"))
        self.day_end.setTime(QTime.fromString(s["backfill_day_end"], "HH:mm"))
        self._loading = False

    def save(self, *_):
        if self._loading:
            return
        if self.day_end.time() <= self.day_start.time():
            self.saved.setText("Backfill hours: the end has to be after the start.")
            return
        vals = {"max_age_days": self.max_age.value(), "recent_buffer_minutes": self.buffer.value(),
                "batch_delay_seconds": self.delay.value(), "daily_cap": self.cap.value(), "max_retries": self.retries.value(),
                "check_lastfm_history": self.check_hist.isChecked(), "duplicate_tolerance_seconds": self.tol.value(),
                "send_duration": self.send_dur.isChecked(), "min_segment_fraction": self.min_frac.value() / 100,
                "live_scrobble": self.live.isChecked(), "detect_soundcloud": self.detect.isChecked(),
                "backfill_day_start": self.day_start.time().toString("HH:mm"),
                "backfill_day_end": self.day_end.time().toString("HH:mm")}
        with db.tx():
            for k, v in vals.items():
                db.set_setting(k, v)
        self.saved.setText("Saved. Changes here apply right away.")
        app_state().changed()


class DataPanel(Panel):
    def __init__(self):
        super().__init__()
        g, rows = group("Export", "Your library lives in one local database. Exports never include logins.")
        add_row(rows, "Scrobbles", "Every logged and sent song, as a spreadsheet.", button("Export CSV", None, None, self.export_csv))
        add_row(rows, "Everything", "Sets, songs, listens and settings.", button("Export JSON", None, None, self.export_json))
        self.lay.addWidget(g)
        g, rows = group("Backup")
        add_row(rows, "Back up", "A complete copy of your library.", button("Back up…", None, None, self.backup))
        add_row(rows, "Restore", "Replaces your current library. A safety copy is kept.", button("Restore…", None, None, self.restore))
        self.lay.addWidget(g)
        g, rows = group("Other")
        add_row(rows, "Import an old CSV project", "A folder from the original command-line importer.",
                button("Choose folder…", None, None, self.import_legacy))
        add_row(rows, "Data folder", str(config.data_dir()),
                button("Open", None, None, lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.data_dir())))))
        self.lay.addWidget(g)
        self.lay.addStretch(1)

    def export_csv(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export scrobbles", "cuelog-scrobbles.csv", "CSV (*.csv)")
        if p:
            n = exporter.export_scrobbles_csv(Path(p))
            app_state().toast(f"Exported {n:,} scrobbles", "success")

    def export_json(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export everything", "cuelog-export.json", "JSON (*.json)")
        if p:
            exporter.export_json(Path(p))
            app_state().toast("Export saved", "success")

    def backup(self):
        p, _ = QFileDialog.getSaveFileName(self, "Back up", f"cuelog-backup-{time.strftime('%Y%m%d')}.db", "Cuelog backup (*.db)")
        if p:
            exporter.backup(Path(p))
            app_state().toast("Backup saved", "success")

    def restore(self):
        p, _ = QFileDialog.getOpenFileName(self, "Restore backup", "", "Cuelog backup (*.db)")
        if not p:
            return
        try:
            info = exporter.validate_backup(Path(p))
        except ValueError as err:
            notify(self, "Can't restore that file", str(err), kind="error")
            return
        if not confirm(self, "Restore this backup?", f"Your library is replaced with the backup ({info.get('sets', 0)} "
                       f"sets, {info.get('sessions', 0)} listens). A safety copy of the current one is kept.",
                       "Restore", danger=True):
            return
        st = app_state()
        if st.runner:
            st.runner.stop()
            st.runner.wait(8000)
        safety = exporter.restore(Path(p))
        notify(self, "Backup restored", f"Your previous library was saved as {safety.name}. Cuelog closes now; "
               "start it again to continue.", kind="success")
        self.window().close()

    def import_legacy(self):
        folder = QFileDialog.getExistingDirectory(self, "Folder with tracks.csv and config.json")
        if not folder:
            return
        try:
            report = legacy.migrate(Path(folder), force=True)
        except (legacy.LegacySchemaError, OSError, ValueError) as err:
            notify(self, "Couldn't import that folder", str(err), kind="error")
            return
        app_state().toast(f"Imported {report.tracks} songs and {report.sessions} listens." if report.set_id else
                          "Nothing imported: " + "; ".join(report.notes))
        app_state().changed()


class AboutPanel(Panel):
    def __init__(self):
        super().__init__()
        g, rows = group(None)
        add_row(rows, f"{APP_NAME} {__version__}", "Turns SoundCloud DJ sets into individual Last.fm scrobbles.", None)
        add_row(rows, "Where the data comes from", "Songs from Spotify and MusicBrainz, artwork from Spotify and the Cover "
                                                   "Art Archive, previews from Deezer, stats from Last.fm, icons from Lucide.",
                None)
        add_row(rows, "Keys and logins", f"API keys are stored in {config.ENV_FILE.name}. Logins are "
                                         "encrypted with your Windows account.", None)
        folder = button("Open folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.APP_HOME))))
        add_row(rows, "Your data", f"Database, settings and caches live in {config.APP_HOME}.", folder)
        self.lay.addWidget(g)
        self.lay.addStretch(1)


class AppearancePanel(Panel):
    def __init__(self):
        super().__init__()
        g, rows = group("Colours")
        sw = QWidget()
        sl = QHBoxLayout(sw)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(T.S2)
        self.swatches = {}
        for key, (name, color, _h, _s) in T.ACCENTS.items():
            b = QPushButton()
            b.setFixedSize(T.z(28), T.z(28))
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(name)
            b.setAccessibleName(f"Accent colour {name}")
            b.clicked.connect(lambda _=False, k=key: self._set("accent", k))
            self.swatches[key] = (b, color)
            sl.addWidget(b)
        add_row(rows, "Accent colour", "Buttons, charts and highlights.", sw)
        self.base = Segmented([(k, v[0]) for k, v in T.BASES.items()], lambda k: self._set("base", k))
        add_row(rows, "Background", "Midnight is near-black; Graphite is a softer dark grey.", self.base)
        self.zoom = Segmented([("0.9", "90%"), ("1.0", "100%"), ("1.1", "110%"), ("1.2", "120%"), ("1.35", "135%")],
                              lambda k: self._zoom(float(k)))
        add_row(rows, "Size", "Also Ctrl + and Ctrl −; Ctrl 0 resets.", self.zoom)
        self.sound = Toggle()
        self.sound.toggled.connect(self._sound)
        add_row(rows, "Notification sound", "A soft chime when Cuelog shows a notification.", self.sound)
        self.lay.addWidget(g)
        self.lay.addStretch(1)

    def refresh(self):
        cur = db.setting("accent") or "ember"
        for key, (b, color) in self.swatches.items():
            ring = T.TEXT if key == cur else "transparent"
            b.setStyleSheet(f"QPushButton {{ background: {color}; border: 2px solid {ring}; border-radius: 14px; "
                            f"min-height: 0; max-height: 1000px; padding: 0; }}")
        self.base.set(db.setting("base") or "midnight")
        self.sound.blockSignals(True)
        self.sound.setChecked(bool(db.setting("toast_sound")))
        self.sound.blockSignals(False)
        z = min(("0.9", "1.0", "1.1", "1.2", "1.35"), key=lambda v: abs(float(v) - T.ZOOM))
        self.zoom.set(z)

    def _set(self, key: str, value: str):
        db.set_setting(key, value)
        T.set_scheme(db.setting("accent") or "ember", db.setting("base") or "midnight")
        self.window().restyle()

    def _sound(self, on: bool):
        db.set_setting("toast_sound", bool(on))
        if on:
            self.window().toast("Notification sound on")

    def _zoom(self, value: float):
        db.set_setting("zoom", value)
        T.set_zoom(value)
        self.window().restyle()


SECTIONS = [("appearance", "Appearance", AppearancePanel), ("lastfm", "Last.fm", LastfmPanel), ("spotify", "Spotify", SpotifyPanel),
            ("soundcloud", "SoundCloud", SoundCloudPanel), ("scrobbling", "Scrobbling", ScrobblingPanel),
            ("data", "Data", DataPanel), ("about", "About", AboutPanel)]


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.S6, 28, T.S6, 0)
        outer.setSpacing(T.S5)
        head, _ = page_header("Settings")
        outer.addLayout(head)
        body = QHBoxLayout()
        body.setSpacing(T.S6)
        self.nav = QListWidget()
        self.nav.setObjectName("SubNav")
        self.nav.setFixedWidth(176)
        self.nav.setFrameShape(QFrame.NoFrame)
        self.nav.setStyleSheet(f"""
            QListWidget#SubNav {{ background: transparent; border: none; }}
            QListWidget#SubNav::item {{ padding: 0 10px; border-radius: 4px; color: {T.TEXT_2}; margin-bottom: 2px; border: none; }}
            QListWidget#SubNav::item:hover {{ background: {T.SURFACE}; color: {T.TEXT}; }}
            QListWidget#SubNav::item:selected {{ background: {T.SURFACE_2}; color: {T.TEXT}; }}""")
        self.stack = QStackedWidget()
        self.panels: dict[str, Panel] = {}
        for key, title, cls in SECTIONS:
            it = QListWidgetItem(title)
            it.setData(Qt.UserRole, key)
            it.setSizeHint(QSize(160, 34))
            self.nav.addItem(it)
            p = cls()
            self.panels[key] = p
            self.stack.addWidget(scroll(p))
        self.nav.setFixedHeight(len(SECTIONS) * 36 + 4)
        self.nav.currentRowChanged.connect(self._show)
        body.addWidget(self.nav, 0, Qt.AlignTop)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.nav.setCurrentRow(0)

    def _show(self, row: int):
        self.stack.setCurrentIndex(row)
        self.panels[SECTIONS[row][0]].refresh()

    def open(self, arg=None):
        if isinstance(arg, str):
            keys = [k for k, _, _ in SECTIONS]
            if arg in keys:
                self.nav.setCurrentRow(keys.index(arg))
        self.refresh()

    def refresh(self):
        self.panels[SECTIONS[self.nav.currentRow()][0]].refresh()
