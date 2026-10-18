"""Audio leg: a null sink the roles play into, re-encoded per listener and served over HTTP."""

from __future__ import annotations

import contextlib
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def ensure_sink(name: str, env: dict[str, str], run: Any = subprocess.run) -> bool:
    """Create the null sink when absent; return True when it had to be created."""
    listing = run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, env=env, check=True).stdout
    if any(line.split("\t")[1:2] == [name] for line in listing.splitlines()):
        return False
    run(
        ["pactl", "load-module", "module-null-sink", f"sink_name={name}", f"sink_properties=device.description={name}"],
        capture_output=True,
        env=env,
        check=True,
    )
    return True


def encoder_command(audio: dict[str, Any]) -> list[str]:
    return [item.format(sink=audio["sink"]) for item in audio["command"]]


def make_server(audio: dict[str, Any], popen: Any = subprocess.Popen) -> ThreadingHTTPServer:
    path = str(audio.get("path", "/audio"))
    content_type = str(audio.get("content_type", "audio/ogg"))
    command = encoder_command(audio)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.split("?")[0] != path:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            encoder = popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                while chunk := os.read(encoder.stdout.fileno(), 4096):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with contextlib.suppress(ProcessLookupError):
                    encoder.kill()
                encoder.wait()

        def log_message(self, *_args: Any) -> None:
            return

    return ThreadingHTTPServer((str(audio.get("bind", "0.0.0.0")), int(audio["port"])), Handler)


def page(up: dict[str, Any]) -> str:
    """The stream page: noVNC in a frame plus an audio element that starts muted until the viewer clicks."""
    audio = up.get("audio")
    audio_url = f"http://' + location.hostname + ':{int(audio['port'])}{audio.get('path', '/audio')}" if audio else ""
    script = (
        f"""
const a = document.getElementById('a'), b = document.getElementById('b');
function connect() {{ a.src = '{audio_url}'; a.play().catch(() => {{}}); }}
a.addEventListener('error', () => setTimeout(connect, 2000));
a.addEventListener('ended', () => setTimeout(connect, 2000));
b.addEventListener('click', () => {{
  a.muted = !a.muted; a.play();
  b.textContent = a.muted ? 'Click to unmute' : 'Mute';
}});
connect();
"""
        if audio
        else ""
    )
    button = '<button id="b">Click to unmute</button><audio id="a" autoplay muted></audio>' if audio else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stream</title>
<style>
html,body{{margin:0;height:100%;background:#000}}
iframe{{border:0;width:100%;height:100%}}
button{{position:fixed;top:8px;right:8px;z-index:9;padding:6px 12px;font:14px sans-serif;opacity:.85}}
</style></head><body>
<iframe src="vnc.html?autoconnect=1&amp;resize=scale" allow="autoplay; clipboard-read; clipboard-write"></iframe>
{button}
<script>{script}</script>
</body></html>
"""
