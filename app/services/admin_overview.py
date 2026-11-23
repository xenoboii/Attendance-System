"""Numbers for the admin's Overview page: accounts, classes, today's activity, storage, device."""
import shutil
from datetime import datetime
from pathlib import Path

from . import app_settings, backup, battery

LOW_SPACE_PERCENT = 15          # warn when less than this much of the disk is free
LOW_SPACE_BYTES = 500 * 1024 * 1024


def human(n: int) -> str:
    """1536 -> '1.5 KB'"""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024


def _folder_size(path: Path) -> int:
    total = 0
    if path.exists():
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    return total


def storage(database: Path, data_dir: Path, signature_dir: Path) -> dict:
    """Free space on the disk the data lives on (the SD card on the Pi) + what the app uses."""
    database, data_dir = Path(database), Path(data_dir)
    try:
        usage = shutil.disk_usage(data_dir if data_dir.exists() else database.parent)
    except OSError:
        return {"known": False}
    db_bytes = sum(f.stat().st_size for f in database.parent.glob(database.name + "*") if f.is_file())
    sig_bytes = _folder_size(Path(signature_dir))
    free_pct = round(100 * usage.free / usage.total) if usage.total else 0
    return {"known": True, "total": human(usage.total), "used": human(usage.used), "free": human(usage.free),
            "used_percent": 100 - free_pct, "free_percent": free_pct,
            "low": free_pct < LOW_SPACE_PERCENT or usage.free < LOW_SPACE_BYTES,
            "database": human(db_bytes), "signatures": human(sig_bytes)}


def counts(conn, now=None) -> dict:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0] or 0          # noqa: E731
    live = "deleted_at IS NULL"
    return {
        "students": one(f"SELECT COUNT(*) FROM users WHERE role = 'student' AND {live}"),
        "instructors": one(f"SELECT COUNT(*) FROM users WHERE role = 'instructor' AND {live}"),
        "admins": one(f"SELECT COUNT(*) FROM users WHERE role = 'admin' AND {live}"),
        "subjects": one(f"SELECT COUNT(*) FROM subjects WHERE {live} AND ended_at IS NULL"),
        "archived": one(f"SELECT COUNT(*) FROM subjects WHERE {live} AND ended_at IS NOT NULL"),
        "sections": one("""SELECT COUNT(*) FROM sections s JOIN subjects sub ON sub.id = s.subject_id
                           WHERE s.deleted_at IS NULL AND sub.deleted_at IS NULL AND sub.ended_at IS NULL"""),
        "sessions_today": one(f"SELECT COUNT(*) FROM class_sessions WHERE session_date = ? AND {live}", today),
        "checkins_today": one(f"""SELECT COUNT(*) FROM attendance WHERE {live} AND method IN ('rfid', 'web')
                                  AND checked_in_at >= ? AND checked_in_at < date(?, '+1 day')""", today, today),
        "card_checkins_today": one(f"""SELECT COUNT(*) FROM attendance WHERE {live} AND method = 'rfid'
                                       AND checked_in_at >= ? AND checked_in_at < date(?, '+1 day')""", today, today),
        "no_card": one(f"SELECT COUNT(*) FROM users WHERE role = 'student' AND {live} AND rfid_uid IS NULL"),
        "no_signature": one(f"SELECT COUNT(*) FROM users WHERE role = 'student' AND {live} AND signature_path IS NULL"),
        "no_department": one(f"""SELECT COUNT(*) FROM users WHERE role = 'instructor' AND {live}
                                 AND (department IS NULL OR trim(department) = '')"""),
    }


def attention(conn, limit=300) -> list[dict]:
    """Things the admin may need to chase, each with the people it is about."""
    people = "SELECT id, role, first_name, last_name, student_number, username FROM users WHERE deleted_at IS NULL AND "
    order = " ORDER BY last_name, first_name LIMIT ?"
    groups = [
        ("card", "credit-card-2-front", "without a registered school ID", "student",
         "role = 'student' AND rfid_uid IS NULL"),
        ("signature", "pen", "without a stored signature", "student",
         "role = 'student' AND signature_path IS NULL"),
        ("department", "building", "without a department (blank on the attendance sheet)", "instructor",
         "role = 'instructor' AND (department IS NULL OR trim(department) = '')"),
    ]
    out = []
    for key, icon, text, who, where in groups:
        rows = [dict(r) for r in conn.execute(people + where + order, (limit,))]
        if rows:
            out.append({"key": key, "icon": icon, "text": text, "who": who, "people": rows})
    return out


def recent_activity(conn, limit=6):
    """Latest admin / instructor actions from the audit log."""
    return conn.execute(
        """SELECT a.action, a.entity, a.created_at, u.first_name, u.last_name, u.role
           FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
           ORDER BY a.id DESC LIMIT ?""", (limit,)).fetchall()


def build(conn, config, now=None) -> dict:
    now = now or datetime.now()
    return {"now": now, "counts": counts(conn, now), "battery": battery.get(conn),
            "signature_days": app_settings.signature_days(conn, config.get("CHECKIN_SIGNATURE_DAYS", 60)),
            "signature_days_range": app_settings.SIGNATURE_DAYS_RANGE,
            "storage": storage(config["DATABASE"], config["DATA_DIR"], config["SIGNATURE_DIR"]),
            "attention": attention(conn), "activity": recent_activity(conn),
            "backup": backup.status(conn, config["DATA_DIR"], now)}
