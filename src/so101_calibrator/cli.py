"""so101-cal: command line front end. Every command works on real hardware or, with --mock, on a simulated rig."""

from __future__ import annotations

import argparse
import json
import signal
import sys

from . import __version__, render
from . import checks as K
from .bus import BusError
from .console import BOLD, DIM, RESET, color, print_checks, run_session
from .env import Env
from .ports import diff_ports
from .store import Store, StoreError
from .wizard import Session


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
        print_checks([chk])
    if len(removed) == 1:
        print(f"\nThe port is {BOLD}{removed[0]}{RESET} (USB serial {before[removed[0]] or 'unknown'}). "
              "Plug the cable back in.")
        return 0
    return 1


def cmd_calibrate(args, env: Env) -> int:
    target = env.target(args.role, args.id, args.port, args.note)
    ctx = env.context(supply=args.supply, export=not args.no_export, export_dir=args.calibration_dir,
                      legacy_export=args.legacy_export)
    print(f"{BOLD}Calibrating {args.role} '{target.arm_id}'{RESET}" + (f" on {target.port}" if target.port else ""))
    code = run_session(Session.calibrate(ctx, target), args.yes, args.force, env.actor)
    env.save()
    return code


def cmd_verify(args, env: Env) -> int:
    target = env.target(args.role, args.id, args.port)
    code = run_session(Session.verify(env.context(supply=args.supply), target), args.yes, args.force, env.actor)
    env.save()
    return code


def cmd_teleop_test(args, env: Env) -> int:
    leader = env.target("leader", args.leader_id, args.leader_port)
    follower = env.target("follower", args.follower_id, args.follower_port)
    ctx = env.context(supply=args.supply, teleop_seconds=args.seconds)
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
            mark = color("pass", "* active") if rec.version == info.get("active") else "        "
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
        print_checks(bad)
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
    from . import profiles as P
    from .bus import Arm

    rec = env.store.get(args.id, args.version)
    role = rec.data["role"]
    port = args.port or env.remembered_port(args.id)
    if not port:
        print(f"'{args.id}' is not plugged in (or its port is unknown): pass --port.", file=sys.stderr)
        return 1
    arm = Arm(env.open_bus(port))
    try:
        arm.connect()
        # Writing a follower calibration into the leader (or the other way round) would go unnoticed by
        # the read-back check, so first make sure the arm on this port is the one we think it is.
        checks = K.check_motors(arm.scan(), None)
        if K.worst(checks) != K.FAIL:
            checks += K.check_power(arm.voltages(), arm.temperatures(), P.profile(role))
        if K.worst(checks) == K.FAIL and not args.force:
            print_checks([c for c in checks if c.status != K.PASS])
            print(f"Not writing: is {port} really the {role} '{args.id}'? (--force to write anyway)", file=sys.stderr)
            return 1
        arm.set_torque(False)
        arm.write_calibration(rec.calibration)
        checks = K.check_motor_matches(rec.calibration, arm.read_calibration())
    finally:
        arm.release()
    print_checks(checks)
    env.save()
    return 0 if K.worst(checks) != K.FAIL else 1


def cmd_where(args, env: Env) -> int:
    print(env.store.lerobot_path(args.role, args.id or args.role, legacy=args.legacy_export))
    return 0


def cmd_tui(args, env: Env) -> int:
    from .tui import run

    return run(env, args.ai, args.lang)


def cmd_ai(args, env: Env) -> int:
    from . import ai as AI

    root = env.store.root
    try:
        if args.action == "use":
            if not args.arg:
                print("usage: so101-cal ai use ollama[:model] | claude[:model] | openai:<model>")
                return 2
            spec = args.arg[0]
            AI.parse_spec(spec)
            AI.save_spec(root, spec)
            print(f"AI helper: {spec} (saved in {root / 'config.json'})")
            return 0
        if args.action == "off":
            AI.save_spec(root, None)
            print("AI helper off.")
            return 0
        spec = AI.load_spec(root, args.ai)
        if args.action == "status":
            print(f"AI helper: {spec or 'off'}")
            if not spec:
                print("Turn it on: so101-cal ai use ollama  (local)  or  so101-cal ai use claude  (API)")
            return 0
        if not spec:
            print("AI helper is off: so101-cal ai use ollama | claude | openai:<model>", file=sys.stderr)
            return 2
        print(AI.Assistant(spec, store=env.store).ask(" ".join(args.arg), AI.describe(None, env.store.arms())))
        return 0
    except AI.AIError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


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
    p.add_argument("--ai", metavar="SPEC", help="AI helper: ollama[:model], claude[:model], openai:<model> or off "
                   "(default: `so101-cal ai use ...` or $SO101CAL_AI)")
    p.add_argument("--lang", choices=["en", "tr"], help="language of the terminal UI (default: saved choice or locale)")
    sub = p.add_subparsers(dest="cmd", metavar="command")
    p.set_defaults(func=cmd_tui)

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
    sp.add_argument("--force", action="store_true", help="write even if the arm on the port does not look right")
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser("where", help="print the LeRobot cache path for an arm")
    sp.add_argument("role", choices=["follower", "leader"])
    sp.add_argument("--id")
    sp.add_argument("--legacy-export", action="store_true")
    sp.set_defaults(func=cmd_where)

    sub.add_parser("tui", help="the terminal UI (also: so101-cal with no command)").set_defaults(func=cmd_tui)

    sp = sub.add_parser("ai", help="set up or use the optional AI helper")
    sp.add_argument("action", nargs="?", choices=["status", "use", "off", "ask"], default="status")
    sp.add_argument("arg", nargs="*", help="use: ollama[:model] | claude[:model] | openai:<model>; ask: a question")
    sp.set_defaults(func=cmd_ai)

    sp = sub.add_parser("dashboard", help="open the local web dashboard")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8101)
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(func=cmd_dashboard)
    return p


def _exit_on_signal(signum, _frame):
    # Closing the terminal (SIGHUP) or `kill` (SIGTERM) must still run the `finally` blocks that turn
    # follower torque off, so turn the signal into an ordinary exit.
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # legacy Windows consoles cannot print the check icons
        sys.stdout.reconfigure(errors="replace")
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _exit_on_signal)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        env = Env(Store(args.store), mock=args.mock, mock_faults=args.mock_faults, mock_speed=args.mock_speed)
        return args.func(args, env) or 0
    except (StoreError, BusError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
