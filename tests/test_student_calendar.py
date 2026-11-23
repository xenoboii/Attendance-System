"""Student: My classes page, dashboard calendar. Admin: backup-code help desk."""
from datetime import date, datetime

from app.db import connect
from app.services import accounts, calendar_view, classes, recovery, sessions

from .conftest import MARIA
from .test_auth import PROF_SITE, give_signature, login

PROF = 1
NOW = datetime(2026, 11, 16, 7, 1)            # Monday (same as the fixtures' T0)


def test_month_view_marks_class_days_and_no_class(conn):
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": "2026-11-25", "reason": "Holiday"})
    cal = calendar_view.month_view(conn, MARIA, 2026, 11, NOW)
    days = {d["iso"]: d for w in cal["weeks"] for d in w}
    assert cal["label"] == "November 2026" and cal["prev"] == "2026-10" and cal["next"] == "2026-12"
    assert len(cal["weeks"][0]) == 7 and cal["weeks"][0][0]["date"].weekday() == 6        # Sunday first
    assert days["2026-11-18"]["has_class"] and not days["2026-11-18"]["has_no_class"]
    off = days["2026-11-25"]
    assert off["has_no_class"] and [i["reason"] for i in off["events"] if i["kind"] == "no_class"] == ["Holiday"]
    assert days["2026-11-16"]["is_today"] and cal["selected"] == "2026-11-16"
    assert [n["reason"] for n in cal["notices"]] == ["Holiday"] and cal["notices"][0]["when"] == "In 9 days"


def test_month_view_shows_makeup_and_hides_other_students_classes(conn):
    sessions.create_session(conn, PROF, 1, {"type": "makeup", "session_date": "2026-11-21", "start_time": "08:00"}, NOW)
    cal = calendar_view.month_view(conn, MARIA, 2026, 11, NOW)
    days = {d["iso"]: d for w in cal["weeks"] for d in w}
    assert days["2026-11-21"]["has_extra"]
    outsider = conn.execute("INSERT INTO users (role, username, student_number, password_hash, first_name, last_name) "
                            "VALUES ('student', '2026-7777', '2026-7777', 'x', 'No', 'Body')").lastrowid
    empty = calendar_view.month_view(conn, outsider, 2026, 11, NOW)
    assert not any(d["events"] for w in empty["weeks"] for d in w) and empty["notices"] == []


def test_parse_month():
    today = date(2026, 11, 16)
    assert calendar_view.parse_month("2026-12", today) == (2026, 12)
    for bad in ("", None, "abc", "2026-13", "1999-01", "2090-01"):
        assert calendar_view.parse_month(bad, today) == (2026, 11)


def test_dashboard_has_calendar_and_classes_moved(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    dash = client.get("/student/").data
    assert b'id="calendar"' in dash and b"data-cal-title>Today&#39;s classes<" in dash
    assert dash.index(b'id="calendar"') < dash.index(b"data-cal-title")                # calendar first (left)
    assert dash.count(b"data-cal-day=") == dash.count(b"data-cal-detail=") >= 28       # one panel per day
    assert b'name="join_code"' not in dash                                              # moved to My classes
    side = dash[dash.index(b'class="side-nav"'):]
    assert side.index(b"Dashboard") < side.index(b"My classes") < side.index(b"My account")
    assert client.get("/student/?m=2026-12").status_code == 200
    assert client.get("/student/?m=nonsense").status_code == 200
    page = client.get("/student/classes").data
    assert b'name="join_code"' in page and b"IT312" in page and b"Leave class" in page
    assert client.get("/student/classes", base_url=PROF_SITE).status_code in (302, 403)


# ---------------------------------------------------------------- admin help desk
ADMIN = {"username": "admin", "first_name": "Dev", "last_name": "Support",
         "password": "dev-password-123", "confirm_password": "dev-password-123"}


def _admin(app, client):
    with connect(app.config["DATABASE"]) as db:
        uid = accounts.create_admin(db, ADMIN)
    with client.session_transaction() as s:
        s["user_id"] = uid
    return uid


def test_admin_issues_one_code_for_a_student(app, client):
    admin = _admin(app, client)
    with connect(app.config["DATABASE"]) as db:
        old = recovery.generate(db, 2)
    page = client.get("/admin/backup-codes").data
    assert b"2026-0001" in page and b"prof" in page and b"10 of 10 unused" in page
    assert client.get("/admin/backup-codes?q=2026-0001").data.count(b"Issue a code") >= 1
    assert client.get("/admin/backup-codes/issued").status_code == 302                   # nothing issued yet
    assert client.post("/admin/backup-codes/2").location.endswith("/admin/backup-codes/issued")
    shown = client.get("/admin/backup-codes/issued").data
    with client.session_transaction() as s:
        new = s["issued_codes"]["codes"]
    assert len(new) == 1 and new[0].encode() in shown and old[0].encode() not in shown
    form = {"username": "2026-0001", "new_password": "brandnew123", "confirm_new_password": "brandnew123"}
    with connect(app.config["DATABASE"]) as db:
        assert recovery.status(db, 2)["unused"] == 11                                    # their own set is untouched
        assert recovery.reset_password(db, {**form, "code": new[0]}, student=True) == 2
        try:
            recovery.reset_password(db, {**form, "code": new[0]}, student=True)
            assert False, "the code works once"
        except accounts.ValidationError:
            pass
        assert db.execute("SELECT COUNT(*) FROM audit_log WHERE user_id = ? AND action = 'issue_backup_codes'",
                          (admin,)).fetchone()[0] == 1
    assert client.post("/admin/backup-codes/issued/done").status_code == 302
    assert client.get("/admin/backup-codes/issued").status_code == 302                   # gone for good


def test_list_admins_command(app, client):
    assert "No admin account yet" in app.test_cli_runner().invoke(args=["list-admins"]).output
    _admin(app, client)
    assert "admin  (Dev Support)" in app.test_cli_runner().invoke(args=["list-admins"]).output


def test_backup_code_desk_is_admin_only(app, client):
    admin = _admin(app, client)
    assert client.post(f"/admin/backup-codes/{admin}").status_code == 404                # not for admin accounts
    assert client.post("/admin/backup-codes/9999").status_code == 404
    prof = app.test_client()
    login(prof, "prof", "prof123", PROF_SITE)
    assert prof.get("/admin/backup-codes", base_url=PROF_SITE).status_code == 403
    assert prof.post("/admin/backup-codes/2", base_url=PROF_SITE).status_code == 403
