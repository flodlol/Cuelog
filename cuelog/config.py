"""Paths and API credentials.

Credentials live in ``settings.env`` next to the project (git-ignored), with
environment variables taking precedence. They are only ever read by the Python
process; nothing here is shipped to a web page.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FROZEN = bool(getattr(sys, "frozen", False))      # running as the packaged Cuelog.exe
# Everything personal lives in %LOCALAPPDATA%\Cuelog, so the installed app and a source checkout share it.
APP_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Cuelog"
DATA_DIR = Path(os.environ.get("CUELOG_DATA_DIR", APP_HOME / "data"))
ENV_FILE = Path(os.environ.get("CUELOG_ENV_FILE", APP_HOME / "settings.env"))

CREDENTIAL_KEYS = (
    "LASTFM_API_KEY", "LASTFM_API_SECRET",
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
    "SOUNDCLOUD_CLIENT_ID", "SOUNDCLOUD_CLIENT_SECRET", "SOUNDCLOUD_REDIRECT_URI",
)
DEFAULT_SOUNDCLOUD_REDIRECT = "http://127.0.0.1:8765/soundcloud/callback"
_KEY_RE = re.compile(r"^[A-Z0-9_]+$")


def migrate_old_location() -> bool:
    """Copy data/ and settings.env from a source checkout (where they used to live) on first start.

    Copies, never moves: the old files stay where they are as a backup."""
    old_data, old_env = ROOT / "data", ROOT / "settings.env"
    moved = False
    if "CUELOG_DATA_DIR" not in os.environ and not (DATA_DIR / "cuelog.db").exists() and (old_data / "cuelog.db").exists():
        try:
            shutil.copytree(old_data, DATA_DIR, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("*.lock", "*.lock.*", "cuelog.db", "cuelog.db-*"))
        except shutil.Error:
            pass          # a few browser-cache files in use; the rest is copied and the caches refill themselves
        import sqlite3
        src, dst = sqlite3.connect(old_data / "cuelog.db"), sqlite3.connect(DATA_DIR / "cuelog.db")
        try:
            src.backup(dst)       # consistent copy, WAL included, even while the old copy is open
        finally:
            dst.close()
            src.close()
        moved = True
    if "CUELOG_ENV_FILE" not in os.environ and not ENV_FILE.exists() and old_env.exists():
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_env, ENV_FILE)
        moved = True
    return moved


def data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def db_path() -> Path:
    return data_dir() / "cuelog.db"


def cache_dir() -> Path:
    p = data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def _usable(value: str | None) -> str:
    value = (value or "").strip()
    return "" if not value or value.upper().startswith("PASTE_") else value


@dataclass(frozen=True)
class Credentials:
    lastfm_key: str = ""
    lastfm_secret: str = ""
    spotify_id: str = ""
    spotify_secret: str = ""
    soundcloud_id: str = ""
    soundcloud_secret: str = ""
    soundcloud_redirect: str = DEFAULT_SOUNDCLOUD_REDIRECT

    @property
    def lastfm(self) -> bool:
        return bool(self.lastfm_key and self.lastfm_secret)

    @property
    def spotify(self) -> bool:
        return bool(self.spotify_id and self.spotify_secret)

    @property
    def soundcloud(self) -> bool:
        return bool(self.soundcloud_id and self.soundcloud_secret)


def load_credentials(env_file: Path | None = None) -> Credentials:
    values = _read_env_file(env_file or ENV_FILE)
    for k in CREDENTIAL_KEYS:
        if os.environ.get(k):
            values[k] = os.environ[k]
    return Credentials(
        lastfm_key=_usable(values.get("LASTFM_API_KEY")),
        lastfm_secret=_usable(values.get("LASTFM_API_SECRET")),
        spotify_id=_usable(values.get("SPOTIFY_CLIENT_ID")),
        spotify_secret=_usable(values.get("SPOTIFY_CLIENT_SECRET")),
        soundcloud_id=_usable(values.get("SOUNDCLOUD_CLIENT_ID")),
        soundcloud_secret=_usable(values.get("SOUNDCLOUD_CLIENT_SECRET")),
        soundcloud_redirect=_usable(values.get("SOUNDCLOUD_REDIRECT_URI")) or DEFAULT_SOUNDCLOUD_REDIRECT,
    )


def save_credentials(updates: dict[str, str], env_file: Path | None = None) -> None:
    """Merge ``updates`` into the env file, preserving comments and unknown keys."""
    path = env_file or ENV_FILE
    for k, v in updates.items():
        if not _KEY_RE.match(k) or k not in CREDENTIAL_KEYS:
            raise ValueError(f"Unknown credential key: {k}")
        if any(c in v for c in "\r\n"):
            raise ValueError(f"{k} must be a single line")
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            out.append(f"{key}={updates[key].strip()}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v.strip()}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)
