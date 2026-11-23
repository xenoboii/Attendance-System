"""Inclusive vacation dates skip sessions and absences without changing recorded activity."""
from datetime import date, timedelta

import pytest

from app.db import connect
from app.services import classes, sessions, trash
from app.services.accounts import ValidationError

from .conftest import MARIA, T0
from .test_admin import sign_in_as
from .test_auth import give_signature

START = T0.date() + timedelta(days=7)
END = START + timedelta(days=2)
FORM = {"vacation_start": START.isoformat(), "vacation_end": END.isoformat()}


def test_vacation_is_inclusive_and_no_sessions_or_absences_are_created(conn):
    for weekday in (0, 1, 2, 3):
        classes.add_schedule(conn, 1, 1, {"day_of_week": str(weekday), "start_time": "07:00", "end_time": "09:00"})
    sessions.ensure_sessions_for_date(conn, START)
    pending = conn.execute("SELECT id FROM class_sessions WHERE section_id=1 AND session_date=?", (START.isoformat(),)).fetchone()[0]
    assert classes.add_vacation(conn, 1, 1, FORM)["days"] == 3
    for offset in range(3):
        day = START + timedelta(days=offset)
        sessions.ensure_sessions_for_date(conn, day)
        assert not conn.execute("SELECT 1 FROM class_sessions WHERE section_id=1 AND session_date=? AND deleted_at IS NULL", (day.isoformat(),)).fetchone()
        assert classes.todays_schedule(conn, student_id=MARIA, today=T0.replace(year=day.year, month=day.month, day=day.day)) == []
    history = sessions.student_section_stats(conn, MARIA, 1, T0 + timedelta(days=20))["history"]
    assert not any(FORM["vacation_start"] <= row["session_date"] <= FORM["vacation_end"] for row in history)
    sessions.ensure_sessions_for_date(conn, END + timedelta(days=1))
    assert conn.execute("SELECT 1 FROM class_sessions WHERE session_date=? AND deleted_at IS NULL", ((END+timedelta(days=1)).isoformat(),)).fetchone()
    with pytest.raises(ValidationError):
        trash.restore(conn, 1, "session", pending)
    with pytest.raises(ValidationError):
        sessions.create_session(conn, 1, 1, {"type":"regular", "session_date":START.isoformat(), "start_time":"10:00"})
    with pytest.raises(ValidationError):
        sessions.add_column_now(conn, 1, 1, T0 + timedelta(days=7))


def test_vacation_only_applies_to_owned_sections_and_can_apply_to_all(conn):
    extra = classes.create_section(conn, 1, 1, {"name":"BSIT 3B"})
    classes.add_vacation(conn, 1, 1, {**FORM,"all_sections":"1"})
    assert len(classes.list_schedule_exceptions(conn, 1)) == 3
    assert len(classes.list_schedule_exceptions(conn, extra)) == 3
    with pytest.raises(ValidationError):
        classes.add_vacation(conn, 999, 1, {**FORM,"all_sections":"1"})


@pytest.mark.parametrize("changes", [{"vacation_start":"bad"}, {"vacation_end":"2026-01-01"},
                                     {"vacation_end":"2029-12-31"}, {"vacation_reason":"x"*61}])
def test_invalid_vacation_never_writes_partial_dates(conn, changes):
    with pytest.raises(ValidationError):
        classes.add_vacation(conn, 1, 1, {**FORM,**changes})
    assert classes.list_schedule_exceptions(conn, 1) == []


def test_recorded_class_prevents_entire_vacation_range_from_being_changed(conn):
    sid = sessions.create_session(conn, 1, 1, {"type":"regular", "session_date":END.isoformat(),"start_time":"10:00"})
    sessions.mark(conn, 1, sid, MARIA, "present")
    with pytest.raises(ValidationError):
        classes.add_vacation(conn, 1, 1, FORM)
    assert classes.list_schedule_exceptions(conn, 1) == []
    assert conn.execute("SELECT deleted_at FROM class_sessions WHERE id=?", (sid,)).fetchone()[0] is None
    assert conn.execute("SELECT status FROM attendance WHERE session_id=?", (sid,)).fetchone()[0] == "present"


def test_vacation_can_be_removed_per_date_and_cannot_be_credited_present(conn):
    classes.add_vacation(conn, 1, 1, FORM)
    classes.add_vacation(conn, 1, 1, FORM)
    assert len(classes.list_schedule_exceptions(conn, 1)) == 3
    with pytest.raises(ValidationError):
        classes.add_schedule_exception(conn, 1, 1, {"exception_date":START.isoformat(),"reason":"Vacation","mark_present":"1"})
    exception = classes.list_schedule_exceptions(conn, 1)[0]
    classes.delete_schedule_exception(conn, 1, exception["id"])
    assert len(classes.list_schedule_exceptions(conn, 1)) == 2
    assert sessions.create_session(conn, 1, 1, {"type":"regular","session_date":START.isoformat(),"start_time":"10:00"})


def test_vacation_ui_and_permissions(app, client, admin_account):
    # The `app` fixture seeds demo classes (with attendance) for the REAL today, so use dates
    # well after it: the fixed FORM dates hit those classes when the tests run on 2026-11-23.
    start = date.today() + timedelta(days=30)
    form = {"vacation_start": start.isoformat(), "vacation_end": (start + timedelta(days=2)).isoformat()}
    sign_in_as(client, 1)
    page = client.get("/instructor/sections/1?tab=schedule")
    assert b'id="vacation-modal"' in page.data and b'name="vacation_start"' in page.data
    assert client.post("/instructor/sections/1/vacation", data=form).status_code == 302
    with connect(app.config["DATABASE"]) as db:
        assert len(classes.list_schedule_exceptions(db, 1)) == 3
    bad = client.post("/instructor/sections/1/vacation", data={**form,"vacation_end":"bad"})
    assert bad.status_code == 400 and b'data-open-on-load' in bad.data
    give_signature(app, MARIA)
    for uid in (MARIA, admin_account["id"]):
        sign_in_as(client, uid)
        assert client.post("/instructor/sections/1/vacation", data=form).status_code == 403
