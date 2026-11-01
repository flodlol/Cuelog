"""Legacy CSV schemas from the original command-line importer, and migration into SQLite.

Two incompatible layouts of ``tracks.csv`` exist in the wild, which caused
``KeyError: 'number'`` in ``importer.tracks_load()``:

* **tracklist v1** (what importer.py expected)::

      number,artist,title,album,spotify_url,mbid,start_seconds

* **plan export v2** (what tracks.csv was later overwritten with: one row per
  planned scrobble, 25 tracks x 97 sessions)::

      play_id,session,track_number,local_time,unix_timestamp,artist,title,album,
      spotify_url,spotify_artists,match_notes,segment_duration_seconds,timestamp_source,status

* **preview** (``preview.csv`` written by ``prepare``)::

      session,number,local_time,unix_timestamp,artist,title,album,spotify_url,status

Column names are matched case-insensitively with aliases, and every schema is
validated with a descriptive error instead of a bare KeyError.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

POSITION_ALIASES = ("number", "track_number", "position", "#", "no", "track_no")
ALIASES = {
    "position": POSITION_ALIASES,
    "artist": ("artist", "artist_name"),
    "title": ("title", "track", "track_title", "name"),
    "album": ("album",),
    "spotify_url": ("spotify_url", "spotify", "spotify_link"),
    "spotify_artists": ("spotify_artists",),
    "mbid": ("mbid",),
    "start_seconds": ("start_seconds", "start", "offset_seconds"),
    "match_notes": ("match_notes", "notes"),
    "session": ("session",),
    "timestamp": ("unix_timestamp", "timestamp"),
    "status": ("status",),
    "play_id": ("play_id", "id"),
    "segment_duration": ("segment_duration_seconds", "duration"),
    "timestamp_source": ("timestamp_source",),
}


class LegacySchemaError(ValueError):
    pass


@dataclass
class LegacyTable:
    schema: str                       # tracklist | plan_export | preview
    path: Path
    rows: list[dict] = field(default_factory=list)   # normalised keys (see ALIASES)


def _resolve_columns(fieldnames: list[str] | None, path: Path) -> dict[str, str]:
    if not fieldnames:
        raise LegacySchemaError(f"{path.name} has no header row")
    lower = {f.strip().lstrip("﻿").lower(): f for f in fieldnames}
    out = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in lower:
                out[key] = lower[n]
                break
    return out


def detect_schema(cols: dict[str, str]) -> str:
    if "play_id" in cols and "session" in cols and "timestamp" in cols:
        return "plan_export"
    if "session" in cols and "timestamp" in cols:
        return "preview"
    return "tracklist"


def read_table(path: Path) -> LegacyTable:
    if not path.exists():
        raise LegacySchemaError(f"{path} does not exist")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = _resolve_columns(reader.fieldnames, path)
        raw_rows = list(reader)
    schema = detect_schema(cols)
    missing = [k for k in ("artist", "title") if k not in cols]
    if schema != "tracklist" and "position" not in cols:
        missing.append("number/track_number")
    if missing:
        raise LegacySchemaError(
            f"{path.name} is missing required column(s): {', '.join(missing)}. "
            f"Found columns: {', '.join(reader.fieldnames or [])}. "
            "Expected a tracklist (number,artist,title,...) or a plan export (play_id,session,track_number,...).")
    rows = []
    for line_no, r in enumerate(raw_rows, start=2):
        row = {k: (r.get(c) or "").strip() for k, c in cols.items()}
        row["_line"] = line_no
        rows.append(row)
    if not rows:
        raise LegacySchemaError(f"{path.name} contains no data rows")
    return LegacyTable(schema, path, rows)


def _int(value: str, what: str, line: int, path: Path) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise LegacySchemaError(f"{path.name} line {line}: {what} must be a whole number, got {value!r}") from None


def tracklist_rows(table: LegacyTable) -> list[dict]:
    """Unique ordered tracks from any legacy schema, validated."""
    path = table.path
    if table.schema == "tracklist":
        rows = table.rows
        if "position" in rows[0]:
            nums = [_int(r["position"], "number", r["_line"], path) for r in rows]
            if nums != list(range(1, len(rows) + 1)):
                raise LegacySchemaError(f"{path.name}: track numbers must be sequential starting at 1 (got {nums[:6]}...)")
        else:
            for i, r in enumerate(rows, 1):
                r["position"] = str(i)
    else:
        sessions: dict[int, list[dict]] = {}
        for r in table.rows:
            sessions.setdefault(_int(r["session"], "session", r["_line"], path), []).append(r)
        first = min(sessions)
        rows = sorted(sessions[first], key=lambda r: _int(r["position"], "track number", r["_line"], path))
        signature = [(r["artist"], r["title"]) for r in rows]
        for s, items in sessions.items():
            items = sorted(items, key=lambda r: int(r["position"]))
            if [(r["artist"], r["title"]) for r in items] != signature:
                raise LegacySchemaError(f"{path.name}: session {s} has a different tracklist than session {first}")
    for r in rows:
        if not r["artist"] or not r["title"]:
            raise LegacySchemaError(f"{path.name} line {r['_line']}: every song needs an artist and a title")
    starts = [r.get("start_seconds", "") for r in rows]
    if any(starts) and not all(starts):
        raise LegacySchemaError(f"{path.name}: either fill start_seconds for ALL tracks or leave ALL blank")
    return [{
        "number": int(r["position"]),
        "artist": r["artist"],
        "title": r["title"],
        "album": r.get("album", ""),
        "spotify_url": r.get("spotify_url", ""),
        "spotify_artists": r.get("spotify_artists", ""),
        "mbid": r.get("mbid", ""),
        "start_seconds": r.get("start_seconds", ""),
        "match_notes": r.get("match_notes", ""),
    } for r in rows]


def session_rows(table: LegacyTable) -> dict[int, list[dict]]:
    if table.schema == "tracklist":
        return {}
    out: dict[int, list[dict]] = {}
    for r in table.rows:
        out.setdefault(int(r["session"]), []).append({
            "position": int(r["position"]), "timestamp": _int(r["timestamp"], "unix_timestamp", r["_line"], table.path),
            "status": r.get("status") or "pending", "artist": r["artist"], "title": r["title"],
            "segment": int(r["segment_duration"]) if r.get("segment_duration", "").isdigit() else None,
        })
    for v in out.values():
        v.sort(key=lambda x: x["position"])
    return out


# ---- migration into the database ------------------------------------------------------------
@dataclass
class MigrationReport:
    set_id: int | None = None
    tracks: int = 0
    songs: int = 0
    sessions: int = 0
    scrobbles: int = 0
    accepted: int = 0
    account: str | None = None
    notes: list[str] = field(default_factory=list)


def migrate(root: Path, *, force: bool = False) -> MigrationReport:
    """Import the original project's files once. Never submits anything."""
    from . import db, repo, scheduler
    from .lastfm import LastfmClient
    from .matching import Candidate, query_key
    from .urls import spotify_track_id

    report = MigrationReport()
    con = db.get()
    if db.setting("legacy_migrated") and not force:
        report.notes.append("Already migrated")
        return report

    cfg_path = root / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

    # Existing Last.fm session (auth_session.json)
    auth = root / "auth_session.json"
    if auth.exists() and not LastfmClient.account():
        try:
            data = json.loads(auth.read_text(encoding="utf-8"))
            if data.get("name") and data.get("key"):
                LastfmClient.store_session(data["name"], data["key"])
                report.account = data["name"]
        except (ValueError, OSError) as err:
            report.notes.append(f"Could not read auth_session.json: {err}")

    tracks_csv = root / "tracks.csv"
    if not tracks_csv.exists():
        db.set_setting("legacy_migrated", True)
        report.notes.append("No tracks.csv found")
        return report
    table = read_table(tracks_csv)
    tracks = tracklist_rows(table)
    sessions = session_rows(table)

    # The original SoundCloud tracklist text is preserved from preview.csv/plan.json when available.
    original_lines: dict[int, tuple[str, str]] = {}
    preview = root / "preview.csv"
    if preview.exists():
        try:
            for r in tracklist_rows(read_table(preview)):
                original_lines[r["number"]] = (r["artist"], r["title"])
        except LegacySchemaError as err:
            report.notes.append(f"preview.csv skipped: {err}")

    source_url = cfg.get("source_url") or None
    title = cfg.get("mix_title") or "Imported set"
    duration = int(cfg.get("mix_duration_seconds") or 0) or None
    legacy_ref = "legacy:" + (source_url or title)
    if con.execute("SELECT 1 FROM sets WHERE legacy_ref = ?", (legacy_ref,)).fetchone():
        db.set_setting("legacy_migrated", True)
        report.notes.append("Set already present")
        return report

    raw_text = "\n".join(f"{n:02d}. {a} - {t}" for n, (a, t) in sorted(original_lines.items())) or \
        "\n".join(f"{t['number']:02d}. {t['artist']} - {t['title']}" for t in tracks)
    with db.tx(con):
        set_id = repo.create_set(title=title, source_url=source_url, duration_s=duration, tracklist_raw=raw_text,
                                 raw_meta={"legacy_config": cfg}, legacy_ref=legacy_ref, con=con)
        if cfg.get("notes"):
            con.execute("UPDATE sets SET notes = ? WHERE id = ?", (cfg["notes"], set_id))
        report.set_id = set_id
        inputs = []
        for t in tracks:
            artist, title_ = original_lines.get(t["number"], (t["artist"], t["title"]))
            sid = spotify_track_id(t["spotify_url"])
            artists = [a.strip() for a in t["spotify_artists"].split(";") if a.strip()] or [t["artist"]]
            if artists[0] != t["artist"]:
                report.notes.append(f"Track {t['number']}: first Spotify artist '{artists[0]}' used instead of '{t['artist']}'")
            if sid:
                song = Candidate("spotify", t["title"], artists, t["album"], spotify_id=sid,
                                 url=f"https://open.spotify.com/track/{sid}", mbid=t["mbid"] or None)
                status, note = "confirmed", t["match_notes"] or "Imported from tracks.csv"
            else:
                song = Candidate("manual", t["title"], artists, t["album"], mbid=t["mbid"] or None)
                status = "manual"
                note = (t["match_notes"] or "Imported from tracks.csv") + " · no Spotify track ID"
            song_id = repo.upsert_song(song, con=con)
            report.songs += 1
            start = int(t["start_seconds"]) if t["start_seconds"] else None
            inputs.append(repo.TrackInput(f"{artist} - {title_}", artist, title_, start,
                                          "manual" if start is not None else None, sid, song_id, status, 1.0, note))
        repo.replace_tracklist(set_id, inputs)
        # Remember these verified mappings for future sets.
        for row in repo.tracks_for_set(set_id):
            if row["song_id"]:
                con.execute("INSERT OR IGNORE INTO match_cache(query_key, song_id) VALUES (?, ?)",
                            (query_key(row["parsed_artist"], row["parsed_title"]), row["song_id"]))
        report.tracks = len(inputs)

        # Sessions: reconstructed plan, NOT submitted. Status is preserved exactly.
        if sessions:
            track_rows = repo.tracks_for_set(set_id)
            segs = {s.position: s for s in scheduler.segments_from_rows(track_rows, duration or 0)}
            for n in sorted(sessions):
                items = sessions[n]
                start = items[0]["timestamp"] - segs[items[0]["position"]].start_s
                planned = [scheduler.PlannedScrobble(segs[i["position"]], i["timestamp"]) for i in items]
                sess_id = repo._insert_session(
                    set_id, "legacy", planned, start, start + (duration or 0), reconstructed=True,
                    note="Migrated from the original importer plan: reconstructed timestamps, "
                         "listen count assumed from the SoundCloud play counter", legacy_ref=f"{legacy_ref}#session-{n}", con=con)
                for i in items:
                    status = {"accepted": "accepted", "duplicate_existing": "duplicate"}.get(i["status"], "pending")
                    if status != "pending":
                        con.execute("""UPDATE scrobbles SET status = ? WHERE session_id = ? AND
                                       set_track_id = (SELECT id FROM set_tracks WHERE set_id = ? AND position = ?)""",
                                    (status, sess_id, set_id, i["position"]))
                    report.accepted += status == "accepted"
                report.sessions += 1
                report.scrobbles += len(planned)
        db.set_setting("legacy_migrated", True, con=con)
    return report
