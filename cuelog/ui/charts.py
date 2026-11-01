"""Small, crisp charts: vertical bars over time and labelled horizontal bars. Hover shows exact values."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from . import theme as T


def _font(px: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont(T.FONT)
    f.setPixelSize(T.z(px))
    f.setWeight(weight)
    return f


class BarChart(QWidget):
    """Vertical bars with a light value grid, sparse x labels and hover tooltips.

    data: list of (label, value, tooltip_label)."""

    def __init__(self, height: int = 150, label_every: int = 7, unit: str = ""):
        super().__init__()
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(height)
        self.data: list[tuple[str, float, str]] = []
        self.label_every = label_every
        self.unit = unit
        self.hover: int | None = None
        self.highlight_last = True
        self.setMouseTracking(True)

    def set_data(self, data):
        self.data = list(data)
        self.hover = None
        self.update()

    def _geometry(self):
        left, bottom, top = 34, 20, 6
        plot = QRectF(left, top, self.width() - left, self.height() - top - bottom)
        return plot

    @staticmethod
    def _nice_max(v: float) -> float:
        if v <= 0:
            return 1
        for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000):
            if v <= step * 4:
                return step * 4 if v > step * 2 else step * 2 if v > step else step
        return v

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.data:
            return
        plot = self._geometry()
        mx = self._nice_max(max(v for _, v, _ in self.data))
        small = _font(11)
        p.setFont(small)
        fm = QFontMetrics(small)
        # grid: 0, half, max
        for frac in (0.0, 0.5, 1.0):
            y = plot.bottom() - plot.height() * frac
            p.setPen(QPen(QColor(T.BORDER), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            val = mx * frac
            text = f"{val:,.0f}"
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(0, y - 8, plot.left() - 6, 16), Qt.AlignRight | Qt.AlignVCenter, text)
        n = len(self.data)
        slot = plot.width() / n
        bw = max(2.0, min(18.0, slot * 0.62))
        for i, (lab, v, _tip) in enumerate(self.data):
            x = plot.left() + slot * i + (slot - bw) / 2
            h = plot.height() * (v / mx) if mx else 0
            if v and h < 2:
                h = 2
            color = QColor(T.ACCENT)
            if self.hover is not None and i != self.hover:
                color.setAlpha(110)
            elif not v:
                color = QColor(T.SURFACE_3)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            r = QRectF(x, plot.bottom() - max(h, 1.5 if not v else h), bw, max(h, 1.5 if not v else h))
            p.drawRoundedRect(r, 1.5, 1.5)
            last_ok = i == n - 1 and (n - 1) % self.label_every >= self.label_every * 0.6
            if self.label_every and (i % self.label_every == 0 or last_ok):
                p.setPen(QColor(T.MUTED))
                tw = fm.horizontalAdvance(lab)
                lx = min(max(plot.left() + slot * i + slot / 2 - tw / 2, plot.left()), self.width() - tw)
                p.drawText(QPointF(lx, self.height() - 4), lab)
        p.end()

    def _index_at(self, x: float) -> int | None:
        plot = self._geometry()
        if not self.data or x < plot.left():
            return None
        i = int((x - plot.left()) / (plot.width() / len(self.data)))
        return i if 0 <= i < len(self.data) else None

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position().x())
        if i != self.hover:
            self.hover = i
            self.update()
        if i is not None:
            lab, v, tip = self.data[i]
            QToolTip.showText(e.globalPosition().toPoint(), f"{tip or lab}\n{v:,.0f}{self.unit}", self)

    def leaveEvent(self, _):
        self.hover = None
        self.update()


class HBars(QWidget):
    """Labelled horizontal bars: name on the left, value on the right, bar underneath."""

    def __init__(self, row_height: int = 30, max_rows: int = 8):
        super().__init__()
        self.rows: list[tuple[str, float]] = []
        self.row_height = row_height
        self.max_rows = max_rows
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, rows):
        self.rows = list(rows)[: self.max_rows]
        self.setFixedHeight(max(1, len(self.rows)) * self.row_height)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.rows:
            return
        mx = max(v for _, v in self.rows) or 1
        name_f, val_f = _font(13), _font(12)
        fm = QFontMetrics(name_f)
        w = self.width()
        for i, (name, v) in enumerate(self.rows):
            y = i * self.row_height
            p.setFont(val_f)
            p.setPen(QColor(T.MUTED))
            val = f"{v:,.0f}"
            vw = QFontMetrics(val_f).horizontalAdvance(val)
            p.drawText(QRectF(w - vw, y, vw, 18), Qt.AlignRight | Qt.AlignVCenter, val)
            p.setFont(name_f)
            p.setPen(QColor(T.TEXT))
            p.drawText(QRectF(0, y, w - vw - 12, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(name, Qt.ElideRight, int(w - vw - 12)))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.SURFACE_3))
            p.drawRoundedRect(QRectF(0, y + 21, w, 3), 1.5, 1.5)
            p.setBrush(QColor(T.ACCENT))
            p.drawRoundedRect(QRectF(0, y + 21, max(3, w * v / mx), 3), 1.5, 1.5)
        p.end()


# ---- Last.fm-style image grid -------------------------------------------------------------------
class TileGrid(QWidget):
    """Square image tiles with a dark gradient and title / subtitle / plays, like Last.fm's top grids.

    Tiles stay around ``tile`` px; wider space shows more columns instead of bigger pictures.
    items: dicts with title, subtitle (optional), plays, image, url."""

    def __init__(self, rows: int = 2, tile: int = 138):
        super().__init__()
        from .widgets import ImageLoader
        self.rows, self.tile = rows, tile
        self.all_items: list[dict] = []
        self.hover: int | None = None
        self._scaled: dict = {}
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.loader = ImageLoader.instance()
        self.loader.loaded.connect(self._image_loaded)

    def _image_loaded(self, url: str):
        if any(it.get("image") == url for it in self.items):
            self.update()

    @property
    def columns(self) -> int:
        return max(2, round(max(1, self.width()) / T.z(self.tile)))

    @property
    def items(self) -> list[dict]:
        return self.all_items[: self.columns * self.rows]

    def set_items(self, items: list[dict]):
        self.all_items = list(items)
        for it in self.all_items[:24]:
            self.loader.get(it.get("image") or "")
        self._fit()
        self.update()

    def set_image(self, index: int, url: str):
        if 0 <= index < len(self.all_items):
            self.all_items[index]["image"] = url
            self.loader.get(url)
            self.update()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        cols = max(2, round(max(1, w) / T.z(self.tile)))
        n = min(len(self.all_items), cols * self.rows)
        rows = max(1, -(-n // cols)) if n else 1
        return int(w / cols) * rows

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(640, self.heightForWidth(640))

    def _fit(self):
        h = self.heightForWidth(self.width())
        if self.minimumHeight() != h or self.maximumHeight() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _cell(self, i: int) -> QRectF:
        cols = self.columns
        size = self.width() / cols
        return QRectF((i % cols) * size, (i // cols) * size, size, size)

    def _pixmap(self, url: str, side: int):
        """Centre-cropped, pre-scaled square; scaling a 640px photo on every paint is what made scrolling slow."""
        key = (url, side)
        pm = self._scaled.get(key)
        if pm is None:
            src = self.loader.pixmaps.get(url)
            if not src or src.isNull():
                return None
            dpr = self.devicePixelRatioF()
            edge = min(src.width(), src.height())
            crop = src.copy(int((src.width() - edge) / 2), int((src.height() - edge) / 3), edge, edge)
            pm = crop.scaled(int(side * dpr), int(side * dpr), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            if len(self._scaled) > 80:
                self._scaled.clear()
            self._scaled[key] = pm
        return pm

    def paintEvent(self, _):
        from PySide6.QtGui import QLinearGradient, QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 4, 4)
        p.setClipPath(clip)
        small = _font(11)
        fm_small = QFontMetrics(small)
        big = _font(13, QFont.DemiBold)
        fm_big = QFontMetrics(big)
        for i, it in enumerate(self.items):
            r = self._cell(i)
            pm = self._pixmap(it.get("image") or "", int(r.width()) + 1)
            if pm is not None:
                p.drawPixmap(r.topLeft(), pm)
            else:
                p.fillRect(r, QColor(T.SURFACE_3))
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            g.setColorAt(0.4, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, 220))
            p.fillRect(r, g)
            if i == self.hover:
                p.fillRect(r, QColor(255, 255, 255, 22))
            pad = max(8.0, r.width() * 0.07)
            width = int(r.width() - 2 * pad)
            y = r.bottom() - pad
            p.setFont(small)
            p.setPen(QColor("#d8d8dc"))
            p.drawText(QPointF(r.left() + pad, y), f"{it.get('plays', 0):,} plays")
            y -= fm_small.height()
            if it.get("subtitle"):
                p.drawText(QPointF(r.left() + pad, y), fm_small.elidedText(it["subtitle"], Qt.ElideRight, width))
                y -= fm_small.height()
            p.setFont(big)
            p.setPen(QColor("#ffffff"))
            p.drawText(QPointF(r.left() + pad, y), fm_big.elidedText(it.get("title", ""), Qt.ElideRight, width))
        p.end()

    def _index(self, pos) -> int | None:
        for i in range(len(self.items)):
            if self._cell(i).contains(pos):
                return i
        return None

    def mouseMoveEvent(self, e):
        i = self._index(e.position())
        if i != self.hover:
            self.hover = i
            self.setCursor(Qt.PointingHandCursor if i is not None else Qt.ArrowCursor)
            it = self.items[i] if i is not None else None
            self.setToolTip(f"{it['title']}" + (f" — {it['subtitle']}" if it.get("subtitle") else "")
                            + f"\n{it.get('plays', 0):,} plays" if it else "")
            self.update()

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, e):
        i = self._index(e.position())
        if i is not None and self.items[i].get("url"):
            from .widgets import open_url
            open_url(self.items[i]["url"])


# ---- reports --------------------------------------------------------------------------------------
class GroupedBars(QWidget):
    """Two series side by side per category (this year vs last year), with a legend."""

    def __init__(self, height: int = 220):
        super().__init__()
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cats: list[str] = []
        self.series: list[tuple[str, list[float]]] = []
        self.setMouseTracking(True)
        self.hover: int | None = None

    def set_data(self, cats, series):
        self.cats, self.series = list(cats), list(series)
        self.update()

    @staticmethod
    def _colors():
        a = QColor(T.ACCENT)
        b = QColor(T.ACCENT)
        b.setAlpha(85)
        return [a, b]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.cats or not self.series:
            return
        colors = self._colors()
        small = _font(11)
        p.setFont(small)
        x = 0.0
        for (name, _vals), c in zip(self.series, colors):
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, 4, 10, 10), 2, 2)
            p.setPen(QColor(T.TEXT_2))
            p.drawText(QPointF(x + 16, 13), name)
            x += 28 + QFontMetrics(small).horizontalAdvance(name)
        plot = QRectF(40, 30, self.width() - 40, self.height() - 52)
        mx = BarChart._nice_max(max([max(v) if v else 0 for _, v in self.series] + [1]))
        for frac in (0, 0.5, 1):
            y = plot.bottom() - plot.height() * frac
            p.setPen(QPen(QColor(T.BORDER), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(0, y - 8, plot.left() - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{mx * frac:,.0f}")
        slot = plot.width() / len(self.cats)
        bw = min(22.0, slot * 0.32)
        for i, cat in enumerate(self.cats):
            cx = plot.left() + slot * i + slot / 2
            for k, ((_n, vals), c) in enumerate(zip(self.series, colors)):
                v = vals[i] if i < len(vals) else 0
                h = plot.height() * v / mx
                col = QColor(c)
                if self.hover is not None and self.hover != i:
                    col.setAlpha(int(col.alpha() * 0.45))
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawRoundedRect(QRectF(cx - bw - 1 + k * (bw + 2), plot.bottom() - h, bw, h), 2, 2)
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(cx - slot / 2, plot.bottom() + 6, slot, 16), Qt.AlignCenter, cat)
        p.end()

    def mouseMoveEvent(self, e):
        if not self.cats:
            return
        left = 40
        slot = (self.width() - left) / len(self.cats)
        x = e.position().x()
        i = int((x - left) / slot) if x > left else None
        i = i if i is not None and 0 <= i < len(self.cats) else None
        if i != self.hover:
            self.hover = i
            self.update()
        if i is not None:
            lines = [self.cats[i]] + [f"{n}: {v[i]:,.0f}" for n, v in self.series if i < len(v)]
            QToolTip.showText(e.globalPosition().toPoint(), "\n".join(lines), self)

    def leaveEvent(self, _):
        self.hover = None
        self.update()


def _wedge(c: QPointF, r0: float, r1: float, start_deg: float, span_deg: float):
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    outer = QRectF(c.x() - r1, c.y() - r1, 2 * r1, 2 * r1)
    inner = QRectF(c.x() - r0, c.y() - r0, 2 * r0, 2 * r0)
    path.arcMoveTo(outer, start_deg)
    path.arcTo(outer, start_deg, span_deg)
    path.arcTo(inner, start_deg + span_deg, -span_deg)
    path.closeSubpath()
    return path


class ListeningClock(QWidget):
    """24 wedges around a clock; each wedge's length shows how much you scrobble in that hour."""

    def __init__(self, size: int = 240):
        super().__init__()
        self.setFixedSize(size, size)
        self.counts = [0] * 24
        self.setMouseTracking(True)

    def set_data(self, counts):
        self.counts = (list(counts) + [0] * 24)[:24]
        self.update()

    def paintEvent(self, _):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        outer = min(self.width(), self.height()) / 2 - 2
        inner = outer * 0.3
        mx = max(self.counts) or 1
        for h in range(24):
            start = 90 - h * 15            # hour 0 at the top, clockwise
            p.setPen(QPen(QColor(T.BG), 1.5))
            p.setBrush(QColor(T.SURFACE_2))
            p.drawPath(_wedge(c, inner, outer, start, -15))
            if self.counts[h]:
                t = self.counts[h] / mx
                col = QColor(T.ACCENT)
                col.setAlpha(int(80 + 175 * t))
                p.setBrush(col)
                p.drawPath(_wedge(c, inner, inner + (outer - inner) * t, start, -15))
        p.setPen(QColor(T.MUTED))
        p.setFont(_font(11))
        for h, ang in ((0, -90), (6, 0), (12, 90), (18, 180)):
            r = inner * 0.62
            x = c.x() + r * math.cos(math.radians(ang))
            y = c.y() + r * math.sin(math.radians(ang))
            p.drawText(QRectF(x - 12, y - 8, 24, 16), Qt.AlignCenter, f"{h:02d}")
        p.end()

    def mouseMoveEvent(self, e):
        import math
        c = QPointF(self.width() / 2, self.height() / 2)
        ang = (math.degrees(math.atan2(e.position().y() - c.y(), e.position().x() - c.x())) + 90) % 360
        h = int(ang // 15) % 24
        QToolTip.showText(e.globalPosition().toPoint(), f"{h:02d}:00 – {h:02d}:59\n{self.counts[h]:,} scrobbles", self)


STREAM_PALETTE = ["#c792ff", "#5ad1b3", "#4ea1ff", "#6c3fd1", "#ff8a5c", "#2f7f73", "#9fb6ff", "#ff5c8a"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class StreamGraph(QWidget):
    """Stacked, smoothed areas around a centred baseline (a stream graph), labelled where each is widest."""

    def __init__(self, height: int = 260):
        super().__init__()
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.labels: list[str] = []
        self.months: list[list[float]] = []
        self.setMouseTracking(True)

    def set_data(self, tags: list[str], months: list[list[float]]):
        self.labels, self.months = list(tags), [list(m) for m in months]
        self.update()

    def _layers(self):
        n = len(self.months)
        plot = QRectF(10, 8, self.width() - 20, self.height() - 34)
        totals = [sum(m) for m in self.months]
        scale = plot.height() / (max(totals) or 1)
        xs = [plot.left() + plot.width() * i / max(1, n - 1) for i in range(n)]
        mid = plot.top() + plot.height() / 2
        base = [mid - t * scale / 2 for t in totals]
        layers = []
        for k in range(len(self.labels)):
            lower = [base[i] + sum(self.months[i][:k]) * scale for i in range(n)]
            upper = [lower[i] + self.months[i][k] * scale for i in range(n)]
            layers.append((lower, upper))
        return xs, layers

    def paintEvent(self, _):
        from PySide6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.labels or not self.months or not any(sum(m) for m in self.months):
            p.setPen(QColor(T.MUTED))
            p.setFont(_font(12))
            p.drawText(self.rect(), Qt.AlignCenter, "No tag data for this year yet")
            return
        xs, layers = self._layers()
        n = len(xs)

        def trace(path, pts, move):
            for i, (x, y) in enumerate(pts):
                if i == 0:
                    (path.moveTo if move else path.lineTo)(x, y)
                    continue
                px, py = pts[i - 1]
                cx = (px + x) / 2
                path.cubicTo(cx, py, cx, y, x, y)

        for k, (lower, upper) in enumerate(layers):
            path = QPainterPath()
            trace(path, list(zip(xs, upper)), True)
            trace(path, list(zip(reversed(xs), reversed(lower))), False)
            path.closeSubpath()
            p.setPen(QPen(QColor(0, 0, 0, 90), 0.8))
            p.setBrush(QColor(STREAM_PALETTE[k % len(STREAM_PALETTE)]))
            p.drawPath(path)
        bold = _font(12, QFont.DemiBold)
        p.setFont(bold)
        fm = QFontMetrics(bold)
        for k, (lower, upper) in enumerate(layers):
            i = max(range(n), key=lambda j: upper[j] - lower[j])
            if upper[i] - lower[i] < fm.height() + 2:
                continue
            text = self.labels[k]
            tw = fm.horizontalAdvance(text)
            x = min(max(xs[i] - tw / 2, xs[0] + 2), xs[-1] - tw - 2)
            p.setPen(QColor("#ffffff"))
            p.drawText(QPointF(x, (upper[i] + lower[i]) / 2 + fm.ascent() / 2 - 1), text)
        p.setFont(_font(11))
        p.setPen(QColor(T.MUTED))
        for i, x in enumerate(xs):
            p.drawText(QRectF(x - 20, self.height() - 18, 40, 16), Qt.AlignCenter, MONTHS[i] if i < 12 else str(i + 1))
        p.end()

    def mouseMoveEvent(self, e):
        if not self.months:
            return
        n = len(self.months)
        i = max(0, min(n - 1, round((e.position().x() - 10) / max(1, self.width() - 20) * (n - 1))))
        total = sum(self.months[i]) or 1
        rows = sorted(zip(self.labels, self.months[i]), key=lambda r: -r[1])
        lines = [MONTHS[i] if i < 12 else str(i + 1)] + [f"{t}: {round(100 * v / total)}%" for t, v in rows if v]
        QToolTip.showText(e.globalPosition().toPoint(), "\n".join(lines), self)
