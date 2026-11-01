"""Stand-alone SoundCloud player window (Microsoft Edge WebView2 via pywebview).

Qt WebEngine as shipped on PyPI cannot decode AAC, and many SoundCloud uploads
are AAC-only, so playback runs in a WebView2 window (built into Windows 10/11),
which can. The official Widget API events are forwarded to the main app as JSON
lines on stdout; commands (seek/play/pause/close) arrive as JSON lines on stdin.

    python -m cuelog.player_proc <soundcloud-url> [seek-seconds] [window-title]
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse

from .urls import UrlError, parse_soundcloud

ACCENT = "#ff5a1f"


def emit(obj: dict) -> None:
    obj.setdefault("wall", time.time())
    try:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def player_html(url: str, seek_s: int = 0) -> str:
    src = "https://w.soundcloud.com/player/?" + urllib.parse.urlencode({
        "url": url, "auto_play": "false", "visual": "true", "show_comments": "false", "hide_related": "true",
        "show_reposts": "false", "show_teaser": "false", "color": ACCENT})
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Cuelog player</title>
<style>html,body{{margin:0;height:100%;background:#0b0b0c;overflow:hidden}}iframe{{border:0;width:100%;height:100%}}</style>
<script src="https://w.soundcloud.com/player/api.js"></script></head><body>
<iframe id="sc" allow="autoplay; encrypted-media" src="{src}"></iframe>
<script>
var w = null, seekTo = {int(seek_s) * 1000};
function api() {{ return (window.pywebview && window.pywebview.api) || null; }}
function send(n, p) {{ var a = api(); if (a) a.event(n, p || 0); }}
function start() {{
  if (typeof SC === 'undefined') {{ var a = api(); a && a.error('The SoundCloud player script could not load.'); return; }}
  w = SC.Widget(document.getElementById('sc'));
  w.bind(SC.Widget.Events.READY, function() {{
    w.getDuration(function(d) {{ var a = api(); a && a.duration(d || 0); }});
    send('READY', 0);
    if (seekTo > 0) {{ w.seekTo(seekTo); }}
  }});
  ['PLAY','PAUSE','FINISH','SEEK'].forEach(function(n) {{
    w.bind(SC.Widget.Events[n], function(e) {{ send(n, (e && e.currentPosition) || 0); }});
  }});
  var last = 0;
  w.bind(SC.Widget.Events.PLAY_PROGRESS, function(e) {{
    var now = Date.now();
    if (now - last >= 400) {{ last = now; send('PROGRESS', (e && e.currentPosition) || 0); }}
  }});
  w.bind(SC.Widget.Events.ERROR, function() {{ var a = api(); a && a.error('SoundCloud reported a playback error.'); }});
}}
window.addEventListener('pywebviewready', start);
window.cuelogSeek = function(ms) {{ if (w) {{ w.seekTo(ms); w.play(); }} }};
window.cuelogPlay = function() {{ if (w) w.play(); }};
window.cuelogPause = function() {{ if (w) w.pause(); }};
window.cuelogVolume = function(v) {{ if (w) w.setVolume(v); }};
</script></body></html>"""


class Api:
    def event(self, name, pos_ms):
        emit({"type": "event", "name": str(name), "pos": float(pos_ms or 0)})

    def duration(self, ms):
        emit({"type": "duration", "ms": float(ms or 0)})

    def error(self, msg):
        emit({"type": "error", "message": str(msg)[:300]})


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    try:
        url = argv[1] if argv[1].startswith("https://api.soundcloud.com/") else parse_soundcloud(argv[1]).url
    except UrlError as err:
        emit({"type": "error", "message": str(err)})
        return 2
    seek = int(float(argv[2])) if len(argv) > 2 else 0
    title = argv[3] if len(argv) > 3 else "Cuelog player"
    try:
        import webview
    except ImportError:
        emit({"type": "error", "message": "pywebview is not installed; reinstall Cuelog (or run start.bat again from source)."})
        return 3
    window = webview.create_window(f"{title} · Cuelog", html=player_html(url, seek), js_api=Api(), width=720, height=420,
                                   min_size=(420, 300), background_color="#0b0b0c")

    def commands():
        for line in sys.stdin:
            try:
                cmd = json.loads(line)
            except ValueError:
                continue
            op = cmd.get("op")
            try:
                if op == "seek":
                    window.evaluate_js(f"window.cuelogSeek({int(float(cmd.get('ms', 0)))})")
                elif op == "play":
                    window.evaluate_js("window.cuelogPlay()")
                elif op == "pause":
                    window.evaluate_js("window.cuelogPause()")
                elif op == "volume":
                    window.evaluate_js(f"window.cuelogVolume({max(0, min(100, int(cmd.get('value', 100))))})")
                elif op == "close":
                    window.destroy()
                    return
                elif op == "focus":
                    window.restore()
                    window.show()
            except Exception as err:  # window may be closing
                emit({"type": "error", "message": f"Command failed: {err}"})
        # parent went away: close the player too
        try:
            window.destroy()
        except Exception:
            pass

    window.events.closed += lambda: emit({"type": "closed"})
    threading.Thread(target=commands, daemon=True).start()
    emit({"type": "started"})
    webview.start(gui="edgechromium", private_mode=False, storage_path=_storage())
    return 0


def _storage() -> str:
    from .config import data_dir
    p = data_dir() / "webview2"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
