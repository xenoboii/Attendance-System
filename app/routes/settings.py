"""Settings page (students + instructors): theme, text size, report default, device backup."""
import io

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.security import check_password_hash

from ..db import get_db
from ..security import login_required
from ..migrations import MIGRATIONS
from ..services import accounts, backup, instructor_import

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.get("/")
@login_required
def index():
    status = None
    if g.user["role"] == "instructor":
        status = backup.instructor_status(get_db(), g.user["id"])
    return render_template("settings.html", backup=status, remind_days=backup.REMIND_AFTER_DAYS)


@bp.post("/backup")
@login_required("instructor")
def backup_download():
    """An instructor's own backup: only THEIR classes (never another instructor's), with their
    students' signatures. Needs their password. Restoring the device stays with the admin's full backup."""
    if not check_password_hash(g.user["password_hash"], request.form.get("password") or ""):
        flash("Wrong password. The backup was not downloaded.", "warning")
        return redirect(url_for(".index") + "#backup")
    cfg = current_app.config
    data = backup.create_for_instructor(get_db(), cfg["DATABASE"], cfg["SIGNATURE_DIR"], dict(g.user))
    backup.mark_instructor_download(get_db(), g.user["id"])
    resp = send_file(io.BytesIO(data), mimetype="application/zip", as_attachment=True,
                     download_name=backup.instructor_filename(g.user), max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/import")
@login_required("instructor")
def backup_import():
    """Bring back the instructor's own subjects from their backup file (e.g. after the device was
    rebuilt). Adds to what is here; never replaces anything. Needs their password."""
    back = redirect(url_for(".index") + "#backup")
    if not check_password_hash(g.user["password_hash"], request.form.get("password") or ""):
        flash("Wrong password. Nothing was imported.", "warning")
        return back
    upload = request.files.get("file")
    data = upload.read() if upload and upload.filename else b""
    try:
        r = instructor_import.import_backup(get_db(), data, g.user["id"], current_app.config["SIGNATURE_DIR"],
                                            len(MIGRATIONS))
    except accounts.ValidationError as e:
        flash(e.errors["file"], "warning")
        return back
    skipped = r["subjects_skipped"]
    if r["nothing_new"]:
        flash("Nothing to import: you already have " + ", ".join(skipped) + ".", "info")
        return back
    plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"      # noqa: E731
    msg = (f"Imported {plural(r['subjects'], 'subject')}, {plural(r['sections'], 'section')} and "
           f"{plural(r['attendance'], 'attendance record')}. "
           f"{plural(r['students_matched'], 'student')} already registered here, "
           f"{r['students_new']} will get their classes when they register with the same student number.")
    if skipped:
        msg += " Skipped (you already have them): " + ", ".join(skipped) + "."
    flash(msg, "info")
    return redirect(url_for("instructor.dashboard"))


@bp.post("/")
@login_required
def save():
    try:
        accounts.update_settings(get_db(), g.user, request.form)
    except accounts.ValidationError:
        abort(400)
    if request.headers.get("X-Requested-With") == "fetch":     # saved instantly from the page
        return jsonify(ok=True)
    return redirect(url_for(".index"))
