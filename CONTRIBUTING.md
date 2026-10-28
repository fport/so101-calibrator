# Contributing

Thanks for helping! Bug reports from real arms are especially valuable: they are how the check
thresholds get better.

## Development setup

```bash
git clone https://github.com/fport/so101-calibrator && cd so101-calibrator
uv sync --extra ai            # package + dev tools (pytest, ruff) + the Claude SDK
uv run pytest -q              # everything runs against the mock bus, no hardware needed
uv run ruff check .
uv run so101-cal --mock       # the TUI with a simulated leader + follower
```

## How the code is laid out

| Module | What it does |
| --- | --- |
| `profiles.py` | SO-101 facts: joints, motor ids, supply voltages, check thresholds |
| `bus.py` | `MotorBus` protocol, `FeetechBus` (real serial, `scservo_sdk`), `Arm` (homing, ranges, calibration I/O, safe torque release) |
| `mock.py` | `MockBus` with STS3215 register semantics, `MockArm`/`MockHand`/`MockActor` that play the human, fault injection |
| `checks.py` | Pure pass/warn/fail check functions, each with a `fix` text |
| `wizard.py` | `Session` + `Step`s: the flows (`calibrate`, `verify`, `teleop`); `snapshot()` is what every front end draws |
| `store.py` | Versioned JSON history (atomic writes), diff, activate (rollback), export to the LeRobot cache |
| `env.py` | Real hardware or the mock, port lookup by USB serial |
| `controller.py` | Runs a session on a poll thread for interactive front ends; cached snapshot, never blocks the UI |
| `tui/` | Textual app: `screens.py` (home, wizard, history), `widgets.py` (sprite, assistant panel, joint bars) |
| `theme.py`, `i18n.py`, `settings.py` | Colours, English/Turkish texts, saved preferences |
| `console.py`, `cli.py`, `render.py` | Line-based wizard and the commands |
| `dashboard/` | Standard-library HTTP server + a framework-free static page |
| `ai.py` | Optional assistant: Strands agent (Ollama / OpenAI-compatible) with read-only tools, or Claude |
| `assets/` | The SO-101 pixel-art clips (role sheets), made by `tools/render_illustrations.py` |

Rules of thumb:

- Logic lives in `wizard.py`/`checks.py`, never in a front end. If two front ends need the same thing,
  it belongs in the `Session` snapshot (`current.primary` is the action Enter / the main button does).
- Hardware safety is the engine's job: a step's `abort()` must make its hardware safe and must not
  raise, and every exit path of a session ends in `Session.close()`, which releases torque.
- Front ends send actions with the step id they displayed (`Session.act(action, step_id)`).

## Re-rendering the SO-101 sprites

The clips in `src/so101_calibrator/assets/so101/` are made by `tools/render_illustrations.py` from the
[SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100) MuJoCo model:

```bash
pip install mujoco numpy pillow
python tools/render_illustrations.py /path/to/SO-ARM100/Simulation/SO101/so101_new_calib.xml [--only ranges]
```

MuJoCo's segmentation render tells which link and which mesh (printed part or motor) each pixel shows;
each block is reduced to its majority role, then a dot pattern, separators and a 1-pixel outline are
added. The PNG sheets store roles (`assets.ROLES`), not colours: `theme.py` colours them at run time, so
a new theme needs no re-render. The script also checks with forward kinematics that every direction clip
moves the way `profiles.DIRECTION_PROMPTS` describes, and writes preview GIFs to `docs/so101/`.

## Texts and languages

UI texts and step hints live in `i18n.py` (English and Turkish). Steps return an i18n key from
`hint_spec()`; `*word*` marks the word the TUI draws in the accent colour. A new language is a new table
in `STRINGS`.

## Adding or tuning a check

1. Add a function in `checks.py` that returns `Check`s. Every non-pass check needs a `fix` that tells
   the user what to physically do.
2. Call it from the relevant `Step` in `wizard.py`.
3. Add a unit test in `tests/test_checks.py` and, if a fault can be simulated, a mock fault in
   `mock.Faults` plus an end-to-end test in `tests/test_wizard.py`. Add the fault to `FAULTS` in
   `tests/test_stress.py` too: the stress tests press every button in every state on every fault.

If you change a threshold in `profiles.py`, please say in the PR which arm(s) and readings motivated it.

## Supporting another arm

The tool is SO-101 specific today, but everything arm-specific is in `profiles.py` (joints, ids,
voltages, spans) and the `PROFILES` table. A new arm with Feetech STS motors is mostly a new profile;
other motor families need a new `MotorBus` implementation.

## Pull requests

- Keep PRs focused; include tests (mock-based) for behaviour changes.
- `uv run pytest -q` and `uv run ruff check .` must pass (CI runs both on Linux, macOS and Windows,
  and builds the package and runs it from a clean install).
- If you tested on real hardware, mention which arm, motor variant and supply.

## Reporting a bug

Please include `so101-cal --version`, OS, the output of the failing step, and if possible
`so101-cal show <arm-id> --json` for the affected version.

## Releasing

1. Update `__version__` in `src/so101_calibrator/__init__.py` and `CHANGELOG.md`.
2. Tag and push: `git tag v0.1.0 && git push --tags`. The release workflow runs CI, publishes to PyPI
   through trusted publishing and creates the GitHub release.
