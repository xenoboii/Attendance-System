"""
Login sessions, role checks and CSRF protection.

    @login_required                  any logged-in user
    @login_required("instructor")    only instructors

CSRF: every POST form must include  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      fetch() calls send the header  X-CSRF-Token  (see static/js/app.js)
"""
import logging
import secrets
from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for

from .db import get_db
from .services import accounts

log = logging.getLogger(__name__)

# Pages a student can open before drawing their signature
SIGNATURE_EXEMPT = {"auth.signature", "auth.logout", "static", "auth.signature_image",
                    # backup codes come right after registering, before the signature
                    "auth.backup_codes", "auth.backup_codes_pdf", "auth.backup_codes_done",
                    # phones' "is there internet?" checks (routes/main.py) must always get their answer
                    "main.probe_android", "main.probe_apple", "main.probe_windows",
                    "main.probe_windows_old", "main.probe_firefox"}


def dashboard_for(user) -> str:
    if user["role"] == "admin":
        return url_for("admin.dashboard")
    return url_for("instructor.dashboard" if user["role"] == "instructor"
                   else "student.dashboard")


def login_required(arg=None):
    roles = (arg,) if isinstance(arg, str) else None

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("auth.login", next=request.path))
            if roles and g.user["role"] not in roles:
                log.warning("user %s (%s) blocked from %s", g.user["id"], g.user["role"], request.path)
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    return decorator(arg) if callable(arg) else decorator


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def init_security(app) -> None:
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def load_user_and_check():
        g.user = None
        # restoring a backup uploads a big file: raise the 2 MB upload limit for that page only
        # (must happen before anything reads the form, e.g. the CSRF check below)
        if request.endpoint in ("admin.backup_restore", "settings.backup_import"):
            request.max_content_length = app.config.get("BACKUP_MAX_BYTES", 300 * 1024 * 1024)
        uid = session.get("user_id")
        if uid is not None:
            g.user = accounts.get_user(get_db(), uid)
            if g.user is None:          # deleted / deactivated while logged in
                session.clear()

        # CSRF check on every POST (the dev test page is exempt)
        if (request.method == "POST" and app.config.get("CSRF_ENABLED", True)
                and request.blueprint != "dev"):
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
                log.warning("CSRF check failed on %s", request.path)
                abort(400, "Your form expired. Please go back, refresh the page and try again.")

        # Students must draw their signature before using anything else
        if (g.user is not None and accounts.needs_signature(g.user)
                and request.endpoint not in SIGNATURE_EXEMPT and request.blueprint != "dev"):
            return redirect(url_for("auth.signature"))

    @app.context_processor
    def inject_user():
        user = g.get("user")
        device_battery = None
        if user is not None and user["role"] == "instructor":
            from .services import battery
            device_battery = battery.get(get_db())
        photo = get_db().execute("SELECT revision FROM profile_photos WHERE user_id = ?",
                                 (user["id"],)).fetchone() if user is not None else None
        return {"current_user": user, "is_prof_site": is_prof_site(), "device_battery": device_battery,
                "profile_photo_revision": photo["revision"] if photo else None}

    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")


def is_prof_site() -> bool:
    """True when the page was opened through the instructor address (prof.attendance.lan).
    Any address starting with "prof." counts too, so other devices can test the laptop through
    e.g. prof.192.168.39.47.nip.io (nip.io turns that name into 192.168.39.47)."""
    host = request.host.split(":")[0].lower()
    return host in current_app.config["PROF_HOSTS"] or host.startswith("prof.")
