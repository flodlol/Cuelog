"""Saved set detail: header, actions, songs, timeline and listening history."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QMenu, QTabWidget, QVBoxLayout, QWidget

from ... import exporter, repo
from .. import theme as T
from ..actions import import_sessions, replan_sessions
from ..components import ElidedLabel, SessionRow, relative_day
from ..state import app_state
from ..timeline import Timeline
from ..widgets import (Artwork, Badge, Banner, EmptyState, button, clear_layout, fit_tabs_to_current, fmt_duration,
                       fmt_long_duration, icon_button, label, open_url, scroll_page)


class TrackRow(QFrame):
    def __init__(self, t: dict, seg_len: int | None, on_open):
        super().__init__()
        self.setObjectName("TrackRow")
        self.setCursor(Qt.PointingHandCursor)
        self.on_open = on_open
        self.t = t
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(14)
        num = label(str(t["position"]), "Muted")
        num.setFixedWidth(22)
        num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(num)
        lay.addWidget(Artwork(46, 6, t.get("art_url") or ""))
        col = QVBoxLayout()
        col.setSpacing(1)
        if t["song_id"] and t["lf_track"]:
            col.addWidget(ElidedLabel(t["lf_track"], "H3"))
            others = t["artists"][1:]
            artist = t["lf_artist"] or "Unknown artist"
            col.addWidget(ElidedLabel(artist + (f"  ·  with {', '.join(others)}" if others else ""), "Secondary"))
        else:
            col.addWidget(ElidedLabel(t["parsed_title"] or t["raw_line"] or "(empty)", "H3"))
            col.addWidget(ElidedLabel(t["parsed_artist"] or "No artist", "Muted"))
        lay.addLayout(col, 3)
        album = ElidedLabel(t["lf_album"] or "", "Muted")
        lay.addWidget(album, 2)
        timing = label(f"{fmt_duration(t['start_s']) if t['start_s'] else '0:00'}" + (f"  ·  {fmt_duration(seg_len)}" if seg_len else ""), "Muted")
        timing.setToolTip(("Estimated position" if t["start_source"] == "estimated" else "Position from " + t["start_source"])
                          + " in the mix · length in mix")
        timing.setFixedWidth(104)
        lay.addWidget(timing)
        if t.get("song_url"):
            lay.addWidget(icon_button("external", "Open on Spotify" if t.get("spotify_id") else "Open source page",
                                      lambda: open_url(t["song_url"]), T.MUTED, 16))
        else:
            sp = QWidget()
            sp.setFixedWidth(30)
            lay.addWidget(sp)
        b = Badge(t["match_status"])
        b.setFixedWidth(96)
        b.setAlignment(Qt.AlignCenter)
        lay.addWidget(b)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_open(self.t["id"])


class SetDetailPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.set_id: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, _, self.lay = scroll_page(margins=(T.S6, 20, T.S6, T.S6))
        outer.addWidget(area)

        top = QHBoxLayout()
        top.addWidget(button("Back", "back", "Ghost", lambda: self.window().back()))
        top.addStretch(1)
        self.lay.addLayout(top)

        head = QHBoxLayout()
        head.setSpacing(T.S5)
        self.art = Artwork(184, 10, icon="disc")
        head.addWidget(self.art, 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(T.S1)
        self.title = label("", "Display", wrap=True, selectable=True)
        info.addWidget(self.title)
        self.uploader = label("", "Secondary")
        info.addWidget(self.uploader)
        self.meta = label("", "Muted", wrap=True)
        info.addWidget(self.meta)
        info.addSpacing(T.S3)
        stats = QHBoxLayout()
        stats.setSpacing(T.S6)
        self.s_listens, self.s_last, self.s_acc, self.s_pending = (self._stat("Logged listens"), self._stat("Last listened"),
                                                                   self._stat("Accepted scrobbles"), self._stat("Pending"))
        for w in (self.s_listens, self.s_last, self.s_acc, self.s_pending):
            stats.addWidget(w)
        stats.addStretch(1)
        info.addLayout(stats)
        info.addStretch(1)
        acts = QHBoxLayout()
        acts.setSpacing(T.S2)
        self.log_btn = button("Log another listen", "repeat", "Primary", self.log_listen)
        acts.addWidget(self.log_btn)
        acts.addWidget(button("Play", "play", None, lambda: app_state().go("player", {"set_id": self.set_id})))
        acts.addWidget(button("Review matches", None, None, lambda: app_state().go("matches", {"set_id": self.set_id})))
        acts.addWidget(button("Edit tracklist", None, None, self.edit_tracklist))
        more = button("", "more", None, tooltip="More actions")
        more.setAccessibleName("More actions")
        menu = QMenu(more)
        menu.addAction("Backfill multiple listens…", self.backfill)
        menu.addAction("Open on SoundCloud", lambda: open_url(self.s.get("source_url")))
        self.fav_action = menu.addAction("Add to favorites", self.toggle_fav)
        menu.addAction("Set duration…", self.set_duration)
        menu.addAction("Export tracklist CSV…", self.export_csv)
        menu.addSeparator()
        menu.addAction("Delete set…", self.delete)
        more.setMenu(menu)
        acts.addWidget(more)
        acts.addStretch(1)
        info.addLayout(acts)
        head.addLayout(info, 1)
        self.lay.addLayout(head)

        self.banner_slot = QVBoxLayout()
        self.lay.addLayout(self.banner_slot)

        self.tabs = QTabWidget()
        self.tracks_host = QWidget()
        self.tracks_lay = QVBoxLayout(self.tracks_host)
        self.tracks_lay.setContentsMargins(0, 12, 0, 0)
        self.tracks_lay.setSpacing(0)
        self.tabs.addTab(self.tracks_host, "Songs")

        tl_host = QWidget()
        tl = QVBoxLayout(tl_host)
        tl.setContentsMargins(0, 14, 0, 0)
        tl.addWidget(label("Drag a boundary to move where a song starts. Ctrl+click a block to preview it in the player. "
                           "Positions marked “est.” are estimated; the full recording length rarely equals its length in the mix.",
                           "Muted", wrap=True))
        self.timeline = Timeline(editable=True, height=110)
        self.timeline.boundaryMoved.connect(self._moved)
        self.timeline.blockClicked.connect(self._block)
        self.timeline.seekRequested.connect(lambda s: app_state().go("player", {"set_id": self.set_id, "seek": s}))
        tl.addWidget(self.timeline)
        self.block_info = label("", "Secondary")
        tl.addWidget(self.block_info)
        tl_row = QHBoxLayout()
        tl_row.addWidget(button("Reset estimates", "refresh", None, self._reset,
                                tooltip="Discard manual positions and re-estimate all but tracklist timestamps"))
        tl_row.addWidget(button("Preview selected segment", "play", None, self._preview))
        tl_row.addStretch(1)
        tl.addLayout(tl_row)
        tl.addStretch(1)
        self.tabs.addTab(tl_host, "Timeline")

        self.hist_host = QWidget()
        self.hist_lay = QVBoxLayout(self.hist_host)
        self.hist_lay.setContentsMargins(0, 12, 0, 0)
        self.hist_lay.setSpacing(0)
        self.tabs.addTab(self.hist_host, "Listening history")
        fit_tabs_to_current(self.tabs)
        self.lay.addWidget(self.tabs)
        self.lay.addStretch(1)
        self.s: dict = {}

    def _stat(self, title: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        val = label("—", "H2")
        v.addWidget(val)
        v.addWidget(label(title, "Muted"))
        w.value = val  # type: ignore[attr-defined]
        return w

    def open(self, arg=None):
        if isinstance(arg, dict):
            if arg.get("tab") == "history":
                self.tabs.setCurrentIndex(2)
            arg = arg.get("set_id")
        if arg:
            if arg != self.set_id:
                self.tabs.setCurrentIndex(0)
            self.set_id = int(arg)
        self.refresh()

    def refresh(self):
        if not self.set_id:
            return
        s = repo.get_set(self.set_id)
        if not s:
            app_state().go("library")
            return
        self.s = s
        self.art.set_url(s["artwork_url"])
        self.title.setText(s["title"])
        self.uploader.setText(s["uploader"] or "Unknown uploader")
        saved = s["created_at"][:10]
        self.meta.setText(f"{fmt_long_duration(s['duration_s'])} · {s['track_count']} songs · saved {saved}"
                          + (" · on SoundCloud" if s["source_url"] else ""))
        self.s_listens.value.setText(str(s["listen_count"]))
        self.s_last.value.setText(relative_day(s["last_listened"]))
        self.s_acc.value.setText(f"{s['accepted_count']:,}")
        self.s_pending.value.setText(f"{s['pending_count']:,}")
        self.fav_action.setText("Remove from favorites" if s["favorite"] else "Add to favorites")
        self.log_btn.setEnabled(s["track_count"] > 0)

        clear_layout(self.banner_slot)
        if not s["duration_s"]:
            self.banner_slot.addWidget(Banner("The mix duration is unknown, so song positions are rough. Open it in the "
                                              "Cuelog player once (it reads the duration) or set it from the ⋯ menu.", "info", "clock"))
        if s["track_count"] == 0:
            self.banner_slot.addWidget(Banner("This set has no tracklist yet. Paste or type one to start matching songs.",
                                              action=button("Add tracklist", "pencil", "Primary", self.edit_tracklist)))
        elif s["review_count"]:
            self.banner_slot.addWidget(Banner(
                f"{s['review_count']} song(s) still need a confirmed match. They are skipped when scrobbling until you review them.",
                action=button("Review matches", "sparkles", None, lambda: app_state().go("matches", {"set_id": self.set_id}))))
        expired = [x for x in repo.list_sessions(self.set_id) if x["status"] == "expired"]
        if expired:
            self.banner_slot.addWidget(Banner(
                f"{len(expired)} session(s) are older than Last.fm accepts and were never submitted. "
                "Re-plan them into the accepted window (reconstructed timestamps) or leave them as local history.",
                action=button("Re-plan", "refresh", None, lambda: replan_sessions([x["id"] for x in expired], self.window()))))

        tracks = repo.tracks_for_set(self.set_id)
        segs = repo.segments_for_set(self.set_id)
        seg_len = {sg.set_track_id: sg.length for sg in segs}
        sig = (self.set_id, T.ZOOM, T.ACCENT, tuple(tuple(t.items()) for t in tracks), tuple(sorted(seg_len.items())))
        same = sig == getattr(self, "_rows_sig", None)
        self._rows_sig = sig
        duration = s["duration_s"] or (segs[-1].end_s if segs else 1)
        self.timeline.set_data([{"id": t["id"], "pos": t["position"], "start": sg.start_s, "end": sg.end_s,
                                 "title": t["lf_track"] or t["parsed_title"], "status": t["match_status"],
                                 "estimated": t["start_source"] == "estimated"} for t, sg in zip(tracks, segs)], duration)
        if same:          # opening the same set again: the rows are already right
            return
        self._row_gen = getattr(self, "_row_gen", 0) + 1
        clear_layout(self.tracks_lay)
        if not tracks:
            self.tracks_lay.addWidget(EmptyState("list", "No songs yet", "Add the tracklist of this set.",
                                                 button("Add tracklist", "pencil", "Primary", self.edit_tracklist)))
        hdr = QHBoxLayout()
        hdr.setContentsMargins(10, 0, 10, 6)
        hdr.setSpacing(14)
        for text, w in (("#", 22), ("", 46), ("Title", 0), ("Album", 0), ("In the mix", 104), ("", 30), ("Match", 96)):
            lb = label(text, "Eyebrow")
            if w:
                lb.setFixedWidth(w)
            hdr.addWidget(lb, 0 if w else (3 if text == "Title" else 2))
        if tracks:
            self.tracks_lay.addLayout(hdr)
        self.tracks_lay.addStretch(1)
        # First screenful now, the rest in small batches so the page appears instantly.
        self._add_rows(list(tracks), seg_len, self._row_gen, 18)

    def _add_rows(self, todo: list, seg_len: dict, gen: int, n: int = 30):
        if gen != self._row_gen:
            return
        open_track = lambda tid: app_state().go("matches", {"set_id": self.set_id, "track_id": tid})
        at = self.tracks_lay.count() - 1          # before the stretch
        self.tracks_host.setUpdatesEnabled(False)
        for t in todo[:n]:
            self.tracks_lay.insertWidget(at, TrackRow(t, seg_len.get(t["id"]), open_track))
            at += 1
        self.tracks_host.setUpdatesEnabled(True)
        if todo[n:]:
            QTimer.singleShot(0, lambda: self._add_rows(todo[n:], seg_len, gen))

        clear_layout(self.hist_lay)
        sessions = repo.list_sessions(self.set_id)
        if not sessions:
            self.hist_lay.addWidget(EmptyState("history", "No listens logged", "Use “Log another listen” after you hear this set."))
        else:
            ready = [x["id"] for x in sessions if x["status"] in ("draft", "partial", "needs_review") and x["n_pending"]]
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 8)
            row.addWidget(label(f"{len(sessions)} sessions", "Muted"))
            row.addStretch(1)
            if ready:
                row.addWidget(button(f"Import {len(ready)} unsubmitted session(s)", "upload", None,
                                     lambda: import_sessions(ready, self.window())))
            self.hist_lay.addLayout(row)
            for x in sessions[:300]:
                self.hist_lay.addWidget(SessionRow(x, show_set=False))
            if len(sessions) > 300:
                self.hist_lay.addWidget(label(f"Showing 300 of {len(sessions)}. See History for all.", "Muted"))
        self.hist_lay.addStretch(1)
        self.tabs.currentChanged.emit(self.tabs.currentIndex())

    # ---- actions ---------------------------------------------------------------------------
    def log_listen(self):
        from ..dialogs.log_listen import LogListenDialog
        LogListenDialog(self.set_id, self.window()).exec()

    def backfill(self):
        from ..dialogs.backfill import BackfillDialog
        BackfillDialog(self.set_id, self.window()).exec()

    def edit_tracklist(self):
        from ..dialogs.tracklist_editor import TracklistEditor
        TracklistEditor(self.set_id, self.window()).exec()

    def toggle_fav(self):
        repo.update_set(self.set_id, favorite=0 if self.s.get("favorite") else 1)
        app_state().changed()

    def set_duration(self):
        from ...tracklist import format_timestamp, parse_timestamp
        from ..dialogs.base import ask_text, notify
        text, ok = ask_text(self, "Mix length", "How long is this mix? Use hours:minutes:seconds.",
                            format_timestamp(self.s.get("duration_s")) if self.s.get("duration_s") else "", "1:03:48", "Save")
        if ok:
            d = parse_timestamp(text.strip())
            if not d or d < 60:
                notify(self, "That's not a length", "Use a format like 1:03:48.", kind="error")
                return
            repo.update_set(self.set_id, duration_s=d)
            app_state().changed()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export tracklist", f"{self.s['title'][:40]}.csv", "CSV (*.csv)")
        if path:
            n = exporter.export_set_csv(self.set_id, Path(path))
            app_state().toast(f"Exported {n} songs", "success")

    def delete(self):
        from ..dialogs.base import confirm, notify
        if not confirm(self, "Delete this set?", f"“{self.s['title']}”, its tracklist and listens that were never sent "
                       "are removed. Song matches stay available for your other sets.", "Delete set", danger=True):
            return
        try:
            repo.delete_set(self.set_id)
        except ValueError as err:
            notify(self, "Can't delete this set", str(err), kind="error")
            return
        app_state().changed()
        app_state().go("library")

    def _moved(self, track_id: int, start: int):
        repo.set_track_start(track_id, start)
        self.refresh()

    def _block(self, i: int):
        b = self.timeline.blocks[i]
        self.block_info.setText(f"{b['pos']}. {b['title']} · {fmt_duration(b['start'])} – {fmt_duration(b['end'])}"
                                + ("  (estimated)" if b.get("estimated") else ""))

    def _reset(self):
        repo.reset_estimates(self.set_id)
        self.refresh()

    def _preview(self):
        i = self.timeline.selected
        start = self.timeline.blocks[i]["start"] if i is not None and self.timeline.blocks else 0
        app_state().go("player", {"set_id": self.set_id, "seek": start})
