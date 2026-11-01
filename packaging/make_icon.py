"""Render cuelog/assets/cuelog.ico (and packaging/cuelog.png) from the app's wave logo."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
WAVE = ('<path d="M2 13a2 2 0 0 0 2-2V7a2 2 0 0 1 4 0v13a2 2 0 0 0 4 0V4a2 2 0 0 1 4 0v13a2 2 0 0 0 4 0v-4a2 2 0 0 1 2-2"/>')
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    inset = 0 if size <= 24 else size * 0.04
    r = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    tile = QPainterPath()
    tile.addRoundedRect(r, r.width() * 0.23, r.width() * 0.23)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor("#ff7a3d"))
    g.setColorAt(1, QColor("#f0420f"))
    p.fillPath(tile, g)
    stroke = 2.6 if size <= 24 else 2.2
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#140804" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{WAVE}</svg>')
    pad = r.width() * (0.17 if size <= 24 else 0.2)
    QSvgRenderer(QByteArray(svg.encode())).render(p, r.adjusted(pad, pad, -pad, -pad))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def main() -> int:
    QGuiApplication(sys.argv)
    blobs = [png_bytes(render(s)) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    for s, b in zip(SIZES, blobs):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(b), offset)
        offset += len(b)
    (HERE.parent / "cuelog" / "assets" / "cuelog.ico").write_bytes(out + b"".join(blobs))
    render(512).save(str(HERE / "cuelog.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
