"""
RFID reader process: reads card taps, records them, and updates the LCD, buzzer and LEDs
(green = accepted, red = rejected). Wiring: docs/PI_SETUP.md.
Runs separately from the web app (both share the same SQLite database), so if
one crashes the other keeps working.

    Laptop (no hardware):  python -m hardware.rfid_reader
        -> type a card UID and press Enter; the LCD is printed in the terminal
    Pi with USB reader:    same command; the reader "types" the UID + Enter
    Pi with RC522:         set RFID_BACKEND=rc522, SIMULATE_HARDWARE=0

Logs go to logs/hardware.log
"""
import logging
import time

from app.db import connect, upgrade_db
from app.logging_setup import setup_logging
from app.services import battery, card_registration
from config import Config

from .lcd_display import (Lcd, countdown_screen, idle_screen,
                          registration_waiting_screen, screen_for_tap, shutdown_confirm_screen,
                          shutdown_holding_screen,
                          shutdown_screen, shutting_down_screen)

log = logging.getLogger("hardware.rfid")


# ---------------------------------------------------------------- readers
class KeyboardReader:
    """USB RFID readers act like a keyboard, so this also works for the laptop simulator."""

    def read_uid(self) -> str | None:
        uid = input("Tap card (type UID, Enter; 'q' to quit): ").strip().upper()
        if uid == "Q":
            raise KeyboardInterrupt
        return uid or None


class RC522Reader:
    """RC522 module on SPI. pip install mfrc522  (Pi only)

    The RST wire is on GPIO25 (physical pin 22). The mfrc522 library guesses that pin from the
    pin-numbering mode, and the buzzer / LEDs (gpiozero) have already switched the mode to BCM -
    then the library picks GPIO15 (physical pin 10), the reader stays in reset and never sees a
    card. So the pin is passed explicitly, matching whichever numbering is active."""
    RST_GPIO = 25                     # BCM number (physical pin 22)
    RST_BOARD_PIN = 22                # the same wire, physical numbering

    def __init__(self):
        import RPi.GPIO as GPIO
        from mfrc522 import MFRC522
        mode = GPIO.getmode()
        if mode is None:              # nothing chosen yet: use BCM like gpiozero
            GPIO.setmode(GPIO.BCM)
            mode = GPIO.BCM
        rst = self.RST_GPIO if mode == GPIO.BCM else self.RST_BOARD_PIN
        self._r = MFRC522(pin_mode=mode, pin_rst=rst)
        log.info("RC522 ready (RST on physical pin 22)")

    def read_uid(self) -> str | None:
        """Same card number as SimpleMFRC522.read_id_no_block(), in hex, so cards registered
        before keep working."""
        r = self._r
        status, _ = r.MFRC522_Request(r.PICC_REQIDL)
        if status != r.MI_OK:
            return None
        status, uid = r.MFRC522_Anticoll()
        if status != r.MI_OK or len(uid) < 5:
            return None
        n = 0
        for b in uid[:5]:
            n = n * 256 + b
        return format(n, "X") if n else None


# ---------------------------------------------------------------- buzzer
class Buzzer:
    PATTERNS = {"present": [0.1], "late": [0.1, 0.1], "registration": [0.1, 0.1, 0.1],
                "checked_out": [0.1],
                "low_battery": [0.06, 0.06, 0.06]}         # quick beep-beep-beep, like a speaker

    def __init__(self, simulate: bool, pin: int):
        self._bz = None
        if not simulate:
            try:
                from gpiozero import Buzzer as GpioBuzzer
                self._bz = GpioBuzzer(pin)
            except Exception:
                log.exception("buzzer not available on GPIO %s", pin)

    def beep(self, result: str) -> None:
        pattern = self.PATTERNS.get(result, [0.6])  # long beep = rejected
        if not self._bz:
            print("  *" + " ".join("beep" if p < 0.5 else "BEEEEP" for p in pattern) + "*")
            return
        for p in pattern:
            self._bz.on(); time.sleep(p); self._bz.off(); time.sleep(0.08)


# ---------------------------------------------------------------- LEDs
class Leds:
    """Green = accepted (present / late / card registered), red = not accepted.
    Late blinks green twice so it looks different from present. Runs in the background
    (gpiozero), so the LCD and buzzer don't wait for it."""
    OK = ("present", "late", "registration", "checked_out")

    def __init__(self, simulate: bool, green_pin: int, red_pin: int):
        self._g = self._r = None
        if not simulate:
            try:
                from gpiozero import LED
                self._g, self._r = LED(green_pin), LED(red_pin)
            except Exception:
                log.exception("LEDs not available on GPIO %s / %s", green_pin, red_pin)

    @staticmethod
    def color_for(result: str) -> str:
        return "green" if result in Leds.OK else "red"

    def show(self, result: str, seconds: float = 2.0) -> None:
        color = self.color_for(result)
        if not self._g:
            print(f"  [{'GREEN' if color == 'green' else 'RED'} LED{' x2 blink' if result == 'late' else ''}]")
            return
        self._g.off(); self._r.off()
        if result == "late":
            self._g.blink(on_time=0.25, off_time=0.15, n=2, background=True)
        else:
            led = self._g if color == "green" else self._r
            led.blink(on_time=seconds, off_time=0, n=1, background=True)    # on for a while, then off

    def error(self) -> None:
        if self._r:
            self._r.blink(on_time=0.15, off_time=0.15, n=4, background=True)
        else:
            print("  [RED LED fast blink]")


# ---------------------------------------------------------------- main loop
def make_reader(backend: str):
    return RC522Reader() if backend == "rc522" else KeyboardReader()


def current_screen(conn):
    reg = card_registration.lcd_waiting_info(conn)
    return registration_waiting_screen(reg) if reg else idle_screen(battery=battery.get(conn))


def show_result_countdown(lcd, conn, lines):
    """Show each countdown second, then restore idle (or active registration)."""
    for seconds_left in range(Config.LCD_RESULT_SECONDS, 0, -1):
        lcd.show(countdown_screen(lines, seconds_left))
        time.sleep(1)
    lcd.show(current_screen(conn))


# ---------------------------------------------------------------- battery
class BatteryWatch:
    """Reads the UPS board every 30 s, shares it with the website, and shuts the Pi down
    cleanly when the cells are almost empty (protects the SD card).
    Low and not charging -> the buzzer warns like a Bluetooth speaker: beep-beep-beep every
    2 minutes at 20% and below, every 30 s at 10% and below; plugging in the charger stops it."""
    EVERY = 30
    BEEP_EVERY = {"low": 120, "critical": 30}          # seconds between low-battery beeps

    def __init__(self, conn, lcd, buzzer=None):
        self.conn, self.lcd, self.last, self.history = conn, lcd, 0.0, []
        self.buzzer, self.last_beep = buzzer, 0.0
        self.reader = battery.Reader(Config.BATTERY_BACKEND, Config.BATTERY_I2C_ADDRESS, Config.BATTERY_CELLS_SERIES,
                                     invert_current=Config.BATTERY_CURRENT_INVERT)
        self.power_pin = battery.PowerLossPin(Config.BATTERY_PLD_GPIO)

    def warn(self, percent, charging) -> None:
        """Low-battery beep, at most once per BEEP_EVERY for the current level."""
        if charging or self.buzzer is None or percent > battery.LOW:
            self.last_beep = 0.0                       # next time it gets low, beep right away
            return
        level = "critical" if percent <= battery.CRITICAL else "low"
        if time.time() - self.last_beep >= self.BEEP_EVERY[level]:
            self.last_beep = time.time()
            log.info("low battery warning beep (%.0f%%)", percent)
            self.buzzer.beep("low_battery")

    def tick(self) -> None:
        if Config.BATTERY_BACKEND == "none" or time.time() - self.last < self.EVERY:
            return
        self.last = time.time()
        reading = self.reader.read()
        if reading is None:
            return
        percent, charging, volts = reading
        if charging is None:
            charging = self.power_pin.charging()
        battery.save(self.conn, percent, charging, volts)
        self.history = (self.history + [percent])[-5:]
        self.warn(percent, charging)
        if Config.BATTERY_SHUTDOWN and battery.should_shut_down(self.history, charging):
            log.warning("battery at %.0f%% and not charging - shutting down safely", percent)
            self.lcd.show(shutdown_screen(int(percent)))
            import subprocess
            subprocess.run(["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"], check=False)


# ---------------------------------------------------------------- shutdown button
class ShutdownButton:
    """Case push button (pin 37 = GPIO26 to pin 39 = GND) with a confirmation step:

        press             -> LCD "Keep holding the button... 3s 2s 1s" right away
        let go early      -> back to the normal screen
        held 3 s          -> LCD "SHUT DOWN? Press again to confirm (10s)"
        press within 10 s -> shut down safely
        no press          -> cancelled, back to the normal screen

    so a bump or an accidental hold can never turn the device off by itself.

    The pin is READ by the main loop (poll(), several times a second), not watched with
    gpiozero callbacks: on newer Raspberry Pi kernels the RPi.GPIO backend can't do "edge
    detection" (callbacks) any more, so a callback button silently never worked there."""
    CONFIRM_SECONDS = 10

    def __init__(self, gpio: int | None, hold_seconds: float = 3, clock=time.monotonic):
        self._clock = clock
        self.hold_seconds = hold_seconds
        self.state, self.deadline, self.pressed_at = "idle", 0.0, 0.0
        self._read = None                              # -> True while the button is pressed
        self._was_down = False
        if gpio is None:
            return
        try:
            import RPi.GPIO as GPIO
            if GPIO.getmode() is None:
                GPIO.setmode(GPIO.BCM)
            pin = gpio if GPIO.getmode() == GPIO.BCM else {26: 37}.get(gpio, gpio)
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)   # pressed = connected to GND = 0
            self._read = lambda: GPIO.input(pin) == 0
            log.info("shutdown button ready on GPIO%s (hold %ss, then press to confirm)", gpio, hold_seconds)
        except Exception:
            log.exception("shutdown button not available on GPIO%s", gpio)

    def update(self, is_down: bool) -> None:
        """Feed the current button level; turns it into press / release / held-3-s events."""
        if is_down and not self._was_down:
            self._pressed()
        elif not is_down and self._was_down:
            self._released()
        elif is_down and self.state == "holding" and self._clock() - self.pressed_at >= self.hold_seconds:
            self._held()
        self._was_down = is_down

    def _pressed(self) -> None:
        if self.state == "idle":                       # start of a hold: show the countdown
            self.state, self.pressed_at = "holding", self._clock()
        elif self.state == "confirm" and self._clock() < self.deadline:
            self.state = "shutdown"                    # the second press confirms

    def _released(self) -> None:
        if self.state == "holding":                    # let go before 3 s: nothing happens
            self.state = "released"

    def _held(self) -> None:
        if self.state in ("idle", "holding"):
            self.state, self.deadline = "confirm", self._clock() + self.CONFIRM_SECONDS

    def seconds_left(self) -> int:
        """Confirm step: seconds until it cancels. Holding: seconds still to hold."""
        if self.state == "holding":
            return max(1, int(self.pressed_at + self.hold_seconds - self._clock() + 0.999))
        return max(0, int(self.deadline - self._clock() + 0.999))

    def poll(self) -> str:
        """'idle' | 'holding' | 'confirm' | 'cancelled' (once: let go early, or the 10 s ran
        out) | 'shutdown'"""
        if self._read is not None:
            try:
                self.update(self._read())
            except Exception:                          # never let the button stop card reading
                log.exception("shutdown button read failed")
                self._read = None
        if self.state == "released":
            self.state = "idle"
            return "cancelled"
        if self.state == "confirm" and self._clock() >= self.deadline:
            self.state = "idle"
            return "cancelled"
        return self.state


def shut_down_now(lcd) -> None:
    lcd.show(shutting_down_screen("off"))
    import subprocess
    subprocess.run(["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"], check=False)


def run() -> None:
    setup_logging(Config.LOG_DIR, "hardware", Config.LOG_LEVEL)
    upgrade_db(Config.DATABASE)
    conn = connect(Config.DATABASE)
    lcd = Lcd(Config.LCD_I2C_ADDRESS)
    buzzer = Buzzer(Config.SIMULATE_HARDWARE, Config.BUZZER_GPIO)
    leds = Leds(Config.SIMULATE_HARDWARE, Config.LED_GREEN_GPIO, Config.LED_RED_GPIO)
    reader = make_reader(Config.RFID_BACKEND)
    blocking = isinstance(reader, KeyboardReader)
    watch = BatteryWatch(conn, lcd, buzzer)
    button = ShutdownButton(None if Config.SIMULATE_HARDWARE else Config.SHUTDOWN_BUTTON_GPIO)
    log.info("RFID reader started: backend=%s battery=%s db=%s", Config.RFID_BACKEND, Config.BATTERY_BACKEND, Config.DATABASE)

    last_refresh = 0.0
    try:
        while True:
            watch.tick()
            pressed = button.poll()
            if pressed == "holding":                  # pressed: count down while it is held
                lcd.show(shutdown_holding_screen(button.seconds_left()))
                time.sleep(0.1)
                continue
            if pressed == "confirm":                  # waiting for the second press
                lcd.show(shutdown_confirm_screen(button.seconds_left()))
                time.sleep(0.2)
                continue
            if pressed == "cancelled":
                log.info("shutdown cancelled (not confirmed)")
                last_refresh = 0.0                    # back to the normal screen right away
            elif pressed == "shutdown":
                log.warning("shutdown button confirmed - shutting down safely")
                shut_down_now(lcd)
                while True:                           # systemd stops this process in a moment
                    time.sleep(1)
            if blocking or time.time() - last_refresh >= 1:   # clock / countdown refresh
                lcd.show(current_screen(conn))
                last_refresh = time.time()

            uid = reader.read_uid()
            if not uid:
                time.sleep(0.2)
                continue

            try:
                result = card_registration.handle_tap(conn, uid)
            except Exception:
                log.exception("tap failed for uid=%s", uid)
                lines = ["      ERROR", "Tap failed. Please", "try again.", ""]
                lcd.show(lines)
                leds.error()
                buzzer.beep("error")
                show_result_countdown(lcd, conn, lines)
                continue

            lines = screen_for_tap(result, card_registration.lcd_waiting_info(conn))
            lcd.show(lines)
            outcome = "registration" if result["kind"] == "registration" else result["result"]
            leds.show(outcome)                       # background: lights while the buzzer beeps
            buzzer.beep(outcome)
            show_result_countdown(lcd, conn, lines)
    except KeyboardInterrupt:
        log.info("RFID reader stopped")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
