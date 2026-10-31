<p align="center">
  <img src="https://raw.githubusercontent.com/fport/so101-calibrator/main/docs/so101/direction_elbow_flex.gif" width="240" alt="Pixel-art SO-101 with the elbow highlighted">
</p>

<h1 align="center">so101-calibrator</h1>

<p align="center"><b>Calibrate your SO-101 arms step by step, with a check at every step.</b><br>
For <a href="https://github.com/huggingface/lerobot">LeRobot</a>. In the terminal. English and Turkish.</p>

<p align="center">
  <a href="https://pypi.org/project/so101-calibrator/"><img src="https://img.shields.io/pypi/v/so101-calibrator?color=ff7a1a&labelColor=1c1c1c&label=pypi" alt="PyPI"></a>
  <a href="https://pypi.org/project/so101-calibrator/"><img src="https://img.shields.io/badge/python-3.10%2B-ededed?labelColor=1c1c1c" alt="Python 3.10+"></a>
  <a href="https://github.com/fport/so101-calibrator/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/fport/so101-calibrator/ci.yml?branch=main&labelColor=1c1c1c&label=tests" alt="Tests"></a>
  <a href="https://github.com/huggingface/lerobot"><img src="https://img.shields.io/badge/LeRobot-compatible-ff7a1a?labelColor=1c1c1c" alt="LeRobot compatible"></a>
  <a href="https://github.com/fport/so101-calibrator/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-ededed?labelColor=1c1c1c" alt="MIT license"></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/fport/so101-calibrator/main/docs/tui-direction.png" width="820" alt="One step of the calibration: move the elbow">
</p>

## Start

```bash
uv tool install so101-calibrator     # or: pipx install so101-calibrator / pip install so101-calibrator
so101-cal --mock                     # try it on a simulated arm
so101-cal                            # then with your arms plugged in
uv tool upgrade so101-calibrator     # later, to update (or: pipx upgrade / pip install -U)
```

Pick **Follower**, then **Leader**, then **Test both arms** (start it with both arms folded). Every screen
shows one thing to do; the joint to move is orange; **Enter** goes on. LeRobot then finds the calibration
with no extra flags.

## Why

`lerobot-calibrate` does the math right but leaves you blind: a motor on the wrong id, a follower on USB
power, a joint swept halfway or a motor mounted backwards only shows up later as a jerky robot. Here every
step is checked, every failure says what to fix, and every calibration is kept as a version.

| | |
| --- | --- |
| ✅ **Checked steps** | USB port, motors 1–6, supply voltage, middle pose, ranges, save |
| 🕘 **Versions** | history, diff, one-key rollback, export to LeRobot |
| 🦾 **Teleop test** | leader → follower; from the folded rest pose it spots a reversed motor before any torque |
| 💬 **Assistant** | optional, a [Strands](https://strandsagents.com) agent on a local model (Ollama) or Claude |
| 🧪 **Mock arm** | try everything without hardware |

<p align="center">
  <img src="https://raw.githubusercontent.com/fport/so101-calibrator/main/docs/tui-home.png" width="400" alt="Home screen">
  &nbsp;
  <img src="https://raw.githubusercontent.com/fport/so101-calibrator/main/docs/tui-ai.png" width="400" alt="The assistant explaining a failed check">
</p>

> [!WARNING]
> The teleop test turns follower torque on. Keep the workspace clear and a hand near the follower: it
> goes limp again when the test ends (and on Ctrl+C, a closed terminal or a pulled cable).

## More

<details>
<summary><b>Keys</b></summary>

| Key | |
| --- | --- |
| `⏎` | start · done · continue · retry |
| `r` / `f` | retry / accept a failed step anyway |
| `j` | live joint bars |
| `?` | the long explanation of a step |
| `a` | ask the assistant |
| `l` | English ↔ Türkçe |
| `h` | history (`⏎` make active, `d` compare, `e` export) |
| `esc` | cancel (press twice) |

</details>

<details>
<summary><b>Commands</b> (scripts, other ids, rollback)</summary>

```bash
so101-cal calibrate follower --id follower --note "new elbow motor"
so101-cal calibrate leader --id leader
so101-cal teleop-test
so101-cal verify follower             # EEPROM still matches, middle pose reads ~2047

so101-cal history                     # all arms and versions
so101-cal diff follower 2 3           # what changed, in encoder ticks
so101-cal activate follower 2         # roll back and export
so101-cal write follower              # push the active version into the motors
so101-cal export follower --calibration-dir hardware/calibration
so101-cal import path/to/follower.json --role follower --id follower
```

Then, as usual:

```bash
lerobot-teleoperate --robot.type=so101_follower --robot.port=... --robot.id=follower \
  --teleop.type=so101_leader --teleop.port=... --teleop.id=leader
```

</details>

<details>
<summary><b>Assistant</b></summary>

```bash
uv tool install "so101-calibrator[ai]"
so101-cal ai use ollama               # local model (or ollama:qwen2.5, ...)
so101-cal ai use openai:my-model      # LM Studio / llama.cpp / vLLM: URL in $SO101CAL_AI_URL
so101-cal ai use claude               # ANTHROPIC_API_KEY
```

Press `a` in the app and ask, or press Enter on an empty line to have the current problem explained. Every
model runs as a Strands agent that can also read the calibration history and compare versions. It only
explains: none of its tools can move the arm.

</details>

<details>
<summary><b>Try it without hardware</b></summary>

```bash
so101-cal --mock --mock-faults voltage:5         # see a failing check
so101-cal --mock dashboard                       # the web version, http://127.0.0.1:8101
```

Faults: `missing:<id>`, `voltage:<V>`, `reversed:<joint>`, `stuck:<joint>`, `hot:<joint>` (prefix `leader.` for the leader).

</details>

<details>
<summary><b>Where things are stored</b></summary>

History: `~/.so101-calibrator/<arm>/v0001.json …` (plain JSON; `--store` or `$SO101CAL_HOME` to move it).
Exports: the LeRobot cache, `~/.cache/huggingface/lerobot/calibration/robots/so_follower/<id>.json` and
`teleoperators/so_leader/<id>.json` (or `$HF_LEROBOT_CALIBRATION`). A different existing file is kept as a
`.bak`.

</details>

<details>
<summary><b>Troubleshooting</b></summary>

| Symptom | Likely cause |
| --- | --- |
| No port disappears when unplugging | Charge-only USB cable, or the CH343 driver is missing (Windows) |
| No motor answers | Power adapter off, or the cable to the first motor |
| Motors answer at another baud rate | Run `lerobot-setup-motors` once |
| Missing motor id 4 | Loose cable going into motor 4 |
| Teleop test: a joint's values are "mirror images" | A motor or horn is mounted the other way round on one arm |
| EEPROM differs | Motor replaced or another tool wrote it: recalibrate or `so101-cal write` |

</details>

<details>
<summary><b>How it relates to LeRobot</b></summary>

Same math and file format as `lerobot-calibrate` for the SO-101 (homing offset = position − 2047, recorded
min/max, `wrist_roll` 0..4095), through Feetech's `scservo_sdk`, so neither LeRobot nor PyTorch is needed.
Built against LeRobot 0.6.1. The command line is the stable interface; the Python API may change before 1.0.

</details>

## Contributing

Reports from real arms are very welcome. See [CONTRIBUTING.md](https://github.com/fport/so101-calibrator/blob/main/CONTRIBUTING.md).

<sub>MIT. The SO-101 pixel art is rendered from the <a href="https://github.com/TheRobotStudio/SO-ARM100">SO-ARM100</a> model (Apache-2.0).</sub>
