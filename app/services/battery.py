"""
Battery level of the 18650 UPS board.

Who does what:
    hardware/rfid_reader.py  reads the UPS board's battery chip every 30 s (it owns the hardware)
                             and saves the reading with save(); it also shuts the Pi down safely
                             when the battery is almost empty (should_shut_down()).
    the website              shows the last saved reading with get() (sidebar badge, Settings,
                             low-battery warning). It never touches I2C itself.

Supported battery chips (set BATTERY_BACKEND in /etc/attendance.env):
    none       no battery monitor (the badge is simply hidden)
    max17040   fuel gauge at I2C 0x36 - Geekworm X728/X729 and similar UPS boards; reports % directly
    ina219     power monitor at I2C 0x42 (or 0x40/0x41/0x43) - Waveshare-style UPS boards;
               % is estimated from the voltage; charging = current flowing into the cells
On the laptop the dev page sets a fake reading (/dev -> Battery).
"""
import logging
from datetime import datetime

log = logging.getLogger(__name__)

STALE_SECONDS = 180          # no reading for 3 minutes -> treat as unknown
LOW, CRITICAL = 20, 10       # % - warn the instructor / the LCD
SHUTDOWN_AT = 5              # % - the Pi shuts itself down (only while NOT charging)

KEYS = ("battery_percent", "battery_charging", "battery_voltage")


def _ts(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- shared reading (database)
def save(conn, percent, charging=None, voltage=None, now=None) -> None:
    ts = _ts(now)
    values = {"battery_percent": None if percent is None else str(int(round(max(0, min(100, percent))))),
              "battery_charging": None if charging is None else ("1" if charging else "0"),
              "battery_voltage": None if voltage is None else f"{voltage:.2f}"}
    conn.executemany(
        """INSERT INTO device_status (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        [(k, v, ts) for k, v in values.items()])
    conn.commit()


def clear(conn) -> None:
    conn.execute(f"DELETE FROM device_status WHERE key IN ({','.join('?' * len(KEYS))})", KEYS)
    conn.commit()


def get(conn, now=None) -> dict | None:
    """The latest reading, or None if there is no battery monitor / no fresh reading.
    {'percent': 82, 'charging': True|False|None, 'voltage': 4.01|None, 'level': 'ok'|'low'|'critical',
     'icon': 'battery-charging'|'battery-full'|'battery-half'|'battery', 'updated_at': '...'}"""
    rows = {r["key"]: r for r in conn.execute(
        f"SELECT key, value, updated_at FROM device_status WHERE key IN ({','.join('?' * len(KEYS))})", KEYS)}
    pct = rows.get("battery_percent")
    if not pct or pct["value"] is None:
        return None
    now = now or datetime.now()
    age = (now - datetime.strptime(pct["updated_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
    if age > STALE_SECONDS:
        return None
    percent = int(pct["value"])
    ch = rows.get("battery_charging")
    charging = None if not ch or ch["value"] is None else ch["value"] == "1"
    v = rows.get("battery_voltage")
    level = "critical" if percent <= CRITICAL else "low" if percent <= LOW else "ok"
    if charging:
        level = "ok" if percent > CRITICAL else "low"          # plugged in: no alarm
        icon = "battery-charging"
    else:
        icon = "battery-full" if percent >= 70 else "battery-half" if percent >= 30 else "battery"
    return {"percent": percent, "charging": charging, "voltage": float(v["value"]) if v and v["value"] else None,
            "level": level, "icon": icon, "updated_at": pct["updated_at"]}


def should_shut_down(recent_percents: list, charging) -> bool:
    """Shut down only if the last 2 readings are both at/below SHUTDOWN_AT and it isn't charging
    (one bad reading never turns the device off)."""
    if charging:
        return False
    last = [p for p in recent_percents[-2:] if p is not None]
    return len(last) == 2 and all(p <= SHUTDOWN_AT for p in last)


# ---------------------------------------------------------------- reading the chip (Pi only)
def cell_percent(volts_per_cell: float) -> float:
    """Li-ion resting voltage -> rough % (used when the chip only gives voltage)."""
    curve = [(3.00, 0), (3.45, 5), (3.60, 15), (3.70, 30), (3.80, 50), (3.90, 65), (4.00, 80), (4.10, 92), (4.20, 100)]
    if volts_per_cell <= curve[0][0]:
        return 0.0
    for (v1, p1), (v2, p2) in zip(curve, curve[1:]):
        if volts_per_cell <= v2:
            return p1 + (p2 - p1) * (volts_per_cell - v1) / (v2 - v1)
    return 100.0


def decode_max17040(vcell: bytes, soc: bytes) -> tuple[float, float]:
    """Raw registers -> (percent, volts). VCELL: 12 bits × 1.25 mV; SOC: whole % + 1/256 %."""
    volts = ((vcell[0] << 4) | (vcell[1] >> 4)) * 1.25 / 1000
    percent = soc[0] + soc[1] / 256
    return min(percent, 100.0), volts


DRAIN_THRESHOLD = 50     # shunt steps (10 µV each) = 0.5 mV: clearly drawing from the cells


def decode_ina219(bus_reg: bytes, shunt_reg: bytes, cells_in_series: int = 1,
                  invert: bool = False) -> tuple[float, float, bool]:
    """Raw registers -> (percent, volts, plugged_in). Bus voltage: bits 15..3 × 4 mV.
    Shunt voltage is signed (10 µV steps). After `invert` (boards wired the other way round,
    e.g. the Waveshare UPS HAT (D)), positive = current flowing INTO the cells.

    'plugged in' (reported as charging) = the cells are NOT being drained: either charging, or
    full with the charger still in (the current then drops to ~0). Running on battery always
    draws a clear current out of the cells (the Pi needs several hundred mA)."""
    bus = int.from_bytes(bus_reg, "big")
    volts = (bus >> 3) * 0.004
    shunt = int.from_bytes(shunt_reg, "big", signed=True)
    if invert:
        shunt = -shunt
    plugged_in = shunt > -DRAIN_THRESHOLD
    return cell_percent(volts / max(cells_in_series, 1)), volts, plugged_in


class Reader:
    """Reads the battery chip over I2C. read() -> (percent, charging|None, volts) or None."""

    def __init__(self, backend: str, address: int | None = None, cells_in_series: int = 1, bus_no: int = 1,
                 invert_current: bool = False):
        self.backend, self.cells, self.invert = backend, cells_in_series, invert_current
        self.address = address or {"max17040": 0x36, "ina219": 0x42}.get(backend)
        self._bus = None
        if backend in ("max17040", "ina219"):
            try:
                from smbus2 import SMBus
                self._bus = SMBus(bus_no)
            except Exception:
                log.exception("battery monitor: I2C bus %s not available", bus_no)

    def read(self):
        if not self._bus:
            return None
        try:
            if self.backend == "max17040":
                vcell = bytes(self._bus.read_i2c_block_data(self.address, 0x02, 2))
                soc = bytes(self._bus.read_i2c_block_data(self.address, 0x04, 2))
                percent, volts = decode_max17040(vcell, soc)
                return percent, None, volts
            if self.backend == "ina219":
                bus_reg = bytes(self._bus.read_i2c_block_data(self.address, 0x02, 2))
                shunt_reg = bytes(self._bus.read_i2c_block_data(self.address, 0x01, 2))
                percent, volts, charging = decode_ina219(bus_reg, shunt_reg, self.cells, self.invert)
                return percent, charging, volts
        except Exception:
            log.warning("battery monitor: no answer from %s at 0x%02x", self.backend, self.address)
        return None


class PowerLossPin:
    """Optional: many UPS boards pull a GPIO pin high/low when the USB-C power is unplugged
    (X728: GPIO6). charging() -> True while external power is present, None if not configured."""

    def __init__(self, gpio: int | None):
        self._pin = None
        if gpio is not None:
            try:
                from gpiozero import DigitalInputDevice
                self._pin = DigitalInputDevice(gpio)
            except Exception:
                log.exception("battery monitor: power-loss pin GPIO%s not available", gpio)

    def charging(self):
        if not self._pin:
            return None
        return not self._pin.value          # X728: 1 = power lost
