"""Students leave only their own enrollment; history and join approval rules remain intact."""
import pytest
from datetime import timedelta

from app.db import connect
from app.services import accounts, attendance_service, classes, report, sessions, trash

from .conftest import MARIA, T0, at
from .test_admin import sign_in_as
from .test_auth import give_signature


def test_leaving_removes_access_but_preserves_attendance_after_cleanup(conn):
    attendance_service.rfid_tap(conn, "CARD0001", at("07:00"))
    before = [tuple(row) for row in conn.execute("SELECT * FROM attendance WHERE student_id=? ORDER BY id", (MARIA,))]
    assert classes.leave_class(conn, MARIA, 1)["section_name"] == "BSIT 3A"
    assert not classes.student_classes(conn, MARIA)
    assert not classes.todays_schedule(conn, student_id=MARIA, today=at("07:00"))
    assert attendance_service.rfid_tap(conn, "CARD0001", at("07:01"))["result"] == "no_open_session"
    trash.purge_expired(conn, 30, now=at("07:00").replace(year=2027))
    assert [tuple(row) for row in conn.execute("SELECT * FROM attendance WHERE student_id=? ORDER BY id", (MARIA,))] == before
    enrollment = conn.execute("SELECT * FROM enrollments WHERE student_id=? AND section_id=1", (MARIA,)).fetchone()
    assert enrollment["status"] == "dropped" and enrollment["deleted_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM enrollments WHERE section_id=1 AND status='active'").fetchone()[0] == 3
    assert conn.execute("SELECT user_id FROM audit_log WHERE action='leave_class'").fetchone()[0] == MARIA


def test_leave_ui_and_endpoint_only_change_current_students_membership(app, client):
    give_signature(app, MARIA)
    sign_in_as(client, MARIA)
    page = client.get("/student/")
    assert b"student-dashboard-panel" in page.data and page.data.count(b"student-dashboard-scroll") == 2
    assert b'data-confirm-title="Leave class?"' in client.get("/student/classes").data    # list moved to My classes
    assert b"Leave class" in client.get("/student/classes/1").data
    # Forging another student's ID in the submitted form cannot affect them.
    response = client.post("/student/classes/1/leave", data={"student_id": "3"})
    assert response.status_code == 302 and response.location.endswith("/student/classes")
    assert client.get("/student/classes/1").status_code == 404
    with connect(app.config["DATABASE"]) as db:
        assert not classes.student_classes(db, MARIA)
        assert classes.student_classes(db, 3)
    assert client.post("/student/classes/1/leave").status_code == 404


def test_leave_requires_role_membership_post_and_csrf(app, client, admin_account):
    assert client.post("/student/classes/1/leave").status_code == 302  # sign in first
    for uid in (1, admin_account["id"]):
        sign_in_as(client, uid)
        assert client.post("/student/classes/1/leave").status_code == 403
    give_signature(app, MARIA)
    sign_in_as(client, MARIA)
    assert client.get("/student/classes/1/leave").status_code == 405
    assert client.post("/student/classes/9999/leave").status_code == 404
    app.config["CSRF_ENABLED"] = True
    assert client.post("/student/classes/1/leave").status_code == 400
    client.get("/student/")
    with client.session_transaction() as session:
        token = session["_csrf"]
    assert client.post("/student/classes/1/leave", data={"csrf_token": token}).status_code == 302


@pytest.mark.parametrize("policy", ["outsiders", "everyone"])
def test_rejoining_after_leaving_follows_instructor_approval(conn, policy):
    accounts.update_profile(conn, accounts.get_user(conn, MARIA),
                            {"program": "BSIT", "year_level": "3", "section_letter": "A"})
    conn.execute("UPDATE sections SET join_approval=? WHERE id=1", (policy,))
    # An existing approved request must disappear when its student leaves.
    conn.execute("INSERT INTO join_requests (section_id, student_id, status) VALUES (1, ?, 'approved')", (MARIA,))
    conn.commit()
    classes.leave_class(conn, MARIA, 1)
    assert not classes.student_join_requests(conn, MARIA)
    response = classes.join_by_code(conn, MARIA, "JOIN3A")
    assert response["ok"]
    assert bool(classes.student_classes(conn, MARIA)) == (policy == "outsiders")
    if policy == "everyone":
        assert classes.section_join_requests(conn, 1)


@pytest.mark.parametrize("return_method", ["join_code", "request", "instructor"])
@pytest.mark.parametrize("explicit_absence", [False, True])
def test_rejoining_preserves_absences_and_history_across_all_return_paths(conn, monkeypatch, return_method, explicit_absence):
    accounts.update_profile(conn, accounts.get_user(conn, MARIA),
                            {"program": "BSIT", "year_level": "3", "section_letter": "A"})
    if explicit_absence:
        sessions.mark(conn, 1, 1, MARIA, "absent", at("08:00"))
    later = T0 + timedelta(days=20)
    monkeypatch.setattr(classes, "_now", lambda: later.strftime("%Y-%m-%d %H:%M:%S"))
    enrollment_date = conn.execute("SELECT enrolled_at FROM enrollments WHERE section_id=1 AND student_id=?", (MARIA,)).fetchone()[0]
    before = sessions.student_section_stats(conn, MARIA, 1, later)
    assert before["summary"]["absent"] == 1
    section = dict(classes.get_section(conn, 1, 1))
    chosen = [dict(row) for row in conn.execute("SELECT * FROM class_sessions WHERE section_id=1")]
    assert report.build_matrix(conn, section, chosen, later)[1][(MARIA, 1)] == "absent"
    if return_method == "instructor":
        classes.remove_student(conn, 1, 1, MARIA)
        classes.enroll_by_numbers(conn, 1, 1, "2026-0001")
    else:
        classes.leave_class(conn, MARIA, 1)
        if return_method == "request":
            conn.execute("UPDATE sections SET join_approval='everyone' WHERE id=1")
            conn.commit()
            classes.join_by_code(conn, MARIA, "JOIN3A")
            request = classes.section_join_requests(conn, 1)[0]
            classes.review_join_request(conn, 1, 1, request["id"], True)
        else:
            classes.join_by_code(conn, MARIA, "JOIN3A")
    assert conn.execute("SELECT enrolled_at FROM enrollments WHERE section_id=1 AND student_id=?", (MARIA,)).fetchone()[0] == enrollment_date
    after = sessions.student_section_stats(conn, MARIA, 1, later)
    assert after == before
    assert sessions.section_student_stats(conn, 1, later)[MARIA] == before["summary"]
    assert report.build_matrix(conn, section, chosen, later)[1][(MARIA, 1)] == "absent"
