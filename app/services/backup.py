"""
Backup and restore (admin).

A backup is ONE .zip file:
    manifest.json     what it is, when it was made, database version
    attendance.db     a consistent copy of the whole database (accounts, classes, attendance,
                      profile pictures, settings ...)
    signatures/...    the signature images (stored ones + check-in drawings)

Restore replaces everything with what is in the file. Before it does, the current state is saved
as a safety copy in data/backups/ (the last few are kept), so a wrong file can be undone.

The live database is never swapped as a file: SQLite's own backup API copies the pages while
other connections (the card reader process) stay valid.
"""
import io
import json
import logging
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import app_settings
from .accounts import ValidationError

log = logging.getLogger(__name__)

FORMAT = "attendance-backup"
FORMAT_VERSION = 1
DB_NAME = "attendance.db"
LAST_BACKUP_KEY = "last_backup_at"
SAFETY_KEEP = 3
REMIND_AFTER_DAYS = 7
_SIGNATURE_NAME = re.compile(r"^signatures/(checkins/)?\d+\.png$")
_MAX_SIGNATURE = 600_000
_MAX_DB = 2 * 1024 ** 3


def filename(now=None) -> str:
    return f"attendance-backup-{(now or datetime.now()).strftime('%Y%m%d-%H%M')}.zip"


def _snapshot(database: Path) -> bytes:
    """A consistent copy of the database, even while it is being written to."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / DB_NAME
        src = sqlite3.connect(str(database), timeout=10)
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return target.read_bytes()


def _signature_files(signature_dir: Path):
    signature_dir = Path(signature_dir)
    if not signature_dir.exists():
        return
    for p in sorted(signature_dir.rglob("*.png")):
        name = "signatures/" + p.relative_to(signature_dir).as_posix()
        if _SIGNATURE_NAME.match(name):
            yield name, p


def create(database: Path, signature_dir: Path, now=None) -> bytes:
    """The backup file as bytes."""
    now = now or datetime.now()
    db_bytes = _snapshot(Path(database))
    version = _db_version(db_bytes)
    files = list(_signature_files(signature_dir))
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION,
                "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "database_version": version, "signatures": len(files)}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr(DB_NAME, db_bytes)
        for name, path in files:
            z.write(path, name)
    return buf.getvalue()


def _db_version(db_bytes: bytes) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "probe.db"
        p.write_bytes(db_bytes)
        conn = sqlite3.connect(str(p))
        try:
            return conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()


def mark_downloaded(conn, admin_id, now=None) -> None:
    from .classes import audit
    app_settings.put(conn, LAST_BACKUP_KEY, (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"))
    audit(conn, admin_id, "download_backup", "backup", 0)
    conn.commit()


def status(conn, data_dir: Path, now=None) -> dict:
    """When the last backup was downloaded (+ whether to nag) and the safety copies on the device."""
    now = now or datetime.now()
    last = app_settings.get(conn, LAST_BACKUP_KEY)
    days = None
    if last:
        try:
            days = (now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).days
        except ValueError:
            last = None
    folder = Path(data_dir) / "backups"
    copies = sorted(folder.glob("before-restore-*.zip"), reverse=True) if folder.exists() else []
    return {"last": last, "days": days, "overdue": last is None or days >= REMIND_AFTER_DAYS,
            "safety": [{"name": p.name, "size": p.stat().st_size} for p in copies]}


# ---------------------------------------------------------------- instructor: my classes only
INSTRUCTOR_FORMAT = "attendance-instructor-backup"


def instructor_filename(user, now=None) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "-", user["username"]).strip("-") or "instructor"
    return f"my-subjects-backup-{name}-{(now or datetime.now()).strftime('%Y%m%d-%H%M')}.zip"


def _own_database(database: Path, instructor_id: int) -> tuple[bytes, set, set]:
    """A copy of the database with everything that is NOT this instructor's removed:
    other instructors' subjects and all their classes, other people's accounts, logs, codes.
    Passwords are blanked. Returns (db bytes, kept user ids, kept attendance ids)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mine.db"
        path.write_bytes(_snapshot(database))
        c = sqlite3.connect(str(path))
        try:
            c.execute("PRAGMA foreign_keys = OFF")
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
            cols = {t: {r[1] for r in c.execute(f'PRAGMA table_info("{t}")')} for t in tables}

            c.execute("DELETE FROM subjects WHERE instructor_id <> ?", (instructor_id,))
            c.execute("DELETE FROM sections WHERE subject_id NOT IN (SELECT id FROM subjects)")
            for t in tables - {"sections"}:
                if "section_id" in cols[t]:
                    c.execute(f'DELETE FROM "{t}" WHERE section_id NOT IN (SELECT id FROM sections)')
            for t in tables - {"class_sessions"}:
                if "session_id" in cols[t]:
                    c.execute(f'DELETE FROM "{t}" WHERE session_id IS NOT NULL '
                              "AND session_id NOT IN (SELECT id FROM class_sessions)")
            # private or device-wide tables: nothing of these belongs in a class backup
            for t in ("recovery_codes", "audit_log", "rfid_scans", "card_registrations", "device_status", "app_settings"):
                if t in tables:
                    c.execute(f'DELETE FROM "{t}"')
            keep = {instructor_id}
            keep |= {r[0] for r in c.execute("SELECT student_id FROM enrollments")}
            keep |= {r[0] for r in c.execute("SELECT student_id FROM attendance")}
            marks = ",".join("?" * len(keep))
            c.execute(f"DELETE FROM users WHERE id NOT IN ({marks})", list(keep))
            for t in tables - {"users"}:
                for col in ("user_id", "student_id"):
                    if col in cols[t]:
                        c.execute(f'DELETE FROM "{t}" WHERE {col} IS NOT NULL AND {col} NOT IN ({marks})', list(keep))
            c.execute("UPDATE users SET password_hash = ''")          # nobody's password leaves the device
            attendance_ids = {r[0] for r in c.execute("SELECT id FROM attendance WHERE signature_path IS NOT NULL")}
            c.commit()
            c.execute("VACUUM")                                       # really remove the deleted rows from the file
            c.commit()
        finally:
            c.close()
        return path.read_bytes(), keep, attendance_ids


def create_for_instructor(conn, database: Path, signature_dir: Path, instructor: dict, now=None) -> bytes:
    """The instructor's own backup: only their subjects, sections, schedules, sessions and
    attendance, and their students' names and signatures."""
    now = now or datetime.now()
    db_bytes, users, attendance_ids = _own_database(Path(database), instructor["id"])
    signature_dir = Path(signature_dir)
    sigs = []
    for name, path in _signature_files(signature_dir):
        stem = path.stem
        mine = (int(stem) in attendance_ids) if "/checkins/" in name else (int(stem) in users)
        if mine:
            sigs.append((name, path))
    sections = conn.execute(
        """SELECT s.id FROM sections s JOIN subjects sub ON sub.id = s.subject_id
           WHERE sub.instructor_id = ? AND s.deleted_at IS NULL AND sub.deleted_at IS NULL
           ORDER BY sub.school_year, sub.code, s.name""", (instructor["id"],)).fetchall()
    manifest = {"format": INSTRUCTOR_FORMAT, "format_version": FORMAT_VERSION,
                "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "instructor": instructor["username"], "sections": len(sections), "signatures": len(sigs),
                "note": "Only this instructor's subjects. It cannot restore the whole device; "
                        "the admin's full backup does that."}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("my-classes.db", db_bytes)
        for name, path in sigs:
            z.write(path, name)
    return buf.getvalue()


def mark_instructor_download(conn, instructor_id, now=None) -> None:
    from .classes import audit
    app_settings.put(conn, f"{LAST_BACKUP_KEY}:{instructor_id}", (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"))
    audit(conn, instructor_id, "download_my_classes_backup", "backup", 0)
    conn.commit()


def instructor_status(conn, instructor_id, now=None) -> dict:
    now = now or datetime.now()
    last = app_settings.get(conn, f"{LAST_BACKUP_KEY}:{instructor_id}")
    days = None
    if last:
        try:
            days = (now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).days
        except ValueError:
            last = None
    return {"last": last, "days": days, "overdue": last is None or days >= REMIND_AFTER_DAYS}


# ---------------------------------------------------------------- restore
def inspect(data: bytes, current_version: int) -> dict:
    """Check an uploaded file BEFORE touching anything. Returns its manifest + counts, or raises
    ValidationError({"file": ...})."""
    def bad(msg):
        return ValidationError({"file": msg})
    if not data:
        raise bad("Choose a backup file first.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise bad("That is not a backup file (it must be the .zip downloaded from this page).")
    with z:
        names = set(z.namelist())
        if "my-classes.db" in names:
            raise bad("That is an instructor's own-subjects backup. Only the admin's full backup can restore the device.")
        if "manifest.json" not in names or DB_NAME not in names:
            raise bad("That zip file is not an attendance backup.")
        try:
            manifest = json.loads(z.read("manifest.json"))
        except (ValueError, UnicodeDecodeError):
            raise bad("The backup's manifest can't be read.")
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise bad("That zip file is not an attendance backup.")
        if z.getinfo(DB_NAME).file_size > _MAX_DB:
            raise bad("The database in that backup is too large.")
        db_bytes = z.read(DB_NAME)
        if not db_bytes.startswith(b"SQLite format 3\x00"):
            raise bad("The database inside the backup is damaged.")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "check.db"
            p.write_bytes(db_bytes)
            conn = sqlite3.connect(str(p))
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise bad("The database inside the backup is damaged.")
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                if not {"users", "subjects", "sections", "attendance"} <= tables:
                    raise bad("The database inside the backup is not from this system.")
                counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                          for t in ("users", "subjects", "attendance")}
            except sqlite3.DatabaseError:
                raise bad("The database inside the backup is damaged.")
            finally:
                conn.close()
        if version > current_version:
            raise bad("That backup was made by a newer version of this system. Update the system first.")
        signatures = [n for n in names if _SIGNATURE_NAME.match(n)]
        return {"manifest": manifest, "database_version": version, "counts": counts, "signatures": len(signatures)}


def _keep_safety_copy(database: Path, signature_dir: Path, data_dir: Path, now) -> Path:
    folder = Path(data_dir) / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"before-restore-{now.strftime('%Y%m%d-%H%M%S')}.zip"
    path.write_bytes(create(database, signature_dir, now))
    for old in sorted(folder.glob("before-restore-*.zip"), reverse=True)[SAFETY_KEEP:]:
        try:
            old.unlink()
        except OSError:
            log.exception("could not remove old safety copy %s", old)
    return path


def restore(data: bytes, database: Path, signature_dir: Path, data_dir: Path, current_version: int, now=None) -> dict:
    """Replace the database and signatures with the backup's. Returns inspect()'s info plus
    the safety copy's file name. The caller runs the migrations afterwards."""
    now = now or datetime.now()
    info = inspect(data, current_version)
    safety = _keep_safety_copy(Path(database), signature_dir, data_dir, now)

    with zipfile.ZipFile(io.BytesIO(data)) as z, tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / DB_NAME
        src_path.write_bytes(z.read(DB_NAME))
        src = sqlite3.connect(str(src_path))
        dst = sqlite3.connect(str(database), timeout=30)
        try:
            src.backup(dst)                       # page-by-page copy into the live database
        finally:
            dst.close()
            src.close()

        signature_dir = Path(signature_dir)
        for _name, old in list(_signature_files(signature_dir)):
            try:
                old.unlink()
            except OSError:
                log.exception("could not remove %s", old)
        for name in z.namelist():
            if not _SIGNATURE_NAME.match(name) or z.getinfo(name).file_size > _MAX_SIGNATURE:
                continue
            target = signature_dir / name[len("signatures/"):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
    log.warning("database restored from a backup made %s (safety copy: %s)",
                info["manifest"].get("created_at"), safety.name)
    return {**info, "safety_copy": safety.name}
