"""Backfill several past listens, spread over the accepted window with an editable per-day plan."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget)

from ... import db, jobs, repo, scheduler
from ...scheduler import TimestampError
from .. import theme as T
from ..actions import ensure_lastfm
from ..state import app_state
from ..widgets import StatusLabel, Toggle, button, fmt_duration, label
from .base import Dialog


class BackfillDialog(Dialog):
    def __init__(self, set_id: int, parent=None):
        self.set_id = set_id
        self.s = repo.get_set(set_id)
        super().__init__(parent, "Backfill listens", self.s["title"], self.s["artwork_url"] or "", width=800)
        st = db.all_settings()
        self.max_age = float(st["max_age_days"])
        self.days = scheduler.window_days(int(time.time()), self.max_age - 0.5)
        self.starts: list[int] = []
        self.segs = repo.segments_for_set(set_id)
        self.span = scheduler.listen_span(self.segs)

        note = label(f"For listens you've already made. They get generated times spread over the last {int(self.max_age)} "
                     "days, because Last.fm only accepts recent scrobbles, and they're marked as backfilled everywhere.",
                     "Muted", wrap=True)
        self.body.addWidget(note)

        top = QHBoxLayout()
        top.setSpacing(T.S3)
        top.addWidget(label("Listens", "Secondary"))
        self.count = QSpinBox()
        self.count.setRange(1, 500)
        self.count.setValue(10)
        self.count.setFixedWidth(84)
        top.addWidget(self.count)
        top.addSpacing(T.S4)
        top.addWidget(label("Between", "Secondary"))
        self.h_start = QTimeEdit(QTime.fromString(st["backfill_day_start"], "HH:mm"))
        self.h_end = QTimeEdit(QTime.fromString(st["backfill_day_end"], "HH:mm"))
        for w in (self.h_start, self.h_end):
            w.setDisplayFormat("HH:mm")
            w.setFixedWidth(76)
        top.addWidget(self.h_start)
        top.addWidget(label("and", "Secondary"))
        top.addWidget(self.h_end)
        top.addStretch(1)
        top.addWidget(button("Spread evenly", None, "Ghost", self.spread))
        self.body.addLayout(top)

        cal = QFrame()
        cal.setObjectName("Card")
        grid = QGridLayout(cal)
        grid.setContentsMargins(T.S3, T.S3, T.S3, T.S3)
        grid.setHorizontalSpacing(T.S2)
        grid.setVerticalSpacing(T.S2)
        self.day_spins: dict = {}
        offset = self.days[0].weekday() if self.days else 0
        for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            h = label(name, "Small")
            h.setAlignment(Qt.AlignCenter)
            grid.addWidget(h, 0, i)
        for i, d in enumerate(self.days):
            k = i + offset
            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(2)
            day = label(d.strftime("%d %b"), "Small")
            day.setAlignment(Qt.AlignCenter)
            cv.addWidget(day)
            sp = QSpinBox()
            sp.setRange(0, 24)
            sp.setAlignment(Qt.AlignCenter)
            sp.valueChanged.connect(self.recompute)
            sp.setAccessibleName(f"Listens on {d.isoformat()}")
            cv.addWidget(sp)
            self.day_spins[d] = sp
            grid.addWidget(cell, 1 + k // 7, k % 7)
        self.body.addWidget(cal)

        line = QHBoxLayout()
        self.summary = label("", "H3")
        line.addWidget(self.summary)
        line.addStretch(1)
        self.sub = label("", "Small")
        line.addWidget(self.sub)
        self.body.addLayout(line)
        self.error = StatusLabel()
        self.body.addWidget(self.error)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Generated time", "Songs", "Starts with"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 230)
        self.table.setColumnWidth(1, 70)
        self.table.setMinimumHeight(170)
        self.table.setMaximumHeight(240)
        self.body.addWidget(self.table)

        user = app_state().lastfm_user()
        self.submit = Toggle("Send to Last.fm now", False)
        self.submit.setEnabled(bool(user))
        self.footer_left.addWidget(self.submit)
        self.ok = self.add_actions("Create listens", self.create)
        self.count.valueChanged.connect(self.spread)
        self.h_start.timeChanged.connect(self.spread)
        self.h_end.timeChanged.connect(self.spread)
        self.spread()

    def _hours(self) -> tuple[str, str]:
        return self.h_start.time().toString("HH:mm"), self.h_end.time().toString("HH:mm")

    def spread(self):
        if not self.span:
            self.recompute()
            return
        busy = repo.busy_intervals()
        buffer_s = int(float(db.setting("recent_buffer_minutes")) * 60)
        n = self.count.value()
        start, end = self._hours()
        cap = scheduler.free_capacity(self.days, now=int(time.time()), max_age_days=self.max_age - 0.5,
                                      day_start=start, day_end=end, buffer_s=buffer_s, busy=busy)
        counts = scheduler.distribute_evenly(n, self.days, cap, self.span)
        if sum(counts.values()) < n and (start, end) != ("00:00", "23:59"):
            # Not enough room in the usual listening hours: use whole days.
            for w, t in ((self.h_start, "00:00"), (self.h_end, "23:59")):
                w.blockSignals(True)
                w.setTime(QTime.fromString(t, "HH:mm"))
                w.blockSignals(False)
            return self.spread()
        for d, sp in self.day_spins.items():
            sp.blockSignals(True)
            sp.setValue(counts.get(d, 0))
            sp.blockSignals(False)
        self.recompute()
        if sum(counts.values()) < n:
            self.error.setText(f"Only {sum(counts.values())} of {n} listens fit in the last {int(self.max_age)} days next "
                               "to your other listens.")

    def recompute(self):
        counts = {d: sp.value() for d, sp in self.day_spins.items()}
        self.error.setStyleSheet("")
        self.error.setText("")
        self.table.setRowCount(0)
        self.starts = []
        total = sum(counts.values())
        if not self.segs or self.span <= 0:
            self.error.setText("Add a tracklist first.")
            self.ok.setEnabled(False)
            return
        start, end = self._hours()
        try:
            self.starts = scheduler.backfill_starts(
                counts, self.span, now=int(time.time()), max_age_days=self.max_age - 0.5, day_start=start, day_end=end,
                buffer_s=int(float(db.setting("recent_buffer_minutes")) * 60), busy=repo.busy_intervals())
        except TimestampError as err:
            self.error.setStyleSheet(f"color:{T.RED};")
            self.error.setText(str(err))
            self.ok.setEnabled(False)
            self.summary.setText(f"{total} listens")
            return
        pv = repo.preview_backfill(self.set_id, self.starts)
        n = sum(len(b["scrobbles"]) for b in pv["blocks"])
        cap = int(db.setting("daily_cap"))
        self.summary.setText(f"{total} listen{'s' if total != 1 else ''} · {n:,} scrobbles")
        days_needed = -(-n // cap) if cap else 1
        self.sub.setText(f"{fmt_duration(self.span)} each" + (f" · takes about {days_needed} days to send "
                                                              f"({cap:,} a day)" if days_needed > 1 else ""))
        self.table.setRowCount(len(pv["blocks"]))
        for i, b in enumerate(pv["blocks"]):
            first = b["scrobbles"][0].segment if b["scrobbles"] else None
            vals = [f"{scheduler.fmt_local(b['start'], '%a %d %b, %H:%M')} – {scheduler.fmt_local(b['end'], '%H:%M')}",
                    str(len(b["scrobbles"])), f"{first.artist} – {first.track}" if first else ""]
            for c, val in enumerate(vals):
                self.table.setItem(i, c, QTableWidgetItem(val))
        if pv["skipped"]:
            self.error.setStyleSheet(f"color:{T.AMBER};")
            self.error.setText(f"{len(pv['skipped'])} song(s) per listen are skipped until their match is reviewed.")
        self.ok.setText(f"Create {total} listen{'s' if total != 1 else ''}")
        self.ok.setEnabled(total > 0 and n > 0)

    def create(self):
        try:
            ids = repo.create_backfill(self.set_id, self.starts)
        except TimestampError as err:
            self.error.setText(str(err))
            return
        st = app_state()
        if self.submit.isChecked() and ensure_lastfm(self):
            jobs.create_job(session_ids=ids, label=f"Backfill: {self.s['title']}")
            st.wake_runner()
        st.toast(f"Added {len(ids)} backfilled listens", "success")
        st.changed()
        self.accept()
