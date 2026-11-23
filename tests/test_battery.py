"""Battery percentage from the 18650 UPS board."""
from datetime import datetime, timedelta

import pytest

from app.services import battery
from hardware.lcd_display import (BATTERY_GLYPHS, CHARGING_GLYPH, CUSTOM_CHARS, battery_line,
                                  idle_screen)

from .test_auth import PROF_SITE, login

NOW = datetime(2026, 11, 16, 8, 0, 0)


def test_no_reading_means_hidden(conn):
    assert battery.get(conn, NOW) is None


def test_save_and_get_levels(conn):
    battery.save(conn, 82, charging=False, voltage=3.98, now=NOW)
    b = battery.get(conn, NOW + timedelta(seconds=30))
    assert b["percent"] == 82 and b["level"] == "ok" and b["icon"] == "battery-full" and b["voltage"] == 3.98
    battery.save(conn, 15, charging=False, now=NOW)
    assert battery.get(conn, NOW)["level"] == "low"
    battery.save(conn, 8, charging=False, now=NOW)
    assert battery.get(conn, NOW)["level"] == "critical"
    battery.save(conn, 15, charging=True, now=NOW)
    b = battery.get(conn, NOW)
    assert b["level"] == "ok" and b["icon"] == "battery-charging"     # plugged in: no alarm


def test_old_reading_is_ignored(conn):
    battery.save(conn, 50, now=NOW)
    assert battery.get(conn, NOW + timedelta(minutes=10)) is None    # hardware process stopped


def test_shutdown_needs_two_low_readings_and_no_charging():
    assert not battery.should_shut_down([30, 4], charging=False)      # one bad reading isn't enough
    assert battery.should_shut_down([5, 4], charging=False)
    assert not battery.should_shut_down([5, 4], charging=True)
    assert not battery.should_shut_down([4], charging=False)


def test_decode_max17040():
    # VCELL 0xC80 * 1.25 mV = 4.0 V ; SOC 82 + 128/256 = 82.5 %
    percent, volts = battery.decode_max17040(bytes([0xC8, 0x00]), bytes([82, 128]))
    assert round(volts, 3) == 4.0 and percent == 82.5


def test_decode_ina219():
    raw_bus = (int(3.8 / 0.004) << 3).to_bytes(2, "big")                # 3.8 V
    pct, volts, charging = battery.decode_ina219(raw_bus, (200).to_bytes(2, "big", signed=True))
    assert round(volts, 2) == 3.8 and 45 <= pct <= 55 and charging
    _, _, charging = battery.decode_ina219(raw_bus, (-300).to_bytes(2, "big", signed=True))
    assert not charging
    pct2, _, _ = battery.decode_ina219((int(7.6 / 0.004) << 3).to_bytes(2, "big"), bytes(2), cells_in_series=2)
    assert abs(pct2 - pct) < 1.5                                         # 2S board, same per-cell level


def _shunt(n):
    return n.to_bytes(2, "big", signed=True)


def test_full_on_the_charger_still_counts_as_plugged_in():
    raw_bus = (int(4.18 / 0.004) << 3).to_bytes(2, "big")
    assert battery.decode_ina219(raw_bus, _shunt(3))[2]                  # ~0 current: charging stopped, still plugged
    assert not battery.decode_ina219(raw_bus, _shunt(-900))[2]           # Pi drawing from the cells: on battery


def test_ups_hat_d_reads_charging_as_negative():
    """Measured on the Waveshare UPS HAT (D), charger plugged in: shunt about -1800."""
    raw_bus = (int(3.92 / 0.004) << 3).to_bytes(2, "big")
    assert battery.decode_ina219(raw_bus, _shunt(-1800), invert=True)[2]           # plugged in
    assert not battery.decode_ina219(raw_bus, _shunt(1500), invert=True)[2]        # on battery


def test_invert_is_automatic_for_the_ups_hat_d_address(monkeypatch):
    import importlib

    import config
    monkeypatch.setenv("BATTERY_I2C_ADDRESS", "0x43")
    monkeypatch.delenv("BATTERY_CURRENT_INVERT", raising=False)
    assert importlib.reload(config).Config.BATTERY_CURRENT_INVERT is True
    monkeypatch.setenv("BATTERY_CURRENT_INVERT", "0")                    # can be overridden
    assert importlib.reload(config).Config.BATTERY_CURRENT_INVERT is False
    monkeypatch.setenv("BATTERY_I2C_ADDRESS", "0x42")
    monkeypatch.delenv("BATTERY_CURRENT_INVERT")
    assert importlib.reload(config).Config.BATTERY_CURRENT_INVERT is False
    monkeypatch.delenv("BATTERY_I2C_ADDRESS")
    importlib.reload(config)


def test_lcd_line():
    """Line 3 only warns when nearly empty and not charging; the level is the icon on line 1."""
    assert battery_line(None) == ""
    assert battery_line({"percent": 82, "charging": True, "level": "ok"}) == ""
    assert battery_line({"percent": 82, "charging": False, "level": "ok"}) == ""
    assert battery_line({"percent": 7, "charging": False, "level": "critical"}) == "LOW BATTERY 7%"
    assert battery_line({"percent": 7, "charging": True, "level": "critical"}) == ""


@pytest.mark.parametrize("percent, bars", [(100, 4), (88, 4), (70, 3), (50, 2), (20, 1), (5, 0)])
def test_battery_icon_in_the_last_column_of_line_1(percent, bars):
    screen = idle_screen(NOW, {"percent": percent, "charging": False, "level": "ok"})
    assert screen[0] == " ATTENDANCE SYSTEM " + BATTERY_GLYPHS[bars]
    assert all(len(line) == 20 for line in screen) and screen[2].strip() == ""


def test_charging_shows_a_bolt_and_no_monitor_shows_nothing():
    assert idle_screen(NOW, {"percent": 40, "charging": True, "level": "ok"})[0][-1] == CHARGING_GLYPH
    assert idle_screen(NOW, None)[0] == " ATTENDANCE SYSTEM  "


def test_custom_characters_are_valid_5x8_bitmaps():
    assert len(CUSTOM_CHARS) == 6                                  # fits the LCD's 8 slots
    for bitmap in CUSTOM_CHARS:
        assert len(bitmap) == 8 and all(0 <= row < 32 for row in bitmap)
    # full battery: every inside row filled; empty: only the frame
    assert CUSTOM_CHARS[4][2:6] == [0b11111] * 4 and CUSTOM_CHARS[0][2:6] == [0b10001] * 4


def test_real_lcd_gets_the_drawn_icons(monkeypatch):
    from unittest.mock import Mock
    from hardware.lcd_display import Lcd
    lcd = Lcd.__new__(Lcd)
    lcd._lcd = Mock()
    lcd.show(idle_screen(NOW, {"percent": 100, "charging": False, "level": "ok"}))
    first_line = lcd._lcd.write_string.call_args_list[0].args[0]
    assert first_line == " ATTENDANCE SYSTEM \x04"                # CGRAM slot 4 = full battery


def test_instructor_sees_badge_and_warning(app, client):
    from app.db import connect
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/instructor/", base_url=PROF_SITE).data
    assert b"data-battery-url" in page                                  # badge there, hidden (no reading)
    c = connect(app.config["DATABASE"]); battery.save(c, 12, charging=False); c.close()
    page = client.get("/instructor/", base_url=PROF_SITE).data.decode()
    assert "Device battery" in page and "12%" in page and "Device battery is low" in page
    j = client.get("/instructor/device", base_url=PROF_SITE).get_json()
    assert j["battery"]["percent"] == 12 and j["battery"]["level"] == "low"
    assert "Attendance device" in client.get("/settings/", base_url=PROF_SITE).data.decode()


def test_students_dont_see_battery(app, client):
    from app.db import connect
    from .test_auth import give_signature
    c = connect(app.config["DATABASE"]); battery.save(c, 12); c.close()
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    assert b"Device battery" not in client.get("/student/").data
