# Cuelog data schema

## SQLite (`data/cuelog.db`)

The schema version is stored in `PRAGMA user_version`. Migrations live in `cuelog/db.py` (`MIGRATIONS`) and run
automatically at startup. A database from a newer Cuelog version is refused rather than modified.

| Table | Purpose | Key relationships |
|---|---|---|
| `settings` | App settings as JSON values (defaults in `db.DEFAULT_SETTINGS`) | – |
| `accounts` | Connected Last.fm / SoundCloud accounts. Tokens are sealed with Windows DPAPI | `provider` PK |
| `sc_resources` | Raw SoundCloud metadata as fetched (never edited by the user) | `url` unique |
| `sets` | Saved DJ sets: user-facing, editable metadata plus the **original tracklist text** (`tracklist_raw`) | → `sc_resources` |
| `songs` | Canonical recordings (Spotify, MusicBrainz or manual). `artists_json` keeps Spotify's **credited order** | `spotify_id` unique |
| `set_tracks` | Ordered entries of a set: original line, parsed artist/title, start offset + its source (`tracklist`/`manual`/`estimated`), match status/confidence/candidates | → `sets`, → `songs` |
| `match_cache` | Verified tracklist-line → song mappings, reused across sets | → `songs` |
| `sessions` | One row per listen of a set (`listen`, `player`, `backfill`, `legacy`). `reconstructed` marks generated times; `real_started_at` keeps a known real start | → `sets` |
| `scrobbles` | Individual planned/submitted scrobbles. Exact Last.fm fields (`artist` = first credited artist, `track`, `album`, `timestamp`), `original_timestamp` when re-planned, status, ignore code/message, attempts | → `sessions`, `set_tracks`, `songs`, `jobs` |
| `jobs` | Last.fm import jobs (queued, uploading, completed, partial, rate_limited, failed, needs_review, cancelled) | – |
| `api_log` | Every submission attempt and response (no secrets) | → `jobs` |

A set with 97 sessions stores its metadata once; sessions only reference it.

Scrobble statuses: `pending → queued → submitting → accepted | ignored | expired`. A timeout or malformed reply
leaves a scrobble `uncertain` until it's checked against `user.getRecentTracks`; it then becomes `accepted` or
goes back to `queued`. Other outcomes are `duplicate`, `skipped` (cancelled) and `failed`.

## Legacy CSV layouts (original command-line importer)

`cuelog/legacy.py` detects the layout from the header (case-insensitive, with aliases), validates every row, and
reports problems by file and line number.

**Tracklist v1**, the layout `importer.py` originally expected:

```
number,artist,title,album,spotify_url,mbid,start_seconds
```

`number` must run 1..n. Either all `start_seconds` are filled or none.

**Plan export v2**, the layout `tracks.csv` was later overwritten with (one row per planned scrobble). This caused
`KeyError: 'number'`:

```
play_id,session,track_number,local_time,unix_timestamp,artist,title,album,spotify_url,
spotify_artists,match_notes,segment_duration_seconds,timestamp_source,status
```

The unique tracklist is taken from the first session, and every session must contain the same tracks.
`spotify_artists` is `;`-separated in credited order.

**Preview**, written by `importer.py prepare`:

```
session,number,local_time,unix_timestamp,artist,title,album,spotify_url,status
```

Cuelog uses it to recover the original SoundCloud tracklist lines.

## Tracklist CSV (editor import/export)

```
position,start,artist,title,spotify_url,raw_line
```

`start` is `mm:ss`, `h:mm:ss` or seconds. A file with only a `line` column is parsed like pasted text.
