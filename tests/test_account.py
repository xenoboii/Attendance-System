"""My Account page, password change, signature lock + instructor reset, migrations."""
from app.db import connect, init_db
from app.migrations import MIGRATIONS, migrate

from .test_auth import PROF_SITE, TINY_PNG, give_signature, login


def db(app):
    return connect(app.config["DATABASE"])


def student(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")


# ---------------------------------------------------------------- migrations
def test_new_database_has_latest_version(tmp_path):
    init_db(tmp_path / "x.db")
    c = connect(tmp_path / "x.db")
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    cols = [r["name"] for r in c.execute("PRAGMA table_info(users)")]
    assert {"program", "year_level", "contact_number", "department", "title"} <= set(cols)


def test_migrate_is_safe_to_repeat(conn):
    assert migrate(conn) == len(MIGRATIONS)
    assert migrate(conn) == len(MIGRATIONS)


# ---------------------------------------------------------------- student profile
def test_student_account_page(app, client):
    student(app, client)
    r = client.get("/account/")
    assert r.status_code == 200
    assert b"2026-0001" in r.data and b"only the admin can correct them" in r.data
    assert "••••0001".encode() in r.data            # masked card
    assert b'value="None"' not in r.data            # empty fields stay empty


def test_student_profile_update(app, client):
    student(app, client)
    r = client.post("/account/profile", data={"program": "bsit", "year_level": "3", "section_letter": "A",
                                             "contact_number": "0917 123 4567", "email": "m@x.com",
                                             "first_name": "Hacker"})
    assert r.status_code == 302
    u = db(app).execute("SELECT * FROM users WHERE id = 2").fetchone()
    assert (u["program"], u["year_level"], u["contact_number"]) == ("BSIT", 3, "09171234567")
    assert u["first_name"] == "Maria"               # students cannot rename themselves


def test_profile_validation(app, client):
    student(app, client)
    r = client.post("/account/profile", data={"contact_number": "12345", "year_level": "9"})
    assert r.status_code == 400
    assert b"09XXXXXXXXX" in r.data and b"Choose a year level" in r.data


def test_instructor_profile_update(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    r = client.post("/account/profile", base_url=PROF_SITE, data={
        "title": "Engr.", "first_name": "Juan", "last_name": "Dela Cruz",
        "department": "College of Computer Studies"})
    assert r.status_code == 302
    u = db(app).execute("SELECT title, department FROM users WHERE id = 1").fetchone()
    assert tuple(u) == ("Engr.", "College of Computer Studies")


# ---------------------------------------------------------------- password
def test_change_password(app, client):
    student(app, client)
    bad = client.post("/account/password", data={"current_password": "wrong",
                                                  "new_password": "newpass123", "confirm_new_password": "newpass123"})
    assert bad.status_code == 400 and b"current password is wrong" in bad.data
    ok = client.post("/account/password", data={"current_password": "student123",
                                                "new_password": "newpass123", "confirm_new_password": "newpass123"})
    assert ok.status_code == 302
    client.post("/logout")
    assert login(client, "2026-0001", "newpass123").location.endswith("/student/")


# ---------------------------------------------------------------- signature lock
def test_student_cannot_redraw_signature_freely(app, client):
    student(app, client)
    assert client.get("/signature").location.endswith("/account/")
    assert client.post("/signature", data={"signature": TINY_PNG}).location.endswith("/account/")


def test_admin_reset_lets_student_sign_again(app, client, admin_account):
    login(client, admin_account["username"], admin_account["password"], PROF_SITE)
    r = client.post("/admin/students/2/signature-reset", base_url=PROF_SITE)
    assert r.status_code == 302
    assert db(app).execute("SELECT must_resign FROM users WHERE id = 2").fetchone()[0] == 1

    other = client.application.test_client()
    give_signature(app, 2)   # has an old signature, but must_resign = 1
    c = db(app); c.execute("UPDATE users SET must_resign = 1 WHERE id = 2"); c.commit()
    login(other, "2026-0001", "student123")
    assert other.get("/student/").location.endswith("/signature")
    assert other.post("/signature", data={"signature": TINY_PNG}).location.endswith("/student/")
    assert db(app).execute("SELECT must_resign FROM users WHERE id = 2").fetchone()[0] == 0


def test_instructor_can_update_own_signature(client):
    login(client, "prof", "prof123", PROF_SITE)
    r = client.post("/signature", data={"signature": TINY_PNG}, base_url=PROF_SITE)
    assert r.location.endswith("/instructor/")


def test_department_is_picked_from_the_list(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    base = {"first_name": "Juan", "last_name": "Dela Cruz"}
    page = client.get("/account/", base_url=PROF_SITE).data
    assert b'<select id="department"' in page and b"College of Engineering</option>" in page
    bad = client.post("/account/profile", data={**base, "department": "asdfgh"}, base_url=PROF_SITE)
    assert bad.status_code == 400 and b"Choose a department from the list." in bad.data
    ok = client.post("/account/profile", data={**base, "department": "College of Engineering"}, base_url=PROF_SITE)
    assert ok.status_code == 302
    # an older typed-in value is kept (and stays selectable) until the instructor changes it
    c = db(app); c.execute("UPDATE users SET department = 'Old Typed Dept' WHERE id = 1"); c.commit()
    assert b'<option value="Old Typed Dept" selected>' in client.get("/account/", base_url=PROF_SITE).data
    assert client.post("/account/profile", data={**base, "department": "Old Typed Dept"}, base_url=PROF_SITE).status_code == 302

def test_admin_manages_the_department_list(app, client):
    from app.services import accounts
    with connect(app.config["DATABASE"]) as c:
        admin = accounts.create_admin(c, {"username": "admin", "first_name": "Dev", "last_name": "Support",
                                          "password": "dev-password-123", "confirm_password": "dev-password-123"})
    boss = app.test_client()
    with boss.session_transaction() as s:
        s["user_id"] = admin
    page = boss.get("/admin/departments").data
    assert b"College of Engineering" in page and b"Add department" in page
    assert boss.post("/admin/departments", data={"name": "  College of   Medicine "}).status_code == 302
    for bad in ("", "ab", "College of Medicine", "<b>x</b>", "x" * 81):
        assert boss.post("/admin/departments", data={"name": bad}).status_code == 400
    login(client, "prof", "prof123", PROF_SITE)
    base = {"first_name": "Juan", "last_name": "Dela Cruz"}
    assert b"College of Medicine</option>" in client.get("/account/", base_url=PROF_SITE).data
    assert client.post("/account/profile", data={**base, "department": "College of Medicine"}, base_url=PROF_SITE).status_code == 302
    # disabled: gone for others, kept for the instructor who already uses it
    assert boss.post("/admin/departments/status", data={"name": "College of Medicine", "active": "0"}).status_code == 302
    assert boss.post("/admin/departments/status", data={"name": "College of Law", "active": "0"}).status_code == 302
    mine = client.get("/account/", base_url=PROF_SITE).data
    assert b'<option value="College of Medicine" selected>' in mine and b"College of Law</option>" not in mine
    assert client.post("/account/profile", data={**base, "department": "College of Law"}, base_url=PROF_SITE).status_code == 400
    assert boss.post("/admin/departments/status", data={"name": "Nope", "active": "1"}).status_code == 400
    assert client.get("/admin/departments", base_url=PROF_SITE).status_code == 403
