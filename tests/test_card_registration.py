from app.services import card_registration as cr

from .conftest import JOSE, MARIA, MARK, at


def test_full_registration_flow(conn):
    r = cr.start(conn, MARK, at("07:02"))
    assert r["ok"] and r["registration"]["seconds_left"] == cr.TAP_SECONDS

    tap = cr.handle_tap(conn, "MARKCARD", at("07:02", 10))
    assert tap["kind"] == "registration" and tap["result"] == "detected"
    assert cr.get_status(conn, MARK, at("07:02", 11))["status"] == "detected"

    assert cr.confirm(conn, MARK, at("07:02", 15))["ok"]
    uid = conn.execute("SELECT rfid_uid FROM users WHERE id = ?", (MARK,)).fetchone()[0]
    assert uid == "MARKCARD"

    # the new card now works for attendance
    assert cr.handle_tap(conn, "MARKCARD", at("07:03"))["result"] == "present"


def test_second_student_must_wait(conn):
    conn.execute("UPDATE users SET rfid_uid = NULL WHERE id = ?", (JOSE,))
    conn.commit()
    cr.start(conn, MARK, at("07:02"))
    busy = cr.start(conn, JOSE, at("07:02", 10))
    assert busy["ok"] is False and busy["reason"] == cr.BUSY
    assert "wait until the current session is finished" in busy["message"]


def test_registered_cards_still_check_in_during_registration(conn):
    cr.start(conn, MARK, at("07:02"))
    r = cr.handle_tap(conn, "CARD0001", at("07:02", 5))
    assert r["kind"] == "attendance" and r["result"] == "present"
    assert cr.get_status(conn, MARK, at("07:02", 6))["status"] == "waiting"


def test_tapping_a_card_that_belongs_to_someone_tells_the_registering_student(conn):
    cr.start(conn, MARK, at("07:02"))
    assert cr.get_status(conn, MARK, at("07:02", 1))["message"] is None
    r = cr.handle_tap(conn, "CARD0001", at("07:02", 5))          # another student's card
    assert r["kind"] == "attendance"                              # still that student's check-in
    status = cr.get_status(conn, MARK, at("07:02", 6))
    assert status["status"] == "waiting"                          # can still tap the right card
    assert "CARD0001 is already registered to another account" in status["message"]
    # LCD: "ALREADY REGISTERED" + the owner's name and check-in result
    from hardware.lcd_display import COLS, screen_for_tap
    assert r["already_registered"] is True
    lines = screen_for_tap(r, cr.lcd_waiting_info(conn, at("07:02", 5)))
    assert lines[0].strip() == "ALREADY REGISTERED"
    assert lines[1].strip() == "Santos, Maria"
    assert lines[2].strip() == "Checked in: PRESENT"
    assert lines[3].strip() == "Tap a different card"
    assert all(len(line) <= COLS for line in lines)
    # tapping their own new card afterwards still works
    assert cr.handle_tap(conn, "MARKCARD", at("07:02", 10))["result"] == "detected"


def test_normal_taps_keep_the_normal_lcd_screen(conn):
    from hardware.lcd_display import screen_for_tap
    r = cr.handle_tap(conn, "CARD0001", at("07:02"))             # nobody is registering
    assert r["already_registered"] is False
    assert screen_for_tap(r, None)[0].strip() == "CHECKED IN: PRESENT"


def test_registration_page_receives_the_message(app, client):
    from .test_auth import give_signature, login
    from app.db import connect
    give_signature(app, MARK)
    login(client, "2026-0004", "student123")                     # Mark: no card yet
    db = connect(app.config["DATABASE"])
    cr.start(db, MARK)
    cr.handle_tap(db, "CARD0001")                                 # Maria's card
    db.close()
    reg = client.get("/student/card/status").get_json()["registration"]
    assert reg["status"] == "waiting" and "already registered" in reg["message"]


def test_timeout_releases_the_reader(conn):
    conn.execute("UPDATE users SET rfid_uid = NULL WHERE id = ?", (JOSE,))
    conn.commit()
    cr.start(conn, MARK, at("07:02"))
    later = at("07:02", cr.TAP_SECONDS + 1)
    assert cr.get_status(conn, MARK, later)["status"] == "expired"
    assert cr.start(conn, JOSE, later)["ok"]


def test_retry_and_cancel(conn):
    cr.start(conn, MARK, at("07:02"))
    cr.handle_tap(conn, "WRONGCARD", at("07:02", 5))
    cr.retry(conn, MARK, at("07:02", 8))
    assert cr.get_status(conn, MARK, at("07:02", 9))["status"] == "waiting"
    cr.cancel(conn, MARK, at("07:02", 10))
    assert cr.get_status(conn, MARK, at("07:02", 11))["status"] == "cancelled"


def test_student_with_card_cannot_register_again(conn):
    assert cr.start(conn, MARIA, at("07:02"))["reason"] == cr.HAS_CARD


def test_unknown_card_without_registration(conn):
    r = cr.handle_tap(conn, "STRANGER", at("07:02"))
    assert r["kind"] == "attendance" and r["result"] == "unknown_card"
