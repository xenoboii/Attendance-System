"""Shutdown button with confirmation: hold 3 s, then press again within 10 s.
No second press = cancelled. The LCD says what is happening at every step."""
import subprocess
from unittest.mock import Mock

import pytest

from hardware import power_screen, rfid_reader
from hardware.lcd_display import (COLS, safe_off_screen, shutdown_confirm_screen,
                                  shutdown_holding_screen, shutting_down_screen)
from hardware.rfid_reader import ShutdownButton


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def btn():
    clock = Clock()
    return ShutdownButton(None, clock=clock), clock      # no GPIO: events are simulated


def test_press_shows_the_hold_countdown_right_away(btn):
    b, clock = btn
    assert b.poll() == "idle"
    b._pressed()                       # the moment it's pressed: "Keep holding... 3s"
    assert b.poll() == "holding" and b.seconds_left() == 3
    clock.t += 1.2
    assert b.seconds_left() == 2
    clock.t += 1
    assert b.seconds_left() == 1


def test_letting_go_early_cancels(btn):
    b, clock = btn
    b._pressed()
    clock.t += 1.5
    b._released()                      # let go before 3 s
    assert b.poll() == "cancelled"
    assert b.poll() == "idle"


def test_hold_then_press_confirms(btn):
    b, clock = btn
    b._pressed(); b._held()            # held 3 s
    b._released()                      # letting go after the hold doesn't cancel
    assert b.poll() == "confirm" and b.seconds_left() == 10
    clock.t += 4
    assert b.seconds_left() == 6
    b._pressed()                       # second press within 10 s
    assert b.poll() == "shutdown"
    b._released()
    assert b.poll() == "shutdown"


def test_no_second_press_cancels_back_to_idle(btn):
    b, clock = btn
    b._held()
    clock.t += 10
    assert b.poll() == "cancelled"     # reported once ...
    assert b.poll() == "idle"          # ... then normal again
    b._pressed(); b._released()        # a later quick press only flashes the countdown
    assert b.poll() == "cancelled" and b.poll() == "idle"


def test_press_after_the_10_seconds_does_not_shut_down(btn):
    b, clock = btn
    b._held()
    clock.t += 11
    assert b.poll() == "cancelled"     # the main loop notices within a fraction of a second
    b._pressed()                       # a late press just starts a new hold countdown
    assert b.poll() == "holding"


def test_holding_again_while_confirming_does_not_restart_the_timer(btn):
    b, clock = btn
    b._held()
    clock.t += 8
    b._held()
    assert b.seconds_left() == 2


def test_polled_button_level_drives_the_whole_flow(btn):
    """The pin is read by the main loop (no callbacks): a sequence of up/down readings."""
    b, clock = btn
    b.update(False)
    assert b.poll() == "idle"
    b.update(True)                     # pressed
    assert b.poll() == "holding"
    clock.t += 2.9
    b.update(True)                     # still held, not 3 s yet
    assert b.poll() == "holding"
    clock.t += 0.2
    b.update(True)                     # held 3 s
    assert b.poll() == "confirm"
    b.update(False)                    # let go after the hold: stays in confirm
    assert b.poll() == "confirm"
    clock.t += 2
    b.update(True)                     # second press
    assert b.poll() == "shutdown"


def test_polled_short_press_cancels(btn):
    b, clock = btn
    b.update(True)
    clock.t += 1
    b.update(False)
    assert b.poll() == "cancelled" and b.poll() == "idle"


def test_poll_reads_the_pin_itself(btn):
    b, clock = btn
    level = {"down": False}
    b._read = lambda: level["down"]
    assert b.poll() == "idle"
    level["down"] = True
    assert b.poll() == "holding"
    clock.t += 3
    assert b.poll() == "confirm"


def test_a_broken_pin_read_never_stops_the_reader(btn):
    b, _ = btn
    def boom():
        raise RuntimeError("gpio gone")
    b._read = boom
    assert b.poll() == "idle" and b._read is None


# ---------------------------------------------------------------- low battery beep
def _watch(buzzer):
    w = rfid_reader.BatteryWatch.__new__(rfid_reader.BatteryWatch)
    w.buzzer, w.last_beep = buzzer, 0.0
    return w


def test_low_battery_beeps_every_2_minutes_then_every_30_seconds(monkeypatch):
    now = {"t": 10_000.0}
    monkeypatch.setattr(rfid_reader.time, "time", lambda: now["t"])
    buzzer = Mock()
    w = _watch(buzzer)
    w.warn(50, False)
    assert buzzer.beep.call_count == 0                     # fine
    w.warn(18, False)
    assert buzzer.beep.call_args.args == ("low_battery",) and buzzer.beep.call_count == 1
    now["t"] += 60; w.warn(17, False)
    assert buzzer.beep.call_count == 1                     # not yet: every 2 minutes at <=20%
    now["t"] += 61; w.warn(16, False)
    assert buzzer.beep.call_count == 2
    now["t"] += 31; w.warn(9, False)
    assert buzzer.beep.call_count == 3                     # <=10%: every 30 s
    now["t"] += 31; w.warn(8, False)
    assert buzzer.beep.call_count == 4


def test_charging_stops_the_beeping(monkeypatch):
    monkeypatch.setattr(rfid_reader.time, "time", lambda: 10_000.0)
    buzzer = Mock()
    w = _watch(buzzer)
    w.warn(9, True)
    assert buzzer.beep.call_count == 0


def test_loading_screen_at_power_on(monkeypatch):
    from hardware.lcd_display import loading_screen
    lcd = Mock()
    monkeypatch.setattr(power_screen, "_wait_for_i2c", lambda: None)
    monkeypatch.setattr(power_screen, "Lcd", lambda address: lcd)
    monkeypatch.setattr(power_screen, "shutdown_kind", Mock())
    power_screen.main(["boot"])
    lcd.show.assert_called_once_with(loading_screen())
    power_screen.shutdown_kind.assert_not_called()
    assert "LOADING" in loading_screen()[0] and all(len(l) <= 20 for l in loading_screen())


def test_screens_fit_the_lcd():
    for lines in (shutdown_holding_screen(3), shutdown_confirm_screen(10), shutting_down_screen("off"),
                  shutting_down_screen("reboot"), safe_off_screen()):
        assert len(lines) == 4 and all(len(line) <= 20 for line in lines)
    assert "again to confirm" in "".join(shutdown_confirm_screen(7))
    assert "Cancels in 7s" in "".join(shutdown_confirm_screen(7))


def test_confirmed_shutdown_shows_the_screen_then_shuts_down(monkeypatch):
    lcd, run = Mock(), Mock()
    monkeypatch.setattr(subprocess, "run", run)
    rfid_reader.shut_down_now(lcd)
    lcd.show.assert_called_once_with(shutting_down_screen("off"))
    assert run.call_args.args[0] == ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"]


# ---------------------------------------------------------------- "Safe to switch OFF"
def _systemctl(state, jobs=""):
    def fake_run(cmd, **kwargs):
        out = state if "is-system-running" in cmd else jobs
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")
    return fake_run


@pytest.mark.parametrize("state, jobs, expected", [
    ("running", "", None),                                   # only the service restarted
    ("stopping", "1 poweroff.target start waiting", "off"),
    ("stopping", "", "off"),
    ("stopping", "1 reboot.target start waiting", "reboot"),
])
def test_shutdown_kind(monkeypatch, state, jobs, expected):
    monkeypatch.setattr(power_screen.subprocess, "run", _systemctl(state, jobs))
    assert power_screen.shutdown_kind() == expected


@pytest.mark.parametrize("kind, shown", [("off", safe_off_screen()), (None, None),
                                         ("reboot", shutting_down_screen("reboot"))])
def test_final_screen_only_when_shutting_down(monkeypatch, kind, shown):
    lcd = Mock()
    monkeypatch.setattr(power_screen, "shutdown_kind", lambda: kind)
    monkeypatch.setattr(power_screen, "Lcd", lambda address: lcd)
    power_screen.main()
    if shown is None:
        lcd.show.assert_not_called()
    else:
        lcd.show.assert_called_once_with(shown)


def test_button_setting(monkeypatch):
    import importlib

    import config
    monkeypatch.delenv("SHUTDOWN_BUTTON_GPIO", raising=False)
    assert importlib.reload(config).Config.SHUTDOWN_BUTTON_GPIO == 26      # default pin 37
    monkeypatch.setenv("SHUTDOWN_BUTTON_GPIO", "")
    assert importlib.reload(config).Config.SHUTDOWN_BUTTON_GPIO is None    # no button
    monkeypatch.delenv("SHUTDOWN_BUTTON_GPIO")
    importlib.reload(config)
