"""PDF attendance report (school attendance sheet format)."""
import base64
from datetime import datetime

import pytest

from app.db import connect
from app.services import attendance_service as attendance
from app.services import report, sessions
from app.services.accounts import ValidationError

from .conftest import ANA, JOSE, MARIA, MARK, T0, at
from .test_auth import PROF_SITE, TINY_PNG, login

PROF = 1


def _sig(tmp_path, user_id):
    (tmp_path / f"{user_id}.png").write_bytes(base64.b64decode(TINY_PNG.split(",")[1]))


def _section(conn):
    from app.services import classes
    return dict(classes.get_section(conn, PROF, 1))


def test_selection_limits(conn):
    ids = [sessions.create_session(conn, PROF, 1, {"type": "makeup", "session_date": f"2026-10-{d:02d}",
                                                   "start_time": "08:00"}, T0) for d in range(1, 8)]
    assert len(report.validate_selection(conn, 1, ids, T0)) == 7   # more than 6 is fine -> 2 pages
    assert len(report.validate_selection(conn, 1, ids[:6], T0)) == 6
    with pytest.raises(ValidationError):
        report.validate_selection(conn, 1, [])
    with pytest.raises(ValidationError):
        report.validate_selection(conn, 1, [999])                   # not this section


def test_upcoming_sessions_are_not_reportable(conn):
    future = sessions.create_session(conn, PROF, 1, {"type": "makeup", "session_date": "2099-01-05",
                                                     "start_time": "08:00"}, T0)
    assert future not in [s["id"] for s in report.reportable_sessions(conn, 1, T0)]
    with pytest.raises(ValidationError) as e:
        report.validate_selection(conn, 1, [future])
    assert "Upcoming" in e.value.errors["sessions"]


def test_current_session_is_reportable(conn):
    sessions.set_state(conn, PROF, 1, "present", T0)                 # open right now
    assert 1 in [s["id"] for s in report.reportable_sessions(conn, 1, T0)]


def test_async_columns_come_after_dates(conn):
    later = sessions.create_session(conn, PROF, 1, {"type": "makeup", "session_date": "2026-11-10",
                                                    "start_time": "08:00"}, T0)
    chosen = report.validate_selection(conn, 1, [2, 1, later], at("08:00"))    # 2 = demo async
    assert [c["type"] for c in chosen][-1] == "async"


def test_matrix_statuses(conn):
    attendance.rfid_tap(conn, "CARD0001", at("07:00"))            # Maria present
    attendance.rfid_tap(conn, "CARD0002", at("07:20"))            # Jose late
    sessions.mark(conn, PROF, 1, ANA, "excused")
    chosen = report.validate_selection(conn, 1, [1], at("08:00"))
    students, cells = report.build_matrix(conn, _section(conn), chosen, at("08:00"))
    assert [s["last_name"] for s in students] == ["Lim", "Reyes", "Santos", "Tan"]
    assert cells[(MARIA, 1)] == "present" and cells[(JOSE, 1)] == "late"
    assert cells[(ANA, 1)] == "excused" and cells[(MARK, 1)] == "absent"


def test_pdf_is_built_with_signatures(conn, tmp_path):
    attendance.rfid_tap(conn, "CARD0001", at("07:00"))
    from PIL import Image
    _sig(tmp_path, MARIA)
    Image.new("RGBA", (40, 12), (10, 20, 90, 255)).save(tmp_path / f"{PROF}.png")   # a different image
    for uid in (MARIA, PROF):
        conn.execute("UPDATE users SET signature_path = 'x' WHERE id = ?", (uid,))
    conn.commit()
    chosen = report.validate_selection(conn, 1, [1, 2], at("08:00"))
    instructor = dict(conn.execute("SELECT * FROM users WHERE id = 1").fetchone())
    pdf = report.build_pdf(conn, _section(conn), instructor, chosen, tmp_path,
                           school_lines=["Republic of the Philippines", "Test University", "Province"],
                           now=at("08:00"))
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000
    # instructor signature row is blank by default, filled only when asked
    signed = report.build_pdf(conn, _section(conn), instructor, chosen, tmp_path, now=at("08:00"),
                              include_prof_signature=True)
    blank = report.build_pdf(conn, _section(conn), instructor, chosen, tmp_path, now=at("08:00"))
    assert signed.count(b"/Subtype /Image") > blank.count(b"/Subtype /Image")


def _pages(pdf: bytes) -> int:
    import re
    return len(re.findall(rb"/Type /Page[^s]", pdf))


def test_pages_split_by_40_students_and_6_dates(conn, tmp_path):
    # 45 students (4 demo + 41 more) and 8 dates -> 2 student pages x 2 date pages = 4 sheets
    for i in range(100, 141):
        conn.execute("""INSERT INTO users (id, role, username, password_hash, first_name, last_name, student_number)
                        VALUES (?, 'student', ?, 'x', 'S', ?, ?)""", (i, f"u{i}", f"Z{i}", f"2026-{i}"))
        conn.execute("INSERT INTO enrollments (section_id, student_id, enrolled_at) VALUES (1, ?, '2026-01-01 00:00:00')", (i,))
    conn.commit()
    ids = [sessions.create_session(conn, PROF, 1, {"type": "makeup", "session_date": f"2026-10-{d:02d}",
                                                   "start_time": "08:00"}, T0) for d in range(1, 9)]
    chosen = report.validate_selection(conn, 1, ids, T0)
    instructor = dict(conn.execute("SELECT * FROM users WHERE id = 1").fetchone())
    pdf = report.build_pdf(conn, _section(conn), instructor, chosen, tmp_path, min_rows=40, now=T0)
    assert _pages(pdf) == 4
    # 6 dates (one date page) x 45 students (two student pages) -> 2 sheets
    six = report.build_pdf(conn, _section(conn), instructor, chosen[:6], tmp_path, min_rows=40, now=T0)
    assert _pages(six) == 2


def test_report_route(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    r = client.get("/instructor/sections/1/report?s=1&s=2&dl=1", base_url=PROF_SITE)
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert "attachment" in r.headers["Content-Disposition"] and ".pdf" in r.headers["Content-Disposition"]
    preview = client.get("/instructor/sections/1/report?s=1", base_url=PROF_SITE)
    assert "inline" in preview.headers["Content-Disposition"]
    assert "no-store" in preview.headers["Cache-Control"]               # preview never shows a stale PDF
    bad = client.get("/instructor/sections/1/report", base_url=PROF_SITE)       # nothing picked
    assert bad.status_code == 302


# ---------------------------------------------------------------- blank dates (fill in on paper)
def _pdf_text(pdf):
    import re, zlib, base64 as b64
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        raw = m.group(1).strip()
        for decode in (lambda r: zlib.decompress(b64.a85decode(r, adobe=True)), zlib.decompress):
            try:
                out += decode(raw)
                break
            except Exception:
                pass
    return out


def test_blank_dates_sorted_with_real_dates(conn):
    chosen = report.validate_selection(conn, 1, [1], at("08:00"),
                                       blank_dates=["2026-11-25", "2026-11-10", "2026-11-25", ""])
    assert [s["session_date"] for s in chosen if s["type"] != "async"] == ["2026-11-10", "2026-11-16", "2026-11-25"]
    assert [bool(s.get("blank")) for s in chosen] == [True, False, True]      # duplicate + empty dropped


def test_only_blank_dates_is_enough(conn):
    chosen = report.validate_selection(conn, 1, [], at("08:00"), blank_dates=["2026-12-01"])
    assert len(chosen) == 1 and chosen[0]["blank"]


def test_blank_date_validation(conn):
    with pytest.raises(ValidationError, match="not a valid date"):
        report.validate_selection(conn, 1, [1], at("08:00"), blank_dates=["2026-13-45"])
    too_many = [f"2026-12-{d:02d}" for d in range(1, report.MAX_BLANK_DATES + 2)]
    with pytest.raises(ValidationError, match="at most"):
        report.validate_selection(conn, 1, [], at("08:00"), blank_dates=too_many)


def test_blank_date_column_is_empty_with_every_name(conn, tmp_path):
    attendance.rfid_tap(conn, "CARD0001", at("07:00"))                   # Maria present on the real date
    _sig(tmp_path, MARIA)
    chosen = report.validate_selection(conn, 1, [], at("08:00"), blank_dates=["2026-12-01"])
    students, cells = report.build_matrix(conn, _section(conn), chosen, at("08:00"))
    assert {MARIA, JOSE, ANA, MARK} <= {s["id"] for s in students}            # every name printed
    assert not cells                                                           # nothing filled in
    instructor = dict(conn.execute("SELECT * FROM users WHERE id = 1").fetchone())
    text = _pdf_text(report.build_pdf(conn, _section(conn), instructor, chosen, tmp_path, now=at("08:00")))
    assert b"12/01" in text and b"Santos" in text


def test_report_route_with_blank_dates(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    r = client.get("/instructor/sections/1/report?d=2026-12-01&d=2026-12-03", base_url=PROF_SITE)
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    bad = client.get("/instructor/sections/1/report?d=nope", base_url=PROF_SITE)
    assert bad.status_code == 302
    page = client.get("/instructor/sections/1", base_url=PROF_SITE).data
    assert b"data-add-blank" in page and b'name="d"' in page


def test_report_banner_on_section_page(client):
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/instructor/sections/1?tab=students", base_url=PROF_SITE).data
    assert b"Attendance report</button>" in page and b'id="report-modal"' in page and b'name="s"' in page
    assert b"export.xlsx" not in page                                    # PDF only, no Excel button
    assert b"report-banner" not in page                                  # no banner, just the button
    assert page.index(b"join-code-box") < page.index(b"Attendance report</button>") < page.index(b"tabs-bar")


def test_report_only_own_section(app, client):
    from werkzeug.security import generate_password_hash
    c = connect(app.config["DATABASE"])
    c.execute("INSERT INTO users (role, username, password_hash, first_name, last_name) "
              "VALUES ('instructor', 'other', ?, 'O', 'T')", (generate_password_hash("other1234"),))
    c.commit()
    login(client, "other", "other1234", PROF_SITE)
    assert client.get("/instructor/sections/1/report?s=1", base_url=PROF_SITE).status_code == 404


def test_department_line_is_optional(conn, tmp_path, app, client):
    conn.execute("UPDATE users SET department = 'College of Zzyzx Studies' WHERE id = 1")
    conn.commit()
    attendance.rfid_tap(conn, "CARD0001", at("07:00"))
    chosen = report.validate_selection(conn, 1, [1], at("08:00"))
    instructor = dict(conn.execute("SELECT * FROM users WHERE id = 1").fetchone())
    kw = dict(now=at("08:00"))

    def text(pdf):
        import re, zlib, base64 as b64
        out = b""
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
            raw = m.group(1).strip()
            try:
                out += zlib.decompress(b64.a85decode(raw, adobe=True))
            except Exception:
                try:
                    out += zlib.decompress(raw)
                except Exception:
                    pass
        return out
    with_dept = text(report.build_pdf(conn, _section(conn), instructor, chosen, tmp_path, **kw))
    without = text(report.build_pdf(conn, _section(conn), instructor, chosen, tmp_path, include_department=False, **kw))
    assert b"Zzyzx Studies" in with_dept and b"Zzyzx Studies" not in without and b"College of" in without
    assert b"(College of ) Tj" in with_dept and b"(Zzyzx Studies) Tj" in with_dept   # one line, name underlined
    # the pop-up offers the choice; with no department chosen it is switched off
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/instructor/sections/1", base_url=PROF_SITE).data
    assert b'name="dept"' in page and b"haven't chosen a department" in page
