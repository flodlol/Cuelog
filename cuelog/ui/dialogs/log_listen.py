"""“Log another listen”: the fast path for repeat listens of a saved set."""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtWidgets import QComboBox, QDateEdit, QFrame, QHBoxLayout, QSpinBox, QTimeEdit, QVBoxLayout, QWidget

from ... import db, jobs, repo, scheduler
from ...scheduler import TimestampError
from .. import theme as T
from ..actions import ensure_lastfm
from ..state import app_state
from ..widgets import Segmented, Toggle, button, icon_label, label
from .base import Dialog, form


class LogListenDialog(Dialog):
    def __init__(self, set_id: int, parent=None):
        self.set_id = set_id
        self.s = repo.get_set(set_id)
        self.segs = repo.segments_for_set(set_id)
        super().__init__(parent, "Log a listen", self.s["title"], self.s["artwork_url"] or "", width=540)
        self.anchor = "end"
        self.mode = "full"

        # when
        self.anchor_seg = Segmented([("end", "Finished at"), ("start", "Started at")], self._set_anchor)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("ddd d MMM yyyy")
        self.date.setMaximumDate(QDate.currentDate())
        self.date.setAccessibleName("Date")
        self.time = QTimeEdit(QTime.currentTime())
        self.time.setDisplayFormat("HH:mm")
        self.time.setFixedWidth(84)
        self.time.setAccessibleName("Time")
        when_row = QHBoxLayout()
        when_row.setSpacing(T.S2)
        when_row.addWidget(self.date, 1)
        when_row.addWidget(self.time)
        when_row.addWidget(button("Now", None, "Ghost", self._now))

        # how much
        self.mode_seg = Segmented([("full", "Whole set"), ("partial", "Part of it")], self._set_mode)
        self.first, self.last = QComboBox(), QComboBox()
        for sg in self.segs:
            text = f"{sg.position}. {sg.track or '(not matched)'}"
            self.first.addItem(text, sg.position)
            self.last.addItem(text, sg.position)
        self.last.setCurrentIndex(max(0, len(self.segs) - 1))
        for c in (self.first, self.last):
            c.setMinimumWidth(0)
            c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(14)
        range_row = QHBoxLayout()
        range_row.setSpacing(T.S2)
        range_row.addWidget(self.first, 1)
        range_row.addWidget(label("to", "Muted"))
        range_row.addWidget(self.last, 1)
        self.range_host = QWidget()
        self.range_host.setLayout(range_row)
        range_row.setContentsMargins(0, 0, 0, 0)

        self.plays = QSpinBox()
        self.plays.setRange(1, 20)
        self.plays.setFixedWidth(84)
        self.plays.setAccessibleName("Number of plays")
        plays_row = QHBoxLayout()
        plays_row.setSpacing(T.S3)
        plays_row.addWidget(self.plays)
        plays_row.addWidget(label("back to back", "Muted"))
        plays_row.addStretch(1)

        anchor_row = QHBoxLayout()
        anchor_row.addWidget(self.anchor_seg)
        anchor_row.addStretch(1)
        mode_row = QVBoxLayout()
        mode_row.setSpacing(T.S2)
        seg_row = QHBoxLayout()
        seg_row.addWidget(self.mode_seg)
        seg_row.addStretch(1)
        mode_row.addLayout(seg_row)
        mode_row.addWidget(self.range_host)       # only takes space when "Part of it" is chosen
        self.body.addLayout(form([("When", anchor_row), ("", when_row), ("Listened to", mode_row), ("Plays", plays_row)]))

        self.reconstruct = Toggle("Log it with a recent time (the real date is kept in Cuelog)")
        self.reconstruct.setVisible(False)
        self.body.addWidget(self.reconstruct)

        # summary
        self.summary = QFrame()
        self.summary.setObjectName("Summary")
        self.summary.setStyleSheet(f"QFrame#Summary {{ background:{T.SURFACE}; border:1px solid {T.BORDER}; border-radius: 6px; }}")
        sv = QVBoxLayout(self.summary)
        sv.setContentsMargins(T.S4, T.S4, T.S4, T.S4)
        sv.setSpacing(T.S2)
        top = QHBoxLayout()
        top.setSpacing(T.S2)
        self.p_count = label("", "Display")
        top.addWidget(self.p_count, 0, Qt.AlignBottom)
        self.p_main = label("", "Secondary")
        top.addWidget(self.p_main, 0, Qt.AlignBottom)
        top.addStretch(1)
        self.p_when = label("", "Secondary")
        top.addWidget(self.p_when, 0, Qt.AlignBottom)
        sv.addLayout(top)
        self.p_sub = label("", "Small", wrap=True)
        sv.addWidget(self.p_sub)
        warn = QHBoxLayout()
        warn.setSpacing(T.S2)
        self.p_warn_icon = icon_label("alert", T.AMBER, 14)
        warn.addWidget(self.p_warn_icon, 0, Qt.AlignTop)
        self.p_warn = label("", wrap=True)
        self.p_warn.setStyleSheet(f"color:{T.AMBER}; font-size:12px;")
        warn.addWidget(self.p_warn, 1)
        self.warn_host = QWidget()
        warn.setContentsMargins(0, T.S1, 0, 0)
        self.warn_host.setLayout(warn)
        sv.addWidget(self.warn_host)
        self.backfill_btn = button("Backfill instead", None, None, self._open_backfill)
        self.backfill_btn.setVisible(False)
        sv.addWidget(self.backfill_btn, 0, Qt.AlignLeft)
        self.body.addWidget(self.summary)

        user = app_state().lastfm_user()
        self.submit = Toggle("Send to Last.fm now" if user else "Send to Last.fm now (connect first)", bool(user))
        self.submit.setEnabled(bool(user))
        self.footer_left.addWidget(self.submit)
        self.ok = self.add_actions("Log listen", self.accept_listen)

        for sig in (self.date.dateChanged, self.time.timeChanged, self.first.currentIndexChanged,
                    self.last.currentIndexChanged, self.plays.valueChanged, self.reconstruct.toggled):
            sig.connect(self.update_preview)
        self._set_mode("full")
        self._now()
        self.ok.setFocus()

    def _now(self):
        self.date.setDate(QDate.currentDate())
        self.time.setTime(QTime.currentTime())

    def _set_anchor(self, k):
        self.anchor = k
        self.anchor_seg.set(k)
        self.update_preview()

    def _set_mode(self, k):
        self.mode = k
        self.mode_seg.set(k)
        self.range_host.setVisible(k == "partial")
        self.update_preview()

    def _range(self):
        if self.mode == "partial":
            a, b = self.first.currentData(), self.last.currentData()
            return (min(a, b), max(a, b))
        return (None, None)

    def _start_ts(self) -> int:
        d, t = self.date.date(), self.time.time()
        ts = int(time.mktime(datetime(d.year(), d.month(), d.day(), t.hour(), t.minute()).timetuple()))
        if self.anchor == "end":
            first, last = self._range()
            ts -= scheduler.listen_span(self.segs, first, last) * self.plays.value()
        return ts

    def _warn(self, text: str):
        self.p_warn.setText(text)
        self.warn_host.setVisible(bool(text))

    def update_preview(self):
        if not hasattr(self, "p_main"):
            return
        first, last = self._range()
        try:
            start = self._start_ts()
            max_age = float(db.setting("max_age_days"))
            self.reconstruct.setVisible(start < int(time.time()) - int(max_age * 86400))
            pv = repo.preview_listen(self.set_id, start, self.plays.value(), first, last,
                                     reconstruct=self.reconstruct.isChecked())
        except TimestampError as err:
            self.p_count.setText("—")
            self.p_main.setText("can't be logged like this")
            self.p_when.setText("")
            self.p_sub.setText(str(err))
            many = self.plays.value() > 1 or "Overlaps" in str(err) or "days" in str(err)
            self.backfill_btn.setVisible(many)
            if many:
                self._warn("For several past listens, Backfill spreads them over the last two weeks for you.")
            else:
                self._warn("")
            self.ok.setEnabled(False)
            self.adjustSize()
            return
        self.backfill_btn.setVisible(False)
        b0, bN = pv["blocks"][0], pv["blocks"][-1]
        n_listens = len(pv["blocks"])
        self.p_count.setText(f"{pv['total']:,}")
        self.p_main.setText("scrobbles" + (f" · {n_listens} listens" if n_listens > 1 else ""))
        self.p_when.setText(f"{scheduler.fmt_local(b0['start'], '%H:%M')} – {scheduler.fmt_local(bN['end'], '%H:%M')}")
        lines = []
        sc = b0["scrobbles"]
        if sc:
            lines.append(f"Starts with {sc[0].segment.artist} – {sc[0].segment.track}")
            if len(sc) > 1:
                lines.append(f"Ends with {sc[-1].segment.artist} – {sc[-1].segment.track}")
        if b0["real_start"]:
            lines.append(f"You listened on {scheduler.fmt_local(b0['real_start'])}; Last.fm gets the recent time above.")
        self.p_sub.setText("\n".join(lines))
        warn = []
        if pv["skipped"]:
            names = ", ".join(f"#{s.position}" for s in pv["skipped"][:8])
            warn.append(f"{len(pv['skipped'])} song{'s' if len(pv['skipped']) != 1 else ''} skipped ({names}): "
                        f"{pv['skipped'][0].reason.lower()}.")
        if pv["displaced"]:
            warn.append(f"{len(pv['displaced'])} backfilled listen(s) with generated times are moved out of the way.")
        self._warn(" ".join(warn))
        self.ok.setEnabled(pv["total"] > 0)
        self.adjustSize()

    def _open_backfill(self):
        from .backfill import BackfillDialog
        n = self.plays.value()
        self.reject()
        d = BackfillDialog(self.set_id, self.parent())
        d.count.setValue(max(n, 1))
        d.exec()

    def accept_listen(self):
        first, last = self._range()
        try:
            ids = repo.create_listen(self.set_id, self._start_ts(), self.plays.value(), first, last,
                                     reconstruct=self.reconstruct.isChecked())
        except TimestampError as err:
            self.p_sub.setText(str(err))
            return
        st = app_state()
        if self.submit.isChecked() and ensure_lastfm(self):
            if jobs.create_job(session_ids=ids, label=self.s["title"]):
                st.wake_runner()
                st.toast("Listen logged. Sending it to Last.fm…", "success")
        else:
            st.toast("Listen logged. Send it from Activity whenever you like.", "success")
        st.changed()
        self.accept()
