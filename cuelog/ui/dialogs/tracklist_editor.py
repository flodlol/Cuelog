"""Tracklist editor: paste, parse, reorder (drag and drop), edit, CSV import/export."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QMenu, QPlainTextEdit, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import repo, services, tracklist
from ...tracklist import TrackEntry, format_timestamp, parse_timestamp
from .. import theme as T
from ..state import app_state
from ..widgets import StatusLabel, button, label
from .base import Dialog
from ..workers import friendly, run

COLS = ["#", "Start", "Artist", "Title", "Original line"]


class TrackTree(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.setColumnCount(len(COLS))
        self.setHeaderLabels(COLS)
        self.setRootIsDecorated(False)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                             QAbstractItemView.SelectedClicked)
        self.setAlternatingRowColors(False)
        self.setUniformRowHeights(True)
        h = self.header()
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.setColumnWidth(0, 40)
        self.setColumnWidth(1, 70)
        self.setColumnWidth(4, 220)
        self.setAccessibleName("Tracklist")
        self.model().rowsMoved.connect(self.renumber)

    def dropEvent(self, e):
        super().dropEvent(e)
        # flatten any accidental nesting
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            while it.childCount():
                child = it.takeChild(0)
                self.insertTopLevelItem(self.indexOfTopLevelItem(it) + 1, child)
        self.renumber()

    def add_entry(self, e: TrackEntry, index: int | None = None):
        it = QTreeWidgetItem(["", format_timestamp(e.start_s), e.artist, e.title,
                              f"Spotify: {e.spotify_id}" if e.spotify_id else e.raw_line])
        it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable | Qt.ItemIsDragEnabled)
        it.setData(0, Qt.UserRole, e.raw_line)
        it.setData(1, Qt.UserRole, e.spotify_id)
        it.setData(2, Qt.UserRole, e.start_s is not None and "tracklist")
        it.setForeground(4, T.qcolor(T.MUTED))
        if e.problems:
            it.setToolTip(3, "; ".join(e.problems))
            it.setForeground(3, T.qcolor(T.AMBER))
        if index is None:
            self.addTopLevelItem(it)
        else:
            self.insertTopLevelItem(index, it)
        self.renumber()
        return it

    def renumber(self, *_):
        for i in range(self.topLevelItemCount()):
            self.topLevelItem(i).setText(0, str(i + 1))

    def entries(self) -> tuple[list[TrackEntry], list[str]]:
        out, problems = [], []
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            start_text = it.text(1).strip()
            start = None
            if start_text:
                start = int(start_text) if start_text.isdigit() else parse_timestamp(start_text)
                if start is None:
                    problems.append(f"Row {i + 1}: “{start_text}” is not a time (use mm:ss or h:mm:ss)")
            artist, title = it.text(2).strip(), it.text(3).strip()
            sid = it.data(1, Qt.UserRole)
            if not title and not sid:
                problems.append(f"Row {i + 1}: title is empty")
            raw = it.data(0, Qt.UserRole) or f"{artist} - {title}"
            out.append(TrackEntry(i + 1, raw, artist, title, start, sid))
        return out, problems


class TracklistEditor(Dialog):
    def __init__(self, set_id: int, parent=None):
        self.set_id = set_id
        self.s = repo.get_set(set_id)
        super().__init__(parent, "Edit tracklist", self.s["title"], self.s["artwork_url"] or "", width=1080)
        self.resize(1160, 760)
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(T.S5)
        split.setStyleSheet("QSplitter::handle { background: transparent; }")

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(T.S2)
        lh = QHBoxLayout()
        lh.addWidget(label("Paste", "H3"))
        lh.addStretch(1)
        more = button("", "more", "Ghost", tooltip="More ways to add songs")
        menu = QMenu(more)
        act = menu.addAction("Find the tracklist in the description", self.from_description)
        act.setEnabled(bool(self.s.get("description")))
        menu.addSeparator()
        menu.addAction("Import CSV…", self.import_csv)
        menu.addAction("Export CSV…", self.export_csv)
        more.setMenu(menu)
        lh.addWidget(more)
        lv.addLayout(lh)
        lv.addWidget(label("One song per line. Numbers, timestamps and Spotify links are understood.", "Small", wrap=True))
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText("01. JDX ft. Sarah Maria - Live The Moment\n02. D-Block & S-te-Fan - Music Made Addict\n\n"
                                      "00:00 JDX - Live The Moment\n03:12 D-Block & S-te-Fan - Music Made Addict\n\n"
                                      "https://open.spotify.com/track/…")
        self.paste.setAccessibleName("Paste tracklist")
        self.paste.setLineWrapMode(QPlainTextEdit.NoWrap)   # one song per line stays readable
        lv.addWidget(self.paste, 1)
        r = QHBoxLayout()
        r.setSpacing(T.S2)
        r.addWidget(button("Use this list", None, None, lambda: self.parse(replace=True),
                           tooltip="Replace the songs on the right with what you pasted"))
        r.addWidget(button("Add to the end", None, "Ghost", lambda: self.parse(replace=False)))
        r.addStretch(1)
        lv.addLayout(r)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(T.S2)
        rh = QHBoxLayout()
        rh.setSpacing(T.S1)
        self.count = label("Songs", "H3")
        rh.addWidget(self.count)
        rh.addStretch(1)
        rh.addWidget(button("Add song", "plus", "Ghost", self.add_row))
        rh.addWidget(button("", "chevron-up", "Ghost", lambda: self.move(-1), tooltip="Move up"))
        rh.addWidget(button("", "chevron-down", "Ghost", lambda: self.move(1), tooltip="Move down"))
        rh.addWidget(button("", "trash", "Ghost", self.remove_rows, tooltip="Remove selected songs"))
        tools = button("", "more", "Ghost", tooltip="More")
        tm = QMenu(tools)
        tm.addAction("Swap artist and title", self.swap)
        tm.addAction("Find and replace…", self.find_replace)
        tm.addSeparator()
        tm.addAction("Clear all start times", self.clear_times)
        tools.setMenu(tm)
        rh.addWidget(tools)
        rv.addLayout(rh)
        rv.addWidget(label("Drag to reorder. Double-click a cell to edit it. Matches are kept for songs you don't change.",
                           "Small", wrap=True))
        self.tree = TrackTree()
        self.tree.itemChanged.connect(lambda *_: self.validate())
        self.tree.model().rowsInserted.connect(lambda *_: self.validate())
        self.tree.model().rowsRemoved.connect(lambda *_: self.validate())
        rv.addWidget(self.tree, 1)
        split.addWidget(right)
        split.setSizes([380, 760])
        self.body.addWidget(split, 1)
        self.problems = StatusLabel()
        self.problems.setStyleSheet(f"color:{T.AMBER};")
        self.footer_left.addWidget(self.problems)
        self.save_btn = self.add_actions("Save tracklist", self.save)
        self._load()

    def _load(self):
        rows = repo.tracks_for_set(self.set_id)
        for t in rows:
            e = TrackEntry(t["position"], t["raw_line"], t["parsed_artist"], t["parsed_title"],
                           t["start_s"] if t["start_source"] != "estimated" else None)
            if not t["parsed_artist"] and not t["parsed_title"] and t.get("spotify_id"):
                e.spotify_id = t["spotify_id"]
            self.tree.add_entry(e)
        if not rows:
            self.paste.setPlainText(self.s.get("tracklist_raw") or "")
            if self.s.get("tracklist_raw"):
                self.parse(True)
        self.validate()

    def parse(self, replace: bool):
        entries = tracklist.parse_tracklist(self.paste.toPlainText())
        if not entries:
            self.problems.setText("No songs found in the pasted text. Put one song per line, like “Artist - Title”.")
            return
        if replace:
            self.tree.clear()
        for e in entries:
            self.tree.add_entry(e)
        self.validate()

    def from_description(self):
        entries = tracklist.extract_from_description(self.s.get("description") or "")
        if not entries:
            self.problems.setText("The SoundCloud description has no tracklist. Paste one from the comments or a "
                                  "tracklist site instead.")
            return
        self.paste.setPlainText("\n".join(e.raw_line for e in entries))
        self.parse(True)

    def import_csv(self):
        p, _ = QFileDialog.getOpenFileName(self, "Import tracklist CSV", "", "CSV (*.csv)")
        if not p:
            return
        try:
            if Path(p).stat().st_size > 5 * 1024 * 1024:
                raise ValueError("That file is larger than 5 MB.")
            entries = tracklist.entries_from_csv(Path(p).read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeDecodeError, OSError) as err:
            self.problems.setText(f"Couldn't import that CSV: {err}")
            return
        self.tree.clear()
        for e in entries:
            self.tree.add_entry(e)
        self.validate()

    def export_csv(self):
        entries, _ = self.tree.entries()
        p, _ = QFileDialog.getSaveFileName(self, "Export tracklist CSV", "tracklist.csv", "CSV (*.csv)")
        if p:
            Path(p).write_text(tracklist.entries_to_csv(entries), encoding="utf-8-sig")

    def add_row(self):
        cur = self.tree.currentItem()
        idx = self.tree.indexOfTopLevelItem(cur) + 1 if cur else None
        it = self.tree.add_entry(TrackEntry(0, "", "", ""), idx)
        self.tree.setCurrentItem(it)
        self.tree.editItem(it, 2)

    def remove_rows(self):
        for it in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
        self.tree.renumber()
        self.validate()

    def move(self, d: int):
        it = self.tree.currentItem()
        if not it:
            return
        i = self.tree.indexOfTopLevelItem(it)
        j = i + d
        if 0 <= j < self.tree.topLevelItemCount():
            self.tree.takeTopLevelItem(i)
            self.tree.insertTopLevelItem(j, it)
            self.tree.setCurrentItem(it)
            self.tree.renumber()

    def swap(self):
        for it in self.tree.selectedItems() or ([self.tree.currentItem()] if self.tree.currentItem() else []):
            a, t = it.text(2), it.text(3)
            it.setText(2, t)
            it.setText(3, a)

    def find_replace(self):
        from .base import ask_text
        find, ok = ask_text(self, "Find and replace", "Text to find in artists and titles", ok="Next")
        if not ok or not find:
            return
        repl, ok = ask_text(self, "Find and replace", f"Replace “{find}” with", ok="Replace all")
        if not ok:
            return
        n = 0
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            for c in (2, 3):
                if find in it.text(c):
                    it.setText(c, it.text(c).replace(find, repl))
                    n += 1
        self.problems.setText(f"Replaced in {n} cell{'s' if n != 1 else ''}.")

    def clear_times(self):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setText(1, "")

    def validate(self):
        entries, problems = self.tree.entries()
        self.count.setText(f"Songs · {len(entries)}")
        starts = [e for e in entries if e.start_s is not None]
        problems += tracklist.validate_timeline(starts, self.s.get("duration_s"))
        self.problems.setText("  ·  ".join(problems[:4]) + (f"  (+{len(problems) - 4} more)" if len(problems) > 4 else ""))
        self.save_btn.setEnabled(bool(entries) and not any("not a time" in p or "starts" in p or "empty" in p for p in problems))

    def save(self):
        entries, _ = self.tree.entries()
        raw = self.paste.toPlainText().strip() or "\n".join(e.raw_line for e in entries)
        self.save_btn.setEnabled(False)
        self.save_btn.setText("Saving…")
        creds = app_state().creds
        set_id = self.set_id

        def work():
            svc = services.MatchService(creds)
            inputs = services.build_track_inputs(entries, svc)
            for it, e in zip(inputs, entries):
                if e.start_s is not None:
                    it.start_source = "tracklist"
            repo.replace_tracklist(set_id, inputs, tracklist_raw=raw)
            return sum(1 for t in repo.tracks_for_set(set_id) if t["match_status"] in ("unmatched", "ambiguous"))

        def done(open_n):
            app_state().changed()
            self.accept()
            if open_n:
                app_state().go("matches", {"set_id": set_id})
                page = self.parent().window().pages.get("matches") if self.parent() else None
                if page:
                    page.auto_match()

        def fail(err):
            self.save_btn.setEnabled(True)
            self.save_btn.setText("Save tracklist")
            self.problems.setText(f"Couldn't save: {friendly(err)}")
        run(work, done, fail)
