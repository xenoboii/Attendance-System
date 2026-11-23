"""Recently deleted (backup): restore within 30 days, then deleted for good."""
from datetime import datetime, timedelta

import pytest

from app.db import connect
from app.services import attendance_service as attendance
from app.services import classes, sessions, trash
from app.services.accounts import ValidationError

from .conftest import MARIA, at
from .test_auth import PROF_SITE, login

PROF = 1


def kinds(conn):
    return sorted((i["kind"], i["id"]) for i in trash.list_trash(conn, PROF))


def test_deleted_items_show_up_and_restore(conn):
    sc = classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    classes.delete_schedule(conn, PROF, sc)
    sessions.delete_session(conn, PROF, 2)
    classes.remove_student(conn, PROF, 1, MARIA)
    enrol_id = conn.execute("SELECT id FROM enrollments WHERE student_id = ?", (MARIA,)).fetchone()[0]
    assert kinds(conn) == sorted([("schedule", sc), ("session", 2), ("student", enrol_id)])

    for kind, item in kinds(conn):
        trash.restore(conn, PROF, kind, item)
    assert kinds(conn) == []
    assert MARIA in [s["id"] for s in classes.list_students(conn, 1)]
    assert len(classes.list_schedules(conn, 1)) == 1


def test_children_hidden_under_deleted_subject(conn):
    classes.delete_section(conn, PROF, 1)
    classes.delete_subject(conn, PROF, 1)
    # the section belongs to a deleted subject -> only the subject is listed
    assert [k for k, _ in kinds(conn)] == ["subject"]
    trash.restore(conn, PROF, "subject", 1)
    assert classes.get_subject(conn, PROF, 1)


def test_restore_schedule_checks_overlap(conn):
    sc = classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    classes.delete_schedule(conn, PROF, sc)
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:30", "end_time": "15:00"})
    with pytest.raises(ValidationError) as e:
        trash.restore(conn, PROF, "schedule", sc)
    assert "Overlaps" in e.value.errors["trash"]


def test_delete_forever_removes_rows(conn):
    attendance.rfid_tap(conn, "CARD0001", at("07:00"))         # a check-in in session 1
    sessions.delete_session(conn, PROF, 1)
    trash.delete_forever(conn, PROF, "session", 1)
    assert conn.execute("SELECT COUNT(*) FROM class_sessions WHERE id = 1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM attendance WHERE session_id = 1").fetchone()[0] == 0
    assert conn.execute("SELECT session_id FROM rfid_scans").fetchone()[0] is None   # tap log kept


def test_purge_after_30_days_whole_subject(conn):
    now = datetime(2026, 12, 20)
    classes.delete_subject(conn, PROF, 1)
    conn.execute("UPDATE subjects SET deleted_at = ? WHERE id = 1",
                 ((now - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()
    assert trash.purge_expired(conn, 30, now) == 1
    for table in ("subjects", "sections", "enrollments", "class_sessions"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 5      # accounts are untouched


def test_not_purged_before_30_days(conn):
    sessions.delete_session(conn, PROF, 2)
    later = datetime.now() + timedelta(days=29)
    assert trash.purge_expired(conn, 30, later) == 0
    item = trash.list_trash(conn, PROF, 30, later)[0]
    assert item["days_left"] in (1, 2)


def test_other_instructor_cannot_restore(conn):
    conn.execute("INSERT INTO users (id, role, username, password_hash, first_name, last_name) "
                 "VALUES (99, 'instructor', 'o', 'x', 'O', 'T')")
    conn.commit()
    sessions.delete_session(conn, PROF, 2)
    assert trash.list_trash(conn, 99) == []
    with pytest.raises(ValidationError):
        trash.restore(conn, 99, "session", 2)


def test_trash_page_and_buttons(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    client.post("/instructor/sessions/2/delete", base_url=PROF_SITE)
    page = client.get("/instructor/trash", base_url=PROF_SITE).data
    assert b"Recently deleted" in page and b"Restore" in page and b"Delete forever" in page and b"In 30 days" in page
    client.post("/instructor/trash/session/2/restore", base_url=PROF_SITE)
    c = connect(app.config["DATABASE"])
    assert c.execute("SELECT deleted_at FROM class_sessions WHERE id = 2").fetchone()[0] is None
    assert b'href="/instructor/trash"' in client.get("/instructor/", base_url=PROF_SITE).data   # sidebar link
