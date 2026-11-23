"""Login, registration (student + instructor), signature, roles, CSRF, prof address."""
import pytest

from app.db import connect

STUDENT_SITE = "http://localhost"
PROF_SITE = "http://prof.localhost"

# 1x1 transparent PNG
TINY_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

NEW_STUDENT = {"student_number": "2026-0099", "first_name": "Liza", "last_name": "Cruz",
               "program": "BSCPE", "year_level": "4", "section_letter": "A",
               "password": "password123", "confirm_password": "password123"}
NEW_PROF = {"registration_code": "PROF-2026", "username": "mreyes", "first_name": "Mario",
            "last_name": "Reyes", "password": "password123", "confirm_password": "password123"}


def db(app):
    return connect(app.config["DATABASE"])


def give_signature(app, user_id):
    c = db(app)
    c.execute("UPDATE users SET signature_path = 'signatures/x.png' WHERE id = ?", (user_id,))
    c.commit()


def login(client, who, pw, site=STUDENT_SITE):
    return client.post("/login", data={"login": who, "password": pw}, base_url=site)


# ---------------------------------------------------------------- two sites
def test_student_and_prof_login_pages_differ(client):
    assert b"Student Login" in client.get("/login", base_url=STUDENT_SITE).data
    assert b"Instructor Login" in client.get("/login", base_url=PROF_SITE).data


def test_instructor_registration_only_on_prof_site(client):
    assert client.get("/register/instructor", base_url=STUDENT_SITE).status_code == 404
    assert client.get("/register/instructor", base_url=PROF_SITE).status_code == 200


def test_prof_site_from_another_device(client):
    # phone on the same hotspot: prof.<laptop ip>.nip.io = prof site, plain IP = student site
    assert client.get("/register/instructor", base_url="http://prof.192.168.39.47.nip.io:5000").status_code == 200
    assert client.get("/register/instructor", base_url="http://192.168.39.47:5000").status_code == 404


def test_register_link_goes_to_the_right_form(client):
    assert client.get("/register", base_url=STUDENT_SITE).location.endswith("/register/student")
    assert client.get("/register", base_url=PROF_SITE).location.endswith("/register/instructor")


def test_instructor_cannot_login_on_student_site(client):
    r = login(client, "prof", "prof123", STUDENT_SITE)
    assert b"Instructors sign in at" in r.data


def test_student_cannot_login_on_prof_site(app, client):
    give_signature(app, 2)
    r = login(client, "2026-0001", "student123", PROF_SITE)
    assert b"Students sign in at" in r.data


# ---------------------------------------------------------------- student registration
def test_student_registration_then_signature(app, client):
    r = client.post("/register/student", data=NEW_STUDENT)
    assert r.location.endswith("/backup-codes")                  # 2. backup codes
    assert client.get("/backup-codes").status_code == 200         # (allowed before signing)
    r = client.post("/backup-codes/done")
    assert r.location.endswith("/signature")                     # 3. signature

    # can't open the dashboard before signing
    assert client.get("/student/").location.endswith("/signature")

    r = client.post("/signature", data={"signature": TINY_PNG})
    assert r.location.endswith("/student/")
    assert b"Liza!" in client.get("/student/").data

    user = db(app).execute("SELECT * FROM users WHERE student_number = '2026-0099'").fetchone()
    assert (app.config["SIGNATURE_DIR"] / f"{user['id']}.png").exists()
    assert user["signature_signed_at"]


def test_signature_rejects_empty_and_non_png(client):
    client.post("/register/student", data=NEW_STUDENT)
    assert b"draw your signature" in client.post("/signature", data={"signature": ""}).data
    bad = "data:image/png;base64,aGVsbG8="     # "hello", not a PNG
    assert b"invalid" in client.post("/signature", data={"signature": bad}).data


@pytest.mark.parametrize("change, field_error", [
    ({"student_number": "2026-0001"}, b"already registered"),
    ({"student_number": "!!"}, b"letters, numbers or dashes"),
    ({"password": "short", "confirm_password": "short"}, b"at least 8 characters"),
    ({"confirm_password": "different1"}, b"do not match"),
    ({"first_name": ""}, b"First name is required"),
    ({"program": "", "year_level": "", "section_letter": ""}, b"Choose your course, year, and section"),
    ({"section_letter": ""}, b"Choose your course, year, and section together"),
    ({"program": "NOTACOURSE"}, b"Choose an available course"),
])
def test_student_registration_validation(client, change, field_error):
    r = client.post("/register/student", data={**NEW_STUDENT, **change})
    assert r.status_code == 200 and field_error in r.data


def test_registration_saves_course_year_section_locked(app, client):
    page = client.get("/register/student").data
    assert b'name="program"' in page and b'name="year_level"' in page and b'name="section_letter"' in page
    client.post("/register/student", data=NEW_STUDENT)
    user = db(app).execute("SELECT * FROM users WHERE student_number = '2026-0099'").fetchone()
    assert (user["program"], user["year_level"], user["section_letter"], user["academic_locked"]) == ("BSCPE", 4, "A", 1)


def test_registration_without_any_courses_set_up_still_works(app, client):
    c = db(app)
    c.execute("UPDATE courses SET is_active = 0")
    c.commit()
    bare = {k: v for k, v in NEW_STUDENT.items() if k not in ("program", "year_level", "section_letter")}
    assert client.post("/register/student", data=bare).location.endswith("/backup-codes")


def test_login_with_student_number(app, client):
    give_signature(app, 2)
    assert login(client, "2026-0001", "student123").location.endswith("/student/")


def test_wrong_password_wording_per_site(client):
    assert b"Wrong student number or password" in login(client, "2026-0001", "nope").data
    r = login(client, "prof", "nope", PROF_SITE)
    assert b"Wrong username or password" in r.data and b"student" not in r.data.split(b'class="auth-panel"')[1].lower()


def test_logout(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    client.post("/logout")
    assert client.get("/student/").location.startswith("/login")


# ---------------------------------------------------------------- instructor registration
def test_instructor_registration_needs_code(client):
    r = client.post("/register/instructor", data={**NEW_PROF, "registration_code": "guess"},
                    base_url=PROF_SITE)
    assert b"Wrong instructor registration code" in r.data


def test_instructor_registration_and_login(client):
    r = client.post("/register/instructor", data=NEW_PROF, base_url=PROF_SITE)
    assert r.location.endswith("/backup-codes")              # backup codes come first
    client.post("/logout", base_url=PROF_SITE)
    assert login(client, "mreyes", "password123", PROF_SITE).location.endswith("/instructor/")


# ---------------------------------------------------------------- roles + privacy
def test_student_blocked_from_instructor_pages(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    assert client.get("/instructor/").status_code == 403


def test_student_cannot_see_other_signatures(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    assert client.get("/signature/3.png").status_code == 403


def test_csrf_blocks_forms_without_token(app, client):
    app.config["CSRF_ENABLED"] = True
    r = client.post("/login", data={"login": "prof", "password": "prof123"})
    assert r.status_code == 400


# ---------------------------------------------------------------- student features
def test_student_web_checkin(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    assert client.get("/student/checkin/status").get_json()["variant"] == "present"
    r = client.post("/student/checkin", data={"signature": TINY_PNG}, follow_redirects=True)
    assert b"You&#39;re checked in" in r.data or b"You're checked in" in r.data    # the check-in card says it
    assert b"app-toast" not in r.data                                            # no success banner/toast
    assert client.get("/student/checkin/status").get_json()["variant"] == "done"


def test_student_card_registration_busy(app, client):
    give_signature(app, 5)
    give_signature(app, 3)
    c = db(app)
    c.execute("UPDATE users SET rfid_uid = NULL WHERE id = 3")
    c.commit()

    login(client, "2026-0004", "student123")          # Mark
    assert client.post("/student/card/start").get_json()["ok"]
    assert client.get("/student/card/status").get_json()["registration"]["status"] == "waiting"

    other = client.application.test_client()
    login(other, "2026-0002", "student123")           # Jose
    busy = other.post("/student/card/start").get_json()
    assert busy["ok"] is False and "wait until the current session is finished" in busy["message"]


# ---------------------------------------------------------------- dev page regression
@pytest.mark.parametrize("choice", ["auto", "present", "late", "disabled"])
def test_dev_session_mode_buttons(app, client, choice):
    r = client.post("/dev/session/1/mode", data={"choice": choice})
    assert r.status_code == 302
    row = db(app).execute("SELECT checkin_mode, manual_state FROM class_sessions WHERE id=1").fetchone()
    assert row["checkin_mode"] == ("auto" if choice == "auto" else "manual")
