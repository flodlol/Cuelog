# PyInstaller spec for Cuelog.exe. Build with packaging/build.ps1.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = SPECPATH + "/.."

hidden = collect_submodules("winrt") + collect_submodules(
    "webview", filter=lambda n: not any(x in n for x in (".android", ".cocoa", ".gtk", ".qt"))) + collect_submodules("cuelog")
datas = [(ROOT + "/cuelog/assets", "cuelog/assets")] + collect_data_files("webview")

a = Analysis(
    [SPECPATH + "/cuelog_main.py"],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "PyInstaller", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
              "PySide6.QtGraphs", "PySide6.QtBluetooth", "PySide6.QtSerialPort", "PySide6.QtSensors", "PySide6.QtDesigner",
              "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtHelp"],
    noarchive=False,
)


def wanted(dest: str) -> bool:
    """Drop what a widgets app never loads: devtools, QML/Quick3D, translations, software OpenGL."""
    d = dest.replace("\\", "/").lower()
    if "qtwebengine_locales/" in d:
        return d.endswith("/en-us.pak")
    if "/translations/" in d or "/qml/" in d or "devtools_resources" in d:
        return False
    return not any(x in d for x in ("opengl32sw.dll", "qt6quick3d", "qt6shadertools", "qt6pdf", "qt6charts",
                                    "qt6datavisualization", "qt6graphs", "qt6designer", "qt6sql", "qt6test"))


a.binaries = [b for b in a.binaries if wanted(b[0])]
a.datas = [d for d in a.datas if wanted(d[0])]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Cuelog",
    icon=ROOT + "/cuelog/assets/cuelog.ico",
    version=SPECPATH + "/version_info.txt",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Cuelog", upx=False)
