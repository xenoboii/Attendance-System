"""
Student self-service RFID card registration ("wifi vendo" style), one at a time.

Web (student)                      Hardware (RFID loop)
-------------                      --------------------
start(student)  -> waiting  60s    LCD: "Tap your card now"
poll get_status() every 1s         card tapped -> handle_tap() -> detected
  shows "Card detected. Approve?"  LCD: "Confirm on website"
confirm() -> card saved            LCD: "CARD REGISTERED"
retry()   -> back to waiting
cancel()  -> lock released

While someone is registering, other students get "busy" with the seconds left.
Taps of ALREADY-registered cards still count as attendance during registration,
so registering never blocks a class that is checking in.
"""
import logging
import sqlite3
from datetime import datetime, timedelta

from . import attendance_service as attendance

log = logging.getLogger(__name__)

TAP_SECONDS = 60       # time to tap the card after clicking "Register card"
APPROVE_SECONDS = 60   # time to approve after the card is detected

# start() reasons
STARTED, RESUMED, BUSY, HAS_CARD = "started", "resumed", "busy", "already_has_card"

# shown on the registration page when a card that belongs to an account is tapped
ALREADY_REGISTERED = ("Card {uid} is already registered to another account. Tap a different card, "
                      "or ask the admin to remove it from that account first.")


def _ts(dt): return dt.strftime("%Y-%m-%d %H:%M:%S")
def _dt(s): return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _expire_stale(conn, now):
    conn.execute(
        """UPDATE card_registrations
           SET status = 'expired', active_lock = NULL, finished_at = ?
           WHERE active_lock = 1 AND expires_at <= ?""",
        (_ts(now), _ts(now)),
    )
    conn.commit()


def _active(conn, now):
    _expire_stale(conn, now)
    row = conn.execute(
        """SELECT cr.*, u.first_name, u.last_name
           FROM card_registrations cr JOIN users u ON u.id = cr.student_id
           WHERE cr.active_lock = 1"""
    ).fetchone()
    return dict(row) if row else None


def _view(reg, now):
    reg = dict(reg)
    reg["seconds_left"] = max(0, int((_dt(reg["expires_at"]) - now).total_seconds()))
    return reg


def start(conn, student_id: int, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    user = conn.execute("SELECT rfid_uid FROM users WHERE id = ?", (student_id,)).fetchone()
    if user["rfid_uid"]:
        return {"ok": False, "reason": HAS_CARD,
                "message": "You already have a registered card. Ask the admin to replace it."}

    active = _active(conn, now)
    if active:
        if active["student_id"] == student_id:            # page refreshed
            return {"ok": True, "reason": RESUMED, "registration": _view(active, now)}
        left = _view(active, now)["seconds_left"]
        return {"ok": False, "reason": BUSY, "seconds_left": left,
                "message": ("The card reader is being used by another student. "
                            f"Please wait until the current session is finished (about {left}s).")}

    try:
        conn.execute(
            """INSERT INTO card_registrations
                   (student_id, status, active_lock, started_at, expires_at)
               VALUES (?, 'waiting', 1, ?, ?)""",
            (student_id, _ts(now), _ts(now + timedelta(seconds=TAP_SECONDS))),
        )
        conn.commit()
    except sqlite3.IntegrityError:                        # two clicks in the same instant
        return start(conn, student_id, now)

    return {"ok": True, "reason": STARTED, "registration": _view(_active(conn, now), now)}


def get_status(conn, student_id: int, now: datetime | None = None) -> dict | None:
    """Latest registration of this student (for the polling page)."""
    now = now or datetime.now()
    _expire_stale(conn, now)
    row = conn.execute(
        """SELECT * FROM card_registrations WHERE student_id = ?
           ORDER BY id DESC LIMIT 1""",
        (student_id,),
    ).fetchone()
    return _view(row, now) if row else None


def confirm(conn, student_id: int, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    reg = _active(conn, now)
    if not reg or reg["student_id"] != student_id or reg["status"] != "detected":
        return {"ok": False, "message": "Nothing to approve. The session may have expired."}
    try:
        conn.execute("UPDATE users SET rfid_uid = ?, updated_at = ? WHERE id = ?",
                     (reg["detected_uid"], _ts(now), student_id))
    except sqlite3.IntegrityError:
        conn.rollback()
        return {"ok": False, "message": "That card was just registered to another account."}
    conn.execute(
        """UPDATE card_registrations
           SET status = 'confirmed', active_lock = NULL, finished_at = ?
           WHERE id = ?""",
        (_ts(now), reg["id"]),
    )
    conn.commit()
    return {"ok": True, "uid": reg["detected_uid"], "message": "Your card is now registered."}


def retry(conn, student_id: int, now: datetime | None = None) -> dict:
    """'That's not my card' -> wait for another tap."""
    now = now or datetime.now()
    reg = _active(conn, now)
    if not reg or reg["student_id"] != student_id:
        return {"ok": False, "message": "No active registration."}
    conn.execute(
        """UPDATE card_registrations
           SET status = 'waiting', detected_uid = NULL, message = NULL,
               detected_at = NULL, expires_at = ?
           WHERE id = ?""",
        (_ts(now + timedelta(seconds=TAP_SECONDS)), reg["id"]),
    )
    conn.commit()
    return {"ok": True}


def cancel(conn, student_id: int, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    conn.execute(
        """UPDATE card_registrations
           SET status = 'cancelled', active_lock = NULL, finished_at = ?
           WHERE student_id = ? AND active_lock = 1""",
        (_ts(now), student_id),
    )
    conn.commit()
    return {"ok": True}


def handle_tap(conn, uid: str, now: datetime | None = None) -> dict:
    """Single entry point for EVERY card tap from the reader.

    Returns {"kind": "attendance" | "registration", ...}
    """
    now = now or datetime.now()
    owner = conn.execute(
        "SELECT id FROM users WHERE rfid_uid = ? AND deleted_at IS NULL", (uid,)
    ).fetchone()
    reg = _active(conn, now)

    # A card that already belongs to an account was tapped while someone is registering:
    # it still counts as that account's attendance (a classmate may be checking in), but the
    # student who is registering is told, instead of the page just waiting for nothing.
    already_registered = bool(owner and reg and reg["status"] == "waiting"
                              and owner["id"] != reg["student_id"])
    if already_registered:
        conn.execute("UPDATE card_registrations SET message = ? WHERE id = ?",
                     (ALREADY_REGISTERED.format(uid=uid), reg["id"]))
        conn.commit()
        log.info("card registration: uid=%s is already registered (student=%s)", uid, reg["student_id"])

    # Registered card (even during a registration), or no registration waiting
    # -> normal attendance; an unknown card here gets "Please enroll your card first"
    if owner or not reg or reg["status"] != "waiting":
        out = attendance.rfid_tap(conn, uid, now)
        out["kind"] = "attendance"
        out["already_registered"] = already_registered     # LCD: "ALREADY REGISTERED" screen
        return out

    # Unknown card while a registration is waiting -> this is the card being registered
    conn.execute(
        """UPDATE card_registrations
           SET status = 'detected', detected_uid = ?, detected_at = ?, expires_at = ?
           WHERE id = ?""",
        (uid, _ts(now), _ts(now + timedelta(seconds=APPROVE_SECONDS)), reg["id"]),
    )
    conn.execute(
        """INSERT INTO rfid_scans (rfid_uid, user_id, result, scanned_at)
           VALUES (?, ?, 'card_registration', ?)""",
        (uid, reg["student_id"], _ts(now)),
    )
    conn.commit()
    log.info("card registration: uid=%s detected for student=%s", uid, reg["student_id"])
    return {"kind": "registration", "result": "detected", "uid": uid,
            "student": {"first_name": reg["first_name"], "last_name": reg["last_name"]}}


def lcd_waiting_info(conn, now: datetime | None = None) -> dict | None:
    """For the LCD idle loop: show the registration countdown instead of the clock."""
    now = now or datetime.now()
    reg = _active(conn, now)
    return _view(reg, now) if reg else None
