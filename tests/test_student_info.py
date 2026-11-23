"""Instructor read access and Admin corrections to student identity and cards."""
from werkzeug.security import generate_password_hash

from app.db import connect
from app.services import accounts
from app.services.accounts import ValidationError

from .test_auth import PROF_SITE, give_signature, login

MARIA = 2


def prof(client):
    login(client, "prof", "prof123", PROF_SITE)


def url(sid=MARIA, section=1, extra=""):
    return f"/instructor/sections/{section}/students/{sid}{extra}"


def test_info_page_shows_profile_card_and_attendance(app, client):
    c = connect(app.config["DATABASE"])
    c.execute("UPDATE users SET program='BSIT', year_level=3, contact_number='09171234567', email='m@x.com' WHERE id=2")
    c.commit()
    prof(client)
    page = client.get(url(), base_url=PROF_SITE).data
    for text in (b"Santos, Maria", b"2026-0001", b"BSIT", b"09171234567", b"m@x.com",
                 "••••0001".encode(), b"Attendance rate", b"Attendance summary"):
        assert text in page, text
    assert b"table-modern" not in page                  # summary only, no per-date attendance list


def test_students_list_links_to_info(client):
    prof(client)
    page = client.get("/instructor/sections/1?tab=students", base_url=PROF_SITE).data
    assert url().encode() in page and b"View student" in page


def test_only_own_students(app, client):
    c = connect(app.config["DATABASE"])
    c.execute("INSERT INTO users (role, username, password_hash, first_name, last_name) "
              "VALUES ('instructor', 'other', ?, 'O', 'T')", (generate_password_hash("other1234"),))
    c.commit()
    login(client, "other", "other1234", PROF_SITE)
    assert client.get(url(), base_url=PROF_SITE).status_code == 404
    assert client.post(url(extra="/remove-card"), base_url=PROF_SITE).status_code == 403


def test_not_enrolled_is_404(app, client):
    c = connect(app.config["DATABASE"])
    c.execute("UPDATE enrollments SET deleted_at = '2026-01-01' WHERE student_id = 2")
    c.commit()
    prof(client)
    assert client.get(url(), base_url=PROF_SITE).status_code == 404


def test_correct_name_and_number(app, client, admin_account):
    login(client, admin_account["username"], admin_account["password"], PROF_SITE)
    r = client.post("/admin/students/2", base_url=PROF_SITE, data={
        "student_number": "2026-0101", "first_name": "Maria Clara", "last_name": "Santos-Reyes"})
    assert r.status_code == 302
    u = connect(app.config["DATABASE"]).execute("SELECT * FROM users WHERE id = 2").fetchone()
    assert (u["student_number"], u["username"], u["first_name"], u["last_name"]) == \
           ("2026-0101", "2026-0101", "Maria Clara", "Santos-Reyes")
    # student logs in with the corrected number
    give_signature(app, 2)
    student = client.application.test_client()
    assert login(student, "2026-0101", "student123").location.endswith("/student/")


def test_correct_identity_validation(conn):
    try:
        accounts.correct_student_identity(conn, MARIA, {"student_number": "2026-0002", "first_name": "M",
                                                        "last_name": "S"})
    except ValidationError as e:
        assert "already uses" in e.errors["student_number"]
    else:
        raise AssertionError("duplicate number accepted")


def test_identity_error_stays_on_admin_form(client, admin_account):
    login(client, admin_account["username"], admin_account["password"], PROF_SITE)
    r = client.post("/admin/students/2", base_url=PROF_SITE,
                    data={"student_number": "2026-0001", "first_name": "", "last_name": "Santos"})
    assert r.status_code == 400 and b"Student information" in r.data and b"This field is required" in r.data


def test_remove_card(app, client, admin_account):
    login(client, admin_account["username"], admin_account["password"], PROF_SITE)
    client.post("/admin/students/2/remove-card", base_url=PROF_SITE)
    c = connect(app.config["DATABASE"])
    assert c.execute("SELECT rfid_uid FROM users WHERE id = 2").fetchone()[0] is None
    assert c.execute("SELECT action FROM audit_log WHERE action = 'remove_card'").fetchone()
