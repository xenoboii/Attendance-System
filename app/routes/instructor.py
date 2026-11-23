"""
Instructor pages: subjects -> sections -> (students, schedule, check-in settings).

URL map
    /instructor/                               dashboard (my subjects)
    /instructor/subjects/new                   create subject
    /instructor/subjects/<id>                  subject page (sections)
    /instructor/subjects/<id>/edit|delete
    /instructor/subjects/<id>/sections         POST create section
    /instructor/sections/<id>?tab=students|schedule|settings
    /instructor/sections/<id>/...              edit, delete, join-code, students, schedules
"""
import io
from datetime import date, datetime
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)

from ..db import get_db
from ..security import login_required
from ..services import battery, calendar_view, classes, clock, courses, report, sessions, terms, trash
from ..services.accounts import ValidationError

bp = Blueprint("instructor", __name__, url_prefix="/instructor")


# ---------------------------------------------------------------- device clock (RTC backup)
@bp.get("/clock")
@login_required("instructor")
def clock_status():
    """The device's time - the page compares it with the phone's time."""
    return jsonify(clock.status(get_db()))


@bp.get("/device")
@login_required("instructor")
def device_status():
    """Battery reading for the sidebar badge / low-battery warning (null = no monitor)."""
    return jsonify({"battery": battery.get(get_db())})


@bp.post("/clock")
@login_required("instructor")
def clock_sync():
    """'Sync time from this phone': set the device clock to the instructor's phone time."""
    data = request.get_json(silent=True) or request.form
    db = get_db()
    auto = bool(data.get("auto"))
    try:
        if auto:
            # sent by the page itself when an instructor's device notices the clock is off.
            # Not while a class is taking check-ins - then the instructor decides (banner + button).
            decision = clock.auto_decision(data.get("epoch_ms"),
                                           class_open=sessions.any_open_for_checkin(db))
            if decision != "sync":
                return jsonify({"ok": True, "changed": False, "held": decision == "held", **clock.status(db)})
        r = clock.set_time(data.get("epoch_ms"),
                           enabled=current_app.config.get("TIME_SYNC_ENABLED", False))
    except ValidationError as e:
        return jsonify({"ok": False, "error": e.errors["time"]}), 400
    r["auto"] = auto
    classes.audit(db, g.user["id"], "sync_time", "device", 0, r)
    db.commit()
    return jsonify({"ok": True, **r, **clock.status(db)})


def _subject_or_404(subject_id):
    subject = classes.get_subject(get_db(), g.user["id"], subject_id)
    if subject is None:
        abort(404)
    return subject


def _section_or_404(section_id) -> dict:
    section = classes.get_section(get_db(), g.user["id"], section_id)
    if section is None:
        abort(404)
    return dict(section)


# ---------------------------------------------------------------- subjects
@bp.get("/")
@login_required("instructor")
def dashboard():
    """Like the student dashboard: what's happening now (banner), numbers, and a month calendar
    with the classes of the picked day. The subject list is on My subjects."""
    db, iid, now = get_db(), g.user["id"], datetime.now()
    year, month = calendar_view.parse_month(request.args.get("m"), now.date())
    today = sessions.todays_sessions(db, iid)
    return render_template("instructor/dashboard.html",
                           stats=classes.instructor_stats(db, iid),
                           today=today,
                           live=[t for t in today if t["phase"] in ("present", "late", "checkout")],
                           next_up=next((t for t in today if t["phase"] in ("upcoming", "paused")), None),
                           cal=calendar_view.month_view(db, iid, year, month, now, who="instructor"),
                           now=now)


@bp.get("/subjects")
@login_required("instructor")
def subjects_page():
    """My subjects: every subject this instructor teaches, and where new ones are added."""
    return render_template("instructor/subjects.html", subjects=classes.list_subjects(get_db(), g.user["id"]))


@bp.route("/subjects/new", methods=["GET", "POST"])
@login_required("instructor")
def subject_new():
    errors, form = {}, request.form
    if request.method == "POST":
        try:
            subject_id = classes.create_subject(get_db(), g.user["id"], form)
        except ValidationError as e:
            errors = e.errors
        else:
            flash("Subject saved. Now add a section.", "success")
            return redirect(url_for(".subject", subject_id=subject_id))
    return render_template("instructor/subject_form.html", errors=errors, form=form,
                           semesters=classes.SEMESTERS, subject=None)


@bp.get("/subjects/<int:subject_id>")
@login_required("instructor")
def subject(subject_id):
    subject = _subject_or_404(subject_id)
    return render_template("instructor/subject.html", subject=subject,
                           next_term=terms.next_term(subject["school_year"], subject["semester"]),
                           semesters=classes.SEMESTERS,
                           sections=classes.list_sections(get_db(), subject_id), errors={},
                           courses=courses.list_courses(get_db()))


@bp.route("/subjects/<int:subject_id>/edit", methods=["GET", "POST"])
@login_required("instructor")
def subject_edit(subject_id):
    subject = _subject_or_404(subject_id)
    errors, form = {}, (request.form if request.method == "POST" else dict(subject))
    if request.method == "POST":
        try:
            classes.update_subject(get_db(), g.user["id"], subject_id, form)
        except ValidationError as e:
            errors = e.errors
        else:
            flash("Subject updated.", "success")
            return redirect(url_for(".subject", subject_id=subject_id))
    return render_template("instructor/subject_form.html", errors=errors, form=form,
                           semesters=classes.SEMESTERS, subject=subject)


@bp.post("/subjects/<int:subject_id>/delete")
@login_required("instructor")
def subject_delete(subject_id):
    subject = _subject_or_404(subject_id)
    classes.delete_subject(get_db(), g.user["id"], subject_id)
    flash(f"{subject['code']} moved to Recently deleted. You can restore it within 30 days.", "info")
    return redirect(url_for(".subjects_page"))


# ---------------------------------------------------------------- end of semester / archive
ARCHIVE_ALLOWED = {"instructor.subject_reopen", "instructor.subject_copy", "instructor.subject_delete"}


@bp.before_request
def _archived_is_read_only():
    """A subject whose semester was ended is read-only (reports and exports still work)."""
    if request.method != "POST" or request.endpoint in ARCHIVE_ALLOWED or g.get("user") is None:
        return None
    args = request.view_args or {}
    keys = {k: args[k] for k in ("subject_id", "section_id", "session_id", "schedule_id", "exception_id") if k in args}
    if not keys:
        return None
    subject = terms.subject_of(get_db(), **keys)
    if subject is None or not subject["ended_at"] or subject["instructor_id"] != g.user["id"]:
        return None
    message = f"{subject['code']} is archived (semester ended). Reopen it to make changes."
    if _is_fetch() or request.is_json:
        return jsonify({"ok": False, "error": message}), 409
    flash(message, "warning")
    return redirect(request.referrer or url_for(".archive_page"))


@bp.get("/archive")
@login_required("instructor")
def archive_page():
    return render_template("instructor/archive.html", subjects=terms.list_archived(get_db(), g.user["id"]),
                           semesters=classes.SEMESTERS)


@bp.post("/subjects/<int:subject_id>/end")
@login_required("instructor")
def subject_end(subject_id):
    subject = _subject_or_404(subject_id)
    if terms.end_semester(get_db(), g.user["id"], subject_id):
        flash(f"Semester ended for {subject['code']}. It is now in Archive: no more classes are scheduled, "
              "and the attendance records and reports are kept.", "info")
    return redirect(url_for(".archive_page"))


@bp.post("/subjects/<int:subject_id>/reopen")
@login_required("instructor")
def subject_reopen(subject_id):
    subject = _subject_or_404(subject_id)
    if terms.reopen(get_db(), g.user["id"], subject_id):
        flash(f"{subject['code']} reopened. Its weekly schedule is running again.", "info")
    return redirect(url_for(".subject", subject_id=subject_id))


@bp.post("/subjects/<int:subject_id>/copy")
@login_required("instructor")
def subject_copy(subject_id):
    subject = _subject_or_404(subject_id)
    try:
        new_id = terms.copy_to_new_term(get_db(), g.user["id"], subject_id, request.form)
    except ValidationError as e:
        flash("Couldn't copy: " + " ".join(str(v) for v in e.errors.values()), "warning")
        return redirect(request.referrer or url_for(".archive_page"))
    flash(f"{subject['code']} copied to {request.form.get('school_year')} {request.form.get('semester')} sem "
          "with the same sections and schedule. Students join with the new join codes.", "info")
    return redirect(url_for(".subject", subject_id=new_id))


# ---------------------------------------------------------------- sections
@bp.post("/subjects/<int:subject_id>/sections")
@login_required("instructor")
def section_create(subject_id):
    subject = _subject_or_404(subject_id)
    try:
        section_id = classes.create_section(get_db(), g.user["id"], subject_id, request.form)
    except ValidationError as e:
        return render_template("instructor/subject.html", subject=subject, errors=e.errors, form=request.form,
                               next_term=terms.next_term(subject["school_year"], subject["semester"]),
                               semesters=classes.SEMESTERS,
                               sections=classes.list_sections(get_db(), subject_id),
                               courses=courses.list_courses(get_db())), 400
    flash("Section created. Add students and the class schedule.", "success")
    return redirect(url_for(".section", section_id=section_id))


@bp.get("/sections/<int:section_id>")
@login_required("instructor")
def section(section_id, errors=None, form=None, tab=None, status=200):
    section = _section_or_404(section_id)
    db = get_db()
    tab = tab or request.args.get("tab", "sessions")
    show_all_upcoming = request.args.get("upcoming") == "all"
    if tab == "sessions":
        sessions.ensure_sessions_for_date(db)
    return render_template(
        "instructor/section.html", section=section, tab=tab,
        students=classes.list_students(db, section_id),
        schedules=classes.list_schedules(db, section_id),
        exceptions=classes.list_schedule_exceptions(db, section_id) if tab == "schedule" else None,
        other_sections=len(classes.instructor_section_ids(db, g.user["id"])) - 1 if tab == "schedule" else 0,
        overview=sessions.attendance_overview(db, section_id, show_all=show_all_upcoming) if tab == "sessions" else None,
        show_all_upcoming=show_all_upcoming,
        when_label=sessions.when_label,
        stats=sessions.section_student_stats(db, section_id) if tab == "students" else {},
        dropped=classes.list_dropped(db, section_id) if tab == "students" else [],
        drop_status=classes.drop_status,
        types=sessions.TYPE_LABELS, today=date.today().isoformat(),
        report_sessions=report.reportable_sessions(db, section_id),
        report_limits=(report.MAX_SELECT_DATES, report.MAX_SELECT_ASYNC, report.MAX_BLANK_DATES),
        report_per_page=(report.MAX_DATES, report.MAX_ASYNC, current_app.config["REPORT_MIN_ROWS"]),
        days=classes.DAYS, errors=errors or {}, form=form or {},
        name_parts=classes.split_section_name(section["name"]),
        courses=courses.list_courses(db, current=classes.split_section_name(section["name"])["course"].upper()),
        join_requests=classes.section_join_requests(db, section_id),
    ), status


@bp.post("/sections/<int:section_id>/edit")
@login_required("instructor")
def section_edit(section_id):
    _section_or_404(section_id)
    try:
        classes.update_section(get_db(), g.user["id"], section_id, request.form)
    except ValidationError as e:
        return section(section_id, errors=e.errors, form=request.form, tab="settings", status=400)
    flash("Section settings saved.", "success")
    return redirect(url_for(".section", section_id=section_id, tab="settings"))


@bp.post("/sections/<int:section_id>/join-code")
@login_required("instructor")
def section_join_code(section_id):
    _section_or_404(section_id)
    code = classes.regenerate_join_code(get_db(), g.user["id"], section_id)
    flash(f"New join code: {code}. The old code no longer works.", "success")
    back = request.form.get("back")      # stay on the tab the instructor was on
    return redirect(url_for(".section", section_id=section_id,
                            tab=back if back in ("sessions", "students", "schedule", "settings") else "students"))


@bp.post("/sections/<int:section_id>/delete")
@login_required("instructor")
def section_delete(section_id):
    section = _section_or_404(section_id)
    classes.delete_section(get_db(), g.user["id"], section_id)
    flash(f"Section {section['name']} moved to Recently deleted. You can restore it within 30 days.", "info")
    return redirect(url_for(".subject", subject_id=section["subject_id"]))


# ---------------------------------------------------------------- students
@bp.post("/sections/<int:section_id>/students")
@login_required("instructor")
def students_add(section_id):
    _section_or_404(section_id)
    r = classes.enroll_by_numbers(get_db(), g.user["id"], section_id, request.form.get("student_numbers", ""))
    if r["added"]:
        flash(f"Added {len(r['added'])} student(s): {', '.join(r['added'])}", "success")
    if r["already"]:
        flash(f"Already enrolled: {', '.join(r['already'])}", "info")
    if r["not_found"]:
        flash(f"Not registered yet (ask them to create an account first): {', '.join(r['not_found'])}", "warning")
    if not any(r.values()):
        flash("Type at least one student number.", "warning")
    return redirect(url_for(".section", section_id=section_id, tab="students"))


@bp.post("/sections/<int:section_id>/students/<int:student_id>/remove")
@login_required("instructor")
def students_remove(section_id, student_id):
    _section_or_404(section_id)
    classes.remove_student(get_db(), g.user["id"], section_id, student_id)
    flash("Student removed. You can restore them from Recently deleted within 30 days.", "info")
    return redirect(url_for(".section", section_id=section_id, tab="students"))


@bp.post("/sections/<int:section_id>/students/<int:student_id>/drop")
@login_required("instructor")
def students_drop(section_id, student_id):
    _section_or_404(section_id)
    if not classes.drop_student(get_db(), g.user["id"], section_id, student_id):
        abort(404)
    flash("Student dropped from the class. Their attendance records are kept; you can put them back from the Dropped list.", "info")
    return redirect(url_for(".section", section_id=section_id, tab="students"))


@bp.post("/sections/<int:section_id>/students/<int:student_id>/restore")
@login_required("instructor")
def students_restore(section_id, student_id):
    _section_or_404(section_id)
    if not classes.restore_student(get_db(), g.user["id"], section_id, student_id):
        abort(404)
    flash("Student is back in the class.", "info")
    return redirect(url_for(".section", section_id=section_id, tab="students"))


def _student_or_404(section_id, student_id):
    section = _section_or_404(section_id)
    student = classes.get_section_student(get_db(), section_id, student_id)
    if student is None:
        abort(404)
    return section, student


@bp.post("/sections/<int:section_id>/requests/<int:request_id>")
@login_required("instructor")
def join_request_review(section_id, request_id):
    _section_or_404(section_id)
    decision = request.form.get("decision")
    if decision not in ("approve", "reject"):
        abort(400)
    try:
        classes.review_join_request(get_db(), g.user["id"], section_id, request_id, decision == "approve")
    except ValidationError as exc:
        flash(exc.errors["request"], "warning")
    else:
        flash("Request approved." if decision == "approve" else "Request declined.", "success")
    return redirect(url_for(".section", section_id=section_id, tab="students"))


@bp.get("/sections/<int:section_id>/students/<int:student_id>")
@login_required("instructor")
def student_info(section_id, student_id, errors=None, form=None, status=200):
    """Student info for the instructor: profile, card, signature and an attendance SUMMARY
    for this section (the per-date details live on each session page)."""
    section, student = _student_or_404(section_id, student_id)
    db = get_db()
    stats = sessions.student_section_stats(db, student_id, section_id)
    return render_template(
        "instructor/student.html", section=section, st=student, summary=stats["summary"],
        other_classes=classes.student_other_classes(db, g.user["id"], student_id, section_id),
        errors=errors or {}, form=form or student,
    ), status


@bp.post("/sections/<int:section_id>/students/<int:student_id>/identity")
@login_required("admin")
def student_identity(section_id, student_id):
    from .admin import student
    return student(student_id)


@bp.post("/sections/<int:section_id>/students/<int:student_id>/remove-card")
@login_required("admin")
def student_remove_card(section_id, student_id):
    from .admin import remove_card
    return remove_card(student_id)


@bp.post("/sections/<int:section_id>/students/<int:student_id>/reset-signature")
@login_required("admin")
def students_reset_signature(section_id, student_id):
    from .admin import signature_reset
    return signature_reset(student_id)


# ---------------------------------------------------------------- sessions (attendance)
def _session_or_404(session_id) -> dict:
    s = sessions.get_session(get_db(), g.user["id"], session_id)
    if s is None:
        abort(404)
    return s


def _is_fetch():
    return request.headers.get("X-Requested-With") == "fetch"


@bp.post("/sections/<int:section_id>/start")
@login_required("instructor")
def section_start(section_id):
    """Open TODAY's existing session now. Never creates a new report column."""
    _section_or_404(section_id)
    sid = sessions.start_now(get_db(), g.user["id"], section_id)
    if sid is None:
        flash("There is no upcoming class session today for this section. Use “Add session” to schedule one.", "warning")
        return redirect(url_for(".section", section_id=section_id, tab="sessions"))
    flash("Check-in is open. Students are marked PRESENT until you switch to Late.", "success")
    return redirect(url_for(".session_page", session_id=sid))


@bp.post("/sections/<int:section_id>/add-column")
@login_required("instructor")
def section_add_column(section_id):
    """Deliberately add a NEW session (report column) starting now, and open it."""
    _section_or_404(section_id)
    try:
        sid = sessions.add_column_now(get_db(), g.user["id"], section_id)
    except ValidationError as exc:
        flash(next(iter(exc.errors.values())), "warning")
        return redirect(url_for(".section", section_id=section_id, tab="sessions"))
    flash("New column added for today and check-in is open. Added by mistake? Delete it below.", "success")
    return redirect(url_for(".session_page", session_id=sid))


@bp.post("/sections/<int:section_id>/sessions")
@login_required("instructor")
def session_create(section_id):
    """Save a session with its selected mode. Async activities award attendance immediately."""
    _section_or_404(section_id)
    db = get_db()
    try:
        sid = sessions.create_session(db, g.user["id"], section_id, request.form)
    except ValidationError as e:
        return section(section_id, errors=e.errors, form=request.form, tab="sessions", status=400)
    if request.form.get("type") == "async":
        flash("Async activity added. Everyone enrolled is marked present.", "success")
    else:
        flash("Session added.", "info")
    return redirect(url_for(".session_page", session_id=sid))


def _session_context(s):
    db = get_db()
    now = datetime.now()
    rows = sessions.roster(db, s, now)
    counts = {k: sum(1 for r in rows if r["status"] == k)
              for k in ("present", "late", "excused", "absent", "pending")}
    return {"s": s, "info": sessions.describe(s, now), "rows": rows, "counts": counts,
            "has_records": sessions.has_records(db, s["id"]), "now": now, "types": sessions.TYPE_LABELS,
            "can_set_async": sessions.can_set_async(s, now)}


@bp.get("/sessions/<int:session_id>")
@login_required("instructor")
def session_page(session_id):
    return render_template("instructor/session.html", **_session_context(_session_or_404(session_id)))


@bp.get("/sessions/<int:session_id>/live")
@login_required("instructor")
def session_live(session_id):
    """Just the live parts for the 3-second refresh: ?part=controls or the roster (default)."""
    template = {"controls": "instructor/_session_controls.html",
                "mode": "instructor/_session_mode.html"}.get(request.args.get("part"), "instructor/_session_live.html")
    return render_template(template, **_session_context(_session_or_404(session_id)))


@bp.post("/sessions/<int:session_id>/state")
@login_required("instructor")
def session_state(session_id):
    """Live buttons: open / reopen as Present or Late (takes manual control)."""
    _session_or_404(session_id)
    choice = request.form.get("choice", "")
    if choice not in ("present", "late"):
        abort(400)
    sessions.set_state(get_db(), g.user["id"], session_id, choice)
    flash({"present": "Check-in is open. Students are marked PRESENT.",
           "late": "Check-in is open. Students are now marked LATE."}[choice], "success")
    return redirect(url_for(".session_page", session_id=session_id))


@bp.post("/sessions/<int:session_id>/checkout")
@login_required("instructor")
def session_checkout(session_id):
    """Check-out control: the dropdown sends require = yes | no, the button sends open = 1."""
    _session_or_404(session_id)
    db, uid = get_db(), g.user["id"]
    try:
        if request.form.get("open"):
            sessions.open_checkout(db, uid, session_id)
            flash("Check-out is open. Students tap their card or press Check out before leaving; "
                  "anyone who doesn't is marked ABSENT when you end the session.", "info")
        elif request.form.get("require") in ("yes", "no"):
            sessions.set_require_checkout(db, uid, session_id, request.form["require"] == "yes")
        else:
            abort(400)
    except ValidationError as exc:
        flash(next(iter(exc.errors.values())), "warning")
    return redirect(url_for(".session_page", session_id=session_id))


@bp.post("/sessions/<int:session_id>/end")
@login_required("instructor")
def session_end(session_id):
    s = _session_or_404(session_id)
    sessions.end_session(get_db(), g.user["id"], session_id)
    if s.get("require_checkout") and s.get("checkout_open_at"):
        flash("Session ended. Students who didn't check in, or didn't check out, are now absent.", "info")
    else:
        flash("Session ended. Students who didn't check in are now absent.", "info")
    return redirect(url_for(".session_page", session_id=session_id))


@bp.post("/sessions/<int:session_id>/reset")
@login_required("instructor")
def session_reset(session_id):
    """Undo a mistake: clear every status on this class date and go back to 'not opened yet'."""
    _session_or_404(session_id)
    cleared = sessions.reset_session(get_db(), g.user["id"], session_id)
    flash(f"Statuses reset{f' ({cleared} cleared)' if cleared else ''}. Everyone shows “—” again; "
          "open check-in when you're ready.", "info")
    return redirect(url_for(".session_page", session_id=session_id))


@bp.post("/sessions/<int:session_id>/async")
@login_required("instructor")
def session_set_async(session_id):
    _session_or_404(session_id)
    try:
        sessions.set_async(get_db(), g.user["id"], session_id, request.form.get("title", ""))
    except ValidationError as exc:
        flash(next(iter(exc.errors.values())), "warning")
    else:
        flash("Session changed to an async activity.", "info")
    return redirect(url_for(".session_page", session_id=session_id))


@bp.post("/sessions/<int:session_id>/async/change")
@login_required("instructor")
def session_change_async(session_id):
    s = _session_or_404(session_id)
    try:
        sessions.change_upcoming_async(get_db(), g.user["id"], session_id, request.form)
    except ValidationError as exc:
        flash(next(iter(exc.errors.values())), "warning")
    else:
        flash("Changed back to a regular class." if request.form.get("choice") == "regular"
              else "Async activity changed.", "info")
    if request.form.get("back") == "session":           # sent from the session page itself
        return redirect(url_for(".session_page", session_id=session_id))
    return redirect(url_for(".section", section_id=s["section_id"], tab="sessions", _anchor="upcoming-sessions"))


@bp.post("/sessions/<int:session_id>/mode")
@login_required("instructor")
def session_mode(session_id):
    """Session settings: automatic (follow class time) or manual (your buttons)."""
    _session_or_404(session_id)
    mode = request.form.get("mode", "")
    if mode not in ("auto", "manual"):
        abort(400)
    try:
        sessions.set_mode(get_db(), g.user["id"], session_id, mode)
    except ValidationError as e:
        flash(e.errors["state"], "warning")
    else:
        flash("Automatic mode: check-in follows the class time." if mode == "auto"
              else "Manual mode: check-in only changes when you press a button.", "success")
    return redirect(url_for(".session_page", session_id=session_id))


@bp.post("/sessions/<int:session_id>/code")
@login_required("instructor")
def session_code(session_id):
    _session_or_404(session_id)
    sessions.set_code(get_db(), session_id, request.form.get("enabled") == "1")
    return redirect(url_for(".session_page", session_id=session_id))


@bp.post("/sessions/<int:session_id>/mark/<int:student_id>")
@login_required("instructor")
def session_mark(session_id, student_id):
    s = _session_or_404(session_id)
    status = request.form.get("status", "")
    if status not in ("present", "late", "absent", "excused", "clear"):
        abort(400)
    if not any(r["id"] == student_id for r in sessions.roster(get_db(), s)):
        abort(404)
    sessions.mark(get_db(), g.user["id"], session_id, student_id, status)
    if _is_fetch():
        return session_live(session_id)
    return redirect(url_for(".session_page", session_id=session_id))


@bp.post("/sessions/<int:session_id>/delete")
@login_required("instructor")
def session_delete(session_id):
    s = _session_or_404(session_id)
    sessions.delete_session(get_db(), g.user["id"], session_id)
    flash("Session moved to Recently deleted. You can restore it within 30 days.", "info")
    return redirect(url_for(".section", section_id=s["section_id"], tab="sessions"))


# ---------------------------------------------------------------- recently deleted (backup)
@bp.get("/trash")
@login_required("instructor")
def trash_page():
    db, days = get_db(), current_app.config["TRASH_DAYS"]
    trash.purge_expired(db, days)                        # anything past 30 days is gone for good
    return render_template("instructor/trash.html", items=trash.list_trash(db, g.user["id"], days), days=days)


@bp.post("/trash/<kind>/<int:item_id>/<action>")
@login_required("instructor")
def trash_action(kind, item_id, action):
    if kind not in trash.KINDS or action not in ("restore", "delete"):
        abort(404)
    try:
        if action == "restore":
            msg = trash.restore(get_db(), g.user["id"], kind, item_id)
            flash(msg, "success")
        else:
            trash.delete_forever(get_db(), g.user["id"], kind, item_id)
    except ValidationError as e:
        flash(e.errors["trash"], "warning")
    return redirect(url_for(".trash_page"))


# ---------------------------------------------------------------- PDF report
@bp.get("/sections/<int:section_id>/report")
@login_required("instructor")
def section_report(section_id):
    """PDF with names, signatures and the chosen dates. ?s=<session id> (repeat),
    ?d=YYYY-MM-DD (repeat) for blank date columns to fill in on paper, &dl=1 to download."""
    section = _section_or_404(section_id)
    db = get_db()
    try:
        chosen = report.validate_selection(db, section_id, request.args.getlist("s"),
                                           blank_dates=request.args.getlist("d"))
    except ValidationError as e:
        flash(e.errors["sessions"], "warning")
        return redirect(url_for(".section", section_id=section_id))
    cfg = current_app.config
    pdf = report.build_pdf(
        db, section, dict(g.user), chosen, cfg["SIGNATURE_DIR"],
        school_lines=cfg["REPORT_SCHOOL_LINES"], page_inches=cfg["REPORT_PAGE_INCHES"],
        min_rows=cfg["REPORT_MIN_ROWS"],
        logo_path=Path(current_app.static_folder) / "img" / "school_logo.png",
        include_prof_signature=request.args.get("sign") == "1",
        include_department=request.args.get("dept") == "1",
        margins_inches=cfg["REPORT_MARGINS_INCHES"],
        school_font_files=cfg["REPORT_SCHOOL_FONT_FILES"])
    resp = send_file(io.BytesIO(pdf), mimetype="application/pdf", max_age=0,
                     as_attachment=request.args.get("dl") == "1", download_name=report.filename(section))
    # always freshly built - never let the browser show an old copy in the preview
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- schedules
@bp.post("/sections/<int:section_id>/schedules")
@login_required("instructor")
def schedule_add(section_id):
    _section_or_404(section_id)
    try:
        added = classes.add_schedules(get_db(), g.user["id"], section_id, request.form)
    except ValidationError as e:
        form = {"schedule_rows": classes.schedule_rows(request.form)}
        return section(section_id, errors=e.errors, form=form, tab="schedule", status=400)
    flash("Schedule added." if len(added) == 1 else f"{len(added)} schedules added.", "success")
    return redirect(url_for(".section", section_id=section_id, tab="schedule"))


@bp.post("/schedules/<int:schedule_id>/edit")
@login_required("instructor")
def schedule_edit(schedule_id):
    row = classes._own_schedule(get_db(), g.user["id"], schedule_id)
    if row is None:
        abort(404)
    try:
        classes.update_schedule(get_db(), g.user["id"], schedule_id, request.form)
    except ValidationError as e:
        form = {**request.form.to_dict(), "schedule_id": str(schedule_id)}
        return section(row["section_id"], errors=e.errors, form=form, tab="schedule", status=400)
    flash("Schedule updated. Upcoming unused sessions follow the new time.", "success")
    return redirect(url_for(".section", section_id=row["section_id"], tab="schedule"))


@bp.post("/schedules/<int:schedule_id>/delete")
@login_required("instructor")
def schedule_delete(schedule_id):
    section_id = classes.delete_schedule(get_db(), g.user["id"], schedule_id)
    if section_id is None:
        abort(404)
    flash("Schedule moved to Recently deleted. You can restore it within 30 days.", "info")
    return redirect(url_for(".section", section_id=section_id, tab="schedule"))


# ---------------------------------------------------------------- schedule exceptions (no class)
def _back_tab(default="schedule"):
    back = request.form.get("back")
    return back if back in ("sessions", "students", "schedule", "settings") else default


@bp.post("/sections/<int:section_id>/no-class")
@login_required("instructor")
def schedule_exception_add(section_id):
    _section_or_404(section_id)
    tab = _back_tab()
    try:
        applied = classes.add_schedule_exception(get_db(), g.user["id"], section_id, request.form)
    except ValidationError as e:
        if request.form.get("as_async") or tab == "sessions":
            flash(next(iter(e.errors.values())), "warning")
            return redirect(url_for(".section", section_id=section_id, tab=tab))
        return section(section_id, errors=e.errors, form=request.form, tab=tab, status=400)
    reason = (request.form.get("reason") or "").strip()
    if request.form.get("as_async"):
        flash("Async activity added. Find it in Attendance on its scheduled date."
              if applied else "That date is already an async activity.", "info")
        return redirect(url_for(".section", section_id=section_id, tab=tab))
    if len(applied) > 1:
        flash(f"Marked {reason} on {len(applied)} sections.", "success")
    elif applied:
        flash(f"Marked {reason}.", "success")
    else:
        flash(f"Already marked {reason}.", "info")
    return redirect(url_for(".section", section_id=section_id, tab=tab))


@bp.post("/sections/<int:section_id>/vacation")
@login_required("instructor")
def vacation_add(section_id):
    _section_or_404(section_id)
    try:
        result = classes.add_vacation(get_db(), g.user["id"], section_id, request.form)
    except ValidationError as exc:
        return section(section_id, errors=exc.errors, form=request.form, tab="schedule", status=400)
    flash(f"Vacation added: {result['days']} days on {len(result['sections'])} section(s). No classes or absences during these dates.", "success")
    return redirect(url_for(".section", section_id=section_id, tab="schedule"))


@bp.post("/no-class/<int:exception_id>/delete")
@login_required("instructor")
def schedule_exception_delete(exception_id):
    try:
        section_id = classes.delete_schedule_exception(get_db(), g.user["id"], exception_id)
    except ValidationError as e:
        flash(e.errors["exception"], "warning")
        return redirect(request.referrer or url_for(".dashboard"))
    if section_id is None:
        abort(404)
    flash("Back to a regular class day.", "info")
    return redirect(url_for(".section", section_id=section_id, tab=_back_tab()))
