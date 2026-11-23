"""
Class sessions: one actual meeting of a section = one date column in the report.

Where sessions come from
  * Weekly schedule  -> ensure_sessions_for_date() creates today's sessions automatically
                        (called on every check-in / status check, safe to repeat).
  * "Start attendance now" -> start_now(): for when the schedule changed. Opens check-in
                        immediately (reuses today's unfinished session if there is one).
  * "Add session"    -> create_session(): make-up class, async activity, or a past date.

Instructor controls (set_state): auto | present | late | disabled  - works any time.

Absent = enrolled (on or before that date), no record, and the session has FINISHED:
  * an earlier date, or
  * today + auto mode + check-in window already closed, or
  * today + manual mode + it was opened at some point and is now closed.
"""
import logging
import re
import secrets
from datetime import date, datetime

from .accounts import ValidationError
from .checkin_state import get_checkin_state, get_checkin_window

log = logging.getLogger(__name__)

TYPES = ("regular", "makeup", "async")
TYPE_LABELS = {"regular": "Regular", "makeup": "Make-up", "async": "Async"}
STATUSES = ("present", "late", "absent", "excused")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _ts(dt=None):
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _audit(conn, user_id, action, entity_id, details=None):
    from .classes import audit
    audit(conn, user_id, action, "class_sessions", entity_id, details)


# ================================================================ generation
def ensure_sessions_for_date(conn, day: date | None = None) -> int:
    """Create sessions for every schedule that meets on `day` (default today).
    A session that was deleted (class cancelled) is NOT re-created, and a date marked
    "no class" (holiday, suspended classes ...) in schedule_exceptions is skipped too."""
    day = day or date.today()
    cur = conn.execute(
        """INSERT INTO class_sessions
               (section_id, schedule_id, session_date, start_time, end_time, type, checkin_mode,
                open_before_minutes, grace_minutes, close_after_minutes)
           SELECT sc.section_id, sc.id, ?, sc.start_time, sc.end_time, 'regular', s.default_checkin_mode,
                  s.default_open_before_minutes, s.default_grace_minutes, s.default_close_after_minutes
           FROM schedules sc
           JOIN sections s ON s.id = sc.section_id
           JOIN subjects sub ON sub.id = s.subject_id
           WHERE sc.day_of_week = ? AND sc.deleted_at IS NULL
             AND s.deleted_at IS NULL AND sub.deleted_at IS NULL AND sub.ended_at IS NULL
             AND NOT EXISTS (SELECT 1 FROM class_sessions cs
                             WHERE cs.schedule_id = sc.id AND cs.session_date = ?)
             AND NOT EXISTS (SELECT 1 FROM schedule_exceptions se
                             WHERE se.section_id = sc.section_id AND se.exception_date = ?)""",
        (day.isoformat(), day.weekday(), day.isoformat(), day.isoformat()),
    )
    if cur.rowcount:
        conn.commit()
        log.info("created %s session(s) for %s from schedules", cur.rowcount, day)
    finish_overdue_checkouts(conn, day)
    return cur.rowcount


# ================================================================ lookups
SESSION_SELECT = """
    SELECT cs.*, s.name AS section_name, s.id AS section_id, sub.code AS subject_code,
           sub.title AS subject_title, sub.instructor_id, sub.ended_at AS subject_ended_at,
           COALESCE(cs.start_time, sc.start_time) AS regular_start_time,
           COALESCE(cs.end_time, sc.end_time) AS regular_end_time
    FROM class_sessions cs
    JOIN sections s ON s.id = cs.section_id
    JOIN subjects sub ON sub.id = s.subject_id
    LEFT JOIN schedules sc ON sc.id = cs.schedule_id
"""


def get_session(conn, instructor_id, session_id):
    row = conn.execute(
        SESSION_SELECT + """ WHERE cs.id = ? AND sub.instructor_id = ? AND cs.deleted_at IS NULL
                             AND s.deleted_at IS NULL AND sub.deleted_at IS NULL""",
        (session_id, instructor_id)).fetchone()
    return dict(row) if row else None


def is_finished(session: dict, now: datetime) -> bool:
    """Use the scheduled completion time for async and the check-in state for timed classes."""
    today = now.strftime("%Y-%m-%d")
    if session.get("type") == "async":
        if session["session_date"] != today:
            return session["session_date"] < today
        start = session.get("start_time")
        return not start or now.strftime("%H:%M") >= start
    if session.get("ended_at"):
        return True
    # check-in open by hand right now (e.g. an older class date reopened): not finished, so
    # students without a record show "—" (not yet), not Absent, until it's closed again
    if session["checkin_mode"] == "manual" and session.get("manual_state") in ("present", "late"):
        return False
    if session["session_date"] < today:
        return True
    if session["session_date"] > today:
        return False
    if session["checkin_mode"] == "auto":
        return now >= get_checkin_window(session)["closes_at"]
    return False                          # manual: finished only when the instructor ends it


def any_open_for_checkin(conn, now=None) -> bool:
    """Is any class (of any instructor) taking check-ins right now? The device clock is not
    corrected automatically while one is: present / late cut-offs must not move mid-class."""
    now = now or datetime.now()
    rows = conn.execute(
        SESSION_SELECT + """ WHERE cs.session_date = ? AND cs.deleted_at IS NULL AND cs.ended_at IS NULL
                             AND s.deleted_at IS NULL AND sub.deleted_at IS NULL
                             AND sub.ended_at IS NULL""",
        (now.strftime("%Y-%m-%d"),)).fetchall()
    return any(get_checkin_state(dict(r), now) != "disabled" for r in rows)


def describe(session: dict, now: datetime) -> dict:
    """State info for the UI."""
    state = get_checkin_state(session, now)
    finished = is_finished(session, now)
    if session.get("type") == "async":
        return {"state": "disabled", "phase": "ended" if finished else "upcoming",
                "finished": finished, "window": None}
    if state != "disabled":
        phase = state                      # present | late  (open right now)
    elif session.get("checkout_open_at") and not finished:
        phase = "checkout"                 # check-in closed, students tap / press to check out
    elif finished:
        phase = "ended"
    elif session["checkin_mode"] == "manual" and session.get("opened_at"):
        phase = "paused"                   # closed by hand but not ended (dev page)
    else:
        phase = "upcoming"
    return {"state": state, "phase": phase, "finished": finished,
            "window": get_checkin_window(session) if session["checkin_mode"] == "auto" else None}


def can_set_async(session, now):
    """'Async activity…' is offered until the session ends (also while check-in is open: whoever
    already checked in keeps their status, everyone else is marked present). Not during
    check-out or after the end - Reset statuses first if the class should become async."""
    return (session["type"] != "async" and not session.get("subject_ended_at")
            and describe(session, now)["phase"] in ("upcoming", "present", "late", "paused"))


def _counts_for_session(conn, session, now):
    """present / late / excused / absent / pending / total for one session."""
    rows = roster(conn, session, now)
    c = {"present": 0, "late": 0, "excused": 0, "absent": 0, "pending": 0, "total": len(rows)}
    for r in rows:
        c[r["status"]] += 1
    return c


def list_section_sessions(conn, section_id, now=None):
    now = now or datetime.now()
    rows = conn.execute(
        SESSION_SELECT + """ WHERE cs.section_id = ? AND cs.deleted_at IS NULL
                             ORDER BY cs.session_date DESC, cs.start_time DESC""", (section_id,)).fetchall()
    out = []
    for r in rows:
        s = dict(r)
        out.append({**s, **describe(s, now), "counts": _counts_for_session(conn, s, now)})
    return out


def todays_sessions(conn, instructor_id, now=None):
    now = now or datetime.now()
    ensure_sessions_for_date(conn, now.date())
    rows = conn.execute(
        SESSION_SELECT + """ WHERE sub.instructor_id = ? AND cs.session_date = ? AND cs.deleted_at IS NULL
                             AND s.deleted_at IS NULL AND sub.deleted_at IS NULL AND sub.ended_at IS NULL
                             ORDER BY cs.start_time IS NULL, cs.start_time""",
        (instructor_id, now.strftime("%Y-%m-%d"))).fetchall()
    out = []
    for r in rows:
        s = dict(r)
        out.append({**s, **describe(s, now), "counts": _counts_for_session(conn, s, now)})
    return out


def upcoming_from_schedule(conn, section_id, now=None, days=28, limit=5):
    """The next class days from the weekly schedule (sessions are only created on the day
    itself, so these don't exist yet). Today counts if the class hasn't started and its
    session wasn't already created or cancelled. A date marked "no class" (holiday,
    suspended classes ...) shows up as a no_class entry instead of a regular class day,
    so the instructor can see why nothing was created there."""
    from datetime import timedelta
    from .classes import async_undo_allowed
    now = now or datetime.now()
    if conn.execute("""SELECT 1 FROM sections s JOIN subjects sub ON sub.id = s.subject_id
                       WHERE s.id = ? AND sub.ended_at IS NOT NULL""", (section_id,)).fetchone():
        return []                                  # semester ended: nothing is coming up
    scheds = conn.execute(
        """SELECT * FROM schedules WHERE section_id = ? AND deleted_at IS NULL
           ORDER BY day_of_week, start_time""", (section_id,)).fetchall()
    exceptions = {r["exception_date"]: r for r in conn.execute(
        "SELECT id, exception_date, reason, mark_present, async_session_id FROM schedule_exceptions WHERE section_id = ?",
        (section_id,))}
    first_day = now.date().isoformat()
    last_day = (now.date() + timedelta(days=days)).isoformat()
    materialized = {(r["schedule_id"], r["session_date"]) for r in conn.execute(
        """SELECT schedule_id, session_date FROM class_sessions WHERE section_id = ? AND schedule_id IS NOT NULL
           AND session_date BETWEEN ? AND ?""", (section_id, first_day, last_day))}
    out = []
    for offset in range(days + 1):
        day = (now + timedelta(days=offset)).date()
        ex = exceptions.get(day.isoformat())
        day_schedules = [sc for sc in scheds if sc["day_of_week"] == day.weekday()]
        if ex and ex["mark_present"]:
            if ex["reason"] == "Async":
                made = conn.execute(
                    "SELECT id, title, start_time FROM class_sessions WHERE id = ? AND type = 'async' AND deleted_at IS NULL",
                    (ex["async_session_id"],)).fetchone()
            else:
                made = conn.execute(
                    "SELECT id FROM class_sessions WHERE section_id = ? AND session_date = ? AND deleted_at IS NULL",
                    (section_id, day.isoformat())).fetchone()
            if made and ex["reason"] == "Async":
                # Keep the projected row editable until the activity's completion time.
                if not async_undo_allowed(conn, section_id, day.isoformat(), now, activity_id=made["id"]):
                    continue
                out.append({"date": day.isoformat(), "start_time": made["start_time"], "end_time": None, "room": None,
                            "days_away": offset, "no_class": True, "async_day": True, "reason": ex["reason"],
                            "exception_id": ex["id"], "async_title": made["title"], "async_session_id": made["id"]})
                if limit is not None and len(out) >= limit:
                    return out
                continue
            if made:
                continue                          # already has a real "everyone present" session - it shows on its own
        for sc in day_schedules or ([None] if ex else []):
            if not ex and ((sc["id"], day.isoformat()) in materialized
                           or (offset == 0 and sc["start_time"] <= now.strftime("%H:%M"))):
                continue                              # already created, cancelled, or past its start today
            if ex:
                out.append({"date": day.isoformat(), "start_time": None, "end_time": None,
                            "room": sc["room"] if sc else None, "days_away": offset, "no_class": True,
                            "has_weekly_class": sc is not None,
                            "reason": ex["reason"], "exception_id": ex["id"]})
            else:
                out.append({"date": day.isoformat(), "start_time": sc["start_time"], "end_time": sc["end_time"],
                            "room": sc["room"], "days_away": offset, "no_class": False})
            if limit is not None and len(out) >= limit:
                return out
    return out


def attendance_overview(conn, section_id, now=None, *, show_all=False) -> dict:
    """Attendance tab, split into three groups so nothing is mixed:
         open_now  - check-in open right now
         upcoming  - created sessions that haven't started + class days from the schedule
                     (soonest first)
         ended     - finished sessions (newest first)"""
    now = now or datetime.now()
    created = list_section_sessions(conn, section_id, now)
    today = now.date()

    scheduled = upcoming_from_schedule(conn, section_id, now, limit=None)
    async_day_ids = {entry["async_session_id"] for entry in scheduled if entry.get("async_day")}
    open_now = [s for s in created if s["phase"] in ("present", "late", "checkout")]
    ended = [s for s in created if s["phase"] == "ended" and s["id"] not in async_day_ids]
    upcoming = []
    for s in created:
        if s["phase"] in ("upcoming", "paused") and s["id"] not in async_day_ids:
            d = datetime.strptime(s["session_date"], "%Y-%m-%d").date()
            upcoming.append({**s, "date": s["session_date"], "days_away": (d - today).days, "from_schedule": False})
    for u in scheduled:
        if not any(x["date"] == u["date"] and x.get("start_time") == u["start_time"]
                   and bool(x.get("no_class")) == bool(u.get("no_class")) for x in upcoming):
            extra = {} if u.get("no_class") else {"type": "regular", "title": None}
            upcoming.append({**u, **extra, "from_schedule": True})
    upcoming.sort(key=lambda x: (x["date"], x.get("start_time") or "99:99"))
    return {"open_now": open_now, "upcoming": upcoming if show_all else upcoming[:4],
            "upcoming_total": len(upcoming), "ended": ended}


def when_label(days_away: int) -> str:
    return "Today" if days_away == 0 else "Tomorrow" if days_away == 1 else f"In {days_away} days"


# ================================================================ create
def _new_session(conn, section_id, *, day, start, end, type_, title, mode, state, created_by, now):
    exception = conn.execute("SELECT * FROM schedule_exceptions WHERE section_id=? AND exception_date=?", (section_id, day)).fetchone()
    if exception and dict(exception).get("is_vacation"):
        raise ValidationError({"session_date": "This date is on vacation. Remove its vacation mark in Schedule before adding a class."})
    sec = conn.execute("SELECT * FROM sections WHERE id = ?", (section_id,)).fetchone()
    cur = conn.execute(
        """INSERT INTO class_sessions
               (section_id, session_date, start_time, end_time, type, title, checkin_mode, manual_state,
                open_before_minutes, grace_minutes, close_after_minutes, created_by, opened_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (section_id, day, start, end, type_, title, mode, state,
         sec["default_open_before_minutes"], sec["default_grace_minutes"], sec["default_close_after_minutes"],
         created_by, _ts(now) if state != "disabled" else None, _ts(now)),
    )
    return cur.lastrowid


def todays_open_candidate(conn, section_id, now=None):
    """Today's (non-async) session of this section that hasn't ended yet, or None."""
    now = now or datetime.now()
    ensure_sessions_for_date(conn, now.date())
    for row in conn.execute(
        """SELECT * FROM class_sessions WHERE section_id = ? AND session_date = ? AND deleted_at IS NULL
             AND type <> 'async' ORDER BY start_time""", (section_id, now.strftime("%Y-%m-%d"))).fetchall():
        if not is_finished(dict(row), now):
            return dict(row)
    return None


def start_now(conn, instructor_id, section_id, now=None) -> int | None:
    """'Start attendance': open TODAY'S EXISTING session as PRESENT right now (schedule changed,
    class started early...). Never adds a report column - returns None if there is no session
    today; the instructor then uses add_column_now() on purpose."""
    now = now or datetime.now()
    s = todays_open_candidate(conn, section_id, now)
    if s is None:
        return None
    set_state(conn, instructor_id, s["id"], "present", now)
    return s["id"]


def add_column_now(conn, instructor_id, section_id, now=None) -> int:
    """Deliberately add a NEW session (= new date column in the report) starting now, and open it.
    Refused (nothing is added) while another of the instructor's classes is open."""
    now = now or datetime.now()
    _one_at_a_time(conn, instructor_id, None, now)
    sid = _new_session(conn, section_id, day=now.strftime("%Y-%m-%d"), start=now.strftime("%H:%M"),
                       end=None, type_="regular", title=None, mode="manual", state="present",
                       created_by=instructor_id, now=now)
    _audit(conn, instructor_id, "add_column_now", sid)
    conn.commit()
    log.info("new session column added + opened: section=%s session=%s", section_id, sid)
    return sid


def has_records(conn, session_id) -> bool:
    return conn.execute("SELECT 1 FROM attendance WHERE session_id = ? AND deleted_at IS NULL LIMIT 1",
                        (session_id,)).fetchone() is not None


def create_session(conn, instructor_id, section_id, form, now=None, *, commit=True) -> int:
    """Add a make-up / async / extra session on any date."""
    now = now or datetime.now()
    errors = {}
    day = (form.get("session_date") or "").strip()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        errors["session_date"] = "Pick a date."
    type_ = form.get("type") or "regular"
    if type_ not in TYPES:
        errors["type"] = "Choose a type."
    start = (form.get("start_time") or "").strip() or None
    end = (form.get("end_time") or "").strip() or None
    mode = "manual" if type_ == "async" else (form.get("checkin_mode") or "auto")
    if mode not in ("auto", "manual"):
        errors["checkin_mode"] = "Choose Automatic or Manual."
    if type_ == "async":
        if start and not TIME_RE.fullmatch(start):
            errors["start_time"] = "Enter a valid completion time."
    else:
        if not start or not TIME_RE.fullmatch(start):
            errors["start_time"] = "Enter the start time."
        if end and (not TIME_RE.match(end) or (start and end <= start)):
            errors["end_time"] = "End time must be after the start time."
    title = (form.get("title") or "").strip()[:80] or None
    if type_ == "async" and not title:
        errors["title"] = "Give the async activity a name, e.g. Module 3 quiz."
    if errors:
        raise ValidationError(errors)

    sid = _new_session(conn, section_id, day=day, start=start,
                       end=end if type_ != "async" else None, type_=type_, title=title, mode=mode,
                       state="disabled", created_by=instructor_id, now=now)
    if type_ == "async":
        _auto_present(conn, instructor_id, sid, section_id, now, note="async")
    else:
        # one class at a time: if its class time would open it right now while another class is
        # open, it's added but NOT opened (manual, not opened yet) - open it after ending the other
        new = get_session(conn, instructor_id, sid)
        if new and describe(new, now)["phase"] in OPEN_PHASES and other_open_session(conn, instructor_id, sid, now):
            conn.execute("UPDATE class_sessions SET checkin_mode = 'manual', manual_state = 'disabled' WHERE id = ?",
                         (sid,))
    _audit(conn, instructor_id, "create", sid, {"date": day, "type": type_})
    if commit:
        conn.commit()
    return sid


def _auto_present(conn, instructor_id, session_id, section_id, now, note) -> int:
    """Enrolled students without a record are marked PRESENT and the session is closed.
    Used for async activities (students work from home) and for a "no class, but still
    mark everyone present" day. The instructor can still change anyone by hand afterwards."""
    cur = conn.execute(
        """INSERT INTO attendance (session_id, student_id, status, method, checked_in_at, marked_by, note)
           SELECT ?, e.student_id, 'present', 'manual', ?, ?, ?
           FROM enrollments e JOIN users u ON u.id = e.student_id
           WHERE e.section_id = ? AND e.status = 'active' AND e.deleted_at IS NULL AND u.deleted_at IS NULL
           ON CONFLICT(session_id, student_id) DO UPDATE SET
               status = excluded.status, method = excluded.method, checked_in_at = excluded.checked_in_at,
               marked_by = excluded.marked_by, note = excluded.note, deleted_at = NULL,
               signature_path = NULL, ip_address = NULL, device_info = NULL
           WHERE attendance.deleted_at IS NOT NULL""",
        (session_id, _ts(now), instructor_id, note, section_id))
    conn.execute("UPDATE class_sessions SET opened_at = ?, ended_at = ? WHERE id = ?",
                 (_ts(now), _ts(now), session_id))
    log.info("session=%s (%s): %s student(s) auto-marked present", session_id, note, cur.rowcount)
    return cur.rowcount


def set_async(conn, instructor_id, session_id, title, now=None):
    """Convert one upcoming session, keeping its ID and existing attendance."""
    title = (title or "").strip()[:80]
    if not title:
        raise ValidationError({"title": "Give the async activity a title."})

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        now = now or datetime.now()
        session = get_session(conn, instructor_id, session_id)
        if session is None or not can_set_async(session, now):
            raise ValidationError({"session": "Only sessions that haven't ended can be changed to async."})
        conn.execute(
            """UPDATE class_sessions SET type = 'async', title = ?,
                                        checkin_mode = 'manual', manual_state = 'disabled', checkin_code = NULL
               WHERE id = ?""", (title, session_id))
        count = _auto_present(conn, instructor_id, session_id, session["section_id"], now, note="async")
        _audit(conn, instructor_id, "set_async", session_id, {"title": title, "marked_present": count})


def change_upcoming_async(conn, instructor_id, session_id, form, now=None):
    """Switch an async activity back to Regular (any time - it undoes "Async activity…"), or
    cancel an UPCOMING async activity's class date (holiday, suspended classes ...)."""
    from . import classes

    choice = form.get("choice")
    if choice != "regular" and choice not in classes.QUICK_REASONS:
        raise ValidationError({"session": "Choose Regular, Holiday, Suspended classes or No class."})

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        now = now or datetime.now()
        session = get_session(conn, instructor_id, session_id)
        if session is None or session["type"] != "async" or session["subject_ended_at"]:
            raise ValidationError({"session": "Only async activities can be changed back."})
        if choice != "regular" and is_finished(session, now):
            raise ValidationError({"session": "Only upcoming async activities can be cancelled."})

        day = session["session_date"]
        section_id = session["section_id"]
        if choice == "regular":
            start = (form.get("start_time") or "").strip()
            end = (form.get("end_time") or "").strip() or None
            if not TIME_RE.fullmatch(start):
                raise ValidationError({"start_time": "Enter the start time for the regular session."})
            if end and (not TIME_RE.fullmatch(end) or end <= start):
                raise ValidationError({"end_time": "End time must be after the start time."})
            # class time still ahead: automatic as usual. Already passed: "not opened yet" (manual),
            # so nobody turns Absent the moment it's changed back - open check-in when ready.
            ahead = (day > now.strftime("%Y-%m-%d")
                     or (day == now.strftime("%Y-%m-%d") and start > now.strftime("%H:%M")))
            conn.execute(
                """UPDATE class_sessions SET type = 'regular', start_time = ?, end_time = ?,
                                              opened_at = NULL, ended_at = NULL, checkin_mode = ?,
                                              manual_state = 'disabled'
                   WHERE id = ?""", (start, end, "auto" if ahead else "manual", session_id))
            conn.execute("DELETE FROM attendance WHERE session_id = ? AND note = 'async'", (session_id,))
        else:
            conn.execute("UPDATE class_sessions SET deleted_at = ? WHERE id = ?", (_ts(now), session_id))
            classes.add_schedule_exception(conn, instructor_id, section_id,
                                           {"exception_date": day, "reason": choice}, now=now, commit=False)
        _audit(conn, instructor_id, "change_async", session_id, {"choice": choice})


def credit_present_for_no_class(conn, instructor_id, section_id, day, reason, now=None) -> int:
    """A cancelled class day (holiday, suspended classes ...) that the instructor still wants
    to count: everyone enrolled is marked present, as if it had been held. Adds a date column
    to the report, same idea as an async activity. Returns the new session id."""
    now = now or datetime.now()
    sid = _new_session(conn, section_id, day=day, start=None, end=None, type_="regular", title=reason,
                       mode="manual", state="disabled", created_by=instructor_id, now=now)
    _auto_present(conn, instructor_id, sid, section_id, now, note="no_class")
    log.info("no-class day %s credited as present: section=%s session=%s", day, section_id, sid)
    return sid


# ================================================================ controls
def _log_change(conn, session_id, user_id, mode, state):
    conn.execute("""INSERT INTO session_state_changes (session_id, changed_by, checkin_mode, manual_state)
                    VALUES (?, ?, ?, ?)""", (session_id, user_id, mode, state))


OPEN_PHASES = ("present", "late", "checkout")


def _instructor_of(conn, session_id):
    row = conn.execute("""SELECT sub.instructor_id FROM class_sessions cs
                          JOIN sections s ON s.id = cs.section_id
                          JOIN subjects sub ON sub.id = s.subject_id WHERE cs.id = ?""", (session_id,)).fetchone()
    return row[0] if row else None


def other_open_session(conn, instructor_id, exclude_id=None, now=None):
    """One class at a time: the instructor's session (other than exclude_id) that is taking
    check-ins or check-outs right now, or None. Looks at every date, so an older class
    reopened by hand counts too."""
    now = now or datetime.now()
    rows = conn.execute(
        SESSION_SELECT + """ WHERE sub.instructor_id = ? AND cs.id <> ?
                             AND cs.deleted_at IS NULL AND cs.ended_at IS NULL AND cs.type <> 'async'
                             AND s.deleted_at IS NULL AND sub.deleted_at IS NULL AND sub.ended_at IS NULL
                             AND ((cs.checkin_mode = 'manual' AND cs.manual_state IN ('present', 'late'))
                                  OR cs.checkout_open_at IS NOT NULL
                                  OR (cs.checkin_mode = 'auto' AND cs.session_date = ?))
                             ORDER BY cs.session_date DESC, cs.start_time DESC""",
        (instructor_id, exclude_id or 0, now.strftime("%Y-%m-%d"))).fetchall()
    for r in rows:
        row = dict(r)
        if describe(row, now)["phase"] in OPEN_PHASES:
            return row
    return None


def _one_at_a_time(conn, instructor_id, exclude_id, now):
    other = other_open_session(conn, instructor_id, exclude_id, now)
    if other:
        day = datetime.strptime(other["session_date"], "%Y-%m-%d").strftime("%a, %b %d").replace(" 0", " ")
        if other.get("start_time"):                 # same section twice in a day: the time tells them apart
            day += ", " + datetime.strptime(other["start_time"], "%H:%M").strftime("%I:%M %p").lstrip("0")
        raise ValidationError({
            "state": f"{other['subject_code']} · {other['section_name']} ({day}) is still open. "
                     "Only one class can take check-ins at a time: end that one first.",
            "open_session_id": other["id"]})


def set_state(conn, user_id, session_id, choice, now=None) -> None:
    """Live controls. choice: present | late (open / reopen - takes manual control)
                              disabled    (pause without ending - dev page only)
                              auto        (same as set_mode('auto'))
    Opening is refused while another of the instructor's classes is open (one at a time)."""
    if choice == "auto":
        return set_mode(conn, user_id, session_id, "auto", now)
    if choice not in ("present", "late", "disabled"):
        raise ValueError(choice)
    now = now or datetime.now()
    if choice in ("present", "late"):
        _one_at_a_time(conn, _instructor_of(conn, session_id), session_id, now)
    s = conn.execute("SELECT * FROM class_sessions WHERE id = ?", (session_id,)).fetchone()
    opened_at = s["opened_at"] or (_ts(now) if choice in ("present", "late") else None)
    ended_at = None if choice in ("present", "late") else s["ended_at"]     # opening = reopen
    # opening check-in again also closes check-out (back to the check-in phase)
    checkout_open_at = None if choice in ("present", "late") else s["checkout_open_at"]
    updated = conn.execute("""UPDATE class_sessions SET checkin_mode = 'manual', manual_state = ?, opened_at = ?,
                              ended_at = ?, checkout_open_at = ? WHERE id = ? AND type <> 'async'""",
                           (choice, opened_at, ended_at, checkout_open_at, session_id))
    if not updated.rowcount:
        conn.rollback()
        return
    _log_change(conn, session_id, user_id, "manual", choice)
    conn.commit()
    log.info("session=%s -> manual/%s by user=%s", session_id, choice, user_id)


def end_session(conn, user_id, session_id, now=None) -> None:
    """'End session': close check-in for good. Students without a record become absent.
    Nothing (not even automatic mode) reopens it - only an explicit Reopen."""
    now = now or datetime.now()
    s = conn.execute("SELECT * FROM class_sessions WHERE id = ?", (session_id,)).fetchone()
    updated = conn.execute("""UPDATE class_sessions SET checkin_mode = 'manual', manual_state = 'disabled',
                              opened_at = COALESCE(opened_at, ?), ended_at = ? WHERE id = ? AND type <> 'async'""",
                           (s["start_time"] and f'{s["session_date"]} {s["start_time"]}:00' or _ts(now), _ts(now), session_id))
    if not updated.rowcount:
        conn.rollback()
        return
    _log_change(conn, session_id, user_id, "manual", "ended")
    conn.commit()
    log.info("session=%s ended by user=%s", session_id, user_id)
    apply_checkout_rule(conn, session_id, user_id, now)


def reset_session(conn, user_id, session_id, now=None) -> int:
    """'Reset statuses': undo a mistake (ended too early, wrong button ...). Every mark and
    check-in of this class date is cleared, so every student shows "—" again, and the session goes
    back to "not opened yet" (manual) so the instructor can open check-in again.
    Records are soft-deleted (deleted_at): a student who checks in again gets a clean record.
    Async activities are not reset (their Present marks are the whole point).
    Returns how many records were cleared."""
    now = now or datetime.now()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        s = conn.execute("SELECT type FROM class_sessions WHERE id = ? AND deleted_at IS NULL",
                         (session_id,)).fetchone()
        if s is None or s["type"] == "async":
            return 0
        cleared = conn.execute(
            """UPDATE attendance SET deleted_at = ?, checked_out_at = NULL, note = NULL
               WHERE session_id = ? AND deleted_at IS NULL""", (_ts(now), session_id)).rowcount
        conn.execute("""UPDATE class_sessions SET checkin_mode = 'manual', manual_state = 'disabled',
                          opened_at = NULL, ended_at = NULL, checkout_open_at = NULL, checkout_done_at = NULL
                        WHERE id = ?""", (session_id,))
        _log_change(conn, session_id, user_id, "manual", "reset")
        _audit(conn, user_id, "reset_session", session_id, {"records_cleared": cleared})
    log.info("session=%s reset by user=%s (%s records cleared)", session_id, user_id, cleared)
    return cleared


# ================================================================ check-out
# Stops "check in from outside the room, then leave": the instructor requires check-out for a
# session, opens it near the end of class (only then do taps / the web button count as check-out),
# and whoever checked in but didn't check out is marked ABSENT when the session ends. If check-out
# was required but never opened, nobody could check out, so nobody is marked absent for it.
NO_CHECKOUT_NOTE = "No check-out"


def set_require_checkout(conn, user_id, session_id, required: bool) -> None:
    s = conn.execute("SELECT * FROM class_sessions WHERE id = ?", (session_id,)).fetchone()
    if s is None or s["type"] == "async":
        return
    if s["ended_at"]:
        raise ValidationError({"state": "This session has ended. Reopen it first to change check-out."})
    conn.execute("""UPDATE class_sessions SET require_checkout = ?,
                    checkout_open_at = CASE WHEN ? THEN checkout_open_at END WHERE id = ?""",
                 (1 if required else 0, 1 if required else 0, session_id))
    _audit(conn, user_id, "require_checkout" if required else "no_checkout", session_id)
    conn.commit()


def open_checkout(conn, user_id, session_id, now=None) -> None:
    """'Open check-out': check-in closes, taps and the web button now check students out."""
    now = now or datetime.now()
    s = conn.execute("SELECT * FROM class_sessions WHERE id = ?", (session_id,)).fetchone()
    if s is None or s["type"] == "async":
        return
    if s["ended_at"]:
        raise ValidationError({"state": "This session has ended. Reopen it first."})
    conn.execute("""UPDATE class_sessions SET require_checkout = 1, checkout_open_at = ?,
                           checkin_mode = 'manual', manual_state = 'disabled',
                           opened_at = COALESCE(opened_at, ?) WHERE id = ?""",
                 (_ts(now), _ts(now), session_id))
    _log_change(conn, session_id, user_id, "manual", "checkout")
    conn.commit()
    log.info("session=%s check-out opened by user=%s", session_id, user_id)


def apply_checkout_rule(conn, session_id, user_id=None, now=None) -> int:
    """Checked in but never checked out -> absent (once per session). Returns how many."""
    now = now or datetime.now()
    s = conn.execute("SELECT * FROM class_sessions WHERE id = ?", (session_id,)).fetchone()
    if not (s and s["require_checkout"] and s["checkout_open_at"] and not s["checkout_done_at"]):
        return 0
    cur = conn.execute("""UPDATE attendance SET status = 'absent', note = ?
                          WHERE session_id = ? AND deleted_at IS NULL
                            AND status IN ('present', 'late') AND checked_out_at IS NULL""",
                       (NO_CHECKOUT_NOTE, session_id))
    conn.execute("UPDATE class_sessions SET checkout_done_at = ? WHERE id = ?", (_ts(now), session_id))
    _audit(conn, user_id, "checkout_absent", session_id, {"students": cur.rowcount})
    conn.commit()
    if cur.rowcount:
        log.info("session=%s: %s student(s) absent - no check-out", session_id, cur.rowcount)
    return cur.rowcount


def finish_overdue_checkouts(conn, today: date | None = None) -> None:
    """Check-out was opened but the instructor never pressed End session: apply the rule
    once the class date has passed (runs with the daily session generation)."""
    today = today or date.today()
    rows = conn.execute("""SELECT id FROM class_sessions WHERE checkout_open_at IS NOT NULL
                             AND checkout_done_at IS NULL AND require_checkout = 1
                             AND deleted_at IS NULL AND session_date < ?""",
                        (today.isoformat(),)).fetchall()
    for r in rows:
        apply_checkout_rule(conn, r["id"])


def set_mode(conn, user_id, session_id, mode, now=None) -> None:
    """Session settings: 'auto' (follow the class time) or 'manual' (instructor's buttons).
    Switching to manual keeps whatever state check-in is in right now, so nothing jumps."""
    now = now or datetime.now()
    s = dict(conn.execute("SELECT * FROM class_sessions WHERE id = ?", (session_id,)).fetchone())
    if mode not in ("auto", "manual"):
        raise ValueError(mode)
    if s["ended_at"]:
        raise ValidationError({"state": "This session has ended. Reopen it first if you need to change the mode."})
    if mode == "auto":
        if not s["start_time"]:
            raise ValidationError({"state": "This session has no start time, so it can't run automatically."})
        # automatic would open it right now (inside its class time): one class at a time
        if get_checkin_state({**s, "checkin_mode": "auto"}, now) != "disabled":
            _one_at_a_time(conn, _instructor_of(conn, session_id), session_id, now)
        conn.execute("UPDATE class_sessions SET checkin_mode = 'auto', manual_state = 'disabled' WHERE id = ?",
                     (session_id,))
        _log_change(conn, session_id, user_id, "auto", "disabled")
    else:
        current = get_checkin_state(s, now)
        conn.execute("""UPDATE class_sessions SET checkin_mode = 'manual', manual_state = ?,
                               opened_at = COALESCE(opened_at, ?) WHERE id = ?""",
                     (current, _ts(now) if current != "disabled" else None, session_id))
        _log_change(conn, session_id, user_id, "manual", current)
    conn.commit()


def set_code(conn, session_id, enabled: bool) -> str | None:
    """Require a 4-digit code for WEB check-in (shown on the instructor's screen). RFID never needs it."""
    code = f"{secrets.randbelow(10000):04d}" if enabled else None
    conn.execute("UPDATE class_sessions SET checkin_code = ? WHERE id = ?", (code, session_id))
    conn.commit()
    return code


def delete_session(conn, instructor_id, session_id) -> None:
    s = conn.execute("SELECT * FROM class_sessions WHERE id = ?", (session_id,)).fetchone()
    conn.execute("UPDATE class_sessions SET deleted_at = ? WHERE id = ?", (_ts(), session_id))
    if s["type"] == "async" and s["schedule_id"] is None:
        # deleting the async session of an "async day" undoes the day: back to a regular class
        from .classes import async_session_deleted
        async_session_deleted(conn, s["section_id"], s["session_date"], session_id)
    _audit(conn, instructor_id, "delete", session_id, dict(s))
    conn.commit()


# ================================================================ roster + marking
def roster(conn, session: dict, now=None):
    """EVERY student enrolled in the section, with their status for this session.
    status: present | late | excused | absent (finished, no record) | pending (no record yet).
    A student who joined the section after this class (joined_later) stays 'pending' even when
    it has ended: they weren't in the class yet, so they're never counted absent for it."""
    now = now or datetime.now()
    finished = is_finished(session, now)
    cutoff = _enrolled_cutoff(session)
    rows = conn.execute(
        """SELECT u.id, u.student_number, u.first_name, u.middle_name, u.last_name, u.suffix,
                  a.status AS record_status, a.method, a.checked_in_at, a.note, a.checked_out_at,
                  e.enrolled_at
           FROM enrollments e
           JOIN users u ON u.id = e.student_id
           LEFT JOIN attendance a ON a.session_id = ? AND a.student_id = u.id AND a.deleted_at IS NULL
           WHERE e.section_id = ? AND e.status = 'active' AND e.deleted_at IS NULL AND u.deleted_at IS NULL
           ORDER BY u.last_name, u.first_name""",
        (session["id"], session["section_id"])).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["joined_later"] = d["enrolled_at"] > cutoff
        if d["record_status"]:
            d["status"] = d["record_status"]
        elif finished and not d["joined_later"]:
            d["status"] = "absent"
        else:
            d["status"] = "pending"
        out.append(d)
    return out


def mark(conn, instructor_id, session_id, student_id, status, now=None) -> None:
    """Instructor sets a student's status by hand. status = present|late|absent|excused|clear."""
    now = now or datetime.now()
    if status == "clear":
        conn.execute("""UPDATE attendance SET deleted_at = ? WHERE session_id = ? AND student_id = ?
                          AND deleted_at IS NULL""", (_ts(now), session_id, student_id))
    elif status in STATUSES:
        conn.execute(
            """INSERT INTO attendance (session_id, student_id, status, method, checked_in_at, marked_by)
               VALUES (?, ?, ?, 'manual', ?, ?)
               ON CONFLICT(session_id, student_id) DO UPDATE SET
                   status = excluded.status, marked_by = excluded.marked_by, deleted_at = NULL, note = NULL,
                   method = CASE WHEN attendance.deleted_at IS NULL THEN attendance.method ELSE 'manual' END,
                   signature_path = CASE WHEN attendance.deleted_at IS NULL THEN attendance.signature_path END,
                   checked_in_at = CASE WHEN attendance.deleted_at IS NULL THEN attendance.checked_in_at
                                        ELSE excluded.checked_in_at END""",
            (session_id, student_id, status, _ts(now), instructor_id))
    else:
        raise ValueError(status)
    from .classes import audit
    audit(conn, instructor_id, f"mark_{status}", "attendance", session_id, {"student_id": student_id})
    conn.commit()


# ================================================================ statistics
def _session_start(s) -> str:
    """'YYYY-MM-DD HH:MM:SS' when the session starts. Async: when the instructor assigned it
    (that's when everyone enrolled was marked present). Other untimed: end of that day."""
    if s.get("type") == "async" and s.get("created_at"):
        return s.get("ended_at") or s["created_at"]
    return f'{s["session_date"]} {(s["start_time"] or "23:59")}:59'


def _enrolled_cutoff(s) -> str:
    """Students enrolled before this moment belong on the session's list.
    Normally the session's start (someone who joins later isn't absent for earlier classes).
    But a session the instructor adds AFTERWARDS for a past date (catching up on a class)
    covers everyone enrolled when it was added - otherwise its list would be empty."""
    start = _session_start(s)
    created = s.get("created_at")
    # (only sessions added by hand: schedule sessions are created during their own day)
    if s.get("type") != "async" and not s.get("schedule_id") and created and created > start:
        return created
    return start


def _student_rows(conn, student_id, section_id, now):
    """One row per session the student should have attended: sessions that started after
    they enrolled, plus any session they have a record for."""
    enrolled = conn.execute(
        """SELECT enrolled_at FROM enrollments WHERE section_id = ? AND student_id = ?
             AND status = 'active' AND deleted_at IS NULL""", (section_id, student_id)).fetchone()
    if not enrolled:
        return []
    rows = conn.execute(
        """SELECT cs.*, a.status AS record_status, a.method, a.checked_in_at, a.note
           FROM class_sessions cs
           LEFT JOIN attendance a ON a.session_id = cs.id AND a.student_id = ? AND a.deleted_at IS NULL
           WHERE cs.section_id = ? AND cs.deleted_at IS NULL
             AND (cs.session_date >= date(?, '-1 day') OR cs.created_at >= ?)
           ORDER BY cs.session_date DESC, cs.start_time DESC""",
        (student_id, section_id, enrolled["enrolled_at"], enrolled["enrolled_at"])).fetchall()
    out = []
    for r in rows:
        s = dict(r)
        if s["record_status"] is None and _enrolled_cutoff(s) < enrolled["enrolled_at"]:
            continue                      # class happened before they joined
        s["finished"] = is_finished(s, now)
        if s["type"] == "async" and not s["finished"]:
            s["status"] = "pending"
        else:
            s["status"] = s["record_status"] or ("absent" if s["finished"] else "pending")
        out.append(s)
    return out


def summarize(rows) -> dict:
    c = {"present": 0, "late": 0, "absent": 0, "excused": 0, "pending": 0}
    for r in rows:
        c[r["status"]] += 1
    held = c["present"] + c["late"] + c["absent"] + c["excused"]
    c["held"] = held
    # excused counts as present in the rate (and is never an absence); shown as its own number
    c["rate"] = round(100 * (c["present"] + c["late"] + c["excused"]) / held) if held else None
    return c


def student_section_stats(conn, student_id, section_id, now=None):
    now = now or datetime.now()
    ensure_sessions_for_date(conn, now.date())
    rows = _student_rows(conn, student_id, section_id, now)
    return {"history": rows, "summary": summarize(rows)}


def section_student_stats(conn, section_id, now=None) -> dict:
    """{student_id: summary} for the Students tab."""
    now = now or datetime.now()
    ids = [r["student_id"] for r in conn.execute(
        "SELECT student_id FROM enrollments WHERE section_id = ? AND status = 'active' AND deleted_at IS NULL",
        (section_id,))]
    return {sid: summarize(_student_rows(conn, sid, section_id, now)) for sid in ids}


