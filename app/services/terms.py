"""
End of semester, archive and term rollover (instructor).

  End semester   -> the subject is archived: its weekly schedule stops creating sessions,
                    students can't check in or join, and it moves from My subjects to Archive.
                    Everything stays readable (attendance, reports).
  Reopen         -> undo, if it was ended by mistake.
  Copy to new term -> a fresh subject for the next semester with the same sections and weekly
                    schedule, new join codes and no students.
"""
import logging
from datetime import datetime

from . import classes
from .accounts import ValidationError

log = logging.getLogger(__name__)


def _ts(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def next_term(school_year: str, semester: str) -> dict:
    """What usually comes next: 1st -> 2nd -> Summer -> 1st of the next school year."""
    if semester == "1st":
        return {"school_year": school_year, "semester": "2nd"}
    if semester == "2nd":
        return {"school_year": school_year, "semester": "Summer"}
    m = classes.SCHOOL_YEAR_RE.match(school_year or "")
    if m:
        school_year = f"{int(m.group(1)) + 1}-{int(m.group(2)) + 1}"
    return {"school_year": school_year, "semester": "1st"}


def list_archived(conn, instructor_id):
    rows = classes.list_subjects(conn, instructor_id, archived=True)
    return [{**dict(r), "next": next_term(r["school_year"], r["semester"])} for r in rows]


def end_semester(conn, instructor_id, subject_id, now=None) -> bool:
    """Archive the subject. Open sessions are closed; sessions that were created ahead of
    time and never used are removed. Returns False if it isn't theirs / already ended."""
    subject = classes.get_subject(conn, instructor_id, subject_id)
    if subject is None or subject["ended_at"]:
        return False
    now = now or datetime.now()
    ts, today = _ts(now), now.strftime("%Y-%m-%d")
    sections = "SELECT id FROM sections WHERE subject_id = ?"
    conn.execute("UPDATE subjects SET ended_at = ? WHERE id = ?", (ts, subject_id))
    # close whatever is still open today (students without a record become absent, as usual)
    conn.execute(f"""UPDATE class_sessions SET ended_at = ?, manual_state = 'disabled'
                     WHERE section_id IN ({sections}) AND deleted_at IS NULL AND ended_at IS NULL
                       AND session_date = ?""", (ts, subject_id, today))
    # future sessions nobody used
    conn.execute(f"""UPDATE class_sessions SET deleted_at = ?
                     WHERE section_id IN ({sections}) AND deleted_at IS NULL AND session_date > ?
                       AND NOT EXISTS (SELECT 1 FROM attendance a WHERE a.session_id = class_sessions.id
                                       AND a.deleted_at IS NULL)""", (ts, subject_id, today))
    classes.audit(conn, instructor_id, "end_semester", "subjects", subject_id)
    conn.commit()
    log.info("semester ended subject=%s by instructor=%s", subject_id, instructor_id)
    return True


def reopen(conn, instructor_id, subject_id) -> bool:
    subject = classes.get_subject(conn, instructor_id, subject_id)
    if subject is None or not subject["ended_at"]:
        return False
    conn.execute("UPDATE subjects SET ended_at = NULL WHERE id = ?", (subject_id,))
    classes.audit(conn, instructor_id, "reopen_semester", "subjects", subject_id)
    conn.commit()
    return True


def copy_to_new_term(conn, instructor_id, subject_id, form) -> int:
    """New subject for another term with the same sections + weekly schedule (no students,
    new join codes). Raises ValidationError (keys: school_year / semester / code)."""
    old = classes.get_subject(conn, instructor_id, subject_id)
    if old is None:
        raise ValidationError({"code": "Subject not found."})
    new_id = classes.create_subject(conn, instructor_id, {
        "code": old["code"], "title": old["title"], "description": old["description"] or "",
        "units": "" if old["units"] is None else str(old["units"]),
        "school_year": form.get("school_year", ""), "semester": form.get("semester", "")})
    if new_id == subject_id:
        raise ValidationError({"school_year": "Pick a different term than the one this subject is in."})

    skip = {"id", "subject_id", "join_code", "created_at", "deleted_at"}
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sections)") if r[1] not in skip]
    for sec in conn.execute("SELECT * FROM sections WHERE subject_id = ? AND deleted_at IS NULL ORDER BY name",
                            (subject_id,)).fetchall():
        exists = conn.execute("SELECT id FROM sections WHERE subject_id = ? AND name = ?",
                              (new_id, sec["name"])).fetchone()
        if exists:                                   # a restored subject may already have it
            continue
        cur = conn.execute(
            f"INSERT INTO sections (subject_id, join_code, {', '.join(cols)}) VALUES (?, ?, {', '.join('?' * len(cols))})",
            (new_id, classes._new_join_code(conn), *[sec[c] for c in cols]))
        conn.execute(
            """INSERT INTO schedules (section_id, day_of_week, start_time, end_time, room)
               SELECT ?, day_of_week, start_time, end_time, room FROM schedules
               WHERE section_id = ? AND deleted_at IS NULL""", (cur.lastrowid, sec["id"]))
    classes.audit(conn, instructor_id, "copy_to_new_term", "subjects", new_id, {"from": subject_id})
    conn.commit()
    log.info("subject=%s copied to new term as subject=%s", subject_id, new_id)
    return new_id


def subject_of(conn, *, subject_id=None, section_id=None, session_id=None, schedule_id=None, exception_id=None):
    """The subject row behind any of these ids (used to keep archived subjects read-only)."""
    if subject_id is None:
        if session_id is not None:
            r = conn.execute("SELECT section_id FROM class_sessions WHERE id = ?", (session_id,)).fetchone()
            section_id = r["section_id"] if r else None
        elif schedule_id is not None:
            r = conn.execute("SELECT section_id FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
            section_id = r["section_id"] if r else None
        elif exception_id is not None:
            r = conn.execute("SELECT section_id FROM schedule_exceptions WHERE id = ?", (exception_id,)).fetchone()
            section_id = r["section_id"] if r else None
        if section_id is None:
            return None
        r = conn.execute("SELECT subject_id FROM sections WHERE id = ?", (section_id,)).fetchone()
        subject_id = r["subject_id"] if r else None
    if subject_id is None:
        return None
    return conn.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
