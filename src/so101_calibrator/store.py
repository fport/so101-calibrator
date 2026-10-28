"""Versioned calibration store: plain JSON files, one directory per arm, safe to keep in git.

    <store>/<arm_id>/arm.json      role, USB serial, last port, active version
    <store>/<arm_id>/v0001.json    one record per calibration (metadata + checks + LeRobot calibration)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from . import calibration as C
from .profiles import PROFILES

ARM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def default_store() -> Path:
    return Path(os.environ.get("SO101CAL_HOME", Path.home() / ".so101-calibrator")).expanduser()


def lerobot_calibration_root() -> Path:
    """Same resolution order as lerobot.utils.constants.HF_LEROBOT_CALIBRATION."""
    if "HF_LEROBOT_CALIBRATION" in os.environ:
        return Path(os.environ["HF_LEROBOT_CALIBRATION"]).expanduser()
    if "HF_LEROBOT_HOME" in os.environ:
        return Path(os.environ["HF_LEROBOT_HOME"]).expanduser() / "calibration"
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    hf_home = os.environ.get("HF_HOME") or cache / "huggingface"
    return Path(hf_home).expanduser() / "lerobot" / "calibration"


def lerobot_calibration_path(role: str, arm_id: str, legacy: bool = False) -> Path:
    """Where `lerobot-teleoperate --robot.id=<arm_id>` looks when no calibration_dir is given.

    LeRobot >= 0.4 keys the directory by device class (`so_follower`); older releases used the type
    (`so101_follower`). `legacy=True` targets the latter.
    """
    prof = PROFILES[role]
    sub = prof.lerobot_type if legacy else prof.lerobot_class_dir
    return lerobot_calibration_root() / prof.lerobot_kind / sub / f"{arm_id}.json"


class StoreError(RuntimeError):
    pass


@dataclass
class Record:
    data: dict

    @property
    def version(self) -> int:
        return self.data["version"]

    @property
    def calibration(self) -> C.Calibration:
        return C.from_json_dict(self.data["calibration"])

    def summary(self) -> dict:
        d = {k: v for k, v in self.data.items() if k not in ("calibration", "checks")}
        checks = self.data.get("checks", [])
        d["checks"] = {s: sum(c["status"] == s for c in checks) for s in ("pass", "warn", "fail")}
        return d


class Store:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser() if root else default_store()

    # --- arms ---------------------------------------------------------------------------------------
    def _arm_dir(self, arm_id: str) -> Path:
        if not ARM_ID_RE.match(arm_id):
            raise StoreError(f"invalid arm id '{arm_id}' (letters, digits, '_', '-', '.')")
        return self.root / arm_id

    def arm(self, arm_id: str) -> dict | None:
        p = self._arm_dir(arm_id) / "arm.json"
        return json.loads(p.read_text()) if p.exists() else None

    def _write_arm(self, arm_id: str, info: dict) -> None:
        d = self._arm_dir(arm_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "arm.json").write_text(json.dumps(info, indent=2) + "\n")

    def arms(self) -> list[dict]:
        if not self.root.exists():
            return []
        out = []
        for p in sorted(self.root.glob("*/arm.json")):
            info = json.loads(p.read_text())
            info["versions"] = len(list(p.parent.glob("v*.json")))
            out.append(info)
        return out

    # --- versions -----------------------------------------------------------------------------------
    def versions(self, arm_id: str) -> list[Record]:
        d = self._arm_dir(arm_id)
        return [Record(json.loads(p.read_text())) for p in sorted(d.glob("v*.json"))]

    def get(self, arm_id: str, version: int | None = None) -> Record:
        info = self.arm(arm_id)
        if info is None:
            raise StoreError(f"no calibrations stored for '{arm_id}' in {self.root}")
        v = version if version is not None else info.get("active")
        if v is None:
            raise StoreError(f"'{arm_id}' has no active version")
        p = self._arm_dir(arm_id) / f"v{v:04d}.json"
        if not p.exists():
            raise StoreError(f"'{arm_id}' has no version {v}")
        return Record(json.loads(p.read_text()))

    def active(self, arm_id: str) -> Record | None:
        try:
            return self.get(arm_id)
        except StoreError:
            return None

    def add(self, arm_id: str, role: str, cal: C.Calibration, *, note: str = "", port: str = "",
            usb_serial: str = "", checks: list[dict] | None = None, directions: dict | None = None,
            source: str = "wizard", activate: bool = True) -> Record:
        if role not in PROFILES:
            raise StoreError(f"unknown role '{role}'")
        info = self.arm(arm_id) or {"arm_id": arm_id, "role": role, "usb_serial": usb_serial, "active": None}
        if info["role"] != role:
            raise StoreError(f"'{arm_id}' is a {info['role']}, not a {role}")
        existing = self.versions(arm_id)
        version = (existing[-1].version + 1) if existing else 1
        record = {
            "schema": 1,
            "version": version,
            "arm_id": arm_id,
            "role": role,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "port": port,
            "usb_serial": usb_serial or info.get("usb_serial", ""),
            "note": note,
            "source": source,
            "parent": info.get("active"),
            "tool": f"so101-calibrator {__version__}",
            "checks": checks or [],
            "directions": directions or {},
            "calibration": C.to_json_dict(cal),
        }
        d = self._arm_dir(arm_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"v{version:04d}.json").write_text(json.dumps(record, indent=2) + "\n")
        if port:
            info["port"] = port
        if usb_serial:
            info["usb_serial"] = usb_serial
        if activate:
            info["active"] = version
        self._write_arm(arm_id, info)
        return Record(record)

    def activate(self, arm_id: str, version: int) -> Record:
        rec = self.get(arm_id, version)
        info = self.arm(arm_id)
        info["active"] = version
        self._write_arm(arm_id, info)
        return rec

    def set_note(self, arm_id: str, version: int, note: str) -> Record:
        rec = self.get(arm_id, version)
        rec.data["note"] = note
        (self._arm_dir(arm_id) / f"v{version:04d}.json").write_text(json.dumps(rec.data, indent=2) + "\n")
        return rec

    def diff(self, arm_id: str, a: int, b: int | None = None) -> list[dict]:
        return C.diff(self.get(arm_id, a).calibration, self.get(arm_id, b).calibration)

    def latest_directions(self, role: str, exclude_arm: str | None = None) -> tuple[str, dict] | None:
        """Directions of the most recent active calibration of another arm with this role."""
        best = None
        for info in self.arms():
            if info["role"] != role or info["arm_id"] == exclude_arm or not info.get("active"):
                continue
            rec = self.get(info["arm_id"])
            if rec.data.get("directions") and (best is None or rec.data["created"] > best[0]):
                best = (rec.data["created"], info["arm_id"], rec.data["directions"])
        return (best[1], best[2]) if best else None

    # --- import / export ----------------------------------------------------------------------------
    def import_file(self, path: str | Path, arm_id: str, role: str, note: str = "") -> Record:
        cal = C.load(path)
        problems = C.validate_shape(cal)
        if problems:
            raise StoreError(f"{path}: " + "; ".join(problems))
        return self.add(arm_id, role, cal, note=note or f"imported from {path}", source="import")

    def export(self, arm_id: str, version: int | None = None, dest: str | Path | None = None,
               legacy: bool = False) -> Path:
        """Write a version where LeRobot reads it. `dest`: a calibration_dir (file = <dest>/<arm_id>.json).

        An existing file with different content is kept as `<name>.bak-<timestamp>` first.
        """
        rec = self.get(arm_id, version)
        target = (Path(dest).expanduser() / f"{arm_id}.json") if dest else \
            lerobot_calibration_path(rec.data["role"], arm_id, legacy=legacy)
        content = C.dump(rec.calibration)
        if target.exists() and target.read_text() != content:
            try:
                same = C.load(target) == rec.calibration
            except Exception:
                same = False
            if not same:
                shutil.copy2(target, target.with_name(f"{target.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target
