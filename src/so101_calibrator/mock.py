"""A simulated SO-101 for running the whole tool without hardware.

`MockBus` emulates the STS3215 register semantics that matter here (Present = Actual - Homing_Offset,
position limits, torque, goal tracking, voltage, temperature). `MockArm` holds the physical state of one
arm; `MockHand` plays the human that moves the arm when the wizard asks it to, so the CLI and dashboard
can run end-to-end in demo mode and tests can script every step.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field

from .bus import BusError
from .profiles import FULL_TURN_JOINTS, HALF_TURN, JOINTS, MAX_POS, MOTOR_IDS, RESOLUTION, STS3215_MODEL

# Mechanical end stops of a typical SO-101, in "actual" encoder ticks around the assembled middle pose.
DEFAULT_HALF_SPANS = {
    "shoulder_pan": 1300,
    "shoulder_lift": 1200,
    "elbow_flex": 1100,
    "wrist_flex": 1120,
    "wrist_roll": 2047,
    "gripper": 700,
}


@dataclass
class Faults:
    missing_ids: set[int] = field(default_factory=set)
    voltage: float | None = None  # override the supply voltage
    reversed: set[str] = field(default_factory=set)  # motor mounted the other way round
    stuck: set[str] = field(default_factory=set)  # joint the "hand" cannot move (narrow range)
    hot: set[str] = field(default_factory=set)

    @classmethod
    def parse(cls, spec: str | None) -> Faults:
        """`missing:4,voltage:5.1,reversed:elbow_flex,stuck:gripper,hot:wrist_flex`"""
        f = cls()
        for item in filter(None, (spec or "").split(",")):
            key, _, val = item.partition(":")
            if key == "missing":
                f.missing_ids.add(int(val))
            elif key == "voltage":
                f.voltage = float(val)
            elif key in ("reversed", "stuck", "hot"):
                getattr(f, key).add(val)
            else:
                raise ValueError(f"unknown mock fault '{key}'")
        return f


class MockArm:
    """Physical state of one simulated arm."""

    def __init__(self, role: str, seed: int = 0, faults: Faults | None = None, voltage: float | None = None):
        rng = random.Random(seed)
        self.role = role
        self.faults = faults or Faults()
        self.voltage = voltage if voltage is not None else (12.1 if role == "follower" else 5.0)
        # Where the encoder sits when the arm is in its middle pose: random per assembly, like real motors.
        self.mid_actual = {j: rng.randint(900, 3200) for j in JOINTS}
        self.half_span = dict(DEFAULT_HALF_SPANS)
        for j in self.faults.stuck:
            self.half_span[j] = 150
        self.pose = {j: 0.0 for j in JOINTS}  # -1..1 of the mechanical range, 0 = middle pose
        self.lock = threading.RLock()

    def actual(self, joint: str) -> int:
        sign = -1 if joint in self.faults.reversed else 1
        raw = self.mid_actual[joint] + sign * self.pose[joint] * self.half_span[joint]
        return int(round(raw)) % RESOLUTION

    def set_pose(self, joint: str, value: float) -> None:
        with self.lock:
            self.pose[joint] = max(-1.0, min(1.0, value))


class MockBus:
    """Register-level fake of a Feetech bus with 6 STS3215 motors."""

    def __init__(self, arm: MockArm, port: str = "/dev/mock", present=lambda: True):
        self.arm = arm
        self.port = port
        self.present = present
        self.baudrate = 1_000_000
        self.connected = False
        self.regs = {i: {"Homing_Offset": 0, "Min_Position_Limit": 0, "Max_Position_Limit": MAX_POS,
                         "Torque_Enable": 0, "Lock": 1, "Operating_Mode": 0, "Acceleration": 0,
                         "Goal_Position": HALF_TURN} for i in MOTOR_IDS.values()}
        self.writes: list[tuple[str, int, int]] = []

    def _joint(self, motor_id: int) -> str:
        return JOINTS[motor_id - 1]

    def connect(self) -> None:
        if not self.present():
            raise BusError(f"Cannot open {self.port}: device not found")
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def set_baudrate(self, baudrate: int) -> None:
        self.baudrate = baudrate

    def _alive(self, motor_id: int) -> bool:
        if self.connected and not self.present():  # USB unplugged while open: what FeetechBus reports
            raise BusError(f"{self.port}: device disconnected")
        return (self.connected and self.baudrate == 1_000_000 and motor_id in self.regs
                and motor_id not in self.arm.faults.missing_ids)

    def ping(self, motor_id: int) -> int | None:
        return STS3215_MODEL if self._alive(motor_id) else None

    def read(self, register: str, motor_id: int) -> int:
        if not self._alive(motor_id):
            raise BusError(f"read {register} from motor {motor_id} failed: no status packet")
        regs, joint = self.regs[motor_id], self._joint(motor_id)
        if register == "Present_Position":
            return (self.arm.actual(joint) - regs["Homing_Offset"]) % RESOLUTION
        if register == "Present_Voltage":
            v = self.arm.faults.voltage if self.arm.faults.voltage is not None else self.arm.voltage
            return int(round(v * 10))
        if register == "Present_Temperature":
            return 74 if joint in self.arm.faults.hot else 31
        if register == "Present_Load":
            return 0
        if register == "Model_Number":
            return STS3215_MODEL
        if register == "ID":
            return motor_id
        return regs.get(register, 0)

    def write(self, register: str, motor_id: int, value: int) -> None:
        if not self._alive(motor_id):
            raise BusError(f"write {register} to motor {motor_id} failed: no status packet")
        if register == "Homing_Offset" and not -2047 <= value <= 2047:
            raise ValueError(f"{value} does not fit in sign-magnitude with sign bit 11")
        self.regs[motor_id][register] = int(value)
        self.writes.append((register, motor_id, int(value)))
        if register == "Goal_Position" and self.regs[motor_id]["Torque_Enable"]:
            self._track_goal(motor_id, int(value))

    def _track_goal(self, motor_id: int, goal: int) -> None:
        """With torque on, the motor drives the joint to the (limit-clamped) goal, like a servo."""
        regs, joint = self.regs[motor_id], self._joint(motor_id)
        goal = min(regs["Max_Position_Limit"], max(regs["Min_Position_Limit"], goal))
        actual = (goal + regs["Homing_Offset"]) % RESOLUTION
        sign = -1 if joint in self.arm.faults.reversed else 1
        delta = (actual - self.arm.mid_actual[joint] + RESOLUTION // 2) % RESOLUTION - RESOLUTION // 2
        self.arm.set_pose(joint, sign * delta / self.arm.half_span[joint])


class MockHand:
    """Moves a MockArm the way a person would during each wizard step."""

    def __init__(self, arm: MockArm, speed: float = 1.0):
        self.arm = arm
        self.speed = speed
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def middle(self) -> None:
        for j in JOINTS:
            self.arm.set_pose(j, random.uniform(-0.03, 0.03))

    def full_sweep(self, steps: int = 40) -> None:
        """Instant sweep of every joint end to end (for tests)."""
        for j in JOINTS:
            if j in FULL_TURN_JOINTS:
                continue
            for k in range(steps + 1):
                self.arm.set_pose(j, -1 + 2 * k / steps)
            self.arm.set_pose(j, 0.0)

    def nudge(self, joint: str, amount: float = 0.4) -> None:
        self.arm.set_pose(joint, amount)

    # --- background animation used by the demo dashboard / CLI -------------------------------------
    def animate(self, script: str, joint: str | None = None) -> None:
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(script, joint), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.__dict__.pop("_wander_start", None)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self._thread = None

    def _run(self, script: str, joint: str | None) -> None:
        t0 = time.monotonic()
        while not self._stop.is_set():
            # moves must stay slow enough for a ~25 Hz poller to see the end stops and each nudge
            t = (time.monotonic() - t0) * min(self.speed, 2.0 if script == "directions" else 4.0)
            if script == "sweep":
                # one joint after another, ~2.5 s each, then rest in the middle
                order = [j for j in JOINTS if j not in FULL_TURN_JOINTS]
                idx = int(t // 2.5)
                if idx >= len(order):
                    self.middle()
                    return
                for k, j in enumerate(order):
                    if k == idx:
                        self.arm.set_pose(j, math.sin((t % 2.5) / 2.5 * 2 * math.pi) * 1.05)
                    elif k < idx:
                        self.arm.set_pose(j, 0.0)
            elif script == "nudge" and joint:
                self.arm.set_pose(joint, min(0.5, t * 0.6))
                if t > 1.0:
                    return
            elif script == "directions":
                # each joint: ramp out to +0.35 and back, 0.8 s per joint
                idx = int(t // 0.8)
                if idx >= len(JOINTS):
                    return
                j, phase = JOINTS[idx], (t % 0.8) / 0.8
                self.arm.set_pose(j, 0.35 * math.sin(phase * math.pi))
                for k in range(idx):
                    self.arm.set_pose(JOINTS[k], 0.0)
            elif script == "wander":
                # starts from the current pose and drifts smoothly; never jumps
                if not hasattr(self, "_wander_start"):
                    self._wander_start = dict(self.arm.pose)
                for k, j in enumerate(JOINTS):
                    self.arm.set_pose(j, self._wander_start[j] + 0.4 * math.sin(t * 0.7 + k) - 0.4 * math.sin(k))
            time.sleep(0.02)


class MockPorts:
    """Serial port list that changes when a simulated USB cable is unplugged."""

    def __init__(self, ports: dict[str, str]):
        self.ports = dict(ports)  # device -> serial number
        self.unplugged: set[str] = set()

    def list(self) -> dict[str, str]:
        return {p: s for p, s in self.ports.items() if p not in self.unplugged}

    def unplug(self, port: str) -> None:
        self.unplugged.add(port)

    def replug(self, port: str) -> None:
        self.unplugged.discard(port)


@dataclass
class MockRig:
    """Leader + follower + USB ports: everything `--mock` needs."""

    follower: MockArm
    leader: MockArm
    ports: MockPorts

    FOLLOWER_PORT = "/dev/mock-follower"
    LEADER_PORT = "/dev/mock-leader"

    @classmethod
    def create(cls, faults: str | None = None, seed: int = 7) -> MockRig:
        """`faults` applies to the follower; prefix an item with `leader.` to target the leader."""
        items = [i for i in (faults or "").split(",") if i]
        leader_spec = ",".join(i.removeprefix("leader.") for i in items if i.startswith("leader."))
        follower_spec = ",".join(i for i in items if not i.startswith("leader."))
        rig = cls(
            follower=MockArm("follower", seed=seed, faults=Faults.parse(follower_spec)),
            leader=MockArm("leader", seed=seed + 1, faults=Faults.parse(leader_spec)),
            ports=MockPorts({cls.FOLLOWER_PORT: "MOCK5AB01794851", cls.LEADER_PORT: "MOCK5A7C1232561"}),
        )
        rig._buses = {}
        return rig

    def arm(self, role: str) -> MockArm:
        return self.follower if role == "follower" else self.leader

    def port_of(self, role: str) -> str:
        return self.FOLLOWER_PORT if role == "follower" else self.LEADER_PORT

    def bus(self, port: str) -> MockBus:
        """One persistent bus per port, so register state survives reconnects like EEPROM does."""
        if port not in self._buses:
            if port not in self.ports.ports:
                raise BusError(f"Cannot open {port}: no such device")
            role = "follower" if port == self.FOLLOWER_PORT else "leader"
            self._buses[port] = MockBus(self.arm(role), port, present=lambda: port in self.ports.list())
        return self._buses[port]


class MockActor:
    """Plays the person in front of the arm: reacts to wizard steps by moving the simulated hardware.

    Used by `--mock` so the CLI and dashboard demo every step without hardware.
    """

    def __init__(self, rig: MockRig, speed: float = 1.0):
        self.rig = rig
        self.hands = {"follower": MockHand(rig.follower, speed), "leader": MockHand(rig.leader, speed)}
        self.speed = speed
        self._threads: list[threading.Thread] = []

    def _later(self, fn, delay: float) -> None:
        def run():
            time.sleep(delay / min(self.speed, 2.0))  # a person unplugging is never instant: pollers must see it
            fn()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        self._threads.append(t)

    def idle(self) -> bool:
        self._threads = [t for t in self._threads if t.is_alive()]
        return not self._threads and all(h._thread is None or not h._thread.is_alive() for h in self.hands.values())

    def on_ready(self, session, step) -> None:
        if step is None:
            return
        if step.kind in ("homing", "pose"):
            self.hands[step.role].middle()
        elif step.kind == "teleop":
            for h in self.hands.values():
                h.stop()
            # hold the leader where the follower is, as the instructions ask
            for j in JOINTS:
                self.rig.leader.set_pose(j, self.rig.follower.pose[j])

    def on_start(self, session, step) -> None:
        if step is None:
            return
        hand = self.hands.get(step.role)
        if step.kind == "port" and step.is_live(session):
            port = session.targets[step.role].port or self.rig.port_of(step.role)
            self._later(lambda: self.rig.ports.unplug(port), 0.6)
            self._later(lambda: self.rig.ports.replug(port), 1.4)
        elif step.kind == "ranges":
            hand.animate("sweep")
        elif step.kind == "direction":
            hand.animate("directions")
        elif step.kind == "teleop":
            self.hands["leader"].animate("wander")
            self._later(lambda: None, 0)  # keeps idle() False until the step ends itself

    def on_stop(self, session, step) -> None:
        for h in self.hands.values():
            h.stop()


def load_rig(path, faults: str | None = None) -> MockRig:
    """A rig whose motor EEPROM survives between CLI invocations (demo: calibrate, then verify)."""
    import json
    from pathlib import Path

    rig = MockRig.create(faults)
    p = Path(path)
    if p.exists():
        state = json.loads(p.read_text())
        for port, regs in state.get("regs", {}).items():
            rig.bus(port).regs = {int(i): r for i, r in regs.items()}
    return rig


def save_rig(rig: MockRig, path) -> None:
    import json
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"regs": {port: bus.regs for port, bus in rig._buses.items()}}))
