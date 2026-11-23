from datetime import datetime

from flask import Blueprint, current_app, g, jsonify, redirect, url_for

from ..db import get_db
from ..security import dashboard_for

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    if g.user:
        return redirect(dashboard_for(g.user))
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------- "is there internet?" checks
# The device's Wi-Fi has no internet. A phone that notices switches back to mobile data, and then
# http://attendance.lan stops opening unless the student turns mobile data off. Phones decide by
# fetching a known address and looking for a known answer; the hotspot points every name at this
# device (deploy/hotspot.sh), and these routes give the expected answers, so the phone keeps
# using the Wi-Fi.
_APPLE_OK = "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"


@bp.get("/generate_204")          # Android, Chrome
@bp.get("/gen_204")
def probe_android():
    return "", 204


@bp.get("/hotspot-detect.html")   # iPhone, iPad, Mac
@bp.get("/library/test/success.html")
def probe_apple():
    return _APPLE_OK, 200, {"Content-Type": "text/html"}


@bp.get("/connecttest.txt")       # Windows
def probe_windows():
    return "Microsoft Connect Test", 200, {"Content-Type": "text/plain"}


@bp.get("/ncsi.txt")              # older Windows
def probe_windows_old():
    return "Microsoft NCSI", 200, {"Content-Type": "text/plain"}


@bp.get("/success.txt")           # Firefox
@bp.get("/canonical.html")
def probe_firefox():
    return "success\n", 200, {"Content-Type": "text/plain"}


PROBE_ENDPOINTS = {"main.probe_android", "main.probe_apple", "main.probe_windows",
                   "main.probe_windows_old", "main.probe_firefox"}


@bp.get("/health")
def health():
    """Quick check that the app + database work: open http://<pi>/health"""
    info = {"time": datetime.now().isoformat(timespec="seconds"),
            "simulate_hardware": current_app.config["SIMULATE_HARDWARE"]}
    try:
        db = get_db()
        info["database"] = "ok"
        info["users"] = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        info["sessions_today"] = db.execute(
            "SELECT COUNT(*) FROM class_sessions WHERE session_date = date('now','localtime')"
        ).fetchone()[0]
        status = 200
    except Exception as e:  # noqa: BLE001 - report any DB problem
        info["database"] = f"error: {e}"
        status = 500
    return jsonify(info), status
