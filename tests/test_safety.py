"""Regression tests for the safety review: torque is always released, stale actions are ignored, the
dashboard refuses cross-site requests, and a damaged store or a swapped port never hurts an arm."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from so101_calibrator.bus import BusError
from so101_calibrator.cli import main
from so101_calibrator.controller import Controller
from so101_calibrator.dashboard.server import make_server
from so101_calibrator.mock import MockRig
from so101_calibrator.store import StoreError
from so101_calibrator.wizard import Session, Target

from .conftest import calibrate, drive, make_ctx, mock_env


def torque(rig: MockRig, role: str = "follower") -> list[int]:
    bus = rig.bus(rig.port_of(role))
    return [bus.regs[i]["Torque_Enable"] for i in range(1, 7)]


def teleop_ready(store, rig: MockRig) -> Session:
    """A teleop session on a calibrated rig, stopped right before the teleop step."""
    calibrate(store, rig, "leader", port_known=True)
    calibrate(store, rig, "follower", port_known=True)
    ctx = make_ctx(store, rig, teleop_seconds=30)
    s = Session.teleop(ctx, Target("leader", "leader", rig.port_of("leader")),
                       Target("follower", "follower", rig.port_of("follower")))
    return drive(s, rig, until="teleop")


# --- torque ------------------------------------------------------------------------------------------
def test_unplugging_the_leader_mid_teleop_releases_the_follower(store):
    rig = MockRig.create()
    s = teleop_ready(store, rig)
    s.start()
    s.poll()
    assert s.state.status == "running" and torque(rig) == [1] * 6
    rig.ports.unplug(rig.port_of("leader"))
    s.poll()
    assert s.state.status == "fail" and torque(rig) == [0] * 6


def test_failed_torque_enable_part_way_is_undone(store, monkeypatch):
    rig = MockRig.create()
    s = teleop_ready(store, rig)
    bus = rig.bus(rig.port_of("follower"))
    write = bus.write

    def flaky(register, motor_id, value):
        if register == "Torque_Enable" and value == 1 and motor_id == 4:
            raise BusError("motor 4 did not answer")
        write(register, motor_id, value)

    monkeypatch.setattr(bus, "write", flaky)
    s.start()
    assert s.state.status == "fail"
    assert torque(rig) == [0] * 6  # motors 1-3 had been enabled


def test_abort_completes_even_if_releasing_torque_fails(store, monkeypatch):
    rig = MockRig.create()
    s = teleop_ready(store, rig)
    s.start()
    bus = rig.bus(rig.port_of("follower"))

    def broken(*a):
        raise BusError("bus gone")

    monkeypatch.setattr(bus, "write", broken)
    s.abort()
    assert s.aborted and s.finished and not s.arms


def test_stale_action_is_ignored(store):
    """A second Enter meant for 'compare motors' must not start the teleop test that came next."""
    rig = MockRig.create()
    s = teleop_ready(store, rig)
    assert s.step.id == "teleop"
    assert s.act("start", "match:follower") is False
    assert s.state.status == "ready" and torque(rig) == [0] * 6
    assert s.act("start", "teleop") is True and s.state.status == "running"
    s.abort()
    s.act("start")  # nothing runs after an abort
    assert s.finished and torque(rig) == [0] * 6


def test_aborting_after_new_offsets_warns_about_eeprom(store):
    rig = MockRig.create()
    s = drive(Session.calibrate(make_ctx(store, rig), Target("follower", "follower", rig.port_of("follower"))),
              rig, until="ranges")
    s.abort()
    assert s.notices and "so101-cal write follower" in s.notices[0]


def test_retry_forgets_the_previous_attempt(store):
    rig = MockRig.create()
    s = drive(Session.calibrate(make_ctx(store, rig), Target("follower", "follower", rig.port_of("follower"))),
              rig, until="ranges")
    s.start()
    rig.follower.set_pose("shoulder_pan", 0.8)
    s.poll()
    assert s.snapshot()["current"]["progress"]["recorded"]["shoulder_pan"]["fill"] > 0
    s.retry()
    assert s.snapshot()["current"]["progress"] == {}


# --- controller ----------------------------------------------------------------------------------------
def test_snapshot_does_not_wait_for_a_slow_action():
    controller = Controller(mock_env())
    try:
        session = controller.start("calibrate", role="follower")
        with session.lock:  # what a long motor scan does
            t0 = time.monotonic()
            assert controller.snapshot() is not None
            assert time.monotonic() - t0 < 0.1
    finally:
        controller.shutdown()


# --- dashboard -----------------------------------------------------------------------------------------
@pytest.fixture
def dashboard():
    server, api = make_server(mock_env(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    api.shutdown()
    server.shutdown()


def post(base: str, path: str, body: bytes, headers: dict) -> int:
    req = urllib.request.Request(base + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_dashboard_refuses_cross_site_requests(dashboard):
    body = json.dumps({"flow": "calibrate", "role": "follower"}).encode()
    # a form or text/plain POST from another site needs no preflight: refused by content type
    assert post(dashboard, "/api/session", body, {"Content-Type": "text/plain"}) == 415
    # a page elsewhere that manages to send JSON is refused by its Origin
    assert post(dashboard, "/api/session", body, {"Content-Type": "application/json",
                                                   "Origin": "https://evil.example"}) == 403
    host = dashboard.removeprefix("http://")
    assert post(dashboard, "/api/session", body, {"Content-Type": "application/json",
                                                   "Origin": f"http://{host}"}) == 200


def test_dashboard_validates_bodies(dashboard):
    h = {"Content-Type": "application/json"}
    assert post(dashboard, "/api/session", b"[1, 2]", h) == 400
    assert post(dashboard, "/api/session", b"{not json", h) == 400
    assert post(dashboard, "/api/session", json.dumps({"flow": "teleop", "seconds": 1e9}).encode(), h) == 400
    assert post(dashboard, "/api/session", json.dumps({"flow": "nope"}).encode(), h) == 400
    assert post(dashboard, "/api/session/action", json.dumps({"action": "start"}).encode(), h) == 400


# --- store and ports ---------------------------------------------------------------------------------
def test_corrupt_or_dangling_store_files(store):
    calibrate(store, MockRig.create(), "follower", port_known=True)
    (store.root / "follower" / "v0001.json").unlink()
    assert store.active("follower") is None
    (store.root / "follower" / "arm.json").write_text('{"arm_id": "follo')
    with pytest.raises(StoreError, match="corrupt"):
        store.arm("follower")


def test_port_is_found_by_usb_serial_after_the_paths_swap():
    env = mock_env()
    store = env.store
    calibrate(store, env.rig, "follower", port_known=True)
    leader_port, follower_port = env.rig.port_of("leader"), env.rig.port_of("follower")
    assert env.remembered_port("follower") == follower_port
    info = store.arm("follower")
    info["port"] = leader_port  # as if the follower had been on the other path last time
    (store.root / "follower" / "arm.json").write_text(json.dumps(info))
    assert env.remembered_port("follower") == follower_port


def test_write_refuses_an_arm_that_does_not_look_right(capsys):
    assert main(["--mock", "--mock-speed", "20", "calibrate", "follower", "--yes"]) == 0
    capsys.readouterr()
    # the follower's calibration aimed at the leader's port: 5 V instead of 12 V gives it away
    assert main(["--mock", "write", "follower", "--port", "/dev/mock-leader"]) == 1
    assert "Not writing" in capsys.readouterr().err
    assert main(["--mock", "write", "follower"]) == 0
