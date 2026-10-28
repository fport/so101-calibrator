"""Serial port discovery: list candidate ports and find a board by unplugging it."""

from __future__ import annotations

import glob
import platform


def list_ports() -> dict[str, str]:
    """device path -> USB serial number ("" if unknown) of every likely motor-controller port."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        lp = None
    if lp is not None:
        ports = {}
        for p in lp.comports():
            if platform.system() == "Darwin" and p.device.startswith("/dev/tty."):
                continue  # macOS lists every port twice; /dev/cu.* is the one to open
            if p.vid is None and not p.device.startswith(("/dev/ttyACM", "/dev/ttyUSB")):
                continue  # skip Bluetooth / debug consoles
            ports[p.device] = p.serial_number or ""
        return ports
    patterns = ["/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/ttyACM*", "/dev/ttyUSB*"]
    return {p: _serial_from_path(p) for pat in patterns for p in sorted(glob.glob(pat))}


def _serial_from_path(device: str) -> str:
    # macOS names CDC-ACM ports after the chip's serial number: /dev/cu.usbmodem5AB01794851
    name = device.rsplit("/", 1)[-1]
    for prefix in ("cu.usbmodem", "cu.usbserial-"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return ""


def diff_ports(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    """(removed, added) device paths."""
    return sorted(set(before) - set(after)), sorted(set(after) - set(before))
