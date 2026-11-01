"""Dark Windows title bars for every window and dialog (Windows 10 20H1+ / Windows 11)."""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget


def _rgb(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)          # COLORREF is 0x00BBGGRR


def style_window(w: QWidget, caption: str = "#08080a", text: str = "#d6d6db") -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(w.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))      # immersive dark mode
        for attr, value in ((35, _rgb(caption)), (36, _rgb(text)), (34, _rgb(caption))):  # caption, text, border
            v = ctypes.c_uint(value)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


class DarkChrome(QObject):
    """App-wide event filter: style each top-level window the first time it is shown."""

    def __init__(self):
        super().__init__()
        self._done: set[int] = set()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
            key = id(obj)
            if key not in self._done:
                self._done.add(key)
                style_window(obj)
        return False
