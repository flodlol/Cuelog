"""Live scrobbling while a set plays: follow the playback position, scrobble each song once heard enough.

Used by the in-app player and by the SoundCloud detector (see media.py). Pure logic plus database writes;
no UI. ``update(position, playing)`` is fed every few seconds.
"""
from __future__ import annotations

import time
from typing import Callable

from . import db, jobs, repo, scheduler
from .scheduler import TimestampError


class PlaybackTracker:
    """Which parts of a mix were actually heard: (mix_start, mix_end, wall_clock_start) intervals."""
    JUMP_S = 5.0

    def __init__(self, now=time.time):
        self.now = now
        self.intervals: list[tuple[float, float, float]] = []
        self.current: list[float] | None = None
        self.first_wall: float | None = None
        self.last_wall: float | None = None

    @property
    def playing(self) -> bool:
        return self.current is not None

    def _open(self, pos: float):
        w = self.now()
        self.current = [pos, w, pos]
        self.first_wall = self.first_wall or w

    def _close(self, pos: float | None = None):
        if self.current is None:
            return
        start, wall, last = self.current
        end = last if pos is None else pos
        if end - start >= 1:
            self.intervals.append((start, end, wall))
        self.last_wall = self.now()
        self.current = None

    def play(self, pos: float):
        self._close()
        self._open(pos)

    def progress(self, pos: float):
        if self.current is None:
            self._open(pos)
            return
        last = self.current[2]
        if pos < last - 1 or pos - last > self.JUMP_S:
            self._close(last)
            self._open(pos)
        else:
            self.current[2] = pos

    def pause(self, pos: float | None = None):
        self._close(pos)

    def seek(self, pos: float):
        was = self.playing
        self._close()
        if was:
            self._open(pos)

    def finish(self, pos: float | None = None):
        self._close(pos)

    def all_intervals(self) -> list[tuple[float, float, float]]:
        out = list(self.intervals)
        if self.current and self.current[2] - self.current[0] >= 1:
            out.append((self.current[0], self.current[2], self.current[1]))
        return out

    def heard(self) -> list[tuple[float, float]]:
        spans = sorted((a, b) for a, b, _ in self.all_intervals())
        merged: list[list[float]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1] + 0.5:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return [(a, b) for a, b in merged]

    def heard_seconds(self) -> float:
        return sum(b - a for a, b in self.heard())

    def reset(self):
        self.__init__(self.now)


class LiveSession:
    """Scrobbles a saved set while it plays somewhere (in Cuelog's player or a SoundCloud app)."""

    def __init__(self, set_id: int, *, label: str = "Live", now: Callable[[], float] = time.time,
                 now_playing: Callable[[scheduler.Segment], None] | None = None):
        self.set_id = set_id
        self.s = repo.get_set(set_id)
        self.segs = repo.segments_for_set(set_id)
        self.tracker = PlaybackTracker(now)
        self.label = label
        self.session_id: int | None = None
        self.job_id: int | None = None
        self.done: set[int] = set()
        self.position = 0.0
        self.np_pos: int | None = None
        self.on_now_playing = now_playing
        self.error = ""

    def current(self) -> scheduler.Segment | None:
        return next((sg for sg in self.segs if sg.start_s <= self.position < sg.end_s), None)

    def update(self, position: float, playing: bool) -> int:
        """Feed the latest position. Returns how many new songs were scrobbled."""
        self.position = position
        if playing:
            if self.tracker.playing:
                self.tracker.progress(position)
            else:
                self.tracker.play(position)
            seg = self.current()
            if seg and seg.scrobblable and seg.position != self.np_pos and self.on_now_playing:
                self.np_pos = seg.position
                self.on_now_playing(seg)
        elif self.tracker.playing:
            self.tracker.pause(position)
        return self.step()

    def step(self, final: bool = False) -> int:
        min_frac = float(db.setting("min_segment_fraction"))
        planned = [p for p in scheduler.plan_from_player(self.segs, self.tracker.all_intervals(), min_frac)
                   if p.segment.position not in self.done]
        if not final:
            cur = self.current()
            planned = [p for p in planned if not (cur and p.segment.position == cur.position and self.tracker.playing
                                                  and self.position - cur.start_s < min(cur.length * min_frac, 240))]
        if not planned:
            return 0
        try:
            if self.session_id is None:
                started = int(self.tracker.first_wall or min(p.timestamp for p in planned))
                self.session_id = repo.start_live_session(self.set_id, started, scheduler.listen_span(self.segs))
            ids = repo.add_live_scrobbles(self.session_id, planned)
        except TimestampError as err:
            self.error = str(err)
            return 0
        self.done |= {p.segment.position for p in planned}
        if ids:
            if self.job_id is None:
                self.job_id = jobs.create_job(scrobble_ids=ids, label=f"{self.label}: {self.s['title']}")
            else:
                jobs.append_to_job(self.job_id, ids)
        return len(ids)

    def finish(self) -> int:
        if self.tracker.playing:
            self.tracker.pause()
        n = self.step(final=True)
        if self.session_id is not None:
            complete = len(self.done) >= sum(1 for s in self.segs if s.scrobblable)
            repo.finish_live_session(self.session_id, complete)
        return n
