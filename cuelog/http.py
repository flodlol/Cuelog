"""Minimal HTTP helpers on top of urllib (no third-party dependency)."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from . import USER_AGENT


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.body = body
        self.retry_after = retry_after


class NetworkError(RuntimeError):
    """The request may or may not have reached the server (timeout, DNS, reset)."""


MAX_BODY = 8 * 1024 * 1024


def request(method: str, url: str, *, params: dict | None = None, data: dict | bytes | None = None,
            headers: dict | None = None, timeout: float = 30) -> tuple[int, bytes, dict]:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    body = None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif isinstance(data, bytes):
        body = data
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_BODY), dict(resp.headers)
    except urllib.error.HTTPError as err:
        text = err.read(4000).decode("utf-8", errors="replace")
        retry = err.headers.get("Retry-After") if err.headers else None
        try:
            retry_s = float(retry) if retry else None
        except ValueError:
            retry_s = None
        raise HttpError(f"HTTP {err.code}", err.code, text, retry_s) from err
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise NetworkError(f"Network problem: {reason}") from err


def get_json(url: str, **kw):
    status, body, _ = request("GET", url, **kw)
    return _decode(body)


def post_json(url: str, **kw):
    status, body, _ = request("POST", url, **kw)
    return _decode(body)


def _decode(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise HttpError("The service returned a malformed (non-JSON) response", body=body[:500].decode("latin-1")) from err


class _ValidatingRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow: Callable[[str], bool]):
        self.allow = allow
        self.hops: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allow(newurl):
            raise HttpError(f"Refusing to follow a redirect to an unexpected site: {urllib.parse.urlsplit(newurl).hostname}")
        self.hops.append(newurl)
        if len(self.hops) > 6:
            raise HttpError("Too many redirects")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def resolve_redirects(url: str, allow: Callable[[str], bool], timeout: float = 15) -> str:
    """Follow redirects (validating every hop) and return the final URL."""
    handler = _ValidatingRedirect(lambda u: allow(u) or "on.soundcloud.com" in (urllib.parse.urlsplit(u).hostname or ""))
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 " + USER_AGENT}, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            final = resp.geturl()
    except urllib.error.HTTPError as err:
        if handler.hops and allow(handler.hops[-1]):
            return handler.hops[-1]
        raise HttpError(f"HTTP {err.code} while resolving the link", err.code) from err
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as err:
        raise NetworkError(f"Network problem: {getattr(err, 'reason', err)}") from err
    if not allow(final):
        raise HttpError("The short link did not lead to a SoundCloud page")
    return final
