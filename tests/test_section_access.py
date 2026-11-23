"""Section matching, locked academic identity, sit-in approval, and Admin-only fixes."""
import re

import pytest

from app.db import SCHEMA, connect
from app.migrations import MIGRATIONS, migrate
from app.services import accounts, attendance_service, classes
from app.services.accounts import ValidationError

from .conftest import MARIA, at
from .test_admin import sign_in_as
from .test_auth import PROF_SITE, give_signature, login


def academic(conn, student_id=MARIA, course="BSIT", year=3, letter="A"):
    conn.execute("UPDATE users SET program=?, year_level=?, section_letter=?, academic_locked=1 WHERE id=?",
                 (course, year, letter, student_id))
    conn.commit()


@pytest.mark.parametrize("course,year,letter", [("BSCPE", 3, "B"), ("BSIT", 4, "B"), ("BSIT", 3, "A")])
def test_code_cannot_bypass_course_year_or_section(conn, course, year, letter):
    section_id = classes.create_section(conn, 1, 1, {"name": "BSIT 3B"})
    code = classes.get_section(conn, 1, section_id)["join_code"]
    academic(conn, course=course, year=year, letter=letter)
    response = classes.join_by_code(conn, MARIA, code)
    assert not response["ok"] and "Request approval" in response["message"]
    assert not conn.execute("SELECT 1 FROM enrollments WHERE section_id=? AND student_id=?", (section_id, MARIA)).fetchone()


def test_matching_student_joins_directly_and_missing_profile_is_blocked(conn):
    section_id = classes.create_section(conn, 1, 1, {"name": "BSIT 3B"})
    code = classes.get_section(conn, 1, section_id)["join_code"]
    assert not classes.join_by_code(conn, MARIA, code)["ok"]
    academic(conn, letter="B")
    assert classes.join_by_code(conn, MARIA, code.lower())["ok"]


@pytest.mark.parametrize("submit", [classes.join_by_code, classes.request_join])
@pytest.mark.parametrize("approve", [True, False])
def test_every_student_policy_requires_approval_even_for_matching_students(conn, submit, approve):
    academic(conn)
    classes.remove_student(conn, 1, 1, MARIA)
    form = {key: str(value) for key, value in dict(classes.get_section(conn, 1, 1)).items() if value is not None}
    form["join_approval"] = "everyone"
    classes.update_section(conn, 1, 1, form)
    response = submit(conn, MARIA, "JOIN3A")
    assert response["ok"] and "Request pending" in response["message"]
    assert not classes.student_classes(conn, MARIA)
    request = classes.section_join_requests(conn, 1)[0]
    classes.review_join_request(conn, 1, 1, request["id"], approve)
    assert bool(classes.student_classes(conn, MARIA)) == approve
    if not approve:
        assert attendance_service.rfid_tap(conn, "CARD0001", at("07:00"))["result"] == "no_open_session"
        # Retrying the code after rejection still cannot create an enrollment.
        classes.join_by_code(conn, MARIA, "JOIN3A")
        assert not classes.student_classes(conn, MARIA)


def test_approval_settings_persist_and_validate_and_are_owned_by_instructor(app, client):
    sign_in_as(client, 1)
    page = client.get("/instructor/sections/1?tab=settings")
    assert b'name="join_approval"' in page.data and b"Every student" in page.data
    with connect(app.config["DATABASE"]) as db:
        form = {key: str(value) for key, value in dict(classes.get_section(db, 1, 1)).items() if value is not None}
        form["join_approval"] = "everyone"
    assert client.post("/instructor/sections/1/edit", data=form).status_code == 302
    page = client.get("/instructor/sections/1?tab=settings")
    assert re.search(rb'<option value="everyone"[^>]*selected', page.data)
    assert client.post("/instructor/sections/1/edit", data={**form, "join_approval": "invalid"}).status_code == 400
    with connect(app.config["DATABASE"]) as db:
        assert classes.get_section(db, 1, 1)["join_approval"] == "everyone"
        assert db.execute("SELECT COUNT(*) FROM enrollments WHERE section_id=1 AND status='active' AND deleted_at IS NULL").fetchone()[0] == 4
        with pytest.raises(ValidationError):
            classes.update_section(db, 999, 1, form)
        # Omitting the new field from an older form must preserve the current policy.
        form.pop("join_approval")
        classes.update_section(db, 1, 1, form)
        assert classes.get_section(db, 1, 1)["join_approval"] == "everyone"


@pytest.mark.parametrize("submit", [classes.join_by_code, classes.request_join])
def test_anyone_policy_admits_students_from_other_courses_without_request(conn, submit):
    academic(conn, course="BSCPE", year=4, letter="G")
    section_id = classes.create_section(conn, 1, 1, {"name": "BSIT 2B"})
    form = {key: str(value) for key, value in dict(classes.get_section(conn, 1, section_id)).items() if value is not None}
    form["join_approval"] = "anyone"
    classes.update_section(conn, 1, section_id, form)
    code = classes.get_section(conn, 1, section_id)["join_code"]
    assert submit(conn, MARIA, code)["ok"]
    assert any(row["section_id"] == section_id for row in classes.student_classes(conn, MARIA))
    assert not classes.section_join_requests(conn, section_id)


def test_pending_sit_in_has_no_roster_or_attendance_access_until_approved(conn):
    academic(conn, course="BSCPE", year=4, letter="A")
    classes.remove_student(conn, 1, 1, MARIA)
    assert classes.request_join(conn, MARIA, "JOIN3A", "Taking this subject as a sit-in")["ok"]
    assert classes.request_join(conn, MARIA, "JOIN3A")["ok"]  # no duplicate pending request
    requests = classes.section_join_requests(conn, 1)
    assert len(requests) == 1 and requests[0]["reason"] == "Taking this subject as a sit-in"
    assert not classes.student_classes(conn, MARIA)
    assert attendance_service.rfid_tap(conn, "CARD0001", at("07:00"))["result"] == "no_open_session"
    classes.review_join_request(conn, 1, 1, requests[0]["id"], True)
    assert classes.student_classes(conn, MARIA)
    assert attendance_service.rfid_tap(conn, "CARD0001", at("07:00"))["result"] == "present"
    assert not classes.section_join_requests(conn, 1)
    with pytest.raises(ValidationError):
        classes.review_join_request(conn, 1, 1, requests[0]["id"], False)


def test_only_section_owner_can_review_and_rejection_never_enrolls(conn):
    academic(conn, letter="B")
    classes.remove_student(conn, 1, 1, MARIA)
    classes.request_join(conn, MARIA, "JOIN3A")
    request = classes.section_join_requests(conn, 1)[0]
    with pytest.raises(ValidationError):
        classes.review_join_request(conn, 999, 1, request["id"], True)
    classes.review_join_request(conn, 1, 1, request["id"], False)
    assert not classes.student_classes(conn, MARIA)
    assert classes.student_join_requests(conn, MARIA)[0]["status"] == "rejected"
    # A rejected student can ask again, but must still wait for approval.
    assert classes.request_join(conn, MARIA, "JOIN3A")["ok"]
    assert len(classes.section_join_requests(conn, 1)) == 1
    assert not classes.student_classes(conn, MARIA)


def test_profile_locks_and_cannot_be_switched_or_cleared(conn):
    user = accounts.get_user(conn, MARIA)
    accounts.update_profile(conn, user, {"program": "BSIT", "year_level": "3", "section_letter": "A"})
    assert accounts.get_user(conn, MARIA)["academic_locked"] == 1
    for changes in ({"program": "BSCPE"}, {"year_level": "4"}, {"section_letter": "B"},
                    {"program": "", "year_level": "", "section_letter": ""}):
        with pytest.raises(ValidationError):
            accounts.update_profile(conn, accounts.get_user(conn, MARIA), changes)
    # A stale row from before the first save cannot overwrite the locked values.
    with pytest.raises(ValidationError):
        accounts.update_profile(conn, user, {"program": "BSCPE", "year_level": "4", "section_letter": "B"})
    accounts.update_profile(conn, accounts.get_user(conn, MARIA), {"contact_number": "09171234567"})
    assert accounts.get_user(conn, MARIA)["program"] == "BSIT"


@pytest.mark.parametrize("letter", ["H", "Z", "AA", "1"])
def test_section_choices_are_validated_on_server(conn, letter):
    with pytest.raises(ValidationError):
        classes.create_section(conn, 1, 1, {"course": "BSCPE", "year_level": "4", "section_letter": letter})
    with pytest.raises(ValidationError):
        classes.create_section(conn, 1, 1, {"name": f"BSCPE 4{letter}"})
    with pytest.raises(ValidationError):
        accounts.update_profile(conn, accounts.get_user(conn, MARIA),
                                {"program": "BSCPE", "year_level": "4", "section_letter": letter})


def select_values(html, name):
    select = re.search(r'<select[^>]*name="' + name + r'"[^>]*>(.*?)</select>', html, re.S)
    return re.findall(r'<option value="([^"]*)"', select.group(1))


def test_course_and_section_dropdowns_in_all_three_portals(app, client, admin_account):
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/instructor/subjects/1", base_url=PROF_SITE).get_data(as_text=True)
    assert "BSCPE" in select_values(page, "course")
    assert select_values(page, "section_letter") == ["", *"ABCDEFG"]
    give_signature(app, MARIA)
    student = app.test_client()
    sign_in_as(student, MARIA)
    page = student.get("/account/").get_data(as_text=True)
    assert select_values(page, "section_letter") == ["", *"ABCDEFG"]
    admin = app.test_client()
    sign_in_as(admin, admin_account["id"])
    page = admin.get("/admin/students/2").get_data(as_text=True)
    assert select_values(page, "section_letter") == ["", *"ABCDEFG"]


def test_sit_in_request_and_review_ui(app, client):
    with connect(app.config["DATABASE"]) as db:
        academic(db, letter="B")
        classes.remove_student(db, 1, 1, MARIA)
    give_signature(app, MARIA)
    sign_in_as(client, MARIA)
    response = client.post("/student/join", data={"join_code": "JOIN3A", "action": "request"}, follow_redirects=True)
    assert b"Awaiting instructor approval" in response.data
    assert client.get("/student/classes/1").status_code == 404
    prof = app.test_client()
    sign_in_as(prof, 1)
    page = prof.get("/instructor/sections/1?tab=students")
    assert b'value="approve">Approve</button>' in page.data
    with connect(app.config["DATABASE"]) as db:
        request_id = classes.section_join_requests(db, 1)[0]["id"]
    assert client.post(f"/instructor/sections/1/requests/{request_id}", data={"decision": "approve"}).status_code == 403
    assert prof.post(f"/instructor/sections/1/requests/{request_id}", data={"decision": "approve"}).status_code == 302
    assert client.get("/student/classes/1").status_code == 200


def test_only_admin_can_correct_student_identity_rfid_or_signature(app, client, admin_account):
    give_signature(app, MARIA)
    for uid in (1, MARIA):
        sign_in_as(client, uid)
        for path in ("/admin/students/2", "/admin/students/2/remove-card", "/admin/students/2/signature-reset",
                     "/instructor/sections/1/students/2/identity", "/instructor/sections/1/students/2/remove-card",
                     "/instructor/sections/1/students/2/reset-signature"):
            assert client.post(path).status_code == 403
    sign_in_as(client, admin_account["id"])
    payload = {"student_number": "2026-0001", "first_name": "Maria", "last_name": "Santos",
               "program": "BSCPE", "year_level": "4", "section_letter": "G"}
    assert client.post("/admin/students/2", data=payload).status_code == 302
    with connect(app.config["DATABASE"]) as db:
        user = accounts.get_user(db, MARIA)
        assert (user["program"], user["year_level"], user["section_letter"], user["academic_locked"]) == ("BSCPE", 4, "G", 1)
    assert client.get("/admin/students?q=Maria").status_code == 200


def test_existing_developer_accounts_become_admin_and_complete_profiles_lock(tmp_path):
    from app.seed import seed_demo
    conn = connect(tmp_path / "before-admin.db")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    for sql in MIGRATIONS[:12]:
        conn.executescript(sql)
    conn.execute("PRAGMA user_version=12")
    seed_demo(conn)
    conn.execute("INSERT INTO users (role, username, password_hash, first_name, last_name) VALUES ('developer', 'olddev', 'hash', 'Old', 'Dev')")
    conn.execute("UPDATE users SET program='BSIT', year_level=3, section_letter='A' WHERE id=2")
    conn.commit()
    assert migrate(conn) == len(MIGRATIONS)
    assert conn.execute("SELECT role FROM users WHERE username='olddev'").fetchone()[0] == "admin"
    assert accounts.get_user(conn, MARIA)["academic_locked"] == 1
    conn.close()
