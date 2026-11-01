"""One listening session: its songs as sent to Last.fm, with send / re-plan / cancel actions."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QScrollArea, QVBoxLayout, QWidget

from ... import repo, scheduler
from .. import theme as T
from ..actions import cancel_session, import_sessions, replan_sessions
from ..components import KIND_LABEL, ElidedLabel
from ..widgets import Artwork, Badge, button, clear_layout, divider, label
from .base import Dialog

STATUS_COLOR = {"accepted": T.GREEN, "pending": T.MUTED, "queued": T.BLUE, "submitting": T.ACCENT, "uncertain": T.AMBER,
                "ignored": T.RED, "failed": T.RED, "expired": T.RED, "duplicate": T.MUTED, "skipped": T.MUTED}


class SessionDetailDialog(Dialog):
    def __init__(self, session_id: int, parent=None):
        self.session_id = session_id
        s = repo.get_session(session_id)
        super().__init__(parent, s["set_title"], None, s.get("artwork_url") or "", width=760)
        self.resize(780, 640)
        self.info = QHBoxLayout()
        self.info.setSpacing(T.S2)
        self.body.addLayout(self.info)
        self.note = label("", "Small", wrap=True)
        self.body.addWidget(self.note)
        box = QFrame()
        box.setObjectName("Card")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("CardFlat")
        self.rows = QVBoxLayout(inner)
        self.rows.setContentsMargins(0, T.S1, 0, T.S1)
        self.rows.setSpacing(0)
        area.setWidget(inner)
        bl.addWidget(area)
        self.body.addWidget(box, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(T.S2)
        self.footer_left.addLayout(self.actions)
        self.add_actions("Close", cancel=None)
        self.build()

    def build(self):
        s = repo.get_session(self.session_id)
        clear_layout(self.info)
        when = f"{scheduler.fmt_local(s['started_at'])} – {scheduler.fmt_local(s['ended_at'], '%H:%M')}"
        self.info.addWidget(label(when, "Secondary"))
        self.info.addWidget(label("·", "Muted"))
        self.info.addWidget(label(KIND_LABEL.get(s["kind"], s["kind"]) + (" · part of the set" if s["partial"] else ""), "Secondary"))
        self.info.addStretch(1)
        self.info.addWidget(Badge(s["status"]))
        notes = []
        if s["reconstructed"]:
            notes.append("Generated times: made for Last.fm, not recorded while you listened."
                         + (f" You listened on {scheduler.fmt_local(s['real_started_at'])}." if s["real_started_at"] else ""))
        self.note.setText(" ".join(notes))
        self.note.setVisible(bool(notes))

        clear_layout(self.rows)
        for i, r in enumerate(repo.scrobbles_for_session(self.session_id)):
            if i:
                self.rows.addWidget(divider())
            row = QHBoxLayout()
            row.setContentsMargins(T.S4, T.S2, T.S4, T.S2)
            row.setSpacing(T.S3)
            tm = label(scheduler.fmt_local(r["timestamp"], "%H:%M"), "Small")
            tm.setFixedWidth(40)
            if r["original_timestamp"]:
                tm.setToolTip(f"Originally {scheduler.fmt_local(r['original_timestamp'])}")
            row.addWidget(tm)
            row.addWidget(Artwork(32, 4, r.get("art_url") or ""))
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(ElidedLabel(r["track"], None))
            col.addWidget(ElidedLabel(" · ".join(x for x in (r["artist"], r["album"] or "") if x), "Small"))
            row.addLayout(col, 1)
            detail = r["ignored_message"] or r["last_error"] or ""
            st = label(T.STATUS_STYLE.get(r["status"], (r["status"],))[0], "Small")
            st.setStyleSheet(f"color:{STATUS_COLOR.get(r['status'], T.TEXT_2)}; font-size:12px;")
            st.setToolTip(detail)
            st.setFixedWidth(96)
            st.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(st)
            w = QWidget()
            w.setLayout(row)
            self.rows.addWidget(w)
        self.rows.addStretch(1)

        clear_layout(self.actions)
        if s["status"] in ("draft", "partial", "needs_review") and (s["n_pending"] or s["n_problem"]):
            self.actions.addWidget(button("Send to Last.fm", "upload", "Primary", self._import))
        more = button("", "more", None, tooltip="More")
        menu = QMenu(more)
        if s["status"] in ("draft", "expired", "needs_review") and not s["n_accepted"]:
            menu.addAction("Move to recent times", self._replan)
        if s["status"] not in ("cancelled", "completed", "queued"):
            menu.addAction("Cancel this listen", self._cancel)
        more.setMenu(menu)
        more.setVisible(not menu.isEmpty())
        self.actions.addWidget(more)

    def _import(self):
        if import_sessions([self.session_id], self):
            self.accept()

    def _replan(self):
        replan_sessions([self.session_id], self)
        self.build()

    def _cancel(self):
        cancel_session(self.session_id, self)
        self.build()
