"""Persistence, secrets, exports/backups, credentials file and a UI smoke test."""
import json
import os

import pytest

from cuelog import config, db, exporter, repo, secretstore
from cuelog.lastfm import LastfmClient
from conftest import make_set


def test_data_survives_restart(tmp_path, now):
    path = tmp_path / "persist.db"
    db.set_db_path(path)
    sid = make_set()
    repo.create_listen(sid, now - 3000, now=now)
    LastfmClient.store_session("tester", "secret-session-key")
    db.set_setting("daily_cap", 1234)
    db.close()
    db.set_db_path(None)
    db.set_db_path(path)            # "restart"
    s = repo.get_set(sid)
    assert s["track_count"] == 5 and s["listen_count"] == 1
    assert LastfmClient.account() == ("tester", "secret-session-key")
    assert db.setting("daily_cap") == 1234
    assert db.get().execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    db.set_db_path(None)


def test_session_key_is_not_stored_in_plain_text(tmpdb):
    LastfmClient.store_session("tester", "secret-session-key")
    raw = db.get().execute("SELECT token FROM accounts").fetchone()[0]
    assert "secret-session-key" not in raw
    assert secretstore.unseal(raw) == "secret-session-key"


def test_newer_schema_is_refused(tmp_path):
    import sqlite3
    p = tmp_path / "future.db"
    con = sqlite3.connect(p)
    con.execute("PRAGMA user_version = 99")
    con.close()
    with pytest.raises(RuntimeError, match="newer"):
        db.connect(p)


def test_exports_and_backup_restore(tmpdb, tmp_path, now):
    sid = make_set()
    repo.create_listen(sid, now - 3000, now=now)
    LastfmClient.store_session("tester", "secret-session-key")
    assert exporter.export_scrobbles_csv(tmp_path / "s.csv") == 5
    assert exporter.export_set_csv(sid, tmp_path / "set.csv") == 5
    exporter.export_json(tmp_path / "all.json")
    dump = (tmp_path / "all.json").read_text(encoding="utf-8")
    assert "secret-session-key" not in dump and json.loads(dump)["accounts"] == [{"provider": "lastfm", "username": "tester"}]
    exporter.backup(tmp_path / "b.db")
    assert exporter.validate_backup(tmp_path / "b.db")["sets"] == 1
    repo.create_listen(sid, now - 6000, now=now)
    safety = exporter.restore(tmp_path / "b.db")
    assert safety.exists()
    assert repo.get_set(sid)["listen_count"] == 1
    (tmp_path / "junk.db").write_bytes(b"not a database at all")
    with pytest.raises(ValueError):
        exporter.validate_backup(tmp_path / "junk.db")


def test_credentials_file_roundtrip(tmp_path, monkeypatch):
    for k in config.CREDENTIAL_KEYS:
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / "settings.env"
    env.write_text("# comment\nLASTFM_API_KEY=PASTE_YOUR_KEY_HERE\nOTHER=keep\n", encoding="utf-8")
    assert not config.load_credentials(env).lastfm
    config.save_credentials({"LASTFM_API_KEY": "abc", "LASTFM_API_SECRET": "def"}, env)
    text = env.read_text(encoding="utf-8")
    assert "# comment" in text and "OTHER=keep" in text
    assert config.load_credentials(env).lastfm
    with pytest.raises(ValueError):
        config.save_credentials({"EVIL\nKEY": "x"}, env)
    with pytest.raises(ValueError):
        config.save_credentials({"LASTFM_API_KEY": "a\nINJECTED=1"}, env)


def test_ui_starts_and_every_page_renders(tmpdb, now):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6 import QtWebEngineWidgets  # noqa: F401
    from PySide6.QtCore import QCoreApplication, Qt
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtWidgets import QApplication
    from cuelog.ui import theme
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    sid = make_set(url="https://soundcloud.com/test/set")
    repo.create_listen(sid, now - 3000, now=now)
    from cuelog.ui.main_window import MainWindow
    w = MainWindow()
    try:
        for key, arg in [("dashboard", None), ("library", None), ("discover", None), ("set", sid),
                         ("matches", {"set_id": sid}), ("queue", None), ("history", None), ("activity", "history"), ("settings", None)]:
            w.navigate(key, arg)
            app.processEvents()
        assert w.pages["activity"].history.table.rowCount() == 1
        assert w.pages["set"].s["id"] == sid
        from cuelog.ui.dialogs.log_listen import LogListenDialog
        d = LogListenDialog(sid, w)
        d._set_anchor("start")
        d.date.setDate(d.date.date().addDays(-2))
        d.update_preview()
        assert d.ok.isEnabled() and d.p_count.text() == "5" and "scrobbles" in d.p_main.text()
        d.close()
    finally:
        w.runner.stop()
        w.runner.wait(5000)
        w.deleteLater()
