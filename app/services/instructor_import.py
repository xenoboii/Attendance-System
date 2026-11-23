"""
Import an instructor's own backup ("Backup of my subjects") into this device.

Used after the device was rebuilt (dead SD card, fresh install) when the admin's full backup is
missing or too old: the instructor registers again, then imports their file.

What it does
  * Subjects, sections, weekly schedules, no-class days, sessions and attendance are recreated,
    owned by the instructor who imports. Nothing existing is replaced: a subject the instructor
    already has for the same term is skipped.
  * Students are matched by STUDENT NUMBER.
      - already registered here -> that account is enrolled and gets the old attendance.
        What they entered themselves wins; the file only fills in a missing card / signature.
      - not here yet            -> created "unclaimed" (no password) with name, number, card and
        signature. They claim it by registering with the same student number.
  * Signatures drawn at web check-in come back with their attendance record.
"""
import io
import json
import logging
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import classes
from .accounts import ValidationError
from .backup import INSTRUCTOR_FORMAT, _MAX_DB, _MAX_SIGNATURE, _SIGNATURE_NAME

log = logging.getLogger(__name__)
DB_NAME = "my-classes.db"


def _bad(msg):
    return ValidationError({"file": msg})


def _open_source(data: bytes, current_version: int, tmp: Path):
    """Validate the zip and return (zipfile, connection to its database upgraded to this version)."""
    if not data:
        raise _bad("Choose your backup file first.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise _bad("That is not a backup file (it must be the .zip from Settings > Backup of my subjects).")
    names = set(z.namelist())
    if "attendance.db" in names:
        raise _bad("That is the admin's full backup. The admin restores it from Backup & restore.")
    if "manifest.json" not in names or DB_NAME not in names:
        raise _bad("That zip file is not a backup of an instructor's subjects.")
    try:
        manifest = json.loads(z.read("manifest.json"))
    except (ValueError, UnicodeDecodeError):
        raise _bad("The backup's manifest can't be read.")
    if not isinstance(manifest, dict) or manifest.get("format") != INSTRUCTOR_FORMAT:
        raise _bad("That zip file is not a backup of an instructor's subjects.")
    if z.getinfo(DB_NAME).file_size > _MAX_DB:
        raise _bad("The database in that backup is too large.")
    db_bytes = z.read(DB_NAME)
    if not db_bytes.startswith(b"SQLite format 3\x00"):
        raise _bad("The database inside the backup is damaged.")
    path = tmp / "source.db"
    path.write_bytes(db_bytes)
    src = sqlite3.connect(str(path))
    src.row_factory = sqlite3.Row
    try:
        if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise _bad("The database inside the backup is damaged.")
        version = src.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"users", "subjects", "sections", "attendance"} <= tables:
            raise _bad("The database inside the backup is not from this system.")
        if version > current_version:
            raise _bad("That backup was made by a newer version of this system. Update the system first.")
        if version < current_version:                 # older backup: bring its tables up to date first
            from ..migrations import migrate
            migrate(src)
    except sqlite3.DatabaseError:
        src.close()
        raise _bad("The database inside the backup is damaged.")
    except ValidationError:
        src.close()
        raise
    return z, src, manifest


def _columns(conn, table) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _copy_row(dst, table, row, common, overrides) -> int:
    """Insert one source row into `table` (columns both databases have, minus id) with some
    values replaced. Returns the new id."""
    values = {c: row[c] for c in common}
    values.update(overrides)
    cols = list(values)
    cur = dst.execute(f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
                      [values[c] for c in cols])
    return cur.lastrowid


def import_backup(conn, data: bytes, instructor_id: int, signature_dir: Path, current_version: int, now=None) -> dict:
    """Returns a summary dict. All-or-nothing for the database; raises ValidationError({"file": ..})."""
    now = now or datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    signature_dir = Path(signature_dir)
    summary = {"subjects": 0, "subjects_skipped": [], "sections": 0, "sessions": 0, "attendance": 0,
               "students_new": 0, "students_matched": 0, "signatures": 0}
    files = []                                              # (zip member, target path) written after commit

    with tempfile.TemporaryDirectory() as tmp:
        z, src, manifest = _open_source(data, current_version, Path(tmp))
        try:
            names = set(z.namelist())

            def common(table):
                return [c for c in _columns(src, table) if c in set(_columns(conn, table)) and c != "id"]

            def queue_signature(member, target):
                if member in names and _SIGNATURE_NAME.match(member) and z.getinfo(member).file_size <= _MAX_SIGNATURE:
                    files.append((member, target))
                    return True
                return False

            subjects = src.execute("SELECT * FROM subjects WHERE deleted_at IS NULL ORDER BY id").fetchall()
            if not subjects:
                raise _bad("That backup has no subjects in it.")
            sub_cols = common("subjects")
            sub_map = {}
            for s in subjects:
                dup = conn.execute(
                    "SELECT id, deleted_at FROM subjects WHERE instructor_id = ? AND code = ? AND school_year = ? AND semester = ?",
                    (instructor_id, s["code"], s["school_year"], s["semester"])).fetchone()
                if dup:
                    summary["subjects_skipped"].append(f"{s['code']} ({s['school_year']} {s['semester']} sem)")
                    continue
                sub_map[s["id"]] = _copy_row(conn, "subjects", s, sub_cols, {"instructor_id": instructor_id})
                summary["subjects"] += 1
            if not sub_map:
                conn.rollback()
                return {**summary, "made": manifest.get("created_at"), "nothing_new": True}

            # ---- sections of the imported subjects
            sec_cols, sec_map = common("sections"), {}
            for sec in src.execute("SELECT * FROM sections WHERE deleted_at IS NULL ORDER BY id"):
                if sec["subject_id"] not in sub_map:
                    continue
                code = sec["join_code"]
                if not code or conn.execute("SELECT 1 FROM sections WHERE join_code = ?", (code,)).fetchone():
                    code = classes._new_join_code(conn)
                sec_map[sec["id"]] = _copy_row(conn, "sections", sec, sec_cols,
                                               {"subject_id": sub_map[sec["subject_id"]], "join_code": code})
                summary["sections"] += 1

            # ---- students (matched by student number, or created unclaimed)
            needed = set()
            for table in ("enrollments", "attendance"):
                key = "section_id" if table == "enrollments" else "session_id"
                for r in src.execute(f"SELECT student_id, {key} AS k FROM {table}"):
                    needed.add(r["student_id"])
            user_cols, user_map = common("users"), {}
            for u in src.execute("SELECT * FROM users WHERE role = 'student' ORDER BY id"):
                if u["id"] not in needed or not u["student_number"] or u["deleted_at"]:
                    continue
                number = u["student_number"].strip().upper()
                here = conn.execute("SELECT * FROM users WHERE upper(student_number) = ? OR upper(username) = ?",
                                    (number, number)).fetchone()
                card = u["rfid_uid"]
                if card and conn.execute("SELECT 1 FROM users WHERE rfid_uid = ?", (card,)).fetchone():
                    card = None                                  # someone here already uses that card
                if here is not None:
                    if here["role"] != "student":
                        continue                                 # the number belongs to a non-student account
                    user_map[u["id"]] = here["id"]
                    summary["students_matched"] += 1
                    fills = {}
                    if not here["rfid_uid"] and card:
                        fills["rfid_uid"] = card
                    if not here["signature_path"] and u["signature_path"] and queue_signature(
                            f"signatures/{u['id']}.png", signature_dir / f"{here['id']}.png"):
                        fills.update(signature_path=f"signatures/{here['id']}.png",
                                     signature_signed_at=u["signature_signed_at"] or ts)
                        summary["signatures"] += 1
                    if fills:
                        conn.execute(f"UPDATE users SET {', '.join(k + ' = ?' for k in fills)}, updated_at = ? WHERE id = ?",
                                     (*fills.values(), ts, here["id"]))
                    continue
                new_id = _copy_row(conn, "users", u, user_cols, {
                    "role": "student", "username": number, "student_number": number, "password_hash": "",
                    "rfid_uid": card, "signature_path": None, "signature_signed_at": None, "must_resign": 0,
                    "email": None if u["email"] and conn.execute(
                        "SELECT 1 FROM users WHERE email = ?", (u["email"],)).fetchone() else u["email"]})
                user_map[u["id"]] = new_id
                summary["students_new"] += 1
                if u["signature_path"] and queue_signature(f"signatures/{u['id']}.png", signature_dir / f"{new_id}.png"):
                    conn.execute("UPDATE users SET signature_path = ?, signature_signed_at = ? WHERE id = ?",
                                 (f"signatures/{new_id}.png", u["signature_signed_at"] or ts, new_id))
                    summary["signatures"] += 1

            # ---- weekly schedules
            sched_cols, sched_map = common("schedules"), {}
            for r in src.execute("SELECT * FROM schedules ORDER BY id"):
                if r["section_id"] in sec_map:
                    sched_map[r["id"]] = _copy_row(conn, "schedules", r, sched_cols, {"section_id": sec_map[r["section_id"]]})

            # ---- enrollments
            en_cols = common("enrollments")
            for r in src.execute("SELECT * FROM enrollments ORDER BY id"):
                if r["section_id"] in sec_map and r["student_id"] in user_map:
                    _copy_row(conn, "enrollments", r, en_cols,
                              {"section_id": sec_map[r["section_id"]], "student_id": user_map[r["student_id"]]})

            # ---- sessions + attendance (with the drawings made at web check-in)
            ses_cols, ses_map = common("class_sessions"), {}
            for r in src.execute("SELECT * FROM class_sessions ORDER BY id"):
                if r["section_id"] not in sec_map:
                    continue
                over = {"section_id": sec_map[r["section_id"]],
                        "schedule_id": sched_map.get(r["schedule_id"]) if r["schedule_id"] else None}
                if "created_by" in ses_cols:
                    over["created_by"] = instructor_id
                ses_map[r["id"]] = _copy_row(conn, "class_sessions", r, ses_cols, over)
                summary["sessions"] += 1
            ex_cols = common("schedule_exceptions")
            for r in src.execute("SELECT * FROM schedule_exceptions ORDER BY id"):
                if r["section_id"] in sec_map:
                    over = {"section_id": sec_map[r["section_id"]]}
                    if "created_by" in ex_cols:
                        over["created_by"] = instructor_id
                    if "async_session_id" in ex_cols:
                        over["async_session_id"] = ses_map.get(r["async_session_id"])
                    _copy_row(conn, "schedule_exceptions", r, ex_cols, over)
            att_cols = common("attendance")
            for r in src.execute("SELECT * FROM attendance ORDER BY id"):
                if r["session_id"] not in ses_map or r["student_id"] not in user_map:
                    continue
                new_id = _copy_row(conn, "attendance", r, att_cols, {
                    "session_id": ses_map[r["session_id"]], "student_id": user_map[r["student_id"]],
                    "marked_by": instructor_id if r["marked_by"] else None, "signature_path": None})
                summary["attendance"] += 1
                if r["signature_path"] and queue_signature(f"signatures/checkins/{r['id']}.png",
                                                           signature_dir / "checkins" / f"{new_id}.png"):
                    conn.execute("UPDATE attendance SET signature_path = ? WHERE id = ?",
                                 (f"signatures/checkins/{new_id}.png", new_id))

            classes.audit(conn, instructor_id, "import_my_subjects", "backup", 0,
                          {k: v for k, v in summary.items()} | {"made": manifest.get("created_at")})
            conn.commit()
            for member, target in files:                       # only after the database part succeeded
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(member))
        except Exception:
            conn.rollback()
            raise
        finally:
            src.close()
            z.close()
    log.warning("instructor=%s imported their subjects backup: %s", instructor_id, summary)
    return {**summary, "made": manifest.get("created_at"), "nothing_new": False}
