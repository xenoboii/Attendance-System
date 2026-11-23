"""
Device settings the admin can change from the website:

  * Instructor registration code - what new instructors type to create an account
    (stored in the database; falls back to INSTRUCTOR_REGISTRATION_CODE in /etc/attendance.env)
  * Hotspot Wi-Fi name and password - applied through a tiny root helper,
    /usr/local/sbin/attendance-set-wifi (deploy/device-settings.sh installs it), which can ONLY
    change the attendance-ap hotspot. Every phone is disconnected and has to join again.
"""
import logging
import os
import re
import subprocess

from . import app_settings
from .accounts import ValidationError

log = logging.getLogger(__name__)

CODE_KEY = "instructor_registration_code"
CODE_RE = re.compile(r"^[A-Za-z0-9._-]{4,30}$")
WIFI_HELPER = "/usr/local/sbin/attendance-set-wifi"
HOTSPOT = "attendance-ap"
NAME_RE = re.compile(r"^[A-Za-z0-9 ._()@#&+-]{1,32}$")       # plain characters every phone shows right
PRINTABLE = re.compile(r"^[\x20-\x7e]{8,63}$")                # WPA2: 8-63 printable characters


# ---------------------------------------------------------------- instructor registration code
def instructor_code(conn, default: str) -> str:
    return app_settings.get(conn, CODE_KEY) or default


def set_instructor_code(conn, actor_id, text) -> str:
    code = str(text or "").strip()
    if not CODE_RE.match(code):
        raise ValidationError({"code": "Use 4-30 letters, numbers, dots, dashes or underscores (no spaces)."})
    from .classes import audit
    app_settings.put(conn, CODE_KEY, code)
    audit(conn, actor_id, "set_instructor_code", "app_settings", 0)       # the code itself isn't logged
    conn.commit()
    return code


# ---------------------------------------------------------------- hotspot Wi-Fi
def wifi_available() -> bool:
    """The helper is installed (on the Pi, after deploy/device-settings.sh)."""
    return os.path.exists(WIFI_HELPER)


def wifi_name() -> str | None:
    """Current hotspot name, or None if it can't be read (laptop, no hotspot yet)."""
    try:
        out = subprocess.run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", HOTSPOT],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    name = out.stdout.strip()
    return name if out.returncode == 0 and name else None


def validate_wifi(name, password, confirm) -> tuple[str, str]:
    name, password = str(name or "").strip(), str(password or "")
    errors = {}
    if not NAME_RE.match(name):
        errors["wifi_name"] = "Use 1-32 letters, numbers, spaces or . _ - ( ) @ # & +"
    if not PRINTABLE.match(password):
        errors["wifi_password"] = "Use 8-63 characters (letters, numbers, symbols; no special accents)."
    elif password != str(confirm or ""):
        errors["wifi_confirm"] = "The passwords don't match."
    if errors:
        raise ValidationError(errors)
    return name, password


def set_wifi(conn, actor_id, name, password, confirm, *, runner=subprocess.run) -> str:
    """Change the hotspot name + password. The helper applies it ~4 s later, so this page can
    still answer before every phone (including the admin's) is disconnected."""
    name, password = validate_wifi(name, password, confirm)
    if not wifi_available():
        raise ValidationError({"wifi_name": "This device can't change the hotspot yet. On the Pi run: "
                                            "bash deploy/device-settings.sh"})
    try:
        out = runner(["sudo", "-n", WIFI_HELPER, name, password], capture_output=True, text=True, timeout=20)
    except Exception as e:
        log.exception("hotspot change failed")
        raise ValidationError({"wifi_name": f"Couldn't change the hotspot: {e}"})
    if out.returncode != 0:
        log.warning("hotspot change failed: %s", (out.stderr or out.stdout).strip())
        raise ValidationError({"wifi_name": "Couldn't change the hotspot: "
                                            + ((out.stderr or out.stdout).strip() or "unknown error")})
    from .classes import audit
    audit(conn, actor_id, "set_hotspot", "app_settings", 0, {"name": name})   # never the password
    conn.commit()
    log.info("hotspot renamed to %r by user %s", name, actor_id)
    return name
