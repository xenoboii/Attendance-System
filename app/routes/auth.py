"""
Login, logout, registration and signature.

Two sites on the same Pi (see config.PROF_HOSTS):
    attendance.lan        -> Student login + Student registration
    prof.attendance.lan   -> Instructor login + Instructor registration
"""
import io
import logging

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, send_file, send_from_directory, session, url_for)

from ..db import get_db
from ..security import dashboard_for, is_prof_site, login_required
from ..services import accounts, device_settings, recovery

bp = Blueprint("auth", __name__)
log = logging.getLogger(__name__)


def _safe_next(url: str | None) -> str | None:
    return url if url and url.startswith("/") and not url.startswith("//") else None


def _site_role_ok(user) -> bool:
    is_staff = user["role"] in ("instructor", "admin")
    return is_staff if is_prof_site() else not is_staff


def _wrong_site_message(user) -> str:
    if user["role"] == "student":
        return f"Students sign in at http://{current_app.config['STUDENT_HOST']}"
    prof = sorted(current_app.config["PROF_HOSTS"])[0]
    if user["role"] == "admin":
        return f"Admins sign in at http://{prof}"
    return f"Instructors sign in at http://{prof}"


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(dashboard_for(g.user))

    error, login_value = None, ""
    if request.method == "POST":
        login_value = request.form.get("login", "").strip()
        user = accounts.authenticate(get_db(), login_value, request.form.get("password", ""))
        if user is None:
            error = ("Wrong username or password." if is_prof_site()
                     else "Wrong student number or password.")
        elif not _site_role_ok(user):
            error = _wrong_site_message(user)
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(_safe_next(request.args.get("next")) or dashboard_for(user))

    return render_template("auth/login.html", error=error, login_value=login_value)


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.get("/register")
def register():
    return redirect(url_for("auth.register_instructor" if is_prof_site() else "auth.register_student"))


@bp.route("/register/student", methods=["GET", "POST"])
def register_student():
    if is_prof_site():
        return redirect(url_for("auth.register_instructor"))
    errors, form = {}, {}
    if request.method == "POST":
        form = request.form
        try:
            user_id = accounts.register_student(get_db(), form)
        except accounts.ValidationError as e:
            errors = e.errors
        else:
            session.clear()
            session["user_id"] = user_id
            # backup codes first (PDF downloads), then the signature
            session["backup_codes"] = recovery.generate(get_db(), user_id)
            return redirect(url_for("auth.backup_codes"))
    from ..services import courses
    return render_template("auth/register_student.html", errors=errors, form=form,
                           courses=courses.list_courses(get_db()))


@bp.route("/register/instructor", methods=["GET", "POST"])
def register_instructor():
    if not is_prof_site():
        abort(404)  # instructor registration only exists on the prof address
    errors, form = {}, {}
    if request.method == "POST":
        form = request.form
        try:
            user_id = accounts.register_instructor(
                get_db(), form, device_settings.instructor_code(
                    get_db(), current_app.config["INSTRUCTOR_REGISTRATION_CODE"]))
        except accounts.ValidationError as e:
            errors = e.errors
        else:
            session.clear()
            session["user_id"] = user_id
            # backup codes for "Forgot password?" - shown once, downloaded as a PDF next
            session["backup_codes"] = recovery.generate(get_db(), user_id)
            return redirect(url_for("auth.backup_codes"))
    return render_template("auth/register_instructor.html", errors=errors, form=form)


# ---------------------------------------------------------------- backup codes / forgot password
@bp.get("/backup-codes")
@login_required
def backup_codes():
    """The new codes, shown once. The PDF downloads by itself; Continue unlocks after that."""
    if not session.get("backup_codes"):
        return redirect(url_for("account.index"))
    return render_template("auth/backup_codes.html", codes=session["backup_codes"],
                           pdf_name=recovery.filename(g.user))


@bp.get("/backup-codes.pdf")
@login_required
def backup_codes_pdf():
    codes = session.get("backup_codes")
    if not codes:
        abort(404)
    pdf = recovery.build_pdf(dict(g.user), codes, site=request.host,
                             school_font_files=current_app.config["REPORT_SCHOOL_FONT_FILES"])
    resp = send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=recovery.filename(g.user), max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/backup-codes/done")
@login_required
def backup_codes_done():
    session.pop("backup_codes", None)          # never shown again - only the hashes are kept
    if accounts.needs_signature(g.user):       # new student: last step is the signature
        flash("Last step: draw your signature.", "info")
        return redirect(url_for("auth.signature"))
    return redirect(dashboard_for(g.user))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    errors, form = {}, {}
    if request.method == "POST":
        form = request.form
        try:
            recovery.reset_password(get_db(), form, student=not is_prof_site())
        except accounts.ValidationError as e:
            errors = e.errors
        else:
            flash("Password changed. Log in with your new password.", "info")
            return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html", errors=errors, form=form)


@bp.route("/signature", methods=["GET", "POST"])
@login_required
def signature():
    if not accounts.can_change_signature(g.user):
        # students sign once; only an instructor reset lets them sign again
        flash("To change your signature, ask the admin to reset it.", "warning")
        return redirect(url_for("account.index"))
    error = None
    if request.method == "POST":
        try:
            accounts.save_signature(get_db(), g.user["id"], request.form.get("signature", ""),
                                    current_app.config["SIGNATURE_DIR"])
        except accounts.ValidationError as e:
            error = e.errors["signature"]
        else:
            flash("Signature saved.", "success")
            return redirect(dashboard_for(g.user))
    first_time = g.user["role"] == "student" and not g.user["signature_path"]
    return render_template("auth/signature.html", error=error, first_time=first_time)


@bp.get("/signature/<int:user_id>.png")
@login_required
def signature_image(user_id):
    # students can only see their own; instructors/admin can see all
    if g.user["id"] != user_id and g.user["role"] not in ("instructor", "admin"):
        abort(403)
    if get_db().execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        abort(404)
    return send_from_directory(current_app.config["SIGNATURE_DIR"], f"{user_id}.png", max_age=0)
