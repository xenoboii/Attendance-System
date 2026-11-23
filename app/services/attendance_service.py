"""
One check-in path for both attendance methods.

    RFID tap    -> rfid_tap(conn, uid)            -> check_in(..., method="rfid")
    Web button  -> web_check_in(conn, student_id) -> check_in(..., method="web")
    Web page    -> get_button_status(conn, student_id)  (enabled / greyed out + message)

Everything goes through get_checkin_state(), so the RFID reader and the web
button are always enabled/disabled/late at exactly the same moments.
"""
import logging
import sqlite3
from datetime import datetime

from .checkin_state import DISABLED, get_checkin_state
from .sessions import ensure_sessions_for_date

log = logging.getLogger(__name__)

# Results
OK_PRESENT = "present"
OK_LATE = "late"
DUPLICATE = "duplicate"
NO_OPEN_SESSION = "no_open_session"
UNKNOWN_CARD = "unknown_card"
INVALID_CODE = "invalid_code"
# check-out (only while the instructor has check-out open, see sessions.open_checkout)
CHECKED_OUT = "checked_out"
ALREADY_CHECKED_OUT = "already_checked_out"
NOT_CHECKED_IN = "not_checked_in"


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def find_open_session(conn, student_id: int, now: datetime):
    """(session, state) the student can check in to right now, or (None, 'disabled').

    Candidates: sessions of sections the student is actively enrolled in that are
    today (auto mode) or currently enabled by the instructor (manual mode).
    If more than one is open, pick the one whose start time is closest to now.
    Manual sessions only stay open on their own date (except async activities),
    so a session the instructor forgot to close doesn't stay open forever.
    """
    ensure_sessions_for_date(conn, now.date())   # today's sessions from the weekly schedule
    rows = conn.execute(
        """
        SELECT cs.*, sub.code AS subject_code, sub.title AS subject_title,
               s.name AS section_name
        FROM class_sessions cs
        JOIN enrollments e ON e.section_id = cs.section_id
        JOIN sections    s ON s.id = cs.section_id
        JOIN subjects  sub ON sub.id = s.subject_id
        WHERE e.student_id = ?
          AND e.status = 'active' AND e.deleted_at IS NULL
          AND s.deleted_at IS NULL AND sub.deleted_at IS NULL AND sub.ended_at IS NULL
          AND cs.deleted_at IS NULL
          AND (
                (cs.checkin_mode = 'auto'   AND cs.session_date = ?)
             OR (cs.checkin_mode = 'manual' AND cs.manual_state <> 'disabled'
                 AND (cs.session_date = ? OR cs.type = 'async'))
          )
        """,
        (student_id, now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")),
    ).fetchall()

    open_sessions = []
    for row in rows:
        session = dict(row)
        state = get_checkin_state(session, now)
        if state != DISABLED:
            open_sessions.append((session, state))

    if not open_sessions:
        return None, DISABLED

    def distance(item):
        s = item[0]
        if not s["start_time"]:
            return float("inf")
        start = datetime.strptime(f'{s["session_date"]} {s["start_time"]}', "%Y-%m-%d %H:%M")
        return abs((now - start).total_seconds())

    return min(open_sessions, key=distance)


def find_checkout_session(conn, student_id: int, now: datetime):
    """Today's session of the student's classes where the instructor has check-out open, or None."""
    rows = conn.execute(
        """
        SELECT cs.*, sub.code AS subject_code, sub.title AS subject_title,
               s.name AS section_name
        FROM class_sessions cs
        JOIN enrollments e ON e.section_id = cs.section_id
        JOIN sections    s ON s.id = cs.section_id
        JOIN subjects  sub ON sub.id = s.subject_id
        WHERE e.student_id = ? AND e.status = 'active' AND e.deleted_at IS NULL
          AND s.deleted_at IS NULL AND sub.deleted_at IS NULL AND sub.ended_at IS NULL
          AND cs.deleted_at IS NULL AND cs.ended_at IS NULL
          AND cs.checkout_open_at IS NOT NULL AND cs.session_date = ?
        ORDER BY cs.checkout_open_at DESC
        """,
        (student_id, now.strftime("%Y-%m-%d")),
    ).fetchall()
    return dict(rows[0]) if rows else None


def check_out(conn, student_id: int, now: datetime | None = None) -> dict | None:
    """Check out of the class whose check-out is open. None when no check-out is open for
    this student (the caller then treats it as a normal check-in)."""
    now = now or datetime.now()
    session = find_checkout_session(conn, student_id, now)
    if session is None:
        return None
    out = {"result": None, "student": _student(conn, student_id), "session": session,
           "record": _existing_record(conn, session["id"], student_id)}
    rec = out["record"]
    if not rec or rec["status"] not in ("present", "late"):
        out["result"] = NOT_CHECKED_IN
        return out
    if rec["checked_out_at"]:
        out["result"] = ALREADY_CHECKED_OUT
        return out
    conn.execute("""UPDATE attendance SET checked_out_at = ?
                    WHERE session_id = ? AND student_id = ? AND deleted_at IS NULL""",
                 (_ts(now), session["id"], student_id))
    conn.commit()
    rec["checked_out_at"] = _ts(now)
    out["result"] = CHECKED_OUT
    log.info("check-out student=%s session=%s", student_id, session["id"])
    return out


def _existing_record(conn, session_id: int, student_id: int):
    row = conn.execute(
        """SELECT status, method, checked_in_at, checked_out_at FROM attendance
           WHERE session_id = ? AND student_id = ? AND deleted_at IS NULL""",
        (session_id, student_id),
    ).fetchone()
    return dict(row) if row else None


def _student(conn, student_id: int):
    row = conn.execute(
        "SELECT id, first_name, last_name, student_number FROM users WHERE id = ?",
        (student_id,),
    ).fetchone()
    return dict(row) if row else None


def check_in(conn, student_id: int, method: str, now: datetime | None = None,
             ip_address: str | None = None, device_info: str | None = None,
             code: str | None = None) -> dict:
    """Record attendance.

    Returns {"result", "student", "session", "record"}; "record" is the new
    attendance row, or the existing one when result == DUPLICATE.
    """
    now = now or datetime.now()
    out = {"result": None, "student": _student(conn, student_id),
           "session": None, "record": None}

    # check-out open for one of the student's classes -> this tap / press checks them out
    # (a student who never checked in there may still check in to another open class)
    co = check_out(conn, student_id, now) if method == "rfid" else None
    if co and co["result"] != NOT_CHECKED_IN:
        return co

    session, state = find_open_session(conn, student_id, now)
    if session is None:
        if co:
            return co                      # nothing to check out of, and no check-in open
        out["result"] = NO_OPEN_SESSION
        return out
    out["session"] = session

    existing = _existing_record(conn, session["id"], student_id)
    if existing:
        out.update(result=DUPLICATE, record=existing)
        return out

    # Web check-in must match the code shown on the instructor's screen (if one is set)
    if method == "web" and session["checkin_code"] and code != session["checkin_code"]:
        out["result"] = INVALID_CODE
        return out

    # Insert, or revive a record the instructor had cleared. If an active record appears
    # at the same instant (card tap + button press together), nothing changes -> duplicate.
    cur = conn.execute(
        """INSERT INTO attendance
               (session_id, student_id, status, method, checked_in_at, ip_address, device_info)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_id, student_id) DO UPDATE SET
               status = excluded.status, method = excluded.method, checked_in_at = excluded.checked_in_at,
               ip_address = excluded.ip_address, device_info = excluded.device_info,
               marked_by = NULL, deleted_at = NULL, signature_path = NULL
           WHERE attendance.deleted_at IS NOT NULL""",
        (session["id"], student_id, state, method, _ts(now), ip_address, device_info),
    )
    conn.commit()
    if cur.rowcount == 0:
        out.update(result=DUPLICATE, record=_existing_record(conn, session["id"], student_id))
        return out

    out["result"] = OK_PRESENT if state == "present" else OK_LATE
    out["record"] = {"status": state, "method": method, "checked_in_at": _ts(now)}
    log.info("check-in student=%s session=%s method=%s -> %s",
             student_id, session["id"], method, out["result"])
    return out


def get_button_status(conn, student_id: int, now: datetime | None = None) -> dict:
    """What the student's web dashboard should show for the check-in button.

    enabled  -> clickable;   variant = "present" (green) or "late" (orange)
    disabled -> greyed out;  variant = "closed" or "done" (already checked in)
    """
    now = now or datetime.now()
    co = find_checkout_session(conn, student_id, now)
    if co:
        rec = _existing_record(conn, co["id"], student_id)
        name = f"{co['subject_code']} {co['section_name']}"
        if rec and rec["status"] in ("present", "late"):
            if rec["checked_out_at"]:
                t = datetime.strptime(rec["checked_out_at"], "%Y-%m-%d %H:%M:%S")
                return {"enabled": False, "variant": "out", "session": co, "action": "checkout",
                        "label": "Checked out",
                        "message": f"You checked out of {name} at {t:%I:%M %p}. You can leave now."}
            return {"enabled": True, "variant": "checkout", "session": co, "action": "checkout",
                    "label": "Check out",
                    "message": (f"Check-out is open for {name}. Check out before you leave, or you'll "
                                "be marked ABSENT. Tapping your card works too.")}

    session, state = find_open_session(conn, student_id, now)

    if session is None:
        return {"enabled": False, "variant": "closed", "session": None,
                "label": "Check in",
                "message": "Check-in opens a few minutes before your class starts."}

    existing = _existing_record(conn, session["id"], student_id)
    if existing:
        t = datetime.strptime(existing["checked_in_at"], "%Y-%m-%d %H:%M:%S")
        how = "school ID" if existing["method"] == "rfid" else "web check-in"
        return {"enabled": False, "variant": "done", "session": session,
                "label": f"Checked in ({existing['status'].title()})",
                "message": (f"You're already marked {existing['status'].upper()} for "
                            f"{session['subject_code']} {session['section_name']} at "
                            f"{t:%I:%M %p} via {how}."
                            + (" Check-out is required: don't leave before your instructor opens "
                               "check-out, then check out here or tap your card."
                               if session.get("require_checkout") else ""))}

    if state == "present":
        return {"enabled": True, "variant": "present", "session": session,
                "label": "Check in (Present)",
                "message": f"{session['subject_code']} · {session['section_name']}"}

    return {"enabled": True, "variant": "late", "session": session,
            "label": "Check in (Late)",
            "message": "The grace period has ended. You will be marked LATE."}


def web_check_in(conn, student_id: int, code: str | None = None,
                 ip_address: str | None = None, device_info: str | None = None,
                 now: datetime | None = None) -> dict:
    return check_in(conn, student_id, "web", now, ip_address, device_info, code)


def web_check_out(conn, student_id: int, now: datetime | None = None) -> dict:
    """The dashboard's "Check out" button (no signature needed)."""
    return check_out(conn, student_id, now) or {
        "result": NO_OPEN_SESSION, "student": None, "session": None, "record": None}


def rfid_tap(conn, rfid_uid: str, now: datetime | None = None) -> dict:
    """Called by the RFID reader loop. Logs every tap to rfid_scans.

    Returns the same dict as check_in(), plus "uid". For an unregistered card
    result == UNKNOWN_CARD and student is None.
    """
    now = now or datetime.now()
    user = conn.execute(
        """SELECT id FROM users
           WHERE rfid_uid = ? AND role = 'student'
             AND is_active = 1 AND deleted_at IS NULL""",
        (rfid_uid,),
    ).fetchone()

    if user is None:
        out = {"result": UNKNOWN_CARD, "student": None, "session": None, "record": None}
    else:
        out = check_in(conn, user["id"], "rfid", now)
    out["uid"] = rfid_uid

    # (the scan log only knows the original results: check-out maps onto them)
    log_result = {OK_PRESENT: "ok", OK_LATE: "late", CHECKED_OUT: "ok",
                  ALREADY_CHECKED_OUT: "duplicate", NOT_CHECKED_IN: "no_open_session"
                  }.get(out["result"], out["result"])
    conn.execute(
        """INSERT INTO rfid_scans (rfid_uid, user_id, session_id, result, scanned_at)
           VALUES (?, ?, ?, ?, ?)""",
        (rfid_uid, user["id"] if user else None,
         out["session"]["id"] if out["session"] else None, log_result, _ts(now)),
    )
    conn.commit()
    log.info("rfid tap uid=%s -> %s", rfid_uid, out["result"])
    return out
