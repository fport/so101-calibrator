import pytest

from so101_calibrator.env import Env
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


class FakeClock:
    """Time that only moves when a test says so (the teleop test limits speed per second)."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_ctx(store, rig, **kw):
    kw.setdefault("clock", FakeClock())
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
            elif st.kind == "teleop":  # both arms folded, as the step asks
                MockHand(rig.leader).rest()
                MockHand(rig.follower).rest()
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
                elif st.kind == "teleop":
                    start = dict(rig.leader.pose)
                    for k in range(120):  # unfold every joint a little, towards the middle (past the slow start)
                        for j, v in start.items():
                            rig.leader.set_pose(j, v + (0.2 if v <= 0 else -0.2) * (k / 120))
                        session.ctx.clock.advance(0.04)  # a 25 Hz loop
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


def mock_env(faults: str | None = None, speed: float = 20) -> Env:
    """A simulated rig in the test's store ($SO101CAL_HOME)."""
    return Env(Store(), mock=True, mock_faults=faults, mock_speed=speed)
