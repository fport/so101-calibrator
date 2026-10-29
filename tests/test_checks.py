from so101_calibrator import checks as K
from so101_calibrator.calibration import from_json_dict
from so101_calibrator.profiles import HALF_TURN, JOINTS, profile

from .test_encoding_and_calibration import SAMPLE

FOLLOWER = profile("follower")


def statuses(checks):
    return {(c.id, c.status) for c in checks}


def good_ranges():
    mins = {"shoulder_pan": 800, "shoulder_lift": 850, "elbow_flex": 950, "wrist_flex": 920, "wrist_roll": 0,
            "gripper": 1400}
    maxes = {"shoulder_pan": 3400, "shoulder_lift": 3250, "elbow_flex": 3150, "wrist_flex": 3170,
             "wrist_roll": 4095, "gripper": 2700}
    return mins, maxes


def test_port_found():
    assert K.worst(K.check_port_found(["/dev/a"], [])) == "pass"
    assert K.worst(K.check_port_found([], [])) == "fail"
    assert K.worst(K.check_port_found(["/dev/a", "/dev/b"], [])) == "fail"


def test_motors_missing_names_the_cable():
    checks = K.check_motors({1: 777, 2: 777, 3: 777, 5: 777, 6: 777})
    fail = next(c for c in checks if c.status == "fail")
    assert "4 (wrist_flex)" in fail.message and "wrist_flex" in fail.fix


def test_motors_unconfigured_suggests_setup():
    checks = K.check_motors({}, {57600: {1: 777}})
    assert checks[0].status == "fail" and "lerobot-setup-motors" in checks[0].fix


def test_power_follower_needs_12v_and_leader_rejects_12v():
    temps = {j: 30 for j in JOINTS}
    assert K.worst(K.check_power({j: 12.0 for j in JOINTS}, temps, FOLLOWER)) == "pass"
    low = K.check_power({j: 5.0 for j in JOINTS}, temps, FOLLOWER)
    assert K.worst(low) == "fail" and "12 V" in low[0].fix
    assert K.worst(K.check_power({j: 12.0 for j in JOINTS}, temps, profile("leader"))) == "fail"
    assert K.worst(K.check_power({j: 7.4 for j in JOINTS}, temps, profile("follower", "7.4v"))) == "pass"


def test_power_hot_motor_and_voltage_drop():
    v = {j: 12.0 for j in JOINTS} | {"gripper": 11.0}
    checks = K.check_power(v, {j: 30 for j in JOINTS} | {"elbow_flex": 75}, FOLLOWER)
    assert ("power.drop", "warn") in statuses(checks)
    assert ("power.temp", "fail") in statuses(checks)


def test_homing_checks():
    offs = {j: 100 for j in JOINTS}
    read = {j: HALF_TURN for j in JOINTS}
    assert K.worst(K.check_homing(offs, read, None)) == "pass"
    assert ("homing.offset", "warn") in statuses(K.check_homing(offs | {"elbow_flex": 1990}, read, None))
    assert ("homing.readback", "fail") in statuses(K.check_homing(offs, read | {"gripper": 1500}, None))
    prev = from_json_dict(SAMPLE)
    drift = K.check_homing({j: prev[j].homing_offset for j in JOINTS} | {"shoulder_pan": 400}, read, prev)
    assert ("homing.drift", "warn") in statuses(drift)


def test_ranges_checks():
    mins, maxes = good_ranges()
    assert K.worst(K.check_ranges(mins, maxes, FOLLOWER, None)) == "pass"
    narrow = K.check_ranges(mins | {"elbow_flex": 1800}, maxes | {"elbow_flex": 2300}, FOLLOWER, None)
    assert ("ranges.span", "fail") in statuses(narrow)
    off_centre = K.check_ranges(mins | {"gripper": 2100}, maxes | {"gripper": 3600}, FOLLOWER, None)
    assert ("ranges.center", "fail") in statuses(off_centre)
    wrapped = K.check_ranges(mins | {"shoulder_pan": 3}, maxes, FOLLOWER, None)
    assert ("ranges.wrap", "fail") in statuses(wrapped)


def test_ranges_drift_vs_previous():
    mins, maxes = good_ranges()
    prev = from_json_dict(SAMPLE)
    checks = K.check_ranges(mins | {"gripper": 1700}, maxes | {"gripper": 2400}, FOLLOWER, prev)
    assert ("ranges.drift", "warn") in statuses(checks)


def test_motor_match_and_pose():
    cal = from_json_dict(SAMPLE)
    other = from_json_dict(SAMPLE)
    assert K.worst(K.check_motor_matches(cal, other)) == "pass"
    other["wrist_flex"].homing_offset += 5
    assert "wrist_flex" in K.check_motor_matches(cal, other)[0].message
    target = {j: HALF_TURN for j in JOINTS}
    assert K.worst(K.check_pose(target, target, "middle pose")) == "pass"
    assert K.worst(K.check_pose(target | {"elbow_flex": HALF_TURN + 400}, target, "middle pose")) == "fail"


def test_teleop_checks():
    zero = {j: 0.0 for j in JOINTS}
    assert K.worst(K.check_teleop_start(zero, zero)) == "pass"
    assert K.worst(K.check_teleop_start(zero | {"shoulder_lift": 60.0}, zero)) == "fail"
    rest = zero | {"shoulder_lift": -95.0, "elbow_flex": 95.0}
    mirrored = K.check_teleop_start(rest, rest | {"elbow_flex": -90.0})
    assert [(c.id, c.joint) for c in mirrored] == [("teleop.mirrored", "elbow_flex")]
    # far apart on the same side is a pose difference, not a reversed motor
    assert K.check_teleop_start(rest, rest | {"elbow_flex": 40.0})[0].id == "teleop.aligned"
    # the leader's trigger rests open while the follower's gripper rests closed: not a reason to refuse
    assert K.worst(K.check_teleop_start(rest | {"gripper": 97.0}, rest | {"gripper": 0.0})) == "pass"
    assert K.worst(K.check_teleop_tracking({"elbow_flex": 2.0}, 100)) == "pass"
    assert K.worst(K.check_teleop_tracking({"elbow_flex": 30.0}, 100)) == "fail"
    assert K.worst(K.check_teleop_tracking({"elbow_flex": 2.0}, 3)) == "fail"
