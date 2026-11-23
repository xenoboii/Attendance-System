from datetime import datetime

import pytest

from app.services.checkin_state import get_checkin_state

AUTO = {"checkin_mode": "auto", "manual_state": "disabled",
        "session_date": "2026-11-16", "start_time": "07:00",
        "open_before_minutes": 5, "grace_minutes": 15, "close_after_minutes": 30}


def t(hhmm):
    return datetime.strptime(f"2026-11-16 {hhmm}", "%Y-%m-%d %H:%M")


@pytest.mark.parametrize("time, expected", [
    ("06:54", "disabled"),
    ("06:55", "present"),
    ("07:00", "present"),
    ("07:14", "present"),
    ("07:15", "late"),
    ("07:29", "late"),
    ("07:30", "disabled"),
])
def test_auto_mode_window(time, expected):
    assert get_checkin_state(AUTO, t(time)) == expected


def test_custom_grace_period():
    s = {**AUTO, "grace_minutes": 5}
    assert get_checkin_state(s, t("07:04")) == "present"
    assert get_checkin_state(s, t("07:05")) == "late"


@pytest.mark.parametrize("state", ["disabled", "present", "late"])
def test_manual_mode_ignores_time(state):
    s = {**AUTO, "checkin_mode": "manual", "manual_state": state}
    assert get_checkin_state(s, t("23:59")) == state
