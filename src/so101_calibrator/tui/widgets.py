"""TUI building blocks: the SO-101 sprite, emphasised text, the key line, joint details, the assistant."""

from __future__ import annotations

import re
import time
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING

from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Input, Static

from .. import ai as AI
from .. import assets
from ..i18n import t
from ..profiles import HALF_TURN, JOINTS, RESOLUTION
from ..theme import GRAPHITE, Theme

if TYPE_CHECKING:
    from .app import CalApp

THEME: Theme = GRAPHITE
ALIASES = {"power": "idle", "save": "idle"}  # steps without a clip of their own


def ago(iso: str) -> str:
    try:
        secs = time.time() - datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return ""
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if secs >= n:
            return t(f"ago.{unit}", n=int(secs // n))
    return t("ago.now")


def emphasis(text: str, style: str, accent: str | None = None) -> Text:
    """Text with *marked* words in the accent colour."""
    out = Text()
    for i, part in enumerate(text.split("*")):
        out.append(part, style=f"{style} {accent or THEME.accent}" if i % 2 else style)
    return out


def from_markdown(text: str) -> str:
    """Model answers use Markdown: **bold** becomes an *emphasis* mark, single asterisks become bullets."""
    return re.sub(r"(?<!\*)\*(?!\*)", "•", text).replace("**", "*")


def keys(*pairs: tuple[str, str], first_is_primary: bool = True) -> Text:
    """'⏎ done   r retry   esc cancel': the primary key boxed in the accent colour."""
    out = Text(justify="center")
    for n, (key, label) in enumerate(pairs):
        if n:
            out.append("     ")
        if n == 0 and first_is_primary:
            out.append(f" {key} ", style=f"bold {THEME.bg} on {THEME.accent}")
            out.append(f" {label}", style=THEME.fg)
        else:
            out.append(key, style=f"bold {THEME.fg}")
            out.append(f" {label}", style=THEME.dim)
    return out


# --- the sprite ----------------------------------------------------------------------------------------
@lru_cache(maxsize=1024)
def _style(fg: str | None, bg: str | None) -> Style:
    return Style(color=Color.parse(fg) if fg else None, bgcolor=Color.parse(bg) if bg else None)


def sprite_text(frame: bytes, w: int, h: int, colours: tuple[str | None, ...]) -> Text:
    """Role indices -> half-block text: one cell shows the pixel above (fg) and the one below (bg)."""
    out = Text(no_wrap=True, overflow="crop", justify="center")
    for y in range(0, h, 2):
        for x in range(w):
            top = colours[frame[y * w + x]]
            bottom = colours[frame[(y + 1) * w + x]] if y + 1 < h else None
            if top is None and bottom is None:
                out.append(" ")
            elif bottom is None:
                out.append("▀", _style(top, None))
            elif top is None:
                out.append("▄", _style(bottom, None))
            else:
                out.append("▀", _style(top, bottom))
        out.append("\n")
    return out


def usb_frame(i: int) -> Text:
    """A board with its USB cable sliding out and back in."""
    gap = [0, 0, 0, 1, 2, 3, 4, 5, 6, 6, 6, 6, 5, 4, 2, 0][i % 16]
    out = Text(justify="center", no_wrap=True)
    out.append("╭────────────╮" + " " * 34 + "\n", style=THEME.dim)
    out.append("│", style=THEME.dim)
    out.append("   SO-101   ", style=f"bold {THEME.fg}")
    out.append("│" + " " * gap, style=THEME.dim)
    out.append("▐█▌", style=f"bold {THEME.accent}")
    out.append("━" * (31 - gap) + "\n", style=THEME.dim)
    out.append("╰────────────╯" + " " * 34 + "\n", style=THEME.dim)
    return out


class Sprite(Widget):
    """Plays a clip from so101_calibrator.assets at the largest size that fits."""

    DEFAULT_CSS = "Sprite { width: 1fr; height: 1fr; content-align: center middle; }"

    def __init__(self, clip: str | None = None, kind: str = "step", **kw):
        super().__init__(**kw)
        self.clip, self.kind = clip, kind
        self.index, self.next_at = 0, 0.0
        self._cache: dict[tuple, list[tuple[Text, float]]] = {}

    def on_mount(self) -> None:
        self.set_interval(1 / 24, self._tick)

    def show(self, clip: str | None, kind: str = "step") -> None:
        clip = ALIASES.get(clip or "", clip)
        if (clip, kind) != (self.clip, self.kind):
            self.clip, self.kind, self.index, self.next_at = clip, kind, 0, 0.0
            self.refresh()

    def _frames(self) -> list[tuple[Text, float]]:
        if not self.clip:
            return []
        if self.clip == "usb":
            return [(usb_frame(i), 0.11) for i in range(16)]
        size = assets.best_size(self.clip, self.size.width, self.size.height * 2)
        if size is None:
            return []
        key = (self.clip, size, self.kind)
        if key not in self._cache:
            frames, w, h = assets.frames(self.clip, size)
            palette = THEME.sprite(self.kind)
            colours = tuple(palette.get(role) for role in assets.ROLES)
            delay = 1 / assets.index()[self.clip]["fps"]
            self._cache[key] = [(sprite_text(f, w, h, colours), delay) for f in frames]
        return self._cache[key]

    def _tick(self) -> None:
        frames = self._frames()
        if len(frames) > 1 and time.monotonic() >= self.next_at:
            self.index = (self.index + 1) % len(frames)
            self.next_at = time.monotonic() + frames[self.index][1]
            self.refresh()

    def render(self) -> Text:
        frames = self._frames()
        return frames[self.index % len(frames)][0] if frames else Text("")


# --- joint details (toggled with j) ----------------------------------------------------------------------
def joint_bar(raw, lo, hi, rec_lo, rec_hi, width: int) -> Text:
    """0..4095: stored range (grey), range recorded now (orange), the position (bright)."""
    def cell(v):
        return max(0, min(width - 1, int(v * width / RESOLUTION)))

    cells = [("─", THEME.surface)] * width
    for a, b, colour in ((lo, hi, THEME.dim), (rec_lo, rec_hi, THEME.accent)):
        if a is not None and b is not None:
            for i in range(cell(a), cell(b) + 1):
                cells[i] = ("━", colour)
    c = cell(HALF_TURN)
    if cells[c][0] == "─":
        cells[c] = ("┊", THEME.dim)
    if raw is not None:
        cells[cell(raw)] = ("┃", f"bold {THEME.fg}")
    out = Text()
    for ch, style in cells:
        out.append(ch, style=style)
    return out


def joints_text(snap: dict, width: int) -> Text:
    cur = snap.get("current") or {}
    recorded = (cur.get("progress") or {}).get("recorded", {})
    out = Text(justify="center")
    for role, arm in snap["arms"].items():
        if not arm["connected"]:
            continue
        out.append(f"{role}  {arm['port']}\n", style=THEME.dim)
        for j in JOINTS:
            info, r = arm["joints"][j], recorded.get(j, {})
            out.append(f"{j:<14}", style=THEME.dim)
            out.append_text(joint_bar(info["raw"], info["cal_min"], info["cal_max"], r.get("min"), r.get("max"), width))
            out.append(f" {'' if info['raw'] is None else info['raw']:>4}\n", style=f"bold {THEME.fg}")
    return out


# --- assistant ---------------------------------------------------------------------------------------------
class AIPanel(Vertical):
    """Hidden until `a`. An empty question means: explain the current problem."""

    DEFAULT_CSS = f"""
    AIPanel {{ height: auto; max-height: 14; width: 90; border: round {THEME.dim}; padding: 0 1; display: none;
               margin: 0 0 1 0; }}
    AIPanel #ai-answer {{ height: auto; }}
    AIPanel Input, AIPanel Input:focus {{ border: none; height: 1; padding: 0 1; background: {THEME.surface}; }}
    """

    def compose(self) -> ComposeResult:
        yield Static(id="ai-answer")
        yield Input(id="ai-input", disabled=True)  # disabled while hidden, so it never takes the focus

    @property
    def cal_app(self) -> CalApp:
        return self.app  # type: ignore[return-value]

    def open(self) -> None:
        assistant = self.cal_app.assistant
        inp = self.query_one(Input)
        inp.placeholder = t("ai.ask", model=assistant.label) if assistant else t("ai.off")
        self.display = True
        inp.disabled = False
        inp.focus()

    def close(self) -> None:
        self.query_one(Input).disabled = True
        self.display = False
        self.screen.focus_next()

    def say(self, text: str, style: str = "") -> None:
        self.query_one("#ai-answer", Static).update(emphasis(from_markdown(text), style or THEME.fg))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        app = self.cal_app
        question = event.value
        event.input.value = ""
        if app.assistant is None:
            if not question.strip():
                self.say(t("ai.off"), THEME.dim)
                return
            try:
                app.set_ai(question.strip())
            except AI.AIError as e:
                self.say(str(e), THEME.error)
                return
            self.say(t("ai.on", model=app.assistant.label), THEME.accent)
            self.open()
            return
        self.say(t("ai.thinking"), THEME.dim)
        state = AI.describe(app.controller.snapshot(), app.env.store.arms())
        assistant = app.assistant

        def work() -> None:
            try:
                answer = assistant.ask(question, state, on_text=lambda s: app.call_from_thread(self.say, s))
                style = ""
            except AI.AIError as e:
                answer, style = str(e), THEME.error
            except Exception as e:  # a model/network hiccup must never take the calibration UI down
                answer, style = f"{type(e).__name__}: {e}", THEME.error
            app.call_from_thread(self.say, answer, style)

        self.run_worker(work, thread=True, exclusive=True, group="ai")

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.close()
