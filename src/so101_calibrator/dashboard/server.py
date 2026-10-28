"""Local web dashboard: a thin JSON API over the same wizard sessions and store the CLI uses.

Standard library only. Binds to 127.0.0.1 by default and rejects requests whose Host header is not
local, so other web pages cannot drive the arm through the browser (DNS rebinding).
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..store import StoreError, lerobot_calibration_root
from ..wizard import Session, Target

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class App:
    """Holds the one active wizard session and polls it on a background thread."""

    def __init__(self, env, poll_hz: float = 25.0):
        self.env = env
        self.session: Session | None = None
        self.lock = threading.RLock()
        self.period = 1 / poll_hz
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            with self.lock:
                s = self.session
            if s is not None and not s.finished:
                try:
                    s.poll()
                except Exception as e:  # never let the poller die
                    s.log(f"poll error: {e}", "error")
            time.sleep(self.period)

    def shutdown(self) -> None:
        self._stop.set()
        with self.lock:
            if self.session:
                self.session.abort()
        self.env.save()

    # --- API ----------------------------------------------------------------------------------------
    def state(self) -> dict:
        with self.lock:
            snap = self.session.snapshot() if self.session else None
        return {
            "session": snap,
            "ports": self.env.list_ports(),
            "arms": self.env.store.arms(),
            "mock": self.env.mock,
            "store": _tilde(self.env.store.root),
            "lerobot_root": _tilde(self.env.store.lerobot_root or lerobot_calibration_root()),
        }

    def new_session(self, body: dict) -> dict:
        flow = body.get("flow")
        env = self.env

        def target(role, prefix=""):
            arm_id = (body.get(f"{prefix}arm_id") or role).strip()
            port = (body.get(f"{prefix}port") or "").strip() or None
            if port is None:
                info = env.store.arm(arm_id)
                remembered = info.get("port") if info else None
                port = remembered if remembered in env.list_ports() else None
            return Target(role=role, arm_id=arm_id, port=port, note=body.get("note", ""))

        ctx = env.context(_Args(body.get("supply") or None), export=body.get("export", True),
                          export_dir=body.get("calibration_dir") or None,
                          teleop_seconds=float(body.get("seconds") or 15))
        if flow == "calibrate":
            session = Session.calibrate(ctx, target(body["role"]))
        elif flow == "verify":
            session = Session.verify(ctx, target(body["role"]))
        elif flow == "teleop":
            session = Session.teleop(ctx, target("leader", "leader_"), target("follower", "follower_"))
        else:
            raise ValueError(f"unknown flow '{flow}'")
        with self.lock:
            if self.session:
                self.session.abort()
            self.session = session
        return {"ok": True}

    def action(self, body: dict) -> dict:
        with self.lock:
            s = self.session
        if s is None:
            raise ValueError("no session")
        name = body.get("action")
        if name == "start":
            s.start()
        elif name == "stop":
            s.stop()
        elif name == "retry":
            s.retry()
        elif name == "next":
            s.next()
        elif name == "force":
            s.next(force=True)
        elif name == "skip":
            s.skip()
        elif name == "abort":
            s.abort()
        elif name == "close":
            s.abort()
            with self.lock:
                self.session = None
        else:
            raise ValueError(f"unknown action '{name}'")
        self.env.save()
        return {"ok": True}

    def versions(self, arm_id: str) -> dict:
        info = self.env.store.arm(arm_id)
        if info is None:
            raise StoreError(f"unknown arm '{arm_id}'")
        return {"arm": info, "versions": [r.summary() for r in self.env.store.versions(arm_id)]}

    def record(self, arm_id: str, version: int) -> dict:
        return self.env.store.get(arm_id, version).data

    def diff(self, arm_id: str, a: int, b: int | None) -> dict:
        ra, rb = self.env.store.get(arm_id, a), self.env.store.get(arm_id, b)
        return {"a": ra.version, "b": rb.version, "rows": self.env.store.diff(arm_id, ra.version, rb.version)}

    def activate(self, arm_id: str, body: dict) -> dict:
        rec = self.env.store.activate(arm_id, int(body["version"]))
        path = None
        if body.get("export", True):
            path = str(self.env.store.export(arm_id, rec.version, body.get("calibration_dir") or None))
        return {"ok": True, "exported": path}

    def export(self, arm_id: str, body: dict) -> dict:
        v = body.get("version")
        path = self.env.store.export(arm_id, int(v) if v else None, body.get("calibration_dir") or None)
        return {"ok": True, "exported": str(path)}

    def note(self, arm_id: str, body: dict) -> dict:
        self.env.store.set_note(arm_id, int(body["version"]), body.get("note", ""))
        return {"ok": True}


def _tilde(path: Path) -> str:
    home = str(Path.home())
    p = str(path)
    return "~" + p[len(home):] if p.startswith(home + "/") else p


class _Args:
    def __init__(self, supply):
        self.supply = supply


def make_handler(app: App):
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


def make_server(env, host: str = "127.0.0.1", port: int = 8101) -> tuple[ThreadingHTTPServer, App]:
    if host not in ("127.0.0.1", "localhost", "::1"):
        _extra_hosts.add(host)
    app = App(env)
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
