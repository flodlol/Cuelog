"""Application entry point."""
from __future__ import annotations

import os
import sys
import traceback


def _attach_pipes() -> None:
    """A windowed exe may start without sys.stdin/stdout even when the app hands it pipes: reattach them."""
    if sys.platform != "win32" or (sys.stdin is not None and sys.stdout is not None):
        return
    import ctypes
    import msvcrt
    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    for name, std, flags, mode in (("stdin", -10, os.O_RDONLY, "r"), ("stdout", -11, os.O_WRONLY, "w")):
        if getattr(sys, name) is not None:
            continue
        handle = kernel32.GetStdHandle(std)
        if handle and handle != ctypes.c_void_p(-1).value:
            try:
                fd = msvcrt.open_osfhandle(handle, flags)
                setattr(sys, name, open(fd, mode, encoding="utf-8", buffering=1))
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    if len(argv) > 1 and argv[1] == "--player":
        # Cuelog.exe --player <url> …: the packaged app runs its own player window
        from . import player_proc
        _attach_pipes()
        return player_proc.main(argv[1:])
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Cuelog.DesktopScrobbler")
        except Exception:
            pass
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--autoplay-policy=no-user-gesture-required")

    from PySide6.QtCore import QCoreApplication, QLockFile, Qt
    try:  # must be imported before the QApplication exists
        from PySide6 import QtWebEngineWidgets  # noqa: F401  (must precede QApplication)
    except ImportError:
        pass
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtGui import QColor, QIcon, QPalette
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME, config, db, legacy
    from .ui import theme as T

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in ((QPalette.Window, T.BG), (QPalette.Base, T.SURFACE), (QPalette.AlternateBase, T.SURFACE_2),
                        (QPalette.Text, T.TEXT), (QPalette.WindowText, T.TEXT), (QPalette.Button, T.SURFACE_2),
                        (QPalette.ButtonText, T.TEXT), (QPalette.Highlight, T.ACCENT), (QPalette.HighlightedText, "#000000"),
                        (QPalette.ToolTipBase, T.SURFACE_2), (QPalette.ToolTipText, T.TEXT), (QPalette.PlaceholderText, T.MUTED)):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(T.stylesheet())
    from .ui.winchrome import DarkChrome
    chrome = DarkChrome()
    app.installEventFilter(chrome)
    from pathlib import Path
    icon = QIcon(str(Path(__file__).resolve().parent / "assets" / "cuelog.ico"))
    if icon.isNull():
        for size in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(T.icon_pixmap("wave", T.ACCENT, size, dpr=1.0))
    app.setWindowIcon(icon)

    try:
        config.migrate_old_location()
    except OSError as err:
        QMessageBox.warning(None, APP_NAME, f"Your data could not be copied to {config.APP_HOME}:\n{err}")

    lock = QLockFile(str(config.data_dir() / "cuelog.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already running.")
        return 0

    try:
        db.get()
    except Exception as err:
        QMessageBox.critical(None, APP_NAME, f"Could not open the database:\n{err}")
        return 1

    report = None
    try:
        report = legacy.migrate(config.ROOT)
    except Exception as err:
        QMessageBox.warning(None, APP_NAME, f"Your earlier CSV data could not be imported automatically:\n\n{err}\n\n"
                            "You can retry from Settings → Data → Import legacy CSV project.")

    from .ui.main_window import MainWindow
    from .ui.workers import run
    from . import services

    win = MainWindow()
    win.show()
    # Official Spotify artwork for mapped songs that have none yet (keyless oEmbed).
    run(services.startup_enrichment, lambda n: n and win.state.changed())

    if not db.setting("setup_complete") or "--setup" in argv:
        from .ui.dialogs.setup_wizard import SetupWizard
        SetupWizard(report, win).exec()

    def excepthook(t, v, tb):
        text = "".join(traceback.format_exception(t, v, tb))
        (config.data_dir() / "error.log").open("a", encoding="utf-8").write(text + "\n")
        QMessageBox.warning(win, APP_NAME, f"Something went wrong: {v}\n\nDetails were written to {config.data_dir() / 'error.log'}.")
    sys.excepthook = excepthook
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
