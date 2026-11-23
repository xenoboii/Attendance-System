"""Instructor departments (colleges): the admin manages the list, instructors pick from it."""
import sqlite3

from .accounts import ValidationError, _ts
from .classes import audit

MAX_LENGTH = 80


def list_departments(conn, *, include_inactive=False, current=None):
    """Active departments (+ `current`, so an instructor keeps a disabled or older one)."""
    return conn.execute(
        "SELECT * FROM departments WHERE ? OR is_active = 1 OR name = ? ORDER BY name",
        (include_inactive, current),
    ).fetchall()


def is_available(conn, name) -> bool:
    return conn.execute("SELECT 1 FROM departments WHERE name = ? AND is_active = 1", (name,)).fetchone() is not None


def add(conn, actor_id, name):
    name = " ".join((name or "").split())
    if len(name) < 3 or len(name) > MAX_LENGTH or not any(ch.isalpha() for ch in name) or "<" in name or ">" in name:
        raise ValidationError({"name": f"Type the full name (3 to {MAX_LENGTH} characters), e.g. College of Engineering."})
    try:
        with conn:
            conn.execute("INSERT INTO departments (name, created_by) VALUES (?, ?)", (name, actor_id))
            audit(conn, actor_id, "add_department", "department", 0, {"name": name})
    except sqlite3.IntegrityError as exc:
        if conn.execute("SELECT 1 FROM departments WHERE name = ?", (name,)).fetchone():
            raise ValidationError({"name": "That department already exists. Enable it if it is disabled."}) from exc
        raise
    return name


def set_active(conn, actor_id, name, active):
    with conn:
        cur = conn.execute("UPDATE departments SET is_active = ?, updated_at = ? WHERE name = ?",
                           (int(active), _ts(), name))
        if not cur.rowcount:
            raise ValidationError({"name": "Department not found."})
        audit(conn, actor_id, "enable_department" if active else "disable_department", "department", 0,
              {"name": name})


def remove(conn, actor_id, name):
    with conn:
        if not conn.execute("DELETE FROM departments WHERE name = ?", (name,)).rowcount:
            raise ValidationError({"name": "Department not found."})
        audit(conn, actor_id, "remove_department", "department", 0, {"name": name})
