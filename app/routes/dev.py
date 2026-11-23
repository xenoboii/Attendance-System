"""
DEV TEST PAGE  (/dev) - only enabled when SIMULATE_HARDWARE=1.

Lets you test everything without the RFID reader or LCD:
  - tap any card UID (fake RFID) and see the 4x20 LCD screen it would show
  - press the web check-in button as any student
  - run a card registration (start / tap / approve / retry / cancel)
  - switch a session between auto and manual (present / late / disabled)
"""
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from hardware.lcd_display import countdown_screen, idle_screen, registration_waiting_screen, screen_for_tap

from ..db import get_db
from ..services import attendance_service as attendance
from ..services import battery, card_registration
from ..services.checkin_state import get_checkin_state, get_checkin_window

bp = Blueprint("dev", __name__, url_prefix="/dev")

LOCAL_ADDRS = {"127.0.0.1", "::1"}


@bp.before_request
def only_this_computer():
    """The dev page is its own separate tool: it is not linked in the student or instructor
    portals, and it only answers on the computer running the app (http://localhost:5000/dev).
    Phones / laptops on the classroom network get "not found"."""
    from flask import abort
    if request.remote_addr not in LOCAL_ADDRS:
        abort(404)


def _lcd_now(db, now):
    """What the LCD would show right now if no tap just happened."""
    reg = card_registration.lcd_waiting_info(db, now)
    return registration_waiting_screen(reg) if reg else idle_screen(now, battery.get(db, now))


@bp.post("/battery")
def battery_set():
    """Fake a UPS board reading (what hardware/rfid_reader.py saves on the Pi)."""
    db = get_db()
    if request.form.get("clear"):
        battery.clear(db)
        flash("Battery reading removed (no monitor).")
    else:
        try:
            pct = int(request.form.get("percent", ""))
        except ValueError:
            flash("Type a percentage 0-100.")
            return redirect(url_for(".index"))
        battery.save(db, pct, charging=bool(request.form.get("charging")), voltage=None)
        flash(f"Battery set to {pct}%{' (charging)' if request.form.get('charging') else ''}.")
    return redirect(url_for(".index"))


@bp.get("/")
def index():
    db, now = get_db(), datetime.now()

    students = []
    for s in db.execute(
        """SELECT id, student_number, first_name, last_name, rfid_uid FROM users
           WHERE role = 'student' AND deleted_at IS NULL ORDER BY last_name"""
    ):
        students.append({
            "s": s,
            "button": attendance.get_button_status(db, s["id"], now),
            "reg": card_registration.get_status(db, s["id"], now),
        })

    sessions = []
    for row in db.execute(
        """SELECT cs.*, sub.code AS subject_code, s.name AS section_name
           FROM class_sessions cs
           JOIN sections s ON s.id = cs.section_id
           JOIN subjects sub ON sub.id = s.subject_id
           WHERE cs.deleted_at IS NULL
             AND (cs.session_date = ? OR cs.checkin_mode = 'manual')
           ORDER BY cs.start_time""",
        (now.strftime("%Y-%m-%d"),),
    ):
        cs = dict(row)
        sessions.append({"cs": cs, "state": get_checkin_state(cs, now),
                         "window": get_checkin_window(cs)})

    scans = db.execute(
        """SELECT r.scanned_at, r.rfid_uid, r.result, u.first_name, u.last_name
           FROM rfid_scans r LEFT JOIN users u ON u.id = r.user_id
           ORDER BY r.id DESC LIMIT 10"""
    ).fetchall()

    records = db.execute(
        """SELECT a.checked_in_at, a.status, a.method, u.first_name, u.last_name, cs.type
           FROM attendance a
           JOIN users u ON u.id = a.student_id
           JOIN class_sessions cs ON cs.id = a.session_id
           WHERE a.deleted_at IS NULL ORDER BY a.id DESC LIMIT 20"""
    ).fetchall()

    ready = _lcd_now(db, now)
    result = session.pop("lcd", None)
    frames = [countdown_screen(result, seconds)
              for seconds in range(current_app.config["LCD_RESULT_SECONDS"], 0, -1)] if result else []
    lcd = frames[0] if frames else ready
    return render_template("dev/index.html", now=now, lcd=lcd, students=students,
                           lcd_preview={"frames": frames, "ready": ready},
                           sessions=sessions, scans=scans, records=records, battery=battery.get(db, now))


@bp.get("/lcd")
def lcd_status():
    """Refresh the ready screen after the simulated scan countdown finishes."""
    return {"lines": _lcd_now(get_db(), datetime.now())}, {"Cache-Control": "no-store"}


WATCH_DIRS = ("templates", "static", "routes", "services")


@bp.get("/livereload")
def livereload():
    """Newest modification time of the app's files. The browser reloads when it changes
    (see static/js/livereload.js) - so you never have to press refresh while coding."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    newest = 0.0
    for d in WATCH_DIRS:
        for p in (root / d).rglob("*"):
            if p.is_file() and "vendor" not in p.parts:
                newest = max(newest, p.stat().st_mtime)
    return {"v": newest}


@bp.post("/tap")
def tap():
    db = get_db()
    uid = request.form.get("uid", "").strip().upper()
    if not uid:
        flash("Type a card UID first.")
        return redirect(url_for(".index"))
    result = card_registration.handle_tap(db, uid)
    session["lcd"] = screen_for_tap(result, card_registration.lcd_waiting_info(db))
    flash(f"Tapped {uid}: {result['kind']} -> {result['result']}")
    return redirect(url_for(".index"))


@bp.post("/web-checkin/<int:student_id>")
def web_checkin(student_id):
    r = attendance.web_check_in(
        get_db(), student_id, code=request.form.get("code") or None,
        ip_address=request.remote_addr, device_info=request.user_agent.string,
    )
    flash(f"Web check-in for student {student_id}: {r['result']}")
    return redirect(url_for(".index"))


@bp.post("/card/<int:student_id>/<action>")
def card(student_id, action):
    actions = {"start": card_registration.start, "confirm": card_registration.confirm,
               "retry": card_registration.retry, "cancel": card_registration.cancel}
    if action not in actions:
        flash(f"Unknown action {action}")
        return redirect(url_for(".index"))
    r = actions[action](get_db(), student_id)
    flash(f"Card registration {action} for student {student_id}: "
          f"{r.get('message') or r.get('reason') or ('ok' if r.get('ok') else 'failed')}")
    return redirect(url_for(".index"))


@bp.post("/session/<int:session_id>/mode")
def session_mode(session_id):
    db = get_db()
    # one button per choice: auto | present | late | disabled
    choice = request.form.get("choice", "")
    if choice not in ("auto", "present", "late", "disabled"):
        flash(f"Invalid choice {choice!r}")
        return redirect(url_for(".index"))
    from ..services import sessions
    instructor = db.execute("SELECT id FROM users WHERE role = 'instructor' LIMIT 1").fetchone()
    try:
        sessions.set_state(db, instructor["id"], session_id, choice)
    except sessions.ValidationError as e:
        flash(e.errors["state"])
        return redirect(url_for(".index"))
    flash(f"Session {session_id} -> {choice}")
    return redirect(url_for(".index"))
