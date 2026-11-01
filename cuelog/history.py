"""A local copy of your full Last.fm history, kept in sync, plus the queries behind the reports.

The first sync downloads everything (200 scrobbles per request, resumable); afterwards only new
scrobbles are fetched. All reports are then computed locally and instantly.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Callable

from . import db
from .lastfm import LastfmClient, LastfmError, _text

PAGE = 200


def _set(key, value):
    db.set_setting(key, value)


def _get(key, default=None):
    v = db.setting(key)
    return default if v is None else v


def status() -> dict:
    con = db.get()
    n = con.execute("SELECT COUNT(*) FROM lf_scrobbles").fetchone()[0]
    return {"count": n, "complete": bool(_get("lf_backfill_done", False)), "user": _get("lf_sync_user"),
            "pages": _get("lf_backfill_pages", 0), "page": _get("lf_backfill_page", 0)}


def _store(items: list[dict]) -> int:
    rows = []
    for t in items:
        if not isinstance(t, dict) or not isinstance(t.get("date"), dict):
            continue          # the "now playing" entry has no date
        rows.append((int(t["date"]["uts"]), _text(t.get("artist")), t.get("name", ""), _text(t.get("album"))))
    if not rows:
        return 0
    con = db.get()
    with db.tx(con):
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO lf_scrobbles(uts, artist, track, album) VALUES (?,?,?,?)", rows)
        return con.total_changes - before


def _page(client: LastfmClient, username: str, page: int, *, frm: int | None = None, to: int | None = None) -> tuple[list, int]:
    params = {"user": username, "limit": PAGE, "page": page}
    if frm:
        params["from"] = frm
    if to:
        params["to"] = to
    for attempt in range(4):
        try:
            data = client.call("user.getRecentTracks", params)
            rt = data.get("recenttracks") or {}
            items = rt.get("track") or []
            if isinstance(items, dict):
                items = [items]
            return items, int((rt.get("@attr") or {}).get("totalPages") or 1)
        except LastfmError as err:
            if not err.retryable or attempt == 3:
                raise
            time.sleep(2 ** attempt)
    return [], 0


def sync(client: LastfmClient, username: str, progress: Callable[[int, int], None] | None = None,
         stop: Callable[[], bool] = lambda: False, max_pages: int | None = None) -> dict:
    """Bring the local mirror up to date. Safe to interrupt and call again."""
    con = db.get()
    if _get("lf_sync_user") != username:
        with db.tx(con):
            con.execute("DELETE FROM lf_scrobbles")
            for k in ("lf_backfill_done", "lf_backfill_to", "lf_backfill_page", "lf_backfill_pages"):
                con.execute("DELETE FROM settings WHERE key = ?", (k,))
        _set("lf_sync_user", username)
    done_pages = 0
    # 1. new scrobbles since the newest one we have
    newest = con.execute("SELECT MAX(uts) FROM lf_scrobbles").fetchone()[0]
    if newest and _get("lf_backfill_done", False):
        page, total = 1, 1
        while page <= total and not stop():
            items, total = _page(client, username, page, frm=newest + 1)
            _store(items)
            page += 1
            done_pages += 1
            time.sleep(0.2)
    # 2. the full backfill (fixed upper bound so page numbers stay stable while we resume)
    if not _get("lf_backfill_done", False):
        to = _get("lf_backfill_to") or int(time.time())
        _set("lf_backfill_to", to)
        page = int(_get("lf_backfill_page", 0)) + 1
        total = int(_get("lf_backfill_pages", 0)) or page
        while page <= total and not stop():
            items, total = _page(client, username, page, to=to)
            _store(items)
            _set("lf_backfill_page", page)
            _set("lf_backfill_pages", total)
            if progress:
                progress(page, total)
            page += 1
            done_pages += 1
            if max_pages and done_pages >= max_pages:
                break
            time.sleep(0.2)
        if page > total:
            _set("lf_backfill_done", True)
            # catch anything scrobbled while the backfill ran
            if not stop():
                items, _ = _page(client, username, 1, frm=to + 1)
                _store(items)
    return status()


# ---- queries ---------------------------------------------------------------------------------
def years() -> list[int]:
    rows = db.get().execute("SELECT DISTINCT CAST(strftime('%Y', uts, 'unixepoch', 'localtime') AS INTEGER) "
                            "FROM lf_scrobbles ORDER BY 1 DESC").fetchall()
    return [r[0] for r in rows if r[0]]


def _bounds(year: int) -> tuple[int, int]:
    return (int(time.mktime(datetime(year, 1, 1).timetuple())), int(time.mktime(datetime(year + 1, 1, 1).timetuple())) - 1)


def weekday_counts(year: int) -> list[int]:
    a, b = _bounds(year)
    rows = dict(db.get().execute(
        """SELECT CAST(strftime('%w', uts, 'unixepoch', 'localtime') AS INTEGER), COUNT(*) FROM lf_scrobbles
           WHERE uts BETWEEN ? AND ? GROUP BY 1""", (a, b)).fetchall())
    return [rows.get((d + 1) % 7, 0) for d in range(7)]           # Monday first


def hour_counts(year: int) -> list[int]:
    a, b = _bounds(year)
    rows = dict(db.get().execute(
        """SELECT CAST(strftime('%H', uts, 'unixepoch', 'localtime') AS INTEGER), COUNT(*) FROM lf_scrobbles
           WHERE uts BETWEEN ? AND ? GROUP BY 1""", (a, b)).fetchall())
    return [rows.get(h, 0) for h in range(24)]


def month_counts(year: int) -> list[int]:
    a, b = _bounds(year)
    rows = dict(db.get().execute(
        """SELECT CAST(strftime('%m', uts, 'unixepoch', 'localtime') AS INTEGER), COUNT(*) FROM lf_scrobbles
           WHERE uts BETWEEN ? AND ? GROUP BY 1""", (a, b)).fetchall())
    return [rows.get(m, 0) for m in range(1, 13)]


def busiest_day(year: int) -> tuple[str, int] | None:
    a, b = _bounds(year)
    r = db.get().execute(
        """SELECT date(uts, 'unixepoch', 'localtime') AS d, COUNT(*) AS n FROM lf_scrobbles WHERE uts BETWEEN ? AND ?
           GROUP BY d ORDER BY n DESC LIMIT 1""", (a, b)).fetchone()
    return (r[0], r[1]) if r else None


def year_totals(year: int) -> dict:
    a, b = _bounds(year)
    con = db.get()
    one = lambda q: con.execute(q, (a, b)).fetchone()[0] or 0
    return {"scrobbles": one("SELECT COUNT(*) FROM lf_scrobbles WHERE uts BETWEEN ? AND ?"),
            "artists": one("SELECT COUNT(DISTINCT lower(artist)) FROM lf_scrobbles WHERE uts BETWEEN ? AND ?"),
            "tracks": one("SELECT COUNT(DISTINCT lower(artist) || '|' || lower(track)) FROM lf_scrobbles WHERE uts BETWEEN ? AND ?"),
            "days": one("SELECT COUNT(DISTINCT date(uts, 'unixepoch', 'localtime')) FROM lf_scrobbles WHERE uts BETWEEN ? AND ?")}


def top_artists(year: int, limit: int = 40) -> list[tuple[str, int]]:
    a, b = _bounds(year)
    return [(r[0], r[1]) for r in db.get().execute(
        """SELECT artist, COUNT(*) FROM lf_scrobbles WHERE uts BETWEEN ? AND ? GROUP BY lower(artist)
           ORDER BY 2 DESC LIMIT ?""", (a, b, limit))]


# ---- tags over time --------------------------------------------------------------------------
def _cache_get(key: str, max_age: int):
    r = db.get().execute("SELECT value, fetched_at FROM lf_cache WHERE key = ?", (key,)).fetchone()
    if r and time.time() - r[1] < max_age:
        return json.loads(r[0])
    return None


def _cache_put(key: str, value) -> None:
    db.get().execute("INSERT INTO lf_cache(key, value, fetched_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                     "value = excluded.value, fetched_at = excluded.fetched_at", (key, json.dumps(value), int(time.time())))


IGNORED_TAGS = {"seen live", "favorites", "favourite", "favorite", "albums i own", "under 2000 listeners", "spotify",
                "my favorite", "love", "awesome", "beautiful", "male vocalists", "female vocalists"}


def artist_tags(client: LastfmClient, artist: str) -> list[tuple[str, int]]:
    key = "tags:" + artist.lower()
    hit = _cache_get(key, 60 * 86400)
    if hit is not None:
        return [tuple(x) for x in hit]
    try:
        data = client.call("artist.getTopTags", {"artist": artist, "autocorrect": 1})
        tags = (data.get("toptags") or {}).get("tag") or []
        if isinstance(tags, dict):
            tags = [tags]
        out = [(t.get("name", "").lower(), int(t.get("count") or 0)) for t in tags
               if t.get("name") and t.get("name", "").lower() not in IGNORED_TAGS][:5]
    except LastfmError:
        out = []
    _cache_put(key, out)
    return out


def tags_by_month(client: LastfmClient, year: int, n_tags: int = 8, n_artists: int = 60) -> dict:
    """{'tags': [...], 'months': [[weight per tag] x 12]}: how much of each month went to each top tag."""
    a, b = _bounds(year)
    rows = db.get().execute(
        """SELECT CAST(strftime('%m', uts, 'unixepoch', 'localtime') AS INTEGER) AS m, lower(artist), MIN(artist), COUNT(*)
           FROM lf_scrobbles WHERE uts BETWEEN ? AND ? GROUP BY m, lower(artist)""", (a, b)).fetchall()
    per_artist = Counter()
    names = {}
    for m, low, name, n in rows:
        per_artist[low] += n
        names[low] = name
    top = [low for low, _ in per_artist.most_common(n_artists)]
    tag_map = {}
    for low in top:
        tags = artist_tags(client, names[low])
        total = sum(c for _, c in tags) or 1
        tag_map[low] = [(t, c / total) for t, c in tags]
    month_tag = defaultdict(Counter)
    for m, low, _name, n in rows:
        for t, w in tag_map.get(low, []):
            month_tag[m][t] += n * w
    overall = Counter()
    for c in month_tag.values():
        overall.update(c)
    tags = [t for t, _ in overall.most_common(n_tags)]
    return {"tags": tags, "months": [[month_tag[m].get(t, 0.0) for t in tags] for m in range(1, 13)]}
