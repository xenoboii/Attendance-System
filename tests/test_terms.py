"""End semester / archive / copy to new term, the drop rule, admin overview."""
from datetime import datetime, timedelta

import pytest

from app.db import connect
from app.services import accounts, admin_overview, classes, sessions, terms
from app.services import attendance_service as attendance
from app.services.accounts import ValidationError

from .conftest import JOSE, MARIA, T0, at
from .test_auth import PROF_SITE, give_signature, login

PROF = 1


def subject_id(conn):
    return conn.execute("SELECT subject_id FROM sections WHERE id = 1").fetchone()[0]


# ---------------------------------------------------------------- end semester / archive
def test_end_semester_stops_classes_and_archives(conn):
    sid = subject_id(conn)
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00"})
    assert sessions.upcoming_from_schedule(conn, 1, T0)
    assert terms.end_semester(conn, PROF, sid, T0) is True
    assert terms.end_semester(conn, PROF, sid, T0) is False                       # already ended
    assert [s["id"] for s in classes.list_subjects(conn, PROF)] == [x["id"] for x in classes.list_subjects(conn, PROF) if x["id"] != sid]
    assert sid in [s["id"] for s in terms.list_archived(conn, PROF)]
    assert sessions.upcoming_from_schedule(conn, 1, T0) == []
    wednesday = (T0 + timedelta(days=2)).date()
    before = conn.execute("SELECT COUNT(*) FROM class_sessions").fetchone()[0]
    sessions.ensure_sessions_for_date(conn, wednesday)
    assert conn.execute("SELECT COUNT(*) FROM class_sessions").fetchone()[0] == before   # nothing new
    assert attendance.rfid_tap(conn, "CARD0001", at("07:02"))["result"] == "no_open_session"
    assert classes.todays_schedule(conn, student_id=MARIA, today=T0) == []
    assert classes.join_by_code(conn, JOSE, "JOIN3A")["ok"] is False


def test_reopen_brings_it_back(conn):
    sid = subject_id(conn)
    terms.end_semester(conn, PROF, sid, T0)
    assert terms.reopen(conn, PROF, sid) is True and terms.reopen(conn, PROF, sid) is False
    assert sid in [s["id"] for s in classes.list_subjects(conn, PROF)]


def test_next_term():
    assert terms.next_term("2026-2027", "1st") == {"school_year": "2026-2027", "semester": "2nd"}
    assert terms.next_term("2026-2027", "2nd") == {"school_year": "2026-2027", "semester": "Summer"}
    assert terms.next_term("2026-2027", "Summer") == {"school_year": "2027-2028", "semester": "1st"}


def test_copy_to_new_term_copies_sections_and_schedule_only(conn):
    sid = subject_id(conn)
    classes.add_schedule(conn, PROF, 1, {"day_of_week": "2", "start_time": "13:00", "end_time": "14:00", "room": "Lab 2"})
    terms.end_semester(conn, PROF, sid, T0)
    old_sections = conn.execute("SELECT name, join_code FROM sections WHERE subject_id = ? AND deleted_at IS NULL ORDER BY name", (sid,)).fetchall()
    new = terms.copy_to_new_term(conn, PROF, sid, {"school_year": "2026-2027", "semester": "2nd"})
    assert new != sid
    row = conn.execute("SELECT * FROM subjects WHERE id = ?", (new,)).fetchone()
    assert row["semester"] == "2nd" and row["ended_at"] is None
    new_sections = conn.execute("SELECT id, name, join_code FROM sections WHERE subject_id = ? ORDER BY name", (new,)).fetchall()
    assert [s["name"] for s in new_sections] == [s["name"] for s in old_sections]
    assert not {s["join_code"] for s in new_sections} & {s["join_code"] for s in old_sections}
    first = new_sections[0]["id"]
    assert conn.execute("SELECT COUNT(*) FROM enrollments WHERE section_id = ?", (first,)).fetchone()[0] == 0
    old_sched = conn.execute("SELECT COUNT(*) FROM schedules WHERE section_id = 1 AND deleted_at IS NULL").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM schedules WHERE section_id = ?", (first,)).fetchone()[0] == old_sched
    with pytest.raises(ValidationError):                                           # same term again
        terms.copy_to_new_term(conn, PROF, sid, {"school_year": "2026-2027", "semester": "2nd"})
    with pytest.raises(ValidationError):
        terms.copy_to_new_term(conn, PROF, sid, {"school_year": "nonsense", "semester": "1st"})


def test_archive_pages_and_read_only(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    get = lambda u: client.get(u, base_url=PROF_SITE)                              # noqa: E731
    post = lambda u, d=None: client.post(u, data=d or {}, base_url=PROF_SITE)       # noqa: E731
    with connect(app.config["DATABASE"]) as c:
        sid = c.execute("SELECT subject_id FROM sections WHERE id = 1").fetchone()[0]
    assert b"Nothing archived yet" in get("/instructor/archive").data
    assert b"End semester" in get(f"/instructor/subjects/{sid}").data
    assert b"End of semester" in get("/instructor/sections/1?tab=settings").data
    assert post(f"/instructor/subjects/{sid}/end").location.endswith("/instructor/archive")
    arch = get("/instructor/archive").data
    assert b"IT312" in arch and b"Copy to new term" in arch and b"Reopen" in arch
    assert b"IT312" not in get("/instructor/").data.split(b"panel-body")[1]         # gone from My subjects
    assert b"Archived." in get("/instructor/sections/1").data
    # nothing can be changed while archived, but the report still works
    r = post("/instructor/sections/1/schedules", {"day_of_week": "4", "start_time": "20:00", "end_time": "21:00"})
    assert r.status_code == 302
    with connect(app.config["DATABASE"]) as c:
        assert c.execute("SELECT COUNT(*) FROM schedules WHERE section_id = 1 AND start_time = '20:00'").fetchone()[0] == 0
    assert get("/instructor/sections/1/report?s=1").status_code == 200
    copied = post(f"/instructor/subjects/{sid}/copy", {"school_year": "2026-2027", "semester": "2nd"})
    assert "/instructor/subjects/" in copied.location and not copied.location.endswith(f"/{sid}")
    assert post(f"/instructor/subjects/{sid}/reopen").location.endswith(f"/instructor/subjects/{sid}")
    assert b"Nothing archived yet" in get("/instructor/archive").data


# ---------------------------------------------------------------- drop rule
def test_drop_status():
    assert classes.drop_status(4, 4) == "drop" and classes.drop_status(5, 4) == "drop"
    assert classes.drop_status(3, 4) == "warn" and classes.drop_status(2, 4) is None
    assert classes.drop_status(9, 0) is None                                       # rule switched off


def test_instructor_sets_the_drop_limit(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/instructor/sections/1?tab=settings", base_url=PROF_SITE).data
    assert b'name="drop_absences"' in page and b'value="4"' in page                # default: 4 absences
    with connect(app.config["DATABASE"]) as c:
        sec = dict(c.execute("SELECT * FROM sections WHERE id = 1").fetchone())
    form = {"name": sec["name"],
            "default_checkin_mode": sec["default_checkin_mode"],
            "default_open_before_minutes": sec["default_open_before_minutes"],
            "default_grace_minutes": sec["default_grace_minutes"],
            "default_close_after_minutes": sec["default_close_after_minutes"]}
    assert client.post("/instructor/sections/1/edit", data={**form, "drop_absences": "2"}, base_url=PROF_SITE).status_code == 302
    with connect(app.config["DATABASE"]) as c:
        assert c.execute("SELECT drop_absences FROM sections WHERE id = 1").fetchone()[0] == 2
    assert client.post("/instructor/sections/1/edit", data={**form, "drop_absences": "99"}, base_url=PROF_SITE).status_code == 400


def test_drop_and_put_back(conn):
    assert classes.drop_student(conn, PROF, 1, JOSE) is True
    assert classes.drop_student(conn, PROF, 1, JOSE) is False
    assert JOSE not in [s["id"] for s in classes.list_students(conn, 1)]
    assert [d["id"] for d in classes.list_dropped(conn, 1)] == [JOSE]
    assert classes.restore_student(conn, PROF, 1, JOSE) is True
    assert JOSE in [s["id"] for s in classes.list_students(conn, 1)] and classes.list_dropped(conn, 1) == []


def test_students_tab_flags_and_drop_route(app, client):
    with connect(app.config["DATABASE"]) as c:
        c.execute("UPDATE sections SET drop_absences = 1 WHERE id = 1")            # one absence is enough
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        c.execute("INSERT INTO class_sessions (section_id, session_date, start_time, end_time, type) "
                  "VALUES (1, ?, '08:00', '09:00', 'makeup')", (yesterday,))
        c.execute("UPDATE enrollments SET enrolled_at = '2020-01-01 00:00:00' WHERE section_id = 1")
        c.commit()
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/instructor/sections/1?tab=students", base_url=PROF_SITE).data
    assert b"For dropping" in page and b"Drop from class" in page
    assert client.post("/instructor/sections/1/students/3/drop", base_url=PROF_SITE).status_code == 302
    page = client.get("/instructor/sections/1?tab=students", base_url=PROF_SITE).data
    assert b'id="dropped-modal"' in page and b"Put back" in page
    assert client.post("/instructor/sections/1/students/3/restore", base_url=PROF_SITE).status_code == 302
    assert client.post("/instructor/sections/1/students/3/restore", base_url=PROF_SITE).status_code == 404
    # the student sees the warning on My classes
    give_signature(app, 2)
    student = app.test_client()
    login(student, "2026-0001", "student123")
    assert b"the limit for this class" in student.get("/student/classes").data


# ---------------------------------------------------------------- no Excel (PDF only)
def test_no_excel_download_on_the_section_page(app, client):
    """Instructors get the PDF report only."""
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/instructor/sections/1", base_url=PROF_SITE).data
    assert b"export.xlsx" not in page and b"bi-file-earmark-spreadsheet" not in page
    assert client.get("/instructor/sections/1/export.xlsx", base_url=PROF_SITE).status_code == 404


# ---------------------------------------------------------------- admin overview
def test_admin_overview(app, client, tmp_path):
    with connect(app.config["DATABASE"]) as c:
        uid = accounts.create_admin(c, {"username": "admin", "first_name": "Dev", "last_name": "Support",
                                        "password": "dev-password-123", "confirm_password": "dev-password-123"})
        counts = admin_overview.counts(c)
    assert counts["students"] >= 4 and counts["instructors"] >= 1 and counts["subjects"] >= 1
    st = admin_overview.storage(app.config["DATABASE"], tmp_path, tmp_path / "sig")
    assert st["known"] and 0 <= st["used_percent"] <= 100 and st["database"]
    assert admin_overview.human(1536) == "2 KB" and admin_overview.human(5 * 1024 ** 3) == "5.0 GB"
    with client.session_transaction() as s:
        s["user_id"] = uid
    page = client.get("/admin/").data
    for text in (b"Overview", b"Storage", b"Attendance device", b"Needs attention", b"Recent activity", b"Check-ins today"):
        assert text in page
    side = page[page.index(b'class="side-nav"'):]
    assert side.index(b"Overview") < side.index(b"Courses") < side.index(b"Departments")
    # "Open" lists the people each line is about
    assert b'id="attention-card"' in page and b'id="attention-department"' in page
    card_list = page[page.index(b'id="attention-card"'):page.index(b'id="attention-department"')]
    assert b"2026-0004" in card_list or b"Open</a>" in card_list
    assert b"Dela Cruz" in page[page.index(b'id="attention-department"'):]
    names = [g["key"] for g in admin_overview.attention(connect(app.config["DATABASE"]))]
    assert "department" in names


def test_admin_sets_how_long_checkin_signatures_are_kept(app, client):
    from app.services import app_settings, checkin_signatures
    with connect(app.config["DATABASE"]) as c:
        uid = accounts.create_admin(c, {"username": "admin", "first_name": "Dev", "last_name": "Support",
                                        "password": "dev-password-123", "confirm_password": "dev-password-123"})
        assert app_settings.signature_days(c, 60) == 60                             # default until changed
        # a web check-in 20 days ago, with its drawing
        old = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO class_sessions (section_id, session_date, start_time, type) VALUES (1, ?, '08:00', 'makeup')", (old[:10],))
        sess = c.execute("SELECT MAX(id) FROM class_sessions").fetchone()[0]
        c.execute("INSERT INTO attendance (session_id, student_id, status, method, checked_in_at) VALUES (?, 2, 'present', 'web', ?)", (sess, old))
        c.commit()
        checkin_signatures.save(c, sess, 2, b"\x89PNG fake", app.config["SIGNATURE_DIR"])
    folder = app.config["SIGNATURE_DIR"] / "checkins"
    assert len(list(folder.glob("*.png"))) == 1
    with client.session_transaction() as s:
        s["user_id"] = uid
    assert b'name="days"' in client.get("/admin/").data and b'value="60"' in client.get("/admin/").data
    for bad in ("", "abc", "3", "9999"):
        client.post("/admin/settings/signature-days", data={"days": bad})
    with connect(app.config["DATABASE"]) as c:
        assert app_settings.signature_days(c, 60) == 60
    assert len(list(folder.glob("*.png"))) == 1
    r = client.post("/admin/settings/signature-days", data={"days": "14"}, follow_redirects=True)
    assert b"kept for 14 days" in r.data and b"1 older one deleted" in r.data       # applied right away
    assert list(folder.glob("*.png")) == [] and b'value="14"' in client.get("/admin/").data
    prof = app.test_client()
    login(prof, "prof", "prof123", PROF_SITE)
    assert prof.post("/admin/settings/signature-days", data={"days": "30"}, base_url=PROF_SITE).status_code == 403
