"""Activity → To import: listens waiting for Last.fm and the history of imports, as compact rows."""
from __future__ import annotations

import calendar
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QMenu, QPlainTextEdit, QProgressBar, QVBoxLayout,
                               QWidget)

from ... import db, jobs, repo, scheduler
from .. import theme as T
from ..actions import import_sessions, replan_sessions
from ..components import ElidedLabel, relative_time
from ..state import app_state
from ..widgets import Artwork, Badge, Banner, button, clear_layout, divider, label, scroll_page

ACTIVE = ("queued", "uploading", "rate_limited")
DOT = {"completed": T.GREEN, "partial": T.AMBER, "needs_review": T.AMBER, "rate_limited": T.AMBER, "failed": T.RED,
       "uploading": T.ACCENT, "queued": T.BLUE, "cancelled": T.MUTED}


def _utc(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return calendar.timegm(time.strptime(text[:19], "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def _list_frame() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("Card")
    v = QVBoxLayout(f)
    v.setContentsMargins(0, T.S1, 0, T.S1)
    v.setSpacing(0)
    return f, v


class JobRow(QWidget):
    """One import: status dot, what was imported, how many scrobbles, when; actions on the right."""

    def __init__(self, j: dict, on_change):
        super().__init__()
        self.j = j
        c = j["counts"]
        v = QVBoxLayout(self)
        v.setContentsMargins(T.S4, T.S3, T.S3, T.S3)
        v.setSpacing(T.S2)
        row = QHBoxLayout()
        row.setSpacing(T.S3)
        dot = QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{DOT.get(j['status'], T.MUTED)}; border-radius: 4px;")
        dot.setToolTip(T.STATUS_STYLE.get(j["status"], (j["status"],))[0])
        row.addWidget(dot)
        sets = j.get("sets") or []
        title = sets[0][0] if sets else (j["label"] or f"Import {j['id']}")
        if len(sets) > 1:
            title += f" + {len(sets) - 1} more"
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(ElidedLabel(title, "H3"))
        n_listens = j.get("sessions") or 0
        sub = [f"{n_listens} listen{'s' if n_listens != 1 else ''}"] if n_listens else []
        if j["status"] in ACTIVE:
            sub.append(j["message"] or T.STATUS_STYLE.get(j["status"], ("",))[0])
        elif j["status"] in ("cancelled", "partial"):
            sub.append(T.STATUS_STYLE[j["status"]][0])
        elif j["status"] in ("failed", "needs_review") and j["message"]:
            sub.append(j["message"])
        col.addWidget(ElidedLabel(" · ".join(sub), "Small"))
        row.addLayout(col, 1)
        counts = [f"<span style='color:{T.TEXT}'>{c['accepted']:,}</span> scrobbled"]
        if c["rejected"]:
            counts.append(f"<span style='color:{T.RED}'>{c['rejected']:,} rejected</span>")
        if c["skipped"]:
            counts.append(f"{c['skipped']:,} skipped")
        if c["pending"] and j["status"] not in ("completed",):
            counts.append(f"{c['pending']:,} left")
        nums = label(" · ".join(counts), "Small")
        nums.setTextFormat(Qt.RichText)
        row.addWidget(nums)
        when = label(relative_time(_utc(j["created_at"])), "Small")
        when.setFixedWidth(110)
        when.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(when)
        more = button("", "more", "Ghost", tooltip="More")
        more.setAccessibleName("Import actions")
        menu = QMenu(more)
        if j["status"] in ACTIVE:
            menu.addAction("Cancel what hasn't been sent", lambda: (jobs.cancel_job(j["id"]), on_change()))
        if j["status"] in ("failed", "partial", "needs_review", "rate_limited", "cancelled") and (c["pending"] or c["rejected"]):
            menu.addAction("Retry", lambda: (jobs.retry_job(j["id"]), app_state().wake_runner(), on_change()))
        menu.addAction("Show details", self.details)
        more.setMenu(menu)
        row.addWidget(more)
        v.addLayout(row)
        if j["status"] in ACTIVE:
            bar = QProgressBar()
            total = max(1, j["total"])
            bar.setMaximum(total)
            bar.setValue(min(total, c["accepted"] + c["rejected"] + c["skipped"]))
            v.addWidget(bar)

    def details(self):
        from ..dialogs.base import Dialog
        d = Dialog(self.window(), "Import details", "Every request to Last.fm and its reply. Logins are never logged.",
                   icon="list", width=760)
        d.resize(800, 560)
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        rows = db.get().execute("SELECT * FROM api_log WHERE job_id = ? ORDER BY id", (self.j["id"],)).fetchall()
        lines = []
        for r in rows:
            lines.append(f"[{r['created_at']} UTC] {r['method']}  HTTP {r['http_status'] or '-'}")
            if r["error"]:
                lines.append(f"  error: {r['error']}")
            if r["request"]:
                lines.append(f"  request: {r['request'][:1500]}")
            if r["response"]:
                lines.append(f"  response: {r['response'][:3000]}")
        bad = db.get().execute("""SELECT artist, track, timestamp, status, ignored_code, ignored_message, last_error
                                  FROM scrobbles WHERE job_id = ? AND status NOT IN ('accepted') ORDER BY timestamp""",
                               (self.j["id"],)).fetchall()
        if bad:
            lines.append("\nNot accepted:")
            for b in bad:
                lines.append(f"  {scheduler.fmt_local(b['timestamp'])}  {b['artist']} - {b['track']}: {b['status']}"
                             f"{' (code ' + str(b['ignored_code']) + ')' if b['ignored_code'] else ''} "
                             f"{b['ignored_message'] or b['last_error'] or ''}")
        txt.setPlainText("\n".join(lines) or "Nothing sent yet.")
        d.body.addWidget(txt, 1)
        d.add_actions("Close", cancel=None)
        d.exec()


class QueuePage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, _, lay = scroll_page(margins=(0, T.S5, 0, T.S6))
        outer.addWidget(area)
        self.banner = QVBoxLayout()
        self.banner.setSpacing(T.S2)
        lay.addLayout(self.banner)

        self.ready_head = QHBoxLayout()
        self.ready_head.setSpacing(T.S3)
        lay.addLayout(self.ready_head)
        self.ready_frame, self.ready_lay = _list_frame()
        lay.addWidget(self.ready_frame)

        imports_head = QHBoxLayout()
        imports_head.addWidget(label("Imports", "H2"))
        imports_head.addStretch(1)
        self.imports_note = label("", "Small")
        imports_head.addWidget(self.imports_note)
        lay.addLayout(imports_head)
        self.jobs_frame, self.jobs_lay = _list_frame()
        lay.addWidget(self.jobs_frame)
        self.jobs_empty = label("Imports you start show up here.", "Muted")
        lay.addWidget(self.jobs_empty)
        lay.addStretch(1)
        self.checks: dict[int, QCheckBox] = {}

    def refresh(self):
        clear_layout(self.banner)
        st = app_state()
        if not st.lastfm_user():
            self.banner.addWidget(Banner("Connect Last.fm to import your listens.",
                                         action=button("Connect", None, None, lambda: st.go("settings"))))
        sessions = repo.list_sessions(limit=10000)
        ready = [s for s in sessions if s["status"] in ("draft", "partial", "needs_review") and (s["n_pending"] or s["n_problem"])
                 and not s["n_queued"]]
        expired = [s for s in sessions if s["status"] == "expired"]
        if expired:
            n = len(expired)
            it = "them" if n != 1 else "it"
            self.banner.addWidget(Banner(
                f"{n} listen{'s are' if n != 1 else ' is'} older than Last.fm accepts (about two weeks). Re-plan "
                f"{it} to recent, clearly marked times, or leave {it} in your history.",
                action=button("Re-plan", None, None, lambda: replan_sessions([s["id"] for s in expired], self.window()))))

        clear_layout(self.ready_head)
        clear_layout(self.ready_lay)
        self.checks = {}
        if ready:
            self.ready_head.addWidget(label(f"Waiting for Last.fm · {len(ready)}", "H2"))
            self.ready_head.addStretch(1)
            sel = QCheckBox("Select all")
            sel.toggled.connect(lambda on: [c.setChecked(on) for c in self.checks.values()])
            self.ready_head.addWidget(sel)
            self.ready_head.addWidget(button("Import selected", None, "Primary", self.import_selected))
        else:
            self.ready_head.addWidget(label("Nothing waiting. Every listen you've logged is on Last.fm.", "Muted"))
            self.ready_head.addStretch(1)
        self.ready_frame.setVisible(bool(ready))
        n_recon = sum(1 for s in ready if s["reconstructed"])
        for i, s in enumerate(ready[:400]):
            if i:
                self.ready_lay.addWidget(divider())
            row = QHBoxLayout()
            row.setContentsMargins(T.S4, T.S2, T.S4, T.S2)
            row.setSpacing(T.S3)
            cb = QCheckBox()
            cb.setAccessibleName(f"Select listen of {s['set_title']}")
            self.checks[s["id"]] = cb
            row.addWidget(cb)
            row.addWidget(Artwork(32, 4, s.get("artwork_url") or "", icon="disc"))
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(ElidedLabel(s["set_title"], "H3"))
            tags = [scheduler.fmt_local(s["started_at"])]
            if s["reconstructed"]:
                tags.append("generated time")
            col.addWidget(label(" · ".join(tags), "Small"))
            row.addLayout(col, 1)
            row.addWidget(label(f"{(s['n_pending'] or 0) + (s['n_problem'] or 0)} songs", "Small"))
            if s["status"] != "draft":
                row.addWidget(Badge(s["status"]))
            w = QWidget()
            w.setLayout(row)
            self.ready_lay.addWidget(w)
        if n_recon:
            note = label(f"{n_recon} of these have generated times (backfilled). Only import them if you really listened "
                         "that often.", "Small", wrap=True)
            note.setContentsMargins(T.S4, T.S2, T.S4, T.S3)
            self.ready_lay.addWidget(note)

        clear_layout(self.jobs_lay)
        js = jobs.list_jobs(60)
        self.jobs_frame.setVisible(bool(js))
        self.jobs_empty.setVisible(not js)
        total = sum(j["counts"]["accepted"] for j in js)
        self.imports_note.setText(f"{total:,} scrobbles sent" if js else "")
        for i, j in enumerate(js):
            if i:
                self.jobs_lay.addWidget(divider())
            self.jobs_lay.addWidget(JobRow(j, self.refresh))

    def import_selected(self):
        ids = [i for i, c in self.checks.items() if c.isChecked()]
        if not ids:
            app_state().toast("Select at least one listen.")
            return
        import_sessions(ids, self.window())

    def on_job_progress(self, _p):
        self.refresh()
