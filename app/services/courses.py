"""Course abbreviations shared by student profiles and Admin course management."""
import re
import sqlite3

from .accounts import ValidationError, _ts
from .classes import audit


def list_courses(conn, *, include_inactive=False, current=None):
    rows = conn.execute(
        "SELECT * FROM courses WHERE ? OR is_active = 1 OR code = ? ORDER BY code",
        (include_inactive, current),
    ).fetchall()
    if current and not include_inactive and not any(row["code"].upper() == current.upper() for row in rows):
        # An existing profile or section can retain its code after catalog removal.
        rows.append({"code": current.upper(), "is_active": 0})
        rows.sort(key=lambda row: row["code"])
    return rows


def add(conn, actor_id, abbreviation):
    code = " ".join((abbreviation or "").upper().split())
    if not re.fullmatch(r"[A-Z][A-Z0-9 .-]{0,9}", code):
        raise ValidationError({"code": "Use a course abbreviation of up to 10 characters, e.g. BSCPE."})
    try:
        with conn:
            conn.execute("INSERT INTO courses (code, created_by) VALUES (?, ?)", (code, actor_id))
            audit(conn, actor_id, "add_course", "course", 0, {"code": code})
    except sqlite3.IntegrityError as exc:
        if conn.execute("SELECT 1 FROM courses WHERE code = ?", (code,)).fetchone():
            raise ValidationError({"code": "That course already exists. Enable it if it is disabled."}) from exc
        raise
    return code


def set_active(conn, actor_id, code, active):
    with conn:
        cur = conn.execute("UPDATE courses SET is_active = ?, updated_at = ? WHERE code = ?",
                           (int(active), _ts(), code))
        if not cur.rowcount:
            raise ValidationError({"code": "Course not found."})
        audit(conn, actor_id, "enable_course" if active else "disable_course", "course", 0,
              {"code": code})


def remove(conn, actor_id, code):
    with conn:
        if not conn.execute("DELETE FROM courses WHERE code = ?", (code,)).rowcount:
            raise ValidationError({"code": "Course not found."})
        audit(conn, actor_id, "remove_course", "course", 0, {"code": code})
