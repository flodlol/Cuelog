"""My Library: artwork grid of saved sets with search, sort and filters."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ... import repo
from .. import theme as T
from ..components import SetCard
from ..state import app_state
from ..widgets import EmptyState, FlowLayout, Segmented, button, clear_layout, icon_label, label, page_header, scroll_page


class LibraryPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, _, lay = scroll_page()
        outer.addWidget(area)

        self.subtitle = label("", "Muted")
        head, _ = page_header("Library", self.subtitle, button("Add set", "plus", "Primary", lambda: app_state().go("discover")))
        lay.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(T.S2)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search sets or uploaders")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(380)
        self.search.setAccessibleName("Search library")
        ic = icon_label("search", T.MUTED, 15)
        ic.setParent(self.search)
        ic.move(11, 11)
        self._debounce = QTimer(self, singleShot=True, interval=150, timeout=self.refresh)
        self.search.textChanged.connect(lambda: self._debounce.start())
        bar.addWidget(self.search, 1)
        bar.addSpacing(T.S2)
        self.filter = Segmented([("all", "All"), ("fav", "Favorites"), ("review", "Needs review")], lambda _: self.refresh())
        bar.addWidget(self.filter)
        bar.addStretch(1)

        self.sort = QComboBox()
        for text, key in (("Recently listened", "recent"), ("Recently added", "added"), ("Most listens", "listens"),
                          ("Title", "title"), ("Longest", "duration")):
            self.sort.addItem(f"Sort: {text}", key)
        self.sort.currentIndexChanged.connect(self.refresh)
        self.sort.setAccessibleName("Sort library")
        bar.addWidget(self.sort)
        lay.addLayout(bar)

        self.grid_host = QWidget()
        self.grid = FlowLayout(self.grid_host, spacing=16)
        self.grid.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.grid_host)
        self.empty_slot = QVBoxLayout()
        lay.addLayout(self.empty_slot)
        lay.addStretch(1)

    def open(self, arg=None):
        self.refresh()
        if arg == "focus-search":
            self.search.setFocus()

    def refresh(self):
        rows = repo.list_sets(self.search.text(), self.sort.currentData(), self.filter.current == "fav",
                              self.filter.current == "review")
        total = len(repo.list_sets())
        listens = sum(r["listen_count"] for r in rows)
        self.subtitle.setText(f"{total} set{'s' if total != 1 else ''} · {listens:,} listens logged")
        clear_layout(self.grid)
        clear_layout(self.empty_slot)
        for r in rows:
            self.grid.addWidget(SetCard(r))
        self.grid_host.setVisible(bool(rows))
        if not rows:
            if total:
                self.empty_slot.addWidget(EmptyState("search", "No matching sets", "Try another search or filter."))
            else:
                self.empty_slot.addWidget(EmptyState(
                    "library", "Your library is empty",
                    "Save a SoundCloud DJ set once, match its songs, and log every future listen in seconds.",
                    button("Add your first set", "plus", "Primary", lambda: app_state().go("discover"))))
        self.grid_host.updateGeometry()
