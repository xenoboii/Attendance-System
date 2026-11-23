"""
Month calendar on the student dashboard.

Shows, for the classes the student is enrolled in:
  - the weekly class days,
  - dates the instructor marked "no class" (holiday, suspended classes ...) with the reason,
  - make-up classes and async activities set on a specific date.
"""
import calendar
from datetime import date, datetime, timedelta

MONTHS_AROUND = 12            # how far the student can page from the current month
NOTICE_DAYS = 30              # "coming up" list: no-class days in the next 30 days


def parse_month(text, today: date) -> tuple[int, int]:
    """'2026-12' -> (2026, 12); anything odd or too far away -> the current month."""
    try:
        y, m = (int(p) for p in (text or "").split("-"))
        first = date(y, m, 1)
    except (ValueError, TypeError):
        return today.year, today.month
    if abs((first.year - today.year) * 12 + first.month - today.month) > MONTHS_AROUND:
        return today.year, today.month
    return y, m


def _shift(y, m, by):
    idx = y * 12 + (m - 1) + by
    return f"{idx // 12}-{idx % 12 + 1:02d}"


def _enrolled_sql():
    return """FROM enrollments e
              JOIN sections s ON s.id = e.section_id
              JOIN subjects sub ON sub.id = s.subject_id
              WHERE e.student_id = ? AND e.status = 'active' AND e.deleted_at IS NULL
                AND s.deleted_at IS NULL AND sub.deleted_at IS NULL AND sub.ended_at IS NULL"""


def upcoming_no_class(conn, student_id, today: date, days=NOTICE_DAYS):
    """No-class days coming up for the student's classes, soonest first."""
    rows = conn.execute(
        f"""SELECT se.exception_date, se.reason, sub.code, s.name AS section_name
            FROM schedule_exceptions se JOIN enrollments e ON e.section_id = se.section_id
            JOIN sections s ON s.id = e.section_id JOIN subjects sub ON sub.id = s.subject_id
            WHERE e.student_id = ? AND e.status = 'active' AND e.deleted_at IS NULL
              AND s.deleted_at IS NULL AND sub.deleted_at IS NULL AND sub.ended_at IS NULL
              AND se.exception_date BETWEEN ? AND ?
            ORDER BY se.exception_date, sub.code""",
        (student_id, today.isoformat(), (today + timedelta(days=days)).isoformat())).fetchall()
    out = []
    for r in rows:
        d = date.fromisoformat(r["exception_date"])
        away = (d - today).days
        out.append({**dict(r), "date": d,
                    "when": "Today" if away == 0 else "Tomorrow" if away == 1 else f"In {away} days"})
    return out


def month_view(conn, student_id, year, month, now=None) -> dict:
    now = now or datetime.now()
    today = now.date()
    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)      # Sunday first
    first, last = weeks[0][0].isoformat(), weeks[-1][-1].isoformat()

    weekly = {}                                                # weekday -> [class]
    for r in conn.execute(
            f"""SELECT sc.id AS schedule_id, sc.day_of_week, sc.start_time, sc.end_time, sc.room,
                       s.id AS section_id, s.name AS section_name, sub.code, sub.title
                {_enrolled_sql().replace("FROM enrollments e", "FROM enrollments e JOIN schedules sc ON sc.section_id = e.section_id AND sc.deleted_at IS NULL")}
                ORDER BY sc.start_time""", (student_id,)):
        weekly.setdefault(r["day_of_week"], []).append(dict(r))

    no_class = {}                                              # (section, date) -> reason
    for r in conn.execute(
            f"""SELECT se.section_id, se.exception_date, se.reason
                {_enrolled_sql().replace("FROM enrollments e", "FROM enrollments e JOIN schedule_exceptions se ON se.section_id = e.section_id")}
                AND se.exception_date BETWEEN ? AND ?""", (student_id, first, last)):
        no_class[(r["section_id"], r["exception_date"])] = r["reason"]

    extra = {}                                                 # date -> [make-up / async]
    for r in conn.execute(
            f"""SELECT cs.schedule_id, cs.session_date, cs.start_time, cs.end_time, cs.type, cs.title AS session_title,
                       s.id AS section_id, s.name AS section_name, sub.code, sub.title
                {_enrolled_sql().replace("FROM enrollments e", "FROM enrollments e JOIN class_sessions cs ON cs.section_id = e.section_id AND cs.deleted_at IS NULL AND cs.type IN ('makeup', 'async')")}
                AND cs.session_date BETWEEN ? AND ?
                ORDER BY cs.start_time""", (student_id, first, last)):
        extra.setdefault(r["session_date"], []).append(dict(r))

    grid = []
    for week in weeks:
        row = []
        for d in week:
            iso, items = d.isoformat(), []
            activities = extra.get(iso, [])
            async_schedules = {x["schedule_id"] for x in activities if x["type"] == "async"}
            for c in weekly.get(d.weekday(), []):
                if c["schedule_id"] in async_schedules:
                    continue
                reason = no_class.get((c["section_id"], iso))
                items.append({**c, "kind": "no_class" if reason else "class", "reason": reason})
            for x in activities:
                items.append({**x, "kind": x["type"], "reason": None, "room": None})
            items.sort(key=lambda i: i.get("start_time") or "99:99")
            kinds = {i["kind"] for i in items}
            row.append({"date": d, "iso": iso, "day": d.day, "in_month": d.month == month,
                        "is_today": d == today, "is_past": d < today, "events": items,
                        "title": "Today's classes" if d == today
                                 else "Classes on " + d.strftime("%a, %b %d").replace(" 0", " "),
                        "has_class": "class" in kinds, "has_no_class": "no_class" in kinds,
                        "has_extra": bool(kinds & {"makeup", "async"})})
        grid.append(row)

    in_month = [d for w in grid for d in w if d["in_month"]]
    selected = next((d for d in in_month if d["is_today"]), None) \
        or next((d for d in in_month if d["events"] and not d["is_past"]), None) \
        or next((d for d in in_month if d["events"]), in_month[0])
    return {"year": year, "month": month, "label": f"{calendar.month_name[month]} {year}",
            "prev": _shift(year, month, -1), "next": _shift(year, month, 1),
            "is_current": (year, month) == (today.year, today.month),
            "weeks": grid, "selected": selected["iso"], "selected_title": selected["title"],
            "notices": upcoming_no_class(conn, student_id, today)}
