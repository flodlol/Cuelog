"""Visual DJ-set timeline with draggable track boundaries, heard-segment overlay and playhead."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from . import theme as T
from .widgets import fmt_duration

PALETTE = ["#2b2b31", "#34343b"]


class Timeline(QWidget):
    boundaryMoved = Signal(int, int)    # set_track_id, new start (s)
    blockClicked = Signal(int)          # index
    seekRequested = Signal(int)         # seconds

    def __init__(self, editable: bool = True, height: int = 92):
        super().__init__()
        self.editable = editable
        self.setMinimumHeight(height)
        self.setMouseTracking(True)
        self.blocks: list[dict] = []    # {id, start, end, title, status}
        self.duration = 1
        self.heard: list[tuple[float, float]] = []
        self.playhead: float | None = None
        self.selected: int | None = None
        self._drag: int | None = None
        self._drag_x = 0.0
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Set timeline")

    def set_data(self, blocks: list[dict], duration: int):
        self.blocks = blocks
        self.duration = max(1, duration or (blocks[-1]["end"] if blocks else 1))
        self.update()

    def set_heard(self, intervals, playhead=None):
        self.heard = intervals
        self.playhead = playhead
        self.update()

    # geometry
    def _bar(self) -> QRectF:
        return QRectF(0, 18, self.width(), self.height() - 40)

    def _x(self, s: float) -> float:
        return s / self.duration * self.width()

    def _s(self, x: float) -> int:
        return int(max(0, min(self.duration, x / max(1, self.width()) * self.duration)))

    def _boundary_at(self, x: float) -> int | None:
        if not self.editable:
            return None
        for i in range(1, len(self.blocks)):
            if abs(self._x(self.blocks[i]["start"]) - x) <= 5:
                return i
        return None

    def _block_at(self, x: float) -> int | None:
        s = self._s(x)
        for i, b in enumerate(self.blocks):
            if b["start"] <= s < b["end"]:
                return i
        return None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bar = self._bar()
        small = QFont(T.FONT)
        small.setPixelSize(T.z(10))
        fm = QFontMetrics(small)
        p.setFont(small)
        # ruler
        p.setPen(QColor(T.MUTED))
        step = 300 if self.duration > 2400 else 120 if self.duration > 600 else 60
        t = 0
        while t <= self.duration:
            x = self._x(t)
            p.drawLine(QPointF(x, 12), QPointF(x, 16))
            if x < self.width() - 30:
                p.drawText(QPointF(x + 2, 10), fmt_duration(t) if t else "0:00")
            t += step
        # blocks
        for i, b in enumerate(self.blocks):
            r = QRectF(self._x(b["start"]), bar.top(), self._x(b["end"]) - self._x(b["start"]), bar.height())
            color = QColor(PALETTE[i % 2])
            if b.get("status") in ("unmatched", "ambiguous"):
                color = QColor(T.AMBER_SOFT)
            if i == self.selected:
                color = QColor(T.ACCENT_SOFT)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(r.adjusted(0.5, 0, -0.5, 0), 2, 2)
            if r.width() > 22:
                p.setPen(QColor(T.TEXT if i == self.selected else T.TEXT_2))
                text = fm.elidedText(f"{b['pos']}. {b['title']}", Qt.ElideRight, int(r.width() - 10))
                p.drawText(r.adjusted(5, 4, -4, -4), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, text)
            if b.get("estimated") and r.width() > 30:
                p.setPen(QColor(T.MUTED))
                p.drawText(r.adjusted(5, 0, -4, -4), Qt.AlignLeft | Qt.AlignBottom, "est.")
        # heard overlay
        for a, z in self.heard:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.ACCENT))
            p.drawRoundedRect(QRectF(self._x(a), bar.bottom() + 4, max(2, self._x(z) - self._x(a)), 4), 2, 2)
        # boundaries
        if self.editable:
            pen = QPen(QColor(T.BORDER_STRONG), 2)
            p.setPen(pen)
            for b in self.blocks[1:]:
                x = self._x(b["start"])
                p.drawLine(QPointF(x, bar.top() + 6), QPointF(x, bar.bottom() - 6))
        if self._drag is not None:
            p.setPen(QPen(QColor(T.ACCENT), 2))
            p.drawLine(QPointF(self._drag_x, bar.top() - 2), QPointF(self._drag_x, bar.bottom() + 2))
            p.setPen(QColor(T.ACCENT))
            p.drawText(QPointF(min(self.width() - 40, self._drag_x + 4), bar.bottom() + 14), fmt_duration(self._s(self._drag_x)))
        if self.playhead is not None:
            x = self._x(self.playhead)
            p.setPen(QPen(QColor(T.TEXT), 2))
            p.drawLine(QPointF(x, bar.top() - 4), QPointF(x, bar.bottom() + 8))
        p.end()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag is not None:
            i = self._drag
            lo = self.blocks[i - 1]["start"]
            hi = self.blocks[i + 1]["start"] if i + 1 < len(self.blocks) else self.duration - 1
            s = max(lo, min(hi, self._s(x)))
            self._drag_x = self._x(s)
            self.update()
            return
        if self._boundary_at(x) is not None:
            self.setCursor(Qt.SplitHCursor)
        else:
            self.setCursor(Qt.PointingHandCursor)
        i = self._block_at(x)
        if i is not None:
            b = self.blocks[i]
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"{b['pos']}. {b['title']}\n{fmt_duration(b['start'])} – {fmt_duration(b['end'])}"
                              f" ({fmt_duration(b['end'] - b['start'])}){'  · estimated' if b.get('estimated') else ''}", self)

    def mousePressEvent(self, e):
        x = e.position().x()
        bi = self._boundary_at(x)
        if bi is not None and e.button() == Qt.LeftButton:
            self._drag = bi
            self._drag_x = x
            return
        i = self._block_at(x)
        if i is not None:
            self.selected = i
            self.update()
            self.blockClicked.emit(i)
            if e.modifiers() & Qt.ControlModifier or not self.editable:
                self.seekRequested.emit(self._s(x))

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            i = self._drag
            self._drag = None
            new = self._s(self._drag_x)
            if new != self.blocks[i]["start"] and self.blocks[i].get("id"):
                self.boundaryMoved.emit(self.blocks[i]["id"], new)
            self.update()

    def keyPressEvent(self, e):
        if self.selected is None or not self.blocks:
            return super().keyPressEvent(e)
        if e.key() in (Qt.Key_Left, Qt.Key_Right):
            i = max(0, min(len(self.blocks) - 1, self.selected + (1 if e.key() == Qt.Key_Right else -1)))
            self.selected = i
            self.blockClicked.emit(i)
            self.update()
        else:
            super().keyPressEvent(e)
