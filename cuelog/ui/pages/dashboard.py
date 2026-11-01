"""Home: your Last.fm listening (totals, per-day chart, top artists/tracks, recent songs) plus your Cuelog sets.

Account statistics come straight from Last.fm, so they include everything you scrobble (Spotify, other
apps), not only what Cuelog sent. They load in the background and are cached for a few minutes. Without
a Last.fm connection, Home falls back to Cuelog's own numbers.
"""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ... import images, insights, repo
from ...lastfm import LastfmClient
from .. import theme as T
from ..charts import BarChart, HBars, TileGrid
from ..components import ElidedLabel, SetListRow, relative_time
from ..state import app_state
from ..widgets import (Artwork, Banner, EmptyState, button, card, clear_layout, label, open_url, page_header,
                       scroll_page)
from ..workers import run

PERIODS = [("7day", "7 days"), ("1month", "30 days"), ("12month", "12 months"), ("overall", "All time")]


class Stat(QWidget):
    def __init__(self, caption: str):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.value = label("—", "Display")
        v.addWidget(self.value)
        self.caption = label(caption, "Small")
        v.addWidget(self.caption)

    def set(self, value, caption: str | None = None):
        self.value.setText(f"{value:,}" if isinstance(value, int) else str(value))
        if caption is not None:
            self.caption.setText(caption)


def _section(title: str, right: QWidget | None = None) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(label(title, "H2"))
    h.addStretch(1)
    if right:
        h.addWidget(right)
    return h


class DashboardPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self._gen = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, _, self.lay = scroll_page()
        outer.addWidget(area)

        self.source = label("", "Muted")
        head, _ = page_header("Home", self.source, button("Add set", "plus", "Primary", lambda: app_state().go("discover")))
        self.lay.addLayout(head)
        self.notice_slot = QVBoxLayout()
        self.notice_slot.setSpacing(T.S2)
        self.lay.addLayout(self.notice_slot)

        # One overview card: scrobbles per day on the left, the four totals on the right
        stats_card, scl = card()
        over = QHBoxLayout()
        over.setSpacing(T.S6)
        daily_col = QVBoxLayout()
        daily_col.setSpacing(T.S2)
        self.daily_caption = label("Loading…", "Small")
        daily_col.addLayout(_section("Scrobbles per day", self.daily_caption))
        self.daily = BarChart(height=150, label_every=7, unit=" scrobbles")
        daily_col.addWidget(self.daily)
        over.addLayout(daily_col, 1)
        sep = QFrame()
        sep.setObjectName("Divider")
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{T.BORDER}; border:none;")
        over.addWidget(sep)
        grid = QGridLayout()
        grid.setHorizontalSpacing(T.S6)
        grid.setVerticalSpacing(T.S4)
        self.n_total, self.n_week = Stat("scrobbles"), Stat("this week")
        self.n_artists, self.n_tracks = Stat("artists"), Stat("tracks")
        for i, w in enumerate((self.n_total, self.n_week, self.n_artists, self.n_tracks)):
            grid.addWidget(w, i // 2, i % 2)
        gw = QWidget()
        gw.setLayout(grid)
        gw.setMinimumWidth(T.z(260))
        over.addWidget(gw, 0, Qt.AlignVCenter)
        scl.addLayout(over)
        self.lay.addWidget(stats_card)

        cols = QHBoxLayout()
        cols.setSpacing(T.S4)
        left = QVBoxLayout()
        left.setSpacing(T.S4)
        right = QVBoxLayout()
        right.setSpacing(T.S4)
        cols.addLayout(left, 5)
        cols.addLayout(right, 3)
        self.lay.addLayout(cols)

        # Top artists / albums: image grids like Last.fm
        art_card, acl = card()
        self.artist_period = self._period_box("7day", self._load_artists)
        acl.addLayout(_section("Top artists", self.artist_period))
        self.artists = TileGrid()
        acl.addWidget(self.artists)
        self.artists_note = label("", "Muted")
        acl.addWidget(self.artists_note)
        left.addWidget(art_card)

        self.albums_card, alc = card()
        self.album_period = self._period_box("1month", self._load_albums)
        alc.addLayout(_section("Top albums", self.album_period))
        self.albums = TileGrid()
        alc.addWidget(self.albums)
        left.addWidget(self.albums_card)

        sets_card, sl = card()
        sl.addLayout(_section("Your sets", button("Library", None, "Ghost", lambda: app_state().go("library"))))
        self.sets_lay = QVBoxLayout()
        self.sets_lay.setSpacing(0)
        sl.addLayout(self.sets_lay)
        left.addWidget(sets_card)
        left.addStretch(1)

        self.recent_card, rl = card()
        self.recent_link = button("", "external", "Ghost", lambda: open_url(f"https://www.last.fm/user/{self._user}"),
                                  tooltip="Open your Last.fm profile")
        rl.addLayout(_section("Recently scrobbled", self.recent_link))
        self.recent_lay = QVBoxLayout()
        self.recent_lay.setSpacing(0)
        rl.addLayout(self.recent_lay)
        right.addWidget(self.recent_card)

        tracks_card, tcl = card()
        self.track_period = self._period_box("1month", self._load_tracks)
        tcl.addLayout(_section("Top tracks", self.track_period))
        self.tracks = HBars(max_rows=8)
        tcl.addWidget(self.tracks)
        right.addWidget(tracks_card)

        self.hours_card, hl = card()
        hl.addLayout(_section("When you listen", label("last 7 days", "Small")))
        self.hours = BarChart(height=110, label_every=6, unit=" scrobbles")
        hl.addWidget(self.hours)
        right.addWidget(self.hours_card)

        self.per_set_card, pl = card()
        pl.addLayout(_section("Listens per set", label("in Cuelog", "Small")))
        self.per_set = HBars(max_rows=6)
        pl.addWidget(self.per_set)
        right.addWidget(self.per_set_card)
        right.addStretch(1)
        self._user = ""

    # ---- data -------------------------------------------------------------------------------
    def _period_box(self, default: str, on_change) -> QComboBox:
        box = QComboBox()
        for key, text in PERIODS:
            box.addItem(text, key)
        box.setCurrentIndex([k for k, _ in PERIODS].index(default))
        box.setFixedWidth(T.z(124))
        box.activated.connect(lambda _i: on_change())
        return box

    def _load_tops(self):
        self._load_artists()
        self._load_albums()
        self._load_tracks()

    def refresh(self):
        self._gen += 1
        st = repo.stats()
        s = app_state()
        user = s.lastfm_user() if s.creds.lastfm else None
        self._user = user or ""

        clear_layout(self.notice_slot)
        if not user:
            self.notice_slot.addWidget(Banner("Connect Last.fm to see your listening here and send your listens there.",
                                              action=button("Connect", None, None, lambda: app_state().go("settings"))))
        else:
            waiting = [x for x in repo.list_sessions(limit=20000)
                       if x["status"] in ("draft", "partial", "needs_review", "expired") and (x["n_pending"] or x["n_problem"])]
            if waiting:
                n = len(waiting)
                self.notice_slot.addWidget(Banner(
                    f"{n} listen{'s are' if n != 1 else ' is'} waiting to be sent to Last.fm.", "info", "upload",
                    button("Review", None, None, lambda: app_state().go("queue"))))

        # Cuelog's own parts
        clear_layout(self.sets_lay)
        sets = repo.list_sets(sort="recent")
        if not sets:
            self.sets_lay.addWidget(EmptyState("disc", "No sets yet", "Paste a SoundCloud link to save your first DJ set.",
                                               button("Add set", "plus", "Primary", lambda: app_state().go("discover"))))
        for r in sets[:5]:
            self.sets_lay.addWidget(SetListRow(r))
        if len(sets) > 5:
            self.sets_lay.addWidget(button(f"Show all {len(sets)} sets", None, "Ghost", lambda: app_state().go("library")),
                                    0, Qt.AlignLeft)
        per_set = repo.listens_per_set()
        self.per_set.set_data(per_set)
        self.per_set_card.setVisible(bool(per_set))

        if not user:
            self._local_fallback(st)
            return
        self.source.setText(f"Your Last.fm listening as {user}")
        self.recent_link.setVisible(True)
        client = LastfmClient(s.creds)
        gen = self._gen

        def ok(fn):
            return lambda res: fn(res) if gen == self._gen else None

        run(lambda: insights.overview(client, user), ok(self._show_overview), lambda _e: None)
        run(lambda: insights.daily_counts(client, user, 30), ok(self._show_daily),
            lambda _e: self.daily_caption.setText("Couldn't load from Last.fm"))
        run(lambda: insights.recent(client, user), ok(self._show_recent), lambda _e: None)
        run(lambda: insights.hours(client, user, 7), ok(self._show_hours), lambda _e: self.hours_card.setVisible(False))
        self._load_tops()

    def _load_artists(self):
        user = self._user
        if not user:
            return
        creds = app_state().creds
        client = LastfmClient(creds)
        period = self.artist_period.currentData()
        self.artists_note.setText("")

        def show(rows):
            if period != self.artist_period.currentData():
                return
            self.artists.set_items([{"title": a["name"], "plays": a["plays"], "image": "", "url": a["url"]} for a in rows])
            self.artists_note.setText("" if rows else "Nothing scrobbled in this period.")
            self.artists_note.setVisible(not rows)
            for i, a in enumerate(rows):
                run(lambda n=a["name"]: images.artist_image(n, creds),
                    lambda url, i=i, per=period: self.artists.set_image(i, url)
                    if url and per == self.artist_period.currentData() else None, lambda _e: None)
        run(lambda: insights.top_artists_full(client, user, period), show,
            lambda _e: self.artists_note.setText("Couldn't load from Last.fm"))

    def _load_albums(self):
        user = self._user
        if not user:
            return
        client = LastfmClient(app_state().creds)
        period = self.album_period.currentData()

        def show(rows):
            if period == self.album_period.currentData():
                self.albums.set_items([{"title": a["name"], "subtitle": a["artist"], "plays": a["plays"], "image": a["image"],
                                        "url": a["url"]} for a in rows])
                self.albums_card.setVisible(bool(rows))
        run(lambda: insights.top_albums(client, user, period), show, lambda _e: None)

    def _load_tracks(self):
        user = self._user
        if not user:
            return
        client = LastfmClient(app_state().creds)
        period = self.track_period.currentData()

        def show(rows):
            if period == self.track_period.currentData():
                self.tracks.set_data([(f"{t} — {a}", n) for t, a, n in rows])
        run(lambda: insights.top_tracks(client, user, period), show, lambda _e: None)

    # ---- painters ----------------------------------------------------------------------------
    def _show_overview(self, o: dict):
        self.n_total.set(o["playcount"])
        self.n_artists.set(o["artists"])
        self.n_tracks.set(o["tracks"])
        if o.get("registered"):
            since = datetime.fromtimestamp(o["registered"]).strftime("%b %Y")
            self.n_total.set(o["playcount"], f"scrobbles since {since}")

    def _show_daily(self, rows: list[tuple[str, int]]):
        self.daily.set_data([(date.fromisoformat(d).strftime("%d %b"), n, date.fromisoformat(d).strftime("%a %d %b"))
                             for d, n in rows])
        total = sum(n for _, n in rows)
        self.daily_caption.setText(f"{total:,} in 30 days · {round(total / max(1, len(rows))):,} a day on average")
        self.n_week.set(sum(n for _, n in rows[-7:]), "this week")

    def _show_recent(self, rows: list[dict]):
        clear_layout(self.recent_lay)
        self.recent_card.setVisible(bool(rows))
        for r in rows[:7]:
            row = QHBoxLayout()
            row.setContentsMargins(0, 6, 0, 6)
            row.setSpacing(T.S3)
            art = Artwork(48, 3, r.get("art") or "")
            row.addWidget(art)
            if not r.get("art"):
                run(lambda a=r["artist"], t=r["track"]: images.track_image(a, t),
                    lambda url, art=art: art.set_url(url) if url else None, lambda _e: None)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(ElidedLabel(r["track"], None))
            col.addWidget(ElidedLabel(" · ".join(x for x in (r["artist"], r["album"]) if x), "Small"))
            row.addLayout(col, 1)
            when = label("Scrobbling now" if r["now"] else relative_time(r["uts"]), "Small")
            if r["now"]:
                when.setStyleSheet(f"color:{T.ACCENT}; font-size:12px;")
            row.addWidget(when)
            w = QWidget()
            w.setLayout(row)
            self.recent_lay.addWidget(w)

    def _show_hours(self, hours: list[int]):
        self.hours.set_data([(f"{h:02d}", n, f"{h:02d}:00 – {h:02d}:59") for h, n in enumerate(hours)])
        self.hours_card.setVisible(sum(hours) > 0)

    def _local_fallback(self, st: dict):
        """No Last.fm connection: show what Cuelog itself knows."""
        self.source.setText("Showing Cuelog's own numbers. Connect Last.fm to see all your listening.")
        self.recent_link.setVisible(False)
        self.n_total.set(st["accepted"], "scrobbled from Cuelog")
        self.n_week.set(repo.week_summary()["sent"], "this week")
        self.n_artists.set(len(st["top_artists"]), "artists")
        self.n_tracks.set(st["songs"], "songs")
        per_day = repo.scrobbles_per_day(30)
        self._show_daily(per_day)
        creds = app_state().creds
        self.artists.set_items([{"title": a["artist"], "plays": a["n"], "image": ""} for a in st["top_artists"]])
        for i, a in enumerate(st["top_artists"][:8]):
            run(lambda n=a["artist"]: images.artist_image(n, creds),
                lambda url, i=i: self.artists.set_image(i, url) if url else None, lambda _e: None)
        self.albums_card.setVisible(False)
        self.artists_note.setVisible(not st["top_artists"])
        self.artists_note.setText("Log a listen to see who you play most.")
        self.tracks.set_data([])
        self._show_recent([{"track": r["track"], "artist": r["artist"], "album": r["set_title"], "art": r.get("art_url"),
                            "now": False, "uts": r["timestamp"]} for r in repo.recent_scrobbles(7)])
        self._show_hours(repo.scrobbles_by_hour())
