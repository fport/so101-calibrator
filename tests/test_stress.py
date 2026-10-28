"""Randomized "impatient user" tests: any action in any state, on every flow and fault, must never crash
the engine or a front end, and must never leave follower torque on after the session ends."""

import asyncio
import json
import random

import pytest

from so101_calibrator import ai as AI
from so101_calibrator import render
from so101_calibrator.mock import MockActor, MockRig
from so101_calibrator.tui import CalApp
from so101_calibrator.wizard import Session, Target

from .conftest import calibrate, make_ctx, mock_env

ACTIONS = ("start", "stop", "retry", "accept", "force", "poll", "poll", "poll", "poll")
FAULTS = ["", "missing:4", "voltage:5", "reversed:elbow_flex", "stuck:wrist_flex", "hot:gripper",
          "leader.voltage:12", "missing:1,missing:2,missing:3,missing:4,missing:5,missing:6"]


def act(session: Session, name: str) -> None:
    if name == "poll":
        session.poll()
    else:
        session.act(name)


def check_views(session: Session) -> None:
    snap = session.snapshot()
    json.dumps(snap)
    render.live_lines(snap)
    json.dumps(AI.describe(snap, session.ctx.store.arms()))


def torque_off(rig: MockRig) -> bool:
    bus = rig.bus(rig.port_of("follower"))
    return all(bus.regs[i].get("Torque_Enable", 0) == 0 for i in range(1, 7) if i in bus.regs)


def new_session(flow: str, store, rig: MockRig) -> Session:
    ctx = make_ctx(store, rig, actor=MockActor(rig, speed=50), teleop_seconds=0.3)
    if flow == "calibrate":
        return Session.calibrate(ctx, Target("follower", "follower"))
    if flow == "verify":
        return Session.verify(ctx, Target("follower", "follower"))
    return Session.teleop(ctx, Target("leader", "leader"), Target("follower", "follower"))


@pytest.mark.parametrize("faults", FAULTS)
@pytest.mark.parametrize("flow", ["calibrate", "verify", "teleop"])
def test_random_actions_never_crash(store, flow, faults):
    rig = MockRig.create(faults)
    if flow != "calibrate":  # verify/teleop need stored calibrations
        calibrate(store, MockRig.create(), "leader", port_known=True)
        calibrate(store, MockRig.create(), "follower", port_known=True)
    rng = random.Random(f"{flow}/{faults}")
    for _ in range(3):
        session = new_session(flow, store, rig)
        for _ in range(250):
            act(session, rng.choice(ACTIONS))
            check_views(session)
            if session.finished:
                break
        session.abort()
        check_views(session)
        assert torque_off(rig), "follower torque left on after the session ended"


@pytest.mark.parametrize("seed", range(4))
def test_tui_random_keys_never_crash(seed):
    """Mash keys in the TUI (menus, wizard, history, AI box) on a faulty mock; any exception fails the test."""
    keys = ["enter"] * 6 + ["r", "f", "s", "question_mark", "a", "escape", "escape", "h", "down", "up", "x", "1"]

    async def scenario():
        faults = FAULTS[seed % len(FAULTS)]
        app = CalApp(mock_env(faults), "off")
        rng = random.Random(seed)
        async with app.run_test(size=(100, 32)) as pilot:
            for _ in range(160):
                await pilot.press(rng.choice(keys))
                await pilot.pause(0.02)
                if app.return_code is not None:  # quit from the menu: fine, start over is not needed
                    break

    asyncio.run(scenario())
