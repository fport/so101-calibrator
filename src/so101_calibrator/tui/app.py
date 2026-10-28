"""The Textual application: owns the controller (sessions), the environment, the language and the assistant."""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from .. import ai as AI
from .. import i18n, settings
from ..controller import Controller
from ..env import Env
from .screens import HomeScreen, RunScreen
from .widgets import THEME

CSS = f"""
Screen {{ background: {THEME.bg}; color: {THEME.fg}; }}
.center {{ width: 100%; height: auto; content-align: center middle; }}
Center {{ height: auto; }}
#home, #run, #history {{ height: 100%; padding: 1 2; }}
#hero {{ height: 1fr; min-height: 8; }}
#title {{ margin: 1 0; }}
#menu {{ margin: 0 0 1 0; }}
#progress-dots {{ margin: 0 0 1 0; }}
#art {{ height: 1fr; min-height: 8; }}
#hint {{ margin: 1 0 0 0; }}
#meter {{ margin: 1 0 0 0; }}
#checks {{ margin: 1 0 0 0; }}
#details {{ margin: 1 0 0 0; max-width: 100; }}
#joints {{ margin: 1 0 0 0; }}
#keys {{ dock: bottom; height: 1; margin: 0 0 1 0; }}
#history-title {{ margin: 1 0; }}
#versions {{ width: 70; height: auto; max-height: 20; background: {THEME.bg}; }}
DataTable > .datatable--cursor {{ background: {THEME.accent}; color: {THEME.bg}; }}
DataTable > .datatable--header {{ height: 0; }}
#info {{ margin: 1 0; }}
"""


class CalApp(App):
    TITLE = "so101-calibrator"
    CSS = CSS
    BINDINGS = [Binding("ctrl+c", "quit", "quit", priority=True)]

    def __init__(self, env: Env, ai_spec: str | None = None, lang: str | None = None):
        super().__init__()
        self.env = env
        self.controller = Controller(env)
        prefs = settings.load(env.store.root)
        i18n.set_lang(lang or prefs.get("lang") or i18n.detect())
        self.assistant: AI.Assistant | None = None
        spec = AI.load_spec(env.store.root, ai_spec)
        if spec:
            try:
                self.assistant = AI.Assistant(spec)
            except AI.AIError:
                self.assistant = None

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())

    def toggle_language(self) -> None:
        lang = "en" if i18n.get_lang() == "tr" else "tr"
        i18n.set_lang(lang)
        settings.update(self.env.store.root, lang=lang)

    def set_ai(self, spec: str) -> None:
        self.assistant = AI.Assistant(spec)
        AI.save_spec(self.env.store.root, spec)

    def start_flow(self, flow: str, role: str) -> None:
        self.controller.start(flow, role=role)
        self.push_screen(RunScreen(flow, role))

    def end_flow(self) -> None:
        self.controller.close()
        self.pop_screen()

    def on_unmount(self) -> None:
        self.controller.shutdown()


def run(env: Env, ai_spec: str | None = None, lang: str | None = None) -> int:
    app = CalApp(env, ai_spec, lang)
    try:
        app.run()
    finally:  # also when the app crashed: abort the session so the follower never keeps torque on
        app.controller.shutdown()
    return app.return_code or 0
