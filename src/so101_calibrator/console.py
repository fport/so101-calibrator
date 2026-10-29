"""Line-based wizard for the `calibrate` / `verify` / `teleop-test` commands (no full-screen UI)."""

from __future__ import annotations

import os
import sys
import threading
import time

from . import checks as K
from . import render
from .wizard import Session

TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
RESET, BOLD, DIM = ("\033[0m", "\033[1m", "\033[2m") if TTY else ("", "", "")
COLORS = {"pass": "\033[32m", "warn": "\033[33m", "fail": "\033[31m", "info": "\033[36m"}
ICONS = {"pass": "✔", "warn": "!", "fail": "✘", "pending": " ", "ready": "›", "running": "…"}
LIVE_TIMEOUT_UNATTENDED = 120.0  # --yes on real hardware: stop a live step after this long


def color(status: str, text: str) -> str:
    return f"{COLORS.get(status, '')}{text}{RESET}" if TTY else text


def print_checks(checks: list[K.Check]) -> None:
    for chk in checks:
        print(f"  {color(chk.status, ICONS[chk.status])} {chk.message}")
        if chk.fix and chk.status != K.PASS:
            print(f"      {DIM}→ {chk.fix}{RESET}")


class Keys:
    """Reads whole lines from stdin on a thread so live steps can be stopped with ENTER."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.event = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        for line in sys.stdin:
            self.lines.append(line.strip().lower())
            self.event.set()
        self.lines.append("eof")
        self.event.set()

    def get(self, timeout: float | None = None) -> str | None:
        if not self.lines and not self.event.wait(timeout):
            return None
        line = self.lines.pop(0)
        if not self.lines:
            self.event.clear()
        return line


def run_session(session: Session, yes: bool = False, force: bool = False, actor=None) -> int:
    """Drive a session from the terminal. yes: answer every prompt (warnings accepted, failures abort
    unless force). Live steps stop by themselves with the mock actor, else on ENTER."""
    keys = None if (yes and actor is not None) else Keys()
    total = len(session.steps)
    shown = None
    try:
        while not session.finished:
            snap = session.snapshot()
            cur = snap["current"]
            sid, status = cur["id"], cur["status"]
            if shown != sid:
                shown = sid
                n = next(i for i, s in enumerate(snap["steps"]) if s["id"] == sid) + 1
                print(f"\n{BOLD}[{n}/{total}] {cur['label']}{RESET}")
                print(f"  {cur['instructions']}")
            if status == "ready":
                if not yes and not cur["auto"]:  # checks that need nothing from the person just run
                    extra = " (ENTER again to stop)" if cur["live"] else ""
                    print(f"  {DIM}Press ENTER to start{extra}, 'a' to abort.{RESET}", flush=True)
                    if _key(keys) in ("a", "eof"):
                        session.act("abort")
                        break
                session.act("start", sid)
                if session.step is not None and session.step.id == sid and session.state.status == "running":
                    _live(session, sid, keys, yes, actor)
                after = session.step.id if session.step else None
                print_checks(session.results.get(sid, []))
                if after != sid:
                    continue
                status = session.state.status
            if status == "warn":
                if yes:
                    session.act("accept", sid)
                    continue
                print(f"  {DIM}ENTER: continue anyway · r: retry · a: abort{RESET}", flush=True)
                key = _key(keys)
                session.act({"a": "abort", "eof": "abort", "r": "retry"}.get(key or "", "accept"), sid)
                shown = None if key == "r" else shown
            elif status == "fail":
                if yes:
                    session.act("force" if force else "abort", sid)
                    continue
                print(f"  {DIM}ENTER: retry · f: accept anyway (not recommended) · a: abort{RESET}", flush=True)
                key = _key(keys)
                session.act({"a": "abort", "eof": "abort", "f": "force"}.get(key or "", "retry"), sid)
                shown = None if key not in ("a", "eof", "f") else shown
    except KeyboardInterrupt:
        print("\nInterrupted.")
        session.abort()
    finally:
        session.abort() if not session.finished else session.close()
    for notice in session.notices:
        print(f"\n{color('warn', '!')} {notice}")
    result = session.result()
    print(f"\n{BOLD}Result: {color(result if result != 'aborted' else 'fail', result.upper())}{RESET}")
    return 0 if result in ("pass", "warn") else 1


def _key(keys: Keys | None) -> str | None:
    return keys.get() if keys else "eof"


def _live(session: Session, step_id: str, keys: Keys | None, yes: bool, actor) -> None:
    lines = 0
    t_end = time.monotonic() + LIVE_TIMEOUT_UNATTENDED
    kind = session.step.kind if session.step else ""  # poll() may finish the step: read it once
    if keys:
        print(f"  {DIM}Press ENTER when done.{RESET}", flush=True)
    while session.step is not None and session.step.id == step_id and session.state.status == "running":
        session.poll()
        out = render.live_lines(session.snapshot())
        if sys.stdout.isatty():
            if lines:
                sys.stdout.write(f"\033[{lines}F")
            sys.stdout.write("\n".join(f"\033[2K{line}" for line in out) + "\n")
            sys.stdout.flush()
            lines = len(out)
        if keys and keys.get(timeout=0.04) is not None:
            session.act("stop", step_id)
        elif keys is None:
            time.sleep(0.03)
            mock_done = actor is not None and actor.idle() and kind == "ranges"
            if mock_done or time.monotonic() > t_end:
                session.act("stop", step_id)
