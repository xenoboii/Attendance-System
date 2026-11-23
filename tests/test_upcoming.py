"""Upcoming classes (Attendance tab) computed from the weekly schedule."""
from datetime import datetime

from app.services import classes, report, sessions

PROF = 1
MON_0601 = datetime(2026, 11, 16, 6, 1)      # Monday morning


def add(conn, day, start, end, room=None):
    return classes.add_schedule(conn, PROF, 1, {"day_of_week": str(day), "start_time": start,
                                                "end_time": end, "room": room or ""})


def test_next_classes_in_date_order(conn):
    add(conn, 0, "07:00", "09:00", "Room 204")       # Monday
    add(conn, 2, "13:00", "15:00")                   # Wednesday
    up = sessions.upcoming_from_schedule(conn, 1, MON_0601, limit=4)
    assert [(u["date"], u["start_time"]) for u in up] == [
        ("2026-11-16", "07:00"), ("2026-11-18", "13:00"), ("2026-11-23", "07:00"), ("2026-11-25", "13:00")]
    assert up[0]["days_away"] == 0 and up[0]["room"] == "Room 204"


def test_today_skipped_once_started_or_created(conn):
    sc = add(conn, 0, "07:00", "09:00")
    after_start = datetime(2026, 11, 16, 7, 30)
    assert sessions.upcoming_from_schedule(conn, 1, after_start)[0]["date"] == "2026-11-23"
    sessions.ensure_sessions_for_date(conn, MON_0601.date())          # today's session now exists
    assert sessions.upcoming_from_schedule(conn, 1, MON_0601)[0]["date"] == "2026-11-23"
    assert sc


def test_no_schedule_no_upcoming(conn):
    assert sessions.upcoming_from_schedule(conn, 1, MON_0601) == []


def test_attendance_overview_limits_upcoming_to_four(conn):
    add(conn, 2, "13:00", "15:00")
    for day in (17, 19, 20, 21, 22):
        sessions.create_session(conn, PROF, 1, {"type": "makeup", "session_date": f"2026-11-{day}", "start_time": "10:00"}, MON_0601)
    upcoming = sessions.attendance_overview(conn, 1, MON_0601.replace(hour=8))["upcoming"]
    assert len(upcoming) == 4
    assert [entry["date"] for entry in upcoming] == ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]


def test_when_labels():
    assert [sessions.when_label(d) for d in (0, 1, 5)] == ["Today", "Tomorrow", "In 5 days"]


def test_attendance_tab_shows_upcoming(client):
    from .test_auth import PROF_SITE, login
    login(client, "prof", "prof123", PROF_SITE)
    client.post("/instructor/sections/1/schedules", base_url=PROF_SITE,
                data={"day_of_week": "0", "start_time": "07:00", "end_time": "09:00"})
    page = client.get("/instructor/sections/1", base_url=PROF_SITE).data
    assert b"Upcoming" in page and b"Ended" in page and b"Next \xc2\xb7" in page


def test_dashboard_today_grouped(app, client):
    """Today's classes on the instructor dashboard: Current / Upcoming / Ended groups."""
    from app.db import connect
    from .test_auth import PROF_SITE, login
    c = connect(app.config["DATABASE"])
    c.execute("""INSERT INTO class_sessions (section_id, session_date, start_time, end_time, type)
                 VALUES (1, date('now','localtime'), '23:58', '23:59', 'regular')""")        # later today
    c.commit()
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/instructor/", base_url=PROF_SITE).data.decode()
    # the class open right now is the banner at the top
    assert "Live now" in page and "Open session" in page
    # today's list (calendar day panel), by time: demo class started a minute ago -> open,
    # 23:58 -> Upcoming, the async activity (no time) -> Ended, each with its status chip
    today = page.split('aria-label="Classes on the selected day"')[1].split("</section>")[0]
    assert today.index("Open · Present") < today.index("Upcoming") < today.index("Ended")


def test_student_dashboard_schedule_is_a_list(app, client):
    from app.db import connect
    from .test_auth import give_signature, login
    c = connect(app.config["DATABASE"])
    for day, start, end in (("0", "07:00", "09:00"), ("2", "13:00", "15:00")):
        classes.add_schedule(c, PROF, 1, {"day_of_week": day, "start_time": start, "end_time": end})
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    page = client.get("/student/classes").data             # the class list lives on My classes
    assert page.count(b'class="sched-pill"') == 2          # one pill per class day


def test_overview_groups_do_not_mix(conn):
    """open now / upcoming / ended are separate lists."""
    now = datetime(2026, 11, 16, 7, 5)                       # demo class (07:00) is open as PRESENT
    future = sessions.create_session(conn, PROF, 1, {"type": "makeup", "session_date": "2026-11-20",
                                                     "start_time": "10:00"}, now)
    past = sessions.create_session(conn, PROF, 1, {"type": "makeup", "session_date": "2026-11-10",
                                                   "start_time": "10:00"}, now)
    add(conn, 2, "13:00", "15:00")                           # weekly Wednesday class
    o = sessions.attendance_overview(conn, 1, now)
    assert [s["id"] for s in o["open_now"]] == [1]
    assert past in [s["id"] for s in o["ended"]]
    up = [(u["date"], u["from_schedule"]) for u in o["upcoming"]]
    assert ("2026-11-18", True) in up and ("2026-11-20", False) in up
    assert up == sorted(up, key=lambda x: x[0])               # soonest first
    all_ids = [s["id"] for s in o["open_now"] + o["ended"]] + [u.get("id") for u in o["upcoming"] if not u["from_schedule"]]
    assert len(all_ids) == len(set(all_ids)) and future in all_ids    # each session in exactly one group


def test_async_activity_waits_for_its_completion_time_before_moving_to_ended(conn):
    date = "2026-11-21"
    async_id = sessions.create_session(conn, PROF, 1,
                                       {"type": "async", "session_date": date, "title": "Reading",
                                        "start_time": "14:30"}, MON_0601)
    for day in (17, 18, 19, 20):
        sessions.create_session(conn, PROF, 1,
                                {"session_date": f"2026-11-{day}", "start_time": "10:00"}, MON_0601)
    limited = sessions.attendance_overview(conn, 1, MON_0601.replace(hour=8))
    assert len(limited["upcoming"]) == 4
    overview = sessions.attendance_overview(conn, 1, MON_0601, show_all=True)
    assert async_id in [s["id"] for s in overview["upcoming"]]
    assert async_id not in [s["id"] for s in overview["ended"]]
    assert async_id not in [s["id"] for s in report.reportable_sessions(conn, 1, MON_0601)]
    just_before = datetime.fromisoformat(f"{date}T14:29:59")
    assert async_id in [s["id"] for s in sessions.attendance_overview(conn, 1, just_before)["upcoming"]]
    assert async_id not in [s["id"] for s in report.reportable_sessions(conn, 1, just_before)]
    at_completion = datetime.fromisoformat(f"{date}T14:30:00")
    overview = sessions.attendance_overview(conn, 1, at_completion)
    assert async_id in [s["id"] for s in overview["ended"]]
    assert async_id in [s["id"] for s in report.reportable_sessions(conn, 1, at_completion)]


def test_converted_async_keeps_its_original_start_time_cutoff(conn):
    sessions.set_async(conn, PROF, 1, "Morning reading", MON_0601)
    before = sessions.attendance_overview(conn, 1, MON_0601)
    assert 1 in [entry["id"] for entry in before["upcoming"]]
    after = sessions.attendance_overview(conn, 1, MON_0601.replace(hour=7))
    assert 1 in [entry["id"] for entry in after["ended"]]
