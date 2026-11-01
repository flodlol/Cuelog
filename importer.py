#!/usr/bin/env python3
"""Dry-run-first SoundCloud DJ-set to Last.fm scrobble planner.

Only standard-library dependencies. The plan contains reconstructed (not historical)
timestamps. It is a manual, user-approved import rather than a listening-history exporter.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent
API = "https://ws.audioscrobbler.com/2.0/"
SCROBBLE_BATCH_MAX = 50
APP_USER_AGENT = "SoundCloudSetBackfill/1.0 (user-controlled import)"


def fail(msg):
    raise SystemExit("ERROR: " + msg)


def json_load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if path.name == "auth_session.json" and os.name != "nt":
        os.chmod(path, 0o600)


def configuration():
    c = json_load(ROOT / "config.json")
    if not isinstance(c["set_plays"], int) or not 1 <= c["set_plays"] <= 10000:
        fail("set_plays must be an integer between 1 and 10000")
    if not 1 <= c["window_days"] <= 13:
        fail("window_days must be 1-13 days (safer than Last.fm's ~14-day limit)")
    if c["mix_duration_seconds"] < 31:
        fail("mix_duration_seconds must be longer than 30 seconds")
    return c


def tracks_load():
    """Load the unique tracklist from tracks.csv.

    Accepts both the original tracklist layout (number,artist,title,...) and the
    plan-export layout (play_id,session,track_number,...) that tracks.csv was
    later overwritten with. See cuelog/legacy.py for the documented schemas.
    Previously this raised KeyError: 'number' on the plan-export layout.
    """
    from cuelog.legacy import LegacySchemaError, read_table, tracklist_rows
    try:
        rows = tracklist_rows(read_table(ROOT / "tracks.csv"))
    except LegacySchemaError as err:
        fail(str(err))
    positions = [r["start_seconds"].strip() for r in rows]
    return rows, bool(positions and all(positions))


def offsets_for(tracks, duration):
    known = all(t["start_seconds"].strip() for t in tracks)
    if known:
        offsets = [int(t["start_seconds"].strip()) for t in tracks]
    else:
        offsets = [round(i * duration / len(tracks)) for i in range(len(tracks))]
    if offsets[0] != 0 or offsets != sorted(set(offsets)) or offsets[-1] >= duration:
        fail("start_seconds must begin at 0, increase strictly and stay below mix duration")
    durations = [b - a for a, b in zip(offsets, offsets[1:] + [duration])]
    if min(durations) <= 30:
        fail("Each track segment must exceed 30 seconds; check positions")
    return offsets, durations, known


def fingerprint(config, tracks):
    payload = {"config": {k: config[k] for k in ("set_plays", "mix_duration_seconds", "window_days", "timezone")},
               "tracks": tracks}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def now_utc():
    return datetime.now(timezone.utc)


def plan_entries(c, rows, utc_now=None):
    utc_now = utc_now or now_utc()
    duration = c["mix_duration_seconds"]
    offsets, lengths, known = offsets_for(rows, duration)
    first_start = int((utc_now - timedelta(days=c["window_days"])).timestamp())
    final_finish = int((utc_now - timedelta(minutes=15)).timestamp())
    latest_start = final_finish - duration
    if c["set_plays"] == 1:
        session_starts = [latest_start]
    else:
        gap = (latest_start - first_start) / (c["set_plays"] - 1)
        if gap < duration:
            fail("Not enough time in the chosen window for non-overlapping full sets. Lower set_plays or increase window_days.")
        session_starts = [round(first_start + i * gap) for i in range(c["set_plays"])]
    out = []
    for session, start in enumerate(session_starts, 1):
        for pos, (r, offset, seconds) in enumerate(zip(rows, offsets, lengths), 1):
            out.append({
                "id": f"session-{session:04d}-track-{pos:02d}",
                "session": session,
                "number": pos,
                "timestamp": start + offset,
                "duration": seconds,
                "artist": r["artist"].strip(),
                "title": r["title"].strip(),
                "album": r["album"].strip(),
                "spotify_url": r["spotify_url"].strip(),
                "mbid": r["mbid"].strip(),
                "status": "pending",
                "lastfm_response": ""
            })
    return out, known


def local_text(timestamp, timezone_name):
    try:
        zone = ZoneInfo(timezone_name)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(zone).strftime("%Y-%m-%d %H:%M:%S %Z")
    except ZoneInfoNotFoundError:
        # Windows Python can lack an IANA tz database. Use the PC timezone for
        # display only; upload timestamps always remain accurate UTC epoch seconds.
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S local PC time")


def write_previews(plan, c):
    entries = plan["entries"]
    with (ROOT / "preview.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["session", "number", "local_time", "unix_timestamp", "artist", "title", "album", "spotify_url", "status"])
        w.writeheader()
        for e in entries:
            w.writerow({"session": e["session"], "number": e["number"], "local_time": local_text(e["timestamp"], c["timezone"]),
                        "unix_timestamp": e["timestamp"], "artist": e["artist"], "title": e["title"], "album": e["album"],
                        "spotify_url": e["spotify_url"], "status": e["status"]})
    summary = Counter(e["status"] for e in entries)
    unique = [e for e in entries if e["session"] == 1]
    track_rows = []
    for e in unique:
        artist = html.escape(e["artist"])
        title = html.escape(e["title"])
        query = urllib.parse.quote(e["artist"] + " " + e["title"])
        link = (html.escape(e["spotify_url"], quote=True) if e["spotify_url"].startswith("https://open.spotify.com/track/")
                else "https://open.spotify.com/search/" + query)
        link_label = "Pinned track" if e["spotify_url"].startswith("https://open.spotify.com/track/") else "Find on Spotify"
        track_rows.append(f'<tr><td>{e["number"]}</td><td>{artist}</td><td>{title}</td>'
                          f'<td><a href="{link}" target="_blank" rel="noopener noreferrer">{link_label}</a></td></tr>')
    sample = []
    for e in entries[:15]:
        sample.append(f'<tr><td>{e["session"]}</td><td>{html.escape(local_text(e["timestamp"], c["timezone"]))}</td>'
                      f'<td>{html.escape(e["artist"])} – {html.escape(e["title"])}</td><td>{html.escape(e["status"])}</td></tr>')
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SoundCloud to Last.fm · Import Preview</title><style>
:root{font-family:Inter,Segoe UI,Arial,sans-serif;color-scheme:dark}body{margin:0;background:#0c1017;color:#e8ecf3}
main{max-width:1040px;margin:50px auto;padding:0 22px}h1{font-size:32px;margin:10px 0}h2{font-size:20px;margin-top:38px}
p{line-height:1.65;color:#aeb9ca}.pill{display:inline-block;color:#b6caff;background:#1a2841;border:1px solid #33405d;padding:7px 12px;border-radius:20px;font-size:12px}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:30px 0}.card{background:#171e2a;border:1px solid #2b3646;padding:22px;border-radius:12px}
.card strong{display:block;font-size:31px;margin-bottom:7px}.card span{color:#aab6c5;font-size:13px}
.warn{border-left:3px solid #eab55f;background:#241f19;padding:16px 20px;border-radius:5px}
.wrap{overflow:auto;border:1px solid #2b3646;border-radius:12px}table{border-collapse:collapse;width:100%;font-size:13px}th{text-align:left;color:#9eabc0;background:#192333}th,td{padding:12px 14px;border-bottom:1px solid #283341}tr:last-child td{border:0}
a{color:#80b7ff}footer{margin:36px 0 60px;color:#8895a8;font-size:12px}
@media(max-width:600px){.cards{grid-template-columns:1fr}h1{font-size:24px}}</style></head><body><main>
<span class="pill">Local preview · Nothing uploaded</span><h1>SoundCloud → Last.fm</h1><p>__TITLE__</p>
<section class="cards"><div class="card"><strong>__SESSIONS__</strong><span>Assumed complete set sessions</span></div>
<div class="card"><strong>__TOTAL__</strong><span>Candidate individual scrobbles</span></div>
<div class="card"><strong>__PENDING__</strong><span>Still pending</span></div></section>
<div class="warn"><b>These are reconstructed timestamps, not your original SoundCloud listening dates.</b><p>
__OFFSETS__. The SoundCloud 97-play figure alone does not prove 97 complete listens. Review every track title and remix before importing. Last.fm does not store Spotify track IDs.</p></div>
<h2>Track metadata</h2><p>Each Spotify link opens a search unless you've entered a confirmed track URL in tracks.csv. These songs are <b>not automatically verified</b>.</p>
<div class="wrap"><table><thead><tr><th>#</th><th>Artist submitted to Last.fm</th><th>Title submitted</th><th>Spotify</th></tr></thead><tbody>__TRACKS__</tbody></table></div>
<h2>Preview of the first 15 scheduled entries</h2><p>All rows are saved in preview.csv. Session starts and song positions are spaced in a 12-day window (or as set in config.json).</p>
<div class="wrap"><table><thead><tr><th>Session</th><th>Local time</th><th>Track</th><th>Status</th></tr></thead><tbody>__SAMPLE__</tbody></table></div>
<footer>Generated locally. API upload is deliberately a separate command and requires explicit confirmation.</footer>
</main></body></html>'''
    repl = {"__TITLE__": html.escape(c["mix_title"]), "__SESSIONS__": str(c["set_plays"]), "__TOTAL__": format(len(entries), ","),
            "__PENDING__": format(summary["pending"], ","), "__OFFSETS__": ("Track start positions were provided" if plan["offsets_known"] else "Individual track start positions are evenly estimated"),
            "__TRACKS__": "".join(track_rows), "__SAMPLE__": "".join(sample)}
    for k, v in repl.items():
        page = page.replace(k, v)
    (ROOT / "preview.html").write_text(page, encoding="utf-8")


def save_plan(plan):
    json_save(ROOT / "plan.json", plan)
    write_previews(plan, configuration())


def load_plan():
    path = ROOT / "plan.json"
    if not path.exists():
        fail("No plan. Run 'python importer.py prepare' first.")
    return json_load(path)


def prepare(args):
    c = configuration()
    t, _ = tracks_load()
    path = ROOT / "plan.json"
    if path.exists() and not args.force:
        fail("A plan already exists. Use preview, or 'prepare --force' to replace it (ONLY if no plays were accepted).")
    if path.exists() and args.force:
        old = json_load(path)
        if any(e["status"] == "accepted" for e in old["entries"]):
            fail("Refusing to overwrite a plan with accepted scrobbles. Back it up and use 'rebase' instead.")
    entries, known = plan_entries(c, t)
    plan = {"created_at": now_utc().isoformat(), "fingerprint": fingerprint(c, t), "offsets_known": known, "entries": entries}
    save_plan(plan)
    print(f"Prepared {len(entries):,} candidate scrobbles ({c['set_plays']} sessions × {len(t)} tracks).")
    print(f"Timestamp range: {local_text(entries[0]['timestamp'], c['timezone'])} through {local_text(entries[-1]['timestamp'], c['timezone'])}")
    print("Saved preview.html (visual preview), preview.csv (all rows), and plan.json (pending/accepted state).")
    if not known:
        print("WARNING: Set-song positions are uniformly estimated. Enter start_seconds values in tracks.csv to improve them.")
    if args.open:
        webbrowser.open((ROOT / "preview.html").as_uri())


def ensure_plan_matches(plan):
    c = configuration()
    tracks, _ = tracks_load()
    if fingerprint(c, tracks) != plan["fingerprint"]:
        fail("tracks.csv or config.json has changed. Review changes before creating a new plan. Existing plan stays intact.")


def preview(args):
    plan = load_plan()
    save_plan(plan)
    count = Counter(e["status"] for e in plan["entries"])
    print("Status: " + ", ".join(f"{k}={v}" for k,v in sorted(count.items())))
    print("Open preview.html for the track mapping and preview.csv for all plays.")
    if args.open:
        webbrowser.open((ROOT / "preview.html").as_uri())


def credentials():
    creds = {}
    p = ROOT / "settings.env"
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip().strip('"').strip("'")
    creds.update({k: os.environ[k] for k in ("LASTFM_API_KEY", "LASTFM_API_SECRET") if os.environ.get(k)})
    key, secret = creds.get("LASTFM_API_KEY", ""), creds.get("LASTFM_API_SECRET", "")
    if not key or not secret or key.startswith("PASTE_") or secret.startswith("PASTE_"):
        fail("Copy .env.example to settings.env (or use Cuelog Settings) and enter your Last.fm API key + shared secret.")
    return key, secret


def signature(params, secret):
    text = "".join(str(k) + str(v) for k, v in sorted(params.items()) if k not in {"format", "callback", "api_sig"}) + secret
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def api_call(method, params, *, post=False, authenticated=False):
    key, secret = credentials()
    base = {"method": method, "api_key": key, **params}
    if authenticated:
        session = session_load()
        base["sk"] = session["key"]
    if authenticated or method.startswith("auth."):
        base["api_sig"] = signature(base, secret)
    base["format"] = "json"
    encoded = urllib.parse.urlencode(base).encode("utf-8")
    request = urllib.request.Request(API if post else API + "?" + encoded.decode(),
                                     data=encoded if post else None,
                                     headers={"User-Agent": APP_USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            data = json.load(response)
    except urllib.error.HTTPError as err:
        msg = err.read(500).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {err.code}: {msg[:350]}") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise RuntimeError(f"Network error: {err}") from err
    if "error" in data:
        raise RuntimeError(f"Last.fm error {data['error']}: {data.get('message', '')}")
    return data


def session_load():
    p = ROOT / "auth_session.json"
    if not p.exists():
        fail("Not logged in. Run 'python importer.py auth' first.")
    return json_load(p)


def auth(_args):
    token = api_call("auth.getToken", {})["token"]
    key, _ = credentials()
    url = "https://www.last.fm/api/auth/?" + urllib.parse.urlencode({"api_key": key, "token": token})
    print("Open this Last.fm authorization URL:", url)
    webbrowser.open(url)
    input("After pressing Allow on Last.fm, return here and press Enter: ")
    info = api_call("auth.getSession", {"token": token})["session"]
    json_save(ROOT / "auth_session.json", {"name": info["name"], "key": info["key"]})
    print("Authenticated as:", info["name"])


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").casefold().strip()
    return re.sub(r"\s+", " ", s)


def recent_scrobbles(username, earliest, latest):
    # API documented max is 200 per page.
    seen = defaultdict(list)
    page = 1
    while True:
        result = api_call("user.getRecentTracks", {"user": username, "from": earliest,
                      "to": latest, "limit": 200, "page": page})
        recents = result.get("recenttracks", {})
        attrs = recents.get("@attr", {})
        total = int(attrs.get("totalPages", "1"))
        if total > 200:
            fail("More than 200 pages of recent scrobbles. Refusing an incomplete duplicate check.")
        records = recents.get("track", [])
        if isinstance(records, dict):
            records = [records]
        for r in records:
            if "date" not in r:
                continue
            a = r.get("artist", "")
            a = a.get("#text", "") if isinstance(a, dict) else a
            name = r.get("name", "")
            seen[(norm(a), norm(name))].append(int(r["date"]["uts"]))
        if page >= total:
            break
        page += 1
        time.sleep(.18)
    for values in seen.values():
        values.sort()
    return seen


def is_duplicate(e, seen, tolerance=90):
    timestamps = seen.get((norm(e["artist"]), norm(e["title"])), [])
    return any(abs(s - e["timestamp"]) <= tolerance for s in timestamps)


def validate(_args):
    rows, _ = tracks_load()
    results = []
    for r in rows:
        try:
            v = api_call("track.getInfo", {"artist": r["artist"], "track": r["title"], "autocorrect": 0})
            t = v["track"]
            a = t.get("artist", {})
            artist = a.get("name", "") if isinstance(a, dict) else a
            result = {"number":r["number"], "artist":r["artist"], "title":r["title"],
                      "status":"found", "lastfm_artist":artist, "lastfm_title":t.get("name", ""), "lastfm_url":t.get("url", "")}
        except RuntimeError as err:
            if "error 6" in str(err) or "error 7" in str(err):
                result = {"number":r["number"], "artist":r["artist"], "title":r["title"],
                          "status":"not_found", "lastfm_artist":"", "lastfm_title":"", "lastfm_url":""}
            else:
                print("Validation stopped because of an API/network problem:", err)
                break
        results.append(result)
        print(f"{int(r['number']):2}. {result['status']:9} {r['artist']} – {r['title']}")
        time.sleep(.25)
    with (ROOT / "track_validation.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["number", "artist", "title", "status", "lastfm_artist", "lastfm_title", "lastfm_url"])
        writer.writeheader()
        writer.writerows(results)
    print("Saved track_validation.csv. Not found does not necessarily mean unscrobblable. It does NOT verify Spotify track IDs.")


def rebase(_args):
    plan = load_plan()
    ensure_plan_matches(plan)
    n = sum(e["status"] == "accepted" for e in plan["entries"])
    if n:
        print(f"WARNING: {n} already accepted tracks retain their old timestamps. Pending records will move to a new 12-day window.")
    answer = input("Type REBASE to regenerate timestamps for remaining pending entries: ")
    if answer != "REBASE":
        print("Cancelled.")
        return
    c = configuration()
    tracks, _ = tracks_load()
    fresh, _ = plan_entries(c, tracks)
    fmap = {e["id"]: e["timestamp"] for e in fresh}
    for e in plan["entries"]:
        if e["status"] == "pending":
            e["timestamp"] = fmap[e["id"]]
    # Accepted/duplicate states are deliberately preserved. Chronological sort is done at upload time.
    plan["rebased_at"] = now_utc().isoformat()
    save_plan(plan)
    print("Rebased pending entries only. Check preview.csv before uploading.")


def upload(args):
    plan = load_plan()
    ensure_plan_matches(plan)
    c = configuration()
    profile = session_load()
    if c["lastfm_username"] and norm(c["lastfm_username"]) != norm(profile["name"]):
        fail("Your config.json lastfm_username does not match the authenticated account!")
    if args.limit < 1 or args.limit > 500:
        fail("--limit must be 1..500 per run; use the default 50 for a first test")
    pending = sorted([e for e in plan["entries"] if e["status"] == "pending"], key=lambda e: e["timestamp"])
    if not pending:
        print("Nothing pending.")
        return
    if not plan["offsets_known"]:
        print("WARNING: Track positions are ESTIMATED, not real DJ-set cue times.")
    print(f"Last.fm account: {profile['name']}")
    print(f"Candidate pending tracks: {len(pending)}. Per-run cap: {args.limit}")
    print("WARNING: Your 97 displayed set plays may include partial listens; this tool cannot verify them.")
    print("WARNING: Timestamps are RECONSTRUCTED within the last 12 days, NOT original historical listening dates.")
    if input("Confirm you personally verified the number of complete set listens. Type VERIFIED: ") != "VERIFIED":
        print("Cancelled.")
        return
    if input("Confirm you reviewed the 25 track names/remixes and approximate cue points. Type REVIEWED: ") != "REVIEWED":
        print("Cancelled.")
        return
    if input("Submit up to " + str(args.limit) + " records to your Last.fm account? Type UPLOAD: ") != "UPLOAD":
        print("Cancelled.")
        return
    now = int(time.time())
    # Leave a buffer on the API's approximately 14-day age and any future-time checks.
    earliest = now - 13 * 86400 - 12 * 3600
    latest = now - 60
    print("Fetching existing scrobbles to avoid duplicate timestamps...")
    seen = recent_scrobbles(profile["name"], earliest - 180, latest + 180)
    duplicates = 0
    expired = 0
    selected = []
    for e in pending:
        if e["timestamp"] < earliest or e["timestamp"] > latest:
            expired += 1
            continue  # Do not mark completed. Rebase remaining pending entries later.
        if is_duplicate(e, seen):
            e["status"] = "duplicate_existing"
            duplicates += 1
            continue
        selected.append(e)
        seen[(norm(e["artist"]), norm(e["title"]))].append(e["timestamp"])
        if len(selected) >= args.limit:
            break
    if duplicates:
        save_plan(plan)
    print(f"Selected {len(selected)}, skipped as existing duplicates {duplicates}, outside valid timestamp window {expired}.")
    if not selected:
        if expired:
            print("Run 'python importer.py rebase' to move pending timestamps into a fresh window.")
        return
    accepted_total = 0
    ignored_total = 0
    for chunk_start in range(0, len(selected), SCROBBLE_BATCH_MAX):
        chunk = selected[chunk_start:chunk_start + SCROBBLE_BATCH_MAX]
        body = {}
        for i, e in enumerate(chunk):
            for field, value in (("artist", e["artist"]), ("track", e["title"]),
                                 ("timestamp", str(e["timestamp"])), ("duration", str(e["duration"]))):
                body[f"{field}[{i}]"] = value
            if e["album"]:
                body[f"album[{i}]"] = e["album"]
            if e["mbid"]:
                body[f"mbid[{i}]"] = e["mbid"]
        try:
            response = api_call("track.scrobble", body, post=True, authenticated=True)
        except RuntimeError as err:
            print("Submission error; stopping to avoid an unsafe retry:", err)
            print("If a request timed out after Last.fm received it, use preview and existing-history duplicate check before retrying.")
            break
        reports = response.get("scrobbles", {}).get("scrobble", [])
        if isinstance(reports, dict):
            reports = [reports]
        if not isinstance(reports, list) or len(reports) != len(chunk):
            print("Unexpected API response shape: not updating statuses. Review Last.fm account before resuming.")
            break
        hit_daily_limit = False
        for e, report in zip(chunk, reports):
            message = report.get("ignoredMessage", {})
            ignored_code = int(message.get("code", 0)) if isinstance(message, dict) else -1
            if ignored_code == 0:
                e["status"] = "accepted"
                e["lastfm_response"] = "accepted"
                accepted_total += 1
            else:
                e["lastfm_response"] = f"ignored_code_{ignored_code}"
                ignored_total += 1
                if ignored_code == 5:
                    hit_daily_limit = True
                # Leave ignored records pending; the user can correct and retry.
                print(f"  Ignored: {e['artist']} - {e['title']} (code {ignored_code})")
        save_plan(plan)  # durable checkpoint after every batch
        print(f"Saved batch {chunk_start // 50 + 1}: accepted so far {accepted_total}, ignored {ignored_total}.")
        if hit_daily_limit:
            print("Daily scrobble limit reached. STOPPING. Do not retry today; Last.fm's limit is server-enforced.")
            break
        if chunk_start + 50 < len(selected):
            time.sleep(2)
    print(f"Completed this run: {accepted_total} accepted, {ignored_total} ignored. Current status is in preview.html.")


def menu(_args):
    while True:
        print("\nSOUNDCLOUD → LAST.FM — DJ SET BACKFILL")
        print("1) Prepare 97-set plan + HTML/CSV preview")
        print("2) Open existing preview")
        print("3) Validate track names against Last.fm")
        print("4) Connect Last.fm account")
        print("5) Upload a small verified batch (max 50)")
        print("6) Rebase pending timestamps if they get too old")
        print("0) Exit")
        choice = input("Choose: ").strip()
        if choice == "0":
            return
        try:
            if choice == "1": prepare(argparse.Namespace(force=False, open=True))
            elif choice == "2": preview(argparse.Namespace(open=True))
            elif choice == "3": validate(None)
            elif choice == "4": auth(None)
            elif choice == "5": upload(argparse.Namespace(limit=50))
            elif choice == "6": rebase(None)
        except (OSError, RuntimeError, ValueError) as exc:
            print("ERROR:", exc)


def main():
    p = argparse.ArgumentParser(description="Preview and optionally import DJ-set tracks to Last.fm. Defaults are DRY RUN.")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("prepare", help="Build 97-session plan from tracks.csv")
    a.add_argument("--force", action="store_true", help="Replace an unsubmitted plan")
    a.add_argument("--open", action="store_true", help="Open preview in your browser")
    a = sub.add_parser("preview", help="Regenerate preview without changes")
    a.add_argument("--open", action="store_true")
    sub.add_parser("auth", help="Authorize Last.fm in your browser")
    sub.add_parser("validate", help="Check track name existence on Last.fm")
    sub.add_parser("rebase", help="Reschedule PENDING timestamps only")
    a = sub.add_parser("upload", help="Upload confirmed scrobbles (default 50 per invocation)")
    a.add_argument("--limit", type=int, default=50)
    sub.add_parser("menu", help="Interactive Windows-friendly menu")
    args = p.parse_args()
    {"prepare":prepare,"preview":preview,"auth":auth,"validate":validate,
     "rebase":rebase,"upload":upload,"menu":menu}[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Accepted batches are saved after each successful response.")
        sys.exit(130)
