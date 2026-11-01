"""Discover: paste a SoundCloud link, preview it, save it; browse the connected account."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QTabWidget, QVBoxLayout,
                               QWidget)

from ... import repo, services, soundcloud, tracklist
from ...soundcloud import ScMeta, SoundCloudClient
from .. import theme as T
from ..components import ElidedLabel
from ..dialogs.base import Dialog, form
from ..state import app_state
from ..widgets import (Artwork, Banner, EmptyState, button, card, clear_layout, fmt_long_duration, label, open_url,
                       page_header, scroll_page, StatusLabel)
from ..workers import friendly, run


class ManualSetDialog(Dialog):
    def __init__(self, parent=None):
        super().__init__(parent, "Add a set manually", "For mixes that aren't on SoundCloud or can't be fetched.",
                         icon="plus-circle", width=480)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Name of the mix")
        self.uploader = QLineEdit()
        self.uploader.setPlaceholderText("Optional")
        self.duration = QLineEdit()
        self.duration.setPlaceholderText("1:03:48")
        self.duration.setFixedWidth(120)
        self.url = QLineEdit()
        self.url.setPlaceholderText("Optional SoundCloud link")
        self.body.addLayout(form([("Title", self.title), ("DJ", self.uploader), ("Length", self.duration),
                                  ("Link", self.url)]))
        self.error = StatusLabel()
        self.error.setStyleSheet(f"color:{T.RED};")
        self.body.addWidget(self.error)
        self.add_actions("Add set", self._save)
        self.set_id = None

    def _save(self):
        dur = tracklist.parse_timestamp(self.duration.text().strip()) if self.duration.text().strip() else None
        if self.duration.text().strip() and dur is None:
            self.error.setText("Use h:mm:ss or mm:ss for the duration.")
            return
        url = None
        if self.url.text().strip():
            try:
                url, _ = soundcloud.resolve_link(self.url.text())
            except Exception as err:
                self.error.setText(friendly(err))
                return
        try:
            self.set_id = repo.create_set(title=self.title.text(), uploader=self.uploader.text().strip(),
                                          duration_s=dur, source_url=url)
        except ValueError as err:
            self.error.setText(str(err))
            return
        self.accept()


class DiscoverPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.meta: ScMeta | None = None
        self.entries: list[tracklist.TrackEntry] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, _, lay = scroll_page()
        outer.addWidget(area)
        head, _ = page_header("Add a set", "Paste a link to a SoundCloud mix, track or playlist. Share and short links work too.")
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(T.S2)
        self.url = QLineEdit()
        self.url.setObjectName("Big")
        self.url.setPlaceholderText("https://soundcloud.com/artist/dj-set  or  https://on.soundcloud.com/…")
        self.url.setAccessibleName("SoundCloud link")
        self.url.returnPressed.connect(self.fetch)
        row.addWidget(self.url, 1)
        self.fetch_btn = button("Fetch", None, "Primary", self.fetch)
        self.fetch_btn.setProperty("tall", True)
        self.fetch_btn.setStyleSheet("min-height: 38px; max-height: 38px; padding: 0 20px;")
        row.addWidget(self.fetch_btn)
        lay.addLayout(row)
        manual_row = QHBoxLayout()
        manual_row.addWidget(label("Not on SoundCloud?", "Muted"))
        manual_row.addWidget(button("Add it manually", None, "Ghost", self.manual))
        manual_row.addStretch(1)
        lay.addLayout(manual_row)

        self.status = StatusLabel()
        lay.addWidget(self.status)
        self.preview_slot = QVBoxLayout()
        lay.addLayout(self.preview_slot)

        lay.addWidget(label("From your SoundCloud account", "H2"))
        self.account_slot = QVBoxLayout()
        lay.addLayout(self.account_slot)
        lay.addStretch(1)
        self._scanner = None

    def open(self, arg=None):
        self.refresh_account()
        self.url.setFocus()
        if isinstance(arg, dict) and arg.get("hint"):
            self.url.clear()
            self.status.setText(f"Copy the link of “{arg['hint']}” on SoundCloud (Share → Copy link) and paste it here. "
                                "A SoundCloud search for it just opened in your browser.")
            return
        if isinstance(arg, str) and arg.startswith("http"):
            self.url.setText(arg)
            self.fetch()

    def refresh(self):
        self.refresh_account()

    # ---- fetching -------------------------------------------------------------------------
    def fetch(self):
        raw = self.url.text().strip()
        if not raw:
            self.status.setText("Paste a SoundCloud link first.")
            return
        self.fetch_btn.setEnabled(False)
        self.status.setText("Fetching from SoundCloud…")
        clear_layout(self.preview_slot)
        creds = app_state().creds
        run(lambda: soundcloud.fetch_metadata(raw, creds), self._fetched, self._failed)

    def _failed(self, err):
        self.fetch_btn.setEnabled(True)
        self.status.setText("")
        clear_layout(self.preview_slot)
        self.preview_slot.addWidget(Banner(friendly(err), "warn", "circle-alert"))

    def _fetched(self, meta: ScMeta):
        self.meta = meta
        self.fetch_btn.setEnabled(True)
        if meta.duration_ms and meta.description:
            self.status.setText("")
            self._show()
            return
        # oEmbed gives no duration/description: read them from the official widget.
        self.status.setText("Reading duration and description from the SoundCloud player…")
        from ..player import WidgetScanner
        self._scanner = WidgetScanner(meta.embed_url or meta.url, self)
        self._scanner.finished.connect(self._scanned)
        self._scanner.failed.connect(lambda msg: (self.status.setText(msg), self._show()))
        self._scanner.start()
        self._show()

    def _scanned(self, data: dict):
        if not self.meta:
            return
        self.status.setText("")
        self.meta.duration_ms = self.meta.duration_ms or data.get("duration")
        self.meta.description = self.meta.description or data.get("description") or ""
        if data.get("artwork_url") and not self.meta.artwork_url:
            self.meta.artwork_url = soundcloud.hi_res_artwork(data["artwork_url"])
        if data.get("sounds"):
            self.meta.items = [{"title": s.get("title", ""), "uploader": (s.get("user") or {}).get("username", ""),
                                "duration_ms": s.get("duration")} for s in data["sounds"]]
            self.meta.track_count = len(self.meta.items)
        self._show()

    def _show(self):
        m = self.meta
        clear_layout(self.preview_slot)
        if not m:
            return
        self.entries = tracklist.extract_from_description(m.description)
        if not self.entries and m.kind == "playlist" and m.items:
            self.entries = [tracklist.TrackEntry(i, f"{it['uploader']} - {it['title']}", it["uploader"], it["title"])
                            for i, it in enumerate(m.items, 1)]
        c, cl = card()
        top = QHBoxLayout()
        top.setSpacing(20)
        top.addWidget(Artwork(168, 12, m.artwork_url, icon="disc"), 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(6)
        t = ElidedLabel(m.title, "H2", lines=2)
        info.addWidget(t)
        info.addWidget(label(m.uploader, "Secondary"))
        bits = [fmt_long_duration(int(m.duration_ms / 1000)) if m.duration_ms else "Duration loading…"]
        if m.track_count:
            bits.append(f"{m.track_count} items")
        info.addWidget(label(" · ".join(bits), "Muted"))
        if m.description:
            d = label(m.description[:420] + ("…" if len(m.description) > 420 else ""), "Muted", wrap=True)
            d.setTextFormat(Qt.PlainText)
            d.setMaximumHeight(110)
            info.addWidget(d)
        if self.entries:
            timed = sum(1 for e in self.entries if e.start_s is not None)
            info.addWidget(label(f"Detected {len(self.entries)} tracks in the metadata"
                                 + (f" ({timed} with timestamps)" if timed else ""), "Secondary"))
        else:
            info.addWidget(label("No tracklist found in the metadata. You can paste one in the next step.", "Secondary"))
        info.addStretch(1)
        acts = QHBoxLayout()
        existing = repo.find_set_by_url(m.url)
        if existing:
            acts.addWidget(button("Open in library", "library", "Primary", lambda: app_state().go("set", existing["id"])))
            acts.addWidget(label("Already saved", "Muted"))
        else:
            acts.addWidget(button("Save to library", "plus", "Primary", self.save))
        acts.addWidget(button("Open on SoundCloud", "external", None, lambda: open_url(m.url)))
        acts.addStretch(1)
        info.addLayout(acts)
        top.addLayout(info, 1)
        cl.addLayout(top)
        self.preview_slot.addWidget(c)

    def save(self):
        m = self.meta
        if not m:
            return
        raw = "\n".join(e.raw_line for e in self.entries)
        set_id = repo.create_set(title=m.title or "Untitled set", source_url=m.url, uploader=m.uploader,
                                 artwork_url=m.artwork_url, duration_s=int(m.duration_ms / 1000) if m.duration_ms else None,
                                 description=m.description, tracklist_raw=raw, raw_meta=m.raw, kind=m.kind)
        app_state().changed()
        if self.entries:
            creds = app_state().creds

            def work():
                svc = services.MatchService(creds)
                repo.replace_tracklist(set_id, services.build_track_inputs(self.entries, svc), tracklist_raw=raw)
                return set_id
            run(work, lambda sid: (app_state().changed(), app_state().go("set", sid),
                                   app_state().toast("Saved. Next: match the songs on Spotify.", "success")),
                lambda err: app_state().toast(friendly(err), "error"))
        else:
            app_state().go("set", set_id)
            from ..dialogs.tracklist_editor import TracklistEditor
            TracklistEditor(set_id, self.window()).exec()

    def manual(self):
        d = ManualSetDialog(self.window())
        if d.exec() and d.set_id:
            app_state().changed()
            app_state().go("set", d.set_id)
            from ..dialogs.tracklist_editor import TracklistEditor
            TracklistEditor(d.set_id, self.window()).exec()

    # ---- account ---------------------------------------------------------------------------
    def refresh_account(self):
        clear_layout(self.account_slot)
        creds = app_state().creds
        client = SoundCloudClient(creds)
        if not client.configured:
            self.account_slot.addWidget(label(
                "To browse your playlists and likes here, add an approved SoundCloud app in Settings. "
                "Everything else works without it.", "Muted", wrap=True))
            return
        from ... import db
        row = db.get().execute("SELECT username FROM accounts WHERE provider = 'soundcloud'").fetchone()
        if not row:
            self.account_slot.addWidget(Banner("Connect your SoundCloud account to browse your playlists and liked tracks.",
                                               "info", "user", button("Connect", "user", "Primary", self._connect)))
            return
        tabs = QTabWidget()
        self.account_slot.addWidget(label(f"Connected as {row['username']}. SoundCloud's API does not provide your "
                                          "listening history, so only playlists and likes are shown.", "Muted", wrap=True))
        self.account_slot.addWidget(tabs)
        for title, fn in (("Playlists", client.my_playlists), ("Liked tracks", client.liked_tracks),
                          ("Liked playlists", client.liked_playlists)):
            host = QWidget()
            v = QVBoxLayout(host)
            v.setContentsMargins(0, 10, 0, 0)
            v.addWidget(label("Loading…", "Muted"))
            tabs.addTab(host, title)
            run(fn, lambda items, v=v: self._fill(v, items), lambda err, v=v: (clear_layout(v), v.addWidget(label(friendly(err), "Muted"))))

    def _fill(self, v: QVBoxLayout, items: list[ScMeta]):
        clear_layout(v)
        if not items:
            v.addWidget(EmptyState("music", "Nothing here", "SoundCloud returned no items."))
        for it in items[:100]:
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(Artwork(44, 6, it.artwork_url))
            col = QVBoxLayout()
            col.addWidget(ElidedLabel(it.title, "H3"))
            col.addWidget(label(f"{it.uploader} · {fmt_long_duration(int((it.duration_ms or 0) / 1000))}", "Muted"))
            row.addLayout(col, 1)
            row.addWidget(button("Preview", "search", "Ghost", lambda u=it.url: (self.url.setText(u), self.fetch())))
            w = QWidget()
            w.setLayout(row)
            v.addWidget(w)

    def _connect(self):
        client = SoundCloudClient(app_state().creds)
        app_state().toast("Complete the SoundCloud login in your browser…")
        run(client.connect, lambda name: (app_state().toast(f"SoundCloud connected as {name}", "success"), self.refresh_account()),
            lambda err: app_state().toast(friendly(err), "error"))
