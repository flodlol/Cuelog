"""SoundCloud detection matching and live scrobbling while a set plays."""
from cuelog import db, jobs, media, repo
from cuelog.live import LiveSession
from conftest import make_set


def np(title, artist="", duration=0, app="com.richardhbtz.soundcloud-rpc", playing=True, pos=0):
    return media.NowPlaying(app, title, artist, pos, duration, playing)


def test_match_set_by_title_uploader_and_length(tmpdb):
    sets = [{"id": 1, "title": "Hardstyle Top 25 | DEDIQATED | 20 Years of Q-dance", "uploader": "Hörömpöli István",
             "duration_s": 3828},
            {"id": 2, "title": "POWER HOUR 2023 Defqon.1 Weekend Festival This Is Madness", "uploader": "M Gabriel",
             "duration_s": 4190}]
    assert media.match_set(np("Hardstyle Top 25 | DEDIQATED | 20 Years of Q-dance", "Hörömpöli István", 3828.9), sets)["id"] == 1
    assert media.match_set(np("POWER HOUR 2023 Defqon.1 Weekend Festival This Is Madness", duration=4190), sets)["id"] == 2
    # A different set from the same uploader is not mistaken for one in the library.
    assert media.match_set(np("POWER HOUR | DEDIQATED | 20 Years of Q-dance", "Hörömpöli István", 3664.5), sets) is None
    # Same title but a very different length: not the same upload.
    assert media.match_set(np("Hardstyle Top 25 | DEDIQATED | 20 Years of Q-dance", duration=900), sets) is None
    assert media.looks_like_a_set(np("Some mix", duration=3600))
    assert not media.looks_like_a_set(np("A song", duration=200))
    assert not media.looks_like_a_set(np("Some mix", duration=3600, app="SpotifyAB.SpotifyMusic"))


def test_live_session_scrobbles_heard_songs_once(tmpdb, now):
    sid = make_set()                         # 5 songs of 300 s
    clock = [float(now - 2000)]
    played = []
    live = LiveSession(sid, now=lambda: clock[0], now_playing=lambda seg: played.append(seg.position))
    for pos in range(0, 700, 3):             # listen to the first 700 s
        clock[0] += 3
        live.update(pos, True)
    assert live.done == {1, 2}               # song 3 has only just started
    live.update(700, False)                  # pause
    clock[0] += 3
    assert live.update(700, False) == 0      # nothing new while paused
    n = live.finish()
    assert live.done == {1, 2}               # 100 s of song 3 isn't enough (needs 150 s)
    assert played[:3] == [1, 2, 3] and n == 0
    rows = repo.scrobbles_for_session(live.session_id)
    assert [r["track"] for r in rows] == ["Song 1", "Song 2"]
    assert {r["status"] for r in rows} == {"queued"}
    assert jobs.job_counts(live.job_id)["pending"] == 2
    s = repo.get_session(live.session_id)
    assert s["kind"] == "player" and s["partial"] == 1


def test_live_session_skipping_ahead_does_not_count(tmpdb, now):
    sid = make_set()
    clock = [float(now - 2000)]
    live = LiveSession(sid, now=lambda: clock[0])
    for pos in range(0, 60, 3):
        clock[0] += 3
        live.update(pos, True)
    for pos in range(1200, 1500, 3):         # jumped straight to the last song
        clock[0] += 3
        live.update(pos, True)
    live.finish()
    assert live.done == {5}
    assert db.get().execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0] == 1
