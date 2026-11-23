"""
Check-in state logic shared by the RFID reader and the web check-in button.

    state = get_checkin_state(session, now)
        -> "disabled" | "present" | "late"

Auto mode example (start 07:00, open_before 5, grace 15, close_after 30):
    before 06:55        disabled
    06:55 - 07:14:59    present
    07:15 - 07:29:59    late
    07:30 onward        disabled
"""
from datetime import datetime, timedelta

DISABLED, PRESENT, LATE = "disabled", "present", "late"


def get_checkin_state(session: dict, now: datetime | None = None) -> str:
    now = now or datetime.now()

    if session.get("ended_at"):          # instructor pressed "End session"
        return DISABLED
    if session["checkin_mode"] == "manual":
        return session["manual_state"]

    start = datetime.strptime(
        f'{session["session_date"]} {session["start_time"]}', "%Y-%m-%d %H:%M"
    )
    opens_at = start - timedelta(minutes=session["open_before_minutes"])
    late_at = start + timedelta(minutes=session["grace_minutes"])
    closes_at = start + timedelta(minutes=session["close_after_minutes"])

    if now < opens_at or now >= closes_at:
        return DISABLED
    if now < late_at:
        return PRESENT
    return LATE


def get_checkin_window(session: dict) -> dict | None:
    """Times to show on the instructor/student UI (auto mode only)."""
    if session["checkin_mode"] != "auto":
        return None
    start = datetime.strptime(
        f'{session["session_date"]} {session["start_time"]}', "%Y-%m-%d %H:%M"
    )
    return {
        "opens_at": start - timedelta(minutes=session["open_before_minutes"]),
        "late_at": start + timedelta(minutes=session["grace_minutes"]),
        "closes_at": start + timedelta(minutes=session["close_after_minutes"]),
    }


if __name__ == "__main__":
    s = {
        "checkin_mode": "auto", "manual_state": "disabled",
        "session_date": "2026-11-16", "start_time": "07:00",
        "open_before_minutes": 5, "grace_minutes": 15, "close_after_minutes": 30,
    }
    for t in ["06:54", "06:55", "07:00", "07:14", "07:15", "07:29", "07:30"]:
        now = datetime.strptime(f"2026-11-16 {t}", "%Y-%m-%d %H:%M")
        print(t, get_checkin_state(s, now))
