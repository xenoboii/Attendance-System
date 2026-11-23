from app.services import attendance_service as svc

from .conftest import ANA, JOSE, MARIA, at


def test_rfid_too_early(conn):
    assert svc.rfid_tap(conn, "CARD0001", at("06:50"))["result"] == "no_open_session"


def test_rfid_present_then_duplicate(conn):
    assert svc.rfid_tap(conn, "CARD0001", at("06:58"))["result"] == "present"
    again = svc.rfid_tap(conn, "CARD0001", at("07:05"))
    assert again["result"] == "duplicate"
    assert again["record"]["status"] == "present"


def test_rfid_late_after_grace(conn):
    assert svc.rfid_tap(conn, "CARD0002", at("07:16"))["result"] == "late"


def test_rfid_closed_after_window(conn):
    assert svc.rfid_tap(conn, "CARD0003", at("07:31"))["result"] == "no_open_session"


def test_unknown_card(conn):
    r = svc.rfid_tap(conn, "NOTREAL", at("07:05"))
    assert r["result"] == "unknown_card"
    assert conn.execute("SELECT result FROM rfid_scans").fetchone()["result"] == "unknown_card"


def test_web_and_rfid_share_one_record(conn):
    svc.rfid_tap(conn, "CARD0001", at("07:00"))
    assert svc.web_check_in(conn, MARIA, now=at("07:01"))["result"] == "duplicate"
    n = conn.execute("SELECT COUNT(*) FROM attendance WHERE student_id = ? AND session_id = 1",
                     (MARIA,)).fetchone()[0]
    assert n == 1


def test_button_status_flow(conn):
    assert svc.get_button_status(conn, JOSE, at("06:50"))["variant"] == "closed"
    assert svc.get_button_status(conn, JOSE, at("07:00"))["variant"] == "present"
    assert svc.get_button_status(conn, JOSE, at("07:20"))["variant"] == "late"
    svc.web_check_in(conn, JOSE, now=at("07:20"))
    done = svc.get_button_status(conn, JOSE, at("07:21"))
    assert done["enabled"] is False and done["variant"] == "done"
    assert "already marked LATE" in done["message"]


def test_manual_override_after_window(conn):
    conn.execute("UPDATE class_sessions SET checkin_mode='manual', manual_state='present' WHERE id=1")
    conn.commit()
    assert svc.rfid_tap(conn, "CARD0003", at("08:00"))["result"] == "present"


def test_web_checkin_code_required_when_set(conn):
    conn.execute("UPDATE class_sessions SET checkin_code='4821' WHERE id=1")
    conn.commit()
    assert svc.web_check_in(conn, ANA, code="0000", now=at("07:02"))["result"] == "invalid_code"
    assert svc.web_check_in(conn, ANA, code="4821", now=at("07:02"))["result"] == "present"
    # RFID never needs the code
    assert svc.rfid_tap(conn, "CARD0001", at("07:02"))["result"] == "present"
