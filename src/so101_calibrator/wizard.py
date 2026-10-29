"""The wizard engine shared by the CLI, the TUI and the dashboard.

A `Session` runs a flow (calibrate / verify / teleop) as a list of `Step`s. Each step is either
*instant* (start -> do the work -> checks) or *live* (start -> polled while the user moves the arm ->
stop, by the user or by the step itself -> checks). Front ends call `act(action, step_id)` (or the
individual action methods), `poll()` at ~20-30 Hz, and render `snapshot()`.

Safety rule: whatever happens (abort, retry, a bus error, a crash of the front end that still reaches
`close()`), follower torque is switched off before the ports are released.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from . import calibration as C
from . import checks as K
from . import i18n
from . import profiles as P
from .bus import Arm, BusError, MotorBus
from .ports import diff_ports
from .profiles import FULL_TURN_JOINTS, HALF_TURN, JOINTS, MOTOR_IDS
from .store import Store

Status = Literal["pending", "ready", "running", "pass", "warn", "fail"]
ACTIONS = ("start", "stop", "retry", "accept", "force", "abort")


@dataclass
class Target:
    role: str
    arm_id: str
    port: str | None = None  # None: discover by unplugging
    note: str = ""


@dataclass
class Context:
    store: Store
    open_bus: Callable[[str], MotorBus]
    list_ports: Callable[[], dict[str, str]]
    actor: Any = None  # mock.MockActor in demo mode: plays the human
    supply: str | None = None  # follower supply preset override ("12v", "7.4v")
    export: bool = True  # write the new calibration where LeRobot reads it
    export_dir: str | None = None  # custom calibration_dir instead of the LeRobot cache
    legacy_export: bool = False
    teleop_seconds: float = 15.0


@dataclass
class Draft:
    """What the steps of one session produce for the later ones."""

    offsets: dict[str, int] | None = None  # homing step
    mins: dict[str, int] | None = None  # ranges step
    maxes: dict[str, int] | None = None
    record: int | None = None  # version saved by the save step
    stored: dict[str, C.Calibration] = field(default_factory=dict)  # role -> active version (match steps)

    def ranges(self) -> C.Calibration | None:
        """The ranges recorded so far as a calibration (offsets 0), for display."""
        if self.mins is None or self.maxes is None:
            return None
        return {j: C.MotorCalibration(MOTOR_IDS[j], 0, 0, self.mins[j], self.maxes[j]) for j in JOINTS}


# Human names of Draft fields, for "cannot run" messages when a step that produces them was forced past.
PRODUCED_BY = {
    "offsets": "the middle pose",
    "ranges": "the ranges of motion",
    "stored:leader": "the leader's stored calibration",
    "stored:follower": "the follower's stored calibration",
}


def wrap_delta(a: int, b: int) -> int:
    """b - a on a 12-bit circle."""
    return (b - a + 2048) % 4096 - 2048


def _failure(kind: str, e: Exception) -> K.Check:
    if isinstance(e, BusError):
        return K.Check(f"{kind}.bus", K.FAIL, f"Communication error: {e}",
                       "Check the USB cable and the power supply, then press Retry.")
    return K.Check(f"{kind}.error", K.FAIL, f"Unexpected error: {type(e).__name__}: {e}",
                   "Retry; if it keeps happening please report it with this message.")


# ==================================================================================================
class Step:
    """One step of a flow. Subclasses override the hooks they need; every hook has a safe default."""

    kind = "step"
    title = "Step"
    live = False
    needs: tuple[str, ...] = ()  # keys of PRODUCED_BY that earlier steps must have produced

    def __init__(self, role: str, multi: bool = False):
        self.role = role
        self.id = f"{self.kind}:{role}" if multi else self.kind
        self.label = f"{self.title} ({role})" if multi else self.title
        self.reset()

    # --- lifecycle ----------------------------------------------------------------------------------
    def reset(self) -> None:
        """Forget the state of a previous attempt (called whenever the step becomes ready)."""

    def is_live(self, s: Session) -> bool:
        return self.live

    def on_ready(self, s: Session) -> None:
        """Prepare the hardware when the step becomes current (e.g. torque off)."""

    def run(self, s: Session) -> list[K.Check]:  # instant steps
        raise NotImplementedError

    def begin(self, s: Session) -> list[K.Check] | None:  # live steps; return checks to fail early
        return None

    def tick(self, s: Session) -> None:
        pass

    def done(self, s: Session) -> bool:  # live steps may finish themselves
        return False

    def end(self, s: Session) -> list[K.Check]:
        raise NotImplementedError

    def abort(self, s: Session) -> None:
        """Make the hardware safe after an interrupted attempt. Must not raise."""

    # --- presentation -------------------------------------------------------------------------------
    def instructions(self, s: Session) -> str:
        return ""

    def hint_spec(self, s: Session) -> tuple[str, dict]:
        """What to do now, as an i18n key and its arguments (front ends render it in their language)."""
        return f"hint.{self.kind}", {}

    def hint(self, s: Session) -> str:
        """The hint in plain English (CLI, dashboard, AI prompt)."""
        return i18n.plain(i18n.hint(*self.hint_spec(s), lang="en"))

    def illustration(self, s: Session) -> str | None:
        """Name of the animation the front ends show for this step (see so101_calibrator.assets)."""
        return None

    def progress(self, s: Session) -> dict:
        return {}

    def missing(self, s: Session) -> list[str]:
        have = {"offsets": s.draft.offsets is not None, "ranges": s.draft.ranges() is not None,
                **{f"stored:{r}": r in s.draft.stored for r in ("leader", "follower")}}
        return sorted({PRODUCED_BY[k] for k in self.needs if not have[k]})

    @property
    def roles(self) -> tuple[str, ...]:
        """The arms this step talks to; losing one of them while it runs fails the step."""
        return (self.role,)

    def arm(self, s: Session, role: str | None = None) -> Arm:
        role = role or self.role
        if role not in s.arms:
            raise BusError(f"the {role} is not connected (the motor scan did not pass)")
        return s.arms[role]

    def running(self, s: Session) -> bool:
        return s.step is self and s.state is not None and s.state.status == "running"


class PortStep(Step):
    kind, title = "port", "Find the USB port"

    def reset(self):
        self.before: dict[str, str] = {}
        self.removed: list[str] = []
        self.phase = "idle"

    def is_live(self, s):
        return s.targets[self.role].port is None

    def instructions(self, s):
        t = s.targets[self.role]
        if t.port:
            return f"Using {t.port} for the {self.role}. Make sure its USB cable is plugged in."
        return (f"Plug in ONLY the {self.role}'s USB cable and power. Press Start, then unplug the {self.role}'s "
                "USB cable. Plug it back in when asked. The port is detected automatically.")

    def hint_spec(self, s):
        port = s.targets[self.role].port
        if port and self.phase == "idle":
            return "hint.port.known", {"port": port}
        phase = self.phase if self.phase in ("unplug", "replug", "ambiguous") else "ready"
        return f"hint.port.{phase}", {"role": self.role}

    def illustration(self, s):
        return "usb"

    def run(self, s):
        return K.check_port_exists(s.targets[self.role].port, s.ctx.list_ports())

    def begin(self, s):
        self.before = s.ctx.list_ports()
        self.removed, self.phase = [], "unplug"
        if not self.before:
            return [K.Check("port.found", K.FAIL, "No serial ports at all.",
                            "Plug the board's USB cable in first (and install the CH343/CH340 driver on "
                            "Windows/older macOS if the board never shows up).")]
        return None

    def tick(self, s):
        now = s.ctx.list_ports()
        removed, _ = diff_ports(self.before, now)
        if self.phase == "unplug" and removed:
            self.removed = removed
            self.phase = "replug" if len(removed) == 1 else "ambiguous"
            if len(removed) == 1:
                s.log(f"{removed[0]} disappeared - plug the cable back in.")
        elif self.phase == "replug" and self.removed[0] in now:
            self.phase = "back"

    def done(self, s):
        return self.phase in ("back", "ambiguous")

    def end(self, s):
        checks = K.check_port_found(self.removed, [])
        if len(self.removed) == 1:
            port = self.removed[0]
            s.targets[self.role].port = port
            if self.phase != "back":
                checks.append(K.Check("port.replugged", K.FAIL, f"{port} has not come back yet.",
                                      "Plug the USB cable back in, then press Retry on the next step."))
        return checks

    def progress(self, s):
        return {"phase": self.phase, "removed": self.removed}


class MotorsStep(Step):
    kind, title = "motors", "Connect and scan motors"

    def instructions(self, s):
        return (f"Connects to {s.targets[self.role].port} at 1 Mbps and looks for motor ids 1-6 "
                f"({', '.join(JOINTS)}). The {self.role}'s power supply must be on.")


    def illustration(self, s):
        return "scan"

    def run(self, s):
        t = s.targets[self.role]
        if t.port not in s.ctx.list_ports():
            return K.check_port_exists(t.port, s.ctx.list_ports())
        old = s.arms.pop(self.role, None)
        if old:
            old.release()
        arm = Arm(s.ctx.open_bus(t.port))
        try:
            arm.connect()
        except BusError as e:
            return [K.Check("motors.open", K.FAIL, str(e), "Is another program (lerobot, Arduino IDE, a previous "
                            "run) holding the port? Close it and retry.")]
        found = arm.scan()
        other = arm.scan_baudrates() if not found else None
        checks = K.check_motors(found, other)
        if K.worst(checks) != K.FAIL:
            s.arms[self.role] = arm
        else:
            arm.release()
        return checks


class PowerStep(Step):
    kind, title = "power", "Check supply voltage"

    def instructions(self, s):
        prof = s.profile(self.role)
        return f"Reads every motor's voltage and temperature. Expected {prof.voltage_min}-{prof.voltage_max} V."

    def hint_spec(self, s):
        prof = s.profile(self.role)
        return "hint.power", {"lo": f"{prof.voltage_min:g}", "hi": f"{prof.voltage_max:g}"}

    def illustration(self, s):
        return "power"

    def run(self, s):
        arm = self.arm(s)
        return K.check_power(arm.voltages(), arm.temperatures(), s.profile(self.role))


class HomingStep(Step):
    kind, title = "homing", "Middle pose"

    def instructions(self, s):
        return ("Torque is off: move the arm by hand so EVERY joint is in the middle of its range (upper arm "
                "and forearm forming a right angle, wrist straight, gripper half open). Hold it still and "
                "press Start. This sets the homing offsets.")


    def illustration(self, s):
        return "middle"

    def on_ready(self, s):
        self.arm(s).prepare_for_calibration()

    def run(self, s):
        arm = self.arm(s)
        arm.prepare_for_calibration()
        offsets = arm.set_half_turn_homings()
        readback = arm.positions()
        s.draft.offsets = offsets
        return K.check_homing(offsets, readback, s.previous_cal(self.role))


class RangesStep(Step):
    kind, title = "ranges", "Record ranges of motion"
    live = True

    def reset(self):
        self.mins: dict[str, int] = {}
        self.maxes: dict[str, int] = {}

    def instructions(self, s):
        return ("Press Start, then move each joint EXCEPT wrist_roll slowly all the way to both mechanical "
                "stops, one at a time. Watch the bars fill. Return the arm to the middle and press Stop.")


    def illustration(self, s):
        return "ranges"

    def begin(self, s):
        pos = self.arm(s).positions()
        self.mins = {j: pos[j] for j in JOINTS if j not in FULL_TURN_JOINTS}
        self.maxes = dict(self.mins)
        return None

    def tick(self, s):
        pos = s.live.get(self.role) or {}
        for j in self.mins:
            if j in pos:
                self.mins[j] = min(self.mins[j], pos[j])
                self.maxes[j] = max(self.maxes[j], pos[j])

    def end(self, s):
        mins = {**self.mins, **{j: 0 for j in FULL_TURN_JOINTS}}
        maxes = {**self.maxes, **{j: P.MAX_POS for j in FULL_TURN_JOINTS}}
        s.draft.mins, s.draft.maxes = mins, maxes
        return K.check_ranges(mins, maxes, s.profile(self.role), s.previous_cal(self.role))

    def progress(self, s):
        if not self.mins:
            return {}
        lim = s.profile(self.role).joint_limits
        return {"recorded": {j: {"min": self.mins[j], "max": self.maxes[j],
                                 "fill": min(1.0, (self.maxes[j] - self.mins[j]) / lim[j].typical_span)}
                             for j in self.mins}}


class SaveStep(Step):
    kind, title = "save", "Write, save and activate"
    needs = ("offsets", "ranges")

    def instructions(self, s):
        dest = (s.ctx.export_dir or "the LeRobot cache") if s.ctx.export else None
        tail = f" and exports it to {dest}" if dest else ""
        return ("Writes the ranges to the motors, reads them back, stores a new version in the calibration "
                f"history{tail}.")


    def illustration(self, s):
        return "save"

    def run(self, s):
        t, d = s.targets[self.role], s.draft
        assert d.offsets is not None and d.mins is not None and d.maxes is not None  # guaranteed by `needs`
        cal = {j: C.MotorCalibration(id=MOTOR_IDS[j], drive_mode=0, homing_offset=d.offsets[j],
                                     range_min=d.mins[j], range_max=d.maxes[j]) for j in JOINTS}
        checks = K.check_shape(C.validate_shape(cal))
        if K.worst(checks) == K.FAIL:
            return checks
        arm = self.arm(s)
        arm.write_calibration(cal)
        checks += K.check_motor_matches(cal, arm.read_calibration())
        all_checks = [c.to_dict() for st in s.steps if st is not self for c in s.results.get(st.id, [])]
        # Accepting a failed check ("f") is a decision: the version is activated like any other, and the
        # failed checks stay in its record (history shows them).
        forced = [c for c in all_checks if c["status"] == K.FAIL]
        rec = s.ctx.store.add(t.arm_id, self.role, cal, note=t.note, port=t.port or "",
                              usb_serial=s.ctx.list_ports().get(t.port or "", ""),
                              checks=all_checks + [c.to_dict() for c in checks], written=True)
        d.record = rec.version
        if forced:
            names = ", ".join(sorted({c["joint"] or c["id"] for c in forced}))
            checks.append(K.Check("save.forced", K.WARN, f"Saved as {t.arm_id} v{rec.version} (active), with "
                                  f"accepted failures: {names}.", "Fine if you meant it; the teleop test shows "
                                  "whether the arm works.", args={"arm": t.arm_id, "version": rec.version,
                                                                  "names": names}))
        else:
            checks.append(K.Check("save.stored", K.PASS, f"Saved as {t.arm_id} v{rec.version} (active)."))
        if s.ctx.export:
            path = s.ctx.store.export(t.arm_id, rec.version, s.ctx.export_dir, legacy=s.ctx.legacy_export)
            checks.append(K.Check("save.exported", K.PASS, f"Exported to {path}"))
        return checks


class MatchStep(Step):
    kind, title = "match", "Compare motors with the active version"

    def instructions(self, s):
        return "Reads offsets and limits from the motors and compares them with the active stored version."


    def illustration(self, s):
        return "scan"

    def run(self, s):
        t = s.targets[self.role]
        rec = s.previous[self.role]
        if rec is None:
            return [K.Check("verify.stored", K.FAIL, f"No stored calibration for '{t.arm_id}'.",
                            f"Run `so101-cal calibrate {self.role} --id {t.arm_id}` or import an existing "
                            "LeRobot file with `so101-cal import`.")]
        s.draft.stored[self.role] = rec.calibration
        return K.check_motor_matches(rec.calibration, self.arm(s).read_calibration())


class PoseStep(Step):
    kind, title = "pose", "Middle pose check"

    def instructions(self, s):
        return ("Move the arm by hand into the same middle pose used for calibration and press Start. Each "
                "joint should read close to 2047 again.")


    def illustration(self, s):
        return "middle"

    def on_ready(self, s):
        self.arm(s).set_torque(False)

    def run(self, s):
        return K.check_pose(self.arm(s).positions(), {j: HALF_TURN for j in JOINTS}, "middle pose")


class TeleopStep(Step):
    kind, title = "teleop", "Leader -> follower test"
    live = True
    needs = ("stored:leader", "stored:follower")
    max_step = 60  # ticks per cycle the follower goal may move (~5 deg at 30 Hz)
    settle_cycles = 20

    def __init__(self) -> None:
        super().__init__("follower")
        self.id, self.label = "teleop", self.title

    @property
    def roles(self) -> tuple[str, ...]:
        return ("leader", "follower")

    def reset(self):
        self.t0: float | None = None
        self.start_checks: list[K.Check] = []
        self.err_sum: dict[str, float] = {}
        self.samples = self.cycles = 0
        self.goal: dict[str, int] = {}
        self.last_target: dict[str, int] | None = None

    def instructions(self, s):
        return (f"Fold both arms into the rest pose (lying on their stops), then press Start. A motor mounted "
                f"the other way round shows up here, before any torque. Then the follower's torque turns on "
                f"and it mirrors the leader for {s.ctx.teleop_seconds:.0f} s (or until Stop). Move slowly "
                "through all joints. When it stops the follower goes limp: keep a hand near it.")

    def hint_spec(self, s):
        return ("hint.teleop", {}) if self.running(s) else ("hint.teleop.ready", {})

    def illustration(self, s):
        return "teleop"

    def _norm(self, s, role):
        pos = self.arm(s, role).positions()
        cal = s.draft.stored[role]
        return {j: C.normalize(j, pos[j], cal[j]) for j in JOINTS}

    def begin(self, s):
        self.reset()
        leader, follower = self.arm(s, "leader"), self.arm(s, "follower")
        leader.set_torque(False)
        checks = K.check_teleop_start(self._norm(s, "leader"), self._norm(s, "follower"))
        if K.worst(checks) == K.FAIL:
            return checks
        self.start_checks = checks
        follower.configure_follower()
        self.goal = follower.positions()
        follower.write_goals(self.goal)
        follower.set_torque(True)  # a failure part-way is undone by abort(), which every exit path calls
        self.t0 = time.monotonic()
        self.err_sum = {j: 0.0 for j in JOINTS if j not in FULL_TURN_JOINTS}
        return None

    def tick(self, s):
        lpos, fpos = s.live.get("leader"), s.live.get("follower")
        if not lpos or not fpos:
            return
        lcal, fcal = s.draft.stored["leader"], s.draft.stored["follower"]
        target = {j: C.unnormalize(j, C.normalize(j, lpos[j], lcal[j]), fcal[j]) for j in JOINTS}
        self.cycles += 1
        if self.last_target and self.cycles > self.settle_cycles:  # let the follower catch up first
            # tracking error against what the leader asked for one cycle ago (the follower lags a cycle)
            for j in self.err_sum:
                self.err_sum[j] += abs(C.normalize(j, fpos[j], fcal[j]) - C.normalize(j, self.last_target[j], fcal[j]))
            self.samples += 1
        self.goal = {j: self.goal[j] + max(-self.max_step, min(self.max_step, target[j] - self.goal[j]))
                     for j in JOINTS}
        self.arm(s, "follower").write_goals(self.goal)
        self.last_target = target

    def done(self, s):
        return self.t0 is not None and time.monotonic() - self.t0 >= s.ctx.teleop_seconds

    def end(self, s):
        self.abort(s)
        n = max(1, self.samples)
        errors = {j: v / n for j, v in self.err_sum.items()}
        return self.start_checks + K.check_teleop_tracking(errors, self.samples)

    def abort(self, s):
        if "follower" in s.arms:
            s.arms["follower"].torque_off()

    def progress(self, s):
        if self.t0 is None:
            return {}
        return {"elapsed": round(time.monotonic() - self.t0, 1), "seconds": s.ctx.teleop_seconds,
                "samples": self.samples}


# ==================================================================================================
@dataclass
class StepState:
    status: Status = "pending"
    started: float | None = None
    finished: float | None = None


@dataclass
class Session:
    flow: str
    targets: dict[str, Target]
    ctx: Context
    steps: list[Step] = field(default_factory=list)
    current: int = 0
    aborted: bool = False

    def __post_init__(self):
        self.lock = threading.RLock()
        self.arms: dict[str, Arm] = {}
        self.live: dict[str, dict[str, int]] = {}
        self.draft = Draft()
        self.results: dict[str, list[K.Check]] = {}
        self.states: dict[str, StepState] = {st.id: StepState() for st in self.steps}
        self.events: list[dict] = []
        self.notices: list[str] = []  # shown after the run, e.g. "the motors hold an unsaved calibration"
        # the version active when the run started: what checks compare against (read once, not per poll)
        self.previous = {role: self.ctx.store.active(t.arm_id) for role, t in self.targets.items()}
        self.created = time.time()
        if self.steps:
            self._make_ready()

    # --- construction -------------------------------------------------------------------------------
    @classmethod
    def calibrate(cls, ctx: Context, target: Target) -> Session:
        r = target.role
        steps = [PortStep(r), MotorsStep(r), PowerStep(r), HomingStep(r), RangesStep(r), SaveStep(r)]
        return cls("calibrate", {r: target}, ctx, steps)

    @classmethod
    def verify(cls, ctx: Context, target: Target) -> Session:
        r = target.role
        steps = [PortStep(r), MotorsStep(r), PowerStep(r), MatchStep(r), PoseStep(r)]
        return cls("verify", {r: target}, ctx, steps)

    @classmethod
    def teleop(cls, ctx: Context, leader: Target, follower: Target) -> Session:
        steps: list[Step] = []
        for kind in (PortStep, MotorsStep, PowerStep, MatchStep):
            steps += [kind("leader", multi=True), kind("follower", multi=True)]
        steps.append(TeleopStep())
        return cls("teleop", {"leader": leader, "follower": follower}, ctx, steps)

    # --- helpers used by steps ----------------------------------------------------------------------
    def profile(self, role: str) -> P.RoleProfile:
        return P.profile(role, self.ctx.supply if role == "follower" else None)

    def previous_cal(self, role: str) -> C.Calibration | None:
        rec = self.previous[role]
        return rec.calibration if rec else None

    def log(self, msg: str, level: str = "info") -> None:
        self.events.append({"t": round(time.time() - self.created, 1), "level": level, "msg": msg})
        del self.events[:-200]

    @property
    def step(self) -> Step | None:
        return self.steps[self.current] if self.current < len(self.steps) else None

    @property
    def state(self) -> StepState | None:
        return self.states[self.step.id] if self.step else None

    @property
    def finished(self) -> bool:
        return self.aborted or self.current >= len(self.steps)

    def _actor(self, hook: str) -> None:
        fn = getattr(self.ctx.actor, hook, None)
        if fn:
            fn(self, self.step)

    def _make_ready(self) -> None:
        st = self.step
        if st is None:
            return
        st.reset()
        self.states[st.id].status = "ready"
        if st.role in self.arms:
            try:
                st.on_ready(self)
            except BusError as e:
                self.log(str(e), "error")
        self._actor("on_ready")

    def _record(self, checks: list[K.Check]) -> None:
        st, state = self.step, self.state
        assert st is not None and state is not None
        self.results[st.id] = checks
        state.status = K.worst(checks)
        state.finished = time.time()
        for c in checks:
            if c.status != K.PASS:
                self.log(f"{st.label}: {c.message}", c.status)
        self.log(f"{st.label}: {state.status.upper()}")
        if state.status == K.PASS:
            self._advance()

    def _advance(self) -> None:
        self.current += 1
        self._make_ready()

    def _guard(self, st: Step, fn: Callable[[], list[K.Check] | None]) -> list[K.Check] | None:
        """Run step code; any error fails the step (and makes the hardware safe) instead of escaping
        into the front end."""
        try:
            return fn()
        except Exception as e:
            st.abort(self)
            return [_failure(st.kind, e)]

    # --- actions (called by the front ends) ---------------------------------------------------------
    def act(self, action: str, step_id: str | None = None) -> bool:
        """Run `action` if the current step is still `step_id` (when given). Returns False if ignored.

        Front ends send the step id they displayed, so a double click or a key pressed twice cannot
        act on the step that came next (e.g. start the teleop test right after a check passed).
        """
        if action not in ACTIONS:
            raise ValueError(f"unknown action '{action}'")
        with self.lock:
            if step_id is not None and (self.step is None or self.step.id != step_id):
                return False
            {"start": self.start, "stop": self.stop, "retry": self.retry, "accept": self.next,
             "force": lambda: self.next(force=True), "abort": self.abort}[action]()
            return True

    def start(self) -> None:
        with self.lock:
            st, state = self.step, self.state
            if self.aborted or st is None or state is None or state.status != "ready":
                return
            state.started = time.time()
            missing = st.missing(self)
            if missing:
                self._record([K.Check(f"{st.kind}.prerequisites", K.FAIL,
                                      f"Cannot run: {' and '.join(missing)} {'was' if len(missing) == 1 else 'were'} "
                                      "not recorded because an earlier step failed.",
                                      "Abort this run and start it again.")])
                return
            if not st.is_live(self):
                self._actor("on_start")
                self._record(self._guard(st, lambda: st.run(self)) or [])
                return
            early = self._guard(st, lambda: st.begin(self))
            if early:
                self._record(early)
                return
            state.status = "running"
            self._actor("on_start")  # after begin(): the baseline is taken before the "hand" moves

    def stop(self) -> None:
        with self.lock:
            st, state = self.step, self.state
            if st is None or state is None or state.status != "running":
                return
            self._actor("on_stop")
            self._record(self._guard(st, lambda: st.end(self)) or [])

    def retry(self) -> None:
        with self.lock:
            st, state = self.step, self.state
            if self.aborted or st is None or state is None or state.status not in ("warn", "fail", "running"):
                return
            st.abort(self)  # also after a failed start: begin() may have got half-way
            self._actor("on_stop")
            self.results.pop(st.id, None)
            self._make_ready()

    def next(self, force: bool = False) -> None:
        """Accept a step with warnings (or, with force, failures) and move on."""
        with self.lock:
            if self.aborted or self.step is None or self.state is None:
                return
            status = self.state.status
            if status == "warn" or (force and status == "fail"):
                self.log(f"{self.step.label}: accepted with {status}", "warn")
                self._advance()

    def abort(self) -> None:
        with self.lock:
            if self.aborted:
                return
            try:
                if self.step:
                    self.step.abort(self)
                self._actor("on_stop")
                if self.flow == "calibrate" and self.draft.offsets is not None and self.draft.record is None:
                    arm_id = next(iter(self.targets.values())).arm_id
                    self.notices.append(f"The motors now hold new homing offsets that were not saved. "
                                        f"Recalibrate, or restore the active version with `so101-cal write {arm_id}`.")
            finally:
                self.aborted = True
                self.close()

    def close(self) -> None:
        """Release every arm: torque off (best effort, every joint), then close the port."""
        with self.lock:
            for arm in self.arms.values():
                arm.release()
            self.arms.clear()

    def poll(self) -> None:
        """Refresh live positions and drive the running step. Call at ~20-30 Hz."""
        with self.lock:
            lost: BusError | None = None
            st, state = self.step, self.state
            running = not self.aborted and st is not None and state is not None and state.status == "running"
            for role, arm in list(self.arms.items()):
                try:
                    self.live[role] = arm.positions()
                except BusError as e:
                    self.live.pop(role, None)
                    self.log(f"{role}: {e}", "error")
                    if running and st is not None and role in st.roles:
                        lost = e
            if not running or st is None:
                return
            if lost is not None:  # e.g. the leader unplugged mid-teleop: never keep the follower driven
                st.abort(self)
                self._record([_failure(st.kind, lost)])
                return
            try:
                st.tick(self)
                if st.done(self):
                    self.stop()
            except Exception as e:
                st.abort(self)
                self._record([_failure(st.kind, e)])

    # --- view ---------------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Everything a front end shows, as plain JSON-able data."""
        with self.lock:
            st, state = self.step, self.state
            current = None
            if st is not None and state is not None:
                status = state.status
                can = {"start": status == "ready", "stop": status == "running",
                       "retry": status in ("warn", "fail", "running"), "accept": status == "warn",
                       "force": status == "fail"}
                hint_key, hint_args = st.hint_spec(self)
                current = {
                    "id": st.id, "kind": st.kind, "role": st.role, "label": st.label, "live": st.is_live(self),
                    "status": status, "instructions": st.instructions(self), "hint": st.hint(self),
                    "hint_key": hint_key, "hint_args": hint_args, "progress": st.progress(self),
                    "illustration": st.illustration(self), "can": can,
                    # what Enter / the main button does
                    "primary": next((a for a in ("start", "stop", "accept", "retry") if can[a]), None),
                }
            arms = {}
            for role, t in self.targets.items():
                cal = self.draft.stored.get(role)
                if cal is None and self.flow == "calibrate":
                    cal = self.draft.ranges()
                if cal is None:
                    prev = self.previous[role]
                    cal = prev.calibration if prev else None
                live = self.live.get(role, {})
                joints = {}
                for j in JOINTS:
                    raw = live.get(j)
                    m = cal[j] if cal else None
                    joints[j] = {
                        "raw": raw,
                        "cal_min": m.range_min if m else None,
                        "cal_max": m.range_max if m else None,
                        # a range being recorded (or a failed one) can be empty: no normalized value then
                        "norm": round(C.normalize(j, raw, m), 1) if (m and raw is not None and m.span > 0) else None,
                    }
                arms[role] = {"arm_id": t.arm_id, "port": t.port, "connected": role in self.arms,
                              "joints": joints}
            return {
                "flow": self.flow,
                "targets": {r: t.__dict__.copy() for r, t in self.targets.items()},
                "steps": [{"id": s.id, "kind": s.kind, "role": s.role, "label": s.label,
                           "status": self.states[s.id].status,
                           "checks": [c.to_dict() for c in self.results.get(s.id, [])]} for s in self.steps],
                "current": current,
                "finished": self.finished,
                "aborted": self.aborted,
                "record": self.draft.record,
                "arms": arms,
                "events": self.events[-50:],
                "notices": list(self.notices),
                "result": self.result() if self.finished else None,
            }

    def result(self) -> str:
        statuses = [self.states[s.id].status for s in self.steps]
        if K.FAIL in statuses:
            # failures that were accepted ("f") and still ended in a saved version are warnings
            return K.WARN if self.draft.record is not None and not self.aborted else K.FAIL
        if self.aborted:
            return "aborted"
        return K.WARN if K.WARN in statuses else K.PASS
