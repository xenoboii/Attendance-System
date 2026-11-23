"""Admin portal. Additional instructor support tools can be added here later."""
import io

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.security import check_password_hash

from ..db import get_db
from ..security import login_required
from ..db import connect
from ..migrations import MIGRATIONS, migrate
from ..services import (account_deletion, admin_overview, app_settings, backup, checkin_signatures, courses, departments,
                        device_settings, recovery, student_support)
from ..services import accounts as accounts_svc
from ..services.accounts import ValidationError

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _render(*, errors=None, code="", status=200):
    return render_template("admin/courses.html",
                           courses=courses.list_courses(get_db(), include_inactive=True),
                           errors=errors or {}, code=code), status


@bp.get("/")
@login_required("admin")
def dashboard():
    """Overview: accounts, today's activity, storage and the device."""
    return render_template("admin/overview.html", **admin_overview.build(get_db(), current_app.config))


@bp.post("/settings/signature-days")
@login_required("admin")
def signature_days():
    """How many days a signature drawn at web check-in is kept. Applied right away."""
    db = get_db()
    try:
        days = app_settings.set_signature_days(db, g.user["id"], request.form.get("days", ""))
    except ValidationError as exc:
        flash(exc.errors["signature_days"], "warning")
        return redirect(url_for(".dashboard"))
    removed = checkin_signatures.purge_old(db, current_app.config["SIGNATURE_DIR"], days)
    flash(f"Check-in signatures are now kept for {days} days."
          + (f" {removed} older one{'' if removed == 1 else 's'} deleted." if removed else ""), "info")
    return redirect(url_for(".dashboard"))


# ---------------------------------------------------------------- device settings
def _device_page(errors=None, form=None, status=200):
    db = get_db()
    return render_template(
        "admin/device.html", errors=errors or {}, form=form or {},
        instructor_code=device_settings.instructor_code(db, current_app.config["INSTRUCTOR_REGISTRATION_CODE"]),
        wifi_name=device_settings.wifi_name(), wifi_available=device_settings.wifi_available()), status


@bp.get("/device")
@login_required("admin")
def device_page():
    """Instructor registration code + hotspot Wi-Fi name and password."""
    return _device_page()


@bp.post("/device/instructor-code")
@login_required("admin")
def device_instructor_code():
    try:
        code = device_settings.set_instructor_code(get_db(), g.user["id"], request.form.get("code"))
    except ValidationError as exc:
        return _device_page(errors=exc.errors, form=request.form, status=400)
    flash(f"Instructor registration code changed to {code}. Give it to new instructors.", "info")
    return redirect(url_for(".device_page"))


@bp.post("/device/wifi")
@login_required("admin")
def device_wifi():
    form = request.form
    if not check_password_hash(g.user["password_hash"], form.get("admin_password") or ""):
        return _device_page(errors={"admin_password": "Wrong password."}, form=form, status=400)
    try:
        name = device_settings.set_wifi(get_db(), g.user["id"], form.get("wifi_name"),
                                        form.get("wifi_password"), form.get("wifi_confirm"))
    except ValidationError as exc:
        return _device_page(errors=exc.errors, form=form, status=400)
    return render_template("admin/device_wifi_done.html", name=name)


# ---------------------------------------------------------------- accounts: delete / restore / purge
@bp.get("/accounts")
@login_required("admin")
def accounts_page():
    query = request.args.get("q", "")
    return render_template("admin/accounts.html", query=query,
                           accounts=account_deletion.list_accounts(get_db(), query),
                           deleted=account_deletion.list_deleted(get_db()))


@bp.post("/accounts/<int:user_id>/delete")
@login_required("admin")
def account_delete(user_id):
    try:
        user = account_deletion.delete_account(get_db(), user_id, g.user["id"])
    except ValidationError as exc:
        flash(exc.errors["account"], "warning")
    else:
        flash(f"{user['first_name']} {user['last_name']}'s account was deleted. You can restore it from the Deleted list.", "info")
    return redirect(url_for(".accounts_page"))


@bp.post("/accounts/<int:user_id>/restore")
@login_required("admin")
def account_restore(user_id):
    try:
        user = account_deletion.restore_account(get_db(), user_id, g.user["id"])
    except ValidationError as exc:
        flash(exc.errors["account"], "warning")
    else:
        flash(f"{user['first_name']} {user['last_name']}'s account is back, with its history.", "info")
    return redirect(url_for(".accounts_page"))


@bp.post("/accounts/<int:user_id>/purge")
@login_required("admin")
def account_purge(user_id):
    try:
        files_failed = account_deletion.purge_account(get_db(), user_id, g.user["id"],
                                                      current_app.config["SIGNATURE_DIR"])
    except ValidationError as exc:
        flash(exc.errors["account"], "warning")
    else:
        if files_failed:
            flash("The account was permanently deleted, but some signature files could not be removed. See the app log.", "warning")
        else:
            flash("Account permanently deleted.", "info")
    return redirect(url_for(".accounts_page", _anchor="deleted-accounts"))


# ---------------------------------------------------------------- backup and restore
def _render_backup(*, errors=None, status=200):
    cfg = current_app.config
    return render_template("admin/backup.html", errors=errors or {},
                           backup=backup.status(get_db(), cfg["DATA_DIR"]),
                           max_mb=cfg.get("BACKUP_MAX_BYTES", 0) // (1024 * 1024),
                           remind_days=backup.REMIND_AFTER_DAYS), status


@bp.get("/backup")
@login_required("admin")
def backup_page():
    return _render_backup()


@bp.post("/backup/download")
@login_required("admin")
def backup_download():
    """One .zip with the whole database + the signature images."""
    cfg = current_app.config
    data = backup.create(cfg["DATABASE"], cfg["SIGNATURE_DIR"])
    backup.mark_downloaded(get_db(), g.user["id"])
    resp = send_file(io.BytesIO(data), mimetype="application/zip", as_attachment=True,
                     download_name=backup.filename(), max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/backup/restore")
@login_required("admin")
def backup_restore():
    """Replace everything with an uploaded backup. Needs the admin's password and a tick box."""
    cfg, errors = current_app.config, {}
    if not check_password_hash(g.user["password_hash"], request.form.get("password") or ""):
        errors["password"] = "Wrong password."
    if request.form.get("confirm") != "1":
        errors["confirm"] = "Tick the box to confirm."
    upload = request.files.get("file")
    data = upload.read() if upload and upload.filename else b""
    if not errors:
        try:
            get_db().commit()                       # no open transaction while the pages are replaced
            info = backup.restore(data, cfg["DATABASE"], cfg["SIGNATURE_DIR"], cfg["DATA_DIR"], len(MIGRATIONS))
        except ValidationError as exc:
            errors.update(exc.errors)
    elif not data:
        errors.setdefault("file", "Choose a backup file first.")
    if errors:
        return _render_backup(errors=errors, status=400)

    conn = connect(cfg["DATABASE"])                 # an older backup: bring its tables up to date
    try:
        migrate(conn)
        from ..services.classes import audit
        audit(conn, None, "restore_backup", "backup", 0,
              {"made": info["manifest"].get("created_at"), "safety_copy": info["safety_copy"]})
        conn.commit()
    finally:
        conn.close()
    session.clear()                                 # accounts may be different now: log in again
    flash(f"Restored the backup made {info['manifest'].get('created_at', 'earlier')}. Log in again. "
          f"What was here before is saved on the device as {info['safety_copy']}.", "info")
    return redirect(url_for("auth.login"))


@bp.get("/courses")
@login_required("admin")
def courses_page():
    return _render()


@bp.post("/courses")
@login_required("admin")
def add_course():
    code = request.form.get("code", "")
    try:
        saved = courses.add(get_db(), g.user["id"], code)
    except ValidationError as exc:
        return _render(errors=exc.errors, code=code, status=400)
    flash(f"{saved} added. Students can now select it in their profile.", "success")
    return redirect(url_for(".courses_page"))


@bp.get("/students")
@login_required("admin")
def students():
    query = request.args.get("q", "")
    return render_template("admin/students.html", students=student_support.students(get_db(), query), query=query)


def _student_or_404(student_id):
    student = student_support.get_student(get_db(), student_id)
    if student is None:
        abort(404)
    return student


@bp.route("/students/<int:student_id>", methods=["GET", "POST"])
@login_required("admin")
def student(student_id):
    user = _student_or_404(student_id)
    errors = {}
    if request.method == "POST":
        try:
            student_support.correct(get_db(), g.user["id"], user, request.form)
        except ValidationError as exc:
            errors = exc.errors
        else:
            flash("Student information updated.", "success")
            return redirect(url_for(".student", student_id=student_id))
    return render_template("admin/student.html", st=user, form=request.form if errors else dict(user), errors=errors,
                           unclaimed=student_support.unclaimed_records(get_db(), exclude_id=student_id),
                           is_unclaimed=accounts_svc.is_unclaimed(user),
                           courses=courses.list_courses(get_db(), current=user["program"])), 400 if errors else 200


@bp.post("/students/<int:student_id>/merge")
@login_required("admin")
def student_merge(student_id):
    """A student registered with a mistyped number: move their imported history into this account."""
    _student_or_404(student_id)
    try:
        r = student_support.merge_unclaimed(get_db(), g.user["id"], student_id,
                                            request.form.get("source_id", type=int) or 0,
                                            current_app.config["SIGNATURE_DIR"])
    except ValidationError as exc:
        flash(exc.errors["merge"], "warning")
    else:
        flash(f"Merged. This account is now {r['number']} with {r['classes']} class"
              f"{'' if r['classes'] == 1 else 'es'} and {r['records']} attendance record"
              f"{'' if r['records'] == 1 else 's'} from the imported record.", "info")
    return redirect(url_for(".student", student_id=student_id))


@bp.post("/students/<int:student_id>/signature-reset")
@login_required("admin")
def signature_reset(student_id):
    _student_or_404(student_id)
    student_support.reset_signature(get_db(), g.user["id"], student_id)
    flash("The student can now draw a replacement signature.", "success")
    return redirect(url_for(".student", student_id=student_id))


@bp.post("/students/<int:student_id>/remove-card")
@login_required("admin")
def remove_card(student_id):
    _student_or_404(student_id)
    student_support.remove_card(get_db(), g.user["id"], student_id)
    flash("School ID removed. The student can register a replacement.", "success")
    return redirect(url_for(".student", student_id=student_id))


# ---------------------------------------------------------------- backup codes help desk
# A student / instructor who lost their backup codes asks the admin. Only hashes are stored,
# so the old codes can't be shown: the admin issues ONE single-use code and hands it over.
# The person uses it on "Forgot password?", logs in, and makes their own new set.
@bp.get("/backup-codes")
@login_required("admin")
def backup_codes():
    query = request.args.get("q", "")
    return render_template("admin/backup_codes.html", query=query,
                           accounts=recovery.accounts_overview(get_db(), query))


@bp.post("/backup-codes/<int:user_id>")
@login_required("admin")
def backup_codes_issue(user_id):
    issued = recovery.admin_issue(get_db(), g.user["id"], user_id)
    if issued is None:
        abort(404)
    session["issued_codes"] = {"user_id": user_id, "codes": issued[1]}
    return redirect(url_for(".backup_codes_issued"))


def _issued_or_none():
    data = session.get("issued_codes")
    if not data:
        return None, None
    user = get_db().execute("SELECT * FROM users WHERE id = ?", (data["user_id"],)).fetchone()
    return (dict(user), data["codes"]) if user else (None, None)


@bp.get("/backup-codes/issued")
@login_required("admin")
def backup_codes_issued():
    user, codes = _issued_or_none()
    if user is None:
        return redirect(url_for(".backup_codes"))
    return render_template("admin/backup_codes_issued.html", user=user, code=codes[0])


@bp.post("/backup-codes/issued/done")
@login_required("admin")
def backup_codes_issued_done():
    session.pop("issued_codes", None)            # never shown again - only the hashes are kept
    return redirect(url_for(".backup_codes"))


# ---------------------------------------------------------------- departments (colleges)
def _render_departments(*, errors=None, name="", status=200):
    return render_template("admin/departments.html", errors=errors or {}, name=name,
                           departments=departments.list_departments(get_db(), include_inactive=True)), status


@bp.get("/departments")
@login_required("admin")
def departments_page():
    return _render_departments()


@bp.post("/departments")
@login_required("admin")
def add_department():
    name = request.form.get("name", "")
    try:
        saved = departments.add(get_db(), g.user["id"], name)
    except ValidationError as exc:
        return _render_departments(errors=exc.errors, name=name, status=400)
    flash(f"{saved} added. Instructors can now select it in their profile.", "success")
    return redirect(url_for(".departments_page"))


@bp.post("/departments/status")
@login_required("admin")
def department_status():
    name, choice = request.form.get("name", ""), request.form.get("active")
    if choice not in ("0", "1"):
        return _render_departments(errors={"name": "Choose Enable or Disable."}, status=400)
    try:
        departments.set_active(get_db(), g.user["id"], name, choice == "1")
    except ValidationError as exc:
        return _render_departments(errors=exc.errors, status=400)
    flash(f"{name} {'enabled' if choice == '1' else 'disabled'}.", "success")
    return redirect(url_for(".departments_page"))


@bp.post("/departments/remove")
@login_required("admin")
def department_remove():
    name = request.form.get("name", "")
    try:
        departments.remove(get_db(), g.user["id"], name)
    except ValidationError as exc:
        flash(exc.errors["name"], "warning")
    else:
        flash(f"{name} removed from the department catalog.", "success")
    return redirect(url_for(".departments_page"))


@bp.post("/courses/<code>/status")
@login_required("admin")
def course_status(code):
    choice = request.form.get("active")
    if choice not in ("0", "1"):
        return _render(errors={"code": "Choose Enable or Disable."}, status=400)
    try:
        courses.set_active(get_db(), g.user["id"], code, choice == "1")
    except ValidationError as exc:
        return _render(errors=exc.errors, status=400)
    flash(f"{code} {'enabled' if choice == '1' else 'disabled'}.", "success")
    return redirect(url_for(".courses_page"))


@bp.post("/courses/<code>/remove")
@login_required("admin")
def course_remove(code):
    try:
        courses.remove(get_db(), g.user["id"], code)
    except ValidationError as exc:
        flash(exc.errors["code"], "warning")
    else:
        flash(f"{code} removed from the course catalog.", "success")
    return redirect(url_for(".courses_page"))
