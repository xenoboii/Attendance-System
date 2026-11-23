"""Marking a date as "no class" (holiday, suspended classes ...) on a section's schedule."""
from datetime import timedelta

import pytest

from app.db import connect
from app.services import classes, sessions
from app.services.accounts import ValidationError

from .conftest import T0
from .test_auth import PROF_SITE, login

PROF = 1
MONDAY = T0.date()                    # 2026-11-16 is a Monday
NEXT_MONDAY = MONDAY + timedelta(days=7)


def _weekly(conn, day="0", start="13:00", end="15:00", section_id=1):
    classes.add_schedule(conn, PROF, section_id, {"day_of_week": day, "start_time": start, "end_time": end})


def _session_for(conn, section_id, day):
    return conn.execute(
        "SELECT * FROM class_sessions WHERE section_id = ? AND session_date = ? AND deleted_at IS NULL",
        (section_id, day.isoformat())).fetchall()


# ---------------------------------------------------------------- service: marking
def test_exception_skips_session_generation(conn):
    _weekly(conn)
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday"})
    assert sessions.ensure_sessions_for_date(conn, MONDAY) == 0
    exs = classes.list_schedule_exceptions(conn, 1)
    assert len(exs) == 1 and exs[0]["reason"] == "Holiday" and exs[0]["exception_date"] == MONDAY.isoformat()


def test_requires_date_and_reason(conn):
    with pytest.raises(ValidationError) as e:
        classes.add_schedule_exception(conn, PROF, 1, {"exception_date": "", "reason": ""})
    assert "exception_date" in e.value.errors and "reason" in e.value.errors
    with pytest.raises(ValidationError):
        classes.add_schedule_exception(conn, PROF, 1, {"exception_date": "not-a-date", "reason": "Holiday"})


def test_marking_cancels_an_already_created_unused_session(conn):
    _weekly(conn)
    sessions.ensure_sessions_for_date(conn, MONDAY)
    sid = conn.execute("SELECT id FROM class_sessions WHERE schedule_id IS NOT NULL").fetchone()[0]
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday"})
    row = conn.execute("SELECT deleted_at FROM class_sessions WHERE id = ?", (sid,)).fetchone()
    assert row["deleted_at"] is not None


def test_marking_leaves_a_used_session_alone(conn):
    _weekly(conn)
    sessions.ensure_sessions_for_date(conn, MONDAY)
    sid = conn.execute("SELECT id FROM class_sessions WHERE schedule_id IS NOT NULL").fetchone()[0]
    sessions.end_session(conn, PROF, 1, T0)                         # one class at a time: demo class done
    sessions.set_state(conn, PROF, sid, "present", T0)             # someone opened it
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday"})
    row = conn.execute("SELECT deleted_at FROM class_sessions WHERE id = ?", (sid,)).fetchone()
    assert row["deleted_at"] is None


def test_marking_the_same_reason_twice_is_a_no_op(conn):
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday"})
    applied = classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday"})
    assert applied == []
    assert classes.list_schedule_exceptions(conn, 1)[0]["reason"] == "Holiday"


def test_marking_again_with_a_different_reason_updates_the_type(conn):
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday"})
    ex_id = classes.list_schedule_exceptions(conn, 1)[0]["id"]
    applied = classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Suspended classes"})
    assert applied == [1]
    exs = classes.list_schedule_exceptions(conn, 1)
    assert exs[0]["reason"] == "Suspended classes" and exs[0]["id"] == ex_id      # same row, updated in place


def test_apply_to_all_sections(conn):
    sec2 = classes.create_section(conn, PROF, 1, {"course": "BSCPE", "year_level": "4", "section_letter": "B"})
    applied = classes.add_schedule_exception(
        conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday", "all_sections": "1"})
    assert sorted(applied) == sorted([1, sec2])
    assert classes.list_schedule_exceptions(conn, sec2)[0]["reason"] == "Holiday"


# ---------------------------------------------------------------- service: delete
def test_delete_exception(conn):
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday"})
    ex_id = classes.list_schedule_exceptions(conn, 1)[0]["id"]
    assert classes.delete_schedule_exception(conn, PROF, ex_id) == 1
    assert classes.list_schedule_exceptions(conn, 1) == []


def test_cannot_delete_someone_elses_exception(conn):
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday"})
    ex_id = classes.list_schedule_exceptions(conn, 1)[0]["id"]
    other = conn.execute(
        "INSERT INTO users (role, username, password_hash, first_name, last_name) "
        "VALUES ('instructor', 'other', 'x', 'O', 'T')").lastrowid
    assert classes.delete_schedule_exception(conn, other, ex_id) is None


# ---------------------------------------------------------------- upcoming list / dashboard
def test_upcoming_shows_no_class_entry(conn):
    _weekly(conn)
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday"})
    up = sessions.upcoming_from_schedule(conn, 1, T0, days=14)
    hit = next(u for u in up if u["date"] == NEXT_MONDAY.isoformat())
    assert hit["no_class"] is True and hit["reason"] == "Holiday" and hit["start_time"] is None
    assert hit["exception_id"] == classes.list_schedule_exceptions(conn, 1)[0]["id"]


def test_overview_upcoming_marks_no_class_without_a_type(conn):
    _weekly(conn)
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday"})
    overview = sessions.attendance_overview(conn, 1, T0)
    hit = next(u for u in overview["upcoming"] if u["date"] == NEXT_MONDAY.isoformat())
    assert hit["no_class"] is True and "type" not in hit


def test_todays_schedule_excludes_no_class_day(conn):
    _weekly(conn)
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": MONDAY.isoformat(), "reason": "Holiday"})
    assert classes.todays_schedule(conn, instructor_id=PROF, today=T0) == []
    assert classes.todays_schedule(conn, student_id=2, today=T0) == []


# ---------------------------------------------------------------- routes
def test_mark_no_class_route(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    r = client.post("/instructor/sections/1/no-class", data={
        "exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday"}, base_url=PROF_SITE)
    assert r.status_code == 302
    page = client.get("/instructor/sections/1?tab=schedule", base_url=PROF_SITE).data.decode()
    assert "No class days" in page and "Holiday" in page


def test_mark_no_class_needs_a_reason(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    r = client.post("/instructor/sections/1/no-class",
                    data={"exception_date": NEXT_MONDAY.isoformat(), "reason": ""}, base_url=PROF_SITE)
    assert r.status_code == 400 and b"Say why" in r.data


def test_delete_no_class_route(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    client.post("/instructor/sections/1/no-class",
                data={"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday"}, base_url=PROF_SITE)
    c = connect(app.config["DATABASE"])
    ex_id = classes.list_schedule_exceptions(c, 1)[0]["id"]
    c.close()
    r = client.post(f"/instructor/no-class/{ex_id}/delete", base_url=PROF_SITE)
    assert r.status_code == 302
    page = client.get("/instructor/sections/1?tab=schedule", base_url=PROF_SITE).data
    assert b"No class days" not in page


def test_no_class_route_only_own_section(app, client):
    c = connect(app.config["DATABASE"])
    from werkzeug.security import generate_password_hash
    other = c.execute("INSERT INTO users (role, username, password_hash, first_name, last_name) "
                      "VALUES ('instructor', 'other', ?, 'O', 'T')",
                      (generate_password_hash("other1234"),)).lastrowid
    sub = c.execute("INSERT INTO subjects (instructor_id, code, title, school_year, semester) "
                    "VALUES (?, 'X1', 'X', '2026-2027', '1st')", (other,)).lastrowid
    sec = c.execute("INSERT INTO sections (subject_id, name, join_code) VALUES (?, 'A', 'ZZZZZZ')", (sub,)).lastrowid
    c.commit(); c.close()
    login(client, "prof", "prof123", PROF_SITE)
    assert client.post(f"/instructor/sections/{sec}/no-class",
                       data={"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday"},
                       base_url=PROF_SITE).status_code == 404


# ---------------------------------------------------------------- "still mark everyone present"
def test_mark_present_credits_every_enrolled_student(conn):
    classes.add_schedule_exception(
        conn, PROF, 1, {"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday", "mark_present": "1"})
    rows = _session_for(conn, 1, NEXT_MONDAY)
    assert len(rows) == 1
    s = rows[0]
    assert s["type"] == "regular" and s["title"] == "Holiday" and s["ended_at"] is not None
    records = conn.execute(
        "SELECT status, note FROM attendance WHERE session_id = ? AND deleted_at IS NULL", (s["id"],)).fetchall()
    assert len(records) == 4 and all(r["status"] == "present" and r["note"] == "no_class" for r in records)
    assert classes.list_schedule_exceptions(conn, 1)[0]["mark_present"] == 1


def test_mark_present_is_not_duplicated(conn):
    classes.add_schedule_exception(
        conn, PROF, 1, {"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday", "mark_present": "1"})
    applied = classes.add_schedule_exception(
        conn, PROF, 1, {"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday", "mark_present": "1"})
    assert applied == []
    assert len(_session_for(conn, 1, NEXT_MONDAY)) == 1      # still just the one credited session


def test_plain_no_class_can_be_upgraded_to_credited_later(conn):
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday"})
    assert _session_for(conn, 1, NEXT_MONDAY) == []           # no credit yet
    applied = classes.add_schedule_exception(
        conn, PROF, 1, {"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday", "mark_present": "1"})
    assert applied == [1]
    rows = _session_for(conn, 1, NEXT_MONDAY)
    assert len(rows) == 1 and rows[0]["ended_at"] is not None


def test_upcoming_drops_the_no_class_row_once_credited(conn):
    _weekly(conn)
    classes.add_schedule_exception(
        conn, PROF, 1, {"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday", "mark_present": "1"})
    up = sessions.upcoming_from_schedule(conn, 1, T0, days=14)
    assert not any(u["date"] == NEXT_MONDAY.isoformat() for u in up)    # the real session speaks for itself now


def test_credited_day_shows_under_ended_with_full_present_count(conn):
    _weekly(conn)
    classes.add_schedule_exception(
        conn, PROF, 1, {"exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday", "mark_present": "1"})
    overview = sessions.attendance_overview(conn, 1, T0)
    hit = next(x for x in overview["ended"] if x["session_date"] == NEXT_MONDAY.isoformat())
    assert hit["counts"]["present"] == 4 and hit["counts"]["absent"] == 0


def test_mark_present_route(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    r = client.post("/instructor/sections/1/no-class", data={
        "exception_date": NEXT_MONDAY.isoformat(), "reason": "Holiday", "mark_present": "1"}, base_url=PROF_SITE)
    assert r.status_code == 302
    page = client.get("/instructor/sections/1?tab=schedule", base_url=PROF_SITE).data.decode()
    assert "Everyone present" in page                         # chip in the "No class days" list
    c = connect(app.config["DATABASE"])
    # only the "no class" credit records: the `app` fixture seeds demo sessions for the real
    # today, which collide with NEXT_MONDAY when the tests run on 2026-11-23
    rows = c.execute("SELECT status FROM attendance a JOIN class_sessions cs ON cs.id = a.session_id "
                     "WHERE cs.session_date = ? AND a.note = 'no_class' AND a.deleted_at IS NULL",
                     (NEXT_MONDAY.isoformat(),)).fetchall()
    c.close()
    assert len(rows) == 4 and all(r["status"] == "present" for r in rows)


def test_class_day_marked_as_async_activity(conn):
    """'Mark as > Async activity' on an upcoming class day: no regular session that day, an async
    session with everyone present instead, and the day leaves the Upcoming list."""
    from datetime import datetime, timedelta
    from app.services import sessions
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    now = datetime(2026, 11, 16, 7, 1)
    wednesday = "2026-11-18"
    assert wednesday in [u["date"] for u in sessions.upcoming_from_schedule(conn, 1, now)]
    with pytest.raises(ValidationError) as e:                                       # a title is required
        classes.add_schedule_exception(conn, PROF, 1, {"exception_date": wednesday, "as_async": "1", "title": "  "})
    assert "title" in e.value.errors
    applied = classes.add_schedule_exception(conn, PROF, 1, {"exception_date": wednesday, "as_async": "1",
                                                             "title": "Module 3 quiz"})
    assert applied == [1]
    rows = conn.execute("SELECT * FROM class_sessions WHERE section_id = 1 AND session_date = ? AND deleted_at IS NULL",
                        (wednesday,)).fetchall()
    assert len(rows) == 1 and rows[0]["type"] == "async" and rows[0]["title"] == "Module 3 quiz" and rows[0]["ended_at"]
    enrolled = len(classes.list_students(conn, 1))
    marks = conn.execute("SELECT status, note FROM attendance WHERE session_id = ?", (rows[0]["id"],)).fetchall()
    assert len(marks) == enrolled and {(m["status"], m["note"]) for m in marks} == {("present", "async")}
    listed = [u for u in sessions.upcoming_from_schedule(conn, 1, now) if u["date"] == wednesday]
    assert len(listed) == 1 and listed[0]["async_day"]                           # shown as async (so it can be undone)
    # on the day itself the weekly schedule adds nothing more, and marking it again changes nothing
    sessions.ensure_sessions_for_date(conn, datetime(2026, 11, 18).date())
    assert classes.add_schedule_exception(conn, PROF, 1, {"exception_date": wednesday, "as_async": "1",
                                                          "title": "Again"}) == []
    assert conn.execute("SELECT COUNT(*) FROM class_sessions WHERE section_id = 1 AND session_date = ? AND deleted_at IS NULL",
                        (wednesday,)).fetchone()[0] == 1


def test_async_day_can_be_undone_back_to_regular(conn):
    from datetime import datetime
    from app.services import sessions
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    now, wednesday = datetime(2026, 11, 16, 7, 1), "2026-11-18"
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": wednesday, "as_async": "1", "title": "Module 3 quiz"})
    row = next(u for u in sessions.upcoming_from_schedule(conn, 1, now) if u["date"] == wednesday)
    assert row["async_day"] and row["async_title"] == "Module 3 quiz" and row["async_session_id"]   # still listed, undoable
    overview = sessions.attendance_overview(conn, 1, now)
    assert any(u.get("async_day") for u in overview["upcoming"])
    # only before the class time: at 13:30 that Wednesday the 13:00 class has passed
    late = datetime(2026, 11, 18, 13, 30)
    assert not any(u.get("async_day") for u in sessions.upcoming_from_schedule(conn, 1, late))
    with pytest.raises(ValidationError) as e:
        classes.delete_schedule_exception(conn, PROF, row["exception_id"], late)
    assert "already passed" in e.value.errors["exception"]
    with pytest.raises(ValidationError):
        classes.delete_schedule_exception(conn, PROF, row["exception_id"], datetime(2026, 11, 19, 8, 0))
    early = datetime(2026, 11, 18, 12, 0)                                        # same day, before class: still allowed
    assert any(u.get("async_day") for u in sessions.upcoming_from_schedule(conn, 1, early))
    assert classes.delete_schedule_exception(conn, PROF, row["exception_id"], now) == 1
    assert conn.execute("SELECT COUNT(*) FROM class_sessions WHERE section_id = 1 AND session_date = ? AND deleted_at IS NULL",
                        (wednesday,)).fetchone()[0] == 0                                           # async session gone
    back = next(u for u in sessions.upcoming_from_schedule(conn, 1, now) if u["date"] == wednesday)
    assert not back["no_class"] and back["start_time"] == "13:00"                                  # a regular class again
    sessions.ensure_sessions_for_date(conn, datetime(2026, 11, 18).date())
    made = conn.execute("SELECT type FROM class_sessions WHERE section_id = 1 AND session_date = ? AND deleted_at IS NULL",
                        (wednesday,)).fetchall()
    assert [m["type"] for m in made] == ["regular"]
    assert conn.execute("""SELECT COUNT(*) FROM attendance a JOIN class_sessions cs ON cs.id = a.session_id
                           WHERE cs.session_date = ? AND cs.deleted_at IS NULL""", (wednesday,)).fetchone()[0] == 0


def test_async_day_delete_then_restore_keeps_it_undoable(conn):
    """Bug: mark a class day async, delete the async session, restore it from Recently deleted ->
    the day was stuck under Ended with no way back to a regular class."""
    from datetime import datetime, timedelta
    from app.services import sessions, trash
    today = datetime.now()
    day = (today + timedelta(days=2)).date()
    classes.add_schedule(conn, PROF, 1, {"day_of_week": str(day.weekday()), "start_time": "11:00", "end_time": "14:00"})
    iso = day.isoformat()
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": iso, "as_async": "1", "title": "Module 3 quiz"})
    sid = conn.execute("SELECT id FROM class_sessions WHERE section_id = 1 AND session_date = ? AND type = 'async'", (iso,)).fetchone()[0]

    def row():
        return next((u for u in sessions.upcoming_from_schedule(conn, 1, today) if u["date"] == iso), None)

    # deleting the async session is itself the undo: a regular class day again
    sessions.delete_session(conn, PROF, sid)
    assert not row()["no_class"] and row()["start_time"] == "11:00"
    assert conn.execute("SELECT COUNT(*) FROM schedule_exceptions WHERE section_id = 1 AND exception_date = ?", (iso,)).fetchone()[0] == 0
    # restoring it makes the day async again - listed in Upcoming, with the undo
    msg = trash.restore(conn, PROF, "session", sid)
    assert "async again" in msg
    r = row()
    assert r["async_day"] and r["async_session_id"] == sid and r["async_title"] == "Module 3 quiz"
    # ... and the undo works
    assert classes.delete_schedule_exception(conn, PROF, r["exception_id"], today) == 1
    assert not row()["no_class"]
    assert conn.execute("SELECT COUNT(*) FROM class_sessions WHERE section_id = 1 AND session_date = ? AND deleted_at IS NULL",
                        (iso,)).fetchone()[0] == 0
    # a plain async activity on a day with no class is untouched by all this
    free = (today + timedelta(days=3)).date().isoformat()
    other = sessions.create_session(conn, PROF, 1, {"type": "async", "session_date": free, "title": "Reading"})
    sessions.delete_session(conn, PROF, other)
    assert "back in the report" in trash.restore(conn, PROF, "session", other)
    assert conn.execute("SELECT COUNT(*) FROM schedule_exceptions WHERE exception_date = ?", (free,)).fetchone()[0] == 0


def test_changing_an_async_day_to_holiday_retires_only_its_activity(conn):
    from datetime import datetime
    from app.services import sessions

    now, day = datetime(2026, 11, 16, 8), "2026-11-18"
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    classes.add_schedule_exception(conn, PROF, 1,
                                   {"exception_date": day, "as_async": "1", "title": "Weekly activity"}, now=now)
    extra_id = sessions.create_session(conn, PROF, 1,
                                       {"session_date": day, "type": "async", "title": "Extra reading"}, now)
    classes.add_schedule_exception(conn, PROF, 1, {"exception_date": day, "reason": "Holiday"}, now=now)
    active = conn.execute("SELECT id FROM class_sessions WHERE session_date = ? AND deleted_at IS NULL", (day,)).fetchall()
    assert [row["id"] for row in active] == [extra_id]
    assert tuple(conn.execute("SELECT reason, mark_present, async_session_id FROM schedule_exceptions WHERE exception_date = ?",
                              (day,)).fetchone()) == ("Holiday", 0, None)


def test_async_day_uses_its_completion_time_instead_of_the_weekly_class_time(conn):
    from datetime import datetime
    from app.services import sessions

    day = "2026-11-18"
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    classes.add_schedule_exception(conn, PROF, 1,
                                   {"exception_date": day, "as_async": "1", "title": "Reading", "start_time": "16:00"},
                                   now=datetime(2026, 11, 16, 8))
    before = datetime(2026, 11, 18, 15, 59)
    overview = sessions.attendance_overview(conn, 1, before)
    entry = next(item for item in overview["upcoming"] if item.get("async_day"))
    assert entry["start_time"] == "16:00"
    assert entry["async_session_id"] not in [item["id"] for item in overview["ended"]]

    at_completion = datetime(2026, 11, 18, 16)
    overview = sessions.attendance_overview(conn, 1, at_completion)
    assert not any(item.get("async_day") for item in overview["upcoming"])
    assert entry["async_session_id"] in [item["id"] for item in overview["ended"]]
    with pytest.raises(ValidationError, match="already passed"):
        classes.delete_schedule_exception(conn, PROF, entry["exception_id"], at_completion)
    assert classes.delete_schedule_exception(conn, PROF, entry["exception_id"], before) == 1
