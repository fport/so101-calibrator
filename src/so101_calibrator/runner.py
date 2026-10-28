"""Session runner shared by the dashboard and the TUI."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .store import StoreError, lerobot_calibration_root
from .wizard import Session, Target


def tilde(path: Path) -> str:
    home = str(Path.home())
    p = str(path)
    return "~" + p[len(home):] if p.startswith(home + "/") else p


class _Args:
    def __init__(self, supply):
        self.supply = supply


class Runner:
    """Holds the one active wizard session, polls it on a background thread and wraps the store.

    Shared by the dashboard and the TUI; every method takes and returns plain data.
    """

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
            "store": tilde(self.env.store.root),
            "lerobot_root": tilde(self.env.store.lerobot_root or lerobot_calibration_root()),
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
