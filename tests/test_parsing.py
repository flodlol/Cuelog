"""URL normalisation and tracklist parsing."""
import pytest

from cuelog import tracklist
from cuelog.urls import UrlError, parse_soundcloud, safe_external_url, spotify_track_id


@pytest.mark.parametrize("raw,expected,kind", [
    ("https://soundcloud.com/h-r-mp-li-istv-n/hardstyle-top-25-dediqated-20",
     "https://soundcloud.com/h-r-mp-li-istv-n/hardstyle-top-25-dediqated-20", "track"),
    ("soundcloud.com/qdance/some-mix?si=abc123&utm_source=clipboard&utm_medium=text",
     "https://soundcloud.com/qdance/some-mix", "track"),
    ("https://m.soundcloud.com/qdance/some-mix/", "https://soundcloud.com/qdance/some-mix", "track"),
    ("https://soundcloud.com/qdance/sets/defqon-1-2024", "https://soundcloud.com/qdance/sets/defqon-1-2024", "playlist"),
    ("https://soundcloud.com/qdance/private-mix/s-AbCdEf", "https://soundcloud.com/qdance/private-mix/s-AbCdEf", "track"),
    ("  https://soundcloud.com/qdance/some-mix#t=1:00  ", "https://soundcloud.com/qdance/some-mix", "track"),
])
def test_soundcloud_urls(raw, expected, kind):
    u = parse_soundcloud(raw)
    assert (u.url, u.kind) == (expected, kind)


def test_short_links_need_resolving():
    u = parse_soundcloud("https://on.soundcloud.com/AbC123xyz")
    assert u.kind == "short" and u.needs_resolve


@pytest.mark.parametrize("bad", [
    "", "https://evil.example.com/qdance/mix", "javascript:alert(1)", "ftp://soundcloud.com/a/b",
    "https://user:pw@soundcloud.com/a/b", "https://soundcloud.com:8443/a/b", "https://soundcloud.com/",
    "https://soundcloud.com/discover", "https://soundcloud.com.evil.com/a/b",
])
def test_rejects_unsafe_or_unsupported_urls(bad):
    with pytest.raises(UrlError):
        parse_soundcloud(bad)


def test_spotify_ids_and_external_url_allowlist():
    assert spotify_track_id("https://open.spotify.com/track/7rKfyMxpaa6p1CVSXxf5j3?si=x") == "7rKfyMxpaa6p1CVSXxf5j3"
    assert spotify_track_id("https://open.spotify.com/intl-de/track/7rKfyMxpaa6p1CVSXxf5j3") == "7rKfyMxpaa6p1CVSXxf5j3"
    assert spotify_track_id("spotify:track:7rKfyMxpaa6p1CVSXxf5j3") == "7rKfyMxpaa6p1CVSXxf5j3"
    assert spotify_track_id("https://open.spotify.com/album/7rKfyMxpaa6p1CVSXxf5j3") is None
    assert spotify_track_id("https://open.spotify.com.evil.io/track/7rKfyMxpaa6p1CVSXxf5j3") is None
    assert safe_external_url("https://open.spotify.com/track/x")
    assert safe_external_url("http://open.spotify.com/track/x") is None
    assert safe_external_url("https://evil.com") is None
    assert safe_external_url("file:///C:/Windows") is None


@pytest.mark.parametrize("line,artist,title,start", [
    ("01. JDX ft. Sarah Maria - Live The Moment", "JDX ft. Sarah Maria", "Live The Moment", None),
    ("02. D-Block & S-te-Fan - Music Made Addict", "D-Block & S-te-Fan", "Music Made Addict", None),
    ("00:00 JDX - Live The Moment", "JDX", "Live The Moment", 0),
    ("03:12 D-Block & S-te-Fan - Music Made Addict", "D-Block & S-te-Fan", "Music Made Addict", 192),
    ("[1:02:03] 2 Best Enemies - Phases (TBY Romantic Mix) [Q-dance Records]", "2 Best Enemies", "Phases (TBY Romantic Mix)", 3723),
    ("#3 Headhunterz \u2013 Tonight (Alpha\u00b2 Remix)", "Headhunterz", "Tonight (Alpha\u00b2 Remix)", None),
    ("3) 01:15 Ran-D - Zombie", "Ran-D", "Zombie", 75),
    ("Builder - Her Voice (Headhunterz Remix) 55:10", "Builder", "Her Voice (Headhunterz Remix)", 3310),
    ("\u2022 Showtek - FTS", "Showtek", "FTS", None),
    ("12. 2 Best Enemies - Phases", "2 Best Enemies", "Phases", None),
    ("Wildstylez - Timeless [Radio Edit]", "Wildstylez", "Timeless [Radio Edit]", None),
])
def test_parse_line(line, artist, title, start):
    e = tracklist.parse_line(line, 1)
    assert (e.artist, e.title, e.start_s) == (artist, title, start)
    assert e.raw_line == line


def test_parse_spotify_link_and_headers():
    e = tracklist.parse_line("https://open.spotify.com/track/7rKfyMxpaa6p1CVSXxf5j3", 1)
    assert e.spotify_id == "7rKfyMxpaa6p1CVSXxf5j3"
    assert tracklist.parse_line("Tracklist:") is None
    assert tracklist.parse_line("   ") is None
    assert tracklist.parse_line("no separator here").problems


def test_fifty_track_paste():
    text = "\n".join(f"{i:02d}. Artist {i} - Title {i}" for i in range(1, 51))
    entries = tracklist.parse_tracklist(text)
    assert len(entries) == 50 and entries[-1].title == "Title 50" and entries[-1].position == 50


def test_extract_from_description_ignores_prose():
    desc = """Thanks for listening! Follow me on insta.
Tracklist:
00:00 JDX - Live The Moment
03:12 D-Block & S-te-Fan - Music Made Addict
06:40 2 Best Enemies - Phases
More info - see my website http://x.example
"""
    entries = tracklist.extract_from_description(desc)
    assert [e.title for e in entries] == ["Live The Moment", "Music Made Addict", "Phases"]
    assert [e.start_s for e in entries] == [0, 192, 400]


def test_csv_roundtrip_and_validation():
    entries = tracklist.parse_tracklist("00:00 A - One\n02:00 B - Two\nhttps://open.spotify.com/track/7rKfyMxpaa6p1CVSXxf5j3")
    back = tracklist.entries_from_csv(tracklist.entries_to_csv(entries))
    assert [(e.artist, e.title, e.start_s, e.spotify_id) for e in back] == \
        [(e.artist, e.title, e.start_s, e.spotify_id) for e in entries]
    with pytest.raises(ValueError, match="artist"):
        tracklist.entries_from_csv("foo,bar\n1,2\n")
    bad = tracklist.parse_tracklist("05:00 A - One\n02:00 B - Two")
    assert tracklist.validate_timeline(bad, 3600)


def test_power_hour_tracklist_imports_cleanly():
    """128 songs in 69 minutes: bare '001' numbering, (hh:mm:ss) and (hh:mm:ss:ff) timecodes,
    several songs starting at the same second, and one line without an artist."""
    from conftest import ROOT
    text = (ROOT / "tests" / "fixtures" / "power_hour_2023.txt").read_text(encoding="utf-8")
    es = tracklist.parse_tracklist(text)
    assert len(es) == 128
    assert all(e.start_s is not None for e in es)
    assert not any(e.artist[:1].isdigit() and e.artist.split()[0].isdigit() and len(e.artist.split()[0]) == 3 for e in es)
    assert (es[0].artist, es[0].title, es[0].start_s) == ("The Power Hour Orchestra", "The Choreo of Chaos", 0)
    assert (es[17].artist, es[17].start_s) == ("Dany BPM", 484)              # layered with 017, 019, 020
    assert (es[37].title, es[37].start_s) == ("Wir sind wieder da (Snowbass 2022 Anthem)", 1008)   # 00:16:48:24
    assert (es[58].artist, es[58].title, es[58].start_s) == ("", "Frozen (Da Tweekaz Bootleg)", 1867)
    assert (es[102].artist, es[102].title) == ("Headhunterz", "Dragonborn")  # "Headhunterz- Dragonborn"
    assert es[66].artist == "3 Blokes & Villain"                             # a real leading digit stays
    assert tracklist.validate_timeline(es, 69 * 60 + 30) == []               # equal starts are allowed
    assert [p for e in es for p in e.problems] == ["No artist found: add one, or matching uses the title only"]


def test_bare_numbering_needs_a_real_sequence():
    # Only one numbered-looking line: the digit belongs to the artist.
    es = tracklist.parse_tracklist("2 Best Enemies - Phases\nShowtek - FTS\nRan-D - Zombie")
    assert es[0].artist == "2 Best Enemies"
    es = tracklist.parse_tracklist("1 Zatox - Winter\n2 TNT - Rhythm\n3 2 Best Enemies - Phases")
    assert [e.artist for e in es] == ["Zatox", "TNT", "2 Best Enemies"]
