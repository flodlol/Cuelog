"""One dialog frame for the whole app, plus confirm / notice / text-input popups.

Every dialog gets the same margins, a header (optional artwork, title, one-line subtitle),
a body, and a footer with the main action on the right. Esc cancels, Enter confirms.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from .. import theme as T
from ..widgets import Artwork, button, icon_label, label


class Dialog(QDialog):
    def __init__(self, parent=None, title: str = "", subtitle: str | None = None, art_url: str | None = None,
                 width: int = 520, icon: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.setObjectName("Dialog")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.S5, T.S5, T.S5, T.S5)
        outer.setSpacing(T.S5)
        head = QHBoxLayout()
        head.setSpacing(T.S3 + 2)
        if art_url is not None:
            head.addWidget(Artwork(48, 8, art_url, icon="disc"), 0, Qt.AlignTop)
        elif icon:
            box = QWidget()
            box.setFixedSize(40, 40)
            box.setStyleSheet(f"background:{T.SURFACE_2}; border-radius: 6px;")
            ic = icon_label(icon, T.ACCENT, 20)
            ic.setParent(box)
            ic.move(10, 10)
            head.addWidget(box, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title_label = label(title, "H2")
        col.addWidget(self.title_label)
        self.subtitle_label = label(subtitle or "", "Muted", wrap=True)
        self.subtitle_label.setVisible(bool(subtitle))
        col.addWidget(self.subtitle_label)
        head.addLayout(col, 1)
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(T.S4)
        outer.addLayout(self.body, 1)
        self.footer = QHBoxLayout()
        self.footer.setSpacing(T.S2)
        self.footer_left = QHBoxLayout()
        self.footer_left.setSpacing(T.S2)
        self.footer.addLayout(self.footer_left)
        self.footer.addStretch(1)
        outer.addLayout(self.footer)

    def add_actions(self, primary: str | None, on_primary=None, cancel: str | None = "Cancel", danger: bool = False,
                    icon: str | None = None):
        if cancel:
            self.footer.addWidget(button(cancel, None, "Ghost", self.reject))
        btn = None
        if primary:
            quiet = cancel is None and primary in ("Close", "OK", "Done")
            btn = button(primary, icon, "Danger" if danger else (None if quiet else "Primary"), on_primary or self.accept)
            btn.setDefault(True)
            btn.setAutoDefault(True)
            self.footer.addWidget(btn)
        return btn


def form(rows: list[tuple[str, QWidget | object]], label_width: int = 92) -> QGridLayout:
    """Aligned label/control rows. A control may be a widget or a layout."""
    g = QGridLayout()
    g.setHorizontalSpacing(T.S4)
    g.setVerticalSpacing(T.S3)
    g.setColumnMinimumWidth(0, label_width)
    g.setColumnStretch(1, 1)
    for i, (text, ctl) in enumerate(rows):
        lb = label(text, "Secondary")
        lb.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lb.setFixedHeight(34)
        g.addWidget(lb, i, 0, Qt.AlignTop)
        if isinstance(ctl, QWidget):
            g.addWidget(ctl, i, 1)
        else:
            g.addLayout(ctl, i, 1)
    return g


def confirm(parent, title: str, text: str, ok: str = "Continue", danger: bool = False, cancel: str = "Cancel") -> bool:
    d = Dialog(parent, title, None, width=440, icon="alert" if danger else "circle-alert")
    body = label(text, "Secondary", wrap=True)
    body.setTextFormat(Qt.RichText)
    d.body.addWidget(body)
    d.add_actions(ok, cancel=cancel, danger=danger)
    return d.exec() == QDialog.Accepted


def notify(parent, title: str, text: str, ok: str = "OK", kind: str = "info") -> None:
    d = Dialog(parent, title, None, width=420, icon={"error": "alert", "success": "circle-check"}.get(kind, "circle-alert"))
    body = label(text, "Secondary", wrap=True)
    body.setTextFormat(Qt.RichText)
    d.body.addWidget(body)
    d.add_actions(ok, cancel=None)
    d.exec()


def ask_text(parent, title: str, prompt: str, text: str = "", placeholder: str = "", ok: str = "OK") -> tuple[str, bool]:
    d = Dialog(parent, title, prompt, width=420)
    e = QLineEdit(text)
    e.setPlaceholderText(placeholder)
    e.selectAll()
    d.body.addWidget(e)
    d.add_actions(ok)
    e.setFocus()
    ok_ = d.exec() == QDialog.Accepted
    return e.text(), ok_


__all__ = ["Dialog", "form", "confirm", "notify", "ask_text", "QLabel"]
