from so101_calibrator import calibration as C
from so101_calibrator.mock import MockActor, MockRig
from so101_calibrator.profiles import HALF_TURN, JOINTS
from so101_calibrator.wizard import Session, Target

from .conftest import calibrate, drive, make_ctx


def step_status(session):
    return {s["id"]: s["status"] for s in session.snapshot()["steps"]}


def failing(session):
    return [c for st in session.results.values() for c in st if c.status == "fail"]


def test_calibrate_follower_end_to_end(store, tmp_path):
    rig = MockRig.create()
    s = calibrate(store, rig, "follower")
    assert s.result() == "pass", failing(s)
    assert s.targets["follower"].port == rig.FOLLOWER_PORT  # found by unplugging
    rec = store.get("follower")
    assert rec.data["usb_serial"] == "MOCK5AB01794851"
    assert store.in_motors("follower") == 1
    cal = rec.calibration
    assert C.validate_shape(cal) == []
    # the motors hold exactly what was stored, and the middle pose reads 2047
    bus = rig.bus(rig.FOLLOWER_PORT)
    for m in cal.values():
        assert bus.regs[m.id]["Homing_Offset"] == m.homing_offset
        assert m.range_min < HALF_TURN < m.range_max
    assert (tmp_path / "lerobot/robots/so_follower/follower.json").exists()


def test_recalibration_creates_new_version(store):
    rig = MockRig.create()
    calibrate(store, rig, "follower")
    s = calibrate(store, rig, "follower", port_known=True)
    assert s.result() in ("pass", "warn")
    assert [r.version for r in store.versions("follower")] == [1, 2]
    assert store.get("follower").data["parent"] == 1


def test_missing_motor_fails_scan(store):
    rig = MockRig.create("missing:3")
    s = calibrate(store, rig, "follower", port_known=True)
    assert s.step.kind == "motors" and s.state.status == "fail"
    assert "3 (elbow_flex)" in failing(s)[0].message
    assert store.active("follower") is None


def test_low_voltage_fails_power(store):
    s = calibrate(store, MockRig.create("voltage:5.0"), "follower", port_known=True)
    assert s.step.kind == "power" and s.state.status == "fail"


def test_supply_preset_accepts_7v4(store):
    s = calibrate(store, MockRig.create("voltage:7.4"), "follower", port_known=True, supply="7.4v")
    assert s.result() == "pass"


def test_narrow_range_fails(store):
    s = calibrate(store, MockRig.create("stuck:wrist_flex"), "follower", port_known=True)
    assert s.step.kind == "ranges" and s.state.status == "fail"
    assert failing(s)[0].joint == "wrist_flex"


def test_reversed_motor_is_caught_before_teleop_torque(store):
    # a motor mounted the other way round calibrates fine on its own; with both arms folded in the rest
    # pose it reads from the other end of its range, before any torque goes on
    rig = MockRig.create("reversed:elbow_flex")
    assert calibrate(store, rig, "leader").result() == "pass"
    assert calibrate(store, rig, "follower").result() == "pass"
    ctx = make_ctx(store, rig)
    targets = (Target("leader", "leader", rig.LEADER_PORT), Target("follower", "follower", rig.FOLLOWER_PORT))
    s = drive(Session.teleop(ctx, *targets), rig)
    assert s.step.kind == "teleop" and s.state.status == "fail"
    assert [(c.id, c.joint) for c in failing(s)] == [("teleop.mirrored", "elbow_flex")]
    assert not rig.bus(rig.FOLLOWER_PORT).regs[3]["Torque_Enable"]


def test_accepted_failure_is_saved_and_activated(store):
    rig = MockRig.create("stuck:wrist_flex")
    s = calibrate(store, rig, "follower", port_known=True)
    assert s.step.kind == "ranges" and s.state.status == "fail"
    s.next(force=True)  # "accept anyway"
    drive(s, rig)
    assert s.result() == "warn"
    assert store.active("follower").version == 1
    assert store.in_motors("follower") == 1
    saved = [c for c in s.results["save"] if c.id == "save.forced"]
    assert saved and "wrist_flex" in saved[0].message


def test_port_discovery_requires_replug(store):
    rig = MockRig.create()
    s = Session.calibrate(make_ctx(store, rig), Target("follower", "follower"))
    s.start()
    rig.ports.unplug(rig.FOLLOWER_PORT)
    s.poll()
    assert s.step.progress(s)["phase"] == "replug"
    s.stop()  # user gives up before plugging back in
    assert s.state.status == "fail"


def test_verify_detects_foreign_eeprom(store):
    rig = MockRig.create()
    calibrate(store, rig, "follower")
    ctx = make_ctx(store, rig)
    ok = drive(Session.verify(ctx, Target("follower", "follower", rig.FOLLOWER_PORT)), rig)
    assert ok.result() == "pass", failing(ok)
    rig.bus(rig.FOLLOWER_PORT).regs[2]["Homing_Offset"] += 40  # someone else wrote the motor
    bad = drive(Session.verify(ctx, Target("follower", "follower", rig.FOLLOWER_PORT)), rig)
    assert bad.step.kind == "match" and bad.state.status == "fail"


def test_verify_pose_off(store):
    rig = MockRig.create()
    calibrate(store, rig, "follower")
    s = Session.verify(make_ctx(store, rig), Target("follower", "follower", rig.FOLLOWER_PORT))
    drive(s, rig, until="pose")
    rig.follower.set_pose("shoulder_lift", 0.6)
    s.start()
    assert s.state.status == "fail" and failing(s)[0].joint == "shoulder_lift"


def test_teleop_tracks_and_refuses_misaligned_start(store):
    rig = MockRig.create()
    calibrate(store, rig, "leader")
    calibrate(store, rig, "follower")
    ctx = make_ctx(store, rig, teleop_seconds=60)
    targets = (Target("leader", "leader", rig.LEADER_PORT), Target("follower", "follower", rig.FOLLOWER_PORT))
    s = drive(Session.teleop(ctx, *targets), rig)
    assert s.result() == "pass", failing(s)
    assert not rig.bus(rig.FOLLOWER_PORT).regs[1]["Torque_Enable"]  # limp again afterwards
    # follower moved with the leader
    assert abs(rig.follower.pose["shoulder_pan"] - rig.leader.pose["shoulder_pan"]) < 0.05

    rig.leader.set_pose("shoulder_lift", rig.follower.pose["shoulder_lift"] + 0.4)
    s2 = Session.teleop(ctx, *targets)
    drive(s2, rig, until="teleop")
    s2.start()
    assert s2.state.status == "fail" and failing(s2)[0].id == "teleop.aligned"
    assert not rig.bus(rig.FOLLOWER_PORT).regs[2]["Torque_Enable"]


def test_mock_actor_runs_the_demo(store):
    """The threaded actor used by --mock demos finishes a calibration on its own."""
    import time

    rig = MockRig.create()
    actor = MockActor(rig, speed=20)
    s = Session.calibrate(make_ctx(store, rig, actor=actor), Target("follower", "follower"))
    deadline = time.monotonic() + 20
    while not s.finished and time.monotonic() < deadline:
        if s.state.status == "ready":
            s.start()
        elif s.state.status == "running":
            s.poll()
            if s.step.kind == "ranges" and actor.idle():
                s.stop()
            time.sleep(0.005)
        else:
            break
    assert s.result() == "pass", failing(s)


def test_snapshot_is_json_serializable(store):
    import json

    rig = MockRig.create()
    s = calibrate(store, rig, "follower")
    json.dumps(s.snapshot())


def test_ranges_stopped_early_fails_without_breaking_snapshot(store):
    """Regression: a zero-width recorded range made snapshot() divide by zero (TUI crash)."""
    rig = MockRig.create()
    s = drive(Session.calibrate(make_ctx(store, rig), Target("follower", "follower", port=rig.port_of("follower"))),
              rig, until="ranges")
    s.start()
    s.poll()
    s.stop()  # nothing moved: every recorded range is a single tick
    assert s.state.status == "fail"
    snap = s.snapshot()
    assert all(j["norm"] is None for j in snap["arms"]["follower"]["joints"].values() if j["cal_min"] == j["cal_max"])
    s.retry()
    assert s.snapshot()["current"]["status"] == "ready"


def test_teleop_start_checks_in_turkish():
    from so101_calibrator.checks import check_teleop_start
    from so101_calibrator.i18n import check_text

    rest = {j: 0.0 for j in JOINTS} | {"shoulder_lift": -95.0, "elbow_flex": 95.0}
    bad = check_teleop_start(rest, rest | {"elbow_flex": -93.0})[0].to_dict()
    msg, fix = check_text(bad, "tr")
    assert msg == "elbow_flex: leader +95, follower -93 okuyor: birbirinin tersi." and "ters takılı" in fix
    old = {"id": "teleop.mirrored", "status": "fail", "message": "old text", "fix": "old fix"}
    assert check_text(old, "tr") == ("old text", "old fix")  # records saved before translations
