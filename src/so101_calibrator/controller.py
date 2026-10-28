"""Runs one wizard session in the background for interactive front ends (TUI, dashboard).

The controller polls the session on its own thread and keeps the latest snapshot, so a front end never
waits for the bus: while a slow action (a motor scan at every baud rate, an EEPROM write) holds the
session, the UI keeps drawing the last snapshot and shows what is `busy`.
"""

from __future__ import annotations

import threading
import time

from .env import Env
from .wizard import Session


class Controller:
    def __init__(self, env: Env, poll_hz: float = 25.0):
        self.env = env
        self._session: Session | None = None
        self._snapshot: dict | None = None
        self._busy: str | None = None
        self._lock = threading.Lock()  # guards the three fields above, never held during bus I/O
        self._period = 1 / poll_hz
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, name="so101-poll", daemon=True)
        self._thread.start()

    # --- state for the front ends -------------------------------------------------------------------
    @property
    def session(self) -> Session | None:
        with self._lock:
            return self._session

    def snapshot(self) -> dict | None:
        """The latest snapshot of the current session (never blocks on the hardware)."""
        with self._lock:
            return self._snapshot

    @property
    def busy(self) -> str | None:
        """Name of the action being executed right now, if any."""
        with self._lock:
            return self._busy

    # --- session lifecycle --------------------------------------------------------------------------
    def start(self, flow: str, *, role: str = "follower", arm_id: str | None = None, port: str | None = None,
              leader_id: str | None = None, follower_id: str | None = None, leader_port: str | None = None,
              follower_port: str | None = None, note: str = "", supply: str | None = None, export: bool = True,
              calibration_dir: str | None = None, teleop_seconds: float = 15.0) -> Session:
        """Abort the current session (if any) and start a new one."""
        if not 0 < teleop_seconds <= 600:
            raise ValueError("teleop_seconds must be between 0 and 600")
        env = self.env
        ctx = env.context(supply=supply, export=export, export_dir=calibration_dir, teleop_seconds=teleop_seconds)
        if flow == "calibrate":
            session = Session.calibrate(ctx, env.target(role, arm_id, port, note))
        elif flow == "verify":
            session = Session.verify(ctx, env.target(role, arm_id, port))
        elif flow == "teleop":
            session = Session.teleop(ctx, env.target("leader", leader_id, leader_port),
                                     env.target("follower", follower_id, follower_port))
        else:
            raise ValueError(f"unknown flow '{flow}'")
        self.close()
        with self._lock:
            self._session, self._snapshot = session, session.snapshot()
        return session

    def act(self, action: str, step_id: str | None = None) -> bool:
        """Run a session action (blocking; call it off the UI thread). See Session.act."""
        session = self.session
        if session is None:
            return False
        with self._lock:
            self._busy = action
        try:
            done = session.act(action, step_id)
        finally:
            with self._lock:
                self._busy = None
        self._refresh(session)
        self.env.save()
        return done

    def close(self) -> None:
        """Abort and forget the current session (follower torque off)."""
        with self._lock:
            session, self._session, self._snapshot = self._session, None, None
        if session is not None:
            session.abort()
            self.env.save()

    def shutdown(self) -> None:
        """Stop polling and close the session. Safe to call more than once."""
        if self._stop.is_set():
            return
        self._stop.set()
        self.close()

    # --- internals ----------------------------------------------------------------------------------
    def _refresh(self, session: Session) -> None:
        snap = session.snapshot()
        with self._lock:
            if self._session is session:
                self._snapshot = snap

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            session = self.session
            if session is not None:
                try:
                    if not session.finished:
                        session.poll()
                    self._refresh(session)
                except Exception as e:  # never let the poller die; the session shows the error
                    session.log(f"poll error: {e}", "error")
            time.sleep(self._period)
