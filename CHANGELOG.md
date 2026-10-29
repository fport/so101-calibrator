# Changelog

## 0.1.5

- **Safety fix for the teleop test (all earlier versions are affected).** wrist_roll was left out of the
  start check, so a leader wrist turned ~200 degrees away from the follower's made the follower spin its
  wrist at full torque, which cracked a follower's wrist housing. Now:
  - wrist_roll must match before torque goes on (only the gripper is left out);
  - the follower starts slowly (~9 deg/s, ramping up over 2 s) and never moves faster than ~90 deg/s,
    measured per second so a faster control loop (30, 60 Hz) does not make it faster;
  - torque is limited during the test (35-70 % per joint) and restored afterwards.
- The middle pose now says how to turn the wrist (moving jaw, on the leader the trigger, on top), and
  calibrating an arm whose wrist was turned differently from the other arm's warns: that difference is
  exactly how far teleoperation would turn the follower's wrist.
- The teleop test stops if the leader's wrist turns past the end of its turn, instead of letting the
  follower spin its wrist the other way round.

- The teleop test no longer stops at eight check screens waiting for Enter: checks that need nothing from
  you (remembered port, motors, voltage, stored calibration) run by themselves and only stop on a problem.
- The gripper no longer has to match at the start: the leader's trigger rests open while the follower's
  gripper rests closed, and the follower's gripper simply follows the trigger once the test runs.

## 0.1.4

- No more "joint directions" step. It asked people to move each joint "up" or "left", which is ambiguous
  (seen from where? from which pose?), gave different answers on every run and blocked saving. A motor
  mounted the other way round is now found objectively: the teleop test starts with both arms folded in
  the rest pose, where most joints lie on a stop, so a reversed one reads from the other end of its range
  ("mirror images") and the test stops before any torque.
- "Accept anyway" saves and activates the calibration; the accepted failures stay in its record. The run
  ends as a warning, not as "Failed".
- No offset warning for wrist_roll: it turns all the way, any horn angle works.
- `activate` only says to write the motors when they may hold another version (the tool now remembers
  which version it wrote).

## 0.1.3

(0.1.2 was tagged but never published: a CI test failed on macOS.)

- Joint directions are only measured from a pose the arm rests in, and only while the shown joint is the
  one moving most. A loose leader wrist swinging back, or the arm bumped while picking it up, no longer
  shows up as a reversed motor.
- A direction mismatch now says it may be a joint moved the wrong way as well as a reversed motor, and is
  shown in Turkish too. The shoulder_pan prompt reads "left, seen from behind" instead of "its left".
- A joint resting against an end stop (it could only be moved one way) is not measured: the hint asks to
  bring it to the middle first. The ranges step says what "the middle" is.

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
