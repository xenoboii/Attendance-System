"""System-wide settings the admin can change (stored in the database, so they survive restarts)."""
from .accounts import ValidationError, _ts

SIGNATURE_DAYS_KEY = "checkin_signature_days"
SIGNATURE_DAYS_RANGE = (7, 730)          # one week to two years


def get(conn, key, default=None):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def put(conn, key, value) -> None:
    conn.execute("""INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                 (key, str(value), _ts()))


def signature_days(conn, default: int = 60) -> int:
    """Days a signature drawn at web check-in is kept before it is deleted."""
    try:
        return int(get(conn, SIGNATURE_DAYS_KEY, default))
    except (TypeError, ValueError):
        return default


def set_signature_days(conn, actor_id, text) -> int:
    low, high = SIGNATURE_DAYS_RANGE
    try:
        days = int(str(text).strip())
        if not low <= days <= high:
            raise ValueError
    except ValueError:
        raise ValidationError({"signature_days": f"Use a number of days from {low} to {high}."})
    from .classes import audit
    put(conn, SIGNATURE_DAYS_KEY, days)
    audit(conn, actor_id, "set_signature_days", "app_settings", 0, {"days": days})
    conn.commit()
    return days
