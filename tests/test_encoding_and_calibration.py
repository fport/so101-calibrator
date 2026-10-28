import pytest

from so101_calibrator import calibration as C
from so101_calibrator.bus import decode_sign_magnitude, encode_sign_magnitude

SAMPLE = {  # a real SO-101 follower calibration written by LeRobot 0.6
    "shoulder_pan": {"id": 1, "drive_mode": 0, "homing_offset": -226, "range_min": 845, "range_max": 3454},
    "shoulder_lift": {"id": 2, "drive_mode": 0, "homing_offset": 272, "range_min": 835, "range_max": 3209},
    "elbow_flex": {"id": 3, "drive_mode": 0, "homing_offset": -88, "range_min": 925, "range_max": 3121},
    "wrist_flex": {"id": 4, "drive_mode": 0, "homing_offset": -491, "range_min": 932, "range_max": 3177},
    "wrist_roll": {"id": 5, "drive_mode": 0, "homing_offset": -1910, "range_min": 0, "range_max": 4095},
    "gripper": {"id": 6, "drive_mode": 0, "homing_offset": 567, "range_min": 1992, "range_max": 3443},
}


@pytest.mark.parametrize("value", [0, 1, -1, 2047, -2047, 845, -1910])
def test_sign_magnitude_roundtrip(value):
    assert decode_sign_magnitude(encode_sign_magnitude(value, 11), 11) == value


def test_sign_magnitude_rejects_overflow():
    with pytest.raises(ValueError):
        encode_sign_magnitude(2048, 11)


def test_negative_offset_sets_sign_bit():
    assert encode_sign_magnitude(-5, 11) == (1 << 11) | 5


def test_lerobot_file_roundtrip(tmp_path):
    cal = C.from_json_dict(SAMPLE)
    assert C.validate_shape(cal) == []
    path = C.save(cal, tmp_path / "f.json")
    assert C.load(path) == cal
    assert C.to_json_dict(cal) == SAMPLE


def test_validate_shape_finds_problems():
    bad = C.from_json_dict(SAMPLE)
    bad["elbow_flex"].id = 9
    bad["gripper"].range_min = 4000
    del bad["wrist_flex"]
    problems = " ".join(C.validate_shape(bad))
    assert "elbow_flex: id 9" in problems and "gripper: invalid range" in problems and "wrist_flex" in problems


def test_normalize_matches_lerobot_and_roundtrips():
    cal = C.from_json_dict(SAMPLE)
    m = cal["shoulder_pan"]
    assert C.normalize("shoulder_pan", m.range_min, m) == -100
    assert C.normalize("shoulder_pan", m.range_max, m) == 100
    assert C.normalize("gripper", cal["gripper"].range_max, cal["gripper"]) == 100
    for raw in (900, 2047, 3000):
        assert abs(C.unnormalize("shoulder_pan", C.normalize("shoulder_pan", raw, m), m) - raw) <= 1


def test_diff_lists_changed_fields():
    a = C.from_json_dict(SAMPLE)
    b = C.from_json_dict(SAMPLE)
    b["gripper"].range_max = 3400
    assert C.diff(a, b) == [{"joint": "gripper", "field": "range_max", "a": 3443, "b": 3400, "delta": -43}]
