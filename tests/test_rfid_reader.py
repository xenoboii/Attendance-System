"""Result countdown timing and return to the appropriate ready screen."""
from unittest.mock import Mock, call

import pytest

from hardware import rfid_reader
from hardware.lcd_display import idle_screen, registration_waiting_screen


@pytest.mark.parametrize("registering", [False, True])
def test_result_counts_down_then_returns_to_ready_screen(monkeypatch, registering):
    lcd = Mock()
    sleep = Mock()
    conn = object()
    reg = {"status": "detected", "first_name": "Mark", "last_name": "Tan",
           "seconds_left": 40} if registering else None
    monkeypatch.setattr(rfid_reader.Config, "LCD_RESULT_SECONDS", 2)
    monkeypatch.setattr(rfid_reader.time, "sleep", sleep)
    monkeypatch.setattr(rfid_reader.card_registration, "lcd_waiting_info", lambda db: reg)
    monkeypatch.setattr(rfid_reader.battery, "get", lambda db: None)
    ready = registration_waiting_screen(reg) if reg else idle_screen()
    monkeypatch.setattr(rfid_reader, "idle_screen", lambda **kwargs: ready)

    lines = ["CHECKED IN: PRESENT", "Tan, Mark", "CPE101 4A", "Time: 07:00 AM"]
    rfid_reader.show_result_countdown(lcd, conn, lines)

    screens = [args.args[0] for args in lcd.show.call_args_list]
    assert len(screens) == 3
    assert screens[0][3].endswith(" 2s")
    assert screens[1][3].endswith(" 1s")
    assert screens[2] == ready
    assert sleep.call_args_list == [call(1), call(1)]
