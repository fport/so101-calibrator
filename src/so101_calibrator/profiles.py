"""Static knowledge about the SO-101 arm: joints, motor ids, supply voltages and check thresholds."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# STS3215: 12-bit magnetic encoder, model number 777.
RESOLUTION = 4096
MAX_POS = RESOLUTION - 1
HALF_TURN = MAX_POS // 2  # 2047: the raw value the "middle pose" is mapped to by homing
STS3215_MODEL = 777
DEFAULT_BAUDRATE = 1_000_000
BAUDRATES = [1_000_000, 500_000, 250_000, 128_000, 115_200, 57_600, 38_400, 19_200]

JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
MOTOR_IDS = {name: i + 1 for i, name in enumerate(JOINTS)}
# wrist_roll turns freely, LeRobot stores it as the full 0..4095 range.
FULL_TURN_JOINTS = {"wrist_roll"}

# Physical motion asked for in the direction test. The tool does not assume which raw sign is "right";
# it records the sign and requires leader and follower (and past calibrations) to agree, because LeRobot
# maps the normalized leader value straight onto the follower.
DIRECTION_PROMPTS = {
    "shoulder_pan": "rotate the whole arm to ITS left (counter-clockwise seen from above)",
    "shoulder_lift": "lift the upper arm up / forward",
    "elbow_flex": "unfold the elbow (forearm away from the upper arm)",
    "wrist_flex": "tilt the wrist up",
    "wrist_roll": "roll the wrist clockwise (seen from behind the gripper)",
    "gripper": "open the gripper / pull the trigger open",
}


@dataclass(frozen=True)
class JointLimits:
    """Expected encoder span (range_max - range_min) of a healthy calibration, in ticks."""

    min_span: int  # below this the range recording clearly failed
    typical_span: int  # what a complete sweep usually gives on an SO-101


@dataclass(frozen=True)
class RoleProfile:
    role: str  # "follower" or "leader"
    lerobot_type: str  # --robot.type / --teleop.type
    lerobot_kind: str  # "robots" or "teleoperators" (cache sub-directory)
    lerobot_class_dir: str  # cache sub-directory used by lerobot >= 0.4 (class name of the device)
    voltage_min: float
    voltage_max: float
    supply_hint: str
    joint_limits: dict[str, JointLimits] = field(default_factory=dict)


_JOINT_LIMITS = {
    "shoulder_pan": JointLimits(min_span=1200, typical_span=2600),
    "shoulder_lift": JointLimits(min_span=1200, typical_span=2400),
    "elbow_flex": JointLimits(min_span=1100, typical_span=2200),
    "wrist_flex": JointLimits(min_span=1100, typical_span=2250),
    "wrist_roll": JointLimits(min_span=4095, typical_span=4095),
    "gripper": JointLimits(min_span=500, typical_span=1300),
}

PROFILES = {
    "follower": RoleProfile(
        role="follower",
        lerobot_type="so101_follower",
        lerobot_kind="robots",
        lerobot_class_dir="so_follower",
        voltage_min=10.5,
        voltage_max=13.5,
        supply_hint="Plug in the follower's 12 V power adapter (the 5 V USB cable does not power the motors).",
        joint_limits=_JOINT_LIMITS,
    ),
    "leader": RoleProfile(
        role="leader",
        lerobot_type="so101_leader",
        lerobot_kind="teleoperators",
        lerobot_class_dir="so_leader",
        voltage_min=4.5,
        voltage_max=8.5,
        supply_hint="The leader runs on its 5 V adapter. Never feed it 12 V: its motors are low-voltage variants.",
        joint_limits=_JOINT_LIMITS,
    ),
}

# Supply presets for followers built with 7.4 V STS3215 motors.
SUPPLY_PRESETS = {"12v": (10.5, 13.5), "7.4v": (6.5, 8.5), "5v": (4.5, 8.5)}


def profile(role: str, supply: str | None = None) -> RoleProfile:
    base = PROFILES[role]
    if supply is None:
        return base
    lo, hi = SUPPLY_PRESETS[supply]
    return replace(base, voltage_min=lo, voltage_max=hi)


# Thresholds shared by the checks (raw encoder ticks unless stated otherwise).
OFFSET_WARN = 1900  # |homing_offset| this close to the +-2047 encodable limit is suspicious
HOMING_READBACK_TOL = 20  # after homing the middle pose must read HALF_TURN within this
CENTER_WARN = 700  # middle of the recorded range this far from HALF_TURN: homing pose was off-centre
EDGE_MARGIN = 20  # range touching 0 / 4095 means the joint crossed the encoder wrap
DRIFT_OFFSET_WARN = 150  # homing offset change vs. the previous calibration (~13 degrees)
DRIFT_SPAN_WARN = 0.15  # relative span change vs. the previous calibration
DIRECTION_MIN_MOVE = 150  # ticks of motion needed before the direction of a joint is decided
DIRECTION_STILL_TICKS = 15  # an arm whose joints all stay within this many ticks ...
DIRECTION_STILL_POLLS = 10  # ... for this many polls (~0.4 s) ...
DIRECTION_STILL_SECONDS = 0.3  # ... or at least this long is at rest
DIRECTION_END_MARGIN = 0.1  # a joint within this share of its range from an end stop is not measured
TEMP_WARN_C = 55
TEMP_FAIL_C = 70
POSE_TOL_PASS = 120  # verify: middle pose re-found within this many ticks (~10 degrees)
POSE_TOL_WARN = 250
TELEOP_START_MAX_DIFF = 20.0  # normalized units; leader and follower must start roughly aligned
TELEOP_ERR_PASS = 6.0  # mean tracking error, normalized units
TELEOP_ERR_WARN = 12.0
