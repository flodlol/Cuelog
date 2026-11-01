<div align="center">
  <h1>Cuelog</h1>
  <p>A Windows desktop app that turns the SoundCloud DJ sets you listen to into individual Last.fm scrobbles,<br> plus a full Last.fm stats dashboard for music enthusiasts.</p>

[![Status: WIP](https://img.shields.io/badge/Status-Work_in_progress-orange?style=flat-square)](#status) [![Download](https://img.shields.io/github/v/release/flodlol/Cuelog?include_prereleases&label=Download&style=flat-square&color=ff5a1f)](https://github.com/flodlol/Cuelog/releases/latest) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE) [![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/) [![Qt](https://img.shields.io/badge/PySide6-Qt_6-41CD52?style=flat-square&logo=qt&logoColor=white)](https://doc.qt.io/qtforpython-6/) [![Platform](https://img.shields.io/badge/Platform-Windows_10%2F11-0078D6?style=flat-square&logo=windows&logoColor=white)](#getting-started) [![Local first](https://img.shields.io/badge/100%25-Local-blue?style=flat-square)](#privacy)

</div>

> [!WARNING]
> **Work in progress.** Cuelog works and I use it every day, but it's still changing a lot. Expect rough edges
> and changes between versions.

---

## What is this?

If you listen to DJ sets on SoundCloud (Defqon.1, Q-dance, festival recordings, radio shows…), Last.fm normally
sees nothing, or one hour-long "track". Cuelog fixes that: save a set once, match its tracklist to the real
recordings, and every listen becomes the **individual songs** in your Last.fm history, with the right timestamps.

On top of that it's a proper stats app for your Last.fm account: top artists and albums, scrobbles per day,
listening clock, weekly and monthly reports, and top tags, all from your full Last.fm history.

<br>

<div align="center">
  <img src="docs/screenshot.png" alt="Cuelog home screen" width="900">
</div>

<br>

---

## Why This Exists

A lot of what I listen to is DJ sets, and none of it showed up on Last.fm. My stats were missing the music I
actually listen to most.

- **SoundCloud scrobblers?** They scrobble the whole mix as one track, if anything at all.
- **Manually scrobbling a tracklist?** Fine once, painful for a set you play 100 times.
- **Web tools?** I didn't want another browser tab or a server holding my Last.fm session.

So I built a desktop app that does it properly and stays on my PC.

---

## Features

- **Paste a SoundCloud link** — Normal, share, mobile and `on.soundcloud.com` links all work
- **Tracklist parsing** — Pulls the tracklist from the description (numbered, timestamped or plain `Artist - Title`) or lets you paste/edit it
- **Smart matching** — Matches every song to its official Spotify recording (or MusicBrainz without Spotify), version-aware, so a different remix is never auto-picked
- **Timeline** — See and drag where each song starts in the mix; missing timestamps are estimated and labelled
- **Log another listen** — One click for every repeat listen, with a preview of the exact scrobbles
- **Backfill** — Log past listens in bulk, clearly marked as reconstructed timestamps
- **Built-in player** — Listen in Cuelog's player; only the parts you actually played are scrobbled
- **SoundCloud detection** — Notices a set playing in the SoundCloud app or browser: scrobbles it live if it's in your library, otherwise offers to add it
- **Safe Last.fm import queue** — Batching, rate limits, a daily cap, crash recovery and duplicate prevention
- **Last.fm stats** — Home dashboard with top artists/albums/tracks, scrobbles per day and recent tracks
- **Reports** — Per year: weekly scrobbles vs last year, listening clock, top tags stream graph, scrobbles per month, top artists
- **Colour schemes & zoom** — Pick an accent and background; `Ctrl +` / `Ctrl −` to scale the whole UI
- **Exports & backups** — CSV/JSON exports and database backup/restore

---

## Getting Started

### Install

1. Download **`Cuelog-Setup-x.y.z.exe`** from the [latest release](https://github.com/flodlol/Cuelog/releases/latest)
2. Run it. It installs for your Windows account only (no admin needed) and adds Cuelog to the Start menu
3. Open Cuelog and follow the setup check

> [!NOTE]
> The installer isn't code-signed yet, so Windows SmartScreen may warn about an unknown publisher.
> Click **More info → Run anyway**.

Prefer no installer? Download **`Cuelog-x.y.z-portable.zip`**, unzip it anywhere and run `Cuelog.exe`.

You need:

- Windows 10 or 11 (64-bit)
- A Last.fm account and a (free) [Last.fm API account](https://www.last.fm/api/account/create)

### Run from source

Requires Python 3.10+ from [python.org](https://www.python.org/downloads/).

```bash
# Clone the repo
git clone https://github.com/flodlol/Cuelog.git
cd Cuelog

# Start (creates a private .venv on first run, takes about a minute)
start.bat
```

Or by hand:

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw -m cuelog
```

Add `--setup` to show the setup check again.

### Accounts

| Account | Required? | What it's for | Where |
|---|---|---|---|
| **Last.fm API account** | Yes | API key + shared secret | https://www.last.fm/api/account/create (any app name, no callback URL needed) |
| Last.fm login | Yes | Click **Connect Last.fm** in Settings and approve in the browser | – |
| Spotify app | Optional | Better matching, official metadata, artwork and artist photos (client credentials, no Spotify login) | https://developer.spotify.com/dashboard |
| SoundCloud app | Optional | Richer metadata, your playlists and likes | https://soundcloud.com/you/apps (reviewed by SoundCloud) |

Credentials go in **Settings** and are saved to `%LOCALAPPDATA%\Cuelog\settings.env` (see `.env.example`).
Session tokens are stored in the local database, encrypted with your Windows account (DPAPI).

---

## How It Works

The app is written in **Python** with **PySide6** (Qt 6 widgets). Everything is stored in a local **SQLite**
database; there's no server.

### Architecture

```
cuelog/
├── app.py, __main__.py   --> Entry point
├── db.py, repo.py        --> SQLite schema, migrations and data access
├── tracklist.py          --> Tracklist parsing
├── matching.py           --> Song matching (Spotify / MusicBrainz) and ranking
├── scheduler.py          --> Song timing within a mix, listens and backfill
├── jobs.py               --> Last.fm import engine (batches, retries, dedupe, recovery)
├── live.py, media.py     --> Live scrobbling and SoundCloud detection (Windows media sessions)
├── history.py            --> Local mirror of your Last.fm history for reports
├── lastfm.py, spotify.py, soundcloud.py, musicbrainz.py  --> API clients
├── player_proc.py        --> Player window (WebView2 + SoundCloud widget)
└── ui/                   --> Qt interface (pages, dialogs, charts, theme)
packaging/                --> Cuelog.exe (PyInstaller) and the installer (Inno Setup)
tests/                    --> pytest suite
docs/DATA_SCHEMA.md       --> Legacy CSV formats
```

### What Last.fm receives

- **Artist:** the **first** credited artist (e.g. *Headhunterz* for "Headhunterz, Wildstylez, Noisecontrollers"). All artists stay visible in Cuelog.
- **Title and album:** the official track title and album name.
- **Timestamp:** when that song started within your listen, based on the set's timeline.
- **Duration:** the recording's length (optional).

### Last.fm rules Cuelog respects

- Scrobbles older than **14 days** are ignored by Last.fm, so Cuelog uses 13 days and never sends future timestamps. Older listens get an explicitly labelled **reconstructed timestamp**.
- At most 50 scrobbles per request, a pause between requests, and a daily cap (default 2,800) below Last.fm's limit.
- **No double scrobbles:** every scrobble has a permanent identity, and after a timeout Cuelog checks your Last.fm history before retrying. Songs already scrobbled within ±90 s are skipped.
- Every request is logged (without secrets) in **Activity**.

### SoundCloud detection

Windows exposes what's playing through its media controls (the same thing your volume flyout shows). Cuelog reads
that every few seconds. When a long SoundCloud upload is playing, it compares the title, uploader and length with
your saved sets. If one matches, it follows along and scrobbles the songs as they play.

---

## Tech Stack

| Category | Technology |
|----------|------------|
| UI | PySide6 (Qt 6 Widgets), custom painted charts |
| Storage | SQLite |
| Player | pywebview + Microsoft Edge WebView2 |
| Media detection | WinRT `Windows.Media.Control` |
| APIs | Last.fm, Spotify, SoundCloud, MusicBrainz, Cover Art Archive, Deezer (images) |
| Packaging | PyInstaller, Inno Setup |
| Tests | pytest |
| Language | Python 3.10+ |

---

## Privacy

**Everything stays on your PC.**

- No Cuelog server, no accounts, no analytics
- Your listening data lives in a local SQLite database in `%LOCALAPPDATA%\Cuelog` (Settings → About → Open folder)
- API credentials stay in `settings.env` in that same folder; tokens are encrypted with Windows DPAPI
- Uninstalling Cuelog keeps your data; delete that folder to remove it too
- Cuelog only talks to the music APIs listed above

---

## Status

Cuelog is a **work in progress**. Things on the list:

- [x] Packaged `Cuelog.exe` with an installer, Start menu shortcut and its own data folder
- [ ] Code signing and automatic updates
- [ ] Better tracklist extraction for sets without a description
- [ ] More reports (artist and tag deep dives)
- [ ] Polishing, polishing, polishing

Known limitations:

- **Windows only** (WebView2 player, DPAPI, Windows media sessions).
- **SoundCloud has no listening-history API**, so only listens Cuelog sees (player, detection, or logged by you) count.
- **Last.fm** can't accept timestamps older than 14 days, so historical listens use reconstructed timestamps.

---

## Development

```bash
.venv\Scripts\python -m pytest tests
```

The tests cover tracklist and URL parsing, matching, timing, repeat listens and backfill, the import engine
against a simulated Last.fm (partial acceptance, limits, timeouts, crash recovery, duplicates), live scrobbling,
legacy migration, and a UI smoke test.

### Building the app

```bash
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

This builds `dist\Cuelog\Cuelog.exe`, a portable zip and `Cuelog-Setup-<version>.exe`. It needs
[Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`).

---

## Contributing

It's early, but issues and ideas are very welcome. Found a set that doesn't parse or match well? Open an issue
with the SoundCloud link.

1. Fork the repo
2. Create a branch (`git checkout -b my-feature`)
3. Make your changes and run the tests
4. Open a pull request

---

## License

MIT — do whatever you want with it.

---

<div align="center">
  If you find this useful, a star on GitHub would be nice. ⭐ <br/>
  Thanks for checking it out! ❤️
  <br/>
  <a href="https://github.com/sponsors/flodlol">Sponsor this project</a>
</div>
