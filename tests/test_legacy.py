"""Legacy CSV schemas, the KeyError: 'number' regression and the migration into SQLite."""
import csv
import shutil

import pytest

from cuelog import db, jobs, legacy, repo
from conftest import ROOT

PLAN_HEADER = ["play_id", "session", "track_number", "local_time", "unix_timestamp", "artist", "title", "album",
               "spotify_url", "spotify_artists", "match_notes", "segment_duration_seconds", "timestamp_source", "status"]


def write_csv(path, header, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_plan_export_schema_no_longer_raises_keyerror(tmp_path):
    p = tmp_path / "tracks.csv"
    write_csv(p, PLAN_HEADER, [
        ["session-0001-track-01", 1, 1, "", 100, "A", "One", "", "", "A", "", 150, "reconstructed", "pending"],
        ["session-0001-track-02", 1, 2, "", 250, "B", "Two", "", "", "B; C", "", 150, "reconstructed", "pending"],
        ["session-0002-track-01", 2, 1, "", 900, "A", "One", "", "", "A", "", 150, "reconstructed", "pending"],
        ["session-0002-track-02", 2, 2, "", 1050, "B", "Two", "", "", "B; C", "", 150, "reconstructed", "pending"],
    ])
    table = legacy.read_table(p)
    assert table.schema == "plan_export"
    rows = legacy.tracklist_rows(table)
    assert [r["number"] for r in rows] == [1, 2]
    assert rows[1]["spotify_artists"] == "B; C"
    sessions = legacy.session_rows(table)
    assert sorted(sessions) == [1, 2] and sessions[2][1]["timestamp"] == 1050


def test_original_tracklist_schema(tmp_path):
    p = tmp_path / "tracks.csv"
    write_csv(p, ["number", "artist", "title", "album", "spotify_url", "mbid", "start_seconds"],
              [[1, "A", "One", "", "", "", ""], [2, "B", "Two", "", "", "", ""]])
    t = legacy.read_table(p)
    assert t.schema == "tracklist"
    assert [r["title"] for r in legacy.tracklist_rows(t)] == ["One", "Two"]


def test_missing_column_gives_descriptive_error(tmp_path):
    p = tmp_path / "tracks.csv"
    write_csv(p, ["number", "name_of_artist", "title"], [[1, "A", "One"]])
    with pytest.raises(legacy.LegacySchemaError, match="missing required column.*artist"):
        legacy.read_table(p)


def test_non_sequential_numbers_and_bad_integers(tmp_path):
    p = tmp_path / "tracks.csv"
    write_csv(p, ["number", "artist", "title"], [[1, "A", "One"], [3, "B", "Two"]])
    with pytest.raises(legacy.LegacySchemaError, match="sequential"):
        legacy.tracklist_rows(legacy.read_table(p))
    write_csv(p, PLAN_HEADER, [["x", "one", 1, "", 100, "A", "One", "", "", "", "", "", "", "pending"]])
    with pytest.raises(legacy.LegacySchemaError, match="whole number"):
        legacy.tracklist_rows(legacy.read_table(p))


def test_inconsistent_sessions_rejected(tmp_path):
    p = tmp_path / "tracks.csv"
    write_csv(p, PLAN_HEADER, [
        ["a", 1, 1, "", 100, "A", "One", "", "", "", "", "", "", "pending"],
        ["b", 2, 1, "", 900, "Z", "Other", "", "", "", "", "", "", "pending"],
    ])
    with pytest.raises(legacy.LegacySchemaError, match="different tracklist"):
        legacy.tracklist_rows(legacy.read_table(p))


def _project_copy(tmp_path):
    proj = tmp_path / "project"
    proj.mkdir(exist_ok=True)
    for name in ("tracks.csv", "preview.csv", "config.json"):
        shutil.copy(ROOT / name, proj / name)
    return proj


def test_migrates_existing_project_without_submitting(tmpdb, tmp_path):
    report = legacy.migrate(_project_copy(tmp_path))
    assert report.tracks == 25 and report.sessions == 97 and report.scrobbles == 25 * 97 and report.accepted == 0
    s = repo.get_set(report.set_id)
    assert s["title"] == "Hardstyle Top 25 | DEDIQATED | 20 Years of Q-dance"
    assert s["duration_s"] == 3828 and s["listen_count"] == 97 and s["accepted_count"] == 0
    tracks = repo.tracks_for_set(report.set_id)
    # Original SoundCloud lines are preserved separately from the normalised Spotify metadata.
    t4 = tracks[3]
    assert t4["parsed_artist"] == "Headhunterz & Wildstylez vs. Noisecontrollers"
    assert t4["artists"] == ["Headhunterz", "Wildstylez", "Noisecontrollers"]
    assert t4["lf_artist"] == "Headhunterz"           # only the first credited artist
    assert t4["spotify_id"] == "3fWKWH355efWW24CzL70VM"
    # Two songs had no confirmed Spotify track ID: kept as manual metadata, no fabricated ID.
    no_id = [t for t in tracks if not t["spotify_id"]]
    assert {t["position"] for t in no_id} == {20, 23}
    assert all(t["match_status"] == "manual" for t in no_id)
    # Nothing queued or submitted.
    assert jobs.list_jobs() == []
    statuses = {r[0] for r in db.get().execute("SELECT DISTINCT status FROM scrobbles")}
    assert statuses == {"pending"}
    assert all(r["reconstructed"] for r in repo.list_sessions(report.set_id))
    # Idempotent.
    assert legacy.migrate(_project_copy(tmp_path)).notes == ["Already migrated"]
    assert db.get().execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 1


def test_migration_preserves_accepted_status(tmpdb, tmp_path):
    proj = _project_copy(tmp_path)
    rows = list(csv.reader((proj / "tracks.csv").open(encoding="utf-8-sig")))
    rows[1][-1] = "accepted"        # session 1, track 1 was accepted earlier
    with (proj / "tracks.csv").open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    report = legacy.migrate(proj)
    assert report.accepted == 1
    assert db.get().execute("SELECT COUNT(*) FROM scrobbles WHERE status = 'accepted'").fetchone()[0] == 1
