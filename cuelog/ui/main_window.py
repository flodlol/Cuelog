"""Main window: sidebar navigation, page stack, toasts and the background import runner."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME
from . import theme as T
from .state import app_state
from .components import ElidedLabel
from .widgets import Artwork, Banner, Toast, icon_label, label, open_url
from .workers import ImportRunner

NAV = [
    ("dashboard", "Home", "home"),
    ("library", "Library", "library"),
    ("discover", "Add set", "plus-circle"),
    ("player", "Player", "headphones"),
    ("reports", "Reports", "chart"),
    ("activity", "Activity", "activity"),
]
# Old route names still work everywhere: they open the matching Activity tab.
ALIASES = {"queue": ("activity", "queue"), "history": ("activity", "history")}


class NavButton(QPushButton):
    """Sidebar item: icon, label and an optional count on the right."""

    def __init__(self, text: str, icon_name: str):
        super().__init__(text)
        self.setObjectName("Nav")
        self.setCheckable(True)
        self.setIcon(T.icon(icon_name, T.MUTED, 18, checked_color=T.ACCENT))
        self.setIconSize(QSize(18, 18))
        self.setCursor(Qt.PointingHandCursor)
        self.setText(f"   {text}")
        self.badge = QLabel(self)
        self.badge.setObjectName("NavBadge")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.hide()

    def set_count(self, n: int):
        self.badge.setVisible(n > 0)
        self.badge.setText(f"{n:,}" if n < 1000 else "999+")
        self.badge.adjustSize()
        self.badge.setFixedHeight(18)
        self._place()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def _place(self):
        self.badge.move(self.width() - self.badge.width() - 10, (self.height() - self.badge.height()) // 2)


class AccountCard(QFrame):
    """Last.fm account: avatar, total scrobbles and what's playing. Click opens the Last.fm profile."""

    def __init__(self, on_nav):
        super().__init__()
        self.on_nav = on_nav
        self.username: str | None = None
        self.url = ""
        self.setObjectName("AccountCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""QFrame#AccountCard {{ background: transparent; border-radius: 6px; }}
                               QFrame#AccountCard:hover {{ background: {T.SURFACE}; }}""")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.avatar = Artwork(32, 16, icon="user")
        top.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = ElidedLabel("", "H3")
        self.count = label("", "Small")
        col.addWidget(self.name)
        col.addWidget(self.count)
        top.addLayout(col, 1)
        self.ext = icon_label("external", T.MUTED, 14)
        top.addWidget(self.ext, 0, Qt.AlignVCenter)
        v.addLayout(top)
        self.track_row = QWidget()
        tr = QVBoxLayout(self.track_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(T.z(8))
        self.track_art = Artwork(180, 4)
        tr.addWidget(self.track_art)
        tcol = QVBoxLayout()
        tcol.setSpacing(1)
        self.track_name = ElidedLabel("", "H3")
        self.track_state = ElidedLabel("", "Small")
        tcol.addWidget(self.track_name)
        tcol.addWidget(self.track_state)
        tr.addLayout(tcol)
        v.addWidget(self.track_row)
        self.track_row.setVisible(False)
        self.timer = QTimer(self, interval=45000, timeout=self.refresh)
        self.timer.start()
        self._busy = False
        self._art_key = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        m = self.layout().contentsMargins()
        self.track_art.set_size(max(40, self.width() - m.left() - m.right()))

    def set_user(self, username: str | None):
        self.username = username
        if not username:
            self.avatar.set_url("")
            self.name.set_full("Connect Last.fm")
            self.count.setText("Needed to scrobble")
            self.ext.setVisible(False)
            self.track_row.setVisible(False)
            self.setToolTip("Connect your Last.fm account")
            return
        self.url = f"https://www.last.fm/user/{username}"
        self.name.set_full(username)
        self.ext.setVisible(True)
        self.setToolTip("Open your Last.fm profile")
        self.refresh()

    def refresh(self):
        if not self.username or self._busy:
            return
        from ..lastfm import LastfmClient
        from .workers import run
        client = LastfmClient(app_state().creds)
        if not client.configured:
            return
        self._busy = True
        run(lambda: client.profile_summary(self.username), self._show, lambda _err: setattr(self, "_busy", False))

    def _show(self, p: dict):
        import time as _t
        self._busy = False
        self.url = p.get("url") or self.url
        self.avatar.set_url(p.get("avatar") or "")
        self.count.setText(f"{p['playcount']:,} scrobbles")
        t = p.get("track")
        self.track_row.setVisible(bool(t))
        if t:
            if t["now"]:
                self.track_state.set_full(f"Scrobbling now · {t['artist']}")
                self.track_state.setStyleSheet(f"color:{T.ACCENT}; font-size:12px;")
            else:
                ago = int(_t.time()) - (t["uts"] or int(_t.time()))
                when = "just now" if ago < 90 else f"{ago // 60} min ago" if ago < 3600 else \
                    f"{ago // 3600} h ago" if ago < 86400 else f"{ago // 86400} d ago"
                self.track_state.set_full(f"{t['artist']} · {when}")
                self.track_state.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
            self.track_name.set_full(t["name"])
            key = (t["artist"], t["name"])
            if t.get("art"):
                self.track_art.set_url(t["art"])
            elif self._art_key != key:
                # Last.fm only has its grey star for this one: find the cover elsewhere
                from .. import images
                from .workers import run
                self.track_art.set_url("")
                run(lambda: images.track_image(*key),
                    lambda url: self.track_art.set_url(url) if self._art_key == key else None, lambda _e: None)
            self._art_key = key

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.username:
            open_url(self.url if self.url.startswith("https://www.last.fm/") else f"https://www.last.fm/user/{self.username}")
        else:
            self.on_nav("settings")


class Sidebar(QWidget):
    def __init__(self, on_nav):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(232)
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 20, 12, 12)
        lay.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(10, 0, 0, 0)
        brand.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(T.icon_pixmap("wave", T.ACCENT, 20, stroke=2))
        brand.addWidget(mark)
        brand.addWidget(label(APP_NAME, "Wordmark"))
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(24)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, NavButton] = {}
        for key, text, ic in NAV:
            b = NavButton(text, ic)
            b.clicked.connect(lambda _=False, k=key: on_nav(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.live_line = label("", "Small", wrap=True)
        self.live_line.setContentsMargins(10, 0, 10, 8)
        self.live_line.setStyleSheet(f"color:{T.ACCENT}; font-size:12px;")
        self.live_line.hide()
        lay.addWidget(self.live_line)
        self.job_line = label("", "Small", wrap=True)
        self.job_line.setContentsMargins(10, 0, 10, 8)
        self.job_line.hide()
        lay.addWidget(self.job_line)
        self.acct = AccountCard(on_nav)
        lay.addWidget(self.acct)
        lay.addSpacing(2)
        settings = NavButton("Settings", "settings")
        settings.clicked.connect(lambda: on_nav("settings"))
        self.group.addButton(settings)
        self.buttons["settings"] = settings
        lay.addWidget(settings)

    def set_live(self, text: str):
        self.live_line.setText(text)
        self.live_line.setVisible(bool(text))

    def set_job(self, text: str):
        self.job_line.setText(text)
        self.job_line.setVisible(bool(text))

    def set_active(self, key: str):
        key = {"set": "library", "matches": "library"}.get(key, key)
        if key in self.buttons:
            self.buttons[key].setChecked(True)
            return
        checked = self.group.checkedButton()
        if checked:
            self.group.setExclusive(False)
            checked.setChecked(False)
            self.group.setExclusive(True)

    def set_account(self, username: str | None):
        self.acct.set_user(username)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 880)
        self.setMinimumSize(980, 640)
        self.state = app_state()
        self.pages: dict = {}
        self.history: list[tuple[str, object]] = []
        self.current: tuple[str, object] | None = None
        from .. import db
        T.set_scheme(db.setting("accent") or "ember", db.setting("base") or "midnight")
        T.set_zoom(float(db.setting("zoom") or 1.0))
        T.install_zoom_hook()
        QApplication.instance().setStyleSheet(T.stylesheet())
        self._build()

        self.state.navigate_requested.connect(self.navigate)
        self.state.toast_requested.connect(self.toast)
        self.state.accounts_changed.connect(self._refresh_account)
        self.state.data_changed.connect(self._on_data_changed)
        self.state.job_progress.connect(self._on_job)
        self.state.lastfm_activity.connect(lambda: QTimer.singleShot(2500, self.sidebar.acct.refresh))
        self._refresh_account()

        for seq in ("Ctrl++", "Ctrl+=", "Ctrl+Shift+="):
            QShortcut(QKeySequence(seq), self, activated=lambda: self.zoom(+1))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self.zoom(-1))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.zoom(0))
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.back)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self.navigate("discover"))
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.navigate("library", "focus-search"))

        self.runner = ImportRunner(self)
        self.runner.progress.connect(self.state.job_progress.emit)
        self.runner.idle.connect(lambda: self.sidebar.set_job(""))
        self.state.runner = self.runner
        self.runner.start()
        from .pages.reports import HistorySync
        self.history_sync = HistorySync(self)
        self._connect_sync()
        self.history_sync.start()
        self.state.accounts_changed.connect(self.history_sync.wake)
        from .watcher import SoundCloudWatcher
        self.watcher = SoundCloudWatcher(self)
        self.watcher.status.connect(lambda t: self.sidebar.set_live(t))
        self._dirty: set[str] = set()
        try:
            from .. import db
            paused = db.get().execute("SELECT message FROM jobs WHERE status = 'rate_limited' ORDER BY id DESC LIMIT 1").fetchone()
            if paused:
                self._show_limit(paused[0] or "Waiting for Last.fm.")
        except Exception:
            pass
        self._update_badges()
        self.navigate("dashboard")

    def _build(self):
        """(Re)create the sidebar and every page. The player page survives a rebuild while it's in use."""
        from .pages.activity import ActivityPage
        from .pages.dashboard import DashboardPage
        from .pages.discover import DiscoverPage
        from .pages.library import LibraryPage
        from .pages.matches import MatchReviewPage
        from .pages.reports import ReportsPage
        from .pages.set_detail import SetDetailPage
        from .pages.settings import SettingsPage
        from .player import PlayerPage

        keep_player = self.pages.get("player")
        if keep_player is not None and not keep_player.set_id:
            keep_player = None
        if keep_player is not None:
            keep_player.setParent(None)
        old = self.centralWidget()
        root = QWidget()
        root.setObjectName("Page")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = Sidebar(self.navigate)
        h.addWidget(self.sidebar)
        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        # One standing notice while imports are paused (daily limit, Last.fm down) instead of a toast per retry
        view = QPushButton("View queue")
        view.setCursor(Qt.PointingHandCursor)
        view.clicked.connect(lambda: self.navigate("queue"))
        self.limit_banner = Banner("", action=view)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(T.S6, T.S4, T.S6, 0)
        wl.addWidget(self.limit_banner)
        self.limit_wrap = wrap
        cv.addWidget(wrap)
        self.stack = QStackedWidget()
        cv.addWidget(self.stack, 1)
        h.addWidget(content, 1)
        self._show_limit(getattr(self, "_limit_msg", ""))
        self.setCentralWidget(root)
        if old is not None:
            old.deleteLater()
        self.pages = {
            "dashboard": DashboardPage(), "library": LibraryPage(), "discover": DiscoverPage(),
            "player": keep_player or PlayerPage(), "activity": ActivityPage(), "settings": SettingsPage(),
            "set": SetDetailPage(), "matches": MatchReviewPage(), "reports": ReportsPage(),
        }
        for p in self.pages.values():
            self.stack.addWidget(p)
        if getattr(self, "history_sync", None):
            self._connect_sync()

    def _connect_sync(self):
        rep = self.pages["reports"]
        self.history_sync.progress.connect(rep.on_sync_progress)
        self.history_sync.done.connect(rep.on_sync_done)

    def zoom(self, step: int):
        from .. import db
        steps = T.ZOOM_STEPS
        cur = min(range(len(steps)), key=lambda i: abs(steps[i] - T.ZOOM))
        new = 1.0 if step == 0 else steps[max(0, min(len(steps) - 1, cur + step))]
        if abs(new - T.ZOOM) < 0.001:
            return
        db.set_setting("zoom", new)
        T.set_zoom(new)
        self.restyle()
        self.toast(f"Zoom {round(new * 100)}%", sound=False)

    def restyle(self):
        """Apply a changed zoom or colour scheme: new stylesheet, then rebuild every screen in place."""
        T.icon_pixmap.cache_clear()
        QApplication.instance().setStyleSheet(T.stylesheet())
        where = self.current or ("dashboard", None)
        self.current = None
        self._build()
        self._refresh_account()
        self._update_badges()
        self.navigate(*where)

    # ---- navigation -----------------------------------------------------------------------
    def navigate(self, key: str, arg=None):
        if key in ALIASES:
            key, arg = ALIASES[key]
        if key not in self.pages:
            return
        if self.current and self.current != (key, arg) and arg != "focus-search":
            self.history.append(self.current)
            self.history = self.history[-30:]
        self.current = (key, arg)
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(key)
        if hasattr(page, "open"):
            page.open(arg)
        elif hasattr(page, "refresh"):
            page.refresh()
        self._dirty.discard(key)

    def back(self):
        if self.history:
            key, arg = self.history.pop()
            self.current = None
            self.navigate(key, arg)
            if self.history:
                self.history.pop()

    def _update_badges(self):
        from .. import repo
        try:
            n = sum(1 for s in repo.list_sessions(limit=20000)
                    if s["status"] in ("draft", "partial", "needs_review", "expired") and (s["n_pending"] or s["n_problem"]))
        except Exception:
            n = 0
        self.sidebar.buttons["activity"].set_count(n)
        self.sidebar.buttons["activity"].setToolTip(f"{n} listen(s) not imported to Last.fm yet" if n else "")

    def _on_data_changed(self):
        if not hasattr(self, "_changed_timer"):
            self._changed_timer = QTimer(self, singleShot=True, interval=250, timeout=self._apply_data_changed)
        self._changed_timer.start()

    def _apply_data_changed(self):
        self._update_badges()
        cur = self.stack.currentWidget()
        for key, page in self.pages.items():
            if page is cur:
                if hasattr(page, "refresh"):
                    QTimer.singleShot(0, page.refresh)
            else:
                self._dirty.add(key)

    def _on_job(self, progress):
        c = progress.counts or {}
        if progress.status == "uploading":
            self.sidebar.set_job(f"Importing to Last.fm · {c.get('accepted', 0)} sent, {c.get('pending', 0)} left")
            if progress.message.startswith("Sent"):      # scrobbles are going through again
                self._show_limit("")
        elif progress.status in ("completed", "partial", "needs_review", "failed", "rate_limited", "cancelled"):
            if progress.status == "rate_limited":
                # retried every so often (and on every live scrobble): keep it to one quiet banner
                self._show_limit(progress.message)
            else:
                self._show_limit("")
                kind = {"completed": "success", "partial": "warn", "needs_review": "warn"}.get(progress.status, "error")
                if progress.status != "cancelled":
                    self.toast(f"Import {progress.status.replace('_', ' ')}: {progress.message}", kind)
            from .. import insights
            insights.forget()
            self.state.lastfm_activity.emit()
            self.sidebar.set_job("")
            self.state.changed()
        cur = self.stack.currentWidget()
        if hasattr(cur, "on_job_progress"):
            cur.on_job_progress(progress)

    def _show_limit(self, message: str):
        self._limit_msg = message
        if message:
            self.limit_banner.text.setText(f"<b>Last.fm import paused.</b> {message} New listens are kept and sent later.")
        self.limit_wrap.setVisible(bool(message))

    def _refresh_account(self):
        self.sidebar.set_account(self.state.lastfm_user())

    def toast(self, text: str, kind: str = "info", sound: bool = True):
        old = getattr(self, "_toast", None)
        try:
            if old is not None:
                old.deleteLater()
        except RuntimeError:
            pass
        self._toast = Toast(self, text, kind)
        self._toast.show_for(sound=sound)

    def closeEvent(self, e):
        player = self.pages.get("player")
        if player and hasattr(player, "has_unsaved") and player.has_unsaved():
            from .dialogs.base import confirm
            if not confirm(self, "Quit without saving?", "You have a listening session in the player that hasn't been "
                           "saved yet.", "Quit", danger=True):
                e.ignore()
                return
        if player and hasattr(player, "shutdown"):
            player.shutdown()
        if getattr(self, "watcher", None):
            self.watcher.stop()
        if getattr(self, "history_sync", None):
            self.history_sync.stop()
            self.history_sync.wait(5000)
        self.runner.stop()
        self.runner.wait(8000)
        super().closeEvent(e)
