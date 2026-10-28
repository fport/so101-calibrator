"""Plain-text rendering shared by the CLI (live joint bars, tables)."""

from __future__ import annotations

from .calibration import Calibration
from .profiles import JOINTS, MAX_POS

WIDTH = 36


def bar(raw: int | None, lo: int | None, hi: int | None, rec_lo: int | None = None, rec_hi: int | None = None) -> str:
    """0..4095 track: '·' outside the calibrated range, '─' inside it, '█' recorded so far, '●' now."""
    cells = []
    for i in range(WIDTH):
        a, b = i * (MAX_POS + 1) / WIDTH, (i + 1) * (MAX_POS + 1) / WIDTH
        ch = "·"
        if lo is not None and hi is not None and b > lo and a <= hi:
            ch = "─"
        if rec_lo is not None and rec_hi is not None and b > rec_lo and a <= rec_hi:
            ch = "█"
        if raw is not None and a <= raw < b:
            ch = "●"
        cells.append(ch)
    return "".join(cells)


def live_lines(snap: dict) -> list[str]:
    step = snap.get("current") or {}
    prog = step.get("progress") or {}
    recorded = prog.get("recorded", {})
    lines = []
    for role, arm in snap["arms"].items():
        if not arm["connected"]:
            continue
        if len(snap["arms"]) > 1:
            lines.append(f"  {role}")
        for j in JOINTS:
            d = arm["joints"][j]
            r = recorded.get(j, {})
            raw = d["raw"]
            norm = "" if d["norm"] is None else f"{d['norm']:+7.1f}"
            extra = f"  rec {r['min']:>4}..{r['max']:<4} {r['fill']:>4.0%}" if r else ""
            lines.append(f"  {j:<14} {bar(raw, d['cal_min'], d['cal_max'], r.get('min'), r.get('max'))} "
                         f"{'' if raw is None else raw:>5} {norm}{extra}")
    if "prompt" in prog and prog.get("joint"):
        lines.append(f"  → now move {prog['joint']}: {prog['prompt']}")
    if "elapsed" in prog:
        lines.append(f"  teleop {prog['elapsed']:.0f}/{prog['seconds']:.0f} s, {prog['samples']} cycles")
    if "phase" in prog:
        msg = {"unplug": "waiting for you to unplug the USB cable…",
               "replug": f"found {', '.join(prog['removed'])} — plug it back in…",
               "back": "plugged back in"}.get(prog["phase"], prog["phase"])
        lines.append(f"  {msg}")
    return lines


def calibration_table(cal: Calibration) -> str:
    rows = [f"{'joint':<14} {'id':>2} {'offset':>7} {'min':>5} {'max':>5} {'span':>5}  range"]
    for j in JOINTS:
        m = cal[j]
        rows.append(f"{j:<14} {m.id:>2} {m.homing_offset:>7} {m.range_min:>5} {m.range_max:>5} {m.span:>5}  "
                    f"{bar(None, m.range_min, m.range_max)}")
    return "\n".join(rows)


def diff_table(rows: list[dict]) -> str:
    out = [f"{'joint':<14} {'field':<14} {'A':>6} {'B':>6} {'delta':>6}"]
    for r in rows:
        delta = "" if r["delta"] is None else f"{r['delta']:+d}"
        out.append(f"{r['joint']:<14} {r['field']:<14} {r['a']!s:>6} {r['b']!s:>6} {delta:>6}")
    return "\n".join(out)
