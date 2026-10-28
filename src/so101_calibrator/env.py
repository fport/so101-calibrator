"""Where sessions run: real hardware or the simulated rig, plus the calibration store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bus import MotorBus
from .ports import list_ports
from .store import Store
from .wizard import Context, Target


class Env:
    """Opens buses and lists ports on real hardware, or on a persistent simulated leader + follower."""

    def __init__(self, store: Store, mock: bool = False, mock_faults: str | None = None, mock_speed: float = 1.0):
        if mock_speed <= 0:
            raise ValueError("mock speed must be > 0")
        self.store = store
        self.mock = mock
        self.rig: Any = None
        self.actor: Any = None
        if mock:
            from .mock import MockActor, load_rig

            self.rig_path: Path = store.root / ".mock-rig.json"
            # Simulated calibrations must never land in the real LeRobot cache.
            store.lerobot_root = store.root / "mock-lerobot"
            self.rig = load_rig(self.rig_path, mock_faults)
            self.actor = MockActor(self.rig, speed=mock_speed)

    def open_bus(self, port: str) -> MotorBus:
        if self.mock:
            return self.rig.bus(port)
        from .bus import FeetechBus

        return FeetechBus(port)

    def list_ports(self) -> dict[str, str]:
        return self.rig.ports.list() if self.mock else list_ports()

    def context(self, **kw) -> Context:
        return Context(store=self.store, open_bus=self.open_bus, list_ports=self.list_ports, actor=self.actor, **kw)

    def remembered_port(self, arm_id: str) -> str | None:
        """The arm's port if it is plugged in now. Found by USB serial number first, because device paths
        change with plug order (/dev/ttyACM0 <-> /dev/ttyACM1); falls back to the last path used."""
        info = self.store.arm(arm_id) or {}
        ports = self.list_ports()
        serial = info.get("usb_serial")
        if serial:  # known board: never fall back to a path that may now belong to the other arm
            by_serial = [p for p, s in ports.items() if s == serial]
            return by_serial[0] if len(by_serial) == 1 else None
        port = info.get("port")
        return port if port in ports else None

    def target(self, role: str, arm_id: str | None = None, port: str | None = None, note: str = "") -> Target:
        """port=None and no remembered port -> the wizard finds it by unplugging."""
        arm_id = arm_id or role
        return Target(role=role, arm_id=arm_id, port=port or self.remembered_port(arm_id), note=note)

    def save(self) -> None:
        if self.mock:
            from .mock import save_rig

            save_rig(self.rig, self.rig_path)
