"""Admin-only permissions, course management, provisioning and existing data upgrades."""
import sqlite3

import pytest

from app.db import SCHEMA, connect
from app.migrations import MIGRATIONS, migrate
from app.seed import seed_demo
from app.services import accounts, recovery

from .conftest import T0
from .test_auth import NEW_PROF, NEW_STUDENT, PROF_SITE, give_signature, login

ADMIN = {"username": "admin", "first_name": "Dev", "last_name": "Support",
             "password": "dev-password-123", "confirm_password": "dev-password-123"}


@pytest.fixture
def admin(app):
    with connect(app.config["DATABASE"]) as db:
        return accounts.create_admin(db, ADMIN)


def sign_in_as(client, uid):
    with client.session_transaction() as session:
        session["user_id"] = uid


def test_admin_login_and_navigation(app, client, admin):
    response = login(client, "admin", ADMIN["password"])
    assert b"Admins sign in at" in response.data
    response = login(client, "admin", ADMIN["password"], PROF_SITE)
    assert response.location.endswith("/admin/")
    page = client.get("/admin/", base_url=PROF_SITE)
    assert page.status_code == 200
    assert b"Admin portal" in page.data and b"Overview" in page.data and b"Storage" in page.data
    assert b"Add course" in client.get("/admin/courses", base_url=PROF_SITE).data
    assert b"My subjects" not in page.data
    assert client.get("/account/", base_url=PROF_SITE).status_code == 200
    settings = client.get("/settings/", base_url=PROF_SITE)
    assert settings.status_code == 200 and b"Attendance reports" not in settings.data
    assert client.get("/instructor/", base_url=PROF_SITE).status_code == 403
    assert client.get("/student/", base_url=PROF_SITE).status_code == 403


@pytest.mark.parametrize("role", ["instructor", "student"])
def test_course_management_is_exclusively_for_admins(app, client, role):
    give_signature(app, 2)
    with connect(app.config["DATABASE"]) as db:
        uid = 2 if role == "student" else 1
    sign_in_as(client, uid)
    assert client.get("/admin/").status_code == 403
    assert client.post("/admin/courses", data={"code": "BSAUTOE"}).status_code == 403
    assert client.post("/admin/courses/BSCE/status", data={"active": "0"}).status_code == 403


def test_course_catalog_reaches_student_profiles_and_preserves_disabled_values(app, client, admin):
    sign_in_as(client, admin)
    added = client.post("/admin/courses", data={"code": "bsautoe"})
    assert added.status_code == 302
    give_signature(app, 2)
    student = app.test_client()
    sign_in_as(student, 2)
    assert b'<option value="BSAUTOE"' in student.get("/account/").data
    academic = {"program": "BSAUTOE", "year_level": "4", "section_letter": "A"}
    assert student.post("/account/profile", data=academic).status_code == 302
    assert client.post("/admin/courses/BSAUTOE/status", data={"active": "0"}).status_code == 302
    # Existing students retain their course and can still edit other profile fields.
    assert b'<option value="BSAUTOE"' in student.get("/account/").data
    assert student.post("/account/profile", data={"program": "BSAUTOE", "year_level": "4"}).status_code == 302
    give_signature(app, 3)
    other = app.test_client()
    sign_in_as(other, 3)
    assert b'<option value="BSAUTOE"' not in other.get("/account/").data
    assert other.post("/account/profile", data={"program": "BSAUTOE"}).status_code == 400
    assert other.post("/account/profile", data={"program": "INVENTED"}).status_code == 400
    assert client.post("/admin/courses/BSAUTOE/status", data={"active": "1"}).status_code == 302
    assert other.post("/account/profile", data=academic).status_code == 302
    with connect(app.config["DATABASE"]) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_log WHERE user_id = ? AND entity = 'course'",
                          (admin,)).fetchone()[0] == 3


def test_course_validation_and_csrf(app, client, admin):
    sign_in_as(client, admin)
    for code in ("", "TOO-LONG-ABBREVIATION", "<script>", "bscpe"):
        assert client.post("/admin/courses", data={"code": code}).status_code == 400
    assert client.post("/admin/courses/BSCE/status", data={"active": "maybe"}).status_code == 400
    app.config["CSRF_ENABLED"] = True
    assert client.post("/admin/courses", data={"code": "BSAUTOE"}).status_code == 400
    with client.session_transaction() as session:
        token = session["_csrf"]
    assert client.post("/admin/courses", data={"code": "BSAUTOE", "csrf_token": token}).status_code == 302


def test_removing_catalog_entries_keeps_existing_profile_values(app, client, admin):
    sign_in_as(client, admin)
    department = "College of Engineering"
    with connect(app.config["DATABASE"]) as db:
        db.execute("UPDATE users SET department = ? WHERE id = 1", (department,))
        db.execute("UPDATE users SET program = 'BSIT', year_level = 3, section_letter = 'A', academic_locked = 1 WHERE id = 2")

    prof = app.test_client()
    sign_in_as(prof, 1)
    assert prof.post("/admin/courses/BSIT/remove").status_code == 403
    assert prof.post("/admin/departments/remove", data={"name": department}).status_code == 403
    assert client.post("/admin/courses/BSIT/remove").status_code == 302
    assert client.post("/admin/departments/remove", data={"name": department}).status_code == 302
    assert b"Actions for BSIT" not in client.get("/admin/courses").data
    assert b"Actions for College of Engineering" not in client.get("/admin/departments").data

    give_signature(app, 2)
    student = app.test_client()
    sign_in_as(student, 2)
    assert b'<option value="BSIT"' in student.get("/account/").data
    assert b'<option value="College of Engineering" selected>' in prof.get("/account/").data
    assert b'<option value="BSIT"' in prof.get("/instructor/sections/1?tab=settings").data
    assert b'<option value="BSIT"' not in prof.get("/instructor/subjects/1").data


def test_public_registration_cannot_create_a_admin(app, client):
    client.post("/register/student", data={**NEW_STUDENT, "role": "admin"})
    prof = app.test_client()
    prof.post("/register/instructor", data={**NEW_PROF, "role": "admin"}, base_url=PROF_SITE)
    with connect(app.config["DATABASE"]) as db:
        assert db.execute("SELECT role FROM users WHERE username = ?", (NEW_STUDENT["student_number"],)).fetchone()[0] == "student"
        assert db.execute("SELECT role FROM users WHERE username = ?", (NEW_PROF["username"],)).fetchone()[0] == "instructor"


def test_create_admin_cli_and_backup_code_recovery(app, client):
    result = app.test_cli_runner().invoke(args=["create-admin", "support",
                                              "--first-name", "Dev", "--last-name", "Support"],
                                          input="dev-password-123\ndev-password-123\n")
    assert result.exit_code == 0, result.output
    assert "Admin account created" in result.output
    with connect(app.config["DATABASE"]) as db:
        user = db.execute("SELECT * FROM users WHERE username = 'support'").fetchone()
        assert user["role"] == "admin" and user["password_hash"] != "dev-password-123"
        codes = recovery.generate(db, user["id"])
        recovery.reset_password(db, {"username": "support", "code": codes[0],
                                    "new_password": "replacement123", "confirm_new_password": "replacement123"})
    assert login(client, "support", "replacement123", PROF_SITE).location.endswith("/admin/")


def test_upgrade_preserves_accounts_attendance_and_references(tmp_path):
    db = connect(tmp_path / "old.db")
    db.executescript(SCHEMA.read_text(encoding="utf-8"))
    for sql in MIGRATIONS[:10]:
        db.executescript(sql)
    db.execute("PRAGMA user_version = 10")
    seed_demo(db, T0)
    db.execute("UPDATE users SET program = 'BSLEGACY', theme = 'dark', section_letter = 'A' WHERE id = 2")
    db.execute("INSERT INTO recovery_codes (user_id, code_hash, created_at) VALUES (1, 'keep-me', '2026-11-14')")
    db.execute("INSERT INTO users (id, role, username, password_hash, first_name, last_name) "
               "VALUES (150, 'instructor', 'deleted', 'hash', 'Old', 'Account')")
    db.execute("DELETE FROM users WHERE id = 150")
    db.commit()
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    columns = {table: [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')] for table in tables}
    snapshot = {table: [tuple(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')] for table in tables}
    assert migrate(db) == len(MIGRATIONS)
    for table, rows in snapshot.items():
        selected = ', '.join(f'"{column}"' for column in columns[table])
        assert [tuple(row) for row in db.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid')] == rows
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("SELECT 1 FROM courses WHERE code = 'BSLEGACY'").fetchone()
    assert accounts.create_admin(db, ADMIN) == 151
    assert migrate(db) == len(MIGRATIONS)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO users (role, username, password_hash, first_name, last_name) "
                   "VALUES ('invalid', 'bad', 'hash', 'Bad', 'Role')")
    db.close()
