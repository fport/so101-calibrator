"""Pass/warn/fail checks for every calibration step. Pure functions: easy to test, shared by CLI and UI.

Every non-passing check carries a `fix`: the concrete thing to do about it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import profiles as P
from .calibration import Calibration
from .profiles import FULL_TURN_JOINTS, HALF_TURN, JOINTS, MAX_POS, MOTOR_IDS, RoleProfile

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass
class Check:
    id: str
    status: str
    message: str
    fix: str = ""
    joint: str | None = None
    args: dict | None = None  # values for a translated message (i18n "check.<id>"); English is in message/fix

    def to_dict(self) -> dict:
        return asdict(self)


def worst(checks: list[Check]) -> str:
    statuses = {c.status for c in checks}
    return FAIL if FAIL in statuses else WARN if WARN in statuses else PASS


def _ok(id: str, msg: str, joint: str | None = None) -> Check:
    return Check(id, PASS, msg, joint=joint)


# --- port ------------------------------------------------------------------------------------------
def check_port_found(removed: list[str], added: list[str]) -> list[Check]:
    if len(removed) == 1:
        return [_ok("port.found", f"Board found on {removed[0]}")]
    if not removed:
        return [Check("port.found", FAIL, "No serial port disappeared.",
                      "Unplug the board's USB cable (not the power adapter) after pressing Start, then press "
                      "Done. If it still fails, try another cable: charge-only cables have no data lines.")]
    return [Check("port.found", FAIL, f"Several ports disappeared: {', '.join(removed)}",
                  "Unplug only one board at a time.")]


def check_port_exists(port: str, available: dict[str, str]) -> list[Check]:
    if port in available:
        serial = available[port]
        return [_ok("port.present", f"{port} is present" + (f" (USB serial {serial})" if serial else ""))]
    found = ", ".join(sorted(available)) or "none"
    return [Check("port.present", FAIL, f"{port} is not connected (found: {found}).",
                  "Plug the board's USB cable back in, or pick the right port / run port discovery again.")]


# --- motors ----------------------------------------------------------------------------------------
def check_motors(found: dict[int, int], other_bauds: dict[int, dict[int, int]] | None = None) -> list[Check]:
    checks = []
    expected = set(MOTOR_IDS.values())
    missing = sorted(expected - set(found))
    extra = sorted(set(found) - expected)
    if not found:
        if other_bauds:
            desc = "; ".join(f"{b} baud: ids {sorted(ids)}" for b, ids in other_bauds.items())
            return [Check("motors.ids", FAIL, f"No motor answers at 1 Mbps, but some do elsewhere ({desc}).",
                          "These motors were never set up for the SO-101. Run `lerobot-setup-motors` once to "
                          "give them ids 1-6 and 1 Mbps.")]
        return [Check("motors.ids", FAIL, "No motor answers on this port.",
                      "Check the power supply is plugged in and switched on, and that the 3-pin cable from "
                      "the controller board reaches the first motor (shoulder_pan).")]
    if missing:
        names = ", ".join(f"{i} ({JOINTS[i - 1]})" for i in missing)
        first = JOINTS[missing[0] - 1]
        checks.append(Check("motors.ids", FAIL, f"Missing motor id(s): {names}.",
                            f"Motors are daisy-chained: check the cable going INTO '{first}' and the cable "
                            "between it and the previous motor. A motor with a factory id (1) after "
                            "replacement also shows up here; set it up with `lerobot-setup-motors`."))
    else:
        checks.append(_ok("motors.ids", "All 6 motors answer (ids 1-6)."))
    if extra:
        checks.append(Check("motors.extra", WARN, f"Unexpected motor id(s) on the bus: {extra}.",
                            "Two motors may share an id or a spare motor is attached. Each joint must have "
                            "a unique id 1-6."))
    wrong_model = {i: m for i, m in found.items() if i in expected and m != P.STS3215_MODEL}
    if wrong_model:
        checks.append(Check("motors.model", WARN, f"Unexpected model numbers {wrong_model} (STS3215 is 777).",
                            "This tool assumes STS3215 motors; calibration still works if the encoder is "
                            "12-bit."))
    return checks


# --- power -----------------------------------------------------------------------------------------
def check_power(voltages: dict[str, float], temps: dict[str, int], prof: RoleProfile) -> list[Check]:
    checks = []
    vmin = min(voltages.values())
    vmax = max(voltages.values())
    span = f"{vmin:.1f}-{vmax:.1f} V" if vmax - vmin >= 0.15 else f"{vmin:.1f} V"
    if vmax > prof.voltage_max:
        checks.append(Check("power.voltage", FAIL, f"Supply reads {span}, above {prof.voltage_max} V for a "
                            f"{prof.role}.", prof.supply_hint + " Unplug it now if a 12 V adapter is on the "
                            "leader."))
    elif vmin < prof.voltage_min:
        checks.append(Check("power.voltage", FAIL, f"Supply reads {span}, below {prof.voltage_min} V for a "
                            f"{prof.role}.", prof.supply_hint))
    else:
        checks.append(_ok("power.voltage", f"Supply {span} (expected {prof.voltage_min}-{prof.voltage_max} V)"))
    if vmax - vmin > 0.6:
        low = min(voltages, key=voltages.get)
        checks.append(Check("power.drop", WARN, f"Voltage differs by {vmax - vmin:.1f} V along the chain "
                            f"(lowest at {low}).", "Check for a loose or thin daisy-chain cable before "
                            f"'{low}'.", joint=low))
    for j, t in temps.items():
        if t >= P.TEMP_FAIL_C:
            checks.append(Check("power.temp", FAIL, f"{j} is at {t} C.",
                                "Let the motor cool down; check it is not stalled against an end stop.", j))
        elif t >= P.TEMP_WARN_C:
            checks.append(Check("power.temp", WARN, f"{j} is warm ({t} C).", "Let it cool before long runs.", j))
    return checks


# --- homing ----------------------------------------------------------------------------------------
def check_homing(offsets: dict[str, int], readback: dict[str, int],
                 previous: Calibration | None) -> list[Check]:
    checks = []
    for j in JOINTS:
        off = offsets[j]
        if abs(off) > 2047:
            checks.append(Check("homing.offset", FAIL, f"{j}: offset {off} cannot be stored (limit +-2047).",
                                "Move this joint a few degrees and redo the middle pose.", j))
        elif abs(off) > P.OFFSET_WARN:
            checks.append(Check("homing.offset", WARN, f"{j}: offset {off} is close to the +-2047 limit.",
                                "Works, but usually means the horn was mounted half a turn off. Fine to keep "
                                "unless you are re-assembling the arm anyway.", j))
        err = readback[j] - HALF_TURN
        if abs(err) > P.HOMING_READBACK_TOL:
            checks.append(Check("homing.readback", FAIL, f"{j}: reads {readback[j]} after homing, expected "
                                f"{HALF_TURN}.", "The offset was not written. Keep the arm still during this "
                                "step and retry; if it repeats, check that the motor's EEPROM lock is released "
                                "(torque off).", j))
        if previous and j in previous and j not in FULL_TURN_JOINTS:
            drift = off - previous[j].homing_offset
            if abs(drift) > P.DRIFT_OFFSET_WARN:
                checks.append(Check("homing.drift", WARN, f"{j}: offset moved {drift:+d} ticks "
                                    f"(~{drift * 360 / 4096:+.0f} deg) since the previous calibration.",
                                    "Either the middle pose differs from last time or the motor/horn was "
                                    "re-seated. Redo the middle pose if that was not intended.", j))
    if not any(c.status != PASS for c in checks):
        checks.insert(0, _ok("homing.offset", "Middle pose stored; all joints read 2047."))
    return checks


# --- ranges ----------------------------------------------------------------------------------------
def check_ranges(mins: dict[str, int], maxes: dict[str, int], prof: RoleProfile,
                 previous: Calibration | None) -> list[Check]:
    checks = []
    for j in JOINTS:
        if j in FULL_TURN_JOINTS:
            continue
        lo, hi = mins[j], maxes[j]
        span = hi - lo
        lim = prof.joint_limits[j]
        if span < lim.min_span:
            checks.append(Check("ranges.span", FAIL, f"{j}: range {span} ticks (~{span * 360 / 4096:.0f} deg) "
                                f"is too narrow (min {lim.min_span}).",
                                f"Move {j} slowly all the way to BOTH mechanical stops while recording. If it "
                                "cannot move, check the motor is not jammed or still under torque.", j))
            continue
        if span < 0.75 * lim.typical_span:
            checks.append(Check("ranges.span", WARN, f"{j}: range {span} ticks is smaller than usual "
                                f"(~{lim.typical_span}).", "Did you reach both end stops? Retry if unsure.", j))
        if not lo <= HALF_TURN <= hi:
            checks.append(Check("ranges.center", FAIL, f"{j}: the middle pose ({HALF_TURN}) is outside the "
                                f"recorded range {lo}..{hi}.", "Redo the middle-pose step, then record again.",
                                j))
        elif abs((lo + hi) / 2 - HALF_TURN) > P.CENTER_WARN:
            checks.append(Check("ranges.center", WARN, f"{j}: middle pose is far from the centre of the range "
                                f"({lo}..{hi}).", "The arm was probably not in the middle pose during homing. "
                                "Fine for LeRobot, but re-homing gives a more symmetric range.", j))
        if lo <= P.EDGE_MARGIN or hi >= MAX_POS - P.EDGE_MARGIN:
            checks.append(Check("ranges.wrap", FAIL, f"{j}: range {lo}..{hi} touches the encoder wrap "
                                "(0/4095).", "The joint crossed the encoder zero. Redo the middle pose with "
                                "the arm truly centred, then record again.", j))
        if previous and j in previous:
            prev = previous[j].span
            change = (span - prev) / prev if prev else 0
            if abs(change) > P.DRIFT_SPAN_WARN:
                checks.append(Check("ranges.drift", WARN, f"{j}: range changed {change:+.0%} vs. the previous "
                                    f"calibration ({prev} -> {span}).", "Expected after re-assembly; otherwise "
                                    "check you swept the full range.", j))
    if not checks:
        checks.append(_ok("ranges.span", "All ranges look complete and centred."))
    return checks


# --- direction -------------------------------------------------------------------------------------
def check_directions(signs: dict[str, int], reference: dict[str, int] | None, ref: str) -> list[Check]:
    """`reference`: signs recorded for the paired arm (`ref` like "leader 'leader'") or for a previous
    calibration of the same arm (`ref` "previous")."""
    checks = []
    if not reference:
        return [_ok("direction.recorded", "Joint directions recorded (nothing to compare with yet).")]
    label = "previous calibration" if ref == "previous" else ref
    for j in JOINTS:
        if j in reference and j in signs and reference[j] != signs[j]:
            # the tool cannot tell a joint moved the wrong way from a reversed motor: name both, cheapest first
            checks.append(Check("direction.mismatch", FAIL, f"{j}: moved the opposite way to the {label}.",
                                f"Either {j} was moved the wrong way in this step, or its motor is mounted "
                                "reversed. Retry following the picture; if it happens again, compare the motor and "
                                "horn on both arms. Teleoperation would drive this joint backwards.", j,
                                args={"joint": j, "ref": ref}))
    if not checks:
        checks.append(_ok("direction.match", f"All joint directions match the {label}."))
    return checks


# --- verification ----------------------------------------------------------------------------------
def check_motor_matches(stored: Calibration, on_motor: Calibration) -> list[Check]:
    bad = [j for j in JOINTS if j in stored and (
        stored[j].homing_offset, stored[j].range_min, stored[j].range_max) != (
        on_motor[j].homing_offset, on_motor[j].range_min, on_motor[j].range_max)]
    if bad:
        return [Check("verify.motors", FAIL, f"Motors hold a different calibration than the active version "
                      f"for: {', '.join(bad)}.", "Write the active version to the motors (`so101-cal write`) "
                      "or recalibrate if a motor was replaced.")]
    return [_ok("verify.motors", "Motor EEPROM matches the active calibration.")]


def check_pose(positions: dict[str, int], target: dict[str, int], label: str) -> list[Check]:
    checks = []
    for j in JOINTS:
        if j in FULL_TURN_JOINTS:
            continue
        err = positions[j] - target[j]
        deg = err * 360 / 4096
        if abs(err) > P.POSE_TOL_WARN:
            checks.append(Check("verify.pose", FAIL, f"{j}: {deg:+.0f} deg away from the {label}.",
                                f"Put {j} in the {label} and retry. If you are sure it is there, the "
                                "calibration no longer fits this arm: recalibrate.", j))
        elif abs(err) > P.POSE_TOL_PASS:
            checks.append(Check("verify.pose", WARN, f"{j}: {deg:+.0f} deg away from the {label}.",
                                "Probably hand placement; retry more carefully.", j))
    if not checks:
        checks.append(_ok("verify.pose", f"All joints within ~10 deg of the {label}."))
    return checks


def check_teleop_start(leader: dict[str, float], follower: dict[str, float]) -> list[Check]:
    far = {j: leader[j] - follower[j] for j in JOINTS
           if j not in FULL_TURN_JOINTS and abs(leader[j] - follower[j]) > P.TELEOP_START_MAX_DIFF}
    if far:
        desc = ", ".join(f"{j} ({d:+.0f})" for j, d in far.items())
        return [Check("teleop.aligned", FAIL, f"Leader and follower poses differ: {desc}.",
                      "Hold the leader in the same pose as the follower before starting: the follower jumps "
                      "to the leader's pose as soon as torque is on.")]
    return [_ok("teleop.aligned", "Leader and follower start in the same pose.")]


def check_teleop_tracking(errors: dict[str, float], samples: int) -> list[Check]:
    if samples < 10:
        return [Check("teleop.tracking", FAIL, f"Only {samples} control cycles ran.",
                      "Let the test run longer; check the bus is not dropping packets.")]
    checks = []
    for j, e in errors.items():
        if e > P.TELEOP_ERR_WARN:
            checks.append(Check("teleop.tracking", FAIL, f"{j}: follower lags the leader by {e:.1f} on average.",
                                "Check this joint's cable, power and that leader/follower directions match.", j))
        elif e > P.TELEOP_ERR_PASS:
            checks.append(Check("teleop.tracking", WARN, f"{j}: tracking error {e:.1f}.",
                                "Slightly loose; move the leader slower or check for friction.", j))
    if not checks:
        checks.append(_ok("teleop.tracking", f"Follower tracked the leader on all joints ({samples} cycles)."))
    return checks


def check_shape(problems: list[str]) -> list[Check]:
    if problems:
        return [Check("save.shape", FAIL, "; ".join(problems), "This is a bug or a corrupted file; recalibrate.")]
    return [_ok("save.shape", "Calibration is valid LeRobot format.")]
