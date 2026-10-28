"""so101-cal: command line front end. Every command works on real hardware or, with --mock, on a simulated rig."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

from . import __version__, render
from . import checks as K
from .ports import diff_ports, list_ports
from .store import Store, StoreError, lerobot_calibration_path
from .wizard import Context, Session, Target

TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
RESET, BOLD, DIM = ("\033[0m", "\033[1m", "\033[2m") if TTY else ("", "", "")
COLORS = {"pass": "\033[32m", "warn": "\033[33m", "fail": "\033[31m", "info": "\033[36m"}
ICONS = {"pass": "✔", "warn": "!", "fail": "✘", "skipped": "-", "pending": " ", "ready": "›", "running": "…"}


def c(status: str, text: str) -> str:
    if not TTY:
        return text
    return f"{COLORS.get(status, '')}{text}{RESET}"


# --- environment (real or mock) -------------------------------------------------------------------
class Env:
    def __init__(self, args):
        self.store = Store(args.store)
        self.mock = args.mock
        self.rig = self.actor = None
        if self.mock:
            from .mock import MockActor, load_rig

            self.rig_path = self.store.root / ".mock-rig.json"
            self.rig = load_rig(self.rig_path, args.mock_faults)
            self.actor = MockActor(self.rig, speed=args.mock_speed)

    def open_bus(self, port: str):
        if self.mock:
            return self.rig.bus(port)
        from .bus import FeetechBus

        return FeetechBus(port)

    def list_ports(self) -> dict[str, str]:
        return self.rig.ports.list() if self.mock else list_ports()

    def context(self, args, **kw) -> Context:
        return Context(store=self.store, open_bus=self.open_bus, list_ports=self.list_ports, actor=self.actor,
                       supply=getattr(args, "supply", None), **kw)

    def save(self) -> None:
        if self.mock:
            from .mock import save_rig

            save_rig(self.rig, self.rig_path)


def _remembered_port(store: Store, arm_id: str) -> str | None:
    info = store.arm(arm_id)
    return info.get("port") if info else None


# --- interactive driver for a wizard session ------------------------------------------------------
class Keys:
    """Reads whole lines from stdin on a thread so live steps can be stopped with ENTER."""

    def __init__(self):
        self.lines: list[str] = []
        self.event = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
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


def run_session(session: Session, yes: bool, force: bool, actor=None) -> int:
    keys = None if yes else Keys()
    total = len(session.steps)
    shown = None
    try:
        while not session.finished:
            st, state = session.step, session.state
            if shown != st.id:
                shown = st.id
                print(f"\n{BOLD}[{session.current + 1}/{total}] {st.label}{RESET}")
                print(f"  {st.instructions(session)}")
            if state.status == "ready":
                if not yes:
                    prompt = "Press ENTER to start" if not st.live else "Press ENTER to start (ENTER again to stop)"
                    print(f"  {DIM}{prompt}, 'a' to abort.{RESET}", flush=True)
                    if keys.get() in ("a", "eof"):
                        session.abort()
                        break
                session.start()
                if session.step is st and session.state.status == "running":
                    _live(session, st, keys, yes, actor)
            if session.step is not st or session.finished:
                _print_checks(session.results.get(st.id, []))
                shown = None if session.step is not st else shown
                continue
            status = session.state.status
            _print_checks(session.results.get(st.id, []))
            if status == "warn":
                if yes:
                    session.next()
                    continue
                print(f"  {DIM}ENTER: continue anyway · r: retry · a: abort{RESET}", flush=True)
                key = keys.get()
                if key in ("a", "eof"):
                    session.abort()
                elif key == "r":
                    session.retry()
                    shown = None
                else:
                    session.next()
            elif status == "fail":
                if yes:
                    if force:
                        session.next(force=True)
                        continue
                    session.abort()
                    break
                print(f"  {DIM}ENTER: retry · f: accept anyway (not recommended) · a: abort{RESET}", flush=True)
                key = keys.get()
                if key in ("a", "eof"):
                    session.abort()
                elif key == "f":
                    session.next(force=True)
                else:
                    session.retry()
                    shown = None
    except KeyboardInterrupt:
        print("\nInterrupted.")
        session.abort()
    finally:
        session.close()
    result = session.result()
    print(f"\n{BOLD}Result: {c(result if result != 'aborted' else 'fail', result.upper())}{RESET}")
    return 0 if result in ("pass", "warn") else 1


def _live(session: Session, st, keys: Keys | None, yes: bool, actor) -> None:
    lines = 0
    t_end = time.monotonic() + 600
    while session.step is st and session.state.status == "running":
        session.poll()
        out = render.live_lines(session.snapshot())
        if sys.stdout.isatty():
            if lines:
                sys.stdout.write(f"\033[{lines}F")
            sys.stdout.write("\n".join(f"\033[2K{line}" for line in out) + "\n")
            sys.stdout.flush()
            lines = len(out)
        if keys and keys.get(timeout=0.04) is not None:
            session.stop()
        elif yes:
            time.sleep(0.03)
            mock_done = actor is not None and actor.idle() and st.kind in ("ranges", "direction")
            if mock_done or time.monotonic() > t_end:
                session.stop()


def _print_checks(checks: list[K.Check]) -> None:
    for chk in checks:
        print(f"  {c(chk.status, ICONS[chk.status])} {chk.message}")
        if chk.fix and chk.status != K.PASS:
            print(f"      {DIM}→ {chk.fix}{RESET}")


# --- commands --------------------------------------------------------------------------------------
def cmd_ports(args, env: Env) -> int:
    ports = env.list_ports()
    if not ports:
        print("No serial ports found. Plug in the board's USB cable.")
        return 1
    for p, serial in ports.items():
        print(f"{p}  {DIM}{serial}{RESET}")
    return 0


def cmd_find_port(args, env: Env) -> int:
    before = env.list_ports()
    print("Ports now:", ", ".join(before) or "none")
    if env.mock:
        port = env.rig.port_of("follower")
        env.rig.ports.unplug(port)
        print("(mock) unplugged the follower")
    else:
        input("Unplug the USB cable of the board you want to identify and press ENTER...")
    removed, _ = diff_ports(before, env.list_ports())
    for chk in K.check_port_found(removed, []):
        _print_checks([chk])
    if len(removed) == 1:
        print(f"\nThe port is {BOLD}{removed[0]}{RESET} (USB serial {before[removed[0]] or 'unknown'}). "
              "Plug the cable back in.")
        return 0
    return 1


def _target(args, env: Env, role: str, arm_id: str | None = None, port: str | None = None) -> Target:
    arm_id = arm_id or role
    if not port:
        port = _remembered_port(env.store, arm_id)
        if port and port not in env.list_ports():
            port = None  # remembered port is gone (other USB socket?): fall back to discovery
    return Target(role=role, arm_id=arm_id, port=port, note=getattr(args, "note", "") or "")


def cmd_calibrate(args, env: Env) -> int:
    target = _target(args, env, args.role, args.id, args.port)
    ctx = env.context(args, export=not args.no_export, export_dir=args.calibration_dir,
                      legacy_export=args.legacy_export)
    print(f"{BOLD}Calibrating {args.role} '{target.arm_id}'{RESET}" + (f" on {target.port}" if target.port else ""))
    code = run_session(Session.calibrate(ctx, target), args.yes, args.force, env.actor)
    env.save()
    return code


def cmd_verify(args, env: Env) -> int:
    target = _target(args, env, args.role, args.id, args.port)
    code = run_session(Session.verify(env.context(args), target), args.yes, args.force, env.actor)
    env.save()
    return code


def cmd_teleop_test(args, env: Env) -> int:
    leader = _target(args, env, "leader", args.leader_id, args.leader_port)
    follower = _target(args, env, "follower", args.follower_id, args.follower_port)
    ctx = env.context(args, teleop_seconds=args.seconds)
    code = run_session(Session.teleop(ctx, leader, follower), args.yes, args.force, env.actor)
    env.save()
    return code


def cmd_history(args, env: Env) -> int:
    arms = env.store.arms() if not args.id else [env.store.arm(args.id)]
    if not arms or arms == [None]:
        print(f"No calibrations in {env.store.root}")
        return 1
    for info in arms:
        print(f"{BOLD}{info['arm_id']}{RESET} ({info['role']}"
              + (f", USB {info['usb_serial']}" if info.get("usb_serial") else "") + ")")
        for rec in env.store.versions(info["arm_id"]):
            s = rec.summary()
            mark = c("pass", "* active") if rec.version == info.get("active") else "        "
            counts = s["checks"]
            checks = f"{counts['pass']}✔ {counts['warn']}! {counts['fail']}✘" if sum(counts.values()) else "no checks"
            print(f"  v{rec.version:<3} {mark} {s['created']}  {s['source']:<8} {checks:<12} {s.get('note', '')}")
    return 0


def cmd_show(args, env: Env) -> int:
    rec = env.store.get(args.id, args.version)
    if args.json:
        print(json.dumps(rec.data, indent=2))
        return 0
    s = rec.summary()
    print(f"{BOLD}{args.id} v{rec.version}{RESET}  {s['role']}  {s['created']}  port={s.get('port') or '-'}  "
          f"usb={s.get('usb_serial') or '-'}")
    if s.get("note"):
        print(f"note: {s['note']}")
    print(render.calibration_table(rec.calibration))
    bad = [K.Check(**ch) for ch in rec.data.get("checks", []) if ch["status"] != "pass"]
    if bad:
        print("\nChecks that did not pass when this version was made:")
        _print_checks(bad)
    return 0


def cmd_diff(args, env: Env) -> int:
    a = env.store.get(args.id, args.a)
    b = env.store.get(args.id, args.b)
    rows = env.store.diff(args.id, a.version, b.version)
    print(f"{args.id}: v{a.version} → v{b.version}")
    print(render.diff_table(rows) if rows else "identical")
    return 0


def cmd_activate(args, env: Env) -> int:
    rec = env.store.activate(args.id, args.version)
    print(f"{args.id}: v{rec.version} is now active")
    if not args.no_export:
        path = env.store.export(args.id, rec.version, args.calibration_dir, legacy=args.legacy_export)
        print(f"exported to {path}")
    print(f"{DIM}The motors still hold the old values until you run: so101-cal write {args.id} "
          f"or connect with LeRobot and answer ENTER (use file).{RESET}")
    return 0


def cmd_export(args, env: Env) -> int:
    path = env.store.export(args.id, args.version, args.calibration_dir, legacy=args.legacy_export)
    print(path)
    return 0


def cmd_import(args, env: Env) -> int:
    rec = env.store.import_file(args.file, args.id or args.role, args.role, args.note)
    print(f"imported as {rec.data['arm_id']} v{rec.version} (active)")
    return 0


def cmd_note(args, env: Env) -> int:
    env.store.set_note(args.id, args.version, args.text)
    return 0


def cmd_write(args, env: Env) -> int:
    from .bus import Arm

    rec = env.store.get(args.id, args.version)
    port = args.port or _remembered_port(env.store, args.id)
    if not port:
        print("No port known for this arm; pass --port.")
        return 1
    arm = Arm(env.open_bus(port))
    arm.connect()
    try:
        arm.set_torque(False)
        arm.write_calibration(rec.calibration)
        checks = K.check_motor_matches(rec.calibration, arm.read_calibration())
    finally:
        arm.close()
    _print_checks(checks)
    env.save()
    return 0 if K.worst(checks) != K.FAIL else 1


def cmd_where(args, env: Env) -> int:
    print(lerobot_calibration_path(args.role, args.id or args.role, legacy=args.legacy_export))
    return 0


def cmd_dashboard(args, env: Env) -> int:
    from .dashboard.server import serve

    serve(env, host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


# --- argument parsing ------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="so101-cal", description=__doc__)
    p.add_argument("--version", action="version", version=f"so101-calibrator {__version__}")
    p.add_argument("--store", help="calibration history directory (default ~/.so101-calibrator or $SO101CAL_HOME)")
    p.add_argument("--mock", action="store_true", help="use a simulated leader + follower instead of hardware")
    p.add_argument("--mock-faults", help="inject faults into the mock, e.g. missing:4,voltage:5,reversed:elbow_flex")
    p.add_argument("--mock-speed", type=float, default=1.0, help="speed-up of the simulated hand")
    sub = p.add_subparsers(dest="cmd", required=True)

    def interactive(sp):
        sp.add_argument("-y", "--yes", action="store_true", help="no prompts: continue on warnings, abort on failures")
        sp.add_argument("--force", action="store_true", help="with --yes: continue even on failures")
        sp.add_argument("--supply", choices=["12v", "7.4v", "5v"], help="follower supply preset (default 12v)")

    def export_opts(sp):
        sp.add_argument("--calibration-dir", help="export to this calibration_dir instead of the LeRobot cache")
        sp.add_argument("--legacy-export", action="store_true", help="use the so101_follower/so101_leader cache "
                        "directories of LeRobot < 0.4")

    sub.add_parser("ports", help="list serial ports").set_defaults(func=cmd_ports)
    sub.add_parser("find-port", help="identify a board by unplugging it").set_defaults(func=cmd_find_port)

    sp = sub.add_parser("calibrate", help="step-by-step calibration wizard")
    sp.add_argument("role", choices=["follower", "leader"])
    sp.add_argument("--id", help="LeRobot id of the arm (default: the role name)")
    sp.add_argument("--port", help="serial port (default: last used, else find by unplugging)")
    sp.add_argument("--note", default="", help="free text stored with this version")
    sp.add_argument("--no-export", action="store_true", help="only store the version, do not export")
    interactive(sp)
    export_opts(sp)
    sp.set_defaults(func=cmd_calibrate)

    sp = sub.add_parser("verify", help="check motors still match the active calibration and the middle pose")
    sp.add_argument("role", choices=["follower", "leader"])
    sp.add_argument("--id")
    sp.add_argument("--port")
    interactive(sp)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("teleop-test", help="short leader -> follower test with tracking checks")
    sp.add_argument("--leader-id")
    sp.add_argument("--follower-id")
    sp.add_argument("--leader-port")
    sp.add_argument("--follower-port")
    sp.add_argument("--seconds", type=float, default=15.0)
    interactive(sp)
    sp.set_defaults(func=cmd_teleop_test)

    sp = sub.add_parser("history", help="list stored versions")
    sp.add_argument("id", nargs="?")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("show", help="show one version (default: active)")
    sp.add_argument("id")
    sp.add_argument("version", nargs="?", type=int)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("diff", help="compare two versions (B defaults to the active one)")
    sp.add_argument("id")
    sp.add_argument("a", type=int)
    sp.add_argument("b", type=int, nargs="?")
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("activate", help="make a version active (rollback) and export it")
    sp.add_argument("id")
    sp.add_argument("version", type=int)
    sp.add_argument("--no-export", action="store_true")
    export_opts(sp)
    sp.set_defaults(func=cmd_activate)

    sp = sub.add_parser("export", help="write a version where LeRobot reads it")
    sp.add_argument("id")
    sp.add_argument("--version", type=int)
    export_opts(sp)
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("import", help="import an existing LeRobot calibration JSON as a new version")
    sp.add_argument("file")
    sp.add_argument("--role", required=True, choices=["follower", "leader"])
    sp.add_argument("--id")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("note", help="set the note of a version")
    sp.add_argument("id")
    sp.add_argument("version", type=int)
    sp.add_argument("text")
    sp.set_defaults(func=cmd_note)

    sp = sub.add_parser("write", help="write a stored version into the motors' EEPROM")
    sp.add_argument("id")
    sp.add_argument("--version", type=int)
    sp.add_argument("--port")
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser("where", help="print the LeRobot cache path for an arm")
    sp.add_argument("role", choices=["follower", "leader"])
    sp.add_argument("--id")
    sp.add_argument("--legacy-export", action="store_true")
    sp.set_defaults(func=cmd_where)

    sp = sub.add_parser("dashboard", help="open the local web dashboard")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8101)
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(func=cmd_dashboard)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # legacy Windows consoles cannot print the check icons
        sys.stdout.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    env = Env(args)
    try:
        return args.func(args, env) or 0
    except StoreError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
