# Changelog

## 0.1.1

- Shorter README with a hero image and badges, so the PyPI page matches GitHub. No code changes.

## 0.1.0

First release.

- Terminal UI (`so101-cal`): one screen per step with a pixel-art SO-101 (made from the official MuJoCo
  model, only the joint to move in orange), one sentence and Enter for the obvious action; `j` shows
  live joint bars. Black / grey / orange theme. English and Turkish (`l`).
- Checked calibration of the leader and follower: USB port by unplugging (remembered by USB serial),
  motor scan, supply voltage, middle pose, ranges of motion, joint directions, save. Every check comes
  with a fix.
- Verify (EEPROM vs. active version, middle pose) and a leader → follower teleop test with tracking
  error.
- Versioned history: show, diff, activate (rollback), export to the LeRobot cache or a
  `calibration_dir`, import existing LeRobot files, write a version back to the motors (after checking
  the arm on the port is the right one).
- Optional assistant: local models (Ollama, OpenAI-compatible servers) as a Strands agent with read-only
  history tools, or Claude through the Anthropic SDK.
- Plain CLI commands and a local web dashboard over the same engine.
- Simulated leader + follower with fault injection; the whole test suite runs on it.
- Safety: follower torque is released on every exit path (abort, retry, bus errors, a lost arm
  mid-teleop, Ctrl+C, SIGTERM/SIGHUP); actions are pinned to the step they were meant for; the
  dashboard refuses cross-site requests.
