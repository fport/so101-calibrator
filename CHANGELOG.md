# Changelog

## 0.1.0 (unreleased)

First release.

- Terminal UI (`so101-cal`): a wizard with an animated SO-101 for every step (rendered from the
  official MuJoCo model, the joint to move highlighted), live joint bars, and Enter for the obvious
  action.
- Checked calibration of the leader and follower: USB port by unplugging (remembered by USB serial),
  motor scan, supply voltage, middle pose, ranges of motion, joint directions, save. Every check comes
  with a fix.
- Verify (EEPROM vs. active version, middle pose) and a leader → follower teleop test with tracking
  error.
- Versioned history: show, diff, activate (rollback), export to the LeRobot cache or a
  `calibration_dir`, import existing LeRobot files, write a version back to the motors (after checking
  the arm on the port is the right one).
- Optional AI helper: Ollama, any OpenAI-compatible server, or Claude.
- Plain CLI commands and a local web dashboard over the same engine.
- Simulated leader + follower with fault injection; the whole test suite runs on it.
- Safety: follower torque is released on every exit path (abort, retry, bus errors, a lost arm
  mid-teleop, Ctrl+C, SIGTERM/SIGHUP); actions are pinned to the step they were meant for; the
  dashboard refuses cross-site requests.
