"""Write packaging/version_info.txt (the Windows file properties of Cuelog.exe)."""
import sys
from pathlib import Path

version = sys.argv[1]
nums = tuple(int(x) for x in (version.split(".") + ["0"] * 4)[:4])
Path(__file__).with_name("version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'flodlol'),
      StringStruct('FileDescription', 'Cuelog'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Cuelog'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 flodlol. MIT licence.'),
      StringStruct('OriginalFilename', 'Cuelog.exe'),
      StringStruct('ProductName', 'Cuelog'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
