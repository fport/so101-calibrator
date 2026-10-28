"""Local web dashboard: a thin JSON API over the same wizard sessions and store the CLI uses.

Standard library only. Binds to 127.0.0.1 by default and rejects requests whose Host header is not
local, so other web pages cannot drive the arm through the browser (DNS rebinding).
"""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..runner import Runner, tilde
from ..store import StoreError

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


_tilde = tilde


def make_handler(app: Runner):
    class Handler(BaseHTTPRequestHandler):
        server_version = "so101-calibrator"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in LOCAL_HOSTS or app_host_allowed(host)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _dispatch(self, method: str) -> None:
            if not self._host_ok():
                return self._json({"error": "forbidden host"}, HTTPStatus.FORBIDDEN)
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if method == "GET" and not parts:
                    return self._static("index.html")
                if method == "GET" and parts[0] == "static":
                    return self._static("/".join(parts[1:]))
                if parts[:1] != ["api"]:
                    return self._json({"error": "not found"}, 404)
                route = parts[1:]
                if method == "GET" and route == ["state"]:
                    return self._json(app.state())
                if method == "POST" and route == ["session"]:
                    return self._json(app.new_session(self._body()))
                if method == "POST" and route == ["session", "action"]:
                    return self._json(app.action(self._body()))
                if len(route) >= 2 and route[0] == "arms":
                    arm_id = route[1]
                    if method == "GET" and route[2:] == ["versions"]:
                        return self._json(app.versions(arm_id))
                    if method == "GET" and len(route) == 4 and route[2] == "versions":
                        return self._json(app.record(arm_id, int(route[3])))
                    if method == "GET" and route[2:] == ["diff"]:
                        b = int(q["b"]) if q.get("b") else None
                        return self._json(app.diff(arm_id, int(q["a"]), b))
                    if method == "POST" and route[2:] in (["activate"], ["export"], ["note"]):
                        return self._json(getattr(app, route[2])(arm_id, self._body()))
                return self._json({"error": "not found"}, 404)
            except (StoreError, ValueError, KeyError) as e:
                return self._json({"error": str(e)}, 400)

        def _static(self, name: str) -> None:
            path = (STATIC / name).resolve()
            if STATIC.resolve() not in path.parents or not path.is_file():
                return self._json({"error": "not found"}, 404)
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._send(200, path.read_bytes(), ctype)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


_extra_hosts: set[str] = set()


def app_host_allowed(host: str) -> bool:
    return host in _extra_hosts


def make_server(env, host: str = "127.0.0.1", port: int = 8101) -> tuple[ThreadingHTTPServer, Runner]:
    if host not in ("127.0.0.1", "localhost", "::1"):
        _extra_hosts.add(host)
    app = Runner(env)
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server, app


def serve(env, host: str = "127.0.0.1", port: int = 8101, open_browser: bool = True) -> None:
    server, app = make_server(env, host, port)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{server.server_address[1]}/"
    print(f"so101-calibrator dashboard on {url}" + ("  (mock hardware)" if env.mock else ""))
    print("Ctrl+C to quit.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        server.server_close()
