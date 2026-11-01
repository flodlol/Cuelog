"""CSV / JSON export and full SQLite backups."""
from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import time
from pathlib import Path

from . import db

EXPORT_TABLES = ("sets", "sc_resources", "songs", "set_tracks", "match_cache", "sessions", "scrobbles", "jobs", "settings")
SCROBBLE_FIELDS = ["set_title", "session_uuid", "session_kind", "reconstructed", "local_time", "timestamp",
                   "original_timestamp", "artist", "track", "album", "duration_s", "status", "ignored_code",
                   "ignored_message", "spotify_url"]


def export_scrobbles_csv(path: Path) -> int:
    rows = db.get().execute(
        """SELECT s.title AS set_title, x.uuid AS session_uuid, x.kind AS session_kind, c.reconstructed,
                  datetime(c.timestamp, 'unixepoch', 'localtime') AS local_time, c.timestamp, c.original_timestamp,
                  c.artist, c.track, c.album, c.duration_s, c.status, c.ignored_code, c.ignored_message,
                  g.url AS spotify_url
           FROM scrobbles c JOIN sessions x ON x.id = c.session_id JOIN sets s ON s.id = x.set_id
           LEFT JOIN songs g ON g.id = c.song_id ORDER BY c.timestamp""").fetchall()
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCROBBLE_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(dict(r))
    return len(rows)


def export_set_csv(set_id: int, path: Path) -> int:
    from . import repo
    rows = repo.tracks_for_set(set_id)
    fields = ["position", "start_s", "start_source", "raw_line", "parsed_artist", "parsed_title", "match_status",
              "lastfm_artist", "spotify_title", "all_artists", "album", "spotify_url", "art_url"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({"position": r["position"], "start_s": r["start_s"], "start_source": r["start_source"],
                        "raw_line": r["raw_line"], "parsed_artist": r["parsed_artist"], "parsed_title": r["parsed_title"],
                        "match_status": r["match_status"], "lastfm_artist": r["lf_artist"], "spotify_title": r["lf_track"],
                        "all_artists": "; ".join(r["artists"]), "album": r["lf_album"], "spotify_url": r["song_url"] or "",
                        "art_url": r["art_url"] or ""})
    return len(rows)


def export_json(path: Path) -> dict:
    """Everything except account tokens."""
    con = db.get()
    out: dict = {"format": "cuelog-export", "schema_version": db.SCHEMA_VERSION,
                 "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    for t in EXPORT_TABLES:
        out[t] = [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
    out["accounts"] = [{"provider": r["provider"], "username": r["username"]}
                       for r in con.execute("SELECT provider, username FROM accounts")]
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {t: len(out[t]) for t in EXPORT_TABLES}


def backup(path: Path) -> Path:
    """Consistent online copy of the whole database (sqlite backup API)."""
    src = db.get()
    dst = sqlite3.connect(str(path))
    try:
        src.backup(dst)
    finally:
        dst.close()
    return path


def validate_backup(path: Path) -> dict:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        version = con.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("sets", "sessions", "scrobbles")
                  if t in tables}
        con.close()
    except sqlite3.DatabaseError as err:
        raise ValueError(f"Not a valid Cuelog backup: {err}") from err
    if not {"sets", "sessions", "scrobbles", "songs"} <= tables:
        raise ValueError("That file is a database, but not a Cuelog backup.")
    if version > db.SCHEMA_VERSION:
        raise ValueError("That backup was made by a newer version of Cuelog.")
    return {"version": version, **counts}


def restore(path: Path) -> Path:
    """Replace the live database with a backup. The current one is kept as a safety copy."""
    validate_backup(path)
    live = db.current_path()
    safety = live.with_name(f"cuelog-before-restore-{time.strftime('%Y%m%d-%H%M%S')}.db")
    backup(safety)
    db.close()
    for suffix in ("-wal", "-shm"):
        p = live.with_name(live.name + suffix)
        if p.exists():
            p.unlink()
    shutil.copyfile(path, live)
    db.get()   # reopen + migrate
    return safety
