"""
"Recently deleted" (backup): deleted items can be restored for TRASH_DAYS (30) days,
then they are deleted for good by purge_expired().

What can be in it (instructor's own only):
    subject   - with everything under it
    section   - with its students, schedules and sessions
    schedule  - a weekly class day
    session   - one date column (with its check-ins)
    student   - a student removed from a section (their attendance is kept while restorable)

Deleting is still a "soft delete" (deleted_at is set). Restoring clears deleted_at.
Purging removes the rows from the database, children first.
"""
import logging
from datetime import datetime, timedelta

from . import classes
from .accounts import ValidationError

log = logging.getLogger(__name__)

KINDS = ("subject", "section", "schedule", "session", "student")


def _ts(dt=None):
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _days_left(deleted_at, days, now):
    gone = datetime.strptime(deleted_at, "%Y-%m-%d %H:%M:%S") + timedelta(days=days)
    return max(0, (gone - now).days + (1 if (gone - now).seconds else 0)), gone


# ================================================================ listing
def list_trash(conn, instructor_id, days=30, now=None):
    """Everything this instructor deleted, newest first. A child of something that is
    itself deleted (e.g. a section of a deleted subject) is shown with its parent only."""
    now = now or datetime.now()
    items = []

    for r in conn.execute(
            """SELECT id, code, title, school_year, semester, deleted_at FROM subjects
               WHERE instructor_id = ? AND deleted_at IS NOT NULL""", (instructor_id,)):
        items.append({"kind": "subject", "id": r["id"], "title": f"{r['code']} · {r['title']}",
                      "detail": f"Subject · {r['school_year']}, {r['semester']} sem · with all its sections",
                      "deleted_at": r["deleted_at"]})

    live_sections = """JOIN subjects sub ON sub.id = s.subject_id
                       WHERE sub.instructor_id = ? AND sub.deleted_at IS NULL"""

    for r in conn.execute(
            f"""SELECT s.id, s.name, sub.code, s.deleted_at FROM sections s {live_sections}
                AND s.deleted_at IS NOT NULL""", (instructor_id,)):
        items.append({"kind": "section", "id": r["id"], "title": f"{r['code']} · {r['name']}",
                      "detail": "Section · with its students, schedule and sessions", "deleted_at": r["deleted_at"]})

    for r in conn.execute(
            f"""SELECT sc.id, sc.day_of_week, sc.start_time, sc.end_time, s.name, sub.code, sc.deleted_at
                FROM schedules sc JOIN sections s ON s.id = sc.section_id {live_sections}
                AND s.deleted_at IS NULL AND sc.deleted_at IS NOT NULL""", (instructor_id,)):
        t = lambda x: datetime.strptime(x, "%H:%M").strftime("%I:%M %p").lstrip("0")
        items.append({"kind": "schedule", "id": r["id"],
                      "title": f"{classes.DAYS[r['day_of_week']]} {t(r['start_time'])} – {t(r['end_time'])}",
                      "detail": f"Weekly schedule · {r['code']} {r['name']}", "deleted_at": r["deleted_at"]})

    for r in conn.execute(
            f"""SELECT cs.id, cs.session_date, cs.type, cs.title, s.name, sub.code, cs.deleted_at,
                       (SELECT COUNT(*) FROM attendance a WHERE a.session_id = cs.id AND a.deleted_at IS NULL) AS n
                FROM class_sessions cs JOIN sections s ON s.id = cs.section_id {live_sections}
                AND s.deleted_at IS NULL AND cs.deleted_at IS NOT NULL""", (instructor_id,)):
        d = datetime.strptime(r["session_date"], "%Y-%m-%d").strftime("%a, %b %d, %Y").replace(" 0", " ")
        kind = {"async": "Async activity", "makeup": "Make-up session"}.get(r["type"], "Session")
        items.append({"kind": "session", "id": r["id"], "title": f"{d}" + (f" · {r['title']}" if r["title"] else ""),
                      "detail": f"{kind} · {r['code']} {r['name']} · {r['n']} check-in{'s' if r['n'] != 1 else ''}",
                      "deleted_at": r["deleted_at"]})

    for r in conn.execute(
            f"""SELECT e.id, u.first_name, u.last_name, u.student_number, s.name, sub.code, e.deleted_at
                FROM enrollments e JOIN users u ON u.id = e.student_id
                JOIN sections s ON s.id = e.section_id {live_sections}
                AND s.deleted_at IS NULL AND e.deleted_at IS NOT NULL AND u.deleted_at IS NULL""", (instructor_id,)):
        items.append({"kind": "student", "id": r["id"], "title": f"{r['last_name']}, {r['first_name']}",
                      "detail": f"Removed from {r['code']} {r['name']} · {r['student_number']}",
                      "deleted_at": r["deleted_at"]})

    for it in items:
        it["days_left"], it["gone_at"] = _days_left(it["deleted_at"], days, now)
    items.sort(key=lambda x: x["deleted_at"], reverse=True)
    return items


# ================================================================ ownership
def _owned(conn, instructor_id, kind, item_id):
    """The row if this instructor owns it and it is deleted, else None."""
    q = {
        "subject": "SELECT sub.* FROM subjects sub WHERE sub.id = ? AND sub.instructor_id = ?",
        "section": """SELECT s.* FROM sections s JOIN subjects sub ON sub.id = s.subject_id
                      WHERE s.id = ? AND sub.instructor_id = ?""",
        "schedule": """SELECT sc.* FROM schedules sc JOIN sections s ON s.id = sc.section_id
                       JOIN subjects sub ON sub.id = s.subject_id WHERE sc.id = ? AND sub.instructor_id = ?""",
        "session": """SELECT cs.* FROM class_sessions cs JOIN sections s ON s.id = cs.section_id
                      JOIN subjects sub ON sub.id = s.subject_id WHERE cs.id = ? AND sub.instructor_id = ?""",
        "student": """SELECT e.* FROM enrollments e JOIN sections s ON s.id = e.section_id
                      JOIN subjects sub ON sub.id = s.subject_id WHERE e.id = ? AND sub.instructor_id = ?""",
    }.get(kind)
    if q is None:
        return None
    row = conn.execute(q, (item_id, instructor_id)).fetchone()
    return dict(row) if row and row["deleted_at"] else None


# ================================================================ restore
def restore(conn, instructor_id, kind, item_id) -> str:
    """Bring an item back. Returns a short message; raises ValidationError if it can't."""
    row = _owned(conn, instructor_id, kind, item_id)
    if row is None:
        raise ValidationError({"trash": "That item isn't in Recently deleted anymore."})

    if kind == "subject":
        dup = conn.execute("""SELECT 1 FROM subjects WHERE instructor_id = ? AND code = ? AND school_year = ?
                              AND semester = ? AND id <> ? AND deleted_at IS NULL""",
                           (instructor_id, row["code"], row["school_year"], row["semester"], item_id)).fetchone()
        if dup:
            raise ValidationError({"trash": f"You already have {row['code']} for that term."})
        conn.execute("UPDATE subjects SET deleted_at = NULL WHERE id = ?", (item_id,))
        msg = f"Restored {row['code']}."
    elif kind == "section":
        conn.execute("UPDATE sections SET deleted_at = NULL WHERE id = ?", (item_id,))
        msg = f"Restored section {row['name']}."
    elif kind == "schedule":
        form = {"day_of_week": str(row["day_of_week"]), "start_time": row["start_time"],
                "end_time": row["end_time"], "room": row["room"] or ""}
        try:
            classes._validate_schedule(conn, instructor_id, form, exclude_id=item_id)
        except ValidationError as e:
            raise ValidationError({"trash": "Can't restore: " + next(iter(e.errors.values()))})
        conn.execute("UPDATE schedules SET deleted_at = NULL WHERE id = ?", (item_id,))
        msg = "Restored the schedule."
    elif kind == "session":
        if conn.execute("SELECT 1 FROM schedule_exceptions WHERE section_id=? AND exception_date=? AND is_vacation=1",
                        (row["section_id"], row["session_date"])).fetchone():
            raise ValidationError({"trash": "Remove this date's vacation mark in Schedule before restoring its class."})
        conn.execute("UPDATE class_sessions SET deleted_at = NULL WHERE id = ?", (item_id,))
        msg = "Restored the session. It's back in the report."
        # an async activity restored onto an upcoming class day: the day is an async day again
        # (no regular class beside it), and can be changed back from the Upcoming list
        if (row["type"] == "async" and row["schedule_id"] is None
                and classes.async_session_restored(conn, instructor_id, row["section_id"], row["session_date"], item_id)):
            msg = ("Restored the async activity. That class day is async again; "
                   "change it back to a regular class from the Upcoming list if needed.")
    else:  # student
        conn.execute("UPDATE enrollments SET deleted_at = NULL, status = 'active' WHERE id = ?", (item_id,))
        msg = "Restored the student to the section."

    classes.audit(conn, instructor_id, "restore", kind, item_id)
    conn.commit()
    log.info("restored %s=%s by instructor=%s", kind, item_id, instructor_id)
    return msg


# ================================================================ delete for good
def _purge_sessions(conn, where_sql, args):
    ids = [r[0] for r in conn.execute(f"SELECT id FROM class_sessions WHERE {where_sql}", args)]
    for sid in ids:
        conn.execute("DELETE FROM attendance WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM session_state_changes WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM report_sessions WHERE session_id = ?", (sid,))
        conn.execute("UPDATE rfid_scans SET session_id = NULL WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM class_sessions WHERE id = ?", (sid,))
    return len(ids)


def _purge_section(conn, section_id):
    _purge_sessions(conn, "section_id = ?", (section_id,))
    conn.execute("DELETE FROM join_requests WHERE section_id = ?", (section_id,))
    conn.execute("DELETE FROM schedule_exceptions WHERE section_id = ?", (section_id,))
    conn.execute("DELETE FROM schedules WHERE section_id = ?", (section_id,))
    conn.execute("DELETE FROM enrollments WHERE section_id = ?", (section_id,))
    for r in conn.execute("SELECT id FROM reports WHERE section_id = ?", (section_id,)).fetchall():
        conn.execute("DELETE FROM report_sessions WHERE report_id = ?", (r[0],))
    conn.execute("DELETE FROM reports WHERE section_id = ?", (section_id,))
    conn.execute("DELETE FROM sections WHERE id = ?", (section_id,))


def purge_subject(conn, subject_id):
    """Delete a subject and its dependent records in the caller's transaction."""
    for row in conn.execute("SELECT id FROM sections WHERE subject_id = ?", (subject_id,)).fetchall():
        _purge_section(conn, row[0])
    conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))


def _purge(conn, kind, item_id):
    if kind == "subject":
        purge_subject(conn, item_id)
    elif kind == "section":
        _purge_section(conn, item_id)
    elif kind == "schedule":
        conn.execute("UPDATE class_sessions SET schedule_id = NULL WHERE schedule_id = ?", (item_id,))
        conn.execute("DELETE FROM schedules WHERE id = ?", (item_id,))
    elif kind == "session":
        _purge_sessions(conn, "id = ?", (item_id,))
    elif kind == "student":
        e = conn.execute("SELECT section_id, student_id FROM enrollments WHERE id = ?", (item_id,)).fetchone()
        if e:   # their check-ins in that section go too
            conn.execute("""DELETE FROM attendance WHERE student_id = ? AND session_id IN
                            (SELECT id FROM class_sessions WHERE section_id = ?)""", (e["student_id"], e["section_id"]))
        conn.execute("DELETE FROM enrollments WHERE id = ?", (item_id,))


def delete_forever(conn, instructor_id, kind, item_id) -> None:
    row = _owned(conn, instructor_id, kind, item_id)
    if row is None:
        raise ValidationError({"trash": "That item isn't in Recently deleted anymore."})
    _purge(conn, kind, item_id)
    classes.audit(conn, instructor_id, "delete_forever", kind, item_id)
    conn.commit()
    log.info("deleted forever %s=%s by instructor=%s", kind, item_id, instructor_id)


def purge_expired(conn, days=30, now=None) -> int:
    """Delete for good everything that has been in Recently deleted longer than `days`."""
    now = now or datetime.now()
    cutoff = _ts(now - timedelta(days=days))
    count = 0
    # parents first, so their children go with them
    for kind, sql in (
        ("subject", "SELECT id FROM subjects WHERE deleted_at IS NOT NULL AND deleted_at < ?"),
        ("section", "SELECT id FROM sections WHERE deleted_at IS NOT NULL AND deleted_at < ?"),
        ("schedule", "SELECT id FROM schedules WHERE deleted_at IS NOT NULL AND deleted_at < ?"),
        ("session", "SELECT id FROM class_sessions WHERE deleted_at IS NOT NULL AND deleted_at < ?"),
        ("student", "SELECT id FROM enrollments WHERE deleted_at IS NOT NULL AND deleted_at < ?"),
    ):
        for (item_id,) in conn.execute(sql, (cutoff,)).fetchall():
            _purge(conn, kind, item_id)
            count += 1
    if count:
        classes.audit(conn, None, "purge_expired", "trash", 0, {"items": count, "older_than_days": days})
        conn.commit()
        log.info("trash: %s item(s) older than %s days deleted for good", count, days)
    return count
