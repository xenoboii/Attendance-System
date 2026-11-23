"""My Account page: profile, picture, attendance ID and password."""
import io

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash

from ..db import get_db
from ..security import login_required
from ..services import account_deletion, accounts, courses, departments, profile_photos, recovery

bp = Blueprint("account", __name__, url_prefix="/account")


def _render(profile_errors=None, password_errors=None, form=None, status=200, codes_error=None, photo_error=None):
    user = g.user
    uid = user["rfid_uid"]
    return render_template(
        "account.html",
        profile_errors=profile_errors or {}, password_errors=password_errors or {},
        form=form or dict(user),
        masked_card=("••••" + uid[-4:]) if uid else None,
        can_change_signature=accounts.can_change_signature(user),
        codes=recovery.status(get_db(), user["id"]),
        codes_error=codes_error,
        courses=courses.list_courses(get_db(), current=user["program"]),
        photo_error=photo_error,
        departments=[d["name"] for d in departments.list_departments(get_db(), current=user["department"])],
    ), status


@bp.post("/backup-codes")
@login_required
def new_backup_codes():
    """Generate a new set (like Discord): password required, the old set stops working."""
    if not check_password_hash(g.user["password_hash"], request.form.get("password") or ""):
        return _render(codes_error="Wrong password.", status=400)
    session["backup_codes"] = recovery.generate(get_db(), g.user["id"])
    return redirect(url_for("auth.backup_codes"))


@bp.get("/")
@login_required
def index():
    return _render()


@bp.post("/delete")
@login_required
def delete_me():
    """Student / instructor deletes their own account: password + a tick box. The admin can
    still restore it (see services/account_deletion.py)."""
    if g.user["role"] not in ("student", "instructor"):
        abort(403)
    errors = {}
    if not check_password_hash(g.user["password_hash"], request.form.get("password") or ""):
        errors["delete_password"] = "Wrong password."
    if request.form.get("confirm") != "1":
        errors["delete_confirm"] = "Tick the box to confirm."
    if errors:
        return _render(profile_errors=errors, status=400)
    account_deletion.delete_account(get_db(), g.user["id"], g.user["id"])
    session.clear()
    flash("Your account was deleted. If this was a mistake, ask the admin to restore it.", "info")
    return redirect(url_for("auth.login"))


@bp.get("/photo")
@login_required
def photo_image():
    photo = get_db().execute("SELECT image, revision FROM profile_photos WHERE user_id = ?",
                             (g.user["id"],)).fetchone()
    if photo is None:
        abort(404)
    response = send_file(io.BytesIO(photo["image"]), mimetype="image/jpeg", max_age=0,
                         conditional=True, etag=photo["revision"],
                         as_attachment=request.args.get("download") == "1", download_name="profile-picture.jpg")
    response.cache_control.private = True
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/photo")
@login_required
def photo_upload():
    upload = request.files.get("photo")
    try:
        profile_photos.save(get_db(), g.user["id"], upload.stream if upload else io.BytesIO(), request.form)
    except accounts.ValidationError as e:
        return _render(photo_error=e.errors["photo"], status=400)
    flash("Profile picture saved.", "success")
    return redirect(url_for(".index"))


@bp.post("/photo/remove")
@login_required
def photo_remove():
    profile_photos.remove(get_db(), g.user["id"])
    flash("Profile picture removed.", "success")
    return redirect(url_for(".index"))


@bp.app_errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    if request.endpoint == "account.photo_upload" and g.get("user") is not None:
        return _render(photo_error="Choose a picture smaller than 2 MB.", status=413)
    return error


@bp.post("/profile")
@login_required
def profile():
    try:
        accounts.update_profile(get_db(), g.user, request.form)
    except accounts.ValidationError as e:
        return _render(profile_errors=e.errors, form={**dict(g.user), **request.form.to_dict()}, status=400)
    flash("Profile saved.", "success")
    return redirect(url_for(".index"))


@bp.post("/password")
@login_required
def password():
    try:
        accounts.change_password(get_db(), g.user, request.form)
    except accounts.ValidationError as e:
        return _render(password_errors=e.errors, status=400)
    flash("Password changed.", "success")
    return redirect(url_for(".index"))
