"""Instructor pages (web level) + student join page."""
from app.db import connect

from .test_auth import PROF_SITE, STUDENT_SITE, give_signature, login


def prof(client):
    login(client, "prof", "prof123", PROF_SITE)


def get(client, url):
    return client.get(url, base_url=PROF_SITE)


def post(client, url, data=None):
    return client.post(url, data=data or {}, base_url=PROF_SITE)


def test_dashboard_lists_subjects(client):
    prof(client)
    r = get(client, "/instructor/")
    assert b"IT312" in r.data and b"Embedded Systems" in r.data


def test_create_subject_and_section(app, client):
    prof(client)
    r = post(client, "/instructor/subjects/new", {"code": "IT401", "title": "Capstone",
                                                   "school_year": "2026-2027", "semester": "2nd"})
    assert "/instructor/subjects/" in r.location
    subject_id = int(r.location.rstrip("/").split("/")[-1])

    r = post(client, f"/instructor/subjects/{subject_id}/sections", {"name": "BSIT 4A"})
    assert "/instructor/sections/" in r.location
    assert b"BSIT 4A" in get(client, r.location).data


def test_subject_form_shows_errors(client):
    prof(client)
    r = post(client, "/instructor/subjects/new", {"code": "", "title": "", "school_year": "x", "semester": ""})
    assert r.status_code == 200
    assert b"Subject code is required" in r.data and b"2026-2027" in r.data


def test_section_tabs_render(client):
    prof(client)
    for tab in ("students", "schedule", "settings"):
        r = get(client, f"/instructor/sections/1?tab={tab}")
        assert r.status_code == 200, tab
    assert b"Santos, Maria" in get(client, "/instructor/sections/1?tab=students").data
    assert b"For a 7:00 class" in get(client, "/instructor/sections/1?tab=settings").data


def test_section_settings_error_stays_on_tab(client):
    prof(client)
    r = post(client, "/instructor/sections/1/edit", {
        "name": "BSIT 3A", "default_checkin_mode": "auto", "default_open_before_minutes": "5",
        "default_grace_minutes": "20", "default_close_after_minutes": "10"})
    assert r.status_code == 400
    assert b"must close after the grace period" in r.data


def test_add_schedule_and_students(client):
    prof(client)
    post(client, "/instructor/sections/1/schedules", {"day_of_week": "4", "start_time": "13:00", "end_time": "15:00"})
    assert b"Friday" in get(client, "/instructor/sections/1?tab=schedule").data
    r = post(client, "/instructor/sections/1/students", {"student_numbers": "2026-0001 9999-9999"})
    page = get(client, r.location).data
    assert b"Already enrolled: 2026-0001" in page and b"Not registered yet" in page


def test_students_tab_uses_popup(client):
    prof(client)
    page = get(client, "/instructor/sections/1?tab=students").data
    assert b'id="add-students-modal"' in page and b'data-bs-target="#add-students-modal"' in page
    assert b"Generate new code" not in page                     # old side card is gone
    assert page.count(b'name="student_numbers"') == 1          # only the pop-up's textarea


def test_new_join_code_from_header_stays_on_tab(app, client):
    prof(client)
    c = connect(app.config["DATABASE"])
    old = c.execute("SELECT join_code FROM sections WHERE id = 1").fetchone()[0]
    r = post(client, "/instructor/sections/1/join-code", {"back": "schedule"})
    assert r.location.endswith("tab=schedule")
    assert c.execute("SELECT join_code FROM sections WHERE id = 1").fetchone()[0] != old
    assert b"New code" in get(client, "/instructor/sections/1?tab=settings").data   # on every tab


def test_schedule_tab_uses_popup(client):
    prof(client)
    page = get(client, "/instructor/sections/1?tab=schedule").data
    assert b'id="add-schedule-modal"' in page and b'data-bs-target="#add-schedule-modal"' in page
    assert b"Add a class day" not in page
    r = post(client, "/instructor/sections/1/schedules", {"day_of_week": "0", "start_time": "10:00", "end_time": "09:00"})
    assert r.status_code == 400 and b"data-open-on-load" in r.data       # error -> pop-up reopens


def test_settings_danger_zone_below_settings(client):
    prof(client)
    page = get(client, "/instructor/sections/1?tab=settings").data
    assert page.index(b"Save settings") < page.index(b"Danger zone")


def test_subject_page_lists_each_schedule_on_its_own_line(client):
    prof(client)
    for day, start, end in (("0", "07:00", "09:00"), ("2", "13:00", "15:00"), ("4", "10:00", "11:30")):
        post(client, "/instructor/sections/1/schedules", {"day_of_week": day, "start_time": start, "end_time": end})
    page = get(client, "/instructor/subjects/1").data
    assert page.count(b'class="sched-line"') == 3
    assert b"Add section" in page and b'id="add-section-modal"' in page
    assert b'name="course"' in page and b'name="year_level"' in page and b'name="section_letter"' in page
    assert b"course abbreviation" not in page and b"Each section gets its own join code" not in page


def test_edit_schedule_from_menu(app, client):
    prof(client)
    post(client, "/instructor/sections/1/schedules", {"day_of_week": "0", "start_time": "07:00", "end_time": "09:00"})
    sid = connect(app.config["DATABASE"]).execute("SELECT id FROM schedules").fetchone()[0]
    page = get(client, "/instructor/sections/1?tab=schedule").data
    assert b"Edit schedule" in page and b"data-edit-schedule" in page
    r = post(client, f"/instructor/schedules/{sid}/edit", {"day_of_week": "3", "start_time": "13:00", "end_time": "15:00"})
    assert r.location.endswith("tab=schedule")
    assert b"Thursday" in get(client, r.location).data
    bad = post(client, f"/instructor/schedules/{sid}/edit", {"day_of_week": "3", "start_time": "15:00", "end_time": "13:00"})
    assert bad.status_code == 400 and b"data-open-on-load" in bad.data and b"Save changes" in bad.data


def test_delete_subject(client):
    prof(client)
    post(client, "/instructor/subjects/1/delete")
    assert b"No subjects yet" in get(client, "/instructor/").data
    assert get(client, "/instructor/subjects/1").status_code == 404


def test_other_instructor_gets_404(app, client):
    c = connect(app.config["DATABASE"])
    from werkzeug.security import generate_password_hash
    c.execute("""INSERT INTO users (role, username, password_hash, first_name, last_name)
                 VALUES ('instructor', 'other', ?, 'O', 'Ther')""", (generate_password_hash("other1234"),))
    c.commit()
    login(client, "other", "other1234", PROF_SITE)
    assert get(client, "/instructor/sections/1").status_code == 404
    assert post(client, "/instructor/subjects/1/delete").status_code == 404


def test_student_joins_with_code(app, client):
    c = connect(app.config["DATABASE"])
    c.execute("INSERT INTO sections (subject_id, name, join_code) VALUES (1, 'BSIT 3B', 'ABC234')")
    c.execute("UPDATE users SET program='BSIT', year_level=3, section_letter='B', academic_locked=1 WHERE id = 2")
    c.commit()
    give_signature(app, 2)
    login(client, "2026-0001", "student123", STUDENT_SITE)
    r = client.post("/student/join", data={"join_code": "abc234"}, follow_redirects=True)
    assert b"BSIT 3B" in r.data                         # the class now shows in My classes
    # a wrong code still tells the student, as a floating toast
    bad = client.post("/student/join", data={"join_code": "NOPE99"}, follow_redirects=True)
    assert b"app-toast app-toast-warning" in bad.data and b"No class found" in bad.data
