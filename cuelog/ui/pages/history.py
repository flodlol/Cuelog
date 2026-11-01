"""History: every locally recorded listening session."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QTableWidget, \
    QTableWidgetItem, QVBoxLayout, QWidget

from ... import repo, scheduler
from .. import theme as T
from ..components import KIND_LABEL
from ..widgets import label

STATUSES = ["All", "draft", "queued", "completed", "partial", "needs_review", "expired", "cancelled"]


class HistoryPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 20, 0, 24)
        lay.setSpacing(12)
        self.sub = label("", "Muted")
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by set")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        self._deb = QTimer(self, singleShot=True, interval=150, timeout=self.refresh)
        self.search.textChanged.connect(lambda: self._deb.start())
        bar.addWidget(self.search)
        self.status = QComboBox()
        for s in STATUSES:
            self.status.addItem(T.STATUS_STYLE.get(s, (s,))[0] if s != "All" else "All statuses", s)
        self.status.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.status)
        bar.addStretch(1)
        bar.addWidget(self.sub)
        lay.addLayout(bar)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["When", "Set", "Type", "Tracks", "Accepted", "Status", "Notes"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(6, QHeaderView.Stretch)
        for i, w in ((0, 190), (2, 130), (3, 70), (4, 80), (5, 140)):
            self.table.setColumnWidth(i, w)
        self.table.cellDoubleClicked.connect(self._open)
        lay.addWidget(self.table, 1)

    def refresh(self):
        status = self.status.currentData()
        rows = repo.list_sessions(status=None if status == "All" else status, limit=20000)
        q = self.search.text().strip().lower()
        if q:
            rows = [r for r in rows if q in r["set_title"].lower()]
        self.sub.setText(f"{len(rows):,} sessions · double-click one to see its songs")
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            when = scheduler.fmt_local(r["started_at"], "%Y-%m-%d %H:%M")
            notes = []
            if r["reconstructed"]:
                notes.append("Reconstructed time" + (f"; real {scheduler.fmt_local(r['real_started_at'], '%Y-%m-%d %H:%M')}"
                                                     if r["real_started_at"] else ""))
            if r["partial"]:
                notes.append("Partial listen")
            vals = [when, r["set_title"], KIND_LABEL.get(r["kind"], r["kind"]), str(r["n_total"] or 0),
                    str(r["n_accepted"] or 0), T.STATUS_STYLE.get(r["status"], (r["status"],))[0], "; ".join(notes)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, r["id"])
                if c == 5:
                    it.setForeground(T.qcolor(T.STATUS_STYLE.get(r["status"], ("", T.TEXT_2))[1]))
                self.table.setItem(i, c, it)

    def _open(self, row, _col):
        sid = self.table.item(row, 0).data(Qt.UserRole)
        from ..dialogs.session_detail import SessionDetailDialog
        SessionDetailDialog(sid, self.window()).exec()
