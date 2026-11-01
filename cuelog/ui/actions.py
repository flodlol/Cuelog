"""Shared user actions with confirmation dialogs."""
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from .. import db, jobs, repo
from ..scheduler import TimestampError
from .state import app_state
from .workers import friendly


def _ask(parent: QWidget, title: str, text: str, ok: str = "Continue", danger: bool = False) -> bool:
    from .dialogs.base import confirm
    return confirm(parent, title, text, ok, danger)


def _tell(parent: QWidget, title: str, text: str) -> None:
    from .dialogs.base import notify
    notify(parent, title, text, kind="error")


def ensure_lastfm(parent: QWidget) -> bool:
    st = app_state()
    if not st.creds.lastfm:
        if _ask(parent, "Last.fm API key needed", "Add your Last.fm API key and shared secret in Settings first.", "Open Settings"):
            st.go("settings")
        return False
    if not st.lastfm_user():
        if _ask(parent, "Connect Last.fm", "Connect your Last.fm account before importing.", "Open Settings"):
            st.go("settings")
        return False
    return True


def import_sessions(session_ids: list[int], parent: QWidget, confirm: bool = True) -> int | None:
    if not session_ids or not ensure_lastfm(parent):
        return None
    sessions = [repo.get_session(i) for i in session_ids]
    sessions = [s for s in sessions if s]
    n = sum((s["n_pending"] or 0) for s in sessions)
    problems = sum((s["n_problem"] or 0) for s in sessions)
    recon = sum(1 for s in sessions if s["reconstructed"])
    if n == 0 and problems == 0:
        app_state().toast("Nothing left to import in the selected sessions.")
        return None
    if confirm:
        text = (f"Submit {n} scrobble{'s' if n != 1 else ''} from {len(sessions)} session{'s' if len(sessions) != 1 else ''} "
                f"to Last.fm account <b>{app_state().lastfm_user()}</b>?")
        if problems:
            text += f"<br><br>{problems} previously failed or rejected scrobble(s) will be retried."
        if recon:
            text += (f"<br><br><b>{recon} session(s) use reconstructed timestamps</b>: generated times, not your real "
                     "listening history.")
        text += "<br><br>Cuelog checks your Last.fm history for duplicates first and saves progress after every batch."
        if not _ask(parent, "Import to Last.fm", text, "Import"):
            return None
    job = jobs.create_job(session_ids=session_ids, include_problems=True,
                          label=sessions[0]["set_title"] if len(sessions) == 1 else f"{len(sessions)} sessions")
    if job:
        app_state().toast("Import queued. Progress is shown in the Import Queue.", "success")
        app_state().wake_runner()
        app_state().changed()
    return job


def replan_sessions(session_ids: list[int], parent: QWidget) -> None:
    days = db.setting("max_age_days")
    if not _ask(parent, "Re-plan timestamps",
                f"Move the unsubmitted scrobbles of {len(session_ids)} session(s) to new, evenly spread times within the "
                f"last {days} days so Last.fm accepts them?<br><br>These become <b>reconstructed timestamps</b>. "
                "The previous time is kept as the original timestamp. Accepted scrobbles never move.", "Re-plan"):
        return
    try:
        n = repo.replan_sessions(session_ids)
    except (TimestampError, ValueError) as err:
        _tell(parent, "Couldn't re-plan", friendly(err))
        return
    app_state().toast(f"Re-planned {n} session(s). Review them in the queue, then import.", "success")
    app_state().changed()


def cancel_session(session_id: int, parent: QWidget) -> None:
    if not _ask(parent, "Cancel this listen?", "Songs that haven't been sent yet are removed. Anything already on "
                "Last.fm stays there.", "Cancel listen", danger=True):
        return
    try:
        repo.cancel_session(session_id)
    except ValueError as err:
        _tell(parent, "Can't cancel yet", str(err))
        return
    app_state().changed()
