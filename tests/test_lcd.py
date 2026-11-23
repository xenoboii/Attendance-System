"""Every LCD screen must be exactly 4 lines of exactly 20 characters."""
from app.services import attendance_service as svc
from app.services import card_registration as cr
from hardware.lcd_display import (countdown_screen, idle_screen, registration_done_screen,
                                  registration_waiting_screen, result_screen)

from .conftest import MARK, at


def assert_fits(lines):
    assert len(lines) == 4
    assert all(len(line) == 20 for line in lines), lines


def test_idle():
    assert_fits(idle_screen(at("07:00")))


def test_all_tap_results(conn):
    conn.execute("UPDATE users SET first_name='Christopherson', last_name='Dela Cruz-Villanueva' WHERE id=2")
    conn.commit()
    for uid, when in [("CARD0001", "07:00"), ("CARD0001", "07:01"), ("CARD0002", "07:20"),
                      ("NOPE", "07:20"), ("CARD0003", "07:40")]:
        assert_fits(result_screen(svc.rfid_tap(conn, uid, at(when))))


def test_unknown_card_message(conn):
    lines = result_screen(svc.rfid_tap(conn, "NOPE", at("07:00")))
    assert "CARD NOT REGISTERED" in lines[0]
    assert "enroll your" in lines[1]


def test_countdown_preserves_result_and_time(conn):
    lines = result_screen(svc.rfid_tap(conn, "CARD0001", at("07:00")))
    for seconds in (2, 1):
        display = countdown_screen(lines, seconds)
        assert_fits(display)
        assert display[:3] == lines[:3]
        assert "Time: 07:00 AM" in display[3]
        assert display[3].endswith(f" {seconds}s")
    assert not lines[3].endswith(" 1s")  # the original screen can be reused


def test_registration_screens(conn):
    cr.start(conn, MARK, at("07:02"))
    assert_fits(registration_waiting_screen(cr.lcd_waiting_info(conn, at("07:02", 5))))
    cr.handle_tap(conn, "NEWCARD", at("07:02", 6))
    assert_fits(registration_waiting_screen(cr.lcd_waiting_info(conn, at("07:02", 7))))
    assert_fits(registration_done_screen({"first_name": "Mark", "last_name": "Tan"}, True))
