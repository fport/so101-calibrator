"""The wizard engine shared by the CLI and the dashboard.

A `Session` runs a flow (calibrate / verify / teleop) as a list of `Step`s. Each step is either
*instant* (start -> do the work -> checks) or *live* (start -> polled while the user moves the arm ->
stop, by the user or by the step itself -> checks). UIs only call `start/stop/retry/next/skip/abort`,
`poll()` at ~20-30 Hz, and render `snapshot()`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import calibration as C
from . import checks as K
from . import profiles as P
from .bus import Arm, BusError, MotorBus
from .profiles import FULL_TURN_JOINTS, HALF_TURN, JOINTS, MOTOR_IDS
from .store import Store

FLOWS = ("calibrate", "verify", "teleop")


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
    actor: object | None = None  # mock.MockActor in demo mode: plays the human
    supply: str | None = None  # follower supply preset override ("12v", "7.4v")
    export: bool = True  # write the new calibration where LeRobot reads it
    export_dir: str | None = None  # custom calibration_dir instead of the LeRobot cache
    legacy_export: bool = False
    teleop_seconds: float = 15.0


def wrap_delta(a: int, b: int) -> int:
    """b - a on a 12-bit circle."""
    return (b - a + 2048) % 4096 - 2048


# ==================================================================================================
class Step:
    kind = "step"
    title = "Step"
    live = False
    optional = False

    def __init__(self, role: str, multi: bool = False):
        self.role = role
        self.id = f"{self.kind}:{role}" if multi else self.kind
        self.label = f"{self.title} ({role})" if multi else self.title

    def instructions(self, s: Session) -> str:
        return ""

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

    def progress(self, s: Session) -> dict:
        return {}

    def arm(self, s: Session) -> Arm:
        if self.role not in s.arms:
            raise BusError(f"the {self.role} is not connected (the motor scan did not pass)")
        return s.arms[self.role]


class PortStep(Step):
    kind, title = "port", "Find the USB port"

    def __init__(self, role, multi=False):
        super().__init__(role, multi)
        self.before: dict[str, str] = {}
        self.removed: list[str] = []
        self.phase = "idle"

    def instructions(self, s):
        t = s.targets[self.role]
        if t.port:
            return f"Using {t.port} for the {self.role}. Make sure its USB cable is plugged in."
        return (f"Plug in ONLY the {self.role}'s USB cable and power. Press Start, then unplug the {self.role}'s "
                "USB cable. Plug it back in when asked. The port is detected automatically.")

    def run(self, s):
        t = s.targets[self.role]
        return K.check_port_exists(t.port, s.ctx.list_ports())

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
        removed, _ = _diff(self.before, now)
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
        if self.removed and len(self.removed) == 1:
            port = self.removed[0]
            s.targets[self.role].port = port
            if self.phase != "back":
                checks.append(K.Check("port.replugged", K.FAIL, f"{port} has not come back yet.",
                                      "Plug the USB cable back in, then press Retry on the next step."))
        return checks

    def progress(self, s):
        return {"phase": self.phase, "removed": self.removed}


def _diff(before, after):
    return sorted(set(before) - set(after)), sorted(set(after) - set(before))


class MotorsStep(Step):
    kind, title = "motors", "Connect and scan motors"

    def instructions(self, s):
        return (f"Connects to {s.targets[self.role].port} at 1 Mbps and looks for motor ids 1-6 "
                f"({', '.join(JOINTS)}). The {self.role}'s power supply must be on.")

    def run(self, s):
        t = s.targets[self.role]
        if t.port not in s.ctx.list_ports():
            return K.check_port_exists(t.port, s.ctx.list_ports())
        old = s.arms.pop(self.role, None)
        if old:
            old.close()
        arm = Arm(s.ctx.open_bus(t.port))
        try:
            arm.connect()
        except BusError as e:
            return [K.Check("motors.open", K.FAIL, str(e), "Is another program (lerobot, Arduino IDE, a previous "
                            "run) holding the port? Close it and retry.")]
        found = arm.scan()
        other = None
        if not found:
            other = arm.scan_baudrates()
        checks = K.check_motors(found, other)
        if K.worst(checks) != K.FAIL:
            s.arms[self.role] = arm
        else:
            arm.close()
        return checks


class PowerStep(Step):
    kind, title = "power", "Check supply voltage"

    def instructions(self, s):
        prof = s.profile(self.role)
        return f"Reads every motor's voltage and temperature. Expected {prof.voltage_min}-{prof.voltage_max} V."

    def run(self, s):
        arm = self.arm(s)
        return K.check_power(arm.voltages(), arm.temperatures(), s.profile(self.role))


class HomingStep(Step):
    kind, title = "homing", "Middle pose (homing offsets)"

    def instructions(self, s):
        return ("Torque is off: move the arm by hand so EVERY joint is in the middle of its range (upper arm "
                "and forearm forming a right angle, wrist straight, gripper half open). Hold it still and "
                "press Start.")

    def on_ready(self, s):
        self.arm(s).prepare_for_calibration()

    def run(self, s):
        arm = self.arm(s)
        arm.prepare_for_calibration()
        offsets = arm.set_half_turn_homings()
        readback = arm.positions()
        s.data["offsets"] = offsets
        return K.check_homing(offsets, readback, s.previous_cal(self.role))


class RangesStep(Step):
    kind, title = "ranges", "Record ranges of motion"
    live = True

    def instructions(self, s):
        return ("Press Start, then move each joint EXCEPT wrist_roll slowly all the way to both mechanical "
                "stops, one at a time. Watch the bars fill. Return the arm to the middle and press Stop.")

    def begin(self, s):
        pos = self.arm(s).positions()
        self.mins = {j: pos[j] for j in JOINTS if j not in FULL_TURN_JOINTS}
        self.maxes = dict(self.mins)

    def tick(self, s):
        pos = s.live.get(self.role) or {}
        for j in self.mins:
            if j in pos:
                self.mins[j] = min(self.mins[j], pos[j])
                self.maxes[j] = max(self.maxes[j], pos[j])

    def end(self, s):
        mins = {**self.mins, **{j: 0 for j in FULL_TURN_JOINTS}}
        maxes = {**self.maxes, **{j: P.MAX_POS for j in FULL_TURN_JOINTS}}
        s.data["mins"], s.data["maxes"] = mins, maxes
        return K.check_ranges(mins, maxes, s.profile(self.role), s.previous_cal(self.role))

    def progress(self, s):
        if not hasattr(self, "mins"):
            return {}
        lim = s.profile(self.role).joint_limits
        return {"recorded": {j: {"min": self.mins[j], "max": self.maxes[j],
                                 "fill": min(1.0, (self.maxes[j] - self.mins[j]) / lim[j].typical_span)}
                             for j in self.mins}}


class DirectionStep(Step):
    kind, title = "direction", "Joint directions"
    live = True

    def instructions(self, s):
        return ("Press Start. For each joint shown, move it a little in the direction described, then back. "
                "The tool records which way the encoder counts so leader and follower can be compared.")

    def begin(self, s):
        self.signs: dict[str, int] = {}
        self.order = list(JOINTS)
        self.base = self.arm(s).positions()

    @property
    def current(self):
        return next((j for j in self.order if j not in self.signs), None)

    def tick(self, s):
        j = self.current
        pos = s.live.get(self.role)
        if j is None or not pos:
            return
        d = wrap_delta(self.base[j], pos[j])
        if abs(d) >= P.DIRECTION_MIN_MOVE:
            self.signs[j] = 1 if d > 0 else -1
            s.log(f"{j}: recorded ({'+' if d > 0 else '-'})")
            self.base = dict(pos)

    def done(self, s):
        return self.current is None

    def end(self, s):
        s.data["directions"] = dict(self.signs)
        checks = []
        missing = [j for j in JOINTS if j not in self.signs]
        if missing:
            checks.append(K.Check("direction.incomplete", K.WARN, f"No direction recorded for {', '.join(missing)}.",
                                  "Retry and move each joint at least ~15 degrees."))
        other = "leader" if self.role == "follower" else "follower"
        paired = s.ctx.store.latest_directions(other, exclude_arm=s.targets[self.role].arm_id)
        if paired:
            checks += K.check_directions(self.signs, paired[1], f"{other} '{paired[0]}'")
        prev = s.ctx.store.active(s.targets[self.role].arm_id)
        if prev and prev.data.get("directions"):
            checks += K.check_directions(self.signs, prev.data["directions"], "previous calibration")
        if not paired and not (prev and prev.data.get("directions")):
            checks += K.check_directions(self.signs, None, "")
        return checks

    def progress(self, s):
        if not hasattr(self, "signs"):
            return {}
        j = self.current
        return {"joint": j, "prompt": P.DIRECTION_PROMPTS.get(j, ""), "signs": self.signs}


class SaveStep(Step):
    kind, title = "save", "Write, save and activate"

    def instructions(self, s):
        dest = (s.ctx.export_dir or "the LeRobot cache") if s.ctx.export else None
        tail = f" and exports it to {dest}" if dest else ""
        return ("Writes the ranges to the motors, reads them back, stores a new version in the calibration "
                f"history{tail}.")

    def run(self, s):
        t = s.targets[self.role]
        cal = {j: C.MotorCalibration(id=MOTOR_IDS[j], drive_mode=0, homing_offset=s.data["offsets"][j],
                                     range_min=s.data["mins"][j], range_max=s.data["maxes"][j]) for j in JOINTS}
        checks = K.check_shape(C.validate_shape(cal))
        if K.worst(checks) == K.FAIL:
            return checks
        arm = self.arm(s)
        arm.write_calibration(cal)
        checks += K.check_motor_matches(cal, arm.read_calibration())
        all_checks = [c.to_dict() for st in s.steps[:-1] for c in s.results.get(st.id, [])]
        # A calibration that was pushed past failed checks is kept for reference but never auto-activated.
        forced = any(c["status"] == K.FAIL for c in all_checks)
        rec = s.ctx.store.add(t.arm_id, self.role, cal, note=t.note, port=t.port or "",
                              usb_serial=s.ctx.list_ports().get(t.port, ""), checks=all_checks + [
                                  c.to_dict() for c in checks], directions=s.data.get("directions"),
                              activate=not forced)
        s.data["record"] = rec.version
        if forced:
            checks.append(K.Check("save.stored", K.WARN, f"Saved as {t.arm_id} v{rec.version}, NOT activated: "
                                  "earlier steps failed.", f"Fix the failures and recalibrate, or activate it "
                                  f"anyway with `so101-cal activate {t.arm_id} {rec.version}`."))
            return checks
        checks.append(K.Check("save.stored", K.PASS, f"Saved as {t.arm_id} v{rec.version} (active)."))
        if s.ctx.export:
            path = s.ctx.store.export(t.arm_id, rec.version, s.ctx.export_dir, legacy=s.ctx.legacy_export)
            checks.append(K.Check("save.exported", K.PASS, f"Exported to {path}"))
        return checks


class MatchStep(Step):
    kind, title = "match", "Compare motors with the active version"

    def instructions(self, s):
        return "Reads offsets and limits from the motors and compares them with the active stored version."

    def run(self, s):
        t = s.targets[self.role]
        rec = s.ctx.store.active(t.arm_id)
        if rec is None:
            return [K.Check("verify.stored", K.FAIL, f"No stored calibration for '{t.arm_id}'.",
                            f"Run `so101-cal calibrate {self.role} --id {t.arm_id}` or import an existing "
                            "LeRobot file with `so101-cal import`.")]
        s.data[f"cal:{self.role}"] = rec.calibration
        return K.check_motor_matches(rec.calibration, self.arm(s).read_calibration())


class PoseStep(Step):
    kind, title = "pose", "Middle pose check"

    def instructions(self, s):
        return ("Move the arm by hand into the same middle pose used for calibration and press Start. Each "
                "joint should read close to 2047 again.")

    def on_ready(self, s):
        self.arm(s).set_torque(False)

    def run(self, s):
        pos = self.arm(s).positions()
        return K.check_pose(pos, {j: HALF_TURN for j in JOINTS}, "middle pose")


class TeleopStep(Step):
    kind, title = "teleop", "Leader -> follower test"
    live = True
    max_step = 60  # ticks per cycle the follower goal may move (~5 deg at 30 Hz)
    settle_cycles = 20

    def __init__(self, role="follower", multi=False):
        super().__init__(role, multi)
        self.id, self.label = "teleop", self.title

    def instructions(self, s):
        return (f"Hold the leader in the SAME pose as the follower, then press Start. The follower's torque "
                f"turns on and it mirrors the leader for {s.ctx.teleop_seconds:.0f} s (or until Stop). Move "
                "slowly through all joints. When it stops the follower goes limp: keep a hand near it.")

    def _norm(self, s, role):
        cal = s.data.get(f"cal:{role}")
        if cal is None:
            raise BusError(f"no active calibration loaded for the {role}")
        pos = self.arm_of(s, role).positions()
        return {j: C.normalize(j, pos[j], cal[j]) for j in JOINTS}

    def arm_of(self, s, role):
        if role not in s.arms:
            raise BusError(f"the {role} is not connected (the motor scan did not pass)")
        return s.arms[role]

    def begin(self, s):
        leader, follower = self.arm_of(s, "leader"), self.arm_of(s, "follower")
        leader.set_torque(False)
        checks = K.check_teleop_start(self._norm(s, "leader"), self._norm(s, "follower"))
        if K.worst(checks) == K.FAIL:
            return checks
        self.start_checks = checks
        follower.configure_follower()
        self.goal = follower.positions()
        follower.write_goals(self.goal)
        follower.set_torque(True)
        self.t0 = time.monotonic()
        self.err_sum = {j: 0.0 for j in JOINTS if j not in FULL_TURN_JOINTS}
        self.samples = 0
        self.cycles = 0
        self.last_target = None
        return None

    def tick(self, s):
        lpos, fpos = s.live.get("leader"), s.live.get("follower")
        if not lpos or not fpos:
            return
        lcal, fcal = s.data["cal:leader"], s.data["cal:follower"]  # set by the match steps
        target = {j: C.unnormalize(j, C.normalize(j, lpos[j], lcal[j]), fcal[j]) for j in JOINTS}
        self.cycles += 1
        if self.last_target and self.cycles > self.settle_cycles:  # let the follower catch up first
            for j in self.err_sum:
                e = abs(C.normalize(j, fpos[j], fcal[j]) - C.normalize(j, self.last_target[j], fcal[j]))
                self.err_sum[j] += e
            self.samples += 1
        self.goal = {j: self.goal[j] + max(-self.max_step, min(self.max_step, target[j] - self.goal[j]))
                     for j in JOINTS}
        s.arms["follower"].write_goals(self.goal)
        self.last_target = self.goal

    def done(self, s):
        return time.monotonic() - self.t0 >= s.ctx.teleop_seconds

    def end(self, s):
        s.arms["follower"].set_torque(False)
        n = max(1, self.samples)
        errors = {j: v / n for j, v in self.err_sum.items()}
        return self.start_checks + K.check_teleop_tracking(errors, self.samples)

    def abort(self, s):
        if "follower" in s.arms:
            s.arms["follower"].set_torque(False)

    def progress(self, s):
        if not hasattr(self, "t0"):
            return {}
        return {"elapsed": round(time.monotonic() - self.t0, 1), "seconds": s.ctx.teleop_seconds,
                "samples": self.samples}


# ==================================================================================================
@dataclass
class StepState:
    status: str = "pending"  # pending | ready | running | pass | warn | fail | skipped
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
        self.data: dict = {}
        self.results: dict[str, list[K.Check]] = {}
        self.states: dict[str, StepState] = {st.id: StepState() for st in self.steps}
        self.events: list[dict] = []
        self.created = time.time()
        if self.steps:
            self._make_ready()

    # --- construction -------------------------------------------------------------------------------
    @classmethod
    def calibrate(cls, ctx: Context, target: Target) -> Session:
        r = target.role
        steps = [PortStep(r), MotorsStep(r), PowerStep(r), HomingStep(r), RangesStep(r), DirectionStep(r),
                 SaveStep(r)]
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
        rec = self.ctx.store.active(self.targets[role].arm_id)
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
        self.states[st.id].status = "ready"
        if st.kind == "port":
            st.live = self.targets[st.role].port is None
        hook = getattr(st, "on_ready", None)
        if hook and st.role in self.arms:
            try:
                hook(self)
            except BusError as e:
                self.log(str(e), "error")
        self._actor("on_ready")

    def _record(self, checks: list[K.Check]) -> None:
        st, state = self.step, self.state
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

    def _guard(self, fn):
        try:
            return fn()
        except BusError as e:
            return [K.Check(f"{self.step.kind}.bus", K.FAIL, f"Communication error: {e}",
                            "Check the USB cable and the power supply, then press Retry.")]

    # --- actions (called by the UIs) ----------------------------------------------------------------
    def start(self) -> None:
        with self.lock:
            st, state = self.step, self.state
            if st is None or state.status != "ready":
                return
            state.started = time.time()
            if not st.live:
                self._actor("on_start")
                self._record(self._guard(lambda: st.run(self)))
                return
            early = self._guard(lambda: st.begin(self))
            if early:
                self._record(early)
                return
            state.status = "running"
            self._actor("on_start")  # after begin(): the baseline is taken before the "hand" moves

    def stop(self) -> None:
        with self.lock:
            st, state = self.step, self.state
            if st is None or state.status != "running":
                return
            self._actor("on_stop")
            self._record(self._guard(lambda: st.end(self)))

    def retry(self) -> None:
        with self.lock:
            if self.step and self.state.status in ("warn", "fail", "running"):
                if self.state.status == "running" and hasattr(self.step, "abort"):
                    self.step.abort(self)
                self._actor("on_stop")
                self.results.pop(self.step.id, None)
                self._make_ready()

    def next(self, force: bool = False) -> None:
        """Accept a step with warnings (or, with force, failures) and move on."""
        with self.lock:
            if not self.step:
                return
            status = self.state.status
            if status == "warn" or (force and status == "fail"):
                self.log(f"{self.step.label}: accepted with {status}", "warn")
                self._advance()

    def skip(self) -> None:
        with self.lock:
            if self.step and self.step.optional and self.state.status in ("ready", "warn", "fail"):
                self.state.status = "skipped"
                self._advance()

    def abort(self) -> None:
        with self.lock:
            if self.step and self.state.status == "running" and hasattr(self.step, "abort"):
                self.step.abort(self)
            self._actor("on_stop")
            self.aborted = True
            self.close()

    def close(self) -> None:
        with self.lock:
            for arm in self.arms.values():
                try:
                    arm.close()
                except Exception:
                    pass
            self.arms.clear()

    def poll(self) -> None:
        """Refresh live positions and drive the running step. Call at ~20-30 Hz."""
        with self.lock:
            for role, arm in list(self.arms.items()):
                try:
                    self.live[role] = arm.positions()
                except BusError as e:
                    self.live.pop(role, None)
                    self.log(f"{role}: {e}", "error")
            st, state = self.step, self.state
            if st is None or state is None or state.status != "running":
                return
            try:
                st.tick(self)
                if st.done(self):
                    self.stop()
            except BusError as e:
                if hasattr(st, "abort"):
                    st.abort(self)
                self._record([K.Check(f"{st.kind}.bus", K.FAIL, f"Communication error: {e}",
                                      "Check the USB cable and the power supply, then press Retry.")])

    # --- view ---------------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            st = self.step
            status = self.state.status if st else ("aborted" if self.aborted else "done")
            current = None
            if st:
                current = {
                    "id": st.id, "label": st.label, "live": st.live, "status": status,
                    "instructions": st.instructions(self), "progress": st.progress(self),
                    "can": {
                        "start": status == "ready",
                        "stop": status == "running",
                        "retry": status in ("warn", "fail", "running"),
                        "next": status == "warn",
                        "force": status == "fail",
                        "skip": st.optional and status in ("ready", "warn", "fail"),
                    },
                }
            arms = {}
            for role, t in self.targets.items():
                cal = self.data.get(f"cal:{role}")
                if cal is None and self.flow == "calibrate" and "maxes" in self.data:
                    cal = {j: C.MotorCalibration(MOTOR_IDS[j], 0, 0, self.data["mins"][j], self.data["maxes"][j])
                           for j in JOINTS}
                if cal is None:
                    prev = self.ctx.store.active(t.arm_id)
                    cal = prev.calibration if prev else None
                live = self.live.get(role, {})
                joints = {}
                for j in JOINTS:
                    raw = live.get(j)
                    joints[j] = {
                        "raw": raw,
                        "cal_min": cal[j].range_min if cal else None,
                        "cal_max": cal[j].range_max if cal else None,
                        "norm": round(C.normalize(j, raw, cal[j]), 1) if (cal and raw is not None) else None,
                    }
                arms[role] = {"arm_id": t.arm_id, "port": t.port, "connected": role in self.arms,
                              "joints": joints}
            return {
                "flow": self.flow,
                "targets": {r: t.__dict__ for r, t in self.targets.items()},
                "steps": [{"id": s.id, "label": s.label, "status": self.states[s.id].status,
                           "checks": [c.to_dict() for c in self.results.get(s.id, [])]} for s in self.steps],
                "current": current,
                "finished": self.finished,
                "aborted": self.aborted,
                "record": self.data.get("record"),
                "arms": arms,
                "events": self.events[-50:],
                "result": self.result() if self.finished else None,
            }

    def result(self) -> str:
        statuses = [self.states[s.id].status for s in self.steps]
        if K.FAIL in statuses:
            return K.FAIL
        if self.aborted:
            return "aborted"
        return K.WARN if K.WARN in statuses else K.PASS
