"""The three screens: home, a running wizard, calibration history. One thing per screen, centred."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ..i18n import check_text, hint, t
from ..profiles import JOINTS
from ..store import StoreError, tilde
from .widgets import THEME, AIPanel, Sprite, ago, emphasis, joints_text, keys

if TYPE_CHECKING:
    from .app import CalApp

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ABORT_CONFIRM_S = 2.5
FLOWS = (("calibrate", "follower"), ("calibrate", "leader"), ("teleop", "follower"))


class AppScreen(Screen):
    BINDINGS = [Binding("a", "ask", show=False), Binding("l", "language", show=False)]

    @property
    def cal_app(self) -> CalApp:
        return self.app  # type: ignore[return-value]

    def action_ask(self) -> None:
        self.query_one(AIPanel).open()

    def action_language(self) -> None:
        self.cal_app.toggle_language()
        self.redraw()

    def redraw(self) -> None:
        """Re-render everything (after a language change)."""


# ==================================================================================================
class HomeScreen(AppScreen):
    BINDINGS = [
        *AppScreen.BINDINGS,
        Binding("up,k", "move(-1)", show=False), Binding("down,j", "move(1)", show=False),
        Binding("1", "pick(0)", show=False), Binding("2", "pick(1)", show=False), Binding("3", "pick(2)", show=False),
        Binding("enter", "open", show=False), Binding("v", "verify", show=False),
        Binding("h", "history", show=False), Binding("q", "app.quit", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.selected = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="home"):
            yield Sprite("idle", kind="hero", id="hero")
            yield Static(id="title", classes="center")
            yield Static(id="menu", classes="center")
            with Center():
                yield AIPanel()
            yield Static(id="keys", classes="center")

    def on_mount(self) -> None:
        self.redraw()

    def on_screen_resume(self) -> None:
        self.redraw()

    def _status(self, arm_id: str) -> str:
        store = self.cal_app.env.store
        try:
            info, rec = store.arm(arm_id), store.active(arm_id)
        except StoreError:
            return ""
        if not info:
            return t("status.none")
        return t("status.active", v=rec.version, ago=ago(rec.data["created"])) if rec else t("status.inactive")

    def redraw(self) -> None:
        title = Text(t("app.title"), style=f"bold {THEME.fg}", justify="center")
        if self.cal_app.env.mock:
            title.append("  MOCK", style=f"bold {THEME.accent}")
        self.query_one("#title", Static).update(title)
        items = [(t("menu.follower"), self._status("follower") or t("menu.follower.sub")),
                 (t("menu.leader"), self._status("leader") or t("menu.leader.sub")),
                 (t("menu.teleop"), t("menu.teleop.sub"))]
        menu = Text(justify="center")
        for i, (label, sub) in enumerate(items):
            row = f"  {'▶' if i == self.selected else ' '}  {label:<20}{sub:>20}  "
            menu.append(row, style=f"bold {THEME.bg} on {THEME.accent}" if i == self.selected else THEME.fg)
            menu.append("\n\n")
        self.query_one("#menu", Static).update(menu)
        self.query_one("#keys", Static).update(keys(
            ("⏎", t("key.start")), ("↑↓", t("key.select")), ("v", t("key.verify")), ("h", t("key.history")),
            ("a", t("key.ask")), ("l", t("key.lang")), ("q", t("key.quit"))))

    def action_move(self, delta: int) -> None:
        self.selected = (self.selected + delta) % len(FLOWS)
        self.redraw()

    def action_pick(self, i: int) -> None:
        self.selected = i
        self.action_open()

    def action_open(self) -> None:
        flow, role = FLOWS[self.selected]
        self.cal_app.start_flow(flow, role)

    def action_verify(self) -> None:
        self.cal_app.start_flow("verify", FLOWS[self.selected][1])

    def action_history(self) -> None:
        self.app.push_screen(HistoryScreen())


# ==================================================================================================
class RunScreen(AppScreen):
    BINDINGS = [
        *AppScreen.BINDINGS,
        Binding("enter", "primary", show=False),
        Binding("r", "act('retry')", show=False),
        Binding("f", "act('force')", show=False),
        Binding("j", "toggle('joints')", show=False),
        Binding("question_mark", "toggle('details')", show=False),
        Binding("h", "history", show=False),
        Binding("escape", "back", show=False),
    ]

    def __init__(self, flow: str, role: str):
        super().__init__()
        self.flow, self.role = flow, role
        self.abort_armed_at = 0.0
        self.shown = {"joints": False, "details": False}
        self._last: tuple | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="run"):
            yield Static(id="progress-dots", classes="center")
            yield Sprite(id="art")
            yield Static(id="hint", classes="center")
            yield Static(id="sub", classes="center")
            yield Static(id="meter", classes="center")
            yield Static(id="checks", classes="center")
            yield Static(id="details", classes="center")
            yield Static(id="joints", classes="center")
            with Center():
                yield AIPanel()
            yield Static(id="keys", classes="center")

    def on_mount(self) -> None:
        self.set_interval(1 / 15, self.render_state)
        self.render_state()

    def redraw(self) -> None:
        self._last = None
        self.render_state()

    # --- actions ------------------------------------------------------------------------------------
    def action_primary(self) -> None:
        snap = self.cal_app.controller.snapshot()
        if snap is None or snap["finished"]:
            self.cal_app.end_flow()
        elif snap["current"]["primary"]:
            self.action_act(snap["current"]["primary"])

    def action_act(self, name: str) -> None:
        controller = self.cal_app.controller
        snap = controller.snapshot()
        if snap is None or snap["finished"] or controller.busy or not snap["current"]["can"].get(name):
            return
        step_id = snap["current"]["id"]  # pins the action to the step on screen: a repeated key cannot hit the next
        self.run_worker(lambda: controller.act(name, step_id), thread=True, group="action")

    def action_toggle(self, what: str) -> None:
        self.shown[what] = not self.shown[what]
        self.redraw()

    def action_history(self) -> None:
        snap = self.cal_app.controller.snapshot()
        if snap is None or snap["finished"]:
            self.app.push_screen(HistoryScreen())

    def action_back(self) -> None:
        snap = self.cal_app.controller.snapshot()
        if snap is None or snap["finished"] or time.monotonic() - self.abort_armed_at < ABORT_CONFIRM_S:
            self.cal_app.end_flow()
        else:
            self.abort_armed_at = time.monotonic()

    # --- rendering ----------------------------------------------------------------------------------
    def render_state(self) -> None:
        controller = self.cal_app.controller
        snap, busy = controller.snapshot(), controller.busy
        if snap is None:
            return
        armed = time.monotonic() - self.abort_armed_at < ABORT_CONFIRM_S
        spin = SPINNER[int(time.monotonic() * 10) % len(SPINNER)] if busy else ""
        width = max(30, min(90, self.size.width - 30))
        key = (repr(snap), armed, spin, width, tuple(self.shown.values()))
        if key == self._last:
            return
        self._last = key
        if snap["finished"]:
            self._render_result(snap)
        else:
            self._render_step(snap, busy, spin, armed)
        joints = self.query_one("#joints", Static)
        joints.display = self.shown["joints"]
        if joints.display:
            joints.update(joints_text(snap, width))

    def _dots(self, snap: dict, title: str) -> Text:
        out = Text(justify="center")
        cur = snap["current"]["id"] if snap["current"] else None
        for i, st in enumerate(snap["steps"]):
            if i:
                out.append("  ")
            if st["id"] == cur:
                out.append("◉", style=f"bold {THEME.accent}")
            elif st["status"] == "fail":
                out.append("✗", style=THEME.error)
            elif st["status"] in ("pass", "warn"):
                out.append("●", style=THEME.dim)
            else:
                out.append("○", style=THEME.dim)
        out.append(f"\n\n{title}", style=f"bold {THEME.fg}")
        return out

    def _render_step(self, snap: dict, busy: str | None, spin: str, armed: bool) -> None:
        cur = snap["current"]
        n = next(i for i, s in enumerate(snap["steps"]) if s["id"] == cur["id"]) + 1
        title = t(f"step.{cur['kind']}") + (f" · {cur['role']}" if snap["flow"] == "teleop" else "")
        dots = self._dots(snap, title)
        dots.append(f"   {n}/{len(snap['steps'])}", style=THEME.dim)
        self.query_one("#progress-dots", Static).update(dots)
        self.query_one("#art", Sprite).show(cur["illustration"])

        if busy:
            line = Text(f"{spin} {t('working')}", style=f"bold {THEME.dim}", justify="center")
        else:
            line = emphasis(hint(cur["hint_key"], cur["hint_args"]), f"bold {THEME.fg}")
            line.justify = "center"
        self.query_one("#hint", Static).update(line)
        self.query_one("#sub", Static).update(Text(t(f"sub.{cur['kind']}"), style=THEME.dim, justify="center"))
        self.query_one("#meter", Static).update(self._meter(cur))
        self.query_one("#checks", Static).update(self._checks([s for s in snap["steps"] if s["id"] == cur["id"]]))
        details = self.query_one("#details", Static)
        details.display = self.shown["details"]
        details.update(Text(cur["instructions"], style=THEME.dim, justify="center"))

        pairs = []
        if cur["primary"]:
            pairs.append(("⏎", t(f"key.{cur['primary']}")))
        if cur["can"]["retry"] and cur["primary"] != "retry":
            pairs.append(("r", t("key.retry")))
        if cur["can"]["force"]:
            pairs.append(("f", t("key.force")))
        pairs += [("j", t("key.joints")), ("a", t("key.ask")), ("esc", t("key.abort_again" if armed else "key.abort"))]
        self.query_one("#keys", Static).update(keys(*pairs, first_is_primary=bool(cur["primary"])))

    def _render_result(self, snap: dict) -> None:
        result = snap["result"]
        ok = result in ("pass", "warn")
        self.query_one("#progress-dots", Static).update(self._dots(snap, ""))
        self.query_one("#art", Sprite).show("middle" if ok else None)
        colour = THEME.accent if ok else THEME.error
        self.query_one("#hint", Static).update(Text(t(f"result.{result}"), style=f"bold {colour}", justify="center"))
        sub = Text(justify="center")
        if snap["record"]:
            arm_id = next(iter(snap["targets"].values()))["arm_id"]
            sub.append(t("result.saved", arm=arm_id, v=snap["record"]), style=THEME.fg)
        for notice in snap["notices"]:
            sub.append(f"\n{notice}", style=THEME.accent)
        self.query_one("#sub", Static).update(sub)
        self.query_one("#meter", Static).update("")
        self.query_one("#checks", Static).update(self._checks(snap["steps"], summary=False))
        self.query_one("#details", Static).display = False
        self.query_one("#keys", Static).update(keys(("⏎", t("key.done")), ("h", t("key.history")),
                                                    ("a", t("key.ask"))))

    @staticmethod
    def _meter(cur: dict) -> Text:
        p = cur.get("progress") or {}
        out = Text(justify="center")
        if cur["status"] != "running":
            return out

        def bar(frac: float, label: str) -> None:
            n = round(max(0.0, min(1.0, frac)) * 26)
            out.append("█" * n, style=THEME.accent)
            out.append("█" * (26 - n), style=THEME.surface)
            out.append(f"  {frac * 100:3.0f}%", style=THEME.fg)

        if "recorded" in p:
            fills = [r["fill"] for r in p["recorded"].values()]
            bar(sum(fills) / max(1, len(fills)), t("coverage"))
        elif "signs" in p:
            for j in JOINTS:
                mark, style = ("●", THEME.accent) if j in p["signs"] else (
                    ("◉", f"bold {THEME.accent}") if j == p.get("joint") else ("○", THEME.dim))
                out.append(f"{mark} ", style=style)
        elif "elapsed" in p:
            bar(p["elapsed"] / max(0.1, p["seconds"]), t("time"))
        return out

    @staticmethod
    def _checks(steps: list[dict], summary: bool = True) -> Text:
        """Problems only, each with its fix; a quiet 'n checks passed' otherwise."""
        out = Text(justify="center")
        for st in steps:
            bad = [c for c in st["checks"] if c["status"] != "pass"]
            for c in bad:
                icon, colour = ("✗", THEME.error) if c["status"] == "fail" else ("!", THEME.accent)
                out.append(f"{icon} ", style=f"bold {colour}")
                message, fix = check_text(c)
                out.append(f"{message}\n", style=THEME.fg)
                if fix:
                    out.append(f"{fix}\n", style=THEME.dim)
            if summary and st["checks"] and not bad:
                out.append(f"✓ {t('checks.passed', n=len(st['checks']))}", style=THEME.dim)
        return out


# ==================================================================================================
class HistoryScreen(AppScreen):
    BINDINGS = [
        *AppScreen.BINDINGS,
        Binding("enter", "activate", show=False), Binding("d", "diff", show=False),
        Binding("e", "export", show=False), Binding("escape", "app.pop_screen", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str, int]] = []
        self.confirm: tuple[str, int] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="history"):
            yield Static(id="history-title", classes="center")
            with Center():
                yield DataTable(id="versions", cursor_type="row", zebra_stripes=False)
            yield Static(id="info", classes="center")
            with Center():
                yield AIPanel()
            yield Static(id="keys", classes="center")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("", "", "", "", "")
        self.redraw()
        self.query_one(DataTable).focus()

    def redraw(self) -> None:
        self.query_one("#history-title", Static).update(Text(t("history.title"), style=f"bold {THEME.fg}",
                                                             justify="center"))
        self.query_one("#keys", Static).update(keys(("⏎", t("key.activate")), ("d", t("key.diff")),
                                                    ("e", t("key.export")), ("esc", t("key.back"))))
        self.reload()

    def _info(self, text: Text) -> None:
        text.justify = "center"
        self.query_one("#info", Static).update(text)

    def reload(self) -> None:
        store = self.cal_app.env.store
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        self.rows = []
        try:
            for info in store.arms():
                for rec in reversed(store.versions(info["arm_id"])):
                    d = rec.summary()
                    active = info.get("active") == rec.version
                    ck = d["checks"]
                    checks = Text(f"{ck['pass']}✓", style=THEME.dim)
                    if ck["warn"]:
                        checks.append(f" {ck['warn']}!", style=THEME.accent)
                    if ck["fail"]:
                        checks.append(f" {ck['fail']}✗", style=THEME.error)
                    table.add_row(Text("●" if active else " ", style=THEME.accent),
                                  Text(info["arm_id"], style=f"bold {THEME.fg}" if active else THEME.fg),
                                  Text(f"v{rec.version}", style=THEME.accent if active else THEME.fg),
                                  Text(ago(d["created"]), style=THEME.dim), checks)
                    self.rows.append((info["arm_id"], rec.version))
        except StoreError as e:
            self._info(Text(str(e), style=THEME.error))
            return
        if not self.rows:
            self._info(Text(t("history.empty"), style=THEME.dim))
        elif row is not None:
            table.move_cursor(row=min(row, len(self.rows) - 1))

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
            self._info(Text(t("history.confirm", arm=sel[0], v=sel[1]), style=THEME.accent))
            return
        self.confirm = None
        store = self.cal_app.env.store
        try:
            store.activate(*sel)
            store.export(*sel)
        except (StoreError, OSError) as e:
            self._info(Text(str(e), style=THEME.error))
            return
        self.reload()
        self._info(Text(t("history.activated", arm=sel[0], v=sel[1]), style=THEME.fg))

    def action_diff(self) -> None:
        sel = self._selected()
        if not sel:
            return
        store = self.cal_app.env.store
        try:
            active = (store.arm(sel[0]) or {}).get("active")
            if active is None or active == sel[1]:
                self._info(Text(t("history.same"), style=THEME.dim))
                return
            rows = store.diff(sel[0], sel[1], active)
        except StoreError as e:
            self._info(Text(str(e), style=THEME.error))
            return
        out = Text(f"{sel[0]}  v{sel[1]} → v{active}\n", style=f"bold {THEME.fg}")
        for r in rows:
            delta = "" if r["delta"] is None else f"{r['delta']:+d}"
            out.append(f"{r['joint']:<14}{r['field']:<14}{r['a']!s:>6} → {r['b']!s:<6}", style=THEME.dim)
            out.append(f"{delta:>7}\n", style=THEME.accent if r["delta"] and abs(r["delta"]) > 100 else THEME.fg)
        self._info(out)

    def action_export(self) -> None:
        sel = self._selected()
        if not sel:
            return
        try:
            path = self.cal_app.env.store.export(*sel)
        except (StoreError, OSError) as e:
            self._info(Text(str(e), style=THEME.error))
            return
        self._info(Text(t("history.exported", path=tilde(path)), style=THEME.fg))
