import asyncio
import time

from textual.widgets import Input, Static

from so101_calibrator import settings
from so101_calibrator.tui import CalApp
from so101_calibrator.tui.screens import HistoryScreen, RunScreen
from so101_calibrator.tui.widgets import AIPanel

from .conftest import mock_env


def make_app(faults: str | None = None) -> CalApp:
    return CalApp(mock_env(faults), "off", "en")


async def run_calibration(app: CalApp, pilot) -> dict:
    """Presses Enter like a user would; the mock actor moves the arm."""
    await pilot.press("enter")  # "Calibrate follower" is the first menu item
    await pilot.pause(0.2)
    assert isinstance(app.screen, RunScreen)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 60:
        snap = app.controller.snapshot()
        if snap["finished"]:
            return snap
        cur = snap["current"]
        if cur["status"] == "running" and cur["id"] == "ranges":
            rec = cur["progress"].get("recorded", {})
            if rec and min(r["fill"] for r in rec.values()) >= 0.95:
                await pilot.press("enter")
        elif cur["status"] in ("ready", "warn"):
            await pilot.press("enter")
        elif cur["status"] == "fail":
            raise AssertionError(snap["steps"])
        await pilot.pause(0.1)
    raise AssertionError("calibration did not finish")


def test_tui_calibrate_history_and_ai_setup():
    async def scenario():
        app = make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            snap = await run_calibration(app, pilot)
            assert snap["result"] == "pass" and snap["record"] == 1
            await pilot.pause(0.2)
            head = str(app.screen.query_one("#hint", Static).render())
            assert "Done" in head

            await pilot.press("enter")  # done -> home
            await pilot.pause(0.2)
            assert app.controller.session is None
            assert app.env.store.active("follower").version == 1

            await pilot.press("h")
            await pilot.pause(0.2)
            assert isinstance(app.screen, HistoryScreen) and app.screen.rows == [("follower", 1)]
            await pilot.press("escape")

            # AI is off: typing a provider in the panel turns it on and saves it
            await pilot.press("a")
            await pilot.pause(0.1)
            panel = app.screen.query_one(AIPanel)
            assert panel.display and "Assistant is off" in panel.query_one(Input).placeholder
            for ch in "ollama:tiny":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.assistant is not None and app.assistant.label == "ollama:tiny"
            await pilot.press("escape")
            assert not panel.display

    asyncio.run(scenario())


def test_tui_abort_needs_two_escapes():
    async def scenario():
        app = make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("enter")
            await pilot.pause(0.2)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert isinstance(app.screen, RunScreen)
            assert "press again" in str(app.screen.query_one("#keys", Static).render())
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, RunScreen) and app.controller.session is None

    asyncio.run(scenario())


def test_tui_ai_answer_uses_session_state():
    class FakeAssistant:
        label = "fake"

        def __init__(self):
            self.states = []

        def ask(self, question, state, on_text=None):
            self.states.append(state)
            on_text("Plug in")
            return "Plug in the 12 V adapter."

    async def scenario():
        app = make_app("voltage:5")
        app.assistant = FakeAssistant()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("enter")
            await pilot.pause(0.2)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 30:
                cur = app.controller.snapshot()["current"]
                if cur["status"] == "fail":
                    break
                if cur["status"] == "ready":
                    await pilot.press("enter")
                await pilot.pause(0.1)
            assert app.controller.snapshot()["current"]["id"] == "power"
            await pilot.press("a")
            await pilot.press("enter")  # empty question: explain the problem
            await pilot.pause(0.3)
            answer = str(app.screen.query_one("#ai-answer", Static).render())
            assert "12 V adapter" in answer
            power = next(s for s in app.assistant.states[0]["steps"] if s["step"] == "Check supply voltage")
            assert power["status"] == "fail"

    asyncio.run(scenario())


def test_tui_language_toggle_is_remembered():
    async def scenario():
        app = make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            assert "CALIBRATION" in str(app.screen.query_one("#title", Static).render())
            await pilot.press("l")
            await pilot.pause(0.1)
            assert "KALİBRASYON" in str(app.screen.query_one("#title", Static).render())
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert "USB portu" in str(app.screen.query_one("#progress-dots", Static).render())
        assert settings.load(app.env.store.root)["lang"] == "tr"  # the next start opens in Turkish

    asyncio.run(scenario())
