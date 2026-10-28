"""Reusable TUI pieces: the SO-101 animation, the AI panel, joint bars and the key hint line."""

from __future__ import annotations

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
from ..profiles import HALF_TURN, JOINTS, RESOLUTION

if TYPE_CHECKING:
    from .app import CalApp

ICON = {"pass": ("✓", "green"), "warn": ("!", "yellow"), "fail": ("✗", "red"), "running": ("●", "cyan"),
        "ready": ("›", "bold"), "pending": ("·", "dim")}
STORED = "steel_blue"
RECORDED = "dark_orange"
# illustrations without a clip of their own
ALIASES = {"power": "idle", "save": "idle"}


def ago(iso: str) -> str:
    try:
        secs = time.time() - datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return ""
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if secs >= n:
            return f"{int(secs // n)}{unit} ago"
    return "just now"


def keys_line(*pairs: tuple[str, str]) -> Text:
    t = Text()
    for k, label in pairs:
        t.append(f" {k} ", style="bold black on grey70")
        t.append(f" {label}   ")
    return t


# --- the animation -----------------------------------------------------------------------------------
@lru_cache(maxsize=4096)
def _style(fg: tuple[int, int, int] | None, bg: tuple[int, int, int] | None) -> Style:
    return Style(color=Color.from_rgb(*fg) if fg else None, bgcolor=Color.from_rgb(*bg) if bg else None)


def halfblocks(img) -> Text:
    """An RGBA image as text, two pixels per cell (upper half block: fg = top pixel, bg = bottom pixel).
    Transparent pixels stay the terminal background."""
    w, h = img.size
    px = img.load()
    t = Text(no_wrap=True, overflow="crop")
    for y in range(0, h - 1, 2):
        for x in range(w):
            top, bot = px[x, y], px[x, y + 1]
            a = top[:3] if top[3] > 96 else None
            b = bot[:3] if bot[3] > 96 else None
            if a is None and b is None:
                t.append(" ")
            elif b is None:
                t.append("▀", _style(a, None))
            elif a is None:
                t.append("▄", _style(b, None))
            else:
                t.append("▀", _style(a, b))
        t.append("\n")
    return t


USB_FRAMES = 16


def usb_frame(i: int, width: int) -> Text:
    """A board with a USB cable sliding out and back in (for the find-the-port step)."""
    gap = [0, 0, 0, 1, 2, 3, 4, 5, 6, 6, 6, 6, 5, 4, 2, 0][i % USB_FRAMES]
    cable = max(4, width - 24 - gap)
    t = Text(no_wrap=True)
    t.append("\n\n  ╭──────────────╮\n", style="grey62")
    t.append("  │  ", style="grey62")
    t.append("motor board", style="bold")
    t.append(" │", style="grey62")
    t.append(" " * gap)
    t.append("▐█▌", style="bold dark_orange")
    t.append("━" * cable + "\n", style="grey70")
    t.append("  ╰──────────────╯\n", style="grey62")
    return t


class Illustration(Widget):
    """Plays the SO-101 clip a step asks for, scaled to the widget (half-block pixels)."""

    DEFAULT_CSS = "Illustration { width: 1fr; height: 1fr; }"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.clip: str | None = None
        self.index = 0
        self.next_at = 0.0
        self._cache: dict[tuple, list[tuple[Text, int]]] = {}

    def on_mount(self) -> None:
        self.set_interval(1 / 24, self._advance)

    def show(self, clip: str | None) -> None:
        clip = ALIASES.get(clip or "", clip)
        if clip != self.clip:
            self.clip, self.index, self.next_at = clip, 0, 0.0
            self.refresh()

    def _frames(self) -> list[tuple[Text, int]]:
        w, h = self.size.width, self.size.height
        if not self.clip or w < 8 or h < 4:
            return []
        key = (self.clip, w, h)
        if key not in self._cache:
            if self.clip == "usb":
                self._cache[key] = [(usb_frame(i, w), 110) for i in range(USB_FRAMES)]
            else:
                self._cache[key] = [(halfblocks(img), ms) for img, ms in assets.frames(self.clip, (w, h * 2))]
        return self._cache[key]

    def _advance(self) -> None:
        frames = self._frames()
        if len(frames) < 2:
            return
        now = time.monotonic()
        if now >= self.next_at:
            self.index = (self.index + 1) % len(frames)
            self.next_at = now + frames[self.index][1] / 1000
            self.refresh()

    def render(self) -> Text:
        frames = self._frames()
        return frames[self.index % len(frames)][0] if frames else Text("")


# --- joint bars --------------------------------------------------------------------------------------
def joint_bar(raw, lo, hi, rec_lo, rec_hi, width: int) -> Text:
    """0..4095 on `width` cells: stored range (blue), range recorded now (orange), centre tick, position."""
    def cell(v):
        return max(0, min(width - 1, int(v * width / RESOLUTION)))

    cells = [("─", "grey30")] * width
    for a, b, style in ((lo, hi, STORED), (rec_lo, rec_hi, RECORDED)):
        if a is not None and b is not None:
            for i in range(cell(a), cell(b) + 1):
                cells[i] = ("━", style)
    c = cell(HALF_TURN)
    if cells[c][0] == "─":
        cells[c] = ("┊", "grey50")
    if raw is not None:
        cells[cell(raw)] = ("█", "bold white")
    t = Text()
    for ch, style in cells:
        t.append(ch, style=style)
    return t


def fill_bar(frac: float, width: int = 30) -> Text:
    n = int(round(max(0.0, min(1.0, frac)) * width))
    t = Text("█" * n, style=RECORDED)
    t.append("░" * (width - n), style="grey30")
    t.append(f" {frac * 100:3.0f}%")
    return t


def joints_text(snap: dict, width: int) -> Text:
    """Live joint bars of every connected arm (nothing for an arm that is not connected yet)."""
    cur = snap.get("current") or {}
    recorded = (cur.get("progress") or {}).get("recorded", {})
    t = Text()
    for role, arm in snap["arms"].items():
        if not arm["connected"]:
            continue
        t.append(f"{role}", style="bold")
        t.append(f"  {arm['arm_id']}  {arm['port']}\n", style="dim")
        for j in JOINTS:
            info = arm["joints"][j]
            r = recorded.get(j, {})
            t.append(f" {j:<13} ")
            t.append_text(joint_bar(info["raw"], info["cal_min"], info["cal_max"], r.get("min"), r.get("max"), width))
            t.append(f" {'' if info['raw'] is None else info['raw']:>4}", style="bold")
            t.append(f" {'' if info['norm'] is None else format(info['norm'], '+.0f'):>5}\n", style="dim")
        t.append("\n")
    if t.plain:
        t.append(" ━ stored range  ", style=STORED)
        t.append("━ recorded now  ", style=RECORDED)
        t.append("█ position", style="bold")
    return t


# --- AI ------------------------------------------------------------------------------------------------
class AIPanel(Vertical):
    """Hidden until `a`. An empty question means: explain the current problem."""

    DEFAULT_CSS = """
    AIPanel { height: auto; max-height: 16; border: round $accent; margin: 0 1; padding: 0 1; display: none; }
    AIPanel #ai-answer { height: auto; }
    AIPanel Input { border: none; height: 1; padding: 0; }
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
        if assistant:
            inp.placeholder = f"Ask {assistant.label} (Enter on empty: explain the current problem)"
        else:
            inp.placeholder = "AI is off. Type a model to turn it on: ollama, claude, openai:<model>"
        self.display = True
        inp.disabled = False
        inp.focus()

    def close(self) -> None:
        self.query_one(Input).disabled = True
        self.display = False
        self.screen.focus_next()

    def say(self, text: str, style: str = "") -> None:
        self.query_one("#ai-answer", Static).update(Text(text, style=style))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        app = self.cal_app
        question = event.value
        event.input.value = ""
        if app.assistant is None:
            if not question.strip():
                self.say("Examples: ollama  ·  ollama:qwen2.5  ·  claude  ·  openai:<model> with SO101CAL_AI_URL",
                         "dim")
                return
            try:
                app.set_ai(question.strip())
            except AI.AIError as e:
                self.say(str(e), "red")
                return
            self.say(f"AI on: {app.assistant.label}. Ask away.", "green")
            self.open()
            return
        self.say("thinking…", "dim italic")
        state = AI.describe(app.controller.snapshot(), app.env.store.arms())
        assistant = app.assistant

        def work() -> None:
            try:
                answer = assistant.ask(question, state, on_text=lambda t: app.call_from_thread(self.say, t))
                style = ""
            except AI.AIError as e:
                answer, style = str(e), "red"
            except Exception as e:  # a model/network hiccup must never take the calibration UI down
                answer, style = f"AI error: {type(e).__name__}: {e}", "red"
            app.call_from_thread(self.say, answer, style)

        self.run_worker(work, thread=True, exclusive=True, group="ai")

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.close()
