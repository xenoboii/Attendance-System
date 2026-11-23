"""
Hardware check: is every part connected and answering? Prints a checklist with hints.

On the Pi use the wrapper (it pauses the card reader service while testing, then starts it again):

    bash ~/AttendanceSystem/deploy/check-hardware.sh            # full check (asks you to press the button)
    bash ~/AttendanceSystem/deploy/check-hardware.sh --quick    # no beeps / lights / button test

What is checked:
    I2C + SPI turned on · LCD (shows a test message) · RC522 card reader (version register)
    UPS battery chip (+ cell voltage) · RTC clock · buzzer · green/red LED · shutdown button
    power supply (under-voltage) · the website and reader services
Parts that can't report back (buzzer, LEDs, fan) are switched on so you can see / hear them.
Nothing is saved; attendance data is not touched.
"""
import glob
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # import config.py / hardware/

from config import Config  # noqa: E402

OK, BAD, WARN, INFO = "✓", "✗", "!", "-"
results = []                    # (mark, part, detail)


def report(mark, part, detail="", hint=""):
    results.append((mark, part))
    line = f" {mark}  {part:<24} {detail}"
    print(line)
    if hint and mark != OK:
        for h in hint.split("\n"):
            print(f"    {'':<24} -> {h}")


def section(title):
    print(f"\n== {title}")


# ---------------------------------------------------------------- interfaces
def check_interfaces():
    section("Interfaces")
    i2c = os.path.exists("/dev/i2c-1")
    report(OK if i2c else BAD, "I2C (LCD, UPS, RTC)", "/dev/i2c-1" if i2c else "not turned on",
           "sudo raspi-config nonint do_i2c 0   then   sudo reboot")
    spi = sorted(glob.glob("/dev/spidev0.*"))
    report(OK if spi else BAD, "SPI (card reader)", ", ".join(spi) if spi else "not turned on",
           "sudo raspi-config nonint do_spi 0   then   sudo reboot")
    return i2c, bool(spi)


# ---------------------------------------------------------------- I2C devices
def i2c_scan():
    """Addresses that answer on bus 1 (like `i2cdetect -y 1`)."""
    from smbus2 import SMBus
    found = set()
    with SMBus(1) as bus:
        for addr in range(0x03, 0x78):
            try:
                bus.read_byte(addr)
                found.add(addr)
            except OSError:
                pass
    return found


def check_i2c(i2c_on):
    section("I2C parts")
    if not i2c_on:
        report(BAD, "I2C scan", "skipped (I2C is off)")
        return set()
    try:
        found = i2c_scan()
    except Exception as e:                                       # smbus2 missing etc.
        report(BAD, "I2C scan", f"failed: {e}", "venv/bin/pip install smbus2")
        return set()
    print(f"    addresses found: {' '.join(f'{a:02x}' for a in sorted(found)) or 'none'}")

    # LCD
    lcd_addr = Config.LCD_I2C_ADDRESS
    if lcd_addr in found:
        report(OK, "LCD", f"answers at 0x{lcd_addr:02x}")
    else:
        other = next((a for a in (0x27, 0x3F) if a in found), None)
        if other:
            report(BAD, "LCD", f"found at 0x{other:02x}, but the setting says 0x{lcd_addr:02x}",
                   f"set LCD_I2C_ADDRESS=0x{other:02X} in /etc/attendance.env")
        else:
            report(BAD, "LCD", "not found (expected 0x27 or 0x3f)",
                   "check SDA -> pin 3, SCL -> pin 5, VCC -> 5V, GND -> GND\n"
                   "with the level converter: LV=3.3V, HV=5V, both GNDs connected, LV1/LV2 to the Pi side")

    # UPS (battery chip + controller)
    ups = [a for a in (0x43, 0x2D) if a in found]
    if 0x43 in found:
        detail = "battery chip 0x43" + (" + controller 0x2d" if 0x2D in found else "")
        report(OK, "UPS HAT (D)", detail)
    elif ups:
        report(WARN, "UPS HAT (D)", "controller 0x2d only, battery chip 0x43 missing",
               "check the HAT sits firmly under the Pi (spring pins touching the header)")
    else:
        other = [a for a in (0x36, 0x40, 0x41, 0x42) if a in found]
        report(WARN if other else INFO, "UPS / battery chip",
               f"other battery chip at {', '.join(f'0x{a:02x}' for a in other)}" if other else "not found",
               "only needed with the UPS HAT; check it's mounted and switched ON")

    # RTC: when the Pi uses it, the system driver owns 0x68 ("UU" in i2cdetect) and /dev/rtc0 exists
    if os.path.exists("/dev/rtc0"):
        report(OK, "RTC (DS3231)", "in use by the Pi (/dev/rtc0)")
    elif 0x68 in found:
        report(WARN, "RTC (DS3231)", "connected, but the Pi is NOT using it - the time will fall behind",
               "bash deploy/rtc-setup.sh   then   sudo reboot")
    else:
        report(INFO, "RTC (DS3231)", "not found (optional)",
               "if fitted: SDA/SCL -> pins 3/5, VCC -> 3.3V (pin 1), GND")
    return found


def check_lcd_display(found):
    """Write a test message, so you can see the screen really works (not just answers)."""
    if Config.LCD_I2C_ADDRESS not in found:
        return
    try:
        from hardware.lcd_display import Lcd
        lcd = Lcd(Config.LCD_I2C_ADDRESS)
        if lcd._lcd is None:
            report(BAD, "LCD text", "the LCD library could not open it", "venv/bin/pip install RPLCD smbus2")
            return
        lcd.show(["   HARDWARE TEST", "  LCD is working", "", time.strftime("     %I:%M:%S %p")])
        report(OK, "LCD text", "test message sent - is it on the screen?")
        print(f"    {'':<24}    (blank but lit? turn the small blue contrast screw on the back)")
    except Exception as e:
        report(BAD, "LCD text", f"failed: {e}")


def check_battery(found):
    if 0x43 not in found and Config.BATTERY_BACKEND == "none":
        return
    try:
        from app.services import battery
        addr = Config.BATTERY_I2C_ADDRESS or 0x43
        invert = Config.BATTERY_CURRENT_INVERT or addr == 0x43          # UPS HAT (D) reads charging as negative
        reading = battery.Reader("ina219", addr, Config.BATTERY_CELLS_SERIES, invert_current=invert).read()
    except Exception as e:
        report(BAD, "Battery reading", f"failed: {e}")
        return
    if reading is None:
        report(BAD, "Battery reading", f"no answer from 0x{addr:02x}")
        return
    percent, charging, volts = reading
    if volts < 2.5:
        report(WARN, "Battery cells", f"{volts:.2f} V - no cells (or empty)",
               "insert the 21700 cells, then press the HAT's BOOT button once\n"
               "keep BATTERY_BACKEND=none until the cells are in")
    else:
        report(OK, "Battery cells", f"{volts:.2f} V  ~{percent:.0f}%  {'plugged in / charging' if charging else 'on battery'}")
        print(f"    {'':<24}    (unplug / plug the charger and run again: this should switch)")
    setting = Config.BATTERY_BACKEND
    # the reader service uses the address from the settings (0x42 when it's not set)
    used = Config.BATTERY_I2C_ADDRESS or {"ina219": 0x42, "max17040": 0x36}.get(setting)
    if setting != "none" and used is not None and used not in found:
        chip = next((a for a in (0x43, 0x42, 0x41, 0x40, 0x36) if a in found), None)
        report(BAD, "Battery setting", f"the reader looks for the chip at 0x{used:02x}, but it isn't there"
               + (f" (found at 0x{chip:02x})" if chip else ""),
               f"set BATTERY_I2C_ADDRESS=0x{(chip or 0x43):02X} in /etc/attendance.env,\n"
               "then: sudo systemctl restart attendance-rfid")
    if setting == "none" and volts >= 2.5:
        print(f"    {'':<24} -> cells found: you can now set BATTERY_BACKEND=ina219, BATTERY_I2C_ADDRESS=0x43")
    elif setting != "none" and volts < 2.5:
        print(f"    {'':<24} -> WARNING: BATTERY_BACKEND={setting} with no cells shuts the Pi down! set it to none")


def check_rtc(found):
    """Compare the RTC's time with the Pi's: more than 2 minutes apart = not being kept in sync."""
    from datetime import datetime, timezone
    rtc = None
    try:
        if os.path.exists("/sys/class/rtc/rtc0/date"):          # driver loaded: read it from the system
            d = Path("/sys/class/rtc/rtc0/date").read_text().strip()
            t = Path("/sys/class/rtc/rtc0/time").read_text().strip()
            # the RTC keeps UTC; compare with the Pi's UTC time
            rtc = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S")
            pi = datetime.now(timezone.utc).replace(tzinfo=None)
        elif 0x68 in found:                                      # no driver: read the chip directly
            from smbus2 import SMBus
            with SMBus(1) as bus:
                r = bus.read_i2c_block_data(0x68, 0x00, 7)
            bcd = lambda b: (b >> 4) * 10 + (b & 0x0F)
            rtc = datetime(2000 + bcd(r[6]), bcd(r[5] & 0x1F), bcd(r[4]),
                           bcd(r[2] & 0x3F), bcd(r[1]), bcd(r[0] & 0x7F))
            pi = None
        else:
            return
    except OSError as e:
        if e.errno == 22:                  # EINVAL: the RTC holds no valid time (new / reset / no coin cell)
            report(WARN, "RTC time", "the RTC has no valid time yet",
                   "set it once: log in as an instructor on a phone with the right time,\n"
                   "or if the Pi's time is right: sudo /usr/local/sbin/attendance-set-time $(date +%s)\n"
                   "if it happens again after a power-off, the RTC coin cell (LIR2032) is missing or empty")
        else:
            report(WARN, "RTC time", f"could not read: {e}")
        return
    except Exception as e:
        report(WARN, "RTC time", f"could not read: {e}")
        return
    if pi is None:
        report(INFO, "RTC time", f"{rtc:%Y-%m-%d %H:%M} (Pi local: {datetime.now():%Y-%m-%d %H:%M})",
               "the Pi isn't using it yet: bash deploy/rtc-setup.sh, reboot, then sync the time from a phone")
        return
    off = abs((rtc - pi).total_seconds())
    if off <= 120:
        report(OK, "RTC time", f"matches the Pi (within {off:.0f} s)")
    else:
        report(WARN, "RTC time", f"{off / 60:.0f} min off from the Pi",
               "log in as an instructor on a phone with the right time (it syncs and saves into the RTC),\n"
               "or if the Pi's time is right: sudo /usr/local/sbin/attendance-set-time $(date +%s)\n"
               "keeps drifting after power-off? the RTC coin cell (LIR2032) is empty")


# ---------------------------------------------------------------- card reader
def check_rc522(spi_on):
    section("Card reader")
    if Config.RFID_BACKEND != "rc522":
        report(INFO, "RC522", f"RFID_BACKEND={Config.RFID_BACKEND} (not the RC522)",
               "on the Pi set RFID_BACKEND=rc522 in /etc/attendance.env")
        return
    if not spi_on:
        report(BAD, "RC522", "skipped (SPI is off)")
        return
    try:
        import RPi.GPIO as GPIO
        from mfrc522 import MFRC522
        GPIO.setwarnings(False)
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)
        reader = MFRC522(pin_mode=GPIO.BCM, pin_rst=25)
        version = reader.Read_MFRC522(0x37)
    except Exception as e:
        report(BAD, "RC522", f"failed to open: {e}",
               "is the reader service still running? (the wrapper stops it)\n"
               "venv/bin/pip install mfrc522")
        return
    names = {0x91: "v1.0", 0x92: "v2.0", 0x88: "clone", 0x12: "clone"}
    if version in (0x00, 0xFF):
        report(BAD, "RC522", f"no answer (0x{version:02x})",
               "MOSI -> pin 19, MISO -> pin 21 (not swapped), SCK -> 23, SDA -> 24\n"
               "3.3V -> pin 17, GND -> 25, RST -> 22; RC522 pins SOLDERED")
        return
    report(OK, "RC522", f"answers (0x{version:02x} {names.get(version, '')})")
    if INTERACTIVE:
        print(f"    {'':<24}    tap a card within 8 s ...", end="", flush=True)
        end = time.time() + 8
        while time.time() < end:
            status, _ = reader.MFRC522_Request(reader.PICC_REQIDL)
            if status == reader.MI_OK:
                status, uid = reader.MFRC522_Anticoll()
                if status == reader.MI_OK and len(uid) >= 5:
                    n = 0
                    for b in uid[:5]:
                        n = n * 256 + b
                    print()
                    report(OK, "Card tap", f"read card {n:X}")
                    return
            time.sleep(0.1)
        print()
        report(WARN, "Card tap", "no card read", "hold the card flat on the reader, 1-3 cm, away from metal")


# ---------------------------------------------------------------- outputs + button
def _gpio():
    import RPi.GPIO as GPIO
    GPIO.setwarnings(False)
    if GPIO.getmode() is None:
        GPIO.setmode(GPIO.BCM)
    return GPIO


def check_outputs():
    section("Buzzer, lights, button  (watch and listen)")
    if not INTERACTIVE:
        report(INFO, "Buzzer / LEDs / button", "skipped (--quick)")
        return
    try:
        GPIO = _gpio()
    except Exception as e:
        report(BAD, "GPIO", f"not available: {e}")
        return
    for pin, name, secs in ((Config.BUZZER_GPIO, "Buzzer (pin 12)", 0.4),
                            (Config.LED_GREEN_GPIO, "Green LED (pin 16)", 1.0),
                            (Config.LED_RED_GPIO, "Red LED (pin 18)", 1.0)):
        try:
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, 1); time.sleep(secs); GPIO.output(pin, 0)
            report(INFO, name, f"switched on for {secs}s - did you {'hear it' if 'Buzzer' in name else 'see it'}?",
                   "no? check + / - (long leg = +), the resistor (LEDs) and the GND wire")
        except Exception as e:
            report(BAD, name, f"failed: {e}")
    pin = Config.SHUTDOWN_BUTTON_GPIO
    if pin is None:
        report(INFO, "Shutdown button", "turned off in settings")
        return
    try:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        if GPIO.input(pin) == 0:
            report(BAD, "Shutdown button", "reads PRESSED without touching it",
                   "the wire to pin 37 is touching GND, or the button is stuck / wired as normally-closed")
            return
        print(f"    {'':<24}    press the shutdown button within 10 s ...", end="", flush=True)
        end = time.time() + 10
        while time.time() < end:
            if GPIO.input(pin) == 0:
                print()
                report(OK, "Shutdown button", "press detected (pins 37 + 39)")
                return
            time.sleep(0.02)
        print()
        report(BAD, "Shutdown button", "no press detected",
               "button legs -> pin 37 (GPIO26) and pin 39 (GND); use a momentary, normally-open button")
    except Exception as e:
        report(BAD, "Shutdown button", f"failed: {e}")


# ---------------------------------------------------------------- power + services
def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return None


def check_power_and_services():
    section("Power and services")
    out = _run(["vcgencmd", "get_throttled"])
    if out is None:
        report(INFO, "Power supply", "vcgencmd not available (not a Pi?)")
    else:
        try:
            flags = int(out.split("=")[1], 16)
        except (IndexError, ValueError):
            flags = -1
        if flags == 0:
            report(OK, "Power supply", "no under-voltage")
        elif flags > 0 and flags & 0x1:
            report(BAD, "Power supply", f"UNDER-VOLTAGE NOW ({out})",
                   "adapter or UPS can't supply enough: use a 5V 3A supply, charge the cells")
        else:
            report(WARN, "Power supply", f"under-voltage happened since boot ({out})",
                   "a weak adapter / cable or low cells; random freezes can follow")
    for svc in ("attendance-web", "attendance-rfid", "attendance-lcd-off", "attendance-lcd-boot"):
        state = _run(["systemctl", "is-enabled", svc])
        if state is None:
            report(INFO, svc, "systemctl not available")
            continue
        if svc == "attendance-rfid":
            report(OK if state == "enabled" else BAD, svc, f"{state} (paused during this check)",
                   "sudo systemctl enable --now attendance-rfid")
            continue
        active = _run(["systemctl", "is-active", svc])
        good = state == "enabled" and (active == "active" or svc == "attendance-lcd-boot")
        hint = ("bash deploy/power-button.sh   then   sudo reboot" if svc.startswith("attendance-lcd")
                else f"sudo systemctl enable --now {svc}")
        report(OK if good else WARN, svc, f"{state} / {active}", hint)


# ---------------------------------------------------------------- main
INTERACTIVE = "--quick" not in sys.argv


def main() -> int:
    print("Attendance device - hardware check")
    print(f"settings: RFID={Config.RFID_BACKEND}  LCD=0x{Config.LCD_I2C_ADDRESS:02x}  "
          f"battery={Config.BATTERY_BACKEND}  simulate={int(Config.SIMULATE_HARDWARE)}")
    i2c_on, spi_on = check_interfaces()
    found = check_i2c(i2c_on)
    check_lcd_display(found)
    check_battery(found)
    check_rtc(found)
    check_rc522(spi_on)
    check_outputs()
    check_power_and_services()
    try:
        import RPi.GPIO as GPIO
        GPIO.cleanup()
    except Exception:
        pass
    bad = [p for m, p in results if m == BAD]
    warn = [p for m, p in results if m == WARN]
    print("\n== Result")
    if not bad and not warn:
        print(f" {OK}  Everything that can report back is OK.")
    if bad:
        print(f" {BAD}  Problems: {', '.join(bad)}")
    if warn:
        print(f" {WARN}  Check: {', '.join(warn)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
