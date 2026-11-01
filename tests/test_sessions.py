"""Timelines, repeat listens, timestamp boundaries, backfill and re-planning."""
import pytest

from cuelog import db, repo, scheduler
from cuelog.scheduler import TimestampError
from conftest import make_set


def test_estimate_offsets_respects_anchors_and_weights():
    assert scheduler.estimate_offsets(4, 400) == [0, 100, 200, 300]
    assert scheduler.estimate_offsets(4, 400, known={2: 300}) == [0, 150, 300, 350]
    assert scheduler.estimate_offsets(3, 600, weights=[1, 2, 3]) == [0, 100, 300]
    assert scheduler.timeline_problems([0, 100, 90], 400)


def test_repeated_song_appears_twice(tmpdb):
    sid = repo.create_set(title="S", duration_s=900)
    song = repo.manual_song("A", "Anthem")
    repo.replace_tracklist(sid, [repo.TrackInput("A - Anthem", "A", "Anthem", song_id=song, match_status="manual"),
                                 repo.TrackInput("B - Other", "B", "Other", song_id=repo.manual_song("B", "Other"), match_status="manual"),
                                 repo.TrackInput("A - Anthem", "A", "Anthem", song_id=song, match_status="manual")])
    segs = repo.segments_for_set(sid)
    assert [s.track for s in segs] == ["Anthem", "Other", "Anthem"]
    assert [s.start_s for s in segs] == [0, 300, 600]


def test_log_another_listen_creates_new_session_only(tmpdb, now):
    sid = make_set()
    first = repo.create_listen(sid, now - 86400, now=now)
    before = {r["id"]: r["timestamp"] for r in db.get().execute("SELECT id, timestamp FROM scrobbles")}
    second = repo.create_listen(sid, now - 3600 * 2, now=now)
    assert first != second and len(second) == 1
    after = {r["id"]: r["timestamp"] for r in db.get().execute("SELECT id, timestamp FROM scrobbles")}
    assert all(after[k] == v for k, v in before.items())          # old scrobbles untouched
    assert len(after) == 10
    s = repo.get_set(sid)
    assert s["listen_count"] == 2 and s["last_listened"] == now - 7200
    rows = repo.scrobbles_for_session(second[0])
    assert [r["timestamp"] - (now - 7200) for r in rows] == [0, 300, 600, 900, 1200]
    assert rows[0]["artist"] == "Artist 1" and rows[0]["duration_s"] == 240


def test_multiple_plays_partial_ranges(tmpdb, now):
    sid = make_set()
    ids = repo.create_listen(sid, now - 10000, plays=2, now=now)
    s1, s2 = (repo.get_session(i) for i in ids)
    assert s2["started_at"] == s1["ended_at"]
    part = repo.create_listen(sid, now - 2000, first=2, last=3, now=now)
    rows = repo.scrobbles_for_session(part[0])
    assert [r["track"] for r in rows] == ["Song 2", "Song 3"]
    assert [r["timestamp"] for r in rows] == [now - 2000, now - 1700]
    assert repo.get_session(part[0])["partial"] == 1


def test_future_and_too_old_boundaries(tmpdb, now):
    sid = make_set()
    with pytest.raises(TimestampError, match="future"):
        repo.preview_listen(sid, now - 100, now=now)       # 1500 s set would end in the future
    repo.preview_listen(sid, now - 1500, now=now)            # ends exactly now: allowed
    max_age = int(db.setting("max_age_days")) * 86400
    repo.preview_listen(sid, now - max_age, now=now)          # exactly at the limit: allowed
    with pytest.raises(TimestampError, match="last 13 days"):
        repo.preview_listen(sid, now - max_age - 1, now=now)
    pv = repo.preview_listen(sid, now - 40 * 86400, reconstruct=True, now=now)
    b = pv["blocks"][0]
    assert b["real_start"] == now - 40 * 86400
    assert now - max_age < b["start"] and b["end"] <= now
    ids = repo.create_listen(sid, now - 40 * 86400, reconstruct=True, now=now)
    sess = repo.get_session(ids[0])
    assert sess["reconstructed"] == 1 and sess["real_started_at"] == now - 40 * 86400
    first = repo.scrobbles_for_session(ids[0])[0]
    assert first["original_timestamp"] == now - 40 * 86400 and first["reconstructed"] == 1


def test_eligibility_function():
    assert scheduler.eligibility(1000, 1000, 13) is None
    assert scheduler.eligibility(1001, 1000, 13) == "future"
    assert scheduler.eligibility(1000 - 13 * 86400 - 1, 1000, 13) == "too_old"


def test_overlapping_real_listens_are_impossible(tmpdb, now):
    a, b = make_set(title="A"), make_set(title="B")
    repo.create_listen(a, now - 5000, now=now)
    with pytest.raises(TimestampError, match="Overlaps"):
        repo.create_listen(b, now - 4000, now=now)
    repo.create_listen(b, now - 3500, now=now)   # back-to-back is fine


def test_real_listen_displaces_unsubmitted_reconstructed_session(tmpdb, now):
    sid = make_set()
    backfilled = repo.create_backfill(sid, [now - 6000], now=now)[0]
    new = repo.create_listen(sid, now - 6500, now=now)[0]
    moved = repo.get_session(backfilled)
    assert not scheduler.overlaps(moved["started_at"], moved["ended_at"], now - 6500, now - 5000)
    assert repo.get_session(new)["started_at"] == now - 6500
    assert {r["original_timestamp"] for r in repo.scrobbles_for_session(backfilled)} != {None}


def test_backfill_distribution_is_non_overlapping_and_inside_window(tmpdb, now):
    sid = make_set(tracks=25, duration=3828)
    days = scheduler.window_days(now, 12.5)
    cap = scheduler.day_capacity(days, now=now, max_age_days=12.5, day_start="08:00", day_end="23:59")
    counts = scheduler.distribute_evenly(97, days, cap, 3828)
    assert sum(counts.values()) == 97
    assert all(counts[d] * 3828 <= cap[d] for d in days)
    starts = scheduler.backfill_starts(counts, 3828, now=now, max_age_days=12.5, day_start="08:00", day_end="23:59",
                                       busy=repo.busy_intervals())
    ids = repo.create_backfill(sid, starts, now=now)
    assert len(ids) == 97
    spans = sorted((s["started_at"], s["ended_at"]) for s in repo.list_sessions(sid))
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
    assert spans[0][0] >= now - 13 * 86400 and spans[-1][1] <= now
    assert all(s["reconstructed"] for s in repo.list_sessions(sid))
    with pytest.raises(TimestampError):
        scheduler.backfill_starts({days[-1]: 30}, 3828, now=now, max_age_days=12.5)


def test_replan_expired_sessions_keeps_original_time(tmpdb, now):
    sid = make_set()
    # A session planned 20 days ago (e.g. an old migrated plan) that was never submitted.
    old_start = now - 20 * 86400
    with db.tx():
        sess = repo._insert_session(sid, "legacy", scheduler.plan_listen(repo.segments_for_set(sid), old_start),
                                    old_start, old_start + 1500, reconstructed=True)
    assert repo.get_session(sess)["status"] == "expired"
    assert repo.replan_sessions([sess], now=now) == 1
    s = repo.get_session(sess)
    assert s["status"] == "draft" and now - 13 * 86400 < s["started_at"] and s["ended_at"] <= now
    rows = repo.scrobbles_for_session(sess)
    assert rows[0]["original_timestamp"] == old_start


def test_cancel_keeps_accepted(tmpdb, now):
    sid = make_set()
    sess = repo.create_listen(sid, now - 3000, now=now)[0]
    db.get().execute("UPDATE scrobbles SET status = 'accepted' WHERE id = (SELECT MIN(id) FROM scrobbles)")
    repo.cancel_session(sess)
    c = {r["status"] for r in repo.scrobbles_for_session(sess)}
    assert c == {"accepted", "skipped"}
    assert repo.get_session(sess)["cancelled"] == 0     # sessions with accepted scrobbles stay in history


def test_player_tracking_ignores_skipped_parts():
    from cuelog.ui.player import PlaybackTracker
    clock = [1000.0]
    t = PlaybackTracker(now=lambda: clock[0])
    t.play(0)
    for pos in range(1, 200):
        clock[0] += 1
        t.progress(pos)
    t.progress(900)            # user skipped ahead (jump without SEEK event)
    for pos in range(901, 1000):
        clock[0] += 1
        t.progress(pos)
    t.pause()
    heard = t.heard()
    assert heard == [(0, 199), (900, 999)]
    segs = [scheduler.Segment(i, i + 1, i * 300, (i + 1) * 300, f"A{i}", f"T{i}", "", 240, True) for i in range(4)]
    planned = scheduler.plan_from_player(segs, t.all_intervals(), 0.5)
    # Segment 0 (0-300): heard 199 s >= 150 -> counts. Segment 1 (300-600): nothing. Segment 3 (900-1200): 99 s < 150.
    assert [p.segment.position for p in planned] == [1]
    assert planned[0].timestamp == 1000
    # Re-listening to the same 100 s twice is still only 100 s heard.
    replay = [(300, 400, 5000), (300, 400, 5200)]
    assert scheduler.plan_from_player(segs, replay, 0.5) == []
    assert [p.segment.position for p in scheduler.plan_from_player(segs, replay, 0.3)] == [2]


def test_fast_dj_set_with_layered_songs_scrobbles_every_song(tmpdb, now):
    """A power-hour style set: 128 songs in 69 minutes, some starting at the same second."""
    from conftest import ROOT
    from cuelog import tracklist
    from cuelog.matching import Candidate
    es = tracklist.parse_tracklist((ROOT / "tests" / "fixtures" / "power_hour_2023.txt").read_text(encoding="utf-8"))
    sid = repo.create_set(title="POWER HOUR 2023", duration_s=69 * 60 + 30)
    items = []
    for e in es:
        song = repo.upsert_song(Candidate("manual", e.title, [e.artist or "Unknown"], ""))
        items.append(repo.TrackInput(e.raw_line, e.artist, e.title, e.start_s, "tracklist", song_id=song,
                                     match_status="manual"))
    repo.replace_tracklist(sid, items)
    segs = repo.segments_for_set(sid)
    assert len(segs) == 128 and all(s.scrobblable for s in segs)
    assert all(s.length > 0 for s in segs)
    # songs 17-20 start together at 08:04 and share the stretch until 08:17
    assert {(s.start_s, s.end_s) for s in segs[16:20]} == {(484, 497)}
    ids = repo.create_listen(sid, now - (69 * 60 + 30), now=now)
    rows = repo.scrobbles_for_session(ids[0])
    assert len(rows) == 128
    ts = [r["timestamp"] for r in rows]
    assert ts == sorted(ts) and len(set(ts)) == 128          # every scrobble has its own second
    assert max(ts) <= now
