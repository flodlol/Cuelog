"""Reusable widgets: async artwork, badges, cards, flow layout, toasts, empty states."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation,
                            QUrl, Signal)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLayout, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .. import USER_AGENT, config
from ..urls import safe_external_url
from . import theme as T


# ---- images ------------------------------------------------------------------------------------
class ImageLoader(QObject):
    loaded = Signal(str)

    _instance: "ImageLoader | None" = None

    @classmethod
    def instance(cls) -> "ImageLoader":
        if cls._instance is None:
            cls._instance = ImageLoader()
        return cls._instance

    def __init__(self):
        super().__init__()
        self.nam = QNetworkAccessManager(self)
        cache = QNetworkDiskCache(self)
        cache.setCacheDirectory(str(config.cache_dir() / "images"))
        cache.setMaximumCacheSize(300 * 1024 * 1024)
        self.nam.setCache(cache)
        self.pixmaps: dict[str, QPixmap] = {}
        self.failed: set[str] = set()
        self.pending: set[str] = set()

    def get(self, url: str) -> QPixmap | None:
        if not url:
            return None
        if url in self.pixmaps:
            return self.pixmaps[url]
        if url in self.failed or url in self.pending or not url.startswith("https://"):
            return None
        self.pending.add(url)
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", USER_AGENT.encode())
        req.setAttribute(QNetworkRequest.CacheLoadControlAttribute, QNetworkRequest.PreferCache)
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
        reply = self.nam.get(req)
        reply.finished.connect(lambda r=reply, u=url: self._done(r, u))
        return None

    def _done(self, reply: QNetworkReply, url: str):
        self.pending.discard(url)
        pm = QPixmap()
        if reply.error() == QNetworkReply.NoError and pm.loadFromData(reply.read(10 * 1024 * 1024)):
            self.pixmaps[url] = pm
        else:
            self.failed.add(url)
        reply.deleteLater()
        self.loaded.emit(url)


class Artwork(QWidget):
    """Rounded artwork with a tasteful placeholder while loading / when missing."""

    def __init__(self, size: int = 56, radius: int = 8, url: str = "", parent=None, icon: str = "music"):
        super().__init__(parent)
        size = T.z(size)
        radius = T.z(radius)
        self._size = size
        # Circles (avatars) stay round; everything else gets a small, consistent corner.
        self.radius = radius if radius * 2 >= size else min(radius, 3 if size < 96 else 4)
        self.url = ""
        self.icon_name = icon
        self._cache = None       # (url, w, h, dpr, pixmap): scaling every paint was a big cost in lists
        self.setFixedSize(size, size)
        ImageLoader.instance().loaded.connect(self._on_loaded)
        self.set_url(url)

    def set_url(self, url: str | None):
        self.url = url or ""
        ImageLoader.instance().get(self.url)
        self.update()

    def set_size(self, size: int):
        if size != self._size:
            self._size = size
            self.setFixedSize(size, size)

    def _on_loaded(self, url: str):
        if url == self.url:
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        p.setClipPath(path)
        pm = ImageLoader.instance().pixmaps.get(self.url)
        if pm and not pm.isNull():
            dpr = self.devicePixelRatioF()
            key = (self.url, self.width(), self.height(), dpr)
            if self._cache and self._cache[0] == key:
                scaled = self._cache[1]
            else:
                scaled = pm.scaled(self.size() * dpr, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                self._cache = (key, scaled)
            sw, sh = scaled.width() / scaled.devicePixelRatio(), scaled.height() / scaled.devicePixelRatio()
            p.drawPixmap(QPoint(int((self.width() - sw) / 2), int((self.height() - sh) / 2)), scaled)
        else:
            p.fillRect(r, QColor(T.SURFACE_3))
            s = max(14, int(self._size * 0.34))
            ip = T.icon_pixmap(self.icon_name, "#55555d", s)
            p.drawPixmap(int((self.width() - s) / 2), int((self.height() - s) / 2), ip)
        p.end()


# ---- small building blocks ---------------------------------------------------------------------
def label(text: str = "", obj: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


class IconTextButton(QPushButton):
    """QPushButton whose label keeps a comfortable gap from its icon (Qt's default gap is ~4 px)."""

    def setText(self, text: str):
        t = (text or "").lstrip()
        super().setText(f"  {t}" if t and not self.icon().isNull() else t)

    def setIcon(self, icon):
        super().setIcon(icon)
        self.setText(self.text())


class StatusLabel(QLabel):
    """A one-line status message that takes no space while it is empty."""

    def __init__(self, obj: str = "Muted"):
        super().__init__()
        self.setObjectName(obj)
        self.setWordWrap(True)
        self.hide()

    def setText(self, text: str):
        super().setText(text)
        self.setVisible(bool(text))


def button(text: str = "", icon: str | None = None, kind: str | None = None, on_click: Callable | None = None,
           tooltip: str | None = None, icon_color: str | None = None) -> QPushButton:
    b = IconTextButton(text)
    if kind:
        b.setObjectName(kind)
    if icon and not text:
        b.setProperty("iconOnly", True)
    if icon:
        color = icon_color or ("#120700" if kind == "Primary" else T.RED if kind == "Danger" else T.TEXT_2)
        b.setIcon(T.icon(icon, color, 16))
        b.setIconSize(QSize(T.z(16), T.z(16)))
    if on_click:
        b.clicked.connect(lambda *_: on_click())
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def icon_button(icon: str, tooltip: str, on_click: Callable | None = None, color: str = T.TEXT_2, size: int = 18,
                fill: bool = False) -> QToolButton:
    b = QToolButton()
    b.setIcon(T.icon(icon, color, size, fill))
    b.setIconSize(QSize(T.z(size), T.z(size)))
    b.setToolTip(tooltip)
    b.setAccessibleName(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    if on_click:
        b.clicked.connect(lambda *_: on_click())
    return b


def icon_label(name: str, color: str = T.MUTED, size: int = 16) -> QLabel:
    lb = QLabel()
    lb.setPixmap(T.icon_pixmap(name, color, size))
    lb.setFixedSize(size, size)
    return lb


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    return f


def card(obj: str = "Card") -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName(obj)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(T.S5, T.S5 - 4, T.S5, T.S5 - 4)
    lay.setSpacing(T.S3)
    return f, lay


def page_header(title: str, subtitle: str | QLabel | None = None, *actions: QWidget) -> tuple[QHBoxLayout, QLabel]:
    """The one header every page uses: title, optional one-line subtitle, actions on the right."""
    row = QHBoxLayout()
    row.setSpacing(T.S2)
    col = QVBoxLayout()
    col.setSpacing(T.S1)
    t = label(title, "H1")
    col.addWidget(t)
    if subtitle is not None:
        sub = subtitle if isinstance(subtitle, QLabel) else label(subtitle, "Muted", wrap=True)
        col.addWidget(sub)
    row.addLayout(col, 1)
    for a in actions:
        row.addWidget(a, 0, Qt.AlignTop)
    return row, t


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0), stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for i, w in enumerate(widgets):
        if stretch_at is not None and i == stretch_at:
            lay.addStretch(1)
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    if stretch_at is not None and stretch_at >= len(widgets):
        lay.addStretch(1)
    return lay


def clear_layout(lay: QLayout):
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            # Hide and delete in place. Detaching first (setParent(None)) can flash the widget as its
            # own top-level window while the page refreshes.
            w.hide()
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


class Badge(QLabel):
    """Status pill. Painted directly: a per-widget stylesheet made long lists slow to build."""

    def __init__(self, status: str = "", text: str | None = None):
        super().__init__()
        f = self.font()
        f.setPixelSize(T.z(11))
        f.setWeight(QFont.DemiBold)
        self.setFont(f)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.set_status(status, text)

    def set_status(self, status: str, text: str | None = None):
        lbl, fg, bg = T.STATUS_STYLE.get(status, (status.replace("_", " ").title(), T.TEXT_2, T.SURFACE_3))
        self._fg, self._bg = QColor(fg), QColor(bg)
        self.setText(text or lbl)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + T.z(16), fm.height() + T.z(4))

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.fontMetrics().height() + T.z(4)
        w = min(self.width(), self.sizeHint().width())
        x = (self.width() - w) / 2 if self.alignment() & Qt.AlignHCenter else 0
        r = QRectF(x, (self.height() - h) / 2, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(self._bg)
        p.drawRoundedRect(r, 3, 3)
        p.setPen(self._fg)
        p.setFont(self.font())
        p.drawText(r, Qt.AlignCenter, self.text())
        p.end()


class StatCard(QFrame):
    def __init__(self, title: str, value: str = "—", hint: str = "", icon: str | None = None):
        super().__init__()
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(label(title, "Eyebrow"))
        top.addStretch(1)
        if icon:
            top.addWidget(icon_label(icon, T.MUTED, 15))
        lay.addLayout(top)
        self.value = label(value, "StatValue")
        lay.addWidget(self.value)
        self.hint = label(hint, "Muted")
        lay.addWidget(self.hint)

    def set(self, value, hint: str | None = None):
        self.value.setText(str(value))
        if hint is not None:
            self.hint.setText(hint)


class EmptyState(QWidget):
    def __init__(self, icon: str, title: str, text: str, action: QPushButton | None = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 40, 20, 40)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignCenter)
        ic = icon_label(icon, "#55555d", 36)
        lay.addWidget(ic, 0, Qt.AlignHCenter)
        t = label(title, "H2")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        d = label(text, "Muted", wrap=True)
        d.setAlignment(Qt.AlignCenter)
        d.setMaximumWidth(460)
        lay.addWidget(d, 0, Qt.AlignHCenter)
        if action:
            lay.addSpacing(6)
            lay.addWidget(action, 0, Qt.AlignHCenter)


class Banner(QFrame):
    def __init__(self, text: str, kind: str = "warn", icon: str = "alert", action: QPushButton | None = None):
        super().__init__()
        self.setObjectName("Banner" if kind == "warn" else "InfoBanner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(T.S4, T.S3, T.S3, T.S3)
        lay.setSpacing(T.S3)
        lay.addWidget(icon_label(icon, T.AMBER if kind == "warn" else T.BLUE, 16), 0, Qt.AlignVCenter)
        self.text = label(text, wrap=True)
        self.text.setStyleSheet(f"color:{T.TEXT};")
        self.text.setTextFormat(Qt.RichText)
        lay.addWidget(self.text, 1)
        if action:
            lay.addWidget(action, 0, Qt.AlignVCenter)


class FlowLayout(QLayout):
    """Wrapping grid for library cards."""

    def __init__(self, parent=None, spacing: int = 16, cell_width: int = 210):
        super().__init__(parent)
        self.items = []
        self._spacing = spacing
        self.cell_width = cell_width   # nominal column width; real width stretches to fill the row

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        m = self.contentsMargins()
        return QSize(self.cell_width + m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        if not self.items:
            return 0
        # A fixed nominal width avoids feedback with children whose minimum size follows their width.
        cols = max(1, (r.width() + self._spacing) // (self.cell_width + self._spacing))
        # stretch cards to fill the row evenly
        cw = (r.width() - (cols - 1) * self._spacing) // cols
        x, y, row_h, col = r.x(), r.y(), 0, 0
        for it in self.items:
            h = it.widget().heightForWidth(cw) if it.widget() and it.widget().hasHeightForWidth() else it.sizeHint().height()
            if not test:
                it.setGeometry(QRect(x, y, cw, h))
            row_h = max(row_h, h)
            col += 1
            if col >= cols:
                col, x, y, row_h = 0, r.x(), y + row_h + self._spacing, 0
            else:
                x += cw + self._spacing
        return y + row_h - rect.y() + m.bottom()


class Toast(QWidget):
    """A small notification card in the bottom-right corner: slides up and fades in, then out again.

    Everything is painted by hand so the fade covers the text too (no graphics effect needed)."""

    KIND = {"info": ("circle-check", "ACCENT"), "success": ("circle-check", "GREEN"), "error": ("circle-alert", "RED"),
            "warn": ("circle-alert", "AMBER")}

    def __init__(self, parent: QWidget, text: str, kind: str = "info"):
        super().__init__(parent)
        self.text = text
        icon, color = self.KIND.get(kind, self.KIND["info"])
        self.color = QColor(getattr(T, color))
        self.icon = T.icon_pixmap(icon, getattr(T, color), T.z(16))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.PointingHandCursor)
        f = QFont(T.FONT)
        f.setPixelSize(T.z(13))
        f.setWeight(QFont.Medium)
        self.setFont(f)
        self.pad, self.gap, self.shadow = T.z(14), T.z(10), T.z(10)
        fm = self.fontMetrics()
        max_text = T.z(360)
        rect = fm.boundingRect(0, 0, max_text, 2000, Qt.TextWordWrap, text)
        self.text_w = min(max_text, rect.width() + 2)
        inner_h = max(rect.height(), T.z(16))
        w = self.pad * 2 + T.z(16) + self.gap + self.text_w
        h = self.pad * 2 + inner_h - T.z(4)
        self.setFixedSize(w + self.shadow * 2, h + self.shadow * 2)
        self._t = 0.0             # 0 → hidden, 1 → fully shown
        self.anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=260)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._step)
        self._closing = False

    def _place(self):
        p = self.parentWidget()
        lift = (1 - self._t) * T.z(14)
        self.move(p.width() - self.width() - T.z(16), int(p.height() - self.height() - T.z(16) + lift))

    def _step(self, v):
        self._t = float(v)
        self._place()
        self.update()

    def show_for(self, ms: int = 3600, sound: bool = True):
        self._place()
        self.show()
        self.raise_()
        self.anim.start()
        if sound:
            play_notify_sound()
        QTimer.singleShot(ms, self.dismiss)

    def dismiss(self):
        try:
            if self._closing:
                return
            self._closing = True
            self.anim.stop()
            self.anim.setDuration(200)
            self.anim.setEasingCurve(QEasingCurve.InCubic)
            self.anim.setStartValue(self._t)
            self.anim.setEndValue(0.0)
            self.anim.finished.connect(self.deleteLater)
            self.anim.start()
        except RuntimeError:
            pass

    def mouseReleaseEvent(self, _):
        self.dismiss()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self._t)
        s = self.shadow
        card = QRectF(s, s, self.width() - 2 * s, self.height() - 2 * s)
        p.setPen(Qt.NoPen)
        for i in range(s, 0, -2):          # soft shadow
            p.setBrush(QColor(0, 0, 0, int(26 * (1 - i / s)) + 4))
            p.drawRoundedRect(card.adjusted(-i / 2, -i / 2 + 3, i / 2, i / 2 + 3), 8 + i / 2, 8 + i / 2)
        p.setBrush(QColor(T.SURFACE_3))
        p.setPen(QPen(QColor(T.BORDER_STRONG), 1))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        icon_s = T.z(16)
        ix, iy = card.left() + self.pad, card.top() + self.pad - T.z(2)
        p.drawPixmap(int(ix), int(iy) + 1, self.icon)
        p.setPen(QColor(T.TEXT))
        p.setFont(self.font())
        tr = QRectF(ix + icon_s + self.gap, card.top() + self.pad - T.z(2), self.text_w, card.height() - 2 * self.pad + T.z(4))
        p.drawText(tr, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, self.text)
        p.end()


_SOUND = None


def play_notify_sound():
    """A short, soft two-note chime (generated once, no audio files shipped). Off via Settings → Appearance."""
    global _SOUND
    try:
        from .. import db
        if not db.setting("toast_sound"):
            return
        if _SOUND is None:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
            path = _chime_file()
            _SOUND = QSoundEffect(QApplication.instance())
            _SOUND.setSource(QUrl.fromLocalFile(str(path)))
            _SOUND.setVolume(0.35)
        _SOUND.play()
    except Exception:
        _SOUND = False if _SOUND is None else _SOUND


def _chime_file():
    import math
    import struct
    import wave
    from ..config import data_dir
    path = data_dir() / "chime.wav"
    if path.exists():
        return path
    rate = 44100
    frames = bytearray()
    notes = [(0.0, 1046.5), (0.07, 1568.0)]          # C6 then G6
    total = int(rate * 0.42)
    for n in range(total):
        t = n / rate
        v = 0.0
        for start, freq in notes:
            if t >= start:
                d = t - start
                env = min(1.0, d / 0.004) * math.exp(-d * 11)
                v += env * (math.sin(2 * math.pi * freq * d) + 0.25 * math.sin(4 * math.pi * freq * d))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, v * 0.3)) * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def scroll_page(margins=None, spacing: int | None = None) -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    margins = margins or T.PAGE_MARGINS          # read at call time so zoom applies
    spacing = T.S5 if spacing is None else spacing
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    inner.setObjectName("Page")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    area.setWidget(inner)
    return area, inner, lay


def open_url(url: str | None) -> bool:
    safe = safe_external_url(url)
    if safe:
        QDesktopServices.openUrl(QUrl(safe))
        return True
    return False


def fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_long_duration(seconds: int | None) -> str:
    if not seconds:
        return "Unknown length"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h} h {m} min" if h else f"{m} min"


def elide(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def bold_font(size: int = 13, weight: int = QFont.DemiBold) -> QFont:
    f = QFont(T.FONT)
    f.setPointSizeF(size * 0.75)
    f.setWeight(weight)
    return f


def fit_tabs_to_current(tabs) -> None:
    """Let a QTabWidget take the height of the visible page instead of the tallest page."""
    def update(i):
        for k in range(tabs.count()):
            tabs.widget(k).setSizePolicy(QSizePolicy.Preferred if k == i else QSizePolicy.Ignored,
                                         QSizePolicy.Preferred if k == i else QSizePolicy.Ignored)
            tabs.widget(k).adjustSize()
        tabs.adjustSize()
    tabs.currentChanged.connect(update)
    update(tabs.currentIndex())


class Toggle(QPushButton):
    """An on/off switch (with optional label) used instead of checkboxes."""
    from PySide6.QtCore import Signal as _Signal
    toggled_ = _Signal(bool)

    def __init__(self, text: str = "", checked: bool = False):
        super().__init__(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }")
        self.setFixedHeight(22)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool):
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0
        self.update()

    def isChecked(self) -> bool:  # noqa: D401 (kept for checkbox-compatible callers)
        return super().isChecked()

    def _get(self):
        return self._pos

    def _set(self, v):
        self._pos = v
        self.update()

    from PySide6.QtCore import Property as _Property
    knob = _Property(float, _get, _set)

    def sizeHint(self):
        fm = self.fontMetrics()
        w = 36 + (10 + fm.horizontalAdvance(self.text()) if self.text() else 0)
        return QSize(w, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(0.5, 2.5, 34, 18)
        on_col, off_col = QColor(T.ACCENT), QColor(T.SURFACE_3)
        c = QColor(int(off_col.red() + (on_col.red() - off_col.red()) * self._pos),
                   int(off_col.green() + (on_col.green() - off_col.green()) * self._pos),
                   int(off_col.blue() + (on_col.blue() - off_col.blue()) * self._pos))
        if not self.isEnabled():
            c.setAlpha(90)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(track, 9, 9)
        x = track.left() + 2 + (track.width() - 18) * self._pos
        p.setBrush(QColor("#ffffff" if self.isEnabled() else "#8a8a90"))
        p.drawEllipse(QRectF(x, track.top() + 2, 14, 14))
        if self.text():
            p.setPen(QColor(T.TEXT if self.isEnabled() else T.MUTED))
            p.drawText(QRectF(46, 0, self.width() - 46, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        if self.hasFocus():
            p.setPen(QColor("#55555e"))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), 11, 11)
        p.end()


class Segmented(QFrame):
    """Joined option buttons ("Finished at | Started at"). Calls on_change(key)."""

    def __init__(self, options: list[tuple[str, str]], on_change: Callable[[str], None], current: str | None = None):
        super().__init__()
        self.setObjectName("Segmented")
        self.setStyleSheet(f"""
            QFrame#Segmented {{ background: {T.SURFACE}; border: 1px solid {T.BORDER_STRONG}; border-radius: 4px; }}
            QFrame#Segmented QPushButton {{ background: transparent; border: none; border-radius: 3px; padding: 5px 12px;
                                           color: {T.TEXT_2}; font-weight: 500; min-height: 18px; }}
            QFrame#Segmented QPushButton:hover {{ color: {T.TEXT}; }}
            QFrame#Segmented QPushButton:checked {{ background: {T.SURFACE_3}; color: {T.TEXT}; font-weight: 600; }}""")
        h = QHBoxLayout(self)
        h.setContentsMargins(3, 3, 3, 3)
        h.setSpacing(2)
        self.buttons: dict[str, QPushButton] = {}
        self.on_change = on_change
        for key, text in options:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set(k, emit=True))
            h.addWidget(b)
            self.buttons[key] = b
        self.current = None
        self.set(current or options[0][0])
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set(self, key: str, emit: bool = False):
        self.current = key
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        if emit:
            self.on_change(key)
