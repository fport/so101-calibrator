"""Register-level access to Feetech STS3215 motors and the arm-level operations calibration needs.

`MotorBus` is the small surface a backend must provide (real serial bus or `mock.MockBus`).
`Arm` builds everything else on top of it, so the mock exercises the same code paths as hardware.
"""

from __future__ import annotations

import threading
from typing import Protocol

from .calibration import Calibration, MotorCalibration
from .profiles import BAUDRATES, DEFAULT_BAUDRATE, HALF_TURN, JOINTS, MAX_POS, MOTOR_IDS

# name: (address, size in bytes, sign bit or None)
REGISTERS = {
    "Model_Number": (3, 2, None),
    "ID": (5, 1, None),
    "Min_Position_Limit": (9, 2, None),
    "Max_Position_Limit": (11, 2, None),
    "Max_Torque_Limit": (16, 2, None),
    "P_Coefficient": (21, 1, None),
    "D_Coefficient": (22, 1, None),
    "I_Coefficient": (23, 1, None),
    "Protection_Current": (28, 2, None),
    "Homing_Offset": (31, 2, 11),
    "Operating_Mode": (33, 1, None),
    "Overload_Torque": (36, 1, None),
    "Torque_Enable": (40, 1, None),
    "Acceleration": (41, 1, None),
    "Goal_Position": (42, 2, 15),
    "Lock": (55, 1, None),
    "Present_Position": (56, 2, 15),
    "Present_Load": (60, 2, 10),
    "Present_Voltage": (62, 1, None),  # 0.1 V
    "Present_Temperature": (63, 1, None),  # degrees C
}


class BusError(RuntimeError):
    pass


def encode_sign_magnitude(value: int, sign_bit: int) -> int:
    limit = (1 << sign_bit) - 1
    if abs(value) > limit:
        raise ValueError(f"{value} does not fit in sign-magnitude with sign bit {sign_bit} (max {limit})")
    return ((1 << sign_bit) if value < 0 else 0) | abs(value)


def decode_sign_magnitude(raw: int, sign_bit: int) -> int:
    magnitude = raw & ((1 << sign_bit) - 1)
    return -magnitude if (raw >> sign_bit) & 1 else magnitude


class MotorBus(Protocol):
    port: str

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def set_baudrate(self, baudrate: int) -> None: ...
    def ping(self, motor_id: int) -> int | None:
        """Model number of the motor, or None if it does not answer."""
    def read(self, register: str, motor_id: int) -> int: ...
    def write(self, register: str, motor_id: int, value: int) -> None: ...


def _patched_packet_timeout(self, packet_length):
    self.packet_start_time = self.getCurrentTime()
    self.packet_timeout = (self.tx_time_per_byte * packet_length) + (self.tx_time_per_byte * 3.0) + 50


class FeetechBus:
    """Real hardware over USB serial, using Feetech's `scservo_sdk` (pip: feetech-servo-sdk)."""

    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, retries: int = 3):
        self.port = port
        self.baudrate = baudrate
        self.retries = retries
        self._ph = self._pk = None

    def connect(self) -> None:
        try:
            import scservo_sdk as scs
        except ImportError as e:  # pragma: no cover - depends on optional extra
            raise BusError("Real motors need Feetech's SDK: pip install feetech-servo-sdk") from e
        self._scs = scs
        self._ph = scs.PortHandler(self.port)
        # Same fix LeRobot applies: the PyPI SDK's packet timeout is too short for STS replies.
        self._ph.setPacketTimeout = _patched_packet_timeout.__get__(self._ph, scs.PortHandler)
        self._pk = scs.PacketHandler(0)  # STS series: protocol 0
        try:
            opened = self._ph.openPort()
        except Exception as e:  # pyserial raises SerialException with a useful message
            raise BusError(f"Cannot open {self.port}: {e}") from e
        if not opened:
            raise BusError(f"Cannot open {self.port}")
        self.set_baudrate(self.baudrate)

    def close(self) -> None:
        if self._ph is not None:
            try:
                self._ph.closePort()
            except Exception:  # the device may already be gone
                pass
            self._ph = None

    def set_baudrate(self, baudrate: int) -> None:
        if not self._ph.setBaudRate(baudrate):
            raise BusError(f"Cannot set baudrate {baudrate} on {self.port}")
        self.baudrate = baudrate

    def _txrx(self, fn, *args):
        """One SDK transaction. A vanished port makes pyserial raise OSError/SerialException (or the SDK
        trip over a closed handle); everything above this class only has to handle BusError."""
        if self._ph is None:
            raise BusError(f"{self.port} is not open")
        try:
            return fn(self._ph, *args)
        except Exception as e:
            raise BusError(f"{self.port}: {e}") from e

    def ping(self, motor_id: int) -> int | None:
        for _ in range(2):  # scans ping absent ids too: keep it quick
            model, comm, _err = self._txrx(self._pk.ping, motor_id)
            if comm == self._scs.COMM_SUCCESS:
                return model
        return None

    def read(self, register: str, motor_id: int) -> int:
        addr, size, sign = REGISTERS[register]
        fn = {1: self._pk.read1ByteTxRx, 2: self._pk.read2ByteTxRx}[size]
        comm = None
        for _ in range(self.retries):
            value, comm, _err = self._txrx(fn, motor_id, addr)
            if comm == self._scs.COMM_SUCCESS:
                return decode_sign_magnitude(value, sign) if sign is not None else value
        raise BusError(f"read {register} from motor {motor_id} failed: {self._pk.getTxRxResult(comm)}")

    def write(self, register: str, motor_id: int, value: int) -> None:
        addr, size, sign = REGISTERS[register]
        raw = encode_sign_magnitude(value, sign) if sign is not None else value
        fn = {1: self._pk.write1ByteTxRx, 2: self._pk.write2ByteTxRx}[size]
        comm = None
        for _ in range(self.retries):
            comm, _err = self._txrx(fn, motor_id, addr, raw)
            if comm == self._scs.COMM_SUCCESS:
                return
        raise BusError(f"write {register}={value} to motor {motor_id} failed: {self._pk.getTxRxResult(comm)}")


class Arm:
    """One SO-101 arm (6 motors) on a bus. Thread-safe: the dashboard polls while the wizard writes."""

    def __init__(self, bus: MotorBus):
        self.bus = bus
        self.lock = threading.RLock()
        self.connected = False

    @property
    def port(self) -> str:
        return self.bus.port

    def connect(self) -> None:
        with self.lock:
            if not self.connected:
                self.bus.connect()
                self.connected = True

    def close(self) -> None:
        with self.lock:
            if self.connected:
                self.bus.close()
                self.connected = False

    def scan(self, ids: range = range(1, 11)) -> dict[int, int]:
        """id -> model number for every motor that answers at the current baudrate."""
        with self.lock:
            found = {}
            for i in ids:
                model = self.bus.ping(i)
                if model is not None:
                    found[i] = model
            return found

    def scan_baudrates(self) -> dict[int, dict[int, int]]:
        with self.lock:
            result = {}
            try:
                for baud in BAUDRATES:
                    self.bus.set_baudrate(baud)
                    found = self.scan()
                    if found:
                        result[baud] = found
            finally:
                self.bus.set_baudrate(DEFAULT_BAUDRATE)
            return result

    def _read_all(self, register: str, joints=JOINTS) -> dict[str, int]:
        with self.lock:
            return {j: self.bus.read(register, MOTOR_IDS[j]) for j in joints}

    def positions(self, joints=JOINTS) -> dict[str, int]:
        return self._read_all("Present_Position", joints)

    def voltages(self) -> dict[str, float]:
        return {j: v / 10 for j, v in self._read_all("Present_Voltage").items()}

    def temperatures(self) -> dict[str, int]:
        return self._read_all("Present_Temperature")

    def set_torque(self, enabled: bool, joints=JOINTS) -> None:
        """Like LeRobot: torque off also unlocks the EEPROM (so offsets/limits can be written), torque on
        locks it again. Stops at the first error; use torque_off() when making things safe."""
        with self.lock:
            for j in joints:
                self.bus.write("Torque_Enable", MOTOR_IDS[j], 1 if enabled else 0)
                self.bus.write("Lock", MOTOR_IDS[j], 1 if enabled else 0)

    def torque_off(self) -> list[str]:
        """Best effort: try to disable torque on every motor even if some fail. Returns the errors."""
        errors = []
        with self.lock:
            if not self.connected:
                return errors
            for j in JOINTS:
                try:
                    self.bus.write("Torque_Enable", MOTOR_IDS[j], 0)
                except Exception as e:  # keep going: the other motors must still be released
                    errors.append(f"{j}: {e}")
        return errors

    def release(self) -> None:
        """Torque off (best effort) and close the port. Never raises."""
        self.torque_off()
        try:
            self.close()
        except Exception:
            pass

    def prepare_for_calibration(self) -> None:
        with self.lock:
            self.set_torque(False)
            for j in JOINTS:
                self.bus.write("Operating_Mode", MOTOR_IDS[j], 0)  # position mode

    def reset_calibration(self, joints=JOINTS) -> None:
        with self.lock:
            for j in joints:
                i = MOTOR_IDS[j]
                self.bus.write("Homing_Offset", i, 0)
                self.bus.write("Min_Position_Limit", i, 0)
                self.bus.write("Max_Position_Limit", i, MAX_POS)

    def set_half_turn_homings(self, joints=JOINTS) -> dict[str, int]:
        """Same as LeRobot: offset = actual - 2047 so the current pose reads 2047 afterwards."""
        with self.lock:
            self.reset_calibration(joints)
            actual = self.positions(joints)
            offsets = {j: actual[j] - HALF_TURN for j in joints}
            for j, off in offsets.items():
                # 2048 is one past the encodable limit; clamp so the write cannot fail mid-way.
                self.bus.write("Homing_Offset", MOTOR_IDS[j], max(-2047, min(2047, off)))
            return offsets

    def write_calibration(self, cal: Calibration) -> None:
        with self.lock:
            for m in cal.values():
                self.bus.write("Homing_Offset", m.id, m.homing_offset)
                self.bus.write("Min_Position_Limit", m.id, m.range_min)
                self.bus.write("Max_Position_Limit", m.id, m.range_max)

    def read_calibration(self) -> Calibration:
        with self.lock:
            return {
                j: MotorCalibration(
                    id=MOTOR_IDS[j],
                    drive_mode=0,
                    homing_offset=self.bus.read("Homing_Offset", MOTOR_IDS[j]),
                    range_min=self.bus.read("Min_Position_Limit", MOTOR_IDS[j]),
                    range_max=self.bus.read("Max_Position_Limit", MOTOR_IDS[j]),
                )
                for j in JOINTS
            }

    def configure_follower(self) -> None:
        """Same gains and gripper protection LeRobot's SO-101 follower applies on connect."""
        with self.lock:
            for j in JOINTS:
                i = MOTOR_IDS[j]
                self.bus.write("Operating_Mode", i, 0)
                self.bus.write("P_Coefficient", i, 16)
                self.bus.write("I_Coefficient", i, 0)
                self.bus.write("D_Coefficient", i, 32)
            g = MOTOR_IDS["gripper"]
            self.bus.write("Max_Torque_Limit", g, 500)
            self.bus.write("Protection_Current", g, 250)
            self.bus.write("Overload_Torque", g, 25)

    def write_goals(self, goals: dict[str, int]) -> None:
        with self.lock:
            for j, g in goals.items():
                self.bus.write("Goal_Position", MOTOR_IDS[j], int(g))
