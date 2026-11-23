"""Student pages: dashboard (check-in button) and RFID card registration."""
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from ..db import get_db
from ..security import login_required
from ..services import attendance_service as attendance
from ..services import calendar_view, card_registration, checkin_signatures, classes, sessions
from ..services.accounts import ValidationError

bp = Blueprint("student", __name__, url_prefix="/student")

# Check-in card wording + icon per button variant (dashboard + its live refresh)
CHECKIN_TEXT = {
    "headlines": {"present": "Check-in is open", "late": "Grace period is over",
                  "done": "You're checked in", "closed": "No class open right now",
                  "checkout": "Check out before you leave", "out": "You're checked out"},
    "icons": {"present": "broadcast", "late": "hourglass-split", "done": "patch-check-fill", "closed": "moon-stars",
              "checkout": "box-arrow-right", "out": "patch-check-fill"},
    "states": {"present": "Open · Present", "late": "Open · Late", "done": "Done", "closed": "Closed",
               "checkout": "Check-out open", "out": "Done"},
}

MESSAGES = {
    "present": "You're checked in as PRESENT.",
    "late": "You're checked in as LATE.",
    "duplicate": "You're already checked in.",
    "no_open_session": "Check-in is closed right now.",
    "invalid_code": "Wrong check-in code. Look at the code on your instructor's screen.",
    "checked_out": "You're checked out. You can leave now.",
    "already_checked_out": "You already checked out.",
    "not_checked_in": "You didn't check in to this class, so there's nothing to check out of.",
}


def _button_json(db, student_id):
    b = attendance.get_button_status(db, student_id)
    s = b.pop("session")
    b.setdefault("action", "checkin")             # "checkout" while check-out is open
    b["needs_code"] = bool(s and s.get("checkin_code") and b["enabled"] and b["action"] == "checkin")
    return b


@bp.get("/")
@login_required("student")
def dashboard():
    db, sid, now = get_db(), g.user["id"], datetime.now()
    year, month = calendar_view.parse_month(request.args.get("m"), now.date())
    return render_template("student/dashboard.html",
                           button=_button_json(db, sid),
                           class_count=len(classes.student_classes(db, sid)),
                           today=classes.todays_schedule(db, student_id=sid),
                           counts=classes.student_attendance_counts(db, sid),
                           cal=calendar_view.month_view(db, sid, year, month, now),
                           now=now, checkin_text=CHECKIN_TEXT)


@bp.get("/classes")
@login_required("student")
def my_classes():
    """Every class the student joined (+ the join-code box and pending requests)."""
    db, sid = get_db(), g.user["id"]
    rows = classes.student_classes(db, sid)
    for c in rows:
        c["summary"] = sessions.student_section_stats(db, sid, c["section_id"])["summary"]
    return render_template("student/classes.html", classes=rows,
                           join_requests=classes.student_join_requests(db, sid))


@bp.get("/checkin/status")
@login_required("student")
def checkin_status():
    return jsonify(_button_json(get_db(), g.user["id"]))


@bp.post("/checkin")
@login_required("student")
def checkin():
    """Web check-in: the student signs on the spot (a card tap uses the stored signature instead)."""
    db = get_db()
    try:
        png = checkin_signatures.decode(request.form.get("signature", ""))
    except ValidationError as e:
        flash(e.errors["signature"], "warning")
        return redirect(url_for(".dashboard"))
    r = attendance.web_check_in(db, g.user["id"], code=request.form.get("code") or None,
                                ip_address=request.remote_addr,
                                device_info=request.user_agent.string)
    ok = r["result"] in ("present", "late")
    if ok:
        checkin_signatures.save(db, r["session"]["id"], g.user["id"], png, current_app.config["SIGNATURE_DIR"])
    flash(MESSAGES.get(r["result"], r["result"]), "success" if ok else "warning")
    return redirect(url_for(".dashboard"))


@bp.post("/checkout")
@login_required("student")
def checkout():
    """Web check-out (only while the instructor has check-out open). No signature needed."""
    r = attendance.web_check_out(get_db(), g.user["id"])
    if r["result"] == "no_open_session":
        flash("Check-out isn't open right now.", "warning")
    else:
        # "info": success flashes are hidden by design, but this result must be seen
        flash(MESSAGES.get(r["result"], r["result"]), "info" if r["result"] == "checked_out" else "warning")
    return redirect(url_for(".dashboard"))


@bp.get("/classes/<int:section_id>")
@login_required("student")
def class_page(section_id):
    """One subject: present / late / absent / excused counts + every session's status."""
    db = get_db()
    info = next((c for c in classes.student_classes(db, g.user["id"]) if c["section_id"] == section_id), None)
    if info is None:
        abort(404)                       # not enrolled in this section
    stats = sessions.student_section_stats(db, g.user["id"], section_id)
    return render_template("student/class.html", c=info, history=stats["history"],
                           summary=stats["summary"], types=sessions.TYPE_LABELS)


@bp.post("/join")
@login_required("student")
def join():
    db, uid, code = get_db(), g.user["id"], request.form.get("join_code", "")
    if request.form.get("action") == "request":
        r = classes.request_join(db, uid, code, request.form.get("reason", ""))
    else:
        r = classes.join_by_code(db, uid, code)
    flash(r["message"], "success" if r["ok"] else "warning")
    return redirect(url_for(".my_classes"))


@bp.post("/classes/<int:section_id>/leave")
@login_required("student")
def leave_class(section_id):
    result = classes.leave_class(get_db(), g.user["id"], section_id)
    if result is None:
        abort(404)
    flash(f"You left {result['subject_code']} {result['section_name']}.", "info")
    return redirect(url_for(".my_classes"))


# ---------------------------------------------------------------- card registration
@bp.get("/card")
@login_required("student")
def card():
    # card registration now lives in My account (pop-up); keep this address working
    return redirect(url_for("account.index", card=1))


@bp.get("/card/status")
@login_required("student")
def card_status():
    db = get_db()
    user = db.execute("SELECT rfid_uid FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    reg = card_registration.get_status(db, g.user["id"])
    return jsonify({
        "has_card": bool(user["rfid_uid"]),
        "card_uid": user["rfid_uid"],
        "registration": reg and {k: reg[k] for k in ("status", "detected_uid", "seconds_left", "message")},
    })


@bp.post("/card/<action>")
@login_required("student")
def card_action(action):
    actions = {"start": card_registration.start, "confirm": card_registration.confirm,
               "retry": card_registration.retry, "cancel": card_registration.cancel}
    if action not in actions:
        return jsonify({"ok": False, "message": "Unknown action"}), 404
    r = actions[action](get_db(), g.user["id"])
    r.pop("registration", None)
    return jsonify(r)
