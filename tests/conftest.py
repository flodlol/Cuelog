import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Never touch the real data folder (database, caches) from tests.
os.environ["CUELOG_DATA_DIR"] = tempfile.mkdtemp(prefix="cuelog-tests-")

from cuelog import db, repo  # noqa: E402
from cuelog.matching import Candidate  # noqa: E402


@pytest.fixture
def tmpdb(tmp_path):
    path = tmp_path / "test.db"
    db.set_db_path(path)
    db.get()
    yield path
    db.set_db_path(None)


@pytest.fixture
def now():
    # A fixed "now" well inside a day, so window arithmetic is deterministic.
    return int(time.time()) // 60 * 60


def make_set(tracks: int = 5, duration: int = 1500, title: str = "Test Set", url: str | None = None) -> int:
    """A set with ``tracks`` confirmed Spotify-matched songs, each 300 s in the mix."""
    sid = repo.create_set(title=title, source_url=url, duration_s=duration)
    items = []
    for i in range(tracks):
        song = repo.upsert_song(Candidate("spotify", f"Song {i + 1}", [f"Artist {i + 1}", "Featured Guy"], f"Album {i + 1}",
                                          duration_ms=240000, spotify_id=f"{i + 1:022d}"))
        items.append(repo.TrackInput(f"Artist {i + 1} - Song {i + 1}", f"Artist {i + 1}", f"Song {i + 1}",
                                     i * (duration // tracks), "tracklist", song_id=song, match_status="confirmed"))
    repo.replace_tracklist(sid, items)
    return sid
