"""Application-wide UI state and signals."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..config import Credentials, load_credentials
from ..lastfm import LastfmClient


class AppState(QObject):
    data_changed = Signal()          # library / sessions / scrobbles changed
    accounts_changed = Signal()
    job_progress = Signal(object)    # jobs.JobProgress
    toast_requested = Signal(str, str)
    navigate_requested = Signal(str, object)
    lastfm_activity = Signal()       # something was sent to Last.fm: refresh the account card

    def __init__(self):
        super().__init__()
        self.creds: Credentials = load_credentials()
        self.runner = None   # workers.ImportRunner

    def reload_credentials(self):
        self.creds = load_credentials()
        self.accounts_changed.emit()

    def lastfm_user(self) -> str | None:
        acct = LastfmClient.account()
        return acct[0] if acct else None

    def toast(self, text: str, kind: str = "info"):
        self.toast_requested.emit(text, kind)

    def go(self, page: str, arg=None):
        self.navigate_requested.emit(page, arg)

    def changed(self):
        self.data_changed.emit()

    def wake_runner(self):
        if self.runner:
            self.runner.wake()


state: AppState | None = None


def app_state() -> AppState:
    global state
    if state is None:
        state = AppState()
    return state
