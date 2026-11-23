"""Check-out: instructor requires it, opens it near the end of class; checked in but not
checked out = absent when the session ends."""
from datetime import timedelta

from app.services import attendance_service as att
from app.services import sessions
from hardware.lcd_display import COLS, result_screen

from .conftest import ANA, JOSE, MARIA, at

PROF = 1
SID = 1          # today's IT312 BSIT 3A session (starts 07:00, auto)


def _status(conn, student_id):
    row = conn.execute("SELECT status, note, checked_out_at FROM attendance WHERE session_id = ? AND student_id = ?",
                       (SID, student_id)).fetchone()
    return dict(row) if row else None


def _session(conn):
    return dict(conn.execute("SELECT * FROM class_sessions WHERE id = ?", (SID,)).fetchone())


def test_full_checkout_flow(conn):
    sessions.set_require_checkout(conn, PROF, SID, True)
    assert att.rfid_tap(conn, "CARD0001", at("07:01"))["result"] == "present"       # Maria
    assert att.rfid_tap(conn, "CARD0002", at("07:02"))["result"] == "present"       # Jose
    # while check-in is open a second tap is still just a duplicate, NOT a check-out
    assert att.rfid_tap(conn, "CARD0001", at("07:03"))["result"] == "duplicate"

    sessions.open_checkout(conn, PROF, SID, at("08:30"))
    assert sessions.describe(_session(conn), at("08:30"))["phase"] == "checkout"

    r = att.rfid_tap(conn, "CARD0001", at("08:31"))
    assert r["result"] == "checked_out" and r["record"]["checked_out_at"].endswith("08:31:00")
    assert att.rfid_tap(conn, "CARD0001", at("08:32"))["result"] == "already_checked_out"
    # Ana never checked in: nothing to check out of (and check-in is closed now)
    assert att.rfid_tap(conn, "CARD0003", at("08:33"))["result"] == "not_checked_in"

    sessions.end_session(conn, PROF, SID, at("08:40"))
    assert _status(conn, MARIA)["status"] == "present"                     # checked out
    jose = _status(conn, JOSE)
    assert jose["status"] == "absent" and jose["note"] == sessions.NO_CHECKOUT_NOTE
    roster = {r["id"]: r for r in sessions.roster(conn, _session(conn), at("08:41"))}
    assert roster[ANA]["status"] == "absent"                               # never checked in
    assert roster[MARIA]["checked_out_at"]

    # the instructor can still overrule it by hand
    sessions.mark(conn, PROF, SID, JOSE, "present", at("08:45"))
    assert _status(conn, JOSE)["status"] == "present" and _status(conn, JOSE)["note"] is None


def test_not_required_means_no_checkout_absences(conn):
    att.rfid_tap(conn, "CARD0001", at("07:01"))
    sessions.end_session(conn, PROF, SID, at("08:40"))
    assert _status(conn, MARIA)["status"] == "present"


def test_required_but_never_opened_marks_nobody_absent(conn):
    """Nobody could check out, so nobody is punished for it."""
    sessions.set_require_checkout(conn, PROF, SID, True)
    att.rfid_tap(conn, "CARD0001", at("07:01"))
    sessions.end_session(conn, PROF, SID, at("08:40"))
    assert _status(conn, MARIA)["status"] == "present"


def test_back_to_checkin_closes_checkout(conn):
    sessions.set_require_checkout(conn, PROF, SID, True)
    att.rfid_tap(conn, "CARD0001", at("07:01"))
    sessions.open_checkout(conn, PROF, SID, at("08:30"))
    sessions.set_state(conn, PROF, SID, "late", at("08:31"))
    assert _session(conn)["checkout_open_at"] is None
    assert att.rfid_tap(conn, "CARD0001", at("08:32"))["result"] == "duplicate"      # check-in again


def test_rule_applies_once_the_day_has_passed_without_end_session(conn):
    sessions.set_require_checkout(conn, PROF, SID, True)
    att.rfid_tap(conn, "CARD0001", at("07:01"))
    sessions.open_checkout(conn, PROF, SID, at("08:30"))
    sessions.ensure_sessions_for_date(conn, (at("08:00") + timedelta(days=1)).date())
    assert _status(conn, MARIA)["status"] == "absent"
    # only once: a later manual fix isn't overwritten by the next run
    sessions.mark(conn, PROF, SID, MARIA, "present")
    sessions.ensure_sessions_for_date(conn, (at("08:00") + timedelta(days=2)).date())
    assert _status(conn, MARIA)["status"] == "present"


def test_web_button_switches_to_check_out(conn):
    sessions.set_require_checkout(conn, PROF, SID, True)
    att.web_check_in(conn, MARIA, now=at("07:01"))
    b = att.get_button_status(conn, MARIA, at("07:10"))           # check-in still open
    assert b["variant"] == "done" and "Check-out is required" in b["message"]

    sessions.open_checkout(conn, PROF, SID, at("08:30"))
    b = att.get_button_status(conn, MARIA, at("08:31"))
    assert b["enabled"] and b["variant"] == "checkout" and b["action"] == "checkout"
    assert att.web_check_out(conn, MARIA, at("08:32"))["result"] == "checked_out"
    b = att.get_button_status(conn, MARIA, at("08:33"))
    assert not b["enabled"] and b["label"] == "Checked out" and b["variant"] == "out"


def test_lcd_screens_fit():
    student = {"first_name": "Maria", "last_name": "Santos"}
    session = {"subject_code": "IT312", "section_name": "BSIT 3A"}
    rec = {"checked_out_at": "2026-11-16 08:31:00"}
    for res in ("checked_out", "already_checked_out", "not_checked_in"):
        lines = result_screen({"result": res, "student": student, "session": session, "record": rec})
        assert len(lines) == 4 and all(len(l) <= COLS for l in lines)
        assert "ERROR" not in lines[0]
    assert result_screen({"result": "checked_out", "student": student, "session": session,
                          "record": rec})[0].strip() == "CHECKED OUT"


# ---------------------------------------------------------------- web pages
def _login_prof(client):
    from .test_auth import PROF_SITE, login
    login(client, "prof", "prof123", site=PROF_SITE)
    return PROF_SITE


def test_instructor_controls(app, client):
    site = _login_prof(client)
    from app.db import connect
    db = connect(app.config["DATABASE"])
    sid = db.execute("SELECT id FROM class_sessions WHERE type <> 'async' AND deleted_at IS NULL ORDER BY id LIMIT 1").fetchone()[0]
    db.close()
    html = client.get(f"/instructor/sessions/{sid}/live?part=controls", base_url=site).get_data(as_text=True)
    assert "Open check-out" not in html
    # the Required / not required dropdown sits next to the check-in mode dropdown
    mode = client.get(f"/instructor/sessions/{sid}/live?part=mode", base_url=site).get_data(as_text=True)
    assert "No check-out" in mode and "Check-out required" in mode and 'name="mode"' in mode

    client.post(f"/instructor/sessions/{sid}/checkout", data={"require": "yes"}, base_url=site)
    html = client.get(f"/instructor/sessions/{sid}/live?part=controls", base_url=site).get_data(as_text=True)
    assert "Open check-out" in html

    client.post(f"/instructor/sessions/{sid}/checkout", data={"open": "1"}, base_url=site)
    html = client.get(f"/instructor/sessions/{sid}/live?part=controls", base_url=site).get_data(as_text=True)
    # the control card shows the check-out progress; the "Check-out open" status is shown once, at the top
    assert "checked out" in html and "Back to check-in" in html
    top = client.get(f"/instructor/sessions/{sid}/live", base_url=site).get_data(as_text=True)
    assert "Check-out open" in top
    # every page that labels a session's phase must know the new "checkout" phase
    from app.db import connect as _c
    db = _c(app.config["DATABASE"])
    section_id = db.execute("SELECT section_id FROM class_sessions WHERE id = ?", (sid,)).fetchone()[0]
    db.close()
    for url in (f"/instructor/sessions/{sid}", f"/instructor/sessions/{sid}/live",
                f"/instructor/sections/{section_id}?tab=sessions", "/instructor/"):
        assert client.get(url, base_url=site).status_code == 200, url


def test_student_checkout_route(app, client):
    from app.db import connect
    from .test_auth import give_signature, login
    db = connect(app.config["DATABASE"])
    sid = db.execute("""SELECT cs.id FROM class_sessions cs JOIN enrollments e ON e.section_id = cs.section_id
                        WHERE e.student_id = ? AND cs.type <> 'async' AND cs.deleted_at IS NULL
                        ORDER BY cs.id LIMIT 1""", (MARIA,)).fetchone()[0]
    db.execute("""INSERT INTO attendance (session_id, student_id, status, method) VALUES (?, ?, 'present', 'rfid')
                  ON CONFLICT(session_id, student_id) DO UPDATE SET status = 'present', deleted_at = NULL""",
               (sid, MARIA))
    db.commit()
    sessions.open_checkout(db, PROF, sid)
    db.close()
    give_signature(app, MARIA)
    login(client, "2026-0001", "student123")
    assert client.get("/student/checkin/status").get_json()["action"] == "checkout"
    assert b"Check out before you leave" in client.get("/student/").data
    client.post("/student/checkout")
    assert b"checked out" in client.get("/student/").data
    db = connect(app.config["DATABASE"])
    assert db.execute("SELECT checked_out_at FROM attendance WHERE session_id = ? AND student_id = ?",
                      (sid, MARIA)).fetchone()[0]
    db.close()
