"""Background work: one-off tasks on a thread pool and the long-running import runner."""
from __future__ import annotations

import time
import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal

from .. import db, jobs
from ..config import load_credentials
from ..lastfm import LastfmClient


class _Signals(QObject):
    done = Signal(object)
    error = Signal(object)
    progress = Signal(object)


class Task(QRunnable):
    def __init__(self, fn: Callable, with_progress: bool = False):
        super().__init__()
        self.fn = fn
        self.with_progress = with_progress
        self.signals = _Signals()
        self.setAutoDelete(True)

    def run(self):
        try:
            result = self.fn(self.signals.progress.emit) if self.with_progress else self.fn()
        except Exception as err:  # surfaced to the UI as a friendly message
            err.trace = traceback.format_exc()  # type: ignore[attr-defined]
            try:
                self.signals.error.emit(err)
            except RuntimeError:
                pass
        else:
            try:
                self.signals.done.emit(result)
            except RuntimeError:
                pass
        finally:
            db.close()


_live: set = set()


def run(fn: Callable, on_done: Callable | None = None, on_error: Callable | None = None,
        on_progress: Callable | None = None) -> Task:
    """Run ``fn`` off the UI thread. If ``on_progress`` is given, fn receives an emit callback."""
    task = Task(fn, with_progress=on_progress is not None)
    _live.add(task.signals)

    def safe(cb):
        # The page that asked may have been rebuilt (zoom / colours) before the result arrived.
        def call(*a):
            try:
                cb(*a)
            except RuntimeError as err:
                if "already deleted" not in str(err):
                    raise
        return call
    if on_done:
        task.signals.done.connect(safe(on_done))
    if on_error:
        task.signals.error.connect(safe(on_error))
    if on_progress:
        task.signals.progress.connect(safe(on_progress))
    task.signals.done.connect(lambda *_: _live.discard(task.signals))
    task.signals.error.connect(lambda *_: _live.discard(task.signals))
    QThreadPool.globalInstance().start(task)
    return task


def friendly(err: Exception) -> str:
    msg = getattr(err, "friendly", None)
    if callable(msg):
        return msg()
    text = str(err) or err.__class__.__name__
    return text.replace("ERROR: ", "")


class ImportRunner(QThread):
    """Processes queued Last.fm jobs one at a time; resumes interrupted jobs at startup."""

    progress = Signal(object)    # jobs.JobProgress
    idle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._wake = False
        self.runner: jobs.JobRunner | None = None

    def wake(self):
        self._wake = True

    def stop(self):
        self._stop = True
        if self.runner:
            self.runner.stop_requested = True

    def _sleep(self, seconds: float):
        end = time.time() + seconds
        while time.time() < end and not self._stop:
            time.sleep(0.2)

    def run(self):
        try:
            while not self._stop:
                job_id = None
                try:
                    job_id = jobs.next_runnable_job()
                except Exception:
                    pass
                if job_id is None:
                    self.idle.emit()
                    for _ in range(25):
                        if self._stop or self._wake:
                            break
                        time.sleep(0.2)
                    self._wake = False
                    continue
                client = LastfmClient(load_credentials())
                self.runner = jobs.JobRunner(client, sleep=self._sleep, on_progress=self.progress.emit)
                try:
                    self.runner.run(job_id)
                except Exception as err:
                    db.get().execute("UPDATE jobs SET status = 'failed', message = ? WHERE id = ?",
                                     (f"Unexpected error: {err}", job_id))
                    self.progress.emit(jobs.JobProgress(job_id, "failed", str(err), {}))
                self.runner = None
        finally:
            db.close()
