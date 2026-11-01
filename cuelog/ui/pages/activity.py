"""Activity: everything that goes to Last.fm (To import) and everything you've logged (History)."""
from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .. import theme as T
from ..widgets import page_header
from .history import HistoryPage
from .queue import QueuePage


class ActivityPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.S6, 28, T.S6, 0)
        lay.setSpacing(T.S4)
        head, _ = page_header("Activity", "Listens waiting for Last.fm, imports in progress, and everything you've logged.")
        lay.addLayout(head)
        self.tabs = QTabWidget()
        self.queue = QueuePage()
        self.history = HistoryPage()
        self.tabs.addTab(self.queue, "To import")
        self.tabs.addTab(self.history, "History")
        self.tabs.currentChanged.connect(lambda _: self.tabs.currentWidget().refresh())
        lay.addWidget(self.tabs, 1)

    def open(self, arg=None):
        if arg in ("queue", "history"):
            self.tabs.setCurrentWidget(self.queue if arg == "queue" else self.history)
        self.refresh()

    def refresh(self):
        self.tabs.currentWidget().refresh()

    def on_job_progress(self, p):
        if self.tabs.currentWidget() is self.queue:
            self.queue.on_job_progress(p)
