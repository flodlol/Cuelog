"""Offline tests for the original command-line importer (importer.py).

Run: python -m pytest tests"""
import json
import tempfile
import unittest
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import importer  # noqa: E402

# These use your own legacy export (config.json + tracks.csv in the project root), which is not in the repo.
HAS_LEGACY = (importer.ROOT / "tracks.csv").exists() and (importer.ROOT / "config.json").exists()
needs_legacy = unittest.skipUnless(HAS_LEGACY, "needs a local legacy config.json + tracks.csv")


class ImporterTests(unittest.TestCase):
    @needs_legacy
    def test_track_data(self):
        tracks, known = importer.tracks_load()
        self.assertEqual(len(tracks), 25)
        # tracks.csv is now a plan export (play_id,session,track_number,...); this used to raise KeyError: 'number'.
        self.assertEqual(tracks[0]["number"], 1)
        self.assertEqual(tracks[0]["title"], "Live The Moment - Feat. Sarah Maria")
        self.assertEqual(tracks[23]["title"], "Her Voice - Headhunterz Remix")
        self.assertFalse(known)

    @needs_legacy
    def test_plan_full_97_sessions(self):
        c = importer.configuration()
        tracks, _ = importer.tracks_load()
        test_now = datetime(2026, 11, 1, 10, 33, tzinfo=timezone.utc)
        entries, known = importer.plan_entries(c, tracks, test_now)
        self.assertEqual(len(entries), 2425)
        self.assertFalse(known)
        self.assertLess(entries[0]["timestamp"], entries[1]["timestamp"])
        self.assertEqual(entries[-1]["timestamp"], int(test_now.timestamp()) - 900 - entries[-1]["duration"])
        self.assertEqual(entries[24]["session"], 1)
        self.assertEqual(entries[25]["session"], 2)
        self.assertEqual(len({e["timestamp"] for e in entries}), 2425)
        self.assertEqual(sum(e["duration"] for e in entries[:25]), c["mix_duration_seconds"])

    def test_lastfm_api_signature_ascii_order(self):
        params = {"api_key": "key", "method": "track.scrobble", "artist[1]": "B", "artist[10]": "A", "format": "json"}
        actual = importer.signature(params, "secret")
        import hashlib
        self.assertEqual(actual, hashlib.md5(b"api_keykeyartist[10]Aartist[1]Bmethodtrack.scrobblesecret").hexdigest())

    def test_duplicate_90_second_window(self):
        e = {"artist": "Ran-D", "title": "Zombie", "timestamp": 1000}
        seen = defaultdict(list, {(importer.norm("RAN-D"), "zombie"): [910, 1200]})
        self.assertTrue(importer.is_duplicate(e, seen))
        self.assertFalse(importer.is_duplicate({**e, "timestamp": 1100}, seen))

    @needs_legacy
    def test_offline_upload_checkpoints_accepted_and_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            c = importer.configuration()
            c["set_plays"] = 1
            c["lastfm_username"] = "example"
            (tmp / "config.json").write_text(json.dumps(c), encoding="utf-8")
            (tmp / "tracks.csv").write_bytes((importer.ROOT / "tracks.csv").read_bytes())
            with patch.object(importer, "ROOT", tmp):
                tracks, _ = importer.tracks_load()
                entries, known = importer.plan_entries(c, tracks)
                plan = {"fingerprint": importer.fingerprint(c, tracks), "offsets_known": known, "entries": entries}
                importer.save_plan(plan)
                history_called = []
                def fake_api(method, params, **kwargs):
                    history_called.append((method, params))
                    if method == "user.getRecentTracks":
                        return {"recenttracks": {"@attr": {"totalPages": 1}, "track": []}}
                    if method == "track.scrobble":
                        return {"scrobbles": {"scrobble": [{"ignoredMessage": {"code": "0"}}] * 24 + [{"ignoredMessage": {"code": "5"}}]}}
                    raise AssertionError(method)
                with patch.object(importer, "session_load", return_value={"name":"example","key":"example-key"}), \
                     patch.object(importer, "api_call", side_effect=fake_api), \
                     patch("builtins.input", side_effect=["VERIFIED", "REVIEWED", "UPLOAD"]):
                    importer.upload(type("Args", (), {"limit":25})())
                saved = importer.load_plan()
                self.assertEqual(sum(e["status"] == "accepted" for e in saved["entries"]), 24)
                self.assertEqual(sum(e["status"] == "pending" for e in saved["entries"]), 1)
                self.assertTrue(any(x[0] == "track.scrobble" for x in history_called))


if __name__ == "__main__":
    unittest.main()
