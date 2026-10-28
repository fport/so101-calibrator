import json
from pathlib import Path

from so101_calibrator.cli import main

from .test_encoding_and_calibration import SAMPLE


def run(capsys, *argv):
    code = main(["--mock", "--mock-speed", "20", *argv])
    return code, capsys.readouterr().out


def test_calibrate_verify_history_diff_activate(capsys, tmp_path):
    code, out = run(capsys, "calibrate", "follower", "--yes", "--note", "first")
    assert code == 0, out
    assert "Result: PASS" in out and "Saved as follower v1" in out
    # motor state persists between invocations, so verify passes
    code, out = run(capsys, "verify", "follower", "--yes")
    assert code == 0, out
    code, out = run(capsys, "calibrate", "follower", "--yes")
    assert code == 0, out
    code, out = run(capsys, "history")
    assert "v1" in out and "v2   * active" in out and "first" in out
    code, out = run(capsys, "diff", "follower", "1", "2")
    assert code == 0 and "follower: v1 → v2" in out
    code, out = run(capsys, "activate", "follower", "1")
    assert code == 0 and "v1 is now active" in out
    # --mock never exports into the real LeRobot cache
    assert not (tmp_path / "lerobot").exists()
    exported = tmp_path / "store/mock-lerobot/robots/so_follower/follower.json"
    assert json.loads(exported.read_text()) == json.loads(run(capsys, "show", "follower", "1", "--json")[1])[
        "calibration"]
    code, out = run(capsys, "write", "follower")
    assert code == 0 and "matches" in out


def test_teleop_test_cli(capsys):
    assert run(capsys, "calibrate", "leader", "--yes")[0] == 0
    assert run(capsys, "calibrate", "follower", "--yes")[0] == 0
    code, out = run(capsys, "teleop-test", "--seconds", "2", "--yes")
    assert code == 0, out
    assert "Follower tracked the leader" in out


def test_failure_exit_code_and_fix_text(capsys):
    code, out = run(capsys, "--mock-faults", "missing:4", "calibrate", "follower", "--yes")
    assert code == 1
    assert "Missing motor id(s): 4 (wrist_flex)" in out and "→" in out


def test_import_export_where(capsys, tmp_path):
    src = tmp_path / "leader.json"
    src.write_text(json.dumps(SAMPLE))
    code, out = run(capsys, "import", str(src), "--role", "leader", "--id", "old")
    assert code == 0 and "old v1" in out
    code, out = run(capsys, "export", "old", "--calibration-dir", str(tmp_path / "hw"))
    assert (tmp_path / "hw/old.json").exists()
    code, out = run(capsys, "where", "leader", "--id", "old")
    assert Path(out.strip()).parts[-3:] == ("teleoperators", "so_leader", "old.json")
    code, out = run(capsys, "show", "nope")
    assert code == 2


def test_find_port_and_ports(capsys):
    code, out = run(capsys, "ports")
    assert "/dev/mock-follower" in out
    code, out = run(capsys, "find-port")
    assert code == 0 and "/dev/mock-follower" in out
