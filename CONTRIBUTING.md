# Contributing

Thanks for helping! Bug reports from real arms are especially valuable: they are how the check
thresholds get better.

## Development setup

```bash
git clone https://github.com/<you>/so101-calibrator && cd so101-calibrator
uv sync                       # core + dev tools (pytest, ruff)
uv sync --extra hardware      # only if you have motors attached
uv run pytest -q              # everything runs against the mock bus, no hardware needed
uv run ruff check .
uv run so101-cal --mock dashboard   # try the UI with a simulated leader + follower
```

## How the code is laid out

| Module | What it does |
| --- | --- |
| `profiles.py` | SO-101 facts: joints, motor ids, supply voltages, check thresholds |
| `bus.py` | `MotorBus` protocol, `FeetechBus` (real serial, `scservo_sdk`), `Arm` (homing, ranges, calibration I/O) |
| `mock.py` | `MockBus` with STS3215 register semantics, `MockArm`/`MockHand`/`MockActor` that play the human, fault injection |
| `checks.py` | Pure pass/warn/fail check functions, each with a `fix` text |
| `wizard.py` | `Session` + `Step`s: the flows (`calibrate`, `verify`, `teleop`) shared by CLI and dashboard |
| `store.py` | Versioned JSON history, diff, activate (rollback), export to the LeRobot cache |
| `cli.py`, `render.py` | Terminal front end |
| `dashboard/` | Standard-library HTTP server + a framework-free static page |

The rule of thumb: logic lives in `wizard.py`/`checks.py`, never in a front end. If the CLI and the
dashboard need the same thing, it belongs in the `Session` snapshot.

## Adding or tuning a check

1. Add a function in `checks.py` that returns `Check`s. Every non-pass check needs a `fix` that tells
   the user what to physically do.
2. Call it from the relevant `Step` in `wizard.py`.
3. Add a unit test in `tests/test_checks.py` and, if a fault can be simulated, a mock fault in
   `mock.Faults` plus an end-to-end test in `tests/test_wizard.py`.

If you change a threshold in `profiles.py`, please say in the PR which arm(s) and readings motivated it.

## Supporting another arm

The tool is SO-101 specific today, but everything arm-specific is in `profiles.py` (joints, ids,
voltages, spans) and the `PROFILES` table. A new arm with Feetech STS motors is mostly a new profile;
other motor families need a new `MotorBus` implementation.

## Pull requests

- Keep PRs focused; include tests (mock-based) for behaviour changes.
- `uv run pytest -q` and `uv run ruff check .` must pass (CI runs both on Linux, macOS and Windows).
- If you tested on real hardware, mention which arm, motor variant and supply.

## Reporting a bug

Please include `so101-cal --version`, OS, the output of the failing step, and if possible
`so101-cal show <arm-id> --json` for the affected version.
