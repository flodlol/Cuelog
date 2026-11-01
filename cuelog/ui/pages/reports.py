"""Reports: your Last.fm history by year, like Last.fm's reports (weekday chart, listening clock, top tags)."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QIcon, QTransform
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from ... import history, images
from ...lastfm import LastfmClient
from .. import theme as T
from ..charts import GroupedBars, ListeningClock, StreamGraph, TileGrid
from ..state import app_state
from ..widgets import Banner, Segmented, button, card, clear_layout, label, page_header, scroll_page
from ..workers import run

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LONG_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class HistorySync(QThread):
    """Keeps the local copy of the Last.fm history up to date (first a full download, then only new scrobbles)."""
    progress = Signal(int, int)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._wake = False

    def stop(self):
        self._stop = True

    def wake(self):
        self._wake = True

    def run(self):
        import time
        from ... import db
        try:
            while not self._stop:
                st = app_state()
                user = st.lastfm_user() if st.creds.lastfm else None
                if user:
                    try:
                        res = history.sync(LastfmClient(st.creds), user, progress=lambda a, b: self.progress.emit(a, b),
                                           stop=lambda: self._stop)
                        self.done.emit(res)
                    except Exception as err:     # network etc.: try again later
                        self.failed.emit(str(err))
                end = time.time() + 600
                while time.time() < end and not self._stop and not self._wake:
                    time.sleep(0.5)
                self._wake = False
        finally:
            db.close()


class Stat(QWidget):
    def __init__(self, caption: str):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.caption = label(caption, "Small")
        self.value = label("—", "Display")
        v.addWidget(self.caption)
        v.addWidget(self.value)


class ReportsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.year = date.today().year
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, _, lay = scroll_page()
        outer.addWidget(area)

        # Year picker: ‹ [2023 | 2024 | 2025 | 2026] ›
        self.year_bar = QWidget()
        self.year_lay = QHBoxLayout(self.year_bar)
        self.year_lay.setContentsMargins(0, 0, 0, 0)
        self.year_lay.setSpacing(T.S1)
        self._years: list[int] = []
        self.year_seg = None
        self.sub = label("", "Muted")
        head, _ = page_header("Reports", self.sub, self.year_bar)
        lay.addLayout(head)

        self.sync_row = QWidget()
        sr = QHBoxLayout(self.sync_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(T.S3)
        self.sync_text = label("", "Small")
        self.sync_bar = QProgressBar()
        self.sync_bar.setFixedWidth(220)
        sr.addWidget(self.sync_text)
        sr.addWidget(self.sync_bar)
        sr.addStretch(1)
        lay.addWidget(self.sync_row)
        self.banner_slot = QVBoxLayout()
        lay.addLayout(self.banner_slot)

        totals, tl = card()
        row = QHBoxLayout()
        row.setSpacing(T.S6 + T.S4)
        self.t_scrobbles, self.t_artists, self.t_tracks, self.t_days = (Stat("Scrobbles"), Stat("Artists"), Stat("Tracks"),
                                                                        Stat("Days you listened"))
        for w in (self.t_scrobbles, self.t_artists, self.t_tracks, self.t_days):
            row.addWidget(w)
        row.addStretch(1)
        tl.addLayout(row)
        lay.addWidget(totals)

        pair = QHBoxLayout()
        pair.setSpacing(T.S4)
        week, wl = card()
        wl.addWidget(label("Weekly scrobbles", "H2"))
        wrow = QHBoxLayout()
        wrow.setSpacing(T.S5)
        self.weekdays = GroupedBars(240)
        wrow.addWidget(self.weekdays, 1)
        side = QVBoxLayout()
        side.setSpacing(T.S4)
        self.busy_day = Stat("Busiest day")
        self.busy_day_n = Stat("Scrobbles on your busiest day")
        side.addStretch(1)
        side.addWidget(self.busy_day)
        side.addWidget(self.busy_day_n)
        side.addStretch(1)
        wrow.addLayout(side)
        wl.addLayout(wrow)
        pair.addWidget(week, 3)

        clock, cl = card()
        cl.addWidget(label("Listening clock", "H2"))
        crow = QHBoxLayout()
        crow.setSpacing(T.S5)
        self.clock = ListeningClock(T.z(230))
        crow.addWidget(self.clock)
        side2 = QVBoxLayout()
        side2.setSpacing(T.S4)
        self.busy_hour = Stat("Busiest hour")
        self.busy_hour_n = Stat("Scrobbles in that hour")
        side2.addStretch(1)
        side2.addWidget(self.busy_hour)
        side2.addWidget(self.busy_hour_n)
        side2.addStretch(1)
        crow.addLayout(side2)
        crow.addStretch(1)
        cl.addLayout(crow)
        pair.addWidget(clock, 2)
        lay.addLayout(pair)

        tags, tgl = card()
        tgl.addWidget(label("Top tags", "H2"))
        tgl.addWidget(label("What your listening was made of, month by month (from your top artists' Last.fm tags).", "Small"))
        self.stream = StreamGraph(T.z(270))
        tgl.addWidget(self.stream)
        self.tags_note = label("", "Small")
        tgl.addWidget(self.tags_note)
        lay.addWidget(tags)

        months, ml = card()
        ml.addWidget(label("Scrobbles per month", "H2"))
        self.months = GroupedBars(220)
        ml.addWidget(self.months)
        lay.addWidget(months)

        artists, al = card()
        al.addWidget(label("Top artists of the year", "H2"))
        self.grid = TileGrid(rows=2)
        al.addWidget(self.grid)
        lay.addWidget(artists)
        lay.addStretch(1)
        self._gen = 0

    # ---- sync ---------------------------------------------------------------------------------
    def on_sync_progress(self, page: int, total: int):
        self.sync_row.setVisible(True)
        self.sync_bar.setVisible(True)
        self.sync_bar.setMaximum(max(1, total))
        self.sync_bar.setValue(page)
        self.sync_text.setText(f"Downloading your Last.fm history… {page:,} of {total:,} pages. Reports fill in as it goes.")

    def on_sync_done(self, st: dict):
        self._sync_status(st)
        if self.isVisible():
            self.refresh()

    def _sync_status(self, st: dict | None = None):
        st = st or history.status()
        if st["complete"]:
            self.sync_bar.setVisible(False)
            self.sync_text.setText(f"{st['count']:,} scrobbles in your local history · kept up to date automatically")
        elif st["pages"]:
            self.on_sync_progress(st["page"], st["pages"])
        else:
            self.sync_bar.setVisible(False)
            self.sync_text.setText("Your Last.fm history downloads in the background the first time (a few minutes).")

    # ---- data ---------------------------------------------------------------------------------
    def _pick_year(self, year):
        if year and int(year) != self.year:
            self.year = int(year)
            self.refresh()

    def _step_year(self, d: int):
        if self.year + d in self._years:
            self._pick_year(self.year + d)

    def _build_years(self, years: list[int]):
        if years != self._years:
            self._years = years
            clear_layout(self.year_lay)
            self.prev_btn = button("", "back", "Ghost", lambda: self._step_year(-1), tooltip="Previous year")
            self.year_seg = Segmented([(str(y), str(y)) for y in sorted(years)[-8:]], lambda k: self._pick_year(int(k)))
            self.next_btn = button("", "back", "Ghost", lambda: self._step_year(+1), tooltip="Next year")
            self.next_btn.setIcon(QIcon(T.icon_pixmap("back", T.TEXT_2, T.z(16)).transformed(QTransform().scale(-1, 1))))
            for w in (self.prev_btn, self.year_seg, self.next_btn):
                self.year_lay.addWidget(w)
        if self.year_seg and str(self.year) in self.year_seg.buttons:
            self.year_seg.set(str(self.year))
        self.prev_btn.setEnabled(self.year - 1 in years)
        self.next_btn.setEnabled(self.year + 1 in years)

    def refresh(self):
        self._gen += 1
        gen = self._gen
        st = app_state()
        user = st.lastfm_user() if st.creds.lastfm else None
        clear_layout(self.banner_slot)
        if not user:
            self.banner_slot.addWidget(Banner("Connect Last.fm to see reports of your listening.",
                                              action=button("Connect", None, None, lambda: app_state().go("settings"))))
        self._sync_status()
        y = self.year

        def gather():
            # 100k+ rows: these queries run off the UI thread so switching pages and years stays instant
            years = history.years() or [date.today().year]
            yy = y if y in years else years[0]
            return {"years": years, "year": yy, "complete": history.status()["complete"],
                    "totals": history.year_totals(yy),
                    "wd": (history.weekday_counts(yy), history.weekday_counts(yy - 1)),
                    "busiest": history.busiest_day(yy), "hours": history.hour_counts(yy),
                    "months": (history.month_counts(yy), history.month_counts(yy - 1)),
                    "tops": history.top_artists(yy, 24)}
        run(gather, lambda d: self._show(d, gen, user), lambda _e: None)

    def _show(self, d: dict, gen: int, user):
        if gen != self._gen:
            return
        st = app_state()
        self.year = y = d["year"]
        self._build_years(d["years"])
        self.sub.setText(f"Your {y} on Last.fm" + ("" if d["complete"] else " (still downloading)"))

        t = d["totals"]
        self.t_scrobbles.value.setText(f"{t['scrobbles']:,}")
        self.t_artists.value.setText(f"{t['artists']:,}")
        self.t_tracks.value.setText(f"{t['tracks']:,}")
        self.t_days.value.setText(f"{t['days']:,}")

        this, last = d["wd"]
        self.weekdays.set_data(DAYS, [(str(y), this), (str(y - 1), last)])
        self.busy_day.value.setText(LONG_DAYS[max(range(7), key=lambda i: this[i])] if any(this) else "—")
        bd = d["busiest"]
        self.busy_day_n.value.setText(f"{bd[1]:,}" if bd else "—")
        self.busy_day_n.caption.setText(f"Busiest date: {datetime.fromisoformat(bd[0]).strftime('%a %d %b')}" if bd
                                        else "Scrobbles on your busiest day")
        hours = d["hours"]
        self.clock.set_data(hours)
        if any(hours):
            h = max(range(24), key=lambda i: hours[i])
            self.busy_hour.value.setText(datetime(2000, 1, 1, h).strftime("%I:00 %p").lstrip("0"))
            self.busy_hour_n.value.setText(f"{hours[h]:,}")
        else:
            self.busy_hour.value.setText("—")
            self.busy_hour_n.value.setText("—")
        self.months.set_data(MONTHS, [(str(y), d["months"][0]), (str(y - 1), d["months"][1])])

        tops = d["tops"]
        self.grid.set_items([{"title": n, "plays": c, "image": "", "url": f"https://www.last.fm/music/{n.replace(' ', '+')}"}
                             for n, c in tops])
        creds = st.creds
        for i, (name, _c) in enumerate(tops):
            run(lambda n=name: images.artist_image(n, creds),
                lambda url, i=i: self.grid.set_image(i, url) if gen == self._gen and url else None, lambda _e: None)

        if user and t["scrobbles"]:
            self.tags_note.setText("Loading tags…")
            client = LastfmClient(creds)
            run(lambda: history.tags_by_month(client, y),
                lambda res: (self.stream.set_data(res["tags"], res["months"]), self.tags_note.setText(""))
                if gen == self._gen else None,
                lambda _e: self.tags_note.setText("Couldn't load tags from Last.fm."))
        else:
            self.stream.set_data([], [])
            self.tags_note.setText("")
