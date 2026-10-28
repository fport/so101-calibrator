import json

import pytest

from so101_calibrator import calibration as C
from so101_calibrator.store import StoreError, lerobot_calibration_path

from .test_encoding_and_calibration import SAMPLE


def cal(**changes):
    c = C.from_json_dict(SAMPLE)
    for joint, (field, value) in changes.items():
        setattr(c[joint], field, value)
    return c


def test_versions_activate_and_diff(store):
    v1 = store.add("follower", "follower", cal(), note="first", usb_serial="5AB0")
    v2 = store.add("follower", "follower", cal(gripper=("range_max", 3400)), note="second")
    assert (v1.version, v2.version) == (1, 2)
    assert store.arm("follower")["active"] == 2
    assert store.arm("follower")["usb_serial"] == "5AB0"
    assert store.get("follower").data["parent"] == 1
    assert store.diff("follower", 1, 2) == [
        {"joint": "gripper", "field": "range_max", "a": 3443, "b": 3400, "delta": -43}]
    store.activate("follower", 1)
    assert store.get("follower").version == 1
    assert [a["arm_id"] for a in store.arms()] == ["follower"]


def test_role_mismatch_and_bad_ids(store):
    store.add("arm1", "follower", cal())
    with pytest.raises(StoreError):
        store.add("arm1", "leader", cal())
    with pytest.raises(StoreError):
        store.add("../evil", "follower", cal())
    with pytest.raises(StoreError):
        store.get("arm1", 7)


def test_inactive_version_is_kept(store):
    store.add("f", "follower", cal())
    store.add("f", "follower", cal(gripper=("range_max", 3000)), activate=False)
    assert store.get("f").version == 1
    assert len(store.versions("f")) == 2


def test_export_to_lerobot_cache_and_backup(store, tmp_path):
    store.add("my_follower", "follower", cal())
    path = store.export("my_follower")
    assert path == tmp_path / "lerobot" / "robots" / "so_follower" / "my_follower.json"
    assert C.load(path) == cal()
    # a foreign file at the target is backed up, not lost
    path.write_text(json.dumps(C.to_json_dict(cal(gripper=("range_min", 2000))), indent=4))
    store.export("my_follower")
    assert len(list(path.parent.glob("my_follower.json.bak-*"))) == 1
    assert C.load(path) == cal()


def test_export_custom_dir_and_legacy(store, tmp_path):
    store.add("leader", "leader", cal())
    assert store.export("leader", dest=tmp_path / "hw").name == "leader.json"
    assert "so101_leader" in str(store.export("leader", legacy=True))
    assert lerobot_calibration_path("leader", "x").parts[-3:] == ("teleoperators", "so_leader", "x.json")


def test_lerobot_path_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_LEROBOT_CALIBRATION")
    monkeypatch.setenv("HF_LEROBOT_HOME", str(tmp_path / "home"))
    assert lerobot_calibration_path("follower", "a") == tmp_path / "home/calibration/robots/so_follower/a.json"
    monkeypatch.delenv("HF_LEROBOT_HOME")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    assert lerobot_calibration_path("follower", "a") == tmp_path / "hf/lerobot/calibration/robots/so_follower/a.json"


def test_import(store, tmp_path):
    src = tmp_path / "leader.json"
    src.write_text(json.dumps(SAMPLE))
    rec = store.import_file(src, "old_leader", "leader")
    assert rec.data["source"] == "import" and rec.calibration == cal()
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({k: v for k, v in SAMPLE.items() if k != "gripper"}))
    with pytest.raises(StoreError):
        store.import_file(broken, "x", "leader")
