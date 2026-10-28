"""Local web dashboard: a thin JSON API over the same wizard sessions and store the CLI uses.

Standard library only. It can move the follower, so it only answers the page it serves:
- binds to 127.0.0.1 by default and rejects a Host header that is not local (DNS rebinding);
- POST bodies must be `application/json` (a cross-origin page cannot send that without a CORS
  preflight, which this server never approves) and a present `Origin` must be this server's own.
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

from ..controller import Controller
from ..env import Env
from ..store import StoreError, lerobot_calibration_root, tilde

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
MAX_BODY = 64 * 1024


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def _str(body: dict, key: str) -> str | None:
    v = body.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise ApiError(f"'{key}' must be a string")
    return v.strip() or None


class DashboardAPI:
    """Request bodies -> controller / store calls. Every method returns JSON-able data."""

    def __init__(self, env: Env):
        self.env = env
        self.controller = Controller(env)

    def state(self) -> dict:
        return {
            "session": self.controller.snapshot(),
            "busy": self.controller.busy,
            "ports": self.env.list_ports(),
            "arms": self.env.store.arms(),
            "mock": self.env.mock,
            "store": tilde(self.env.store.root),
            "lerobot_root": tilde(self.env.store.lerobot_root or lerobot_calibration_root()),
        }

    def new_session(self, body: dict) -> dict:
        try:
            seconds = float(body.get("seconds") or 15)
        except (TypeError, ValueError) as e:
            raise ApiError("'seconds' must be a number") from e
        role = _str(body, "role") or "follower"
        if role not in ("leader", "follower"):
            raise ApiError("'role' must be leader or follower")
        self.controller.start(
            _str(body, "flow") or "", role=role, arm_id=_str(body, "arm_id"), port=_str(body, "port"),
            leader_id=_str(body, "leader_arm_id"), follower_id=_str(body, "follower_arm_id"),
            leader_port=_str(body, "leader_port"), follower_port=_str(body, "follower_port"),
            note=_str(body, "note") or "", supply=_str(body, "supply"), export=body.get("export") is not False,
            calibration_dir=_str(body, "calibration_dir"), teleop_seconds=seconds)
        return {"ok": True}

    def action(self, body: dict) -> dict:
        name = _str(body, "action")
        if name == "close":
            self.controller.close()
            return {"ok": True}
        if self.controller.session is None:
            raise ApiError("no session")
        return {"ok": self.controller.act(name or "", _str(body, "step"))}

    def versions(self, arm_id: str) -> dict:
        info = self.env.store.arm(arm_id)
        if info is None:
            raise ApiError(f"unknown arm '{arm_id}'", HTTPStatus.NOT_FOUND)
        return {"arm": info, "versions": [r.summary() for r in self.env.store.versions(arm_id)]}

    def record(self, arm_id: str, version: int) -> dict:
        return self.env.store.get(arm_id, version).data

    def diff(self, arm_id: str, a: int, b: int | None) -> dict:
        ra, rb = self.env.store.get(arm_id, a), self.env.store.get(arm_id, b)
        return {"a": ra.version, "b": rb.version, "rows": self.env.store.diff(arm_id, ra.version, rb.version)}

    def activate(self, arm_id: str, body: dict) -> dict:
        rec = self.env.store.activate(arm_id, int(body["version"]))
        path = None
        if body.get("export", True) is not False:
            path = str(self.env.store.export(arm_id, rec.version, _str(body, "calibration_dir")))
        return {"ok": True, "exported": path}

    def export(self, arm_id: str, body: dict) -> dict:
        v = body.get("version")
        path = self.env.store.export(arm_id, int(v) if v else None, _str(body, "calibration_dir"))
        return {"ok": True, "exported": str(path)}

    def note(self, arm_id: str, body: dict) -> dict:
        self.env.store.set_note(arm_id, int(body["version"]), _str(body, "note") or "")
        return {"ok": True}

    def shutdown(self) -> None:
        self.controller.shutdown()


def make_handler(api: DashboardAPI, allowed_hosts: set[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "so101-calibrator"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        # --- request checks -------------------------------------------------------------------------
        def _host(self) -> str:
            return (self.headers.get("Host") or "").rsplit(":", 1)[0]

        def _check_post(self) -> None:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise ApiError("POST bodies must be application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            origin = self.headers.get("Origin")
            if origin is not None:
                o = urlparse(origin)
                if o.scheme != "http" or o.netloc != self.headers.get("Host"):
                    raise ApiError("cross-origin request refused", HTTPStatus.FORBIDDEN)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError("request too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except json.JSONDecodeError as e:
                raise ApiError("invalid JSON") from e
            if not isinstance(data, dict):
                raise ApiError("the JSON body must be an object")
            return data

        # --- responses ------------------------------------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _static(self, name: str) -> None:
            path = (STATIC / name).resolve()
            if STATIC.resolve() not in path.parents or not path.is_file():
                raise ApiError("not found", HTTPStatus.NOT_FOUND)
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._send(200, path.read_bytes(), ctype)

        # --- routing --------------------------------------------------------------------------------
        def _route(self, method: str) -> None:
            if self._host() not in allowed_hosts:
                raise ApiError("forbidden host", HTTPStatus.FORBIDDEN)
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if method == "GET" and not parts:
                return self._static("index.html")
            if method == "GET" and parts[0] == "static":
                return self._static("/".join(parts[1:]))
            if parts[:1] != ["api"]:
                raise ApiError("not found", HTTPStatus.NOT_FOUND)
            if method == "POST":
                self._check_post()
            route = parts[1:]
            if method == "GET" and route == ["state"]:
                return self._json(api.state())
            if method == "POST" and route == ["session"]:
                return self._json(api.new_session(self._body()))
            if method == "POST" and route == ["session", "action"]:
                return self._json(api.action(self._body()))
            if len(route) >= 2 and route[0] == "arms":
                arm_id = route[1]
                if method == "GET" and route[2:] == ["versions"]:
                    return self._json(api.versions(arm_id))
                if method == "GET" and len(route) == 4 and route[2] == "versions":
                    return self._json(api.record(arm_id, int(route[3])))
                if method == "GET" and route[2:] == ["diff"]:
                    return self._json(api.diff(arm_id, int(q["a"]), int(q["b"]) if q.get("b") else None))
                if method == "POST" and route[2:] in (["activate"], ["export"], ["note"]):
                    return self._json(getattr(api, route[2])(arm_id, self._body()))
            raise ApiError("not found", HTTPStatus.NOT_FOUND)

        def _dispatch(self, method: str) -> None:
            try:
                self._route(method)
            except ApiError as e:
                self._json({"error": str(e)}, e.status)
            except (StoreError, ValueError, KeyError) as e:
                self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            except Exception as e:  # never drop the connection without an answer
                self._json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


def make_server(env: Env, host: str = "127.0.0.1", port: int = 8101) -> tuple[ThreadingHTTPServer, DashboardAPI]:
    api = DashboardAPI(env)
    allowed = LOCAL_HOSTS | ({host} if host not in ("0.0.0.0", "::") else set())
    server = ThreadingHTTPServer((host, port), make_handler(api, allowed))
    server.daemon_threads = True
    return server, api


def serve(env: Env, host: str = "127.0.0.1", port: int = 8101, open_browser: bool = True) -> None:
    server, api = make_server(env, host, port)
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
        api.shutdown()
        server.server_close()
