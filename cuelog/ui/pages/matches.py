"""Review matches: confirm or correct the recording behind every song of a set."""
from __future__ import annotations

import json

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QProgressBar,
                               QScrollArea, QSplitter, QVBoxLayout, QWidget)

from ... import repo, services
from ...matching import Candidate, confidence_level, rank
from ...urls import spotify_track_id
from .. import theme as T
from ..components import ElidedLabel
from ..preview import PreviewButton, PreviewPlayer
from ..state import app_state
from ..widgets import Artwork, Badge, StatusLabel, button, card, clear_layout, fmt_duration, label, open_url, page_header
from ..workers import friendly, run

SOURCE_LABEL = {"spotify": "Spotify", "musicbrainz": "MusicBrainz", "manual": "Entered manually", "deezer": "Deezer"}
ART_LABEL = {"spotify": "Spotify artwork", "spotify-oembed": "Spotify artwork", "coverartarchive": "Cover Art Archive artwork"}
DOT = {"confirmed": T.GREEN, "manual": T.TEXT_2, "auto": T.BLUE, "ambiguous": T.AMBER, "unmatched": T.RED, "no_match": T.MUTED}
OPEN_STATES = ("auto", "ambiguous", "unmatched")


def _meta(album: str, duration_ms: int | None, date: str, source: str | None = None) -> str:
    bits = [album or "", fmt_duration(int(duration_ms / 1000)) if duration_ms else "", (date or "")[:4],
            SOURCE_LABEL.get(source or "", source or "")]
    return " · ".join(b for b in bits if b)


class CandidateRow(QFrame):
    """One alternative recording: artwork, title, artists, details, confidence, preview, use."""

    def __init__(self, cand: Candidate, score: float | None, reasons: list[str], on_use):
        super().__init__()
        self.setObjectName("Cand")
        self.setStyleSheet(f"QFrame#Cand {{ border-radius: 4px; }} QFrame#Cand:hover {{ background: {T.SURFACE_2}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(T.S2, T.S2, T.S2, T.S2)
        lay.setSpacing(T.S3)
        lay.addWidget(Artwork(44, 6, cand.art_url))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(ElidedLabel(cand.title, "H3"))
        artists = cand.artists or ["Unknown artist"]
        col.addWidget(ElidedLabel(artists[0] + (f" · with {', '.join(artists[1:])}" if len(artists) > 1 else ""), "Secondary"))
        detail = _meta(cand.album, cand.duration_ms, cand.release_date, cand.source)
        if reasons:
            detail += "  —  " + "; ".join(reasons)
        col.addWidget(ElidedLabel(detail, "Small"))
        lay.addLayout(col, 1)
        if score is not None:
            color = {"high": T.GREEN, "medium": T.AMBER}.get(confidence_level(score), T.RED)
            sc = label(f"{round(score * 100)}%")
            sc.setStyleSheet(f"color:{color}; font-weight:600; font-size:12px;")
            sc.setToolTip("How closely this matches the tracklist line")
            sc.setFixedWidth(40)
            sc.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lay.addWidget(sc)
        lay.addWidget(PreviewButton(f"c:{cand.spotify_id or cand.mbid or cand.title}", cand.main_artist, cand.title,
                                    cand.duration_ms, compact=True))
        lay.addWidget(button("Use", None, None, on_use))


class MatchReviewPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.set_id: int | None = None
        self.track_id: int | None = None
        self.busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.S6, 20, T.S6, T.S5)
        outer.setSpacing(T.S4)

        back = QHBoxLayout()
        back.addWidget(button("Back to set", "back", "Ghost", lambda: app_state().go("set", self.set_id)))
        back.addStretch(1)
        outer.addLayout(back)
        self.sub = label("", "Muted")
        self.accept_btn = button("Accept confident matches", None, None, self.accept_high)
        self.auto_btn = button("Find matches", None, "Primary", self.auto_match)
        head, _ = page_header("Review matches", self.sub, self.accept_btn, self.auto_btn)
        outer.addLayout(head)

        status = QHBoxLayout()
        status.setSpacing(T.S3)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setVisible(False)
        status.addWidget(self.progress)
        self.status = StatusLabel("Small")
        status.addWidget(self.status, 1)
        self.status_row = QWidget()
        self.status_row.setLayout(status)
        status.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.status_row)
        self.status_row.hide()

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(T.S5)
        split.setStyleSheet("QSplitter::handle { background: transparent; }")
        self.list = QListWidget()
        self.list.setMinimumWidth(280)
        self.list.setMaximumWidth(340)
        self.list.setSpacing(0)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._select)
        self.list.setAccessibleName("Songs in this set")
        split.addWidget(self.list)
        self.detail_area = QScrollArea()
        self.detail_area.setWidgetResizable(True)
        self.detail_area.setFrameShape(QFrame.NoFrame)
        self.detail_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("Page")
        self.detail = QVBoxLayout(inner)
        self.detail.setContentsMargins(0, 0, T.S3, T.S5)
        self.detail.setSpacing(T.S4)
        self.detail_area.setWidget(inner)
        split.addWidget(self.detail_area)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 900])
        outer.addWidget(split, 1)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.confirm)
        QShortcut(QKeySequence("Ctrl+Down"), self, activated=lambda: self._step(1))
        QShortcut(QKeySequence("Ctrl+Up"), self, activated=lambda: self._step(-1))

    # ---- data ------------------------------------------------------------------------------
    def open(self, arg=None):
        if isinstance(arg, dict):
            if arg.get("set_id") != self.set_id:
                self.list.clear()
            self.set_id = arg.get("set_id", self.set_id)
            self.track_id = arg.get("track_id") or (self.track_id if arg.get("set_id") == self.set_id else None)
        self.status.setText("")
        self.refresh()

    def hideEvent(self, e):
        PreviewPlayer.instance().stop()
        super().hideEvent(e)

    def _say(self, text: str, kind: str = "info"):
        color = {"error": T.RED, "success": T.GREEN}.get(kind, T.MUTED)
        self.status.setStyleSheet(f"color:{color}; font-size:12px;")
        self.status.setText(text)
        self.status_row.setVisible(bool(text) or self.progress.isVisible())

    def refresh(self):
        if not self.set_id:
            return
        s = repo.get_set(self.set_id)
        if not s:
            return
        tracks = repo.tracks_for_set(self.set_id)
        counts: dict[str, int] = {}
        for t in tracks:
            counts[t["match_status"]] = counts.get(t["match_status"], 0) + 1
        verified = counts.get("confirmed", 0) + counts.get("manual", 0)
        todo = sum(counts.get(k, 0) for k in OPEN_STATES)
        high = sum(1 for t in tracks if t["match_status"] == "auto" and (t["match_confidence"] or 0) >= 0.85)
        self.sub.setText(f"{s['title']} · {verified} of {len(tracks)} confirmed" + (f" · {todo} to check" if todo else ""))
        self.accept_btn.setText(f"Accept confident matches ({high})" if high else "Accept confident matches")
        self.accept_btn.setEnabled(high > 0 and not self.busy)
        unmatched = counts.get("unmatched", 0) + counts.get("ambiguous", 0)
        self.auto_btn.setVisible(unmatched > 0 or not tracks)
        self.auto_btn.setEnabled(not self.busy and bool(tracks))
        if not self.status.text() and not services.MatchService(app_state().creds).spotify.configured:
            self._say("Matching uses MusicBrainz. Add Spotify in Settings to match against Spotify.")

        ids = [t["id"] for t in tracks]
        same = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())] == ids
        if self.track_id not in ids:
            self.track_id = next((t["id"] for t in tracks if t["match_status"] in OPEN_STATES), ids[0] if ids else None)
        if same:
            for i, t in enumerate(tracks):                      # update rows in place: no jump, no flicker
                self.list.setItemWidget(self.list.item(i), self._row(t))
        else:
            self.list.blockSignals(True)
            self.list.clear()
            for t in tracks:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, t["id"])
                item.setSizeHint(QSize(260, 52))
                self.list.addItem(item)
                self.list.setItemWidget(item, self._row(t))
            self.list.blockSignals(False)
        if self.track_id is not None:
            self._select_id(self.track_id)
        cur = next((t for t in tracks if t["id"] == self.track_id), None)
        sig = (self.track_id, cur and (cur["match_status"], cur["song_id"], cur["candidates_json"], cur["art_url"]))
        if sig != getattr(self, "_shown", None):      # only rebuild the detail when this song changed
            self.show_track()

    def _row(self, t: dict) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(T.S2, T.S1, T.S3, T.S1)
        h.setSpacing(T.S3)
        num = label(str(t["position"]), "Small")
        num.setFixedWidth(18)
        num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(num)
        h.addWidget(Artwork(36, 4, t.get("art_url") or ""))
        v = QVBoxLayout()
        v.setSpacing(0)
        v.addWidget(ElidedLabel(t["lf_track"] or t["parsed_title"] or "(empty)"))
        v.addWidget(ElidedLabel(t["lf_artist"] or t["parsed_artist"], "Small"))
        h.addLayout(v, 1)
        dot = QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{DOT.get(t['match_status'], T.MUTED)}; border-radius: 4px;")
        dot.setToolTip(T.STATUS_STYLE.get(t["match_status"], ("",))[0])
        h.addWidget(dot)
        return w

    def _select_id(self, track_id: int):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == track_id:
                self.list.blockSignals(True)
                self.list.setCurrentRow(i)
                self.list.blockSignals(False)
                self.list.scrollToItem(self.list.item(i), QListWidget.EnsureVisible)
                return

    def _select(self, item, _prev):
        if item:
            self.track_id = item.data(Qt.UserRole)
            self.show_track()

    def _step(self, d: int):
        row = self.list.currentRow() + d
        if 0 <= row < self.list.count():
            self.list.setCurrentRow(row)

    # ---- detail ------------------------------------------------------------------------------
    def show_track(self):
        clear_layout(self.detail)
        t = repo.get_track(self.track_id) if self.track_id else None
        self._shown = (self.track_id, t and (t["match_status"], t["song_id"], t["candidates_json"], t["art_url"]))
        if not t:
            self.detail.addStretch(1)
            return
        n = self.list.count()
        row = self.list.currentRow()

        nav = QHBoxLayout()
        nav.setSpacing(T.S2)
        nav.addWidget(label(f"Song {t['position']} of {n}", "H2"))
        nav.addStretch(1)
        prev_b = button("", "chevron-up", "Ghost", lambda: self._step(-1), tooltip="Previous song (Ctrl+Up)")
        next_b = button("", "chevron-down", "Ghost", lambda: self._step(1), tooltip="Next song (Ctrl+Down)")
        prev_b.setEnabled(row > 0)
        next_b.setEnabled(row < n - 1)
        nav.addWidget(prev_b)
        nav.addWidget(next_b)
        self.detail.addLayout(nav)
        line = label(f"Tracklist says: {t['raw_line'] or '(no original text)'}", "Secondary", wrap=True, selectable=True)
        self.detail.addWidget(line)

        self.detail.addWidget(self._match_card(t))

        cands = json.loads(t["candidates_json"] or "[]")
        others = [c for c in cands if not (t.get("spotify_id") and c["candidate"].get("spotify_id") == t.get("spotify_id"))
                  and not (t.get("mbid") and c["candidate"].get("mbid") == t.get("mbid"))]
        if others:
            self.detail.addWidget(label("Other versions", "H2"))
            box = QVBoxLayout()
            box.setSpacing(0)
            for cd in others[:6]:
                cand = Candidate.from_dict(cd["candidate"])
                box.addWidget(CandidateRow(cand, cd.get("score"), cd.get("reasons", []),
                                           lambda c=cand, s=cd.get("score"): self.use(c, s)))
            self.detail.addLayout(box)

        self.detail.addWidget(self._search_card(t))
        self.detail.addStretch(1)
        QTimer.singleShot(0, lambda: self.detail_area.verticalScrollBar().setValue(0))

    def _match_card(self, t: dict) -> QFrame:
        c, cl = card()
        top = QHBoxLayout()
        top.setSpacing(T.S5)
        has = bool(t["song_id"])
        top.addWidget(Artwork(112, 8, t.get("art_url") or "" if has else ""), 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(T.S1)
        badges = QHBoxLayout()
        badges.setSpacing(T.S2)
        badges.addWidget(Badge(t["match_status"]))
        if t["match_confidence"] is not None and t["match_status"] in ("auto", "ambiguous"):
            badges.addWidget(label(f"{round(t['match_confidence'] * 100)}% match", "Small"))
        badges.addStretch(1)
        info.addLayout(badges)
        info.addSpacing(T.S1)
        if has:
            info.addWidget(label(t["lf_track"] or "(no title)", "H2", wrap=True))
            others = t["artists"][1:]
            who = label(f"Scrobbles as <b>{t['lf_artist'] or '—'}</b>"
                        + (f"<span style='color:{T.MUTED}'> · with {', '.join(others)}</span>" if others else ""), wrap=True)
            who.setTextFormat(Qt.RichText)
            info.addWidget(who)
            meta = _meta(t["lf_album"], t.get("duration_ms"), t.get("release_date") or "", t["song_source"])
            art = ART_LABEL.get(t.get("art_source") or "")
            info.addWidget(label(" · ".join(x for x in (meta, art) if x), "Small", wrap=True))
        else:
            info.addWidget(label("No recording chosen" if t["match_status"] != "no_match" else "Not scrobbled", "H2"))
            info.addWidget(label("Pick one of the versions below, search, or enter the details yourself. "
                                 "Songs without a match are never scrobbled." if t["match_status"] != "no_match" else
                                 "This song is skipped when you log a listen.", "Muted", wrap=True))
        if t["match_note"] and t["match_status"] in ("auto", "ambiguous"):
            note = label(t["match_note"], "Small", wrap=True)
            note.setStyleSheet(f"color:{T.AMBER}; font-size:12px;")
            info.addWidget(note)
        info.addSpacing(T.S2)
        acts = QHBoxLayout()
        acts.setSpacing(T.S2)
        if has and t["match_status"] in OPEN_STATES:
            acts.addWidget(button("Confirm", "check", "Primary", self.confirm, tooltip="Confirm and go to the next song (Ctrl+Enter)"))
        if has:
            acts.addWidget(PreviewButton(f"s:{t['song_id']}", t["lf_artist"], t["lf_track"], t.get("duration_ms")))
        acts.addWidget(button("Hear it in the mix", "headphones", "Ghost",
                              lambda: app_state().go("player", {"set_id": self.set_id, "seek": t["start_s"] or 0})))
        acts.addStretch(1)
        more = button("", "more", "Ghost", tooltip="More")
        more.setAccessibleName("More actions")
        menu = QMenu(more)
        if t.get("song_url"):
            menu.addAction("Open on Spotify" if t.get("spotify_id") else "Open source page", lambda: open_url(t["song_url"]))
        if t["match_status"] != "no_match":
            menu.addAction("Don't scrobble this song", self.no_match)
        more.setMenu(menu)
        more.setVisible(not menu.isEmpty())
        acts.addWidget(more)
        info.addLayout(acts)
        top.addLayout(info, 1)
        cl.addLayout(top)
        return c

    def _search_card(self, t: dict) -> QFrame:
        c, cl = card()
        cl.addWidget(label("Find a different match", "H2"))
        g = QGridLayout()
        g.setHorizontalSpacing(T.S2)
        g.setVerticalSpacing(T.S2)
        self.q_artist = QLineEdit(t["parsed_artist"])
        self.q_artist.setPlaceholderText("Artist")
        self.q_title = QLineEdit(t["parsed_title"])
        self.q_title.setPlaceholderText("Title")
        self.q_title.returnPressed.connect(self.search)
        self.q_artist.returnPressed.connect(self.search)
        g.addWidget(self.q_artist, 0, 0)
        g.addWidget(self.q_title, 0, 1)
        g.addWidget(button("Search", None, None, self.search), 0, 2)
        self.link = QLineEdit()
        self.link.setPlaceholderText("Or paste a Spotify track link")
        self.link.returnPressed.connect(self.use_link)
        g.addWidget(self.link, 1, 0, 1, 2)
        g.addWidget(button("Use link", None, None, self.use_link), 1, 2)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        cl.addLayout(g)
        self.results = QVBoxLayout()
        self.results.setSpacing(0)
        cl.addLayout(self.results)

        self.manual_toggle = button("Enter details manually", "chevron-down", "Ghost", self._toggle_manual)
        cl.addWidget(self.manual_toggle, 0, Qt.AlignLeft)
        self.manual_box = QWidget()
        mg = QGridLayout(self.manual_box)
        mg.setContentsMargins(0, 0, 0, 0)
        mg.setHorizontalSpacing(T.S2)
        mg.setVerticalSpacing(T.S2)
        self.m_artist = QLineEdit(t["lf_artist"] or t["parsed_artist"])
        self.m_artist.setPlaceholderText("Artist (sent to Last.fm)")
        self.m_title = QLineEdit(t["lf_track"] or t["parsed_title"])
        self.m_title.setPlaceholderText("Title")
        self.m_album = QLineEdit(t["lf_album"] or "")
        self.m_album.setPlaceholderText("Album (optional)")
        mg.addWidget(self.m_artist, 0, 0)
        mg.addWidget(self.m_title, 0, 1)
        mg.addWidget(self.m_album, 1, 0)
        mg.addWidget(button("Save", None, None, self.manual), 1, 2)
        mg.setColumnStretch(0, 1)
        mg.setColumnStretch(1, 1)
        self.manual_box.setVisible(False)
        cl.addWidget(self.manual_box)
        return c

    def _toggle_manual(self):
        on = not self.manual_box.isVisible()
        self.manual_box.setVisible(on)
        self.manual_toggle.setIcon(T.icon("chevron-up" if on else "chevron-down", T.TEXT_2, 16))

    # ---- actions ----------------------------------------------------------------------------
    def _advance(self):
        """Move to the next song that still needs attention, keeping the list where it is."""
        tracks = repo.tracks_for_set(self.set_id)
        ids = [t["id"] for t in tracks]
        i = ids.index(self.track_id) if self.track_id in ids else -1
        nxt = next((t["id"] for t in tracks[i + 1:] + tracks[:i] if t["match_status"] in OPEN_STATES), None)
        if nxt:
            self.track_id = nxt
        else:
            self._say("Every song is reviewed. You're ready to log listens.", "success")
        app_state().changed()        # refreshes this page in place, plus the rest of the app

    def confirm(self):
        t = repo.get_track(self.track_id) if self.track_id else None
        if not t or not t["song_id"] or t["match_status"] not in OPEN_STATES:
            return
        repo.assign_match(t["id"], t["song_id"], "confirmed", t["match_confidence"], t["match_note"])
        self._advance()

    def no_match(self):
        repo.assign_match(self.track_id, None, "no_match", None, "You chose not to scrobble this song")
        self._advance()

    def use(self, cand: Candidate, score):
        services.choose_candidate(self.track_id, cand, score)
        self._advance()

    def manual(self):
        try:
            song = repo.manual_song(self.m_artist.text(), self.m_title.text(), self.m_album.text())
        except ValueError as err:
            self._say(str(err), "error")
            return
        repo.assign_match(self.track_id, song, "manual", None, "Entered manually")
        self._advance()

    def search(self):
        artist, title = self.q_artist.text().strip(), self.q_title.text().strip()
        if not title:
            return
        clear_layout(self.results)
        self.results.addWidget(label("Searching…", "Small"))
        svc = services.MatchService(app_state().creds)
        tid = self.track_id

        def done(cands):
            if tid != self.track_id:
                return
            clear_layout(self.results)
            ranked = rank(artist, title, cands)
            if not ranked:
                self.results.addWidget(label("Nothing found. Try fewer words, or enter the details manually.", "Small"))
            for r in ranked[:8]:
                self.results.addWidget(CandidateRow(r.candidate, r.score, r.reasons,
                                                    lambda c=r.candidate, s=r.score: self.use(c, s)))

        def fail(err):
            if tid == self.track_id:
                clear_layout(self.results)
                self.results.addWidget(label(friendly(err), "Small"))
        run(lambda: svc.search(artist, title), done, fail)

    def use_link(self):
        sid = spotify_track_id(self.link.text())
        if not sid:
            self._say("That isn't a Spotify track link.", "error")
            return
        svc = services.MatchService(app_state().creds)
        tid = self.track_id

        def work():
            song_id, status, note = svc.song_from_spotify_link(sid)
            if status == "confirmed":
                repo.assign_match(tid, song_id, "confirmed", 1.0, note)
            else:
                song = repo.get_song(song_id)
                t = repo.get_track(tid)
                # Without the API we know the title and artwork but not the artist: keep the tracklist artist.
                fixed = repo.upsert_song(Candidate("spotify", song["title"], [t["parsed_artist"]] if t["parsed_artist"] else [],
                                                   spotify_id=sid, url=song["url"], art_url=song["art_url"] or "",
                                                   art_source=song["art_source"] or ""))
                repo.assign_match(tid, fixed, "ambiguous", None,
                                  "Artist taken from the tracklist (Spotify API not set up). Confirm if it's right.")
            return status
        run(work, lambda st: (self._say("Spotify link applied.", "success"), self._advance() if st == "confirmed"
                              else app_state().changed()),
            lambda err: self._say(friendly(err), "error"))

    def accept_high(self):
        n = services.accept_high_confidence(self.set_id)
        self._say(f"Confirmed {n} confident match{'es' if n != 1 else ''}.", "success")
        self._advance()

    def auto_match(self):
        if self.busy:
            return
        self.busy = True
        self.auto_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.status_row.setVisible(True)
        self.progress.setValue(0)
        svc = services.MatchService(app_state().creds)
        sid = self.set_id

        def prog(p):
            n, total, text = p
            self.progress.setMaximum(max(1, total))
            self.progress.setValue(n)
            self._say(f"Matching {n} of {total}: {text}")

        def done(res):
            self.busy = False
            self.progress.setVisible(False)
            errs = res.get("errors") or []
            found = res.get("auto", 0) + res.get("ambiguous", 0) + res.get("confirmed", 0)
            msg = f"Found {found} match{'es' if found != 1 else ''}"
            if res.get("ambiguous"):
                msg += f", {res['ambiguous']} worth a listen"
            if res.get("unmatched"):
                msg += f", {res['unmatched']} without a match"
            if errs:
                msg += f". {len(errs)} failed: {errs[0]}"
            self._say(msg + ".", "error" if errs and not found else "info")
            self.track_id = None
            app_state().changed()

        def fail(err):
            self.busy = False
            self.progress.setVisible(False)
            self._say(friendly(err), "error")
            self.refresh()
        run(lambda emit: svc.match_set(sid, progress=lambda a, b, c: emit((a, b, c))), done, fail, on_progress=prog)
