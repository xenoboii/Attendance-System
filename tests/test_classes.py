"""Subjects, sections, schedules, enrollment (service level)."""
import pytest

from app.services import attendance_service as attendance
from app.services import classes
from app.services import sessions as sessions_svc
from app.services.accounts import ValidationError

from .conftest import MARIA, at

PROF = 1
SUBJECT = {"code": "it 401", "title": "Capstone", "school_year": "2026-2027", "semester": "1st", "units": "3"}


def other_instructor(conn):
    conn.execute("""INSERT INTO users (id, role, username, password_hash, first_name, last_name)
                    VALUES (99, 'instructor', 'other', 'x', 'O', 'Ther')""")
    conn.commit()
    return 99


# ---------------------------------------------------------------- subjects
def test_create_subject_uppercases_code(conn):
    sid = classes.create_subject(conn, PROF, SUBJECT)
    assert classes.get_subject(conn, PROF, sid)["code"] == "IT 401"


@pytest.mark.parametrize("change, field", [
    ({"title": ""}, "title"),
    ({"school_year": "2026"}, "school_year"),
    ({"school_year": "2026-2028"}, "school_year"),
    ({"semester": "3rd"}, "semester"),
    ({"units": "abc"}, "units"),
    ({"units": "3 units"}, "units"),
    ({"units": "1e1"}, "units"),
    ({"units": "nan"}, "units"),
    ({"units": "inf"}, "units"),
    ({"units": "0.25"}, "units"),
    ({"units": "12.5"}, "units"),
])
def test_subject_validation(conn, change, field):
    with pytest.raises(ValidationError) as e:
        classes.create_subject(conn, PROF, {**SUBJECT, **change})
    assert field in e.value.errors


@pytest.mark.parametrize("units,expected", [("", None), ("0.5", 0.5), ("3.5", 3.5), ("12", 12)])
def test_subject_numeric_units(conn, units, expected):
    sid = classes.create_subject(conn, PROF, {**SUBJECT, "units": units})
    assert classes.get_subject(conn, PROF, sid)["units"] == expected


def test_duplicate_subject_rejected(conn):
    classes.create_subject(conn, PROF, SUBJECT)
    with pytest.raises(ValidationError):
        classes.create_subject(conn, PROF, SUBJECT)


def test_deleted_subject_is_restored_when_recreated(conn):
    sid = classes.create_subject(conn, PROF, SUBJECT)
    classes.delete_subject(conn, PROF, sid)
    assert classes.get_subject(conn, PROF, sid) is None
    assert classes.create_subject(conn, PROF, SUBJECT) == sid
    actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log WHERE entity = 'subjects'")]
    assert actions == ["delete", "restore"]


def test_instructors_only_see_their_own(conn):
    other = other_instructor(conn)
    assert classes.get_subject(conn, other, 1) is None
    assert classes.get_section(conn, other, 1) is None
    assert classes.list_subjects(conn, other) == []


# ---------------------------------------------------------------- sections
def test_section_has_join_code_and_no_duplicates(conn):
    sec = classes.create_section(conn, PROF, 1, {"name": "bsit 3b"})
    row = classes.get_section(conn, PROF, sec)
    assert row["name"] == "BSIT 3B" and len(row["join_code"]) == 6
    with pytest.raises(ValidationError):
        classes.create_section(conn, PROF, 1, {"name": "BSIT 3B"})


def test_section_name_from_course_year_section(conn):
    sec = classes.create_section(conn, PROF, 1, {"course": " BSCpE ", "year_level": "4", "section_letter": "a"})
    assert classes.get_section(conn, PROF, sec)["name"] == "BSCpE 4A"           # prof's capitals kept
    with pytest.raises(ValidationError) as e:                                   # same name, any case
        classes.create_section(conn, PROF, 1, {"course": "bscpe", "year_level": "4", "section_letter": "A"})
    assert "section_letter" in e.value.errors
    with pytest.raises(ValidationError) as e:
        classes.create_section(conn, PROF, 1, {"course": "Bachelor of Science in Computer Engineering",
                                               "year_level": "6", "section_letter": ""})
    assert set(e.value.errors) == {"course", "year_level", "section_letter"}
    assert classes.split_section_name("BSCpE 4A") == {"course": "BSCpE", "year_level": "4", "section_letter": "A"}
    assert classes.split_section_name("Special")["course"] == "Special"           # old free-form names


def test_section_settings_validation(conn):
    good = {"name": "BSIT 3A", "default_checkin_mode": "auto", "default_open_before_minutes": "10",
            "default_grace_minutes": "10", "default_close_after_minutes": "45"}
    classes.update_section(conn, PROF, 1, good)
    assert classes.get_section(conn, PROF, 1)["default_grace_minutes"] == 10
    with pytest.raises(ValidationError) as e:
        classes.update_section(conn, PROF, 1, {**good, "default_close_after_minutes": "5"})
    assert "default_close_after_minutes" in e.value.errors


def test_regenerate_join_code(conn):
    old = classes.get_section(conn, PROF, 1)["join_code"]
    assert classes.regenerate_join_code(conn, PROF, 1) != old


# ---------------------------------------------------------------- schedules
def test_schedule_add_and_validation(conn):
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "0", "start_time": "07:00", "end_time": "09:00"})
    assert classes.list_schedules(conn, 1)[0]["day_name"] == "Monday"
    with pytest.raises(ValidationError) as e:
        classes.add_schedule(conn, PROF, 1, {"day_of_week": "1", "start_time": "09:00", "end_time": "08:00"})
    assert "end_time" in e.value.errors


def test_schedule_overlap_across_sections(conn):
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "0", "start_time": "07:00", "end_time": "09:00"})
    sec2 = classes.create_section(conn, PROF, 1, {"name": "BSIT 3B"})
    with pytest.raises(ValidationError) as e:
        classes.add_schedule(conn, PROF, sec2, {"day_of_week": "0", "start_time": "08:30", "end_time": "10:00"})
    assert "Overlaps with IT312 BSIT 3A" in e.value.errors["start_time"]
    # back-to-back is fine
    classes.add_schedule(conn, PROF, sec2, {"day_of_week": "0", "start_time": "09:00", "end_time": "10:00"})


def test_edit_schedule(conn):
    sc = classes.add_schedule(conn, PROF, 1, {"day_of_week": "0", "start_time": "07:00", "end_time": "09:00"})
    # moving it a bit later overlaps only with itself -> allowed
    assert classes.update_schedule(conn, PROF, sc, {"day_of_week": "0", "start_time": "08:00",
                                                    "end_time": "10:00", "room": "Lab 2"}) == 1
    row = classes.list_schedules(conn, 1)[0]
    assert (row["start_time"], row["end_time"], row["room"]) == ("08:00", "10:00", "Lab 2")


def test_edit_schedule_still_checks_other_classes(conn):
    sec2 = classes.create_section(conn, PROF, 1, {"name": "BSIT 3B"})
    classes.add_schedule(conn, PROF, sec2, {"day_of_week": "1", "start_time": "13:00", "end_time": "15:00"})
    sc = classes.add_schedule(conn, PROF, 1, {"day_of_week": "0", "start_time": "07:00", "end_time": "09:00"})
    with pytest.raises(ValidationError) as e:
        classes.update_schedule(conn, PROF, sc, {"day_of_week": "1", "start_time": "14:00", "end_time": "16:00"})
    assert "Overlaps with IT312 BSIT 3B" in e.value.errors["start_time"]


def test_edit_schedule_not_own(conn):
    sc = classes.add_schedule(conn, PROF, 1, {"day_of_week": "0", "start_time": "07:00", "end_time": "09:00"})
    assert classes.update_schedule(conn, other_instructor(conn), sc,
                                   {"day_of_week": "0", "start_time": "08:00", "end_time": "09:00"}) is None


def test_edit_schedule_updates_unused_upcoming_sessions(conn):
    from datetime import date
    sc = classes.add_schedule(conn, PROF, 1, {"day_of_week": str(date.today().weekday()),
                                              "start_time": "07:00", "end_time": "09:00"})
    sessions_svc.ensure_sessions_for_date(conn, date.today())
    classes.update_schedule(conn, PROF, sc, {"day_of_week": str(date.today().weekday()),
                                             "start_time": "10:00", "end_time": "12:00"})
    s = conn.execute("SELECT start_time, end_time FROM class_sessions WHERE schedule_id = ? AND deleted_at IS NULL",
                     (sc,)).fetchone()
    assert tuple(s) == ("10:00", "12:00")
    # moving to another day removes today's unused session
    classes.update_schedule(conn, PROF, sc, {"day_of_week": str((date.today().weekday() + 1) % 7),
                                             "start_time": "10:00", "end_time": "12:00"})
    assert conn.execute("SELECT COUNT(*) FROM class_sessions WHERE schedule_id = ? AND deleted_at IS NULL",
                        (sc,)).fetchone()[0] == 0


def test_delete_schedule_only_own(conn):
    sc = classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    assert classes.delete_schedule(conn, other_instructor(conn), sc) is None
    assert classes.delete_schedule(conn, PROF, sc) == 1
    assert classes.list_schedules(conn, 1) == []


# ---------------------------------------------------------------- enrollment
def test_enroll_by_numbers(conn):
    sec = classes.create_section(conn, PROF, 1, {"name": "BSIT 3B"})
    r = classes.enroll_by_numbers(conn, PROF, sec, "2026-0001\n2026-0002, 2026-0001  9999-0000")
    assert r == {"added": ["2026-0001", "2026-0002"], "already": [], "not_found": ["9999-0000"]}
    r = classes.enroll_by_numbers(conn, PROF, sec, "2026-0001")
    assert r["already"] == ["2026-0001"]


def test_remove_then_readd_student(conn):
    classes.remove_student(conn, PROF, 1, MARIA)
    assert MARIA not in [s["id"] for s in classes.list_students(conn, 1)]
    assert classes.enroll_by_numbers(conn, PROF, 1, "2026-0001")["added"] == ["2026-0001"]
    assert MARIA in [s["id"] for s in classes.list_students(conn, 1)]


def test_removed_student_cannot_check_in(conn):
    classes.remove_student(conn, PROF, 1, MARIA)
    assert attendance.rfid_tap(conn, "CARD0001", at("07:00"))["result"] == "no_open_session"


def test_join_by_code(conn):
    conn.execute("UPDATE users SET program='BSIT', year_level=3, section_letter='B', academic_locked=1 WHERE id = ?", (MARIA,))
    conn.commit()
    sec = classes.create_section(conn, PROF, 1, {"name": "BSIT 3B"})
    code = classes.get_section(conn, PROF, sec)["join_code"]
    assert classes.join_by_code(conn, MARIA, code.lower())["ok"]
    again = classes.join_by_code(conn, MARIA, code)
    assert not again["ok"] and "already enrolled" in again["message"]
    assert not classes.join_by_code(conn, MARIA, "NOPE00")["ok"]


def test_deleted_section_code_stops_working(conn):
    classes.delete_section(conn, PROF, 1)
    assert not classes.join_by_code(conn, MARIA, "JOIN3A")["ok"]
