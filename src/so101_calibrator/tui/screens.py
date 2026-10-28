"""The three screens: home menu, a running wizard, calibration history."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, OptionList, Static
from textual.widgets.option_list import Option

from ..profiles import JOINTS
from ..store import StoreError, tilde
from .widgets import ICON, AIPanel, Illustration, ago, fill_bar, joints_text, keys_line

if TYPE_CHECKING:
    from .app import CalApp

BADGE = {"ready": ("READY", "black on grey70"), "running": ("RECORDING", "black on dark_orange"),
         "pass": ("PASS", "black on green"), "warn": ("WARN", "black on yellow"), "fail": ("FAIL", "white on red")}
PRIMARY_LABEL = {"start": "start", "stop": "stop", "accept": "accept & continue", "retry": "retry"}
RESULT = {"pass": ("All checks passed", "bold green"), "warn": ("Done, with warnings", "bold yellow"),
          "fail": ("Failed", "bold red"), "aborted": ("Aborted", "bold yellow")}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ABORT_CONFIRM_S = 2.5


class AppScreen(Screen):
    @property
    def cal_app(self) -> CalApp:
        return self.app  # type: ignore[return-value]

    def action_ask(self) -> None:
        self.query_one(AIPanel).open()


# ==================================================================================================
class HomeScreen(AppScreen):
    BINDINGS = [Binding("a", "ask", "ask AI"), Binding("h", "history", "history"), Binding("q", "app.quit", "quit")]

    def compose(self) -> ComposeResult:
        yield Static(id="brand")
        with Horizontal(id="home"):
            yield OptionList(id="menu")
            yield Illustration(id="home-arm")
        yield AIPanel()
        yield Static(keys_line(("⏎", "open"), ("h", "history"), ("a", "ask AI"), ("q", "quit")), classes="keys")

    def on_mount(self) -> None:
        self.query_one(Illustration).show("idle")
        self.refresh_menu()

    def on_screen_resume(self) -> None:
        self.refresh_menu()

    def refresh_menu(self) -> None:
        app = self.cal_app
        env = app.env
        brand = Text("SO-101 Calibrator", style="bold")
        if env.mock:
            brand.append("  ")
            brand.append(" MOCK ", style="bold black on yellow")
        brand.append(f"   {tilde(env.store.root)}", style="dim")
        if app.assistant:
            brand.append(f"   AI: {app.assistant.label}", style="dim")
        self.query_one("#brand", Static).update(brand)

        def status(arm_id: str) -> Text:
            try:
                info, rec = env.store.arm(arm_id), env.store.active(arm_id)
            except StoreError as e:
                return Text(str(e), style="red")
            if not info:
                return Text("not calibrated yet", style="dim")
            if not rec:
                return Text("no active version", style="yellow")
            return Text(f"v{rec.version} active · {ago(rec.data['created'])}", style="dim")

        def item(label: str, sub: Text) -> Text:
            t = Text(f"{label:<20}", style="bold")
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
            Option(item("History", Text("versions, diff, rollback", style="dim")), id="history"),
            Option(item("Quit", Text("")), id="quit"),
        ])
        menu.highlighted = highlighted if highlighted is not None else 0
        menu.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        choice = event.option_id or ""
        if choice == "quit":
            self.app.exit()
        elif choice == "history":
            self.action_history()
        else:
            flow, _, role = choice.partition(":")
            self.cal_app.start_flow(flow, role or "follower")

    def action_history(self) -> None:
        self.app.push_screen(HistoryScreen())


# ==================================================================================================
class RunScreen(AppScreen):
    BINDINGS = [
        Binding("enter", "primary", "do it"),
        Binding("r", "act('retry')", "retry"),
        Binding("f", "act('force')", "accept anyway"),
        Binding("a", "ask", "ask AI"),
        Binding("h", "history", "history"),
        Binding("escape", "back", "back"),
    ]

    def __init__(self, title: str):
        super().__init__()
        self.flow_title = title
        self.abort_armed_at = 0.0
        self._last: tuple | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Static(id="steps")
            with Vertical(id="main"):
                yield Static(id="head")
                with Horizontal(id="stage"):
                    yield Illustration(id="arm")
                    with Vertical(id="guide"):
                        yield Static(id="hint")
                        yield Static(id="instructions")
                        yield Static(id="progress")
                        yield Static(id="checks")
                yield Static(id="joints")
        yield AIPanel()
        yield Static(id="keys", classes="keys")

    def on_mount(self) -> None:
        self.set_interval(1 / 15, self.render_state)
        self.render_state()

    # --- actions ------------------------------------------------------------------------------------
    def action_primary(self) -> None:
        snap = self.cal_app.controller.snapshot()
        if snap is None or snap["finished"]:
            self.cal_app.end_flow()
        elif snap["current"]["primary"]:
            self.action_act(snap["current"]["primary"])

    def action_act(self, name: str) -> None:
        snap = self.cal_app.controller.snapshot()
        if snap is None or snap["finished"] or self.cal_app.controller.busy:
            return
        cur = snap["current"]
        if not cur["can"].get(name):
            return
        controller = self.cal_app.controller
        # the step id pins the action to the step on screen (a repeated key cannot hit the next step)
        self.run_worker(lambda: controller.act(name, cur["id"]), thread=True, group="action")

    def action_history(self) -> None:
        snap = self.cal_app.controller.snapshot()
        if snap is None or snap["finished"]:
            self.app.push_screen(HistoryScreen())

    def action_back(self) -> None:
        snap = self.cal_app.controller.snapshot()
        if snap is None or snap["finished"]:
            self.cal_app.end_flow()
        elif time.monotonic() - self.abort_armed_at < ABORT_CONFIRM_S:
            self.cal_app.end_flow()
        else:
            self.abort_armed_at = time.monotonic()

    # --- rendering ----------------------------------------------------------------------------------
    def render_state(self) -> None:
        controller = self.cal_app.controller
        snap, busy = controller.snapshot(), controller.busy
        if snap is None:
            return
        width = max(20, self.query_one("#main").size.width - 26)
        abort_armed = time.monotonic() - self.abort_armed_at < ABORT_CONFIRM_S
        spin = SPINNER[int(time.monotonic() * 10) % len(SPINNER)] if busy else ""
        key = (repr(snap), width, abort_armed, spin)
        if key == self._last:
            return
        self._last = key

        self.query_one("#steps", Static).update(self._steps(snap))
        cur = snap["current"]
        head, hint, instructions, progress = Text(), Text(), Text(), Text()
        if snap["finished"]:
            text, style = RESULT.get(snap["result"], (snap["result"], "bold"))
            head.append(text, style=style)
            if snap["record"]:
                arm_id = next(iter(snap["targets"].values()))["arm_id"]
                hint.append(f"Saved as {arm_id} v{snap['record']}", style="bold")
            for notice in snap["notices"]:
                instructions.append(f"! {notice}\n", style="yellow")
            self.query_one(Illustration).show("idle" if snap["result"] in ("pass", "warn") else None)
            keys = keys_line(("⏎", "done"), ("h", "history"), ("a", "ask AI"))
        else:
            n = next(i for i, s in enumerate(snap["steps"]) if s["id"] == cur["id"]) + 1
            head.append(f"{n}/{len(snap['steps'])}  ", style="dim")
            head.append(cur["label"], style="bold")
            label, style = BADGE.get(cur["status"], (cur["status"].upper(), "reverse"))
            head.append("  ")
            head.append(f" {label} ", style=style)
            if busy:
                hint.append(f"{spin} working…", style="bold cyan")
            else:
                hint.append(cur["hint"], style="bold cyan" if cur["status"] in ("ready", "running") else "bold")
            instructions.append(cur["instructions"], style="grey62")
            progress = self._progress(cur)
            self.query_one(Illustration).show(cur["illustration"])
            pairs = []
            if cur["primary"]:
                pairs.append(("⏎", PRIMARY_LABEL[cur["primary"]]))
            if cur["can"]["retry"] and cur["primary"] != "retry":
                pairs.append(("r", "retry"))
            if cur["can"]["force"]:
                pairs.append(("f", "accept anyway"))
            pairs += [("a", "ask AI"), ("esc", "press again to abort" if abort_armed else "abort")]
            keys = keys_line(*pairs)
        self.query_one("#head", Static).update(head)
        self.query_one("#hint", Static).update(hint)
        self.query_one("#instructions", Static).update(instructions)
        self.query_one("#progress", Static).update(progress)
        self.query_one("#checks", Static).update(self._checks(snap))
        self.query_one("#keys", Static).update(keys)
        joints = joints_text(snap, width)
        self.query_one("#joints", Static).display = bool(joints.plain)
        self.query_one("#joints", Static).update(joints)

    def _steps(self, snap: dict) -> Text:
        t = Text(f"{self.flow_title}\n\n", style="bold")
        cur_id = snap["current"]["id"] if snap["current"] else None
        for i, st in enumerate(snap["steps"]):
            icon, style = ICON.get(st["status"], ("·", "dim"))
            label = f"{i + 1}. {st['label']}"
            if len(label) > 28:
                label = label[:27] + "…"
            t.append(f" {icon} ", style=style)
            t.append(f"{label}\n", style="bold reverse" if st["id"] == cur_id else "")
        return t

    def _checks(self, snap: dict) -> Text:
        """Problems first, each with its fix; the current step only, or every step once finished."""
        cur = snap["current"]
        shown = snap["steps"] if snap["finished"] else [s for s in snap["steps"] if cur and s["id"] == cur["id"]]
        t = Text()
        for st in shown:
            for c in st["checks"]:
                if c["status"] == "pass":
                    continue
                icon, style = ICON[c["status"]]
                t.append(f"{icon} ", style=f"bold {style}")
                t.append(f"{c['message']}\n")
                if c.get("fix"):
                    t.append(f"  → {c['fix']}\n", style="grey62")
            if not snap["finished"] and st["checks"] and all(c["status"] == "pass" for c in st["checks"]):
                t.append("✓ ", style="bold green")
                t.append(f"{len(st['checks'])} checks passed\n", style="dim")
        return t

    @staticmethod
    def _progress(cur: dict) -> Text:
        p = cur.get("progress") or {}
        if cur["status"] != "running":
            return Text()
        if "recorded" in p:
            fills = [r["fill"] for r in p["recorded"].values()]
            return Text("coverage ").append_text(fill_bar(sum(fills) / max(1, len(fills)), 24))
        if "signs" in p:
            t = Text()
            for j in JOINTS:
                if j in p["signs"]:
                    t.append(f"{j} ✓  ", style="green")
                elif j == p.get("joint"):
                    t.append(f" {j} ", style="bold black on cyan")
                    t.append("  ")
                else:
                    t.append(f"{j}  ", style="dim")
            return t
        if "elapsed" in p:
            return Text("time     ").append_text(fill_bar(p["elapsed"] / max(0.1, p["seconds"]), 24))
        return Text()


# ==================================================================================================
class HistoryScreen(AppScreen):
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
        yield Static(Text("History", style="bold"), id="history-head")
        yield DataTable(id="versions", cursor_type="row", zebra_stripes=True)
        yield Static(id="info")
        yield AIPanel()
        yield Static(keys_line(("⏎", "make active"), ("d", "diff vs active"), ("e", "export"), ("a", "ask AI"),
                               ("esc", "back")), classes="keys")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("arm", "v", "created", "source", "checks", "note")
        self.reload()
        self.query_one(DataTable).focus()

    def _info(self, text: Text) -> None:
        self.query_one("#info", Static).update(text)

    def reload(self) -> None:
        store = self.cal_app.env.store
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        self.rows = []
        try:
            arms = store.arms()
            for info in arms:
                for rec in reversed(store.versions(info["arm_id"])):
                    self._add_row(table, info, rec)
        except StoreError as e:
            self._info(Text(str(e), style="red"))
            return
        if not self.rows:
            self._info(Text("No calibrations yet.", style="dim"))
        elif row is not None:
            table.move_cursor(row=min(row, len(self.rows) - 1))

    def _add_row(self, table: DataTable, info: dict, rec) -> None:
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

    def _selected(self) -> tuple[str, int] | None:
        table = self.query_one(DataTable)
        return self.rows[table.cursor_row] if self.rows and table.cursor_row is not None else None

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
        store = self.cal_app.env.store
        try:
            store.activate(*sel)
            path = store.export(*sel)
        except (StoreError, OSError) as e:
            self._info(Text(str(e), style="red"))
            return
        self.reload()
        self._info(Text(f"{sel[0]} v{sel[1]} is active. Exported to {tilde(path)}.\n"
                        f"The motors keep their values until `so101-cal write {sel[0]}`.", style="green"))

    def action_diff(self) -> None:
        sel = self._selected()
        if not sel:
            return
        store = self.cal_app.env.store
        try:
            active = (store.arm(sel[0]) or {}).get("active")
            if active is None or active == sel[1]:
                self._info(Text("This is the active version; pick another one to compare.", style="dim"))
                return
            rows = store.diff(sel[0], sel[1], active)
        except StoreError as e:
            self._info(Text(str(e), style="red"))
            return
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
        if not sel:
            return
        try:
            path = self.cal_app.env.store.export(*sel)
        except (StoreError, OSError) as e:
            self._info(Text(str(e), style="red"))
            return
        self._info(Text(f"Exported {sel[0]} v{sel[1]} to {tilde(path)}", style="green"))
