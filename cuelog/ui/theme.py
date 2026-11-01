"""Design tokens, icons (Lucide, ISC licence) and the application stylesheet."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

BG = "#0b0b0c"
SIDEBAR = "#08080a"
SURFACE = "#131315"
SURFACE_2 = "#1a1a1d"
SURFACE_3 = "#222226"
BORDER = "#26262b"
BORDER_STRONG = "#34343a"
TEXT = "#f2f2f3"
TEXT_2 = "#b4b4bb"
MUTED = "#7d7d86"
ACCENT = "#ff5a1f"
ACCENT_HOVER = "#ff7a45"
ACCENT_SOFT = "#2a1710"
GREEN = "#46d38a"
GREEN_SOFT = "#11261b"
AMBER = "#f2b84b"
AMBER_SOFT = "#2a2112"
RED = "#ff6363"
RED_SOFT = "#2c1415"
BLUE = "#6aa8ff"
BLUE_SOFT = "#121d2e"

FONT = "Segoe UI"
FONT_DISPLAY = "Segoe UI"

_ICONS = {
    # brand: a waveform with a cue marker
    "wave": '<path d="M2 13a2 2 0 0 0 2-2V7a2 2 0 0 1 4 0v13a2 2 0 0 0 4 0V4a2 2 0 0 1 4 0v13a2 2 0 0 0 4 0v-4a2 2 0 0 1 2-2"/>',
    # navigation
    "home": '<g class="d"><rect x="10" y="14" width="4" height="6.5" rx="1"/></g>'
            '<path d="M4 10.4 11.1 4.6a1.4 1.4 0 0 1 1.8 0L20 10.4V19a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 19z"/>',
    "library": '<g class="d"><rect x="9.25" y="4" width="4" height="16" rx="1.2"/></g>'
               '<rect x="4" y="4" width="4" height="16" rx="1.2"/><rect x="9.25" y="4" width="4" height="16" rx="1.2"/>'
               '<path d="m15.3 5.6 3.4-1 3 13.9-3.4.9z"/>',
    "plus-circle": '<g class="d"><rect x="3.5" y="3.5" width="17" height="17" rx="5.5"/></g>'
                   '<rect x="3.5" y="3.5" width="17" height="17" rx="5.5"/><path d="M12 8.5v7M8.5 12h7"/>',
    "headphones": '<circle cx="11" cy="12.5" r="7.5"/><g class="d"><circle cx="11" cy="12.5" r="2.4"/></g>'
                  '<circle cx="11" cy="12.5" r="2.4"/><path d="M20.5 3.5v6.2l-3.2 2.8"/>',
    "chart": '<path d="M4 4v14.5A1.5 1.5 0 0 0 5.5 20H20"/><g class="d"><path d="M7.5 15l3.5-4.5 3 2.5 5-6.5V16H7.5z"/></g><path d="m7.5 15 3.5-4.5 3 2.5 5-6.5"/>',
    "activity": '<g class="d"><rect x="12" y="4" width="4" height="16" rx="1.2"/></g>'
                '<path d="M5 20v-6M9.5 20V9M14 20V4.5M18.5 20v-9"/>',
    "settings": '<path d="M4 7h8.5M17.5 7H20M4 17h3M11.5 17H20"/><g class="d"><circle cx="15" cy="7" r="2.4"/>'
                '<circle cx="9" cy="17" r="2.4"/></g><circle cx="15" cy="7" r="2.4"/><circle cx="9" cy="17" r="2.4"/>',
    # actions
    "plus": '<path d="M12 5.5v13M5.5 12h13"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.3"/><path d="m15.3 15.3 4.7 4.7"/>',
    "play": '<path d="M8 5.6v12.8a1 1 0 0 0 1.5.86l10.4-6.4a1 1 0 0 0 0-1.72L9.5 4.74A1 1 0 0 0 8 5.6z" fill="currentColor"/>',
    "pause": '<rect x="6.5" y="5" width="3.6" height="14" rx="1.2" fill="currentColor"/>'
             '<rect x="13.9" y="5" width="3.6" height="14" rx="1.2" fill="currentColor"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="3" fill="currentColor"/>',
    "repeat": '<path d="M5 12a7 7 0 0 1 12.2-4.7M19 12a7 7 0 0 1-12.2 4.7"/><path d="M17.6 3.8v3.9h-3.9M6.4 20.2v-3.9h3.9"/>',
    "check": '<path d="m5.5 12.5 4.2 4.2 8.8-9.2"/>',
    "x": '<path d="m7 7 10 10M17 7 7 17"/>',
    "external": '<path d="M14 4.5h5.5V10M19.5 4.5 11.5 12.5"/><path d="M17.5 14v3.5a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2H10"/>',
    "more": '<circle cx="6" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.5" fill="currentColor" '
            'stroke="none"/><circle cx="18" cy="12" r="1.5" fill="currentColor" stroke="none"/>',
    "back": '<path d="m14.5 6-6 6 6 6"/>',
    "chevron-up": '<path d="m6.5 14.5 5.5-5.5 5.5 5.5"/>',
    "chevron-down": '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>',
    "star": '<path d="m12 4 2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.3l-4.8 2.6.9-5.4-3.9-3.8 5.4-.8z"/>',
    "pencil": '<g class="d"><path d="M15.5 5.5 18.5 8.5 9 18l-4 1 1-4z"/></g><path d="M15.5 5.5 18.5 8.5 9 18l-4 1 1-4z"/>'
              '<path d="m13.5 7.5 3 3"/>',
    "trash": '<path d="M5 7h14M10 4.5h4"/><path d="M6.5 7l.8 11.2A1.7 1.7 0 0 0 9 19.8h6a1.7 1.7 0 0 0 1.7-1.6L17.5 7"/>',
    "upload": '<path d="M12 15V4.5M7.8 8.7 12 4.5l4.2 4.2"/><path d="M4.5 14.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3"/>',
    "download": '<path d="M12 4.5V15M7.8 10.8 12 15l4.2-4.2"/><path d="M4.5 14.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3"/>',
    "refresh": '<path d="M19 11a7 7 0 0 0-12.4-4.1L5 8.5M5 13a7 7 0 0 0 12.4 4.1L19 15.5"/><path d="M5 4.5v4h4M19 19.5v-4h-4"/>',
    "list": '<path d="M9 7h11M9 12h11M9 17h7"/><circle cx="5" cy="7" r="1" fill="currentColor" stroke="none"/>'
            '<circle cx="5" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="5" cy="17" r="1" fill="currentColor" stroke="none"/>',
    "sparkles": '<g class="d"><path d="M10 4l1.6 4.4L16 10l-4.4 1.6L10 16l-1.6-4.4L4 10l4.4-1.6z"/></g>'
                '<path d="M10 4l1.6 4.4L16 10l-4.4 1.6L10 16l-1.6-4.4L4 10l4.4-1.6z"/><path d="M18 14.5v5M15.5 17h5"/>',
    "link": '<path d="M10 14a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-1 1"/><path d="M14 10a3.5 3.5 0 0 0-5 0l-3 3a3.5 3.5 0 0 0 5 5l1-1"/>',
    "volume": '<g class="d"><path d="M4 9.5h3l4.5-4v13L7 14.5H4z"/></g><path d="M4 9.5h3l4.5-4v13L7 14.5H4z"/>'
              '<path d="M15.5 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11"/>',
    "logout": '<path d="M10 19.5H6.5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2H10"/><path d="M14.5 16.5 19 12l-4.5-4.5M19 12H9.5"/>',
    "file": '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/><path d="M14 3.5V8h4.5"/>',
    "database": '<ellipse cx="12" cy="6" rx="7" ry="2.5"/><path d="M5 6v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/>',
    # status / content
    "music": '<path d="M9 17.5V6.2l10-2v11.3"/><g class="d"><circle cx="6.5" cy="17.5" r="2.5"/><circle cx="16.5" cy="15.5" r="2.5"/></g>'
             '<circle cx="6.5" cy="17.5" r="2.5"/><circle cx="16.5" cy="15.5" r="2.5"/>',
    "disc": '<circle cx="12" cy="12" r="8.5"/><g class="d"><circle cx="12" cy="12" r="2.6"/></g><circle cx="12" cy="12" r="2.6"/>',
    "user": '<g class="d"><circle cx="12" cy="8.5" r="3.6"/></g><circle cx="12" cy="8.5" r="3.6"/><path d="M5 19.5c1.2-3.2 3.8-4.8 7-4.8s5.8 1.6 7 4.8"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "calendar": '<rect x="4" y="5.5" width="16" height="14.5" rx="3"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/>',
    "history": '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 9"/><path d="M4.5 4.5V9H9M12 8v4l3 2"/>',
    "alert": '<g class="d"><path d="M10.3 4.8 3.6 16.6A2 2 0 0 0 5.3 19.6h13.4a2 2 0 0 0 1.7-3L13.7 4.8a2 2 0 0 0-3.4 0z"/></g>'
             '<path d="M10.3 4.8 3.6 16.6A2 2 0 0 0 5.3 19.6h13.4a2 2 0 0 0 1.7-3L13.7 4.8a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 16.4v.1"/>',
    "circle-alert": '<circle cx="12" cy="12" r="8.5"/><path d="M12 8v4.5M12 15.9v.1"/>',
    "circle-check": '<g class="d"><circle cx="12" cy="12" r="8.5"/></g><circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.4 2.4 4.6-4.8"/>',
    "grip": '<circle cx="9" cy="6" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="6" r="1.2" fill="currentColor" stroke="none"/>'
            '<circle cx="9" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="1.2" fill="currentColor" stroke="none"/>'
            '<circle cx="9" cy="18" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="18" r="1.2" fill="currentColor" stroke="none"/>',
}
_ICONS["dashboard"] = _ICONS["home"]
_ICONS["compass"] = _ICONS["plus-circle"]


@lru_cache(maxsize=512)
def icon_pixmap(name: str, color: str = TEXT_2, size: int = 18, fill: bool = False, dpr: float = 2.0,
                stroke: float = 1.6) -> QPixmap:
    body = _ICONS.get(name, _ICONS["disc"]).replace("currentColor", color)
    body = body.replace('<g class="d">', f'<g fill="{color}" fill-opacity="0.22" stroke="none">')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color if fill else "none"}" '
           f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str = TEXT_2, size: int = 18, fill: bool = False, checked_color: str | None = None) -> QIcon:
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, color, size, fill), QIcon.Normal, QIcon.Off)
    if checked_color:
        ic.addPixmap(icon_pixmap(name, checked_color, size, fill), QIcon.Normal, QIcon.On)
    return ic


# Spacing scale (px). Use these instead of ad-hoc numbers. Recomputed by set_zoom().
S1, S2, S3, S4, S5, S6 = 4, 8, 12, 16, 24, 32
PAGE_MARGINS = (S6, 28, S6, S6)

# ---- zoom (Ctrl + / Ctrl -) -----------------------------------------------------------------
ZOOM = 1.0
ZOOM_STEPS = (0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5)
_BASE = (4, 8, 12, 16, 24, 32)


def z(n: float) -> int:
    """Scale a pixel size by the current zoom."""
    return max(1, round(n * ZOOM)) if n else 0


def set_zoom(factor: float) -> None:
    global ZOOM, S1, S2, S3, S4, S5, S6, PAGE_MARGINS
    ZOOM = max(ZOOM_STEPS[0], min(ZOOM_STEPS[-1], factor))
    S1, S2, S3, S4, S5, S6 = (z(v) for v in _BASE)
    PAGE_MARGINS = (S6, z(28), S6, S6)


def scale_css(css: str) -> str:
    """Multiply every px value in a stylesheet by the zoom (1px borders stay 1px)."""
    if ZOOM == 1.0 or not css:
        return css
    import re
    return re.sub(r"(\d+(?:\.\d+)?)px", lambda m: f"{max(1, round(float(m.group(1)) * ZOOM)) if 1 < float(m.group(1)) < 10000 else m.group(1)}px", css)


def install_zoom_hook() -> None:
    """Make every widget's own setStyleSheet() honour the zoom too."""
    from PySide6.QtWidgets import QWidget
    if getattr(QWidget, "_cuelog_zoom_hook", False):
        return
    original = QWidget.setStyleSheet

    def scaled(self, css):
        original(self, scale_css(css))
    QWidget.setStyleSheet = scaled
    QWidget._cuelog_zoom_hook = True


STATUS_STYLE = {
    # status: (label, fg, bg)
    "confirmed": ("Verified", GREEN, GREEN_SOFT),
    "auto": ("Auto match", BLUE, BLUE_SOFT),
    "manual": ("Manual", TEXT_2, SURFACE_3),
    "ambiguous": ("Check", AMBER, AMBER_SOFT),
    "unmatched": ("Unmatched", RED, RED_SOFT),
    "no_match": ("Not scrobbled", MUTED, SURFACE_3),
    "draft": ("Draft", TEXT_2, SURFACE_3),
    "ready": ("Ready", BLUE, BLUE_SOFT),
    "queued": ("Queued", BLUE, BLUE_SOFT),
    "uploading": ("Uploading", ACCENT, ACCENT_SOFT),
    "completed": ("Completed", GREEN, GREEN_SOFT),
    "partial": ("Partially completed", AMBER, AMBER_SOFT),
    "rate_limited": ("Rate-limited", AMBER, AMBER_SOFT),
    "failed": ("Failed", RED, RED_SOFT),
    "needs_review": ("Needs review", AMBER, AMBER_SOFT),
    "cancelled": ("Cancelled", MUTED, SURFACE_3),
    "expired": ("Too old", RED, RED_SOFT),
    "skipped": ("Skipped", MUTED, SURFACE_3),
    "pending": ("Pending", TEXT_2, SURFACE_3),
    "accepted": ("Accepted", GREEN, GREEN_SOFT),
    "ignored": ("Rejected", RED, RED_SOFT),
    "duplicate": ("Duplicate", MUTED, SURFACE_3),
    "submitting": ("Submitting", ACCENT, ACCENT_SOFT),
    "uncertain": ("Unconfirmed", AMBER, AMBER_SOFT),
}


def _asset(name: str, body: str, color: str, size: int = 24) -> str:
    """Write a small SVG used by the stylesheet (checkmarks, arrows) and return its path for url()."""
    from .. import config
    d = config.cache_dir() / "ui"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.svg"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    if not path.exists() or path.read_text(encoding="utf-8") != svg:
        path.write_text(svg, encoding="utf-8")
    return path.as_posix()


def stylesheet() -> str:
    check = _asset("check", '<path d="m5.5 12.5 4.2 4.2 8.8-9.2"/>', "#120700")
    arrow = _asset("chevron", '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>', MUTED)
    return scale_css(_stylesheet(check, arrow))


def _stylesheet(check: str, arrow: str) -> str:
    return f"""
* {{ font-family: "{FONT}", "Segoe UI", sans-serif; font-size: 13px; color: {TEXT}; outline: none; }}
QMainWindow, QDialog, #Page, QStackedWidget > QWidget#Page {{ background: {BG}; }}
QWidget#Sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QLabel {{ background: transparent; }}
QLabel#H1 {{ font-size: 22px; font-weight: 600; }}
QLabel#Display {{ font-size: 24px; font-weight: 600; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#H3 {{ font-size: 13px; font-weight: 600; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Secondary {{ color: {TEXT_2}; }}
QLabel#Eyebrow {{ color: {MUTED}; font-size: 12px; font-weight: 500; }}
QLabel#Small {{ color: {MUTED}; font-size: 12px; }}
QLabel#StatValue {{ font-size: 15px; font-weight: 600; }}
QLabel#Wordmark {{ font-size: 16px; font-weight: 700; }}
QFrame#TrackRow {{ border-radius: 4px; }}
QFrame#TrackRow:hover {{ background: {SURFACE_2}; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#CardFlat {{ background: {SURFACE}; border-radius: 6px; }}
QFrame#Banner {{ background: {AMBER_SOFT}; border: 1px solid #3d3017; border-radius: 6px; }}
QFrame#InfoBanner {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 0 12px;
               min-height: 30px; max-height: 30px; font-weight: 600; color: {TEXT}; }}
QPushButton[iconOnly="true"] {{ padding: 0; min-width: 30px; max-width: 30px; }}
QPushButton:focus {{ border-color: #55555e; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QPushButton:hover {{ background: {SURFACE_3}; border-color: #44444c; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {MUTED}; background: {SURFACE}; border-color: {BORDER}; }}
QPushButton#Primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: #120700; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: #5a2b19; border-color: #5a2b19; color: #a07060; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {TEXT_2}; }}
QPushButton#Ghost:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#Danger {{ background: transparent; border: 1px solid #4a2223; color: {RED}; }}
QPushButton#Danger:hover {{ background: {RED_SOFT}; }}
QPushButton#Nav {{ background: transparent; border: none; border-radius: 4px; padding: 0 10px; min-height: 34px;
                   text-align: left; color: {TEXT_2}; font-weight: 500; font-size: 13px; }}
QPushButton#Nav:hover {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton#Nav:checked {{ background: {SURFACE_2}; color: {TEXT}; font-weight: 600; }}
QLabel#NavBadge {{ background: {SURFACE_3}; color: {TEXT_2}; border-radius: 4px; padding: 0 6px; font-size: 11px; font-weight: 600; }}
QPushButton#Chip {{ background: transparent; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 5px 12px;
                    min-height: 18px; color: {TEXT_2}; font-weight: 500; }}
QPushButton#Chip:hover {{ color: {TEXT}; border-color: #44444c; }}
QPushButton#Chip:checked {{ background: {ACCENT_SOFT}; border-color: #5a2b19; color: {ACCENT_HOVER}; }}
QToolButton {{ background: transparent; border: none; border-radius: 4px; padding: 5px; }}
QToolButton:hover {{ background: {SURFACE_3}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit, QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 0 10px; min-height: 30px;
    max-height: 30px;
    selection-background-color: {ACCENT}; selection-color: #000; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus,
QTimeEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#Search {{ padding-left: 32px; }}
QLineEdit#Big {{ font-size: 14px; padding: 0 12px; min-height: 38px; max-height: 38px; }}
QPushButton#Tall {{ min-height: 38px; max-height: 38px; padding: 0 18px; }}
QPlainTextEdit, QTextEdit {{ padding: 8px 10px; min-height: 0; max-height: 16777215px; }}
QPlainTextEdit {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; padding: 8px 10px; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({arrow}); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ top: 1px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; selection-background-color: {SURFACE_3}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QDateEdit::up-button, QDateEdit::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{ width: 0; border: none; }}
QDateEdit {{ padding-right: 28px; }}
QDateEdit::drop-down {{ border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }}
QDateEdit::down-arrow {{ image: url({arrow}); width: 12px; height: 12px; }}
QCalendarWidget QWidget {{ alternate-background-color: {SURFACE_2}; }}
QCalendarWidget QAbstractItemView:enabled {{ background: {SURFACE}; selection-background-color: {ACCENT}; selection-color: #000; }}
QCalendarWidget QToolButton {{ color: {TEXT}; font-weight: 600; }}
QCalendarWidget #qt_calendar_navigationbar {{ background: {SURFACE_2}; }}

QCheckBox, QRadioButton {{ spacing: 8px; color: {TEXT}; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER_STRONG}; background: {SURFACE}; }}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 4px; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({check}); }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:hover {{ border-color: #55555e; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a3a40; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}

QTableWidget, QTableView, QTreeWidget, QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    gridline-color: transparent; alternate-background-color: {SURFACE}; selection-background-color: {SURFACE_3};
    selection-color: {TEXT}; }}
QTableWidget::item, QTreeWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {BORDER}; }}
QListWidget::item {{ padding: 4px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {SURFACE_3}; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px; font-size: 12px; font-weight: 500; }}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 0; margin-right: 24px; border: none;
    border-bottom: 2px solid transparent; font-weight: 500; font-size: 13px; }}
QTabWidget::tab-bar {{ left: 0; }}
QTabBar {{ border-bottom: 1px solid {BORDER}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 6px 8px; border-radius: 3px; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; padding: 5px; border-radius: 6px; }}
QMenu::item {{ padding: 8px 28px 8px 12px; border-radius: 3px; color: {TEXT}; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {BORDER_STRONG}; margin: 5px 8px; }}
QDialog#Dialog, QDialog {{ background: {BG}; }}
QCalendarWidget QAbstractItemView {{ selection-background-color: {ACCENT}; selection-color: #120700; outline: 0; }}
QCalendarWidget QToolButton {{ padding: 4px 8px; border-radius: 3px; }}
QCalendarWidget QToolButton::menu-indicator {{ image: none; width: 0; }}
QCalendarWidget QSpinBox {{ padding: 2px 6px; }}
QSplitter::handle {{ background: {BORDER}; }}
"""


def qcolor(hex_: str) -> QColor:
    return QColor(hex_)


ICON_SIZE = QSize(18, 18)


# ---- colour schemes (Settings → Appearance) --------------------------------------------------
ACCENTS = {
    # key: (label, accent, hover, soft background)
    "ember": ("Ember", "#ff5a1f", "#ff7a45", "#2a1710"),
    "lastfm": ("Last.fm red", "#e4242b", "#ff474d", "#2c1213"),
    "ocean": ("Ocean", "#3d8bfd", "#69a6ff", "#111d30"),
    "violet": ("Violet", "#9b6dff", "#b28cff", "#1f1730"),
    "mint": ("Mint", "#2fbf8f", "#4fd6a6", "#0f2620"),
    "rose": ("Rose", "#ff4f8b", "#ff76a6", "#2c1320"),
}
BASES = {
    # key: (label, bg, sidebar, surface, surface2, surface3, border, border_strong)
    "midnight": ("Midnight", "#0b0b0c", "#08080a", "#131315", "#1a1a1d", "#222226", "#26262b", "#34343a"),
    "graphite": ("Graphite", "#161618", "#121214", "#1d1d20", "#242428", "#2c2c31", "#303036", "#3e3e45"),
}


def set_scheme(accent: str = "ember", base: str = "midnight") -> None:
    global ACCENT, ACCENT_HOVER, ACCENT_SOFT, BG, SIDEBAR, SURFACE, SURFACE_2, SURFACE_3, BORDER, BORDER_STRONG
    _, ACCENT, ACCENT_HOVER, ACCENT_SOFT = ACCENTS.get(accent, ACCENTS["ember"])
    _, BG, SIDEBAR, SURFACE, SURFACE_2, SURFACE_3, BORDER, BORDER_STRONG = BASES.get(base, BASES["midnight"])
    STATUS_STYLE["uploading"] = ("Uploading", ACCENT, ACCENT_SOFT)
    STATUS_STYLE["submitting"] = ("Submitting", ACCENT, ACCENT_SOFT)
    for k in ("manual", "draft", "pending", "no_match", "cancelled", "skipped", "duplicate"):
        lab, fg, _bg = STATUS_STYLE[k]
        STATUS_STYLE[k] = (lab, fg, SURFACE_3)
    icon_pixmap.cache_clear()
