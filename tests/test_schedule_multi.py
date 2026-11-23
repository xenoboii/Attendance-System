"""Add schedule pop-up: several class days in one submit (all or nothing)."""
import pytest
from werkzeug.datastructures import MultiDict

from app.services import classes
from app.services.accounts import ValidationError

PROF = 1
PROF_SITE = "http://prof.localhost"


def form(*rows):
    md = MultiDict()
    for day, start, end, room in rows:
        md.add("day_of_week", day)
        md.add("start_time", start)
        md.add("end_time", end)
        md.add("room", room)
    return md


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM schedules WHERE deleted_at IS NULL").fetchone()[0]


def test_adds_several_rows_at_once(conn):
    before = count(conn)
    ids = classes.add_schedules(conn, PROF, 1, form(("2", "13:00", "14:00", "Room 204"),
                                                    ("4", "13:00", "14:00", "")))
    assert len(ids) == 2 and count(conn) == before + 2
    rows = [conn.execute("SELECT day_of_week, room FROM schedules WHERE id = ?", (i,)).fetchone() for i in ids]
    assert [tuple(r) for r in rows] == [(2, "Room 204"), (4, None)]


def test_plain_dict_still_adds_one(conn):
    before = count(conn)
    ids = classes.add_schedules(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    assert len(ids) == 1 and count(conn) == before + 1


def test_one_bad_row_saves_nothing(conn):
    before = count(conn)
    with pytest.raises(ValidationError) as e:
        classes.add_schedules(conn, PROF, 1, form(("2", "13:00", "14:00", ""),
                                                  ("3", "15:00", "14:00", "")))
    assert list(e.value.errors["schedule_rows"]) == [1]
    assert "end_time" in e.value.errors["schedule_rows"][1]
    assert count(conn) == before


def test_new_rows_cannot_overlap_each_other(conn):
    before = count(conn)
    with pytest.raises(ValidationError) as e:
        classes.add_schedules(conn, PROF, 1, form(("2", "13:00", "15:00", ""),
                                                  ("2", "14:00", "16:00", "")))
    assert "schedule 1" in e.value.errors["schedule_rows"][1]["start_time"]
    assert count(conn) == before


def test_too_many_rows(conn):
    rows = [(str(i % 7), "20:00", "21:00", "") for i in range(classes.MAX_SCHEDULE_ROWS + 1)]
    with pytest.raises(ValidationError):
        classes.add_schedules(conn, PROF, 1, form(*rows))


def test_route_adds_many_and_keeps_rows_on_error(client, app):
    from .test_instructor_pages import post, prof
    prof(client)
    r = post(client, "/instructor/sections/1/schedules", form(("2", "20:00", "21:00", ""), ("4", "20:00", "21:00", "")))
    assert r.status_code in (302, 303)
    r = post(client, "/instructor/sections/1/schedules", form(("5", "20:00", "21:00", "Lab 1"), ("6", "21:00", "20:00", "")))
    assert r.status_code == 400
    html = r.get_data(as_text=True)
    assert html.count("data-sched-row>") == 3          # two rows kept + the <template> copy
    assert "Lab 1" in html and "End time must be after the start time." in html
