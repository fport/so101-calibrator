"""Calibration data in LeRobot's on-disk format plus the normalization LeRobot applies to it."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .profiles import JOINTS, MAX_POS, MOTOR_IDS


@dataclass
class MotorCalibration:
    id: int
    drive_mode: int
    homing_offset: int
    range_min: int
    range_max: int

    @property
    def span(self) -> int:
        return self.range_max - self.range_min


Calibration = dict[str, MotorCalibration]


def to_json_dict(cal: Calibration) -> dict:
    return {name: asdict(cal[name]) for name in JOINTS if name in cal}


def from_json_dict(data: dict) -> Calibration:
    cal = {}
    for name, m in data.items():
        cal[name] = MotorCalibration(
            id=int(m["id"]),
            drive_mode=int(m.get("drive_mode", 0)),
            homing_offset=int(m["homing_offset"]),
            range_min=int(m["range_min"]),
            range_max=int(m["range_max"]),
        )
    return cal


def load(path: str | Path) -> Calibration:
    return from_json_dict(json.loads(Path(path).read_text()))


def dump(cal: Calibration) -> str:
    # Same layout LeRobot writes (indent=4), so exported files diff cleanly against its own.
    return json.dumps(to_json_dict(cal), indent=4)


def save(cal: Calibration, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(cal))
    return path


def validate_shape(cal: Calibration) -> list[str]:
    """Structural problems that would make LeRobot refuse or misuse the file."""
    problems = []
    for name in JOINTS:
        if name not in cal:
            problems.append(f"missing joint '{name}'")
            continue
        m = cal[name]
        if m.id != MOTOR_IDS[name]:
            problems.append(f"{name}: id {m.id}, expected {MOTOR_IDS[name]}")
        if not 0 <= m.range_min < m.range_max <= MAX_POS:
            problems.append(f"{name}: invalid range {m.range_min}..{m.range_max}")
        if not -2047 <= m.homing_offset <= 2047:
            problems.append(f"{name}: homing_offset {m.homing_offset} not encodable (+-2047)")
    extra = set(cal) - set(JOINTS)
    if extra:
        problems.append(f"unknown joints: {', '.join(sorted(extra))}")
    return problems


def normalize(name: str, raw: int, m: MotorCalibration) -> float:
    """LeRobot's default normalization: -100..100 for body joints, 0..100 for the gripper."""
    lo, hi = m.range_min, m.range_max
    bounded = min(hi, max(lo, raw))
    if name == "gripper":
        v = (bounded - lo) / (hi - lo) * 100
        return 100 - v if m.drive_mode else v
    v = (bounded - lo) / (hi - lo) * 200 - 100
    return -v if m.drive_mode else v


def unnormalize(name: str, value: float, m: MotorCalibration) -> int:
    lo, hi = m.range_min, m.range_max
    if name == "gripper":
        value = 100 - value if m.drive_mode else value
        value = min(100.0, max(0.0, value))
        return int(value / 100 * (hi - lo) + lo)
    value = -value if m.drive_mode else value
    value = min(100.0, max(-100.0, value))
    return int((value + 100) / 200 * (hi - lo) + lo)


def diff(a: Calibration, b: Calibration) -> list[dict]:
    """Per joint/field differences between two calibrations (b - a)."""
    rows = []
    for name in JOINTS:
        ma, mb = a.get(name), b.get(name)
        if ma is None or mb is None:
            rows.append({"joint": name, "field": "*", "a": ma is not None, "b": mb is not None, "delta": None})
            continue
        for field in ("homing_offset", "range_min", "range_max", "drive_mode", "id"):
            va, vb = getattr(ma, field), getattr(mb, field)
            if va != vb:
                rows.append({"joint": name, "field": field, "a": va, "b": vb, "delta": vb - va})
    return rows
