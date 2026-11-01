"""First-run setup: checks database, dependencies, connectivity and accounts."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from ... import APP_NAME, config, db, http
from ...lastfm import LastfmClient
from ...spotify import SpotifyClient
from .. import theme as T
from ..state import app_state
from ..widgets import button, divider, label, open_url
from .base import Dialog
from ..workers import friendly, run


class Check(QWidget):
    def __init__(self, title: str):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, T.S3, 0, T.S3)
        h.setSpacing(T.S3)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        h.addWidget(self.icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(title, "H3"))
        self.text = label("Checking…", "Small", wrap=True)
        col.addWidget(self.text)
        h.addLayout(col, 1)
        self.set("wait", "Checking…")

    def set(self, state: str, text: str):
        name, color = {"ok": ("circle-check", T.GREEN), "warn": ("circle-alert", T.AMBER), "fail": ("x", T.RED),
                       "wait": ("clock", T.MUTED), "optional": ("circle-alert", T.MUTED)}[state]
        self.icon.setPixmap(T.icon_pixmap(name, color, 18))
        self.text.setText(text)


class SetupWizard(Dialog):
    def __init__(self, migration_report=None, parent=None):
        super().__init__(parent, f"Welcome to {APP_NAME}", "Save a DJ set once, match its songs, and log every listen as "
                         "individual Last.fm scrobbles. Here's a quick check of your setup.", icon="wave", width=600)
        box = QFrame()
        box.setObjectName("Card")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(T.S4, T.S2, T.S4, T.S2)
        bl.setSpacing(0)
        self.c_db = Check("Library")
        self.c_deps = Check("Components")
        self.c_net = Check("Internet")
        self.c_lastfm = Check("Last.fm")
        self.c_spotify = Check("Spotify (optional)")
        self.c_sc = Check("SoundCloud account (optional)")
        self.c_legacy = Check("Your earlier data")
        for i, c in enumerate((self.c_db, self.c_deps, self.c_net, self.c_lastfm)):
            if i:
                bl.addWidget(divider())
            bl.addWidget(c)
        self.lf_box = QWidget()
        g = QGridLayout(self.lf_box)
        g.setContentsMargins(30, 0, 0, T.S3)
        g.setHorizontalSpacing(T.S2)
        g.setVerticalSpacing(T.S2)
        self.key = QLineEdit(app_state().creds.lastfm_key)
        self.key.setPlaceholderText("API key")
        self.secret = QLineEdit(app_state().creds.lastfm_secret)
        self.secret.setPlaceholderText("Shared secret")
        self.secret.setEchoMode(QLineEdit.Password)
        g.addWidget(self.key, 0, 0)
        g.addWidget(self.secret, 0, 1)
        g.addWidget(button("Save", None, None, self._save_lf), 0, 2)
        g.addWidget(button("Get a free API key", "external", "Ghost",
                           lambda: open_url("https://www.last.fm/api/account/create")), 1, 0, Qt.AlignLeft)
        bl.addWidget(self.lf_box)
        for c in (self.c_spotify, self.c_sc, self.c_legacy):
            bl.addWidget(divider())
            bl.addWidget(c)
        self.body.addWidget(box)
        self.connect_btn = button("Connect Last.fm", "user", None, self._connect)
        self.footer_left.addWidget(self.connect_btn)
        self.footer_left.addWidget(button("Settings", None, "Ghost", lambda: (self.accept(), app_state().go("settings"))))
        self.finish = self.add_actions("Start using Cuelog", self.accept, cancel=None)
        self.report = migration_report
        self.run_checks()

    def run_checks(self):
        creds = app_state().creds
        try:
            con = db.get()
            n = con.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
            self.c_db.set("ok", f"{db.current_path()} · schema v{db.SCHEMA_VERSION} · {n} set(s)")
        except Exception as err:
            self.c_db.set("fail", str(err))
        try:
            import PySide6
            from PySide6 import QtWebEngineWidgets  # noqa: F401
            import webview  # noqa: F401
            self.c_deps.set("ok", f"Python {sys.version.split()[0]} · Qt {PySide6.__version__} · player window (Edge WebView2)")
        except ImportError as err:
            self.c_deps.set("warn", f"Player window unavailable ({err}); run start.bat again. Everything else works.")
        self.c_net.set("wait", "Contacting SoundCloud…")
        run(lambda: http.request("GET", "https://w.soundcloud.com/player/api.js", timeout=10),
            lambda _: self.c_net.set("ok", "SoundCloud reachable"),
            lambda err: self.c_net.set("warn", f"{friendly(err)}. Metadata fetching and imports need internet."))
        user = app_state().lastfm_user()
        if user:
            self.c_lastfm.set("ok", f"Connected as {user}")
            self.lf_box.setVisible(False)
            self.connect_btn.setVisible(False)
            client = LastfmClient(creds)
            run(client.verify, lambda info: self.c_lastfm.set("ok", f"Connected and verified as {info.get('name', user)}"),
                lambda err: self.c_lastfm.set("warn", f"Connected as {user}, but verification failed: {friendly(err)}"))
        elif creds.lastfm:
            self.c_lastfm.set("warn", "API key saved. Connect your account to scrobble.")
            self.lf_box.setVisible(False)
            self.connect_btn.setVisible(True)
        else:
            self.c_lastfm.set("warn", "Add a Last.fm API key and shared secret (free, takes a minute).")
            self.lf_box.setVisible(True)
            self.connect_btn.setVisible(False)
        if creds.spotify:
            run(SpotifyClient(creds).verify, lambda _: self.c_spotify.set("ok", "Spotify credentials work"),
                lambda err: self.c_spotify.set("warn", friendly(err)))
        else:
            self.c_spotify.set("optional", "Not configured. Matching uses MusicBrainz; pasted Spotify links still work. "
                                           "Add credentials later in Settings.")
        self.c_sc.set("optional", "Not configured. Public links and the player window work without it."
                      if not creds.soundcloud else "Credentials saved. Connect from Settings to browse your playlists.")
        r = self.report
        if r and r.set_id:
            self.c_legacy.set("ok", f"Imported your saved set ({r.tracks} songs) with {r.sessions} reconstructed sessions "
                                    f"({r.scrobbles:,} planned scrobbles, nothing submitted)"
                              + (f" and your Last.fm login ({r.account})" if r.account else "") + ".")
        else:
            n = db.get().execute("SELECT COUNT(*) FROM sets").fetchone()[0]
            self.c_legacy.set("ok" if n else "optional", f"{n} set(s) in your library." if n else "No earlier data found.")

    def _save_lf(self):
        config.save_credentials({"LASTFM_API_KEY": self.key.text().strip(), "LASTFM_API_SECRET": self.secret.text().strip()})
        app_state().reload_credentials()
        self.run_checks()

    def _connect(self):
        from ..pages.settings import LastfmConnectDialog
        if LastfmConnectDialog(self).exec():
            self.run_checks()

    def accept(self):
        db.set_setting("setup_complete", True)
        super().accept()
