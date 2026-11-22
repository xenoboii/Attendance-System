"""
All settings in one place. Override any of them with environment variables,
e.g. on the Pi:   set SIMULATE_HARDWARE=0  and  RFID_BACKEND=rc522
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env_bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


_DATA_DIR = Path(os.environ.get("ATTENDANCE_DATA_DIR", BASE_DIR / "data"))


def _secret_key() -> str:
    """The key that signs login cookies. Made randomly the first time and kept in
    data/secret_key (git-ignored), so it is never in the source code - nobody can forge
    a login by reading the code. Delete the file to log everyone out."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    import secrets
    path = _DATA_DIR / "secret_key"
    try:
        key = path.read_text(encoding="utf-8").strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)                 # Pi: only the app's user can read it
    except OSError:
        pass
    return key


class Config:
    SECRET_KEY = _secret_key()

    # Data lives in data/ (git-ignored). Set ATTENDANCE_DATA_DIR to keep it
    # outside OneDrive on the laptop, e.g. C:\attendance-data
    DATA_DIR = _DATA_DIR
    DATABASE = DATA_DIR / "attendance.db"
    SIGNATURE_DIR = DATA_DIR / "signatures"
    REPORT_DIR = DATA_DIR / "reports"

    LOG_DIR = BASE_DIR / "logs"
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # Hardware
    SIMULATE_HARDWARE = _env_bool("SIMULATE_HARDWARE", "1")  # 1 on laptop, 0 on the Pi
    # "Sync time from this phone" really changes the clock only on the Pi (never the laptop's)
    TIME_SYNC_ENABLED = _env_bool("TIME_SYNC_ENABLED", "0" if SIMULATE_HARDWARE else "1")
    RFID_BACKEND = os.environ.get("RFID_BACKEND", "keyboard")  # keyboard | rc522
    LCD_I2C_ADDRESS = int(os.environ.get("LCD_I2C_ADDRESS", "0x27"), 16)
    # GPIO numbers are BCM numbers (not the physical pin numbers) - see docs/PI_SETUP.md
    BUZZER_GPIO = int(os.environ.get("BUZZER_GPIO", "18"))         # physical pin 12
    LED_GREEN_GPIO = int(os.environ.get("LED_GREEN_GPIO", "23"))   # physical pin 16 - accepted
    LED_RED_GPIO = int(os.environ.get("LED_RED_GPIO", "24"))       # physical pin 18 - rejected
    # shutdown push button: pin 37 (GPIO26) to GND. Empty = no button.
    SHUTDOWN_BUTTON_GPIO = (int(os.environ.get("SHUTDOWN_BUTTON_GPIO", "26"))
                            if os.environ.get("SHUTDOWN_BUTTON_GPIO", "26").strip() else None)

    # Battery % from the 18650 UPS board (see app/services/battery.py):
    #   none | max17040 (Geekworm X728-style, I2C 0x36) | ina219 (Waveshare-style, I2C 0x42)
    BATTERY_BACKEND = os.environ.get("BATTERY_BACKEND", "none").strip().lower()
    BATTERY_I2C_ADDRESS = int(os.environ["BATTERY_I2C_ADDRESS"], 16) if os.environ.get("BATTERY_I2C_ADDRESS") else None
    BATTERY_CELLS_SERIES = int(os.environ.get("BATTERY_CELLS_SERIES", "1"))   # 2 cells side by side = 1
    # ina219 current direction: the Waveshare UPS HAT (D) (chip at 0x43) reads charging as negative,
    # so it is flipped automatically there; BATTERY_CURRENT_INVERT=0/1 overrides it
    BATTERY_CURRENT_INVERT = _env_bool("BATTERY_CURRENT_INVERT", "1" if BATTERY_I2C_ADDRESS == 0x43 else "0")
    BATTERY_PLD_GPIO = int(os.environ["BATTERY_PLD_GPIO"]) if os.environ.get("BATTERY_PLD_GPIO") else None  # X728: 6
    BATTERY_SHUTDOWN = _env_bool("BATTERY_SHUTDOWN", "1")          # safe shutdown at 5% (not charging)
    LCD_RESULT_SECONDS = 2  # countdown after a tap, then return to the idle screen

    # Accounts
    INSTRUCTOR_REGISTRATION_CODE = os.environ.get("INSTRUCTOR_REGISTRATION_CODE", "PROF-2026")
    CSRF_ENABLED = True

    # Deleted items stay in "Recently deleted" this many days, then are removed for good
    TRASH_DAYS = 30
    # Signatures drawn at web check-in are deleted after this many days (saves SD card space).
    # The attendance record stays; reports then show the student's stored signature.
    CHECKIN_SIGNATURE_DAYS = 60

    # Addresses. Students use attendance.lan, instructors use prof.attendance.lan
    # (both point to the same Pi via dnsmasq). On the laptop: localhost / prof.localhost
    STUDENT_HOST = os.environ.get("STUDENT_HOST", "attendance.lan")
    PROF_HOSTS = {h.strip().lower() for h in os.environ.get(
        "PROF_HOSTS", "prof.attendance.lan,prof.localhost").split(",") if h.strip()}

    MAX_CONTENT_LENGTH = 2 * 1024 * 1024 + 64 * 1024  # 2 MB image + multipart form overhead
    BACKUP_MAX_BYTES = 300 * 1024 * 1024               # biggest backup file the admin can restore

    # Attendance report (PDF) - matches the school's "Student's Attendance Sheet" form.
    # Put the school seal at app/static/img/school_logo.png to show it in the header.
    REPORT_SCHOOL_LINES = ["Republic of the Philippines", "Laguna State Polytechnic University", "Province of Laguna"]
    REPORT_PAGE_INCHES = (8.27, 11.69)    # A4 portrait  (long bond would be (8.5, 13))
    REPORT_MARGINS_INCHES = (1.0, 1.0, 0.4, 0.45)    # left, right, top, bottom
    REPORT_MIN_ROWS = 40                  # the form has numbered rows 1-40
    # University name font (like the printed form). It's a Microsoft Office font, so it's
    # used from the computer if installed; otherwise a plain serif font is used.
    # On the Pi: copy your OLDENGL.TTF to app/static/fonts/ (not included in the project).
    REPORT_SCHOOL_FONT_FILES = [
        "C:/Windows/Fonts/OLDENGL.TTF",
        str(BASE_DIR / "app" / "static" / "fonts" / "OLDENGL.TTF"),
        "/usr/share/fonts/truetype/oldengl/OLDENGL.TTF",
    ]
