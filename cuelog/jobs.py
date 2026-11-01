"""Last.fm import jobs: idempotent, resumable, and safe to retry.

Lifecycle of a scrobble:

    pending --(queued in a job)--> queued --(batch sent)--> submitting
        submitting --(response)--> accepted | ignored | queued (retryable code 5)
        submitting --(timeout / malformed / crash)--> uncertain
        uncertain --(reconcile with user.getRecentTracks)--> accepted | queued

``submitting`` is persisted *before* a request goes out, so a crash mid-request
leaves evidence; such rows are reconciled against Last.fm history before any
retry, so a scrobble that Last.fm already recorded is never sent twice.
"""
from __future__ import annotations

import json
import time
import uuid as uuidlib
from dataclasses import dataclass
from typing import Callable

from . import db, http, scheduler
from .lastfm import BATCH_MAX, LastfmClient, LastfmError, MalformedResponse
from .matching import norm


@dataclass
class JobProgress:
    job_id: int
    status: str
    message: str
    counts: dict


def create_job(*, session_ids: list[int] | None = None, scrobble_ids: list[int] | None = None,
               label: str | None = None, include_problems: bool = False) -> int | None:
    """Queue the unsubmitted scrobbles of the given sessions. Returns None if nothing to do."""
    con = db.get()
    states = ["pending"] + (["failed", "ignored", "expired"] if include_problems else [])
    q = f"""SELECT c.id FROM scrobbles c JOIN sessions x ON x.id = c.session_id
            WHERE x.cancelled = 0 AND c.status IN ({','.join('?' * len(states))})"""
    args: list = list(states)
    if session_ids:
        q += f" AND c.session_id IN ({','.join('?' * len(session_ids))})"
        args += session_ids
    if scrobble_ids:
        q += f" AND c.id IN ({','.join('?' * len(scrobble_ids))})"
        args += scrobble_ids
    with db.tx(con):
        ids = [r[0] for r in con.execute(q, args)]
        if not ids:
            return None
        # ignored codes 1/2 (artist/track ignored) need a metadata fix, not a blind retry
        cur = con.execute("INSERT INTO jobs(uuid, status, label, total) VALUES (?, 'queued', ?, ?)",
                          (uuidlib.uuid4().hex, label, len(ids)))
        job_id = cur.lastrowid
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            con.execute(f"""UPDATE scrobbles SET status = 'queued', job_id = ?, ignored_code = NULL, ignored_message = NULL,
                            last_error = NULL WHERE id IN ({','.join('?' * len(part))})""", (job_id, *part))
        return job_id


def append_to_job(job_id: int, scrobble_ids: list[int]) -> None:
    """Add freshly qualified (live) scrobbles to an existing job and make it runnable again."""
    if not scrobble_ids:
        return
    con = db.get()
    with db.tx(con):
        marks = ",".join("?" * len(scrobble_ids))
        con.execute(f"UPDATE scrobbles SET status = 'queued', job_id = ? WHERE status = 'pending' AND id IN ({marks})",
                    (job_id, *scrobble_ids))
        con.execute("""UPDATE jobs SET total = total + ?, cancel_requested = 0, next_attempt_at = NULL, finished_at = NULL,
                       status = CASE WHEN status = 'uploading' THEN 'uploading' ELSE 'queued' END WHERE id = ?""",
                    (len(scrobble_ids), job_id))


def job_counts(job_id: int) -> dict:
    rows = db.get().execute("SELECT status, COUNT(*) FROM scrobbles WHERE job_id = ? GROUP BY status", (job_id,))
    c = {r[0]: r[1] for r in rows}
    return {
        "accepted": c.get("accepted", 0),
        "rejected": c.get("ignored", 0) + c.get("failed", 0) + c.get("expired", 0),
        "skipped": c.get("duplicate", 0) + c.get("skipped", 0),
        "pending": c.get("queued", 0) + c.get("submitting", 0) + c.get("uncertain", 0) + c.get("pending", 0),
        "uncertain": c.get("uncertain", 0),
        "raw": c,
    }


def list_jobs(limit: int = 200) -> list[dict]:
    rows = [dict(r) for r in db.get().execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))]
    for r in rows:
        r["counts"] = job_counts(r["id"])
        sets = db.get().execute(
            """SELECT s.title, COUNT(DISTINCT x.id) AS n FROM scrobbles c JOIN sessions x ON x.id = c.session_id
               JOIN sets s ON s.id = x.set_id WHERE c.job_id = ? GROUP BY s.id ORDER BY n DESC""", (r["id"],)).fetchall()
        r["sets"] = [(t["title"], t["n"]) for t in sets]
        r["sessions"] = sum(n for _, n in r["sets"])
    return rows


def cancel_job(job_id: int) -> None:
    """Stop a job. Queued (unsent) scrobbles go back to pending; in-flight ones finish/reconcile first."""
    con = db.get()
    with db.tx(con):
        con.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
        con.execute("UPDATE scrobbles SET status = 'pending', job_id = NULL WHERE job_id = ? AND status = 'queued'", (job_id,))
        inflight = con.execute("SELECT COUNT(*) FROM scrobbles WHERE job_id = ? AND status IN ('submitting','uncertain')",
                               (job_id,)).fetchone()[0]
        if not inflight:
            con.execute("UPDATE jobs SET status = 'cancelled', finished_at = datetime('now'), message = 'Cancelled' WHERE id = ?",
                        (job_id,))


def retry_job(job_id: int) -> None:
    """Requeue a job's failed/expired/rate-limited scrobbles (not artist/track-ignored ones)."""
    con = db.get()
    with db.tx(con):
        con.execute("""UPDATE scrobbles SET status = 'queued', last_error = NULL WHERE job_id = ?
                       AND (status IN ('failed','pending') OR (status = 'ignored' AND ignored_code = 5))""", (job_id,))
        con.execute("""UPDATE jobs SET status = 'queued', cancel_requested = 0, next_attempt_at = NULL, message = NULL,
                       finished_at = NULL WHERE id = ?""", (job_id,))


def next_runnable_job(now: int | None = None) -> int | None:
    now = now or int(time.time())
    r = db.get().execute(
        """SELECT id FROM jobs WHERE cancel_requested = 0 AND (status IN ('queued','uploading')
           OR (status = 'rate_limited' AND COALESCE(next_attempt_at, 0) <= ?)) ORDER BY id LIMIT 1""", (now,)).fetchone()
    if r:
        return r[0]
    # A cancelled job with in-flight rows still needs reconciliation.
    r = db.get().execute(
        """SELECT DISTINCT j.id FROM jobs j JOIN scrobbles c ON c.job_id = j.id
           WHERE c.status IN ('submitting','uncertain') AND COALESCE(j.next_attempt_at, 0) <= ? LIMIT 1""", (now,)).fetchone()
    return r[0] if r else None


class JobRunner:
    def __init__(self, client: LastfmClient, *, now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, on_progress: Callable[[JobProgress], None] | None = None):
        self.client = client
        self.now = now
        self.sleep = sleep
        self.on_progress = on_progress or (lambda p: None)
        self.stop_requested = False

    # ---- helpers ----------------------------------------------------------------------------
    def _set_job(self, job_id: int, status: str, message: str | None = None, next_attempt: int | None = None) -> None:
        finished = status in ("completed", "partial", "failed", "needs_review", "cancelled")
        db.get().execute(
            """UPDATE jobs SET status = ?, message = ?, next_attempt_at = ?,
               started_at = COALESCE(started_at, datetime('now')),
               finished_at = CASE WHEN ? THEN datetime('now') ELSE NULL END WHERE id = ?""",
            (status, message, next_attempt, int(finished), job_id))
        self.on_progress(JobProgress(job_id, status, message or "", job_counts(job_id)))

    def _cancelled(self, job_id: int) -> bool:
        return self.stop_requested or bool(db.get().execute(
            "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)).fetchone()[0])

    def _history(self, username: str, start: int, end: int) -> dict[tuple[str, str], list[int]]:
        seen: dict[tuple[str, str], list[int]] = {}
        for artist, track, ts in self.client.recent_tracks(username, start, end):
            seen.setdefault((norm(artist), norm(track)), []).append(ts)
        return seen

    @staticmethod
    def _in_history(row, seen, tol: int) -> bool:
        return any(abs(t - row["timestamp"]) <= tol for t in seen.get((norm(row["artist"]), norm(row["track"])), []))

    # ---- reconciliation ---------------------------------------------------------------------
    def reconcile(self, job_id: int, username: str) -> int:
        """Resolve 'submitting'/'uncertain' rows by checking Last.fm history. Returns rows resolved."""
        con = db.get()
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM scrobbles WHERE job_id = ? AND status IN ('submitting','uncertain')", (job_id,))]
        if not rows:
            return 0
        tol = int(db.setting("duplicate_tolerance_seconds"))
        seen = self._history(username, min(r["timestamp"] for r in rows) - tol, max(r["timestamp"] for r in rows) + tol)
        with db.tx(con):
            for r in rows:
                if self._in_history(r, seen, tol):
                    con.execute("""UPDATE scrobbles SET status = 'accepted', accepted_at = datetime('now'),
                                   last_error = 'Confirmed via Last.fm history after an uncertain submission' WHERE id = ?""",
                                (r["id"],))
                else:
                    con.execute("UPDATE scrobbles SET status = 'queued', last_error = 'Not found in Last.fm history; will retry' "
                                "WHERE id = ?", (r["id"],))
        db.log_api("lastfm", "reconcile", job_id=job_id, request=f"{len(rows)} uncertain", response=None)
        return len(rows)

    # ---- main loop --------------------------------------------------------------------------
    def run(self, job_id: int) -> str:
        con = db.get()
        acct = self.client.account()
        if not acct:
            self._set_job(job_id, "failed", "Connect your Last.fm account first (Settings → Accounts).")
            return "failed"
        username, session_key = acct
        settings = db.all_settings()
        max_age = float(settings["max_age_days"])
        tol = int(settings["duplicate_tolerance_seconds"])
        delay = float(settings["batch_delay_seconds"])
        max_retries = int(settings["max_retries"])
        daily_cap = int(settings["daily_cap"])
        self._set_job(job_id, "uploading", "Starting")
        attempt = 0
        try:
            self.reconcile(job_id, username)
        except (LastfmError, http.NetworkError, http.HttpError, MalformedResponse) as err:
            return self._backoff(job_id, err, attempt, max_retries)
        if self._cancelled(job_id):
            return self._finish(job_id, cancelled=True)

        now = int(self.now())
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM scrobbles WHERE job_id = ? AND status = 'queued' ORDER BY timestamp, id", (job_id,))]
        # 1. timestamp eligibility (rolling window, re-checked at submission time)
        ready = []
        with db.tx(con):
            for r in rows:
                why = scheduler.eligibility(r["timestamp"], now, max_age)
                if why == "too_old":
                    con.execute("UPDATE scrobbles SET status = 'expired', last_error = ? WHERE id = ?",
                                (f"Older than {max_age:g} days; re-plan the session to submit it", r["id"]))
                elif why == "future":
                    con.execute("UPDATE scrobbles SET status = 'failed', last_error = 'Timestamp is in the future' WHERE id = ?",
                                (r["id"],))
                else:
                    ready.append(r)
        # 2. local duplicate check: same song already accepted at (nearly) the same time
        unique = []
        with db.tx(con):
            for r in ready:
                dup = con.execute(
                    """SELECT 1 FROM scrobbles WHERE status = 'accepted' AND id != ? AND lower(artist) = lower(?)
                       AND lower(track) = lower(?) AND ABS(timestamp - ?) <= ? LIMIT 1""",
                    (r["id"], r["artist"], r["track"], r["timestamp"], tol)).fetchone()
                if dup:
                    con.execute("UPDATE scrobbles SET status = 'duplicate', last_error = 'Already accepted locally' WHERE id = ?",
                                (r["id"],))
                else:
                    unique.append(r)
        # 3. Last.fm history duplicate check
        if unique and settings["check_lastfm_history"]:
            try:
                seen = self._history(username, unique[0]["timestamp"] - tol, unique[-1]["timestamp"] + tol)
            except (LastfmError, http.NetworkError, http.HttpError, MalformedResponse) as err:
                return self._backoff(job_id, err, attempt, max_retries)
            keep = []
            with db.tx(con):
                for r in unique:
                    if self._in_history(r, seen, tol):
                        con.execute("UPDATE scrobbles SET status = 'duplicate', last_error = 'Already in your Last.fm history' "
                                    "WHERE id = ?", (r["id"],))
                    else:
                        keep.append(r)
            unique = keep

        # 4. batches
        i = 0
        while i < len(unique):
            if self._cancelled(job_id):
                return self._finish(job_id, cancelled=True)
            sent_today = con.execute(
                "SELECT COUNT(*) FROM scrobbles WHERE status = 'accepted' AND accepted_at >= datetime('now','-1 day')").fetchone()[0]
            room = daily_cap - sent_today
            if room <= 0:
                self._set_job(job_id, "rate_limited", f"Daily cap of {daily_cap} reached; resuming automatically later.",
                              int(self.now()) + 3600)
                return "rate_limited"
            batch = unique[i:i + min(BATCH_MAX, room)]
            ids = [r["id"] for r in batch]
            marks = ",".join("?" * len(ids))
            with db.tx(con):
                con.execute(f"""UPDATE scrobbles SET status = 'submitting', attempts = attempts + 1,
                                submitted_at = datetime('now') WHERE id IN ({marks})""", ids)
            payload = [{"artist": r["artist"], "track": r["track"], "timestamp": r["timestamp"], "album": r["album"],
                        "duration": r["duration_s"]} for r in batch]
            summary = json.dumps([f"{p['artist']} - {p['track']} @ {p['timestamp']}" for p in payload], ensure_ascii=False)
            try:
                results = self.client.scrobble(payload, session_key)
            except (http.NetworkError, MalformedResponse) as err:
                # Last.fm may or may not have recorded these: never resend blindly.
                with db.tx(con):
                    con.execute(f"UPDATE scrobbles SET status = 'uncertain', last_error = ? WHERE id IN ({marks})",
                                (str(err), *ids))
                db.log_api("lastfm", "track.scrobble", job_id=job_id, request=summary, error=str(err))
                self.sleep(min(60, 5 * (attempt + 1)))
                attempt += 1
                if attempt > max_retries:
                    self._set_job(job_id, "needs_review", "Repeated uncertain submissions. Cuelog will check your "
                                  "Last.fm history again before retrying.", int(self.now()) + 900)
                    return "needs_review"
                try:
                    self.reconcile(job_id, username)
                except (LastfmError, http.NetworkError, http.HttpError, MalformedResponse) as err2:
                    return self._backoff(job_id, err2, attempt, max_retries)
                # re-read what still needs sending
                unique = unique[:i] + [dict(r) for r in con.execute(
                    f"SELECT * FROM scrobbles WHERE id IN ({marks}) AND status = 'queued' ORDER BY timestamp", ids)] + unique[i + len(batch):]
                continue
            except LastfmError as err:
                with db.tx(con):
                    con.execute(f"UPDATE scrobbles SET status = 'queued', last_error = ? WHERE id IN ({marks})",
                                (err.friendly(), *ids))
                db.log_api("lastfm", "track.scrobble", job_id=job_id, request=summary, error=str(err))
                if err.retryable:
                    attempt += 1
                    if attempt <= max_retries:
                        self.sleep(min(300, 2 ** attempt * 2))
                        continue
                return self._backoff(job_id, err, attempt, max_retries)
            except ValueError as err:
                with db.tx(con):
                    con.execute(f"UPDATE scrobbles SET status = 'failed', last_error = ? WHERE id IN ({marks})", (str(err), *ids))
                i += len(batch)
                continue
            attempt = 0
            daily_limit = False
            with db.tx(con):
                for r, res in zip(batch, results):
                    if res.accepted:
                        con.execute("""UPDATE scrobbles SET status = 'accepted', accepted_at = datetime('now'),
                                       ignored_code = 0, ignored_message = NULL, last_error = NULL WHERE id = ?""", (r["id"],))
                    elif res.code == 5:
                        daily_limit = True
                        con.execute("""UPDATE scrobbles SET status = 'queued', ignored_code = 5, ignored_message = ?
                                       WHERE id = ?""", (res.message, r["id"]))
                    elif res.code in (3, 4):
                        con.execute("""UPDATE scrobbles SET status = 'expired', ignored_code = ?, ignored_message = ?
                                       WHERE id = ?""", (res.code, res.message, r["id"]))
                    else:
                        con.execute("""UPDATE scrobbles SET status = 'ignored', ignored_code = ?, ignored_message = ?
                                       WHERE id = ?""", (res.code, res.message, r["id"]))
                db.log_api("lastfm", "track.scrobble", job_id=job_id, request=summary, http_status=200,
                           response=json.dumps([res.__dict__ for res in results], ensure_ascii=False))
            self.on_progress(JobProgress(job_id, "uploading", f"Sent {min(i + len(batch), len(unique))} of {len(unique)}",
                                         job_counts(job_id)))
            if daily_limit:
                self._set_job(job_id, "rate_limited", "Last.fm's daily scrobble limit was reached. "
                              "Cuelog will resume automatically in a few hours.", int(self.now()) + 6 * 3600)
                return "rate_limited"
            i += len(batch)
            if i < len(unique):
                self.sleep(delay)
        return self._finish(job_id)

    def _backoff(self, job_id: int, err: Exception, attempt: int, max_retries: int) -> str:
        msg = err.friendly() if isinstance(err, LastfmError) else str(err)
        if isinstance(err, LastfmError) and err.auth_problem:
            self._set_job(job_id, "failed", msg)
            return "failed"
        transient = isinstance(err, (http.NetworkError, http.HttpError, MalformedResponse)) or (
            isinstance(err, LastfmError) and (err.retryable or err.code is None))
        if transient:
            self._set_job(job_id, "rate_limited", msg + " Retrying automatically.", int(self.now()) + 60 * min(30, 2 ** attempt))
            return "rate_limited"
        self._set_job(job_id, "failed", msg)
        return "failed"

    def _finish(self, job_id: int, cancelled: bool = False) -> str:
        c = job_counts(job_id)
        if not cancelled and c["raw"].get("queued") and not self._cancelled(job_id):
            # live scrobbles were appended while this run was busy: go round again
            self._set_job(job_id, "queued", "More scrobbles waiting")
            return "queued"
        if cancelled and not c["uncertain"]:
            db.get().execute("UPDATE scrobbles SET status = 'pending', job_id = NULL WHERE job_id = ? AND status = 'queued'", (job_id,))
            self._set_job(job_id, "cancelled", "Cancelled")
            return "cancelled"
        if c["uncertain"]:
            self._set_job(job_id, "needs_review", "Some submissions could not be confirmed yet.", int(self.now()) + 900)
            return "needs_review"
        elif c["rejected"] and c["accepted"]:
            status, msg = "partial", f"{c['accepted']} accepted, {c['rejected']} rejected"
        elif c["rejected"]:
            status, msg = "needs_review", f"{c['rejected']} scrobbles were rejected"
        else:
            status, msg = "completed", f"{c['accepted']} accepted" + (f", {c['skipped']} skipped as duplicates" if c["skipped"] else "")
        self._set_job(job_id, status, msg)
        return status
