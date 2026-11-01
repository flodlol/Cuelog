"""SQLite storage: schema, migrations and settings.

Schema overview (see docs/DATA_SCHEMA.md for the full description):

    accounts          connected Last.fm / SoundCloud accounts (tokens sealed)
    sc_resources      raw metadata fetched from SoundCloud (never user-edited)
    sets              saved DJ sets (user-facing, editable)
    songs             canonical recordings (Spotify / MusicBrainz / manual)
    set_tracks        ordered tracklist entries of a set (+ timeline offsets)
    match_cache       verified tracklist-line -> song mappings, reused across sets
    sessions          listening sessions of a set
    scrobbles         individual planned/submitted scrobbles of a session
    jobs              Last.fm import jobs
    api_log           every Last.fm submission attempt + response
    settings          application settings
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA_VERSION = 2

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE accounts (
        provider     TEXT PRIMARY KEY,           -- 'lastfm' | 'soundcloud'
        username     TEXT NOT NULL,
        token        TEXT NOT NULL,              -- sealed with secretstore
        refresh      TEXT,
        expires_at   INTEGER,
        connected_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE sc_resources (
        id           INTEGER PRIMARY KEY,
        url          TEXT NOT NULL UNIQUE,
        kind         TEXT NOT NULL DEFAULT 'track',   -- track | playlist
        title        TEXT,
        uploader     TEXT,
        uploader_url TEXT,
        artwork_url  TEXT,
        duration_ms  INTEGER,
        description  TEXT,
        raw_json     TEXT,
        fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE sets (
        id            INTEGER PRIMARY KEY,
        uuid          TEXT NOT NULL UNIQUE,
        resource_id   INTEGER REFERENCES sc_resources(id) ON DELETE SET NULL,
        source_url    TEXT,
        title         TEXT NOT NULL,
        uploader      TEXT,
        artwork_url   TEXT,
        duration_s    INTEGER,
        description   TEXT,
        tracklist_raw TEXT,                 -- original tracklist text, untouched
        favorite      INTEGER NOT NULL DEFAULT 0,
        notes         TEXT,
        legacy_ref    TEXT UNIQUE,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE songs (
        id            INTEGER PRIMARY KEY,
        source        TEXT NOT NULL,         -- spotify | musicbrainz | manual
        spotify_id    TEXT UNIQUE,
        mbid          TEXT,
        isrc          TEXT,
        title         TEXT NOT NULL,
        artists_json  TEXT NOT NULL,         -- ordered credited artists
        album         TEXT,
        duration_ms   INTEGER,
        release_date  TEXT,
        url           TEXT,
        art_url       TEXT,
        art_source    TEXT,                  -- spotify | spotify-oembed | coverartarchive | none
        raw_json      TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX songs_mbid ON songs(mbid);
    CREATE TABLE set_tracks (
        id               INTEGER PRIMARY KEY,
        set_id           INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
        position         INTEGER NOT NULL,
        raw_line         TEXT NOT NULL DEFAULT '',
        parsed_artist    TEXT NOT NULL DEFAULT '',
        parsed_title     TEXT NOT NULL DEFAULT '',
        start_s          INTEGER,
        start_source     TEXT NOT NULL DEFAULT 'estimated',  -- tracklist | manual | estimated
        song_id          INTEGER REFERENCES songs(id) ON DELETE SET NULL,
        match_status     TEXT NOT NULL DEFAULT 'unmatched',
            -- unmatched | ambiguous | auto | confirmed | manual | no_match
        match_confidence REAL,
        match_note       TEXT,
        candidates_json  TEXT,
        UNIQUE(set_id, position)
    );
    CREATE TABLE match_cache (
        query_key   TEXT PRIMARY KEY,
        song_id     INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        verified    INTEGER NOT NULL DEFAULT 1,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE sessions (
        id               INTEGER PRIMARY KEY,
        uuid             TEXT NOT NULL UNIQUE,
        set_id           INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
        kind             TEXT NOT NULL,       -- listen | backfill | player | legacy
        started_at       INTEGER NOT NULL,    -- UTC epoch of the (possibly reconstructed) start
        ended_at         INTEGER NOT NULL,
        real_started_at  INTEGER,             -- known real start when reconstructed
        reconstructed    INTEGER NOT NULL DEFAULT 0,
        partial          INTEGER NOT NULL DEFAULT 0,
        cancelled        INTEGER NOT NULL DEFAULT 0,
        note             TEXT,
        legacy_ref       TEXT UNIQUE,
        created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX sessions_set ON sessions(set_id, started_at);
    CREATE TABLE scrobbles (
        id                 INTEGER PRIMARY KEY,
        uuid               TEXT NOT NULL UNIQUE,
        session_id         INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        set_track_id       INTEGER REFERENCES set_tracks(id) ON DELETE SET NULL,
        song_id            INTEGER REFERENCES songs(id) ON DELETE SET NULL,
        artist             TEXT NOT NULL,
        track              TEXT NOT NULL,
        album              TEXT,
        duration_s         INTEGER,
        timestamp          INTEGER NOT NULL,
        original_timestamp INTEGER,
        reconstructed      INTEGER NOT NULL DEFAULT 0,
        status             TEXT NOT NULL DEFAULT 'pending',
            -- pending | queued | submitting | uncertain | accepted | ignored
            -- | failed | duplicate | skipped | expired
        ignored_code       INTEGER,
        ignored_message    TEXT,
        attempts           INTEGER NOT NULL DEFAULT 0,
        last_error         TEXT,
        job_id             INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
        submitted_at       TEXT,
        accepted_at        TEXT
    );
    CREATE INDEX scrobbles_session ON scrobbles(session_id);
    CREATE INDEX scrobbles_status ON scrobbles(status);
    CREATE INDEX scrobbles_ts ON scrobbles(timestamp);
    CREATE TABLE jobs (
        id             INTEGER PRIMARY KEY,
        uuid           TEXT NOT NULL UNIQUE,
        status         TEXT NOT NULL DEFAULT 'queued',
            -- queued | uploading | completed | partial | rate_limited | failed | needs_review | cancelled
        label          TEXT,
        total          INTEGER NOT NULL DEFAULT 0,
        message        TEXT,
        next_attempt_at INTEGER,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at     TEXT NOT NULL DEFAULT (datetime('now')),
        started_at     TEXT,
        finished_at    TEXT
    );
    CREATE TABLE api_log (
        id           INTEGER PRIMARY KEY,
        job_id       INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
        provider     TEXT NOT NULL,
        method       TEXT NOT NULL,
        request      TEXT,
        http_status  INTEGER,
        response     TEXT,
        error        TEXT,
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    2: """
    -- Local mirror of the user's full Last.fm history (for reports), plus a small key/value cache.
    CREATE TABLE lf_scrobbles (
        uts     INTEGER NOT NULL,
        artist  TEXT NOT NULL,
        track   TEXT NOT NULL,
        album   TEXT,
        PRIMARY KEY (uts, artist, track)
    );
    CREATE INDEX lf_scrobbles_artist ON lf_scrobbles(artist);
    CREATE TABLE lf_cache (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        fetched_at INTEGER NOT NULL
    );
    """,
}

DEFAULT_SETTINGS: dict[str, object] = {
    "max_age_days": 13,             # Last.fm rejects scrobbles older than ~14 days
    "recent_buffer_minutes": 2,     # never schedule closer to "now" than this
    "min_segment_fraction": 0.5,    # share of a segment that must be heard (player sessions)
    "batch_delay_seconds": 2.0,     # pause between track.scrobble calls
    "daily_cap": 2800,              # self-imposed daily submission cap
    "max_retries": 5,
    "check_lastfm_history": True,   # compare with user.getRecentTracks before submitting
    "duplicate_tolerance_seconds": 90,
    "backfill_day_start": "10:00",
    "backfill_day_end": "23:30",
    "send_duration": True,
    "live_scrobble": True,
    "zoom": 1.0,
    "toast_sound": True,
    "accent": "ember",
    "base": "midnight",
    "detect_soundcloud": True,      # follow SoundCloud playing anywhere on this PC (Windows media controls)
    "sc_dismissed": [],             # SoundCloud uploads you said "never" to                    # Ctrl + / Ctrl - in the app          # in-app player: now playing + scrobble while listening
    "setup_complete": False,
    "legacy_migrated": False,
}

_local = threading.local()
_path_override: Path | None = None


def set_db_path(path: Path | None) -> None:
    """Point the app at another database (tests, restore). Closes cached connections."""
    global _path_override
    _path_override = path
    close()


def current_path() -> Path:
    return _path_override or config.db_path()


def connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or current_path()), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    migrate(con)
    return con


def get() -> sqlite3.Connection:
    """Per-thread connection to the current database."""
    con = getattr(_local, "con", None)
    path = str(current_path())
    if con is None or getattr(_local, "path", None) != path:
        con = connect()
        _local.con, _local.path = con, path
    return con


def close() -> None:
    con = getattr(_local, "con", None)
    if con is not None:
        con.close()
        _local.con = None


@contextmanager
def tx(con: sqlite3.Connection | None = None):
    con = con or get()
    if con.in_transaction:
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def migrate(con: sqlite3.Connection) -> int:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"Database schema v{version} is newer than this app (v{SCHEMA_VERSION}). Update Cuelog.")
    for v in range(version + 1, SCHEMA_VERSION + 1):
        con.executescript("BEGIN;" + MIGRATIONS[v] + f"; PRAGMA user_version = {v}; COMMIT;")
    return SCHEMA_VERSION


def setting(key: str, con: sqlite3.Connection | None = None):
    row = (con or get()).execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    return json.loads(row[0])


def all_settings(con: sqlite3.Connection | None = None) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for row in (con or get()).execute("SELECT key, value FROM settings"):
        out[row[0]] = json.loads(row[1])
    return out


def set_setting(key: str, value, con: sqlite3.Connection | None = None) -> None:
    (con or get()).execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)))


def log_api(provider: str, method: str, *, job_id=None, request=None, http_status=None, response=None,
            error=None, con: sqlite3.Connection | None = None) -> None:
    (con or get()).execute(
        "INSERT INTO api_log(job_id, provider, method, request, http_status, response, error) VALUES (?,?,?,?,?,?,?)",
        (job_id, provider, method, request, http_status, (response or "")[:20000] or None, error))
