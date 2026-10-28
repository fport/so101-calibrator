import pytest

from so101_calibrator.mock import MockHand, MockRig
from so101_calibrator.store import Store
from so101_calibrator.wizard import Context, Session, Target


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101CAL_HOME", str(tmp_path / "store"))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "lerobot"))
    monkeypatch.delenv("HF_LEROBOT_HOME", raising=False)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store")


def make_ctx(store, rig, **kw):
    return Context(store=store, open_bus=rig.bus, list_ports=rig.ports.list, **kw)


def drive(session: Session, rig: MockRig, *, accept_warnings=True, until=None):
    """Plays the human deterministically (no threads): each step gets the physical action it asks for."""
    for _ in range(100):
        if session.finished or (until and session.step.kind == until):
            return session
        st, state = session.step, session.state
        hand = MockHand(rig.arm(st.role)) if st.role in ("leader", "follower") else None
        if state.status == "ready":
            if st.kind in ("homing", "pose"):
                hand.middle()
            session.start()
            if session.state is state and state.status == "running":
                if st.kind == "port":
                    port = rig.port_of(st.role)
                    rig.ports.unplug(port)
                    session.poll()
                    rig.ports.replug(port)
                    session.poll()
                elif st.kind == "ranges":
                    for j in rig.arm(st.role).pose:
                        if j == "wrist_roll":
                            continue
                        for k in range(41):
                            rig.arm(st.role).set_pose(j, -1 + k / 20)
                            session.poll()
                        rig.arm(st.role).set_pose(j, 0.0)
                    session.poll()
                    session.stop()
                elif st.kind == "direction":
                    arm = rig.arm(st.role)
                    for j in list(arm.pose):
                        arm.set_pose(j, 0.35)
                        session.poll()
                        arm.set_pose(j, 0.0)
                        session.poll()
                    if session.state is state and state.status == "running":
                        session.stop()
                elif st.kind == "teleop":
                    for k in range(40):
                        for j in rig.leader.pose:
                            rig.leader.set_pose(j, 0.2 * (k / 40))
                        session.poll()
                    session.stop()
        elif state.status == "warn" and accept_warnings:
            session.next()
        else:
            return session
    raise AssertionError("wizard did not finish")


def calibrate(store, rig, role, arm_id=None, port_known=False, **ctx_kw):
    ctx = make_ctx(store, rig, **ctx_kw)
    target = Target(role, arm_id or role, port=rig.port_of(role) if port_known else None)
    return drive(Session.calibrate(ctx, target), rig)
