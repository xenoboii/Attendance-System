"""
Device clock: backup for the RTC module.

The Pi has no internet, so if the RTC battery dies or the module fails, its clock is wrong after
a power cut - and present / late / absent all depend on the time. The instructor's phone always
knows the right time, so the prof site compares the two and offers "Sync time from this phone".

Setting the system clock needs root. The website does NOT run as root; install.sh installs a tiny
helper (/usr/local/sbin/attendance-set-time) that only accepts a number of seconds, and a sudo
rule that lets the website run exactly that helper and nothing else.
On the laptop (SIMULATE_HARDWARE=1) nothing is changed - the laptop's clock is left alone.
"""
import logging
import subprocess
import time
from datetime import datetime

from .accounts import ValidationError

log = logging.getLogger(__name__)

HELPER = "/usr/local/sbin/attendance-set-time"
WARN_SECONDS = 120            # warn when the device and the phone differ by more than 2 minutes
AUTO_SECONDS = 60             # correct by itself (instructor's device only) beyond 1 minute
EARLIEST = datetime(2025, 1, 1).timestamp()
LATEST = datetime(2100, 1, 1).timestamp()


def status(conn) -> dict:
    """What the device thinks the time is, plus a hint if it is obviously wrong
    (earlier than a check-in that was already recorded)."""
    now = time.time()
    last = conn.execute("SELECT MAX(checked_in_at) FROM attendance").fetchone()[0]
    behind = False
    if last:
        try:
            behind = datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S").timestamp() > now + 60
        except ValueError:
            pass
    return {"now_ms": int(now * 1000), "behind_last_record": behind, "last_record": last}


def auto_decision(epoch_ms, *, class_open: bool) -> str:
    """Automatic correction from an instructor's device (no button pressed):
    'ok' = clock is close enough, 'held' = a class is taking check-ins so a person must
    decide, 'sync' = go ahead."""
    try:
        offset = int(epoch_ms) / 1000 - time.time()
    except (TypeError, ValueError):
        raise ValidationError({"time": "Invalid time from the phone."})
    if abs(offset) <= AUTO_SECONDS:
        return "ok"
    return "held" if class_open else "sync"


def set_time(epoch_ms, *, enabled: bool) -> dict:
    """Set the device clock to the phone's time. Returns {'changed': bool, 'simulated': bool,
    'offset_s': seconds the clock moved}."""
    try:
        target = int(epoch_ms) / 1000
    except (TypeError, ValueError):
        raise ValidationError({"time": "Invalid time from the phone."})
    if not EARLIEST <= target <= LATEST:
        raise ValidationError({"time": "The phone's date looks wrong. Check the phone's date and time."})
    offset = target - time.time()
    if not enabled:
        log.info("time sync simulated (laptop clock untouched), offset %.1f s", offset)
        return {"changed": False, "simulated": True, "offset_s": round(offset)}
    try:
        out = subprocess.run(["sudo", "-n", HELPER, str(int(round(target)))], check=True,
                             capture_output=True, text=True, timeout=10)
        err = getattr(out, "stderr", "") or ""
        if "NOT saved" in err:                    # clock set, but the RTC module didn't take it
            log.warning("time sync: %s", err.strip())
    except FileNotFoundError:
        raise ValidationError({"time": "Time sync isn't installed on this device (run deploy/install.sh)."})
    except subprocess.CalledProcessError as e:
        log.error("time sync failed: %s", (e.stderr or e.stdout or "").strip())
        raise ValidationError({"time": "The device refused to change its clock. See logs/app.log."})
    except subprocess.TimeoutExpired:
        raise ValidationError({"time": "Setting the clock took too long. Try again."})
    log.warning("device clock set from instructor's phone, moved %.1f s", offset)
    return {"changed": True, "simulated": False, "offset_s": round(offset)}
