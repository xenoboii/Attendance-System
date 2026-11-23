"""Class sessions: generation from schedule, start/add-column, controls, marking, absences."""
from datetime import date, datetime, timedelta

import pytest

from app.db import connect
from app.services import attendance_service as attendance
from app.services import calendar_view, classes, sessions, trash
from app.services.accounts import ValidationError

from .conftest import ANA, JOSE, MARIA, MARK, T0, at
from .test_auth import PROF_SITE, STUDENT_SITE, give_signature, login

PROF = 1
MONDAY = T0.date()            # 2026-11-16 is a Monday


def session_count(conn, section_id=1):
    return conn.execute("SELECT COUNT(*) FROM class_sessions WHERE section_id = ? AND deleted_at IS NULL",
                        (section_id,)).fetchone()[0]


# ---------------------------------------------------------------- generation
def test_sessions_generated_from_schedule_once(conn):
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "0", "start_time": "13:00", "end_time": "15:00"})
    before = session_count(conn)
    assert sessions.ensure_sessions_for_date(conn, MONDAY) == 1
    assert sessions.ensure_sessions_for_date(conn, MONDAY) == 0          # safe to repeat
    assert session_count(conn) == before + 1
    # Tuesday has no schedule
    assert sessions.ensure_sessions_for_date(conn, MONDAY + timedelta(days=1)) == 0


def test_deleted_session_is_not_recreated(conn):
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "0", "start_time": "13:00", "end_time": "15:00"})
    sessions.ensure_sessions_for_date(conn, MONDAY)
    sid = conn.execute("SELECT id FROM class_sessions WHERE schedule_id IS NOT NULL").fetchone()[0]
    sessions.delete_session(conn, PROF, sid)                             # class cancelled
    assert sessions.ensure_sessions_for_date(conn, MONDAY) == 0


def test_generated_session_opens_for_rfid(conn):
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "0", "start_time": "13:00", "end_time": "15:00"})
    r = attendance.rfid_tap(conn, "CARD0001", at("12:57"))               # nobody created it by hand
    assert r["result"] == "present" and r["session"]["start_time"] == "13:00"


# ---------------------------------------------------------------- start / add column
def test_start_now_opens_existing_session_early(conn):
    # class is at 07:00 but the instructor starts at 06:30
    sid = sessions.start_now(conn, PROF, 1, at("06:30"))
    assert sid == 1
    assert attendance.rfid_tap(conn, "CARD0001", at("06:31"))["result"] == "present"


def test_start_now_never_adds_a_column(conn):
    before = session_count(conn)
    assert sessions.start_now(conn, PROF, 1, at("09:00")) is None        # today's class already ended
    assert session_count(conn) == before


def test_add_column_now_is_explicit(conn):
    before = session_count(conn)
    sid = sessions.add_column_now(conn, PROF, 1, at("09:00"))
    assert session_count(conn) == before + 1
    assert attendance.rfid_tap(conn, "CARD0002", at("09:01"))["result"] == "present"
    sessions.delete_session(conn, PROF, sid)                             # added by mistake -> undo
    assert session_count(conn) == before


@pytest.mark.parametrize("form, field", [
    ({"type": "async", "session_date": "2026-11-16"}, "title"),
    ({"type": "async", "session_date": "2026-11-16", "title": "Reading", "start_time": "24:00"}, "start_time"),
    ({"type": "makeup", "session_date": "2026-11-16"}, "start_time"),
    ({"type": "makeup", "session_date": "", "start_time": "10:00"}, "session_date"),
    ({"type": "makeup", "session_date": "2026-11-16", "start_time": "10:00", "end_time": "09:00"}, "end_time"),
    ({"type": "regular", "session_date": "2026-11-16", "start_time": "10:00", "checkin_mode": "invalid"}, "checkin_mode"),
])
def test_create_session_validation(conn, form, field):
    with pytest.raises(ValidationError) as e:
        sessions.create_session(conn, PROF, 1, form)
    assert field in e.value.errors


ASYNC = {"type": "async", "session_date": "2026-11-16", "title": "Module 3"}


def test_async_marks_everyone_present_automatically(conn):
    sid = sessions.create_session(conn, PROF, 1, ASYNC, at("08:00"))
    recs = conn.execute("SELECT student_id, status, method, note FROM attendance WHERE session_id = ?",
                        (sid,)).fetchall()
    assert sorted(r["student_id"] for r in recs) == [MARIA, JOSE, ANA, MARK]
    assert {(r["status"], r["method"], r["note"]) for r in recs} == {("present", "manual", "async")}
    s = _s(conn, sid)
    assert s["start_time"] is None and s["ended_at"]                     # closed: nobody needs to check in
    assert sessions.is_finished(s, at("08:01"))


def test_async_does_not_open_check_in(conn):
    conn.execute("UPDATE class_sessions SET deleted_at = '2026-01-01' WHERE id = 1")   # hide the 07:00 class
    conn.commit()
    sessions.create_session(conn, PROF, 1, ASYNC, at("08:00"))
    assert attendance.rfid_tap(conn, "CARD0001", at("08:05"))["result"] == "no_open_session"


def test_async_instructor_can_change_anyone(conn):
    sid = sessions.create_session(conn, PROF, 1, ASYNC, at("08:00"))
    sessions.mark(conn, PROF, sid, JOSE, "absent", at("09:00"))          # didn't submit
    rec = conn.execute("SELECT status, note FROM attendance WHERE session_id = ? AND student_id = ?",
                       (sid, JOSE)).fetchone()
    assert tuple(rec) == ("absent", None)                                # no longer "auto"
    summary = sessions.student_section_stats(conn, JOSE, 1, at("09:00"))["summary"]
    assert summary["absent"] >= 1


def test_async_skips_students_who_join_later(conn):
    sid = sessions.create_session(conn, PROF, 1, ASYNC, at("08:00"))
    conn.execute("""INSERT INTO users (id, role, username, password_hash, first_name, last_name, student_number)
                    VALUES (50, 'student', 'late1', 'x', 'Late', 'Joiner', '2026-0050')""")
    conn.execute("INSERT INTO enrollments (section_id, student_id, enrolled_at) VALUES (1, 50, '2099-01-01 00:00:00')")
    conn.commit()
    # listed (every enrolled student is), but not given Present for an activity before they joined
    row = next(r for r in sessions.roster(conn, _s(conn, sid), at("10:00")) if r["id"] == 50)
    assert row["joined_later"] and row["status"] == "pending" and row["record_status"] is None


def test_forgotten_manual_session_closes_next_day(conn):
    sessions.set_state(conn, PROF, 1, "present", at("07:00"))            # never closed
    tomorrow = at("07:00") + timedelta(days=1)
    assert attendance.rfid_tap(conn, "CARD0001", tomorrow)["result"] == "no_open_session"


# ---------------------------------------------------------------- controls
def test_auto_needs_a_start_time(conn):
    sid = sessions.create_session(conn, PROF, 1, {"type": "async", "session_date": "2026-11-16", "title": "X"})
    with pytest.raises(ValidationError):
        sessions.set_state(conn, PROF, sid, "auto")


def test_late_switch_any_time(conn):
    sessions.set_state(conn, PROF, 1, "late", at("06:40"))               # before the scheduled time
    assert attendance.rfid_tap(conn, "CARD0003", at("06:41"))["result"] == "late"


# ---------------------------------------------------------------- roster / absences
def test_absent_only_after_window_closes(conn):
    s = dict(conn.execute("SELECT cs.*, cs.section_id FROM class_sessions cs WHERE id = 1").fetchone())
    attendance.rfid_tap(conn, "CARD0001", at("07:00"))
    during = {r["id"]: r["status"] for r in sessions.roster(conn, s, at("07:10"))}
    assert during[MARIA] == "present" and during[JOSE] == "pending"
    after = {r["id"]: r["status"] for r in sessions.roster(conn, s, at("07:31"))}
    assert after[JOSE] == "absent" and after[MARK] == "absent"


def _s(conn, sid=1):
    return dict(conn.execute("SELECT * FROM class_sessions WHERE id = ?", (sid,)).fetchone())


def test_manual_session_absent_after_end(conn):
    sessions.set_state(conn, PROF, 1, "present", at("07:00"))
    assert not sessions.is_finished(_s(conn), at("07:40"))               # manual: still open
    sessions.end_session(conn, PROF, 1, at("07:45"))
    assert sessions.is_finished(_s(conn), at("07:46"))


# ---------------------------------------------------------------- end session vs automatic
def test_end_session_during_auto_window_really_closes(conn):
    sessions.end_session(conn, PROF, 1, at("07:05"))                     # auto window still open until 07:30
    assert attendance.rfid_tap(conn, "CARD0001", at("07:06"))["result"] == "no_open_session"


def test_ended_session_cannot_be_switched_back_to_auto(conn):
    sessions.end_session(conn, PROF, 1, at("07:05"))
    with pytest.raises(ValidationError):
        sessions.set_mode(conn, PROF, 1, "auto", at("07:06"))
    assert attendance.rfid_tap(conn, "CARD0001", at("07:07"))["result"] == "no_open_session"


def test_reopen_after_end(conn):
    sessions.end_session(conn, PROF, 1, at("07:05"))
    sessions.set_state(conn, PROF, 1, "present", at("07:20"))            # explicit Reopen
    assert _s(conn)["ended_at"] is None
    assert attendance.rfid_tap(conn, "CARD0001", at("07:21"))["result"] == "present"


def test_switch_to_manual_keeps_current_state(conn):
    sessions.set_mode(conn, PROF, 1, "manual", at("07:20"))              # auto said "late" at 07:20
    s = _s(conn)
    assert (s["checkin_mode"], s["manual_state"]) == ("manual", "late")
    # and it stays open past the auto closing time until ended
    assert attendance.rfid_tap(conn, "CARD0001", at("07:50"))["result"] == "late"


def test_end_before_opening_marks_everyone_absent(conn):
    sessions.end_session(conn, PROF, 1, at("06:00"))                     # class cancelled? still ended
    rows = sessions.roster(conn, _s(conn), at("06:01"))
    assert {r["status"] for r in rows} == {"absent"}


def test_mark_and_clear_then_student_can_check_in(conn):
    sessions.mark(conn, PROF, 1, ANA, "excused", at("07:00"))
    sessions.mark(conn, PROF, 1, ANA, "clear", at("07:01"))
    assert attendance.rfid_tap(conn, "CARD0003", at("07:02"))["result"] == "present"
    sessions.mark(conn, PROF, 1, ANA, "late", at("07:03"))               # instructor correction
    rec = conn.execute("SELECT status, method FROM attendance WHERE session_id = 1 AND student_id = ?",
                       (ANA,)).fetchone()
    assert tuple(rec) == ("late", "rfid")                                # keeps how they checked in


def test_student_stats_and_rate(conn):
    attendance.rfid_tap(conn, "CARD0001", at("07:00"))                   # Maria present
    attendance.rfid_tap(conn, "CARD0002", at("07:20"))                   # Jose late
    sessions.mark(conn, PROF, 1, ANA, "excused")
    now = at("08:00")
    maria = sessions.student_section_stats(conn, MARIA, 1, now)["summary"]
    mark_ = sessions.student_section_stats(conn, MARK, 1, now)["summary"]
    ana = sessions.student_section_stats(conn, ANA, 1, now)["summary"]
    # the demo data also has an async activity where everyone is auto-present
    assert (maria["present"], maria["held"], maria["rate"]) == (2, 2, 100)
    assert (mark_["absent"], mark_["present"], mark_["rate"]) == (1, 1, 50)
    assert (ana["excused"], ana["rate"]) == (1, 100)                     # excused counts as attended


def test_students_enrolled_later_are_not_absent_for_earlier_classes(conn):
    conn.execute("UPDATE enrollments SET enrolled_at = '2026-12-01 08:00:00' WHERE student_id = ?", (MARK,))
    conn.commit()
    assert sessions.student_section_stats(conn, MARK, 1, at("08:00"))["summary"]["absent"] == 0


def test_joining_after_class_same_day_is_not_absent(conn):
    # Mark joins at 10:00 on the same day as the 07:00 class
    conn.execute("UPDATE enrollments SET enrolled_at = '2026-11-16 10:00:00' WHERE student_id = ?", (MARK,))
    conn.commit()
    assert sessions.student_section_stats(conn, MARK, 1, at("11:00"))["summary"]["absent"] == 0
    s = dict(conn.execute("SELECT * FROM class_sessions WHERE id = 1").fetchone())
    mark_row = next(r for r in sessions.roster(conn, s, at("11:00")) if r["id"] == MARK)
    assert mark_row["joined_later"] and mark_row["status"] == "pending"     # listed, shown "—", not absent


def test_joining_before_class_same_day_counts(conn):
    conn.execute("UPDATE enrollments SET enrolled_at = '2026-11-16 06:00:00' WHERE student_id = ?", (MARK,))
    conn.commit()
    assert sessions.student_section_stats(conn, MARK, 1, at("11:00"))["summary"]["absent"] == 1


def test_session_added_later_for_a_past_date_lists_everyone_enrolled(conn):
    """The instructor adds a session today for a class two weeks ago: the students who are
    enrolled now must be on its list, even if they joined after that date."""
    conn.execute("UPDATE enrollments SET enrolled_at = '2026-11-10 08:00:00' WHERE section_id = 1")
    conn.commit()
    sid = sessions.create_session(conn, PROF, 1, {"session_date": "2026-11-02", "start_time": "07:00",
                                                  "end_time": "09:00", "checkin_mode": "manual"},
                                  now=at("08:00"))
    s = dict(conn.execute("SELECT * FROM class_sessions WHERE id = ?", (sid,)).fetchone())
    listed = {r["id"] for r in sessions.roster(conn, s, at("08:00"))}
    assert {MARIA, JOSE, ANA, MARK} <= listed
    # ... and it counts in each student's own record too
    rows = sessions.student_section_stats(conn, MARK, 1, at("08:00"))["history"]
    assert sid in [r["id"] for r in rows]


def test_students_who_join_after_the_session_was_added_are_never_absent_for_it(conn):
    sid = sessions.create_session(conn, PROF, 1, {"session_date": "2026-11-02", "start_time": "07:00",
                                                  "end_time": "09:00", "checkin_mode": "manual"},
                                  now=at("08:00"))
    conn.execute("UPDATE enrollments SET enrolled_at = '2026-11-20 08:00:00' WHERE student_id = ?", (MARK,))
    conn.commit()
    s = dict(conn.execute("SELECT * FROM class_sessions WHERE id = ?", (sid,)).fetchone())
    sessions.end_session(conn, PROF, sid, at("08:30"))
    rows = {r["id"]: r for r in sessions.roster(conn, s, at("09:00"))}
    assert rows[MARK]["status"] == "pending"           # joined after: "—", not absent
    assert rows[MARIA]["status"] == "absent"           # was enrolled, didn't come: absent


def test_every_enrolled_student_is_listed_with_a_dash_until_class_ends(conn):
    s = dict(conn.execute("SELECT * FROM class_sessions WHERE id = 1").fetchone())
    attendance.rfid_tap(conn, "CARD0001", at("07:00"))                       # only Maria taps
    during = {r["id"]: r["status"] for r in sessions.roster(conn, s, at("07:10"))}
    assert during == {MARIA: "present", JOSE: "pending", ANA: "pending", MARK: "pending"}
    after = {r["id"]: r["status"] for r in sessions.roster(conn, s, at("10:00"))}
    assert after[JOSE] == "absent" and after[MARIA] == "present"


# ---------------------------------------------------------------- web pages
def prof(client):
    login(client, "prof", "prof123", PROF_SITE)


def test_sessions_tab_and_session_page(client):
    prof(client)
    r = client.get("/instructor/sections/1", base_url=PROF_SITE)              # default tab = attendance
    assert r.status_code == 200 and b"Add session" in r.data and b'id="add-session-modal"' in r.data
    assert b"Start session" not in r.data and b"Start today's class" not in r.data
    assert b'<option value="auto" selected>Automatic</option>' in r.data
    r = client.get("/instructor/sessions/1", base_url=PROF_SITE)
    assert r.status_code == 200 and b"Check-in control" in r.data and b"Santos, Maria" in r.data
    live = client.get("/instructor/sessions/1/live", base_url=PROF_SITE)
    assert live.status_code == 200
    # count tiles are buttons that open the student list pop-up; rows carry their status
    assert live.data.count(b'class="card stat stat-open') == 4
    assert b'data-group="present"' in live.data and b'data-status="' in live.data
    assert b'id="group-modal"' in client.get("/instructor/sessions/1", base_url=PROF_SITE).data


def test_web_controls_and_marking(app, client):
    prof(client)
    client.post("/instructor/sessions/1/state", data={"choice": "late"}, base_url=PROF_SITE)
    c = connect(app.config["DATABASE"])
    assert tuple(c.execute("SELECT checkin_mode, manual_state FROM class_sessions WHERE id=1").fetchone()) == ("manual", "late")
    r = client.post("/instructor/sessions/1/mark/2", data={"status": "excused"}, base_url=PROF_SITE,
                    headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and b"Excused" in r.data                     # returns the live partial
    client.post("/instructor/sessions/1/code", data={"enabled": "1"}, base_url=PROF_SITE)
    assert len(c.execute("SELECT checkin_code FROM class_sessions WHERE id=1").fetchone()[0]) == 4
    # old "auto" / "disabled" values are no longer live buttons
    assert client.post("/instructor/sessions/1/state", data={"choice": "auto"}, base_url=PROF_SITE).status_code == 400


def test_web_end_session_and_mode_settings(app, client):
    prof(client)
    c = connect(app.config["DATABASE"])
    client.post("/instructor/sessions/1/end", base_url=PROF_SITE)
    assert c.execute("SELECT ended_at FROM class_sessions WHERE id=1").fetchone()[0]
    page = client.get("/instructor/sessions/1", base_url=PROF_SITE).data
    assert b"Session ended" in page and b"Reopen check-in" in page and b"Locked because the session has ended" in page
    r = client.post("/instructor/sessions/1/mode", data={"mode": "auto"}, base_url=PROF_SITE, follow_redirects=True)
    assert b"Reopen it first" in r.data
    assert client.get("/instructor/sessions/1/live?part=controls", base_url=PROF_SITE).status_code == 200


def test_start_route_without_session_does_not_add_column(app, client):
    prof(client)
    c = connect(app.config["DATABASE"])
    c.execute("UPDATE class_sessions SET deleted_at = '2026-01-01'"); c.commit()
    r = client.post("/instructor/sections/1/start", base_url=PROF_SITE, follow_redirects=True)
    assert b"no upcoming class session today" in r.data
    assert c.execute("SELECT COUNT(*) FROM class_sessions WHERE deleted_at IS NULL").fetchone()[0] == 0
    r = client.post("/instructor/sections/1/add-column", base_url=PROF_SITE)
    assert "/instructor/sessions/" in r.location
    assert c.execute("SELECT COUNT(*) FROM class_sessions WHERE deleted_at IS NULL").fetchone()[0] == 1


def test_other_instructor_cannot_open_session(app, client):
    from werkzeug.security import generate_password_hash
    c = connect(app.config["DATABASE"])
    c.execute("INSERT INTO users (role, username, password_hash, first_name, last_name) "
              "VALUES ('instructor', 'other', ?, 'O', 'T')", (generate_password_hash("other1234"),))
    c.commit()
    login(client, "other", "other1234", PROF_SITE)
    assert client.get("/instructor/sessions/1", base_url=PROF_SITE).status_code == 404
    assert client.post("/instructor/sessions/1/state", data={"choice": "present"},
                       base_url=PROF_SITE).status_code == 404
    assert client.post("/instructor/sessions/1/async", data={"title": "Reading"}, base_url=PROF_SITE).status_code == 404


def test_add_session_today_uses_automatic_window_instead_of_opening_now(app, client):
    prof(client)
    today = date.today().isoformat()
    c = connect(app.config["DATABASE"])
    c.execute("UPDATE sections SET default_checkin_mode = 'manual' WHERE id = 1")
    c.commit()
    r = client.post("/instructor/sections/1/sessions", base_url=PROF_SITE,
                    data={"type": "regular", "session_date": today, "start_time": "08:00"})
    sid = int(r.location.rstrip("/").split("/")[-1])
    s = sessions.get_session(c, PROF, sid)
    assert (s["checkin_mode"], s["manual_state"], s["opened_at"]) == ("auto", "disabled", None)
    start = datetime.fromisoformat(f"{today}T08:00")
    assert sessions.describe(s, start - timedelta(minutes=15))["phase"] == "upcoming"
    assert sessions.describe(s, start)["phase"] == "present"
    assert not sessions.has_records(c, sid)


def test_session_for_another_date_can_choose_manual_and_stays_closed(app, client):
    prof(client)
    r = client.post("/instructor/sections/1/sessions", base_url=PROF_SITE,
                    data={"type": "makeup", "session_date": "2030-01-15", "start_time": "08:00", "checkin_mode": "manual"})
    sid = int(r.location.rstrip("/").split("/")[-1])
    c = connect(app.config["DATABASE"])
    s = c.execute("SELECT checkin_mode, manual_state, opened_at FROM class_sessions WHERE id = ?", (sid,)).fetchone()
    assert tuple(s) == ("manual", "disabled", None)


def test_add_session_errors_reopen_the_popup(client):
    prof(client)
    r = client.post("/instructor/sections/1/sessions", base_url=PROF_SITE,
                    data={"type": "regular", "session_date": "", "start_time": ""})
    assert r.status_code == 400
    assert b"data-open-on-load" in r.data and b"Pick a date" in r.data


def test_web_async_session(app, client):
    prof(client)
    r = client.post("/instructor/sections/1/sessions", base_url=PROF_SITE,
                    data={"type": "async", "session_date": "2026-11-13", "title": "Module 3 quiz",
                          "start_time": "14:30"})
    page = client.get(r.location, base_url=PROF_SITE).data
    assert b"Automatic attendance" in page and b"Auto (async)" in page
    assert b"2:30 PM" in page
    assert b"Web check-in code" not in page and b"End session" not in page


def test_set_upcoming_session_async_keeps_attendance_and_replaces_weekly_occurrence(app, client):
    prof(client)
    day = date.today() + timedelta(days=2)
    c = connect(app.config["DATABASE"])
    classes.add_schedule(c, PROF, 1, {"day_of_week": str(day.weekday()), "start_time": "08:00", "end_time": "09:00"})
    sessions.ensure_sessions_for_date(c, day)
    sid = c.execute("SELECT id FROM class_sessions WHERE schedule_id IS NOT NULL").fetchone()[0]
    sessions.mark(c, PROF, sid, MARIA, "excused")
    sessions.set_code(c, sid, True)

    url = f"/instructor/sessions/{sid}"
    controls = client.get(url + "/live?part=controls", base_url=PROF_SITE).data
    assert controls.index(b"End session") < controls.index(b"<hr") < controls.index("Async activity…".encode())
    response = client.post(url + "/async", base_url=PROF_SITE, data={"title": "Module reading"}, follow_redirects=True)
    assert response.status_code == 200 and b"Module reading" in response.data
    assert b"Web check-in code" not in response.data
    s = sessions.get_session(c, PROF, sid)
    assert (s["type"], s["manual_state"], s["checkin_code"]) == ("async", "disabled", None)
    assert s["ended_at"] and sessions.ensure_sessions_for_date(c, day) == 0
    c.commit()
    marks = {r["student_id"]: (r["status"], r["note"]) for r in c.execute(
        "SELECT student_id, status, note FROM attendance WHERE session_id = ?", (sid,))}
    assert marks == {MARIA: ("excused", None), **{uid: ("present", "async") for uid in (JOSE, ANA, MARK)}}

    calendar = calendar_view.month_view(c, MARIA, day.year, day.month)
    events = next(item["events"] for week in calendar["weeks"] for item in week if item["date"] == day)
    assert [event["kind"] for event in events] == ["async"]
    assert all(row["date"] != day.isoformat() for row in sessions.upcoming_from_schedule(c, 1))
    rejected = client.post(url + "/async", base_url=PROF_SITE, data={"title": "Retry"}, follow_redirects=True)
    assert b"Only sessions that haven&#39;t ended" in rejected.data
    assert sessions.get_session(c, PROF, sid)["title"] == "Module reading"
    sessions.delete_session(c, PROF, sid)
    trash.restore(c, PROF, "session", sid)
    assert sessions.get_session(c, PROF, sid)["type"] == "async"
    assert c.execute("SELECT 1 FROM schedule_exceptions WHERE exception_date = ?", (day.isoformat(),)).fetchone() is None
    page = client.get("/instructor/sections/1?upcoming=all", base_url=PROF_SITE).data
    assert b"Module reading" in page.split(b'id="upcoming-sessions"', 1)[1].split(b'</details>', 1)[0]
    assert b'data-start="08:00"' in page and b"Regular (undo async)" in page

    response = client.post(url + "/async/change", base_url=PROF_SITE,
                           data={"choice": "regular", "start_time": "08:00", "end_time": "09:00"}, follow_redirects=True)
    assert b"Changed back to a regular class." in response.data
    s = sessions.get_session(c, PROF, sid)
    assert (s["type"], s["checkin_mode"], s["start_time"], s["ended_at"]) == ("regular", "auto", "08:00", None)
    marks = c.execute("SELECT student_id, status FROM attendance WHERE session_id = ?", (sid,)).fetchall()
    assert [tuple(r) for r in marks] == [(MARIA, "excused")]


def test_added_async_activity_can_be_changed_to_holiday(app, client):
    prof(client)
    day = (date.today() + timedelta(days=3)).isoformat()
    response = client.post("/instructor/sections/1/sessions", base_url=PROF_SITE,
                           data={"type": "async", "session_date": day, "title": "Future activity"})
    path = response.location
    page = client.get("/instructor/sections/1?upcoming=all", base_url=PROF_SITE).data
    assert b"Future activity" in page.split(b'id="upcoming-sessions"', 1)[1].split(b'</details>', 1)[0]
    assert b"Holiday" in page
    response = client.post(path + "/async/change", base_url=PROF_SITE, data={"choice": "Holiday"}, follow_redirects=True)
    assert response.status_code == 200 and b"Future activity" not in response.data
    c = connect(app.config["DATABASE"])
    assert tuple(c.execute("SELECT reason, mark_present FROM schedule_exceptions WHERE exception_date = ?", (day,)).fetchone()) == ("Holiday", 0)
    assert b"Holiday" in response.data.split(b'id="upcoming-sessions"', 1)[1].split(b'</details>', 1)[0]


def test_student_class_page(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123", STUDENT_SITE)
    r = client.get("/student/classes/1")
    assert r.status_code == 200 and b"Attendance rate" in r.data and b"Attendance history" in r.data
    assert b"absent" in client.get("/student/classes").data                  # My classes chip
    assert client.get("/student/classes/999").status_code == 404


# ---------------------------------------------------------------- reset statuses (undo a mistake)
def _status(conn, sid, now):
    s = sessions.get_session(conn, PROF, sid)
    return {r["id"]: r["status"] for r in sessions.roster(conn, s, now)}


def test_reset_statuses_undoes_an_early_end(conn):
    attendance.web_check_in(conn, MARIA, now=at("07:02"))
    sessions.mark(conn, PROF, 1, JOSE, "late", at("07:03"))
    sessions.end_session(conn, PROF, 1, at("07:05"))                 # pressed too early
    assert _status(conn, 1, at("07:06"))[ANA] == "absent"

    assert sessions.reset_session(conn, PROF, 1, at("07:06")) == 2
    s = sessions.get_session(conn, PROF, 1)
    assert (s["ended_at"], s["opened_at"], s["checkin_mode"], s["manual_state"]) == (None, None, "manual", "disabled")
    assert set(_status(conn, 1, at("07:06")).values()) == {"pending"}     # everyone "—" again
    assert sessions.describe(s, at("07:06"))["phase"] == "upcoming"

    # open again: a student checks in with a clean record (no leftover check-out time or note)
    sessions.set_state(conn, PROF, 1, "present", at("07:07"))
    assert attendance.web_check_in(conn, MARIA, now=at("07:08"))["result"] == "present"
    row = conn.execute("SELECT status, checked_out_at, note, deleted_at FROM attendance "
                       "WHERE session_id = 1 AND student_id = ?", (MARIA,)).fetchone()
    assert tuple(row) == ("present", None, None, None)


def test_reset_leaves_async_activities_alone(conn):
    sid = conn.execute("SELECT id FROM class_sessions WHERE type = 'async'").fetchone()[0]
    before = conn.execute("SELECT COUNT(*) FROM attendance WHERE session_id = ? AND deleted_at IS NULL", (sid,)).fetchone()[0]
    assert sessions.reset_session(conn, PROF, sid, at("08:00")) == 0
    assert conn.execute("SELECT COUNT(*) FROM attendance WHERE session_id = ? AND deleted_at IS NULL",
                        (sid,)).fetchone()[0] == before


def test_reopened_past_class_shows_not_yet_while_open(conn):
    later = T0 + timedelta(days=2)                                   # the demo class date has passed
    assert _status(conn, 1, later)[ANA] == "absent"
    sessions.set_state(conn, PROF, 1, "present", later)              # reopened to fix it
    assert _status(conn, 1, later)[ANA] == "pending"                 # "—" while check-in is open
    sessions.end_session(conn, PROF, 1, later)
    assert _status(conn, 1, later)[ANA] == "absent"


def test_async_offered_until_the_session_ends(conn):
    s = lambda: sessions.get_session(conn, PROF, 1)                  # noqa: E731
    sessions.set_state(conn, PROF, 1, "present", at("07:02"))
    assert sessions.can_set_async(s(), at("07:02"))                 # open: still possible
    sessions.end_session(conn, PROF, 1, at("07:30"))
    assert not sessions.can_set_async(s(), at("07:31"))              # ended: reset first


def test_reset_route(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    c = connect(app.config["DATABASE"])
    sid = c.execute("SELECT id FROM class_sessions WHERE type <> 'async' ORDER BY id LIMIT 1").fetchone()[0]
    sessions.end_session(c, PROF, sid)
    controls = client.get(f"/instructor/sessions/{sid}/live?part=controls", base_url=PROF_SITE).data
    assert b"Reset statuses" in controls
    r = client.post(f"/instructor/sessions/{sid}/reset", base_url=PROF_SITE)
    assert r.status_code == 302
    assert sessions.get_session(c, PROF, sid)["ended_at"] is None
    c.close()
    # students can't reset anything
    give_signature(app, MARIA)
    student = app.test_client()
    login(student, "2026-0001", "student123")
    assert student.post(f"/instructor/sessions/{sid}/reset", base_url=PROF_SITE).status_code in (302, 403, 404)
    assert student.post(f"/instructor/sessions/{sid}/reset", base_url=STUDENT_SITE).status_code in (302, 403, 404)
