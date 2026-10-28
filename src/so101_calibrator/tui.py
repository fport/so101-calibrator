"""Terminal UI: `so101-cal` with no command. One key does the obvious thing (Enter), everything else is a
single letter shown at the bottom. Drives the same wizard sessions and store as the CLI and the dashboard."""

from __future__ import annotations

import time
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Input, OptionList, Static
from textual.widgets.option_list import Option

from . import ai as AI
from .profiles import HALF_TURN, JOINTS, RESOLUTION
from .runner import Runner, tilde

ICON = {"pass": ("✓", "green"), "warn": ("!", "yellow"), "fail": ("✗", "red"), "running": ("●", "cyan"),
        "ready": ("›", "bold"), "pending": ("·", "dim"), "skipped": ("-", "dim")}
BADGE = {"ready": ("READY", "black on grey70"), "running": ("RECORDING", "black on dark_orange"),
         "pass": ("PASS", "black on green"), "warn": ("WARN", "black on yellow"), "fail": ("FAIL", "white on red")}
CAL = "steel_blue"
REC = "dark_orange"


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


def joint_bar(raw, lo, hi, rec_lo, rec_hi, width: int) -> Text:
    """0..4095 on `width` cells: stored range (blue), range recorded now (orange), centre tick, position."""
    def cell(v):
        return max(0, min(width - 1, int(v * width / RESOLUTION)))

    cells = [("─", "grey30")] * width
    for a, b, style in ((lo, hi, CAL), (rec_lo, rec_hi, REC)):
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
    t = Text("█" * n, style=REC)
    t.append("░" * (width - n), style="grey30")
    t.append(f" {frac * 100:3.0f}%")
    return t


# ==================================================================================================
class AIPanel(Vertical):
    """Hidden until `a`. Empty question = explain the current problem."""

    def compose(self) -> ComposeResult:
        yield Static(id="ai-answer")
        yield Input(id="ai-input")

    def on_mount(self) -> None:
        self.display = False

    def open(self) -> None:
        app: CalApp = self.app
        inp = self.query_one(Input)
        if app.assistant:
            inp.placeholder = f"Ask {app.assistant.label} (Enter on empty: explain the current problem)"
        else:
            inp.placeholder = "AI is off. Type a model to turn it on: ollama, claude, openai:<model>"
        self.display = True
        inp.focus()

    def close(self) -> None:
        self.display = False
        self.screen.focus_next()

    def say(self, text: str, style: str = "") -> None:
        self.query_one("#ai-answer", Static).update(Text(text, style=style))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        app: CalApp = self.app
        q = event.value
        event.input.value = ""
        if app.assistant is None:
            if not q.strip():
                self.say("Examples: ollama  ·  ollama:qwen2.5  ·  claude  ·  openai:<model> with SO101CAL_AI_URL",
                         "dim")
                return
            try:
                app.set_ai(q.strip())
            except AI.AIError as e:
                self.say(str(e), "red")
                return
            self.say(f"AI on: {app.assistant.label}. Ask away.", "green")
            self.open()
            return
        self.say("thinking…", "dim italic")
        state = AI.describe(app.snapshot(), app.runner.env.store.arms())
        assistant = app.assistant

        def work():
            try:
                answer = assistant.ask(q, state, on_text=lambda t: app.call_from_thread(self.say, t))
                style = ""
            except AI.AIError as e:
                answer, style = str(e), "red"
            except Exception as e:  # a model/network hiccup must never kill the calibration UI
                answer, style = f"AI error: {e}", "red"
            app.call_from_thread(self.say, answer, style)

        self.run_worker(work, thread=True, exclusive=True, group="ai")

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.close()


# ==================================================================================================
class HomeScreen(Screen):
    BINDINGS = [Binding("a", "ask", "ask AI"), Binding("h", "history", "history"), Binding("q", "app.quit", "quit")]

    def compose(self) -> ComposeResult:
        yield Static(id="brand")
        yield OptionList(id="menu")
        yield AIPanel()
        yield Static(keys_line(("⏎", "open"), ("h", "history"), ("a", "ask AI"), ("q", "quit")), classes="keys")

    def on_screen_resume(self) -> None:
        self.refresh_menu()

    def on_mount(self) -> None:
        self.refresh_menu()

    def refresh_menu(self) -> None:
        app: CalApp = self.app
        env = app.runner.env
        brand = Text("SO-101 Calibrator", style="bold")
        if env.mock:
            brand.append("  ")
            brand.append(" MOCK ", style="bold black on yellow")
        brand.append(f"   {tilde(env.store.root)}", style="dim")
        if app.assistant:
            brand.append(f"   AI: {app.assistant.label}", style="dim")
        self.query_one("#brand", Static).update(brand)

        def status(arm_id: str) -> Text:
            info = env.store.arm(arm_id)
            rec = env.store.active(arm_id)
            if not info:
                return Text("not calibrated yet", style="dim")
            if not rec:
                return Text("no active version", style="yellow")
            return Text(f"v{rec.version} active · {ago(rec.data['created'])}", style="dim")

        def item(label: str, sub: Text) -> Text:
            t = Text(f"{label:<22}", style="bold")
            t.append_text(sub)
            return t

        menu = self.query_one(OptionList)
        highlighted = menu.highlighted
        menu.clear_options()
        menu.add_options([
            Option(item("Calibrate follower", status("follower")), id="calibrate:follower"),
            Option(item("Calibrate leader", status("leader")), id="calibrate:leader"),
            Option(item("Verify follower", Text("motors + middle pose", style="dim")), id="verify:follower"),
            Option(item("Verify leader", Text("motors + middle pose", style="dim")), id="verify:leader"),
            Option(item("Teleop test", Text("leader → follower", style="dim")), id="teleop"),
            Option(item("History", Text(f"{len(env.store.arms())} arm(s)", style="dim")), id="history"),
            Option(item("Quit", Text("")), id="quit"),
        ])
        menu.highlighted = highlighted if highlighted is not None else 0
        menu.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        choice = event.option_id
        if choice == "quit":
            self.app.exit()
        elif choice == "history":
            self.action_history()
        else:
            flow, _, role = choice.partition(":")
            self.app.start_flow(flow, role or None)

    def action_history(self) -> None:
        self.app.push_screen(HistoryScreen())

    def action_ask(self) -> None:
        self.query_one(AIPanel).open()


# ==================================================================================================
class RunScreen(Screen):
    BINDINGS = [
        Binding("enter", "primary", "do it", priority=False),
        Binding("r", "act('retry')", "retry"),
        Binding("f", "act('force')", "accept anyway"),
        Binding("s", "act('skip')", "skip"),
        Binding("question_mark", "details", "details"),
        Binding("a", "ask", "ask AI"),
        Binding("h", "history", "history"),
        Binding("escape", "back", "back"),
    ]

    def __init__(self, title: str):
        super().__init__()
        self.flow_title = title
        self.show_details = False
        self.confirm_abort = 0.0
        self._last = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Static(id="steps")
            with VerticalScroll(id="main"):
                yield Static(id="head")
                yield Static(id="hint")
                yield Static(id="progress")
                yield Static(id="details")
                yield Static(id="checks")
                yield Static(id="joints")
        yield AIPanel()
        yield Static(id="keys", classes="keys")

    def on_mount(self) -> None:
        self.set_interval(1 / 15, self.render_state)
        self.render_state()

    # --- actions ------------------------------------------------------------------------------------
    def _snap(self):
        return self.app.snapshot()

    def action_primary(self) -> None:
        snap = self._snap()
        if snap is None or snap["finished"]:
            self.app.end_flow()
            return
        cur = snap["current"]
        can = cur["can"]
        for name in ("start", "stop", "next", "retry"):
            if can[name]:
                self.action_act(name)
                return

    def action_act(self, name: str) -> None:
        snap = self._snap()
        if snap is None or snap["finished"] or not snap["current"]["can"].get(name):
            return
        runner = self.app.runner
        self.run_worker(lambda: runner.action({"action": name}), thread=True, group="action")

    def action_details(self) -> None:
        self.show_details = not self.show_details
        self._last = None

    def action_ask(self) -> None:
        self.query_one(AIPanel).open()

    def action_history(self) -> None:
        snap = self._snap()
        if snap is None or snap["finished"]:
            self.app.push_screen(HistoryScreen())

    def action_back(self) -> None:
        snap = self._snap()
        if snap is None or snap["finished"]:
            self.app.end_flow()
            return
        now = time.monotonic()
        if now - self.confirm_abort < 2.5:
            self.app.runner.action({"action": "abort"})
            self.app.end_flow()
        else:
            self.confirm_abort = now
            self._last = None

    # --- rendering ----------------------------------------------------------------------------------
    def render_state(self) -> None:
        snap = self._snap()
        if snap is None:
            return
        width = max(20, self.query_one("#main").size.width - 26)
        abort_armed = time.monotonic() - self.confirm_abort < 2.5
        key = (repr(snap), width, self.show_details, abort_armed)
        if key == self._last:
            return
        self._last = key

        # steps column
        steps = Text(f"{self.flow_title}\n\n", style="bold")
        for i, st in enumerate(snap["steps"]):
            icon, style = ICON.get(st["status"], ("·", "dim"))
            current = snap["current"] and snap["current"]["id"] == st["id"]
            steps.append(f" {icon} ", style=style)
            label = f"{i + 1}. {st['label']}"
            if len(label) > 28:
                label = label[:27] + "…"
            steps.append(f"{label}\n", style="bold reverse" if current else "")
        self.query_one("#steps", Static).update(steps)

        cur = snap["current"]
        head, hint, progress = Text(), Text(), Text()
        if snap["finished"]:
            res = snap["result"]
            msg = {"pass": ("All checks passed", "bold green"), "warn": ("Done, with warnings", "bold yellow"),
                   "fail": ("Failed", "bold red"), "aborted": ("Aborted", "bold yellow")}.get(res, (res, "bold"))
            head.append(msg[0], style=msg[1])
            if snap.get("record"):
                arm_id = next(iter(snap["targets"].values()))["arm_id"]
                hint.append(f"Saved as {arm_id} v{snap['record']}", style="bold")
            keys = keys_line(("⏎", "done"), ("h", "history"), ("a", "ask AI"))
        else:
            n = next(i for i, s in enumerate(snap["steps"]) if s["id"] == cur["id"]) + 1
            head.append(f"{n}/{len(snap['steps'])}  ", style="dim")
            head.append(cur["label"], style="bold")
            label, style = BADGE.get(cur["status"], (cur["status"].upper(), "reverse"))
            head.append("  ")
            head.append(f" {label} ", style=style)
            hint.append(cur["hint"], style="bold cyan" if cur["status"] in ("ready", "running") else "bold")
            progress = self._progress(cur, snap)
            can = cur["can"]
            pairs = []
            primary = next((n for n in ("start", "stop", "next", "retry") if can[n]), None)
            if primary:
                pairs.append(("⏎", {"start": "start", "stop": "stop", "next": "accept & continue",
                                    "retry": "retry"}[primary]))
            if can["retry"] and primary != "retry":
                pairs.append(("r", "retry"))
            if can["force"]:
                pairs.append(("f", "accept anyway"))
            if can["skip"]:
                pairs.append(("s", "skip"))
            pairs += [("?", "details"), ("a", "ask AI"), ("esc", "press again to abort" if abort_armed else "abort")]
            keys = keys_line(*pairs)
        self.query_one("#head", Static).update(head)
        self.query_one("#hint", Static).update(hint)
        self.query_one("#progress", Static).update(progress)
        self.query_one("#keys", Static).update(keys)

        details = self.query_one("#details", Static)
        details.display = self.show_details and cur is not None
        if details.display:
            details.update(Text(cur["instructions"], style="dim"))

        # checks of the current step (or of every step once finished): problems first, with the fix
        shown = snap["steps"] if snap["finished"] else [s for s in snap["steps"] if cur and s["id"] == cur["id"]]
        checks = Text()
        for st in shown:
            for c in st["checks"]:
                if c["status"] == "pass" and not snap["finished"]:
                    continue
                if c["status"] == "pass":
                    continue
                icon, style = ICON[c["status"]]
                checks.append(f" {icon} ", style=f"bold {style}")
                checks.append(f"{c['message']}\n")
                if c.get("fix"):
                    checks.append(f"   → {c['fix']}\n", style="dim")
            if not snap["finished"] and st["checks"] and all(c["status"] == "pass" for c in st["checks"]):
                checks.append(" ✓ ", style="bold green")
                checks.append(f"{len(st['checks'])} checks passed\n", style="dim")
        self.query_one("#checks", Static).update(checks)
        self.query_one("#joints", Static).update(self._joints(snap, cur, width))

    def _progress(self, cur: dict, snap: dict) -> Text:
        p = cur.get("progress") or {}
        if "recorded" in p and cur["status"] == "running":
            fills = [r["fill"] for r in p["recorded"].values()]
            return Text("coverage ").append_text(fill_bar(sum(fills) / max(1, len(fills))))
        if "signs" in p and cur["status"] == "running":
            t = Text()
            for j in JOINTS:
                if j in p["signs"]:
                    t.append(f" {j} ✓ ", style="green")
                elif j == p.get("joint"):
                    t.append(f" {j} ", style="bold black on cyan")
                else:
                    t.append(f" {j} ", style="dim")
            return t
        if "elapsed" in p and cur["status"] == "running":
            return Text("time     ").append_text(fill_bar(p["elapsed"] / max(0.1, p["seconds"])))
        return Text()

    def _joints(self, snap: dict, cur: dict | None, width: int) -> Text:
        rec = ((cur or {}).get("progress") or {}).get("recorded", {})
        t = Text()
        for role, arm in snap["arms"].items():
            t.append(f"\n{role}", style="bold")
            t.append(f"  {arm['arm_id']}  {arm['port'] or 'port ?'}", style="dim")
            if not arm["connected"]:
                t.append("  not connected", style="dim italic")
            t.append("\n")
            for j in JOINTS:
                info = arm["joints"][j]
                r = rec.get(j, {})
                t.append(f" {j:<13} ")
                t.append_text(joint_bar(info["raw"], info["cal_min"], info["cal_max"], r.get("min"), r.get("max"),
                                        width))
                raw = "" if info["raw"] is None else str(info["raw"])
                t.append(f" {raw:>4}", style="bold")
                norm = "" if info["norm"] is None else f"{info['norm']:+.0f}"
                t.append(f" {norm:>5}\n", style="dim")
        t.append("\n ━ stored range  ", style=CAL)
        t.append("━ recorded now  ", style=REC)
        t.append("█ position", style="bold")
        return t


# ==================================================================================================
class HistoryScreen(Screen):
    BINDINGS = [
        Binding("enter", "activate", "activate"),
        Binding("d", "diff", "diff"),
        Binding("e", "export", "export"),
        Binding("a", "ask", "ask AI"),
        Binding("escape", "app.pop_screen", "back"),
    ]

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str, int]] = []
        self.confirm: tuple[str, int] | None = None

    def compose(self) -> ComposeResult:
        yield Static(Text("History", style="bold"), id="head")
        yield DataTable(id="versions", cursor_type="row", zebra_stripes=True)
        yield Static(id="info")
        yield AIPanel()
        yield Static(keys_line(("⏎", "make active"), ("d", "diff vs active"), ("e", "export"), ("a", "ask AI"),
                               ("esc", "back")), classes="keys")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("arm", "v", "created", "source", "checks", "note")
        self.reload()
        table.focus()

    def reload(self) -> None:
        store = self.app.runner.env.store
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        self.rows = []
        for info in store.arms():
            for rec in reversed(store.versions(info["arm_id"])):
                d = rec.summary()
                active = info.get("active") == rec.version
                ck = d["checks"]
                checks = Text(f"{ck['pass']}✓", style="green")
                if ck["warn"]:
                    checks.append(f" {ck['warn']}!", style="yellow")
                if ck["fail"]:
                    checks.append(f" {ck['fail']}✗", style="red")
                table.add_row(Text(f"{'★ ' if active else '  '}{info['arm_id']}", style="bold" if active else ""),
                              f"v{rec.version}", f"{d['created'][:16].replace('T', ' ')}  ({ago(d['created'])})",
                              d.get("source", ""), checks, d.get("note", ""))
                self.rows.append((info["arm_id"], rec.version))
        if not self.rows:
            self.query_one("#info", Static).update(Text("No calibrations yet.", style="dim"))
        elif row is not None:
            table.move_cursor(row=min(row, len(self.rows) - 1))

    def _selected(self) -> tuple[str, int] | None:
        table = self.query_one(DataTable)
        return self.rows[table.cursor_row] if self.rows and table.cursor_row is not None else None

    def _info(self, text: Text | str) -> None:
        self.query_one("#info", Static).update(text)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_activate()

    def action_activate(self) -> None:
        sel = self._selected()
        if not sel:
            return
        if self.confirm != sel:
            self.confirm = sel
            self._info(Text(f"Press ⏎ again to make {sel[0]} v{sel[1]} active and export it.", style="yellow"))
            return
        self.confirm = None
        runner = self.app.runner
        res = runner.activate(sel[0], {"version": sel[1]})
        self.reload()
        self._info(Text(f"{sel[0]} v{sel[1]} is active. Exported to {tilde_str(res['exported'])}", style="green"))

    def action_diff(self) -> None:
        sel = self._selected()
        if not sel:
            return
        store = self.app.runner.env.store
        active = (store.arm(sel[0]) or {}).get("active")
        if active is None or active == sel[1]:
            self._info(Text("This is the active version; pick another one to compare.", style="dim"))
            return
        rows = store.diff(sel[0], sel[1], active)
        t = Text(f"{sel[0]}: v{sel[1]} → v{active} (active)\n", style="bold")
        if not rows:
            t.append("identical", style="dim")
        for r in rows:
            delta = "" if r["delta"] is None else f"{r['delta']:+d}"
            t.append(f" {r['joint']:<14}{r['field']:<14}{r['a']!s:>6} → {r['b']!s:<6}")
            t.append(f"{delta:>7}\n", style="yellow" if r["delta"] and abs(r["delta"]) > 100 else "dim")
        self._info(t)

    def action_export(self) -> None:
        sel = self._selected()
        if sel:
            path = self.app.runner.export(sel[0], {"version": sel[1]})["exported"]
            self._info(Text(f"Exported {sel[0]} v{sel[1]} to {tilde_str(path)}", style="green"))

    def action_ask(self) -> None:
        self.query_one(AIPanel).open()


def tilde_str(path: str | None) -> str:
    from pathlib import Path

    return tilde(Path(path)) if path else ""


# ==================================================================================================
class CalApp(App):
    TITLE = "so101-calibrator"
    CSS = """
    Screen { background: $background; }
    #brand { padding: 1 2; }
    #menu { margin: 0 2; height: auto; max-height: 12; border: round $primary-darken-2; }
    #body { height: 1fr; }
    #steps { width: 34; padding: 1 1; border-right: solid $primary-darken-3; }
    #main { padding: 1 2; }
    #head { height: auto; }
    #hint { height: auto; margin: 1 0 0 0; }
    #progress { height: auto; margin: 1 0 0 0; }
    #details { height: auto; margin: 1 0 0 0; }
    #checks { height: auto; margin: 1 0 0 0; }
    #joints { height: auto; }
    AIPanel { height: auto; max-height: 16; border: round $accent; margin: 0 1; padding: 0 1; }
    #ai-answer { height: auto; }
    #ai-input { border: none; height: 1; padding: 0; }
    #versions { height: 1fr; margin: 0 1; }
    #info { height: auto; max-height: 14; padding: 1 2; }
    HistoryScreen #head { padding: 1 2 0 2; }
    .keys { dock: bottom; height: 1; background: $panel; }
    """
    BINDINGS = [Binding("ctrl+c", "quit", "quit", priority=True)]

    def __init__(self, env, ai_spec: str | None = None):
        super().__init__()
        self.runner = Runner(env)
        self.assistant = None
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
        AI.save_spec(self.runner.env.store.root, spec)

    def snapshot(self) -> dict | None:
        with self.runner.lock:
            s = self.runner.session
        return s.snapshot() if s else None

    def start_flow(self, flow: str, role: str | None) -> None:
        body = {"flow": flow, "role": role}
        self.runner.new_session(body)
        title = {"calibrate": f"Calibrate {role}", "verify": f"Verify {role}", "teleop": "Teleop test"}[flow]
        self.push_screen(RunScreen(title))

    def end_flow(self) -> None:
        self.runner.action({"action": "close"})
        self.pop_screen()

    def on_unmount(self) -> None:
        self.runner.shutdown()


def run(env, ai_spec: str | None = None) -> int:
    CalApp(env, ai_spec).run()
    return 0
