"""Protect session keys / OAuth tokens at rest.

On Windows, values are encrypted with DPAPI (CryptProtectData), which ties them
to the current Windows user account. Elsewhere they fall back to base64 so the
format stays portable; the database file itself is git-ignored.
"""
from __future__ import annotations

import base64
import sys

PREFIX_DPAPI = "dpapi:"
PREFIX_PLAIN = "b64:"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _to_blob(data: bytes) -> _Blob:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _from_blob(blob: _Blob) -> bytes:
        out = ctypes.string_at(blob.pbData, blob.cbData)
        _kernel32.LocalFree(blob.pbData)
        return out

    def _protect(data: bytes) -> bytes:
        src, dst = _to_blob(data), _Blob()
        if not _crypt32.CryptProtectData(ctypes.byref(src), "Cuelog", None, None, None, 0x1, ctypes.byref(dst)):
            raise OSError("CryptProtectData failed")
        return _from_blob(dst)

    def _unprotect(data: bytes) -> bytes:
        src, dst = _to_blob(data), _Blob()
        if not _crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):
            raise OSError("CryptUnprotectData failed (was the database copied from another Windows account?)")
        return _from_blob(dst)
else:  # pragma: no cover - exercised on non-Windows only
    _protect = _unprotect = None


def seal(value: str) -> str:
    raw = value.encode("utf-8")
    if _protect is not None:
        try:
            return PREFIX_DPAPI + base64.b64encode(_protect(raw)).decode()
        except OSError:
            pass
    return PREFIX_PLAIN + base64.b64encode(raw).decode()


def unseal(value: str | None) -> str:
    if not value:
        return ""
    if value.startswith(PREFIX_DPAPI):
        if _unprotect is None:
            raise OSError("This secret was encrypted with Windows DPAPI and cannot be read here")
        return _unprotect(base64.b64decode(value[len(PREFIX_DPAPI):])).decode("utf-8")
    if value.startswith(PREFIX_PLAIN):
        return base64.b64decode(value[len(PREFIX_PLAIN):]).decode("utf-8")
    return value
