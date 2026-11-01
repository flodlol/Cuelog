"""Domain widgets: set cards, set rows, session rows."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import repo, scheduler
from . import theme as T
from .state import app_state
from .widgets import Artwork, Badge, button, fmt_duration, icon_button, label


def relative_day(ts: int | None) -> str:
    if not ts:
        return "Never"
    import time
    days = (scheduler.local_date(int(time.time())) - scheduler.local_date(ts)).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return scheduler.fmt_local(ts, "%d %b %Y")


def relative_time(ts: int | None) -> str:
    """'just now', '12 min ago', 'today 14:05', 'yesterday 22:10', '3 Nov'."""
    if not ts:
        return ""
    import time
    ago = int(time.time()) - int(ts)
    if ago < 90:
        return "just now"
    if ago < 3600:
        return f"{ago // 60} min ago"
    days = (scheduler.local_date(int(time.time())) - scheduler.local_date(ts)).days
    if days == 0:
        return "today " + scheduler.fmt_local(ts, "%H:%M")
    if days == 1:
        return "yesterday " + scheduler.fmt_local(ts, "%H:%M")
    return scheduler.fmt_local(ts, "%d %b" if days < 300 else "%d %b %Y")


class SetListRow(QFrame):
    """A set as one dense row: artwork, title, details, listens, and a one-click 'Log listen'."""

    def __init__(self, s: dict):
        super().__init__()
        self.s = s
        self.setObjectName("SetListRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"QFrame#SetListRow {{ border-radius: 4px; }} QFrame#SetListRow:hover {{ background: {T.SURFACE_2}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(T.S2, T.S2, T.S2, T.S2)
        lay.setSpacing(T.S3)
        lay.addWidget(Artwork(48, 6, s.get("artwork_url") or "", icon="disc"))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(ElidedLabel(s["title"], "H3"))
        bits = [s.get("uploader") or "", fmt_duration(s.get("duration_s")) if s.get("duration_s") else "",
                f"{s.get('track_count', 0)} songs"]
        col.addWidget(ElidedLabel(" · ".join(b for b in bits if b), "Small"))
        lay.addLayout(col, 1)
        listens = s.get("listen_count", 0)
        meta = label(f"{listens} listen{'s' if listens != 1 else ''}"
                     + (f" · {relative_day(s['last_listened']).lower()}" if s.get("last_listened") else ""), "Small")
        meta.setFixedWidth(150)
        meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(meta)
        if s.get("review_count"):
            lay.addWidget(Badge("ambiguous", f"{s['review_count']} to review"))
        log = button("Log listen", None, None, self._quick_log)
        log.setEnabled(bool(s.get("track_count")))
        lay.addWidget(log)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            app_state().go("set", self.s["id"])

    def _quick_log(self):
        from .dialogs.log_listen import LogListenDialog
        LogListenDialog(self.s["id"], self.window()).exec()


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", obj: str | None = None, lines: int = 1):
        super().__init__()
        self.full = text
        self.lines = lines
        if obj:
            self.setObjectName(obj)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)
        self.setWordWrap(lines > 1)
        self.setText(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        fm = QFontMetrics(self.font())
        if self.lines == 1:
            super().setText(fm.elidedText(self.full, Qt.ElideRight, max(20, self.width())))
        else:
            words, out, line = self.full.split(), [], ""
            for w in words:
                trial = (line + " " + w).strip()
                if fm.horizontalAdvance(trial) <= self.width() or not line:
                    line = trial
                else:
                    out.append(line)
                    line = w
                if len(out) == self.lines:
                    break
            if line and len(out) < self.lines:
                out.append(line)
            text = "\n".join(out[: self.lines])
            if len(out) >= self.lines and " ".join(out) != self.full:
                text = "\n".join(out[: self.lines - 1] + [fm.elidedText(out[self.lines - 1] + " …", Qt.ElideRight, self.width())])
            super().setText(text)

    def set_full(self, text: str):
        self.full = text
        self.setToolTip(text)
        self.setText(text)
        self.resizeEvent(None) if self.width() else None


class SetCard(QFrame):
    clicked = Signal(int)

    def __init__(self, s: dict):
        super().__init__()
        self.s = s
        self.setObjectName("SetCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame#SetCard {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 16px; }}
            QFrame#SetCard:hover {{ background: {T.SURFACE_2}; border-color: {T.BORDER_STRONG}; }}""")
        self.setAccessibleName(f"Set {s['title']}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        self.art = Artwork(176, 10, s.get("artwork_url") or "", icon="disc")
        lay.addWidget(self.art)
        lay.addSpacing(2)
        title = ElidedLabel(s["title"], "H3", lines=2)
        title.setMinimumHeight(38)
        title.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(title)
        lay.addWidget(ElidedLabel(s.get("uploader") or "Unknown uploader", "Muted"))
        meta = f"{fmt_duration(s.get('duration_s'))} · {s.get('track_count', 0)} songs"
        lay.addWidget(label(meta, "Secondary"))
        foot = QHBoxLayout()
        foot.setSpacing(4)
        listens = s.get("listen_count", 0)
        info = label(f"{listens} listen{'s' if listens != 1 else ''} · {relative_day(s.get('last_listened'))}", "Muted")
        info.setStyleSheet("font-size:12px;")
        foot.addWidget(info, 1)
        star = icon_button("star", "Unfavorite" if s.get("favorite") else "Add to favorites", self._toggle_fav,
                           T.ACCENT if s.get("favorite") else T.MUTED, 16, fill=bool(s.get("favorite")))
        foot.addWidget(star)
        log = icon_button("repeat", "Log another listen", self._quick_log, T.ACCENT, 17)
        foot.addWidget(log)
        lay.addLayout(foot)
        if s.get("review_count"):
            b = Badge("ambiguous", f"{s['review_count']} to review")
            lay.addWidget(b)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return (w - 24) + T.z(160) + (T.z(28) if self.s.get("review_count") else 0)

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(206, self.heightForWidth(206))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        size = max(120, self.width() - 24)
        if size != self.art.width():
            self.art.set_size(size)

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(150, 200)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            app_state().go("set", self.s["id"])

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            app_state().go("set", self.s["id"])
        else:
            super().keyPressEvent(e)

    def _toggle_fav(self):
        repo.update_set(self.s["id"], favorite=0 if self.s.get("favorite") else 1)
        app_state().changed()

    def _quick_log(self):
        from .dialogs.log_listen import LogListenDialog
        LogListenDialog(self.s["id"], self.window()).exec()


class SetRow(QFrame):
    """Compact horizontal set entry for the dashboard."""

    def __init__(self, s: dict, right: str = ""):
        super().__init__()
        self.s = s
        self.setObjectName("SetRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"QFrame#SetRow {{ border-radius: 6px; }} QFrame#SetRow:hover {{ background: {T.SURFACE_2}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(12)
        lay.addWidget(Artwork(44, 6, s.get("artwork_url") or "", icon="disc"))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(ElidedLabel(s["title"], "H3"))
        sub = s.get("uploader") or ""
        col.addWidget(ElidedLabel(sub, "Muted"))
        lay.addLayout(col, 1)
        if right:
            lay.addWidget(label(right, "Secondary"))
        if "id" in s and s.get("track_count", 1):
            lay.addWidget(icon_button("repeat", "Log another listen", self._quick_log, T.ACCENT, 17))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            app_state().go("set", self.s["id"])

    def _quick_log(self):
        from .dialogs.log_listen import LogListenDialog
        LogListenDialog(self.s["id"], self.window()).exec()


KIND_LABEL = {"listen": "Logged listen", "backfill": "Backfilled", "player": "In-app player", "legacy": "Migrated plan"}


class SessionRow(QFrame):
    def __init__(self, s: dict, show_set: bool = True, actions: bool = True):
        super().__init__()
        self.s = s
        self.setObjectName("SessionRow")
        self.setStyleSheet(f"QFrame#SessionRow {{ border-bottom: 1px solid {T.BORDER}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 10, 6, 10)
        lay.setSpacing(12)
        if show_set:
            lay.addWidget(Artwork(40, 6, s.get("artwork_url") or "", icon="disc"))
        col = QVBoxLayout()
        col.setSpacing(2)
        when = scheduler.fmt_local(s["started_at"]) + " – " + scheduler.fmt_local(s["ended_at"], "%H:%M")
        top = ElidedLabel(s["set_title"] if show_set else when, "H3")
        col.addWidget(top)
        bits = [when] if show_set else []
        bits.append(KIND_LABEL.get(s["kind"], s["kind"]))
        if s.get("partial"):
            bits.append("partial")
        if s.get("reconstructed"):
            bits.append("reconstructed time" + (f" (real: {scheduler.fmt_local(s['real_started_at'], '%d %b %Y %H:%M')})"
                                                 if s.get("real_started_at") else ""))
        col.addWidget(ElidedLabel(" · ".join(bits), "Muted"))
        lay.addLayout(col, 1)
        n_acc, n_tot = s.get("n_accepted") or 0, s.get("n_total") or 0
        lay.addWidget(label(f"{n_acc}/{n_tot} scrobbled", "Secondary"))
        lay.addWidget(Badge(s["status"]))
        if actions:
            lay.addWidget(icon_button("list", "Session details", self._details))
            if s["status"] in ("draft", "partial", "needs_review") and (s.get("n_pending") or s.get("n_problem")):
                lay.addWidget(button("Import", "upload", "Ghost", self._import))
            if s["status"] in ("expired",) or (s["status"] == "needs_review" and not n_acc):
                lay.addWidget(button("Re-plan", "refresh", "Ghost", self._replan,
                                     tooltip="Move unsubmitted scrobbles into Last.fm's accepted window (reconstructed time)"))

    def _details(self):
        from .dialogs.session_detail import SessionDetailDialog
        SessionDetailDialog(self.s["id"], self.window()).exec()

    def _import(self):
        from .actions import import_sessions
        import_sessions([self.s["id"]], self.window())

    def _replan(self):
        from .actions import replan_sessions
        replan_sessions([self.s["id"]], self.window())


class Section(QWidget):
    def __init__(self, title: str, right: QWidget | None = None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(label(title, "H2"))
        lay.addStretch(1)
        if right:
            lay.addWidget(right)
