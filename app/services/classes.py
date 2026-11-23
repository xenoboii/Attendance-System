"""
Subjects, sections, schedules and enrollment (instructor side) + join-by-code (student side).

Rules:
  * An instructor can only see/change their OWN subjects (and everything under them).
  * Delete = soft delete (deleted_at) + an audit_log entry. Re-creating something that
    was deleted (same subject code, section name, or student) restores it instead.
"""
import json
import logging
import re
import secrets
from datetime import datetime

from .accounts import ValidationError

log = logging.getLogger(__name__)

SEMESTERS = ("1st", "2nd", "Summer")
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
JOIN_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I to avoid confusion
SCHOOL_YEAR_RE = re.compile(r"^(\d{4})-(\d{4})$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(form, key):
    return (form.get(key) or "").strip()


def audit(conn, user_id, action, entity, entity_id, details=None):
    conn.execute(
        "INSERT INTO audit_log (user_id, action, entity, entity_id, details) VALUES (?, ?, ?, ?, ?)",
        (user_id, action, entity, entity_id, json.dumps(details, default=str) if details else None),
    )


# ================================================================ subjects
def _validate_subject(form):
    errors, data = {}, {
        "code": _clean(form, "code").upper(),
        "title": _clean(form, "title"),
        "description": _clean(form, "description") or None,
        "school_year": _clean(form, "school_year"),
        "semester": _clean(form, "semester"),
        "units": _clean(form, "units"),
    }
    if not data["code"] or len(data["code"]) > 20:
        errors["code"] = "Subject code is required (max 20 characters)."
    if not data["title"]:
        errors["title"] = "Subject title is required."
    m = SCHOOL_YEAR_RE.match(data["school_year"])
    if not m or int(m.group(2)) != int(m.group(1)) + 1:
        errors["school_year"] = "Use the format 2026-2027."
    if data["semester"] not in SEMESTERS:
        errors["semester"] = "Choose a semester."
    if data["units"]:
        try:
            if not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", data["units"]):
                raise ValueError
            data["units"] = float(data["units"])
            if not 0.5 <= data["units"] <= 12:
                raise ValueError
        except ValueError:
            errors["units"] = "Units must be a number from 0.5 to 12."
    else:
        data["units"] = None
    if errors:
        raise ValidationError(errors)
    return data


def list_subjects(conn, instructor_id, archived=False):
    """Current subjects, or (archived=True) the ones whose semester was ended."""
    return conn.execute(
        f"""SELECT sub.*,
                  (SELECT COUNT(*) FROM sections s
                    WHERE s.subject_id = sub.id AND s.deleted_at IS NULL) AS section_count,
                  (SELECT COUNT(*) FROM enrollments e JOIN sections s ON s.id = e.section_id
                    WHERE s.subject_id = sub.id AND s.deleted_at IS NULL
                      AND e.deleted_at IS NULL AND e.status = 'active') AS student_count
           FROM subjects sub
           WHERE sub.instructor_id = ? AND sub.deleted_at IS NULL
             AND sub.ended_at IS {'NOT NULL' if archived else 'NULL'}
           ORDER BY sub.school_year DESC, sub.semester, sub.code""",
        (instructor_id,),
    ).fetchall()


def get_subject(conn, instructor_id, subject_id):
    return conn.execute(
        "SELECT * FROM subjects WHERE id = ? AND instructor_id = ? AND deleted_at IS NULL",
        (subject_id, instructor_id),
    ).fetchone()


def _find_subject_dup(conn, instructor_id, d, exclude_id=None):
    return conn.execute(
        """SELECT * FROM subjects WHERE instructor_id = ? AND code = ? AND school_year = ?
             AND semester = ? AND id IS NOT ?""",
        (instructor_id, d["code"], d["school_year"], d["semester"], exclude_id),
    ).fetchone()


def create_subject(conn, instructor_id, form) -> int:
    d = _validate_subject(form)
    dup = _find_subject_dup(conn, instructor_id, d)
    if dup and dup["deleted_at"] is None:
        raise ValidationError({"code": f"You already have {d['code']} for {d['school_year']} {d['semester']} sem."})
    if dup:  # restore the deleted one
        conn.execute(
            """UPDATE subjects SET title = ?, description = ?, units = ?, deleted_at = NULL
               WHERE id = ?""", (d["title"], d["description"], d["units"], dup["id"]))
        audit(conn, instructor_id, "restore", "subjects", dup["id"])
        conn.commit()
        return dup["id"]
    cur = conn.execute(
        """INSERT INTO subjects (instructor_id, code, title, description, units, school_year, semester)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (instructor_id, d["code"], d["title"], d["description"], d["units"], d["school_year"], d["semester"]),
    )
    conn.commit()
    log.info("subject created id=%s %s by instructor=%s", cur.lastrowid, d["code"], instructor_id)
    return cur.lastrowid


def update_subject(conn, instructor_id, subject_id, form) -> None:
    d = _validate_subject(form)
    dup = _find_subject_dup(conn, instructor_id, d, exclude_id=subject_id)
    if dup:
        raise ValidationError({"code": f"You already have {d['code']} for {d['school_year']} {d['semester']} sem"
                                       + (" (deleted)." if dup["deleted_at"] else ".")})
    conn.execute(
        """UPDATE subjects SET code = ?, title = ?, description = ?, units = ?, school_year = ?, semester = ?
           WHERE id = ? AND instructor_id = ?""",
        (d["code"], d["title"], d["description"], d["units"], d["school_year"], d["semester"],
         subject_id, instructor_id),
    )
    conn.commit()


def delete_subject(conn, instructor_id, subject_id) -> None:
    subject = get_subject(conn, instructor_id, subject_id)
    conn.execute("UPDATE subjects SET deleted_at = ? WHERE id = ?", (_now(), subject_id))
    audit(conn, instructor_id, "delete", "subjects", subject_id, dict(subject))
    conn.commit()
    log.info("subject deleted id=%s by instructor=%s", subject_id, instructor_id)


# ================================================================ sections
def _new_join_code(conn):
    while True:
        code = "".join(secrets.choice(JOIN_CODE_CHARS) for _ in range(6))
        if not conn.execute("SELECT 1 FROM sections WHERE join_code = ?", (code,)).fetchone():
            return code


def list_sections(conn, subject_id):
    rows = conn.execute(
        """SELECT s.*,
                  (SELECT COUNT(*) FROM enrollments e WHERE e.section_id = s.id
                     AND e.deleted_at IS NULL AND e.status = 'active') AS student_count,
                  (SELECT COUNT(*) FROM schedules sc WHERE sc.section_id = s.id
                     AND sc.deleted_at IS NULL) AS schedule_count,
                  (SELECT COUNT(*) FROM class_sessions cs WHERE cs.section_id = s.id
                     AND cs.deleted_at IS NULL) AS session_count
           FROM sections s WHERE s.subject_id = ? AND s.deleted_at IS NULL ORDER BY s.name""",
        (subject_id,),
    ).fetchall()
    # + the weekly schedule as a list (one line per class day) for the table
    return [{**dict(r), "schedule": list_schedules(conn, r["id"])} for r in rows]


def get_section(conn, instructor_id, section_id):
    """Section + its subject, only if the instructor owns it."""
    return conn.execute(
        """SELECT s.*, sub.code AS subject_code, sub.title AS subject_title,
                  sub.school_year, sub.semester, sub.id AS subject_id, sub.ended_at AS ended_at
           FROM sections s JOIN subjects sub ON sub.id = s.subject_id
           WHERE s.id = ? AND sub.instructor_id = ?
             AND s.deleted_at IS NULL AND sub.deleted_at IS NULL""",
        (section_id, instructor_id),
    ).fetchone()


COURSE_MAX = 10
YEAR_LEVELS = (1, 2, 3, 4, 5)
_SECTION_NAME_RE = re.compile(r"^(?P<course>.+?)\s+(?P<year>[1-9])(?P<letter>[A-Za-z0-9]{1,3})$")


def section_name_from(form) -> str:
    """Course + year + section -> 'BSCpE 4A'. Raises ValidationError (per field).
    (Old forms that send a single "name" still work.)"""
    if "course" not in form and "name" in form:
        name = _clean(form, "name").upper()
        if not name or len(name) > 30:
            raise ValidationError({"name": "Section name is required (max 30 characters), e.g. BSIT 3A."})
        match = _SECTION_NAME_RE.fullmatch(name)
        if (not match or match["year"] not in {str(y) for y in YEAR_LEVELS}
                or match["letter"] not in tuple("ABCDEFG")):
            raise ValidationError({"name": "Choose a course, year level, and section from A to G."})
        return name
    errors = {}
    course = " ".join(_clean(form, "course").split())          # keep the prof's capitals: BSCpE
    if not course:
        errors["course"] = "Enter the course, e.g. BSCpE."
    elif len(course) > COURSE_MAX:
        errors["course"] = f"Use the course abbreviation (max {COURSE_MAX} characters), e.g. BSCpE."
    elif not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .-]*", course):
        errors["course"] = "Use the course abbreviation, letters only, e.g. BSCpE."
    year = _clean(form, "year_level")
    if year not in {str(y) for y in YEAR_LEVELS}:
        errors["year_level"] = "Choose the year level."
    letter = _clean(form, "section_letter").upper()
    if letter not in "ABCDEFG" or len(letter) != 1:
        errors["section_letter"] = "Choose a section from A to G."
    if errors:
        raise ValidationError(errors)
    return f"{course} {year}{letter}"


def split_section_name(name: str) -> dict:
    """'BSCpE 4A' -> {'course': 'BSCpE', 'year_level': '4', 'section_letter': 'A'} (to fill the form)."""
    m = _SECTION_NAME_RE.match(name or "")
    if not m:
        return {"course": name or "", "year_level": "", "section_letter": ""}
    return {"course": m["course"], "year_level": m["year"], "section_letter": m["letter"].upper()}


def _section_name_error(form) -> str:
    """Which field to show a 'name' problem (e.g. duplicate) under."""
    return "section_letter" if "course" in form else "name"


def create_section(conn, instructor_id, subject_id, form) -> int:
    name = section_name_from(form)
    _validate_section_course(conn, name)
    dup = conn.execute("SELECT * FROM sections WHERE subject_id = ? AND upper(name) = upper(?)",
                       (subject_id, name)).fetchone()
    if dup and dup["deleted_at"] is None:
        raise ValidationError({_section_name_error(form): f"Section {name} already exists in this subject."})
    if dup:
        conn.execute("UPDATE sections SET deleted_at = NULL WHERE id = ?", (dup["id"],))
        audit(conn, instructor_id, "restore", "sections", dup["id"])
        conn.commit()
        return dup["id"]
    cur = conn.execute("INSERT INTO sections (subject_id, name, join_code) VALUES (?, ?, ?)",
                       (subject_id, name, _new_join_code(conn)))
    conn.commit()
    log.info("section created id=%s %s by instructor=%s", cur.lastrowid, name, instructor_id)
    return cur.lastrowid


MAX_DROP_ABSENCES = 30


def drop_status(absences: int, limit: int) -> str | None:
    """'drop' = reached the section's limit, 'warn' = one absence away, None = fine / no rule."""
    if not limit:
        return None
    return "drop" if absences >= limit else ("warn" if absences == limit - 1 else None)


def update_section(conn, instructor_id, section_id, form) -> None:
    errors = {}
    try:
        name = section_name_from(form)
    except ValidationError as e:
        errors.update(e.errors)
        name = None
    mode = _clean(form, "default_checkin_mode")
    if mode not in ("auto", "manual"):
        errors["default_checkin_mode"] = "Choose automatic or manual."
    nums = {}
    for key, label in (("default_open_before_minutes", "Opens before class"),
                       ("default_grace_minutes", "Grace period"),
                       ("default_close_after_minutes", "Closes after start")):
        try:
            nums[key] = int(_clean(form, key))
            if not 0 <= nums[key] <= 240:
                raise ValueError
        except ValueError:
            errors[key] = f"{label} must be 0 to 240 minutes."
    if not errors and nums["default_close_after_minutes"] < nums["default_grace_minutes"]:
        errors["default_close_after_minutes"] = "Check-in must close after the grace period ends."
    section = get_section(conn, instructor_id, section_id)
    if section is None:
        raise ValidationError({"section": "Section not found."})
    # drop rule: absences that get a student dropped (0 = no rule). Optional in the form.
    try:
        drop_absences = int(str(form.get("drop_absences", section["drop_absences"])).strip() or 0)
        if not 0 <= drop_absences <= MAX_DROP_ABSENCES:
            raise ValueError
    except ValueError:
        errors["drop_absences"] = f"Use a number from 0 to {MAX_DROP_ABSENCES} (0 = no drop rule)."
        drop_absences = 0
    join_approval = form.get("join_approval", section["join_approval"])
    if join_approval not in ("outsiders", "everyone", "anyone"):
        errors["join_approval"] = "Choose who needs approval to join."
    if name:
        try:
            _validate_section_course(conn, name, current=section["name"])
        except ValidationError as exc:
            errors.update(exc.errors)
    if not errors and conn.execute(
        "SELECT 1 FROM sections WHERE subject_id = ? AND upper(name) = upper(?) AND id <> ?",
        (section["subject_id"], name, section_id),
    ).fetchone():
        errors[_section_name_error(form)] = f"Section {name} already exists in this subject."
    if errors:
        raise ValidationError(errors)
    conn.execute(
        """UPDATE sections SET name = ?, default_checkin_mode = ?, default_open_before_minutes = ?,
                  default_grace_minutes = ?, default_close_after_minutes = ?, join_approval = ?,
                  drop_absences = ? WHERE id = ?""",
        (name, mode, nums["default_open_before_minutes"], nums["default_grace_minutes"],
         nums["default_close_after_minutes"], join_approval, drop_absences, section_id),
    )
    if join_approval != section["join_approval"]:
        audit(conn, instructor_id, "update_join_approval", "sections", section_id,
              {"before": section["join_approval"], "after": join_approval})
    conn.commit()


def regenerate_join_code(conn, instructor_id, section_id) -> str:
    code = _new_join_code(conn)
    conn.execute("UPDATE sections SET join_code = ? WHERE id = ?", (code, section_id))
    audit(conn, instructor_id, "regenerate_join_code", "sections", section_id)
    conn.commit()
    return code


def delete_section(conn, instructor_id, section_id) -> None:
    section = get_section(conn, instructor_id, section_id)
    conn.execute("UPDATE sections SET deleted_at = ? WHERE id = ?", (_now(), section_id))
    audit(conn, instructor_id, "delete", "sections", section_id, dict(section))
    conn.commit()
    log.info("section deleted id=%s by instructor=%s", section_id, instructor_id)


# ================================================================ schedules
def list_schedules(conn, section_id):
    rows = conn.execute(
        """SELECT * FROM schedules WHERE section_id = ? AND deleted_at IS NULL
           ORDER BY day_of_week, start_time""", (section_id,)).fetchall()
    return [{**dict(r), "day_name": DAYS[r["day_of_week"]],
             "length": _length(r["start_time"], r["end_time"])} for r in rows]


def _length(start, end) -> str:
    """'07:00','09:30' -> '2 hr 30 min'"""
    mins = (int(end[:2]) * 60 + int(end[3:5])) - (int(start[:2]) * 60 + int(start[3:5]))
    h, m = divmod(mins, 60)
    return " ".join(p for p in (f"{h} hr" if h else "", f"{m} min" if m else "") if p) or "0 min"


def add_schedule(conn, instructor_id, section_id, form) -> int:
    day, start, end, room = _validate_schedule(conn, instructor_id, form)
    cur = conn.execute(
        "INSERT INTO schedules (section_id, day_of_week, start_time, end_time, room) VALUES (?, ?, ?, ?, ?)",
        (section_id, day, start, end, room),
    )
    conn.commit()
    return cur.lastrowid


MAX_SCHEDULE_ROWS = 7          # one per day of the week is plenty


def schedule_rows(form) -> list[dict]:
    """The Add schedule pop-up can send several class days at once: every field is
    repeated, one value per row. Returns them as a list of one-row forms."""
    if not hasattr(form, "getlist"):
        return [dict(form)]
    keys = ("day_of_week", "start_time", "end_time", "room")
    cols = {k: form.getlist(k) for k in keys}
    count = max((len(v) for v in cols.values()), default=0) or 1
    return [{k: (cols[k][i] if i < len(cols[k]) else "") for k in keys} for i in range(count)]


def add_schedules(conn, instructor_id, section_id, form) -> list[int]:
    """Add one or more class days in one go. All or nothing: if any row is wrong,
    nothing is saved and ValidationError carries {"schedule_rows": {row index: errors}}."""
    rows = schedule_rows(form)
    if len(rows) > MAX_SCHEDULE_ROWS:
        raise ValidationError({"schedule_rows": {MAX_SCHEDULE_ROWS: {
            "day_of_week": f"You can add up to {MAX_SCHEDULE_ROWS} schedules at a time."}}})
    row_errors, valid = {}, {}
    for i, row in enumerate(rows):
        try:
            valid[i] = _validate_schedule(conn, instructor_id, row)
        except ValidationError as e:
            row_errors[i] = e.errors
    # the new rows must not overlap each other either
    for i, (day, start, end, _room) in valid.items():
        for j, (day2, start2, end2, _r) in valid.items():
            if j < i and day == day2 and start < end2 and start2 < end:
                row_errors[i] = {"start_time": f"Overlaps with schedule {j + 1} above "
                                               f"({DAYS[day2]} {start2}-{end2})."}
                break
    if row_errors:
        raise ValidationError({"schedule_rows": row_errors})
    ids = []
    for i in sorted(valid):
        day, start, end, room = valid[i]
        ids.append(conn.execute(
            "INSERT INTO schedules (section_id, day_of_week, start_time, end_time, room) VALUES (?, ?, ?, ?, ?)",
            (section_id, day, start, end, room)).lastrowid)
    conn.commit()
    return ids


def _own_schedule(conn, instructor_id, schedule_id):
    return conn.execute(
        """SELECT sc.* FROM schedules sc JOIN sections s ON s.id = sc.section_id
           JOIN subjects sub ON sub.id = s.subject_id
           WHERE sc.id = ? AND sub.instructor_id = ? AND sc.deleted_at IS NULL""",
        (schedule_id, instructor_id),
    ).fetchone()


def update_schedule(conn, instructor_id, schedule_id, form) -> int | None:
    """Change a class day's day / time / room. Returns the section id (None if not theirs).

    Upcoming sessions already created from this schedule that nobody has used yet
    (not opened, no check-ins) follow the change: new time, or removed if the day moved."""
    old = _own_schedule(conn, instructor_id, schedule_id)
    if old is None:
        return None
    day, start, end, room = _validate_schedule(conn, instructor_id, form, exclude_id=schedule_id)
    conn.execute("UPDATE schedules SET day_of_week = ?, start_time = ?, end_time = ?, room = ? WHERE id = ?",
                 (day, start, end, room, schedule_id))

    today = datetime.now().strftime("%Y-%m-%d")
    unused = """schedule_id = ? AND session_date >= ? AND deleted_at IS NULL
                AND opened_at IS NULL AND ended_at IS NULL
                AND NOT EXISTS (SELECT 1 FROM attendance a WHERE a.session_id = class_sessions.id
                                AND a.deleted_at IS NULL)"""
    if day != old["day_of_week"]:
        conn.execute(f"UPDATE class_sessions SET deleted_at = ? WHERE {unused}", (_now(), schedule_id, today))
    else:
        conn.execute(f"UPDATE class_sessions SET start_time = ?, end_time = ? WHERE {unused}",
                     (start, end, schedule_id, today))
    audit(conn, instructor_id, "update", "schedules", schedule_id,
          {"from": dict(old), "to": {"day_of_week": day, "start_time": start, "end_time": end, "room": room}})
    conn.commit()
    log.info("schedule=%s updated by instructor=%s", schedule_id, instructor_id)
    return old["section_id"]


def _validate_schedule(conn, instructor_id, form, exclude_id=None):
    """Returns (day, start, end, room) or raises ValidationError."""
    errors = {}
    try:
        day = int(_clean(form, "day_of_week"))
        if not 0 <= day <= 6:
            raise ValueError
    except ValueError:
        errors["day_of_week"] = "Choose a day."
        day = None
    start, end = _clean(form, "start_time"), _clean(form, "end_time")
    if not TIME_RE.match(start):
        errors["start_time"] = "Enter a start time."
    if not TIME_RE.match(end):
        errors["end_time"] = "Enter an end time."
    elif TIME_RE.match(start) and end <= start:
        errors["end_time"] = "End time must be after the start time."
    room = _clean(form, "room") or None

    if not errors:
        # the same instructor can't be in two classes at once
        clash = conn.execute(
            """SELECT sub.code, s.name, sc.start_time, sc.end_time
               FROM schedules sc JOIN sections s ON s.id = sc.section_id
               JOIN subjects sub ON sub.id = s.subject_id
               WHERE sub.instructor_id = ? AND sc.day_of_week = ?
                 AND sc.deleted_at IS NULL AND s.deleted_at IS NULL AND sub.deleted_at IS NULL
                 AND sub.ended_at IS NULL
                 AND sc.start_time < ? AND ? < sc.end_time AND sc.id IS NOT ?""",
            (instructor_id, day, end, start, exclude_id),
        ).fetchone()
        if clash:
            errors["start_time"] = (f"Overlaps with {clash['code']} {clash['name']} "
                                    f"({DAYS[day]} {clash['start_time']}-{clash['end_time']}).")
    if errors:
        raise ValidationError(errors)
    return day, start, end, room


def delete_schedule(conn, instructor_id, schedule_id) -> int | None:
    """Returns the section id, or None if the schedule isn't the instructor's."""
    row = _own_schedule(conn, instructor_id, schedule_id)
    if not row:
        return None
    conn.execute("UPDATE schedules SET deleted_at = ? WHERE id = ?", (_now(), schedule_id))
    audit(conn, instructor_id, "delete", "schedules", schedule_id, dict(row))
    conn.commit()
    return row["section_id"]


# ================================================================ schedule exceptions (no class)
REASON_MAX = 60
QUICK_REASONS = ("Holiday", "Suspended classes", "No class")
ASYNC_REASON = "Async"          # a class day turned into an async activity (see add_schedule_exception)


def instructor_section_ids(conn, instructor_id) -> list[int]:
    return [r["id"] for r in conn.execute(
        """SELECT s.id FROM sections s JOIN subjects sub ON sub.id = s.subject_id
           WHERE sub.instructor_id = ? AND s.deleted_at IS NULL AND sub.deleted_at IS NULL
             AND sub.ended_at IS NULL""",
        (instructor_id,))]


def _validate_exception(form):
    errors = {}
    day = _clean(form, "exception_date")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        errors["exception_date"] = "Pick a date."
        day = None
    reason = _clean(form, "reason")
    if not reason:
        errors["reason"] = "Say why, e.g. Holiday."
    elif len(reason) > REASON_MAX:
        errors["reason"] = f"Keep it under {REASON_MAX} characters."
    if errors:
        raise ValidationError(errors)
    return day, reason


def add_schedule_exception(conn, instructor_id, section_id, form, *, now=None, commit=True) -> list[int]:
    """Mark a date as 'no class' (holiday, suspended classes ...), or change its reason /
    type if it's already marked. With form['all_sections'], applies to every section this
    instructor teaches instead of just this one. Any already-created, untouched session
    from the weekly schedule on that date is cancelled (one someone already opened, or
    that has check-ins, is left alone).

    form['mark_present']: the class didn't happen, but the instructor still wants it to
    count (e.g. Saturday's class is covered together with Monday's) - everyone enrolled is
    marked present for that date, same as an async activity. Existing credit is preserved
    unless an upcoming async activity is replaced.
    Returns the section ids it was applied to."""
    now = now or datetime.now()
    # form['as_async']: this class day becomes an ASYNC ACTIVITY instead of a cancelled class -
    # students work from home, nobody checks in, everyone enrolled is marked present, and the
    # date goes in the report's Async columns (form['title'] names the activity).
    as_async = bool(form.get("as_async"))
    if as_async:
        form = {**{k: form.get(k) for k in ("exception_date", "all_sections")}, "reason": ASYNC_REASON,
                "mark_present": "1", "title": _clean(form, "title")[:80], "start_time": _clean(form, "start_time")}
        if not form["title"]:
            raise ValidationError({"title": "Give the async activity a title, e.g. Module 3 quiz."})
        if form["start_time"] and not TIME_RE.fullmatch(form["start_time"]):
            raise ValidationError({"start_time": "Enter a valid completion time."})
    day, reason = _validate_exception(form)
    mark_present = bool(form.get("mark_present"))
    section_ids = instructor_section_ids(conn, instructor_id) if form.get("all_sections") else [section_id]
    if mark_present and any(conn.execute(
            "SELECT 1 FROM schedule_exceptions WHERE section_id=? AND exception_date=? AND is_vacation=1", (sid, day)).fetchone()
            for sid in section_ids):
        raise ValidationError({"exception_date": "Remove the vacation mark before crediting attendance on this date."})
    applied = []
    for sid in section_ids:
        existing = conn.execute(
            "SELECT id, reason, mark_present, async_session_id FROM schedule_exceptions WHERE section_id = ? AND exception_date = ?",
            (sid, day)).fetchone()
        replacing_async = existing and existing["reason"] == ASYNC_REASON and reason != ASYNC_REASON
        if replacing_async:
            if not async_undo_allowed(conn, sid, day, now, activity_id=existing["async_session_id"]):
                raise ValidationError({"exception": "The class time has already passed, so this day stays async."})
            _retire_async_day(conn, existing["async_session_id"], now)
        credit_now = mark_present and not (existing and existing["mark_present"] and not replacing_async)
        if existing:
            if existing["reason"] == reason and not credit_now:
                continue                  # already marked like this - nothing to do
            sets = ["reason = ?"]
            if credit_now:
                sets.append("mark_present = 1")
            elif replacing_async:
                sets.append("mark_present = 0")
            if replacing_async:
                sets.append("async_session_id = NULL")
            conn.execute(f"UPDATE schedule_exceptions SET {', '.join(sets)} WHERE id = ?", (reason, existing["id"]))
        else:
            conn.execute(
                """INSERT INTO schedule_exceptions (section_id, exception_date, reason, created_by, mark_present)
                   VALUES (?, ?, ?, ?, ?)""", (sid, day, reason, instructor_id, int(mark_present)))
            _cancel_unused_class(conn, sid, day)
        if credit_now and as_async:
            from .sessions import create_session                    # lazy: avoid a circular import
            activity = create_session(conn, instructor_id, sid,
                                      {"type": "async", "session_date": day, "title": form["title"],
                                       "start_time": form["start_time"]}, now, commit=False)
            conn.execute("UPDATE schedule_exceptions SET async_session_id = ? WHERE section_id = ? AND exception_date = ?",
                         (activity, sid, day))
        elif credit_now:
            from .sessions import credit_present_for_no_class      # lazy: avoid a circular import
            credit_present_for_no_class(conn, instructor_id, sid, day, reason)
        applied.append(sid)
    if applied:
        audit(conn, instructor_id, "create", "schedule_exceptions", applied[0],
              {"date": day, "reason": reason, "mark_present": mark_present, "sections": applied})
    if commit:
        conn.commit()
    return applied


def add_vacation(conn, instructor_id, section_id, form):
    """Skip an inclusive vacation range, atomically, without creating attendance."""
    from datetime import timedelta
    if get_section(conn, instructor_id, section_id) is None:
        raise ValidationError({"vacation": "Section not found."})
    errors, dates = {}, {}
    for key in ("vacation_start", "vacation_end"):
        try:
            dates[key] = datetime.strptime(_clean(form, key), "%Y-%m-%d").date()
        except ValueError:
            errors[key] = "Choose a valid date."
    if not errors:
        days = (dates["vacation_end"] - dates["vacation_start"]).days + 1
        if days < 1:
            errors["vacation_end"] = "End date must be on or after the start date."
        elif days > 366:
            errors["vacation_end"] = "Choose a vacation range of up to one year."
    reason = _clean(form, "vacation_reason") or "Vacation"
    if len(reason) > REASON_MAX:
        errors["vacation_reason"] = f"Keep it under {REASON_MAX} characters."
    if errors:
        raise ValidationError(errors)
    section_ids = instructor_section_ids(conn, instructor_id) if form.get("all_sections") else [section_id]
    start, end = dates["vacation_start"].isoformat(), dates["vacation_end"].isoformat()
    placeholders = ','.join('?' for _ in section_ids)
    with conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        protected = conn.execute(f"""SELECT 1 FROM class_sessions cs
            WHERE cs.section_id IN ({placeholders}) AND cs.session_date BETWEEN ? AND ? AND cs.deleted_at IS NULL
              AND (cs.opened_at IS NOT NULL OR cs.ended_at IS NOT NULL OR EXISTS
                   (SELECT 1 FROM attendance a WHERE a.session_id=cs.id AND a.deleted_at IS NULL)) LIMIT 1""",
            (*section_ids, start, end)).fetchone()
        credited = conn.execute(f"SELECT 1 FROM schedule_exceptions WHERE section_id IN ({placeholders}) AND exception_date BETWEEN ? AND ? AND mark_present=1 LIMIT 1",
                                (*section_ids, start, end)).fetchone()
        if protected or credited:
            raise ValidationError({"vacation": "This range includes a class already opened or marked. Choose dates without recorded class activity."})
        conn.executemany("""INSERT INTO schedule_exceptions
            (section_id,exception_date,reason,created_by,is_vacation) VALUES (?,?,?,?,1)
            ON CONFLICT(section_id,exception_date) DO UPDATE SET reason=excluded.reason, is_vacation=1""",
            [(sid, (dates["vacation_start"] + timedelta(days=offset)).isoformat(), reason, instructor_id)
             for sid in section_ids for offset in range(days)])
        cancelled = conn.execute(f"""UPDATE class_sessions SET deleted_at=?
            WHERE section_id IN ({placeholders}) AND session_date BETWEEN ? AND ? AND deleted_at IS NULL""",
            (_now(), *section_ids, start, end)).rowcount
        audit(conn, instructor_id, "add_vacation", "sections", section_id,
              {"start":start,"end":end,"sections":section_ids,"cancelled_sessions":cancelled})
    return {"days": days, "sections": section_ids, "cancelled": cancelled}


def list_schedule_exceptions(conn, section_id):
    return conn.execute(
        "SELECT * FROM schedule_exceptions WHERE section_id = ? ORDER BY exception_date",
        (section_id,)).fetchall()


def _restore_cancelled_class(conn, section_id, day) -> None:
    """Bring back the weekly-schedule session that was cancelled for a no-class / async mark."""
    conn.execute("""UPDATE class_sessions SET deleted_at = NULL WHERE section_id = ? AND session_date = ?
                      AND schedule_id IS NOT NULL AND deleted_at IS NOT NULL AND opened_at IS NULL AND ended_at IS NULL
                      AND NOT EXISTS (SELECT 1 FROM attendance a WHERE a.session_id = class_sessions.id
                                      AND a.deleted_at IS NULL)""", (section_id, day))


def _cancel_unused_class(conn, section_id, day) -> None:
    conn.execute("""UPDATE class_sessions SET deleted_at = ? WHERE section_id = ? AND session_date = ?
                      AND schedule_id IS NOT NULL AND deleted_at IS NULL AND opened_at IS NULL AND ended_at IS NULL
                      AND NOT EXISTS (SELECT 1 FROM attendance a WHERE a.session_id = class_sessions.id
                                      AND a.deleted_at IS NULL)""", (_now(), section_id, day))


def _retire_async_day(conn, session_id, now):
    """Retire the activity shown for the day-wide schedule exception."""
    conn.execute(
        "UPDATE class_sessions SET deleted_at = ? WHERE id = ? AND type = 'async' AND deleted_at IS NULL",
        (now.strftime("%Y-%m-%d %H:%M:%S"), session_id))


def async_session_deleted(conn, section_id, day, session_id, now=None) -> bool:
    """The async session of an "async day" was deleted: that IS the undo, so the day's Async mark
    goes too and the day is a regular class again (while its class time hasn't passed).
    Returns True if the day went back to regular."""
    ex = conn.execute("SELECT id FROM schedule_exceptions WHERE section_id = ? AND exception_date = ? "
                      "AND reason = ? AND async_session_id = ?", (section_id, day, ASYNC_REASON, session_id)).fetchone()
    if ex is None:
        return False
    conn.execute("DELETE FROM schedule_exceptions WHERE id = ?", (ex["id"],))
    if async_undo_allowed(conn, section_id, day, now, activity_id=session_id):
        _restore_cancelled_class(conn, section_id, day)
        return True
    return False


def async_session_restored(conn, instructor_id, section_id, day, session_id, now=None) -> bool:
    """An async session came back from Recently deleted. If it falls on an upcoming class day,
    make the day an "async day" again (so no regular class runs beside it, and it can be undone
    from the Upcoming list). Returns True if it did."""
    weekday = datetime.strptime(day, "%Y-%m-%d").weekday()
    has_class = conn.execute("SELECT 1 FROM schedules WHERE section_id = ? AND day_of_week = ? AND deleted_at IS NULL",
                             (section_id, weekday)).fetchone()
    if not has_class or not async_undo_allowed(conn, section_id, day, now, activity_id=session_id):
        return False
    existing = conn.execute("SELECT is_vacation, async_session_id FROM schedule_exceptions WHERE section_id = ? AND exception_date = ?",
                            (section_id, day)).fetchone()
    if existing:
        if existing["is_vacation"] or existing["async_session_id"] not in (None, session_id):
            return False
        conn.execute("""UPDATE schedule_exceptions SET reason = ?, mark_present = 1, async_session_id = ?
                        WHERE section_id = ? AND exception_date = ?""", (ASYNC_REASON, session_id, section_id, day))
    else:
        conn.execute("""INSERT INTO schedule_exceptions (section_id, exception_date, reason, created_by, mark_present, async_session_id)
                        VALUES (?, ?, ?, ?, 1, ?)""", (section_id, day, ASYNC_REASON, instructor_id, session_id))
    _cancel_unused_class(conn, section_id, day)
    return True


def async_undo_allowed(conn, section_id, day: str, now=None, *, activity_id=None) -> bool:
    """Use the activity's chosen time, or its weekly schedule for older untimed activities."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    if day != today:
        return day > today
    if activity_id is not None:
        activity = conn.execute("SELECT type, session_date, start_time FROM class_sessions WHERE id = ? AND section_id = ?",
                                (activity_id, section_id)).fetchone()
        if activity and activity["start_time"]:
            from .sessions import is_finished
            return not is_finished(dict(activity), now)
    weekday = datetime.strptime(day, "%Y-%m-%d").weekday()
    return conn.execute("""SELECT 1 FROM schedules WHERE section_id = ? AND day_of_week = ? AND deleted_at IS NULL
                             AND start_time > ?""", (section_id, weekday, now.strftime("%H:%M"))).fetchone() is not None


def delete_schedule_exception(conn, instructor_id, exception_id, now=None) -> int | None:
    """Returns the section id, or None if it isn't the instructor's. Raises ValidationError when an
    async day's class time has already passed (it then stays an async activity)."""
    row = conn.execute(
        """SELECT se.* FROM schedule_exceptions se
           JOIN sections s ON s.id = se.section_id JOIN subjects sub ON sub.id = s.subject_id
           WHERE se.id = ? AND sub.instructor_id = ?""", (exception_id, instructor_id)).fetchone()
    if row is None:
        return None
    now = now or datetime.now()
    can_restore = async_undo_allowed(conn, row["section_id"], row["exception_date"], now, activity_id=row["async_session_id"])
    if row["reason"] == ASYNC_REASON and not can_restore:
        raise ValidationError({"exception": "The class time has already passed, so this day stays an async activity. "
                                            "Open it under Ended to change a student's mark."})
    conn.execute("DELETE FROM schedule_exceptions WHERE id = ?", (exception_id,))
    if row["reason"] == ASYNC_REASON:
        # undo "Async activity": the async session goes (with the automatic present marks), and a
        # regular session that was cancelled for it comes back, so the day is a normal class again
        _retire_async_day(conn, row["async_session_id"], now)
    if can_restore:
        _restore_cancelled_class(conn, row["section_id"], row["exception_date"])
    audit(conn, instructor_id, "delete", "schedule_exceptions", exception_id, dict(row))
    conn.commit()
    return row["section_id"]


# ================================================================ enrollment
def list_students(conn, section_id):
    return conn.execute(
        """SELECT u.id, u.student_number, u.first_name, u.middle_name, u.last_name, u.suffix,
                  u.rfid_uid IS NOT NULL AS has_card,
                  (u.signature_path IS NOT NULL AND u.must_resign = 0) AS has_signature,
                  u.must_resign, u.program, u.year_level, u.section_letter,
                  e.enrolled_at
           FROM enrollments e JOIN users u ON u.id = e.student_id
           WHERE e.section_id = ? AND e.deleted_at IS NULL AND e.status = 'active'
             AND u.deleted_at IS NULL
           ORDER BY u.last_name, u.first_name""",
        (section_id,),
    ).fetchall()


def _enroll(conn, section_id, student_id) -> str:
    """Returns 'added', 'restored' or 'already'."""
    row = conn.execute("SELECT * FROM enrollments WHERE section_id = ? AND student_id = ?",
                       (section_id, student_id)).fetchone()
    if row is None:
        conn.execute("INSERT INTO enrollments (section_id, student_id) VALUES (?, ?)", (section_id, student_id))
        return "added"
    if row["deleted_at"] is None and row["status"] == "active":
        return "already"
    conn.execute(
        # Attendance history is calculated from the original enrollment date.
        # Resetting it here hides earlier absences (and older recorded attendance).
        "UPDATE enrollments SET deleted_at = NULL, status = 'active' WHERE id = ?",
        (row["id"],),
    )
    return "restored"


def enroll_by_numbers(conn, instructor_id, section_id, text: str) -> dict:
    """Instructor pastes student numbers (one per line, or comma/space separated)."""
    numbers = [n.upper() for n in re.split(r"[\s,;]+", text or "") if n]
    result = {"added": [], "already": [], "not_found": []}
    for number in dict.fromkeys(numbers):  # keep order, drop duplicates
        student = conn.execute(
            """SELECT id FROM users WHERE upper(student_number) = ? AND role = 'student'
                 AND deleted_at IS NULL""", (number,)).fetchone()
        if not student:
            result["not_found"].append(number)
            continue
        status = _enroll(conn, section_id, student["id"])
        result["already" if status == "already" else "added"].append(number)
    conn.commit()
    log.info("enroll section=%s by instructor=%s: %s", section_id, instructor_id, result)
    return result


def leave_class(conn, student_id, section_id):
    """Leave only the student's own active enrollment, retaining attendance history."""
    with conn:
        enrollment = conn.execute(
            """SELECT e.id, s.name AS section_name, sub.code AS subject_code
               FROM enrollments e JOIN sections s ON s.id = e.section_id
               JOIN subjects sub ON sub.id = s.subject_id
               WHERE e.student_id = ? AND e.section_id = ? AND e.status = 'active'
                 AND e.deleted_at IS NULL AND s.deleted_at IS NULL AND sub.deleted_at IS NULL""",
            (student_id, section_id),
        ).fetchone()
        if enrollment is None:
            return None
        updated = conn.execute(
            "UPDATE enrollments SET status = 'dropped' WHERE id = ? AND student_id = ? AND status = 'active' AND deleted_at IS NULL",
            (enrollment["id"], student_id),
        )
        if not updated.rowcount:
            return None
        # A previous approval must not appear as a current request after leaving.
        conn.execute("DELETE FROM join_requests WHERE section_id = ? AND student_id = ?", (section_id, student_id))
        audit(conn, student_id, "leave_class", "enrollments", enrollment["id"], {"section_id": section_id})
    return dict(enrollment)


def drop_student(conn, instructor_id, section_id, student_id) -> bool:
    """Instructor drops a student (too many absences). Their records stay; they can be
    put back from the Dropped list."""
    cur = conn.execute(
        """UPDATE enrollments SET status = 'dropped' WHERE section_id = ? AND student_id = ?
             AND status = 'active' AND deleted_at IS NULL""", (section_id, student_id))
    if not cur.rowcount:
        return False
    conn.execute("DELETE FROM join_requests WHERE section_id = ? AND student_id = ?", (section_id, student_id))
    audit(conn, instructor_id, "drop_student", "enrollments", section_id, {"student_id": student_id})
    conn.commit()
    return True


def list_dropped(conn, section_id):
    """Students no longer in the class (dropped by the instructor or left by themselves)."""
    return conn.execute(
        """SELECT u.id, u.student_number, u.first_name, u.last_name
           FROM enrollments e JOIN users u ON u.id = e.student_id
           WHERE e.section_id = ? AND e.status = 'dropped' AND e.deleted_at IS NULL AND u.deleted_at IS NULL
           ORDER BY u.last_name, u.first_name""", (section_id,)).fetchall()


def restore_student(conn, instructor_id, section_id, student_id) -> bool:
    row = conn.execute("""SELECT 1 FROM enrollments WHERE section_id = ? AND student_id = ?
                            AND status = 'dropped' AND deleted_at IS NULL""", (section_id, student_id)).fetchone()
    if not row:
        return False
    _enroll(conn, section_id, student_id)
    audit(conn, instructor_id, "restore_student", "enrollments", section_id, {"student_id": student_id})
    conn.commit()
    return True


def remove_student(conn, instructor_id, section_id, student_id) -> None:
    conn.execute(
        """UPDATE enrollments SET deleted_at = ?, status = 'dropped'
           WHERE section_id = ? AND student_id = ? AND deleted_at IS NULL""",
        (_now(), section_id, student_id),
    )
    audit(conn, instructor_id, "remove_student", "enrollments", section_id, {"student_id": student_id})
    conn.commit()


# ================================================================ dashboards
def _sched_summary(conn, section_id):
    """e.g. 'Mon, Wed 07:00-09:00'"""
    def t12(hhmm):
        return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")
    rows = list_schedules(conn, section_id)
    return " · ".join(f"{r['day_name'][:3]} {t12(r['start_time'])}–{t12(r['end_time'])}" for r in rows)


def student_classes(conn, student_id):
    rows = conn.execute(
        """SELECT s.id AS section_id, s.name AS section_name, sub.code, sub.title, sub.ended_at,
                  s.drop_absences,
                  u.first_name AS prof_first, u.last_name AS prof_last, u.title AS prof_title
           FROM enrollments e
           JOIN sections s ON s.id = e.section_id
           JOIN subjects sub ON sub.id = s.subject_id
           JOIN users u ON u.id = sub.instructor_id
           WHERE e.student_id = ? AND e.status = 'active' AND e.deleted_at IS NULL
             AND s.deleted_at IS NULL AND sub.deleted_at IS NULL
           ORDER BY sub.ended_at IS NOT NULL, sub.code""",
        (student_id,),
    ).fetchall()
    # weekly schedule as a list (one class day per line in the UI)
    return [{**dict(r), "schedule": list_schedules(conn, r["section_id"])} for r in rows]


def todays_schedule(conn, *, student_id=None, instructor_id=None, today=None):
    """Classes meeting today, for the dashboard. A date marked "no class" for a section
    (holiday, suspended classes ...) is left out - there's nothing to check in to."""
    today = today or datetime.now()
    no_class = """AND NOT EXISTS (SELECT 1 FROM schedule_exceptions se
                                  WHERE se.section_id = s.id AND se.exception_date = ?)"""
    if student_id is not None:
        owner_sql = """JOIN enrollments e ON e.section_id = s.id AND e.student_id = ?
                       AND e.status = 'active' AND e.deleted_at IS NULL"""
        where_sql, args = "", (student_id, today.weekday(), today.strftime("%Y-%m-%d"))
    else:
        owner_sql, where_sql, args = "", "AND sub.instructor_id = ?", \
            (today.weekday(), instructor_id, today.strftime("%Y-%m-%d"))
    return conn.execute(
        f"""SELECT sc.start_time, sc.end_time, sc.room, s.id AS section_id, s.name AS section_name,
                   sub.code, sub.title
            FROM schedules sc
            JOIN sections s ON s.id = sc.section_id
            JOIN subjects sub ON sub.id = s.subject_id
            {owner_sql}
            WHERE sc.day_of_week = ? AND sc.deleted_at IS NULL
              AND s.deleted_at IS NULL AND sub.deleted_at IS NULL AND sub.ended_at IS NULL {where_sql} {no_class}
            ORDER BY sc.start_time""",
        args,
    ).fetchall()


def student_attendance_counts(conn, student_id):
    row = conn.execute(
        """SELECT SUM(status = 'present') AS present, SUM(status = 'late') AS late
           FROM attendance WHERE student_id = ? AND deleted_at IS NULL""", (student_id,)).fetchone()
    return {"present": row["present"] or 0, "late": row["late"] or 0}


def instructor_stats(conn, instructor_id):
    row = conn.execute(
        """SELECT COUNT(DISTINCT sub.id) AS subjects, COUNT(DISTINCT s.id) AS sections,
                  COUNT(DISTINCT e.student_id) AS students
           FROM subjects sub
           LEFT JOIN sections s ON s.subject_id = sub.id AND s.deleted_at IS NULL
           LEFT JOIN enrollments e ON e.section_id = s.id AND e.deleted_at IS NULL AND e.status = 'active'
           WHERE sub.instructor_id = ? AND sub.deleted_at IS NULL AND sub.ended_at IS NULL""", (instructor_id,)).fetchone()
    return dict(row)


def get_section_student(conn, section_id, student_id):
    """A student enrolled in this section (call get_section first to check the instructor owns it)."""
    row = conn.execute(
        """SELECT u.*, e.enrolled_at FROM enrollments e JOIN users u ON u.id = e.student_id
           WHERE e.section_id = ? AND e.student_id = ? AND e.status = 'active' AND e.deleted_at IS NULL
             AND u.deleted_at IS NULL AND u.role = 'student'""", (section_id, student_id)).fetchone()
    return dict(row) if row else None


def student_other_classes(conn, instructor_id, student_id, exclude_section_id):
    """Other sections of THIS instructor the student is enrolled in."""
    return conn.execute(
        """SELECT s.id AS section_id, s.name AS section_name, sub.code
           FROM enrollments e JOIN sections s ON s.id = e.section_id JOIN subjects sub ON sub.id = s.subject_id
           WHERE e.student_id = ? AND sub.instructor_id = ? AND s.id <> ?
             AND e.status = 'active' AND e.deleted_at IS NULL AND s.deleted_at IS NULL AND sub.deleted_at IS NULL
           ORDER BY sub.code, s.name""", (student_id, instructor_id, exclude_section_id)).fetchall()


def reset_signature(conn, instructor_id, section_id, student_id) -> bool:
    """Instructor asks a student in their section to sign again (bad/unclear signature)."""
    enrolled = conn.execute(
        """SELECT 1 FROM enrollments WHERE section_id = ? AND student_id = ?
             AND deleted_at IS NULL AND status = 'active'""", (section_id, student_id)).fetchone()
    if not enrolled:
        return False
    conn.execute("UPDATE users SET must_resign = 1 WHERE id = ?", (student_id,))
    audit(conn, instructor_id, "reset_signature", "users", student_id, {"section_id": section_id})
    conn.commit()
    log.info("signature reset for student=%s by instructor=%s", student_id, instructor_id)
    return True


def _validate_section_course(conn, name, current=None):
    parts = split_section_name(name)
    if not parts["year_level"]:
        # Legacy free-form sections require explicit instructor approval to join.
        return
    if current and split_section_name(current)["course"].upper() == parts["course"].upper():
        return
    if not conn.execute("SELECT 1 FROM courses WHERE code = ? AND is_active = 1",
                        (parts["course"],)).fetchone():
        raise ValidationError({"course": "Choose an available course. Ask the admin to add a missing course."})


def _section_for_code(conn, code):
    code = (code or "").strip().upper()
    return conn.execute(
        """SELECT s.id, s.name, s.join_approval, sub.code AS subject_code FROM sections s
           JOIN subjects sub ON sub.id = s.subject_id
           WHERE s.join_code = ? AND s.deleted_at IS NULL AND sub.deleted_at IS NULL
             AND sub.ended_at IS NULL""",
        (code,),
    ).fetchone()
    

def _join_eligibility(conn, student_id, section):
    if section is None:
        return {"ok": False, "message": "No class found with that code. Check the code with your instructor."}
    user = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'student' AND is_active = 1 AND deleted_at IS NULL",
                        (student_id,)).fetchone()
    if user is None or not user["academic_locked"] or not all((user["program"], user["year_level"], user["section_letter"])):
        return {"ok": False, "message": "Save your course, year, and section in My account before joining a class."}
    existing = conn.execute("SELECT 1 FROM enrollments WHERE section_id = ? AND student_id = ? AND status = 'active' AND deleted_at IS NULL",
                            (section["id"], student_id)).fetchone()
    if existing:
        return {"ok": False, "message": "You are already enrolled in this class."}
    return None


def _matches_section(conn, student_id, section):
    user = conn.execute("SELECT program, year_level, section_letter FROM users WHERE id = ?", (student_id,)).fetchone()
    parts = split_section_name(section["name"])
    return (bool(parts["year_level"]) and (user["program"] or "").upper() == parts["course"].upper()
            and str(user["year_level"]) == parts["year_level"]
            and (user["section_letter"] or "").upper() == parts["section_letter"])


def join_by_code(conn, student_id, code: str) -> dict:
    """Apply the section's approval policy before allowing self-enrollment."""
    section = _section_for_code(conn, code)
    error = _join_eligibility(conn, student_id, section)
    if error:
        return error
    if section["join_approval"] == "everyone":
        return _submit_join_request(conn, student_id, section)
    if section["join_approval"] != "anyone" and not _matches_section(conn, student_id, section):
        return {"ok": False, "message": f"This class is for {section['name']}. Use Request approval to ask the instructor to approve your entry."}
    status = _enroll(conn, section["id"], student_id)
    conn.commit()
    label = f"{section['subject_code']} {section['name']}"
    if status == "already":
        return {"ok": False, "message": f"You are already enrolled in {label}."}
    log.info("student=%s joined section=%s with code", student_id, section["id"])
    return {"ok": True, "message": f"You joined {label}."}


def request_join(conn, student_id, code, reason=""):
    section = _section_for_code(conn, code)
    error = _join_eligibility(conn, student_id, section)
    if error:
        return error
    if section["join_approval"] == "anyone" or (section["join_approval"] != "everyone" and _matches_section(conn, student_id, section)):
        return join_by_code(conn, student_id, code)
    return _submit_join_request(conn, student_id, section, reason)


def _submit_join_request(conn, student_id, section, reason=""):
    reason = (reason or "").strip()
    if len(reason) > 500:
        return {"ok": False, "message": "Keep your request reason within 500 characters."}
    with conn:
        cur = conn.execute("""INSERT INTO join_requests (section_id, student_id, reason)
            VALUES (?, ?, ?) ON CONFLICT(section_id, student_id) DO UPDATE SET
            reason = excluded.reason, status = 'pending', created_at = excluded.created_at,
            reviewed_by = NULL, reviewed_at = NULL WHERE join_requests.status <> 'pending'""",
            (section["id"], student_id, reason or None))
        if cur.rowcount:
            audit(conn, student_id, "request_join", "sections", section["id"])
    return {"ok": True, "message": f"Request pending for {section['subject_code']} {section['name']}. Wait for the instructor's approval."}


def section_join_requests(conn, section_id):
    return conn.execute("""SELECT r.*, u.first_name, u.last_name, u.student_number,
            u.program, u.year_level, u.section_letter
        FROM join_requests r JOIN users u ON u.id = r.student_id
        WHERE r.section_id = ? AND r.status = 'pending' AND u.is_active = 1 AND u.deleted_at IS NULL
        ORDER BY r.created_at, r.id""", (section_id,)).fetchall()


def student_join_requests(conn, student_id):
    return conn.execute("""SELECT r.*, s.name AS section_name, sub.code AS subject_code
        FROM join_requests r JOIN sections s ON s.id = r.section_id
        JOIN subjects sub ON sub.id = s.subject_id
        WHERE r.student_id = ? AND s.deleted_at IS NULL AND sub.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM enrollments e WHERE e.section_id = r.section_id
            AND e.student_id = r.student_id AND e.status = 'active' AND e.deleted_at IS NULL)
        ORDER BY r.created_at DESC""", (student_id,)).fetchall()


def review_join_request(conn, instructor_id, section_id, request_id, approve):
    if get_section(conn, instructor_id, section_id) is None:
        raise ValidationError({"request": "Section not found."})
    with conn:
        row = conn.execute("""SELECT r.* FROM join_requests r JOIN users u ON u.id = r.student_id
            WHERE r.id = ? AND r.section_id = ? AND r.status = 'pending'
              AND u.is_active = 1 AND u.deleted_at IS NULL""", (request_id, section_id)).fetchone()
        if row is None:
            raise ValidationError({"request": "This request is no longer pending."})
        # Claim the pending request before enrolling, so two reviews cannot both succeed.
        updated = conn.execute("UPDATE join_requests SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ? AND status = 'pending'",
                               ("approved" if approve else "rejected", instructor_id, _now(), request_id))
        if not updated.rowcount:
            raise ValidationError({"request": "This request was already reviewed."})
        if approve:
            _enroll(conn, section_id, row["student_id"])
        audit(conn, instructor_id, "approve_join_request" if approve else "reject_join_request", "sections", section_id,
              {"student_id": row["student_id"], "request_id": request_id})
