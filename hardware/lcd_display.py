"""
4x20 I2C character LCD (HD44780 + PCF8574 backpack) for RFID feedback.

Wiring (I2C):  VCC -> 5V (pin 4) | GND -> GND (pin 9) | SDA -> GPIO2 (pin 3) | SCL -> GPIO3 (pin 5)
               (full pin map: docs/PI_SETUP.md; SDA/SCL are shared with the DS3231 and the UPS board)
Enable I2C:    sudo raspi-config -> Interface Options -> I2C
Find address:  sudo apt install i2c-tools && i2cdetect -y 1     (usually 0x27 or 0x3F)
Library:       pip install RPLCD smbus2

On a PC without the LCD, Lcd() falls back to printing the 4 lines in the console.
"""
from datetime import datetime

COLS, ROWS = 20, 4


def _fit(text: str) -> str:
    return (text or "")[:COLS].ljust(COLS)


def _center(text: str) -> str:
    return (text or "")[:COLS].center(COLS)


def _name(student: dict | None) -> str:
    if not student:
        return ""
    full = f'{student["last_name"]}, {student["first_name"]}'
    if len(full) <= COLS:
        return full
    return f'{student["last_name"]}, {student["first_name"][0]}.'[:COLS]


def _class(session: dict | None) -> str:
    if not session:
        return ""
    return f'{session["subject_code"]} {session["section_name"]}'


def _time(ts: str) -> str:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%I:%M %p")


# ---------------------------------------------------------------- battery icon
# One LCD cell is 5x8 pixels and the LCD holds 8 self-drawn characters (CGRAM 0-7).
# Screens use these visible stand-ins (the dev page / console show them as they are);
# Lcd.show() swaps them for the drawn icons on the real LCD.
BATTERY_GLYPHS = ["▯", "▂", "▄", "▆", "█"]   # ▯ ▂ ▄ ▆ █ : empty .. full
CHARGING_GLYPH = "⚡"                                               # ⚡


def _battery_bitmap(fill: int) -> list[int]:
    """Upright battery: cap, frame, 4 inside rows filled from the bottom (0-4)."""
    inside = [0b11111 if row >= 4 - fill else 0b10001 for row in range(4)]
    return [0b01110, 0b11111, *inside, 0b11111, 0b00000]


BOLT_BITMAP = [0b00010, 0b00100, 0b01000, 0b11111, 0b00010, 0b00100, 0b01000, 0b00000]
CUSTOM_CHARS = [_battery_bitmap(f) for f in range(5)] + [BOLT_BITMAP]     # CGRAM slots 0-5
_TO_CGRAM = {ord(g): chr(i) for i, g in enumerate(BATTERY_GLYPHS + [CHARGING_GLYPH])}


def battery_icon(battery: dict | None) -> str:
    """One character: ⚡ while charging, else a battery with 0-4 bars; ' ' with no monitor."""
    if not battery:
        return " "
    if battery.get("charging"):
        return CHARGING_GLYPH
    p = battery["percent"]
    bars = 4 if p >= 88 else 3 if p >= 63 else 2 if p >= 38 else 1 if p >= 13 else 0
    return BATTERY_GLYPHS[bars]


def battery_line(battery: dict | None) -> str:
    """Line 3 of the idle screen: only a warning when the battery is nearly empty and not
    charging ('LOW BATTERY 9%'); otherwise empty (the icon on line 1 shows the level)."""
    if battery and not battery.get("charging") and battery.get("level") == "critical":
        return f"LOW BATTERY {battery['percent']}%"
    return ""


def idle_screen(now: datetime | None = None, battery: dict | None = None) -> list[str]:
    """Line 1: ' ATTENDANCE SYSTEM' with the battery icon in the last column."""
    now = now or datetime.now()
    return [
        _fit(" ATTENDANCE SYSTEM")[:COLS - 1] + battery_icon(battery),
        _center(now.strftime("%a %b %d  %I:%M %p")),
        _center(battery_line(battery)),
        _center("TAP YOUR ID"),
    ]


def shutdown_screen(percent: int) -> list[str]:
    return [_center("BATTERY EMPTY"), _center(f"Battery {percent}%"), _center("Shutting down..."), _center("Plug in to charge")]


def loading_screen() -> list[str]:
    """A few seconds after power-on (hardware/power_screen.py boot), until the reader is ready
    and shows TAP YOUR ID. Without it the LCD is blank / black boxes for ~30-60 s."""
    return [_center("LOADING"), _center("Please wait..."), "", _center("About 1 minute")]


# ---------------------------------------------------------------- shutdown button
def shutdown_holding_screen(seconds_left: int) -> list[str]:
    """Shown the moment the shutdown button is pressed, so it's clear it was noticed."""
    return [_center("SHUT DOWN"), _center("Keep holding the"), _center("button..."),
            _center(f"{seconds_left}s")]


def shutdown_confirm_screen(seconds_left: int) -> list[str]:
    """After holding the shutdown button 3 s. Nothing happens unless it is pressed again."""
    return [_center("SHUT DOWN?"), _center("Press the button"), _center("again to confirm"),
            _center(f"Cancels in {seconds_left}s")]


def shutting_down_screen(kind: str = "off") -> list[str]:
    """While the Pi closes everything (button, low battery, or a command)."""
    if kind == "reboot":
        return [_center("RESTARTING"), _center("Please wait..."), "", _center("Back in about 1 min")]
    return [_center("SHUTTING DOWN"), _center("Saving data..."), _center("Do NOT switch off"), _center("yet")]


def safe_off_screen() -> list[str]:
    """Last screen of a shutdown (hardware/power_screen.py). The LCD keeps showing it after the
    Pi has stopped, because the screen still has power until the switch is flipped."""
    return [_center("SHUT DOWN"), _center("Safe to switch OFF"), "", _center("Switch ON to start")]


def result_screen(r: dict) -> list[str]:
    """Turn an rfid_tap() result into 4 LCD lines."""
    res = r["result"]

    if res == "present":
        lines = ["CHECKED IN: PRESENT", _name(r["student"]), _class(r["session"]),
                 f'Time: {_time(r["record"]["checked_in_at"])}']
    elif res == "late":
        lines = ["CHECKED IN: LATE", _name(r["student"]), _class(r["session"]),
                 f'Time: {_time(r["record"]["checked_in_at"])}']
    elif res == "duplicate":
        rec = r["record"]
        lines = ["ALREADY CHECKED IN", _name(r["student"]),
                 f'Marked {rec["status"].upper()}', f'at {_time(rec["checked_in_at"])}']
    elif res == "unknown_card":
        lines = ["CARD NOT REGISTERED", "Please enroll your", "card first.",
                 f'ID: {r["uid"]}']
    elif res == "no_open_session":
        lines = ["CHECK-IN CLOSED", _name(r["student"]), "No class is open", "right now."]
    elif res == "checked_out":
        lines = ["CHECKED OUT", _name(r["student"]), _class(r["session"]),
                 f'Time: {_time(r["record"]["checked_out_at"])}']
    elif res == "already_checked_out":
        lines = ["ALREADY CHECKED OUT", _name(r["student"]), "Checked out",
                 f'at {_time(r["record"]["checked_out_at"])}']
    elif res == "not_checked_in":
        lines = ["NOT CHECKED IN", _name(r["student"]), "Nothing to check out", "of in this class."]
    else:
        lines = ["ERROR", res or "", "", ""]

    return [_center(lines[0])] + [_fit(l) for l in lines[1:]]


def countdown_screen(lines: list[str], seconds_left: int) -> list[str]:
    """Keep the scan result visible with a countdown at the bottom right."""
    result = [_fit(line) for line in lines[:ROWS]]
    result.extend([" " * COLS] * (ROWS - len(result)))
    suffix = f" {seconds_left}s"
    result[-1] = result[-1][:COLS - len(suffix)] + suffix
    return result


def registration_waiting_screen(reg: dict) -> list[str]:
    """Shown while a student's card registration is in progress (refresh every second)."""
    if reg["status"] == "waiting":
        return [_center("CARD REGISTRATION"), _fit(_name(reg)),
                _fit("Tap your card now"), _fit(f'Time left: {reg["seconds_left"]}s')]
    return [_center("CARD DETECTED"), _fit(_name(reg)),
            _fit("Approve it on the"), _fit(f'website ({reg["seconds_left"]}s)')]


def registration_done_screen(student: dict, ok: bool) -> list[str]:
    if ok:
        return [_center("CARD REGISTERED"), _fit(_name(student)),
                _fit("You can now tap to"), _fit("check in.")]
    return [_center("REGISTRATION ENDED"), _fit(_name(student)),
            _fit("Cancelled or timed"), _fit("out. Try again.")]


def screen_for_tap(result: dict, reg_info: dict | None) -> list[str]:
    """LCD lines for a card_registration.handle_tap() result.

    reg_info = card_registration.lcd_waiting_info(conn) right after the tap.
    """
    if result.get("kind") == "registration" and reg_info:
        return registration_waiting_screen(reg_info)
    if result.get("already_registered"):
        return already_registered_screen(result)
    return result_screen(result)


_SHORT_RESULT = {"present": "Checked in: PRESENT", "late": "Checked in: LATE",
                 "duplicate": "Already checked in", "no_open_session": "No class open now",
                 "checked_out": "Checked out", "already_checked_out": "Already checked out",
                 "not_checked_in": "Not checked in"}


def already_registered_screen(r: dict) -> list[str]:
    """A card that belongs to someone was tapped while another student is registering a card.
    Serves both people: the card's owner still sees their name and check-in result, and the
    student registering sees that this card can't be used and should tap a different one."""
    return [_center("ALREADY REGISTERED"), _fit(_name(r.get("student"))),
            _fit(_SHORT_RESULT.get(r.get("result"), "")), _fit("Tap a different card")]


class Lcd:
    def __init__(self, address: int = 0x27, port: int = 1):
        try:
            from RPLCD.i2c import CharLCD
            self._lcd = CharLCD("PCF8574", address, port=port, cols=COLS, rows=ROWS)
            self._lcd.clear()
        except Exception:
            self._lcd = None  # not on the Pi / LCD not connected -> console fallback
            return
        try:                  # draw the battery / charging icons into the LCD's own memory
            for slot, bitmap in enumerate(CUSTOM_CHARS):
                self._lcd.create_char(slot, bitmap)
        except Exception:
            pass              # icons are optional; text still works

    def show(self, lines: list[str]) -> None:
        if self._lcd:
            for row, text in enumerate(lines[:ROWS]):
                self._lcd.cursor_pos = (row, 0)
                self._lcd.write_string(_fit(text).translate(_TO_CGRAM))
        else:
            print("+" + "-" * COLS + "+")
            for text in lines[:ROWS]:
                print("|" + _fit(text) + "|")
            print("+" + "-" * COLS + "+")
