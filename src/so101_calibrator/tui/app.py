"""The Textual application: owns the controller (sessions), the environment and the AI helper."""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from .. import ai as AI
from ..controller import Controller
from ..env import Env
from .screens import HomeScreen, RunScreen

TITLES = {"calibrate": "Calibrate {role}", "verify": "Verify {role}", "teleop": "Teleop test"}


class CalApp(App):
    TITLE = "so101-calibrator"
    CSS = """
    Screen { background: $background; }
    #brand { padding: 1 2; height: auto; }
    #home { height: 1fr; }
    #menu { width: 62; margin: 0 2; height: auto; max-height: 12; border: round $primary-darken-2; }
    #home-arm { margin: 0 2 1 2; }
    #body { height: 1fr; }
    #steps { width: 34; padding: 1 1; border-right: solid $primary-darken-3; }
    #main { padding: 1 2; }
    #head { height: auto; }
    #stage { height: 1fr; min-height: 12; margin: 1 0 0 0; }
    #arm { width: 46; }
    #guide { width: 1fr; padding: 0 0 0 2; }
    #hint { height: auto; }
    #instructions { height: auto; margin: 1 0 0 0; }
    #progress { height: auto; margin: 1 0 0 0; }
    #checks { height: auto; margin: 1 0 0 0; }
    #joints { height: auto; margin: 1 0 0 0; }
    #versions { height: 1fr; margin: 0 1; }
    #info { height: auto; max-height: 14; padding: 1 2; }
    #history-head { padding: 1 2 0 2; }
    .keys { dock: bottom; height: 1; background: $panel; }
    """
    BINDINGS = [Binding("ctrl+c", "quit", "quit", priority=True)]

    def __init__(self, env: Env, ai_spec: str | None = None):
        super().__init__()
        self.env = env
        self.controller = Controller(env)
        self.assistant: AI.Assistant | None = None
        spec = AI.load_spec(env.store.root, ai_spec)
        if spec:
            try:
                self.assistant = AI.Assistant(spec)
            except AI.AIError:
                self.assistant = None

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())

    def set_ai(self, spec: str) -> None:
        self.assistant = AI.Assistant(spec)
        AI.save_spec(self.env.store.root, spec)

    def start_flow(self, flow: str, role: str) -> None:
        self.controller.start(flow, role=role)
        self.push_screen(RunScreen(TITLES[flow].format(role=role)))

    def end_flow(self) -> None:
        self.controller.close()
        self.pop_screen()

    def on_unmount(self) -> None:
        self.controller.shutdown()


def run(env: Env, ai_spec: str | None = None) -> int:
    app = CalApp(env, ai_spec)
    try:
        app.run()
    finally:  # also when the app crashed: abort the session so the follower never keeps torque on
        app.controller.shutdown()
    return app.return_code or 0
