"""Spotify artist ordering, candidate ranking and ambiguity handling."""
from cuelog import repo, services, spotify
from cuelog.matching import Candidate, classify, is_ambiguous, rank, title_info


def sp(title, artists, album="Album", sid=None, date="2012-01-01", isrc=None):
    return Candidate("spotify", title, artists, album, spotify_id=sid or (title + "".join(artists))[:22].ljust(22, "x"),
                     release_date=date, isrc=isrc)


def test_spotify_track_keeps_credited_artist_order():
    raw = {"id": "3fWKWH355efWW24CzL70VM", "name": "Tonight - Alpha² Remix", "duration_ms": 356000,
           "artists": [{"name": "Headhunterz"}, {"name": "Wildstylez"}, {"name": "Noisecontrollers"}],
           "album": {"name": "Ohstyle 2012", "release_date": "2012-05-01",
                     "images": [{"url": "https://i.scdn.co/640", "width": 640}, {"url": "https://i.scdn.co/300", "width": 300}]},
           "external_urls": {"spotify": "https://open.spotify.com/track/3fWKWH355efWW24CzL70VM"},
           "external_ids": {"isrc": "NLA321200001"}}
    c = spotify.candidate_from_track(raw)
    assert c.artists == ["Headhunterz", "Wildstylez", "Noisecontrollers"]
    assert c.main_artist == "Headhunterz"
    assert c.art_url == "https://i.scdn.co/300" and c.art_source == "spotify"


def test_lastfm_fields_use_only_first_artist_and_spotify_title(tmpdb):
    sid = repo.create_set(title="S", duration_s=600)
    song = repo.upsert_song(sp("Tonight - Alpha² Remix", ["Headhunterz", "Wildstylez", "Noisecontrollers"], "Ohstyle 2012"))
    repo.replace_tracklist(sid, [repo.TrackInput("Headhunterz & Wildstylez vs. Noisecontrollers - Tonight (Alpha2 Remix)",
                                                 "Headhunterz & Wildstylez vs. Noisecontrollers", "Tonight (Alpha2 Remix)",
                                                 song_id=song, match_status="confirmed")])
    t = repo.tracks_for_set(sid)[0]
    assert (t["lf_artist"], t["lf_track"], t["lf_album"]) == ("Headhunterz", "Tonight - Alpha² Remix", "Ohstyle 2012")
    seg = repo.segments_for_set(sid)[0]
    assert seg.artist == "Headhunterz"


def test_title_info_versions():
    assert title_info("Tonight - Alpha² Remix").kind == "remix"
    assert title_info("Tonight - Alpha² Remix").version_text == "alpha2"
    assert title_info("Timeless - Radio Edit").kind == "radio"
    assert title_info("Scrap Attack (Defqon.1 Anthem 2009) - Original Mix").kind == "original"
    i = title_info("Live The Moment - Feat. Sarah Maria")
    assert i.base == "live the moment" and i.featured == ["sarah maria"] and i.kind is None


def test_does_not_pick_different_remix_or_radio_edit():
    cands = [sp("Her Voice - Radio Edit", ["Builder"]), sp("Her Voice - Psyko Punkz Remix", ["Builder"]),
             sp("Her Voice - Headhunterz Remix", ["Builder"])]
    ranked = rank("Builder", "Her Voice (Headhunterz Remix)", cands)
    assert ranked[0].candidate.title == "Her Voice - Headhunterz Remix"
    assert ranked[0].score >= 0.85
    plain = rank("Builder", "Her Voice", [sp("Her Voice - Headhunterz Remix", ["Builder"])])
    assert plain[0].score < 0.85 and any("remix" in r.lower() for r in plain[0].reasons)
    radio = rank("Wildstylez", "Timeless", [sp("Timeless - Radio Edit", ["Wildstylez"]), sp("Timeless", ["Wildstylez"])])
    assert radio[0].candidate.title == "Timeless"


def test_featured_artists_punctuation_unicode_and_case():
    r = rank("JDX ft. Sarah Maria", "Live The Moment", [sp("Live The Moment - Feat. Sarah Maria", ["JDX"])])
    assert r[0].score >= 0.85
    r = rank("D-Block & S-te-Fan", "MUSIC MADE ADDICT", [sp("Music Made Addict", ["D-Block & S-te-Fan"])])
    assert r[0].score >= 0.95
    r = rank("Headhunterz & Wildstylez vs. Noisecontrollers", "Tonight (Alpha2 Remix)",
             [sp("Tonight - Alpha² Remix", ["Headhunterz", "Wildstylez", "Noisecontrollers"])])
    assert r[0].score >= 0.85


def test_ambiguity_and_unmatched():
    ranked = rank("Ran-D", "Zombie", [sp("Zombie", ["Ran-D"], sid="a" * 22), sp("Zombie - Extended Mix", ["Ran-D"], sid="b" * 22)])
    assert classify(ranked)[0] == "auto"
    two_versions = rank("Ran-D", "Zombie", [sp("Zombie - Radio Edit", ["Ran-D"], sid="c" * 22),
                                            sp("Zombie - Extended Mix", ["Ran-D"], sid="d" * 22)])
    assert is_ambiguous(two_versions)
    assert classify(rank("Ran-D", "Zombie", [sp("Completely Different", ["Someone Else"])]))[0] == "unmatched"
    assert classify([])[0] == "unmatched"


def test_isrc_wins():
    r = rank("x", "y", [sp("Anything", ["Other"], isrc="ABC123")], isrc="abc123")
    assert r[0].score == 1.0


def test_match_service_caches_and_reuses_verified_matches(tmpdb):
    calls = []

    def fake_search(artist, title, limit=8):
        calls.append((artist, title))
        return [Candidate("musicbrainz", "Zombie", ["Ran-D"], "Zombie EP", mbid="m1")]

    svc = services.MatchService(spotify_client=type("S", (), {"configured": False})(), mb_search=fake_search)
    a = repo.create_set(title="A", duration_s=600)
    repo.replace_tracklist(a, [repo.TrackInput("Ran-D - Zombie", "Ran-D", "Zombie")])
    res = svc.match_set(a)
    assert res["auto"] == 1 and calls == [("Ran-D", "Zombie")]
    t = repo.tracks_for_set(a)[0]
    assert t["match_status"] == "auto" and t["candidates_json"]
    assert services.accept_high_confidence(a) == 1
    # A second set with the same line reuses the verified match without searching again.
    b = repo.create_set(title="B", duration_s=600)
    repo.replace_tracklist(b, [repo.TrackInput("Ran-D - Zombie", "Ran-D", "Zombie")])
    t2 = repo.tracks_for_set(b)[0]
    assert t2["match_status"] == "confirmed" and t2["song_id"] == t["song_id"]
    assert len(calls) == 1


def test_manual_correction_is_remembered_and_unmatched_not_scrobbled(tmpdb):
    sid = repo.create_set(title="A", duration_s=900)
    repo.replace_tracklist(sid, [repo.TrackInput("X - One", "X", "One"), repo.TrackInput("Y - Two", "Y", "Two"),
                                 repo.TrackInput("Z - Three", "Z", "Three")])
    t = repo.tracks_for_set(sid)
    song = repo.manual_song("X", "One (Edit)", "Album")
    repo.assign_match(t[0]["id"], song, "manual")
    segs = repo.segments_for_set(sid)
    assert [s.scrobblable for s in segs] == [True, False, False]
    assert repo.cached_match("X", "One") == song
    # Editing the tracklist keeps the existing mapping of unchanged songs.
    repo.replace_tracklist(sid, [repo.TrackInput("Z - Three", "Z", "Three"), repo.TrackInput("X - One", "X", "One")])
    t = repo.tracks_for_set(sid)
    assert t[1]["song_id"] == song and t[0]["song_id"] is None
