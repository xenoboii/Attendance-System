import logging
import time

import click
from flask import Flask, g, request

from config import Config

from .db import close_db, connect, init_db, upgrade_db
from .logging_setup import setup_logging

log = logging.getLogger(__name__)


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    for key in ("DATA_DIR", "SIGNATURE_DIR", "REPORT_DIR"):
        app.config[key].mkdir(parents=True, exist_ok=True)

    if not app.config.get("TESTING"):
        setup_logging(app.config["LOG_DIR"], "app", app.config["LOG_LEVEL"],
                      to_file=app.config.get("LOG_TO_FILE", True))

    upgrade_db(app.config["DATABASE"])
    _check_report_assets(app)
    _register_static_versions(app)
    app.teardown_appcontext(close_db)
    _register_trash_cleanup(app)
    _register_request_logging(app)
    _register_cli(app)

    from .security import init_security
    init_security(app)
    _register_filters(app)

    from .routes import account, auth, admin, instructor, main, settings, student
    for module in (main, auth, account, settings, student, instructor, admin):
        app.register_blueprint(module.bp)
    if app.config["SIMULATE_HARDWARE"]:
        from .routes import dev
        app.register_blueprint(dev.bp)
        log.info("SIMULATE_HARDWARE is on: dev test page at http://localhost:5000/dev (this computer only)")

    return app


def _register_request_logging(app: Flask) -> None:
    @app.before_request
    def _start_timer():
        g._t0 = time.perf_counter()

    @app.after_request
    def _log_request(response):
        # skip files and the dev auto-refresh check (it runs every second and filled the log)
        # (also the phones' "is there internet?" checks: every phone repeats them all day)
        if not request.path.startswith(("/static", "/dev/livereload", "/generate_204", "/gen_204",
                                        "/hotspot-detect.html", "/connecttest.txt", "/ncsi.txt")):
            ms = (time.perf_counter() - g.get("_t0", time.perf_counter())) * 1000
            log.info("%s %s -> %s (%.0f ms)", request.method, request.path,
                     response.status_code, ms)
        return response

    @app.errorhandler(Exception)
    def _log_error(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        log.exception("Unhandled error on %s %s", request.method, request.path)
        if app.debug:
            raise e  # show the interactive debugger in the browser while developing
        return ("Something went wrong. Check logs/app.log for the details.", 500)


def _register_static_versions(app: Flask) -> None:
    """Let browsers keep CSS / JS / fonts / icons in their cache (no re-download and no icon
    flicker on every click). Each static link gets ?v=<file modified time>, so when a file
    changes its link changes too and the browser fetches the new one."""
    import os
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 365      # 1 year
    cache = {}

    @app.url_defaults
    def _static_version(endpoint, values):
        if endpoint != "static" or "filename" not in values or "v" in values:
            return
        path = os.path.join(app.static_folder, values["filename"])
        try:
            mtime = int(os.stat(path).st_mtime)
        except OSError:
            return
        values["v"] = cache.setdefault((path, mtime), mtime)


def _check_report_assets(app: Flask) -> None:
    """Warn (in logs/app.log) if the report font or school seal is missing - they aren't pip packages."""
    from pathlib import Path
    if not any(Path(p).exists() for p in app.config["REPORT_SCHOOL_FONT_FILES"]):
        log.warning("Report font OLDENGL.TTF not found - copy it to app/static/fonts/ "
                    "(the university name will use a plain serif font until then)")
    if not (Path(app.static_folder) / "img" / "school_logo.png").exists():
        log.warning("School seal app/static/img/school_logo.png not found - reports will have no logo")


def _register_trash_cleanup(app: Flask) -> None:
    """Recently deleted: anything older than TRASH_DAYS is deleted for good.
    Runs at startup and then at most every 6 hours while the app is being used."""
    from .services.trash import purge_expired
    state = {"last": 0.0}

    def run():
        state["last"] = time.time()
        try:
            conn = connect(app.config["DATABASE"])
            purge_expired(conn, app.config["TRASH_DAYS"])
            # drawings from web check-ins are kept for 60 days, then deleted to save space
            from .services import checkin_signatures
            from .services import app_settings      # the admin can change the number of days
            checkin_signatures.purge_old(conn, app.config["SIGNATURE_DIR"],
                                         app_settings.signature_days(conn, app.config.get("CHECKIN_SIGNATURE_DAYS", 60)))
            conn.close()
        except Exception:                                   # never block the app over cleanup
            log.exception("trash cleanup failed")

    from pathlib import Path
    if Path(app.config["DATABASE"]).exists():
        run()

    @app.before_request
    def _maybe_cleanup():
        if time.time() - state["last"] > 6 * 3600:
            run()


def _register_filters(app: Flask) -> None:
    from datetime import datetime as _dt

    @app.template_filter("time12")
    def time12(value):
        """'13:05' -> '1:05 PM'"""
        if not value:
            return ""
        return _dt.strptime(value[:5], "%H:%M").strftime("%I:%M %p").lstrip("0")

    @app.template_filter("suffix")
    def suffix_filter(value):
        """'Jr.....' -> 'Jr.' (also fixes suffixes saved before they were cleaned on save)."""
        from .services.accounts import normalize_suffix
        return normalize_suffix(value)

    @app.template_filter("course_section")
    def course_section(st):
        """Student's program + year + section: 'BSCPE 4A' (or 'BSCPE 4', 'BSCPE', '')."""
        d = dict(st)                                    # works for sqlite rows and dicts
        program, year, letter = d.get("program") or "", d.get("year_level"), d.get("section_letter") or ""
        tail = f"{year or ''}{letter if year else ''}"
        return f"{program} {tail}".strip() if program else tail

    @app.template_filter("nicedate")
    def nicedate(value, with_year=False):
        """'2026-11-13' -> 'Fri, Nov 13'"""
        if not value:
            return ""
        d = _dt.strptime(value[:10], "%Y-%m-%d")
        return d.strftime("%a, %b %d" + (", %Y" if with_year else "")).replace(" 0", " ")


def _register_cli(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.argument("username")
    @click.option("--first-name", prompt="First name")
    @click.option("--last-name", prompt="Last name")
    @click.password_option()
    def create_admin_command(username, first_name, last_name, password):
        """Create an Admin account locally; there is no public Admin registration."""
        from .services.accounts import ValidationError, create_admin
        conn = connect(app.config["DATABASE"])
        try:
            create_admin(conn, {"username": username, "first_name": first_name,
                                    "last_name": last_name, "password": password,
                                    "confirm_password": password})
        except ValidationError as exc:
            raise click.ClickException(str(exc)) from exc
        finally:
            conn.close()
        click.echo("Admin account created. Sign in through the instructor portal.")

    @app.cli.command("list-admins")
    def list_admins_command():
        """Forgot the admin username? Run this on the device to see the admin accounts."""
        conn = connect(app.config["DATABASE"])
        rows = conn.execute("SELECT username, first_name, last_name FROM users "
                            "WHERE role = 'admin' AND deleted_at IS NULL ORDER BY username").fetchall()
        conn.close()
        if not rows:
            click.echo("No admin account yet. Create one:  flask create-admin <username>")
        for r in rows:
            click.echo(f"{r['username']}  ({r['first_name']} {r['last_name']})")

    @app.cli.command("init-db")
    @click.option("--reset", is_flag=True, help="Delete the existing database first.")
    def init_db_command(reset):
        """Create the database tables."""
        init_db(app.config["DATABASE"], reset=reset)
        click.echo(f"Database ready: {app.config['DATABASE']}")

    @app.cli.command("reset-password")
    @click.argument("username")
    def reset_password_command(username):
        """Last resort when someone forgot the password AND lost the backup codes.
        Run it on the Pi itself (needs access to the device):  flask reset-password jdelacruz"""
        from .services.accounts import ValidationError
        from .services.recovery import temporary_password
        conn = connect(app.config["DATABASE"])
        try:
            temp = temporary_password(conn, username)
        except ValidationError as e:
            raise click.ClickException(e.errors["username"])
        finally:
            conn.close()
        click.echo(f"Temporary password for {username}: {temp}")
        click.echo("Log in with it, then change it in My account (and generate new backup codes).")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """RESET the database and fill it with demo accounts + today's sessions."""
        from .seed import seed_demo
        init_db(app.config["DATABASE"], reset=True)
        conn = connect(app.config["DATABASE"])
        seed_demo(conn)
        conn.close()
        click.echo("Demo data loaded. Accounts are listed at the top of app/seed.py")
