"""Last.fm submission engine with a mocked API: acceptance, rejection, limits, duplicates and recovery."""
import json

import pytest

from cuelog import db, http, jobs, lastfm, repo
from cuelog.config import Credentials
from cuelog.lastfm import LastfmClient, MalformedResponse, parse_scrobble_response, signature
from conftest import make_set

CREDS = Credentials(lastfm_key="k", lastfm_secret="s")


class FakeLastfm:
    """Simulates Last.fm: records accepted scrobbles and serves them from user.getRecentTracks."""

    def __init__(self):
        self.history: list[tuple[str, str, int]] = []
        self.scrobble_calls = 0
        self.script: list = []        # per track.scrobble call: None (normal), "timeout_after", "timeout_before", "malformed", dict codes
        self.ignore_codes: dict[str, int] = {}

    def __call__(self, method, params, post):
        if method == "user.getRecentTracks":
            lo, hi = int(params["from"]), int(params["to"])
            items = [{"artist": {"#text": a}, "name": t, "date": {"uts": str(ts)}} for a, t, ts in self.history if lo <= ts <= hi]
            return 200, json.dumps({"recenttracks": {"track": items, "@attr": {"totalPages": "1"}}})
        if method == "track.scrobble":
            assert post and params["sk"] == "sessionkey" and params["api_sig"] == signature(
                {k: v for k, v in params.items() if k not in ("api_sig", "format")}, "s")
            self.scrobble_calls += 1
            action = self.script.pop(0) if self.script else None
            n = len([k for k in params if k.startswith("artist[")])
            if action == "timeout_before":
                raise http.NetworkError("timed out")
            results = []
            for i in range(n):
                code = self.ignore_codes.get(params[f"track[{i}]"], 0)
                if code == 0:
                    self.history.append((params[f"artist[{i}]"], params[f"track[{i}]"], int(params[f"timestamp[{i}]"])))
                results.append({"artist": {"#text": params[f"artist[{i}]"]}, "track": {"#text": params[f"track[{i}]"]},
                                "ignoredMessage": {"code": str(code), "#text": ""}})
            if action == "timeout_after":
                raise http.NetworkError("timed out after the server stored them")
            if action == "malformed":
                return 200, "<html>gateway error</html>"
            if isinstance(action, dict) and "error" in action:
                raise http.HttpError("HTTP 500", 500, json.dumps(action))
            body = {"scrobbles": {"scrobble": results[0] if n == 1 else results,
                                  "@attr": {"accepted": sum(1 for r in results if r["ignoredMessage"]["code"] == "0")}}}
            return 200, json.dumps(body)
        if method == "user.getInfo":
            return 200, json.dumps({"user": {"name": "tester", "playcount": "10"}})
        raise AssertionError(method)


@pytest.fixture
def fake(tmpdb):
    f = FakeLastfm()
    LastfmClient.store_session("tester", "sessionkey")
    db.set_setting("batch_delay_seconds", 0)
    return f


def runner(fake, **kw):
    return jobs.JobRunner(LastfmClient(CREDS, transport=fake), sleep=lambda s: None, **kw)


def statuses(job):
    return jobs.job_counts(job)["raw"]


def test_signature_ascii_order():
    import hashlib
    params = {"api_key": "key", "method": "track.scrobble", "artist[1]": "B", "artist[10]": "A", "format": "json"}
    assert signature(params, "secret") == hashlib.md5(b"api_keykeyartist[10]Aartist[1]Bmethodtrack.scrobblesecret").hexdigest()


def test_response_parsing():
    one = {"scrobbles": {"scrobble": {"ignoredMessage": {"code": "0"}}, "@attr": {"accepted": 1}}}
    assert parse_scrobble_response(one, 1)[0].accepted
    many = {"scrobbles": {"scrobble": [{"ignoredMessage": {"code": "0"}}, {"ignoredMessage": {"code": "3", "#text": "old"}}]}}
    r = parse_scrobble_response(many, 2)
    assert [x.accepted for x in r] == [True, False] and r[1].code == 3
    with pytest.raises(MalformedResponse):
        parse_scrobble_response(many, 3)
    with pytest.raises(MalformedResponse):
        parse_scrobble_response({"nope": 1}, 1)


def test_accepted_and_rejected_items_are_checked_individually(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    fake.ignore_codes = {"Song 3": 1}
    job = jobs.create_job(session_ids=sess)
    assert runner(fake).run(job) == "partial"
    st = statuses(job)
    assert st == {"accepted": 4, "ignored": 1}
    assert repo.get_session(sess[0])["status"] == "partial"
    log = db.get().execute("SELECT * FROM api_log WHERE method = 'track.scrobble'").fetchall()
    assert len(log) == 1 and "Song 3" in log[0]["request"]


def test_batches_of_fifty_and_artist_sent_is_first_credit(fake, now):
    sid = make_set(tracks=25, duration=3800)
    sess = repo.create_listen(sid, now - 9000, plays=2, now=now)
    job = jobs.create_job(session_ids=sess)
    assert runner(fake).run(job) == "completed"
    assert fake.scrobble_calls == 1 and len(fake.history) == 50
    assert {a for a, _, _ in fake.history} == {f"Artist {i}" for i in range(1, 26)}      # never "Artist 1, Featured Guy"


def test_retry_is_idempotent_no_double_submission(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    job = jobs.create_job(session_ids=sess)
    runner(fake).run(job)
    assert jobs.create_job(session_ids=sess) is None            # nothing left to queue
    jobs.retry_job(job)
    runner(fake).run(job)
    assert fake.scrobble_calls == 1 and len(fake.history) == 5


def test_timeout_after_server_stored_is_reconciled_not_resent(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    fake.script = ["timeout_after"]
    job = jobs.create_job(session_ids=sess)
    assert runner(fake).run(job) == "completed"
    assert fake.scrobble_calls == 1                               # reconciled via history, not resent
    assert len(fake.history) == 5 and statuses(job) == {"accepted": 5}


def test_timeout_before_server_stored_is_retried_once(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    fake.script = ["timeout_before"]
    job = jobs.create_job(session_ids=sess)
    assert runner(fake).run(job) == "completed"
    assert fake.scrobble_calls == 2 and len(fake.history) == 5


def test_malformed_response_is_treated_as_uncertain(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    fake.script = ["malformed"]
    job = jobs.create_job(session_ids=sess)
    assert runner(fake).run(job) == "completed"
    assert len(fake.history) == 5 and fake.scrobble_calls == 1


def test_interrupted_job_resumes_after_crash(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    job = jobs.create_job(session_ids=sess)
    # Simulate a crash: two rows were marked 'submitting' and Last.fm stored only one of them.
    rows = db.get().execute("SELECT * FROM scrobbles WHERE job_id = ? ORDER BY timestamp", (job,)).fetchall()
    db.get().execute("UPDATE scrobbles SET status = 'submitting' WHERE id IN (?, ?)", (rows[0]["id"], rows[1]["id"]))
    db.get().execute("UPDATE jobs SET status = 'uploading' WHERE id = ?", (job,))
    fake.history.append((rows[0]["artist"], rows[0]["track"], rows[0]["timestamp"]))
    assert jobs.next_runnable_job() == job
    assert runner(fake).run(job) == "completed"
    assert len(fake.history) == 5                               # the stored one was not sent again
    assert statuses(job) == {"accepted": 5}


def test_duplicates_in_lastfm_history_and_locally_are_skipped(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    first = repo.scrobbles_for_session(sess[0])[0]
    fake.history.append((first["artist"], first["track"], first["timestamp"] + 30))   # e.g. Spotify scrobbled it already
    job = jobs.create_job(session_ids=sess)
    runner(fake).run(job)
    assert statuses(job) == {"accepted": 4, "duplicate": 1}
    assert len(fake.history) == 5


def test_daily_limit_pauses_job(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    fake.ignore_codes = {"Song 4": 5, "Song 5": 5}
    job = jobs.create_job(session_ids=sess)
    assert runner(fake).run(job) == "rate_limited"
    st = statuses(job)
    assert st["accepted"] == 3 and st["queued"] == 2
    assert db.get().execute("SELECT next_attempt_at FROM jobs WHERE id = ?", (job,)).fetchone()[0] > now
    assert jobs.next_runnable_job(now) is None                  # waits until the limit resets


def test_self_imposed_daily_cap(fake, now):
    db.set_setting("daily_cap", 3)
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    job = jobs.create_job(session_ids=sess)
    assert runner(fake).run(job) == "rate_limited"
    assert statuses(job)["accepted"] == 3


def test_expired_timestamps_are_not_sent(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    job = jobs.create_job(session_ids=sess)
    db.get().execute("UPDATE scrobbles SET timestamp = timestamp - 20 * 86400 WHERE job_id = ?", (job,))
    assert runner(fake).run(job) == "needs_review"
    assert fake.scrobble_calls == 0 and statuses(job) == {"expired": 5}


def test_auth_error_fails_job_with_friendly_message(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    fake.script = [{"error": 9, "message": "Invalid session key"}]
    job = jobs.create_job(session_ids=sess)
    assert runner(fake).run(job) == "failed"
    msg = db.get().execute("SELECT message FROM jobs WHERE id = ?", (job,)).fetchone()[0]
    assert "Reconnect Last.fm" in msg
    assert statuses(job) == {"queued": 5}                       # nothing lost; retry after reconnecting


def test_rate_limit_error_backs_off_and_succeeds(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    fake.script = [{"error": 29, "message": "Rate limit exceeded"}]
    sleeps = []
    r = jobs.JobRunner(LastfmClient(CREDS, transport=fake), sleep=sleeps.append)
    job = jobs.create_job(session_ids=sess)
    assert r.run(job) == "completed"
    assert sleeps and sleeps[0] >= 2


def test_cancel_returns_unsent_to_pending(fake, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    job = jobs.create_job(session_ids=sess)
    jobs.cancel_job(job)
    assert statuses(job) == {}
    assert {r["status"] for r in repo.scrobbles_for_session(sess[0])} == {"pending"}
    assert db.get().execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "cancelled"


def test_not_connected(tmpdb, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)
    job = jobs.create_job(session_ids=sess)
    assert jobs.JobRunner(LastfmClient(CREDS, transport=FakeLastfm())).run(job) == "failed"


def test_lastfm_errors_classified():
    assert lastfm.LastfmError(29, "x").retryable and lastfm.LastfmError(9, "x").auth_problem
    assert "secret" in lastfm.LastfmError(13, "x").friendly()


def test_live_scrobbling_appends_to_one_job_without_duplicates(fake, now):
    from cuelog import scheduler
    sid = make_set()
    segs = repo.segments_for_set(sid)
    started = now - 1000
    live = repo.start_live_session(sid, started, scheduler.listen_span(segs), now=now)
    ids = repo.add_live_scrobbles(live, [scheduler.PlannedScrobble(segs[0], started)], now=now)
    job = jobs.create_job(scrobble_ids=ids, label="Live")
    assert runner(fake).run(job) == "completed"
    # the next song qualifies later; adding the first one again is ignored (idempotent)
    more = repo.add_live_scrobbles(live, [scheduler.PlannedScrobble(segs[0], started),
                                          scheduler.PlannedScrobble(segs[1], started + 300)], now=now)
    assert len(more) == 1
    jobs.append_to_job(job, more)
    assert jobs.next_runnable_job(now) == job
    assert runner(fake).run(job) == "completed"
    assert [t for _, t, _ in fake.history] == ["Song 1", "Song 2"]
    repo.finish_live_session(live, complete=False, now=now)
    s = repo.get_session(live)
    assert s["kind"] == "player" and s["partial"] == 1 and s["status"] == "completed"


def test_scrobbles_appended_during_a_run_are_not_stranded(fake, now):
    from cuelog import scheduler
    sid = make_set()
    segs = repo.segments_for_set(sid)
    live = repo.start_live_session(sid, now - 1000, 1500, now=now)
    ids = repo.add_live_scrobbles(live, [scheduler.PlannedScrobble(segs[0], now - 1000)], now=now)
    job = jobs.create_job(scrobble_ids=ids)
    late = []

    def on_progress(p):   # a new song qualifies while the batch is being sent
        if p.status == "uploading" and p.message.startswith("Sent") and not late:
            late.extend(repo.add_live_scrobbles(live, [scheduler.PlannedScrobble(segs[1], now - 700)], now=now))
            jobs.append_to_job(job, late)
    assert runner(fake, on_progress=on_progress).run(job) == "queued"
    assert runner(fake).run(job) == "completed"
    assert len(fake.history) == 2


def test_empty_live_session_is_removed(tmpdb, now):
    sid = make_set()
    live = repo.start_live_session(sid, now - 100, 1500, now=now)
    repo.finish_live_session(live, complete=False, now=now)
    assert repo.get_session(live) is None


def test_now_playing_and_profile_summary(fake):
    calls = []

    def transport(method, params, post):
        calls.append((method, post, "api_sig" in params))
        if method == "user.getInfo":
            return 200, json.dumps({"user": {"name": "tester", "playcount": "102821", "url": "https://www.last.fm/user/tester",
                                             "image": [{"size": "large", "#text": "https://img/a.png"}]}})
        if method == "user.getRecentTracks":
            return 200, json.dumps({"recenttracks": {"track": [{"artist": {"#text": "Ran-D"}, "name": "Zombie",
                                                                "@attr": {"nowplaying": "true"}, "image": []}]}})
        return 200, json.dumps({"nowplaying": {}})
    c = LastfmClient(CREDS, transport=transport)
    p = c.profile_summary("tester")
    assert p["playcount"] == 102821 and p["track"] == {"artist": "Ran-D", "name": "Zombie", "now": True, "uts": None, "art": ""}
    c.update_now_playing("Ran-D", "Zombie", "Album", 289)
    assert calls[-1] == ("track.updateNowPlaying", True, True)
