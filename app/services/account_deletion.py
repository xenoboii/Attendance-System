"""
Deleting an account (by the person themselves, or by the admin) and restoring it (admin).

The initial deletion is reversible:
  * the account can no longer log in and disappears from class lists, rosters and reports;
  * its username / student number / card are freed, so the same person can register again;
  * an instructor's active subjects are archived (semester ended) so no classes keep running;
  * the attendance records themselves stay, so the admin can restore the account with its
    history as long as the username / student number hasn't been taken again.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from . import checkin_signatures, trash
from .accounts import ValidationError
from .backup import LAST_BACKUP_KEY
from .classes import audit

log = logging.getLogger(__name__)


def _ts(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def list_accounts(conn, query: str = "", limit: int = 200):
    """Students and instructors that can be deleted by the admin."""
    needle = "%" + (query or "").strip() + "%"
    return conn.execute(
        """SELECT id, role, username, student_number, first_name, last_name, password_hash, created_at
           FROM users WHERE role IN ('student', 'instructor') AND deleted_at IS NULL
             AND (username LIKE ? OR student_number LIKE ? OR first_name LIKE ? OR last_name LIKE ?)
           ORDER BY role, last_name, first_name LIMIT ?""", (needle, needle, needle, needle, limit)).fetchall()


def list_deleted(conn):
    return conn.execute(
        """SELECT d.*, u.role, u.first_name, u.last_name, a.first_name AS by_first, a.last_name AS by_last
           FROM deleted_accounts d JOIN users u ON u.id = d.user_id
           LEFT JOIN users a ON a.id = d.deleted_by
           ORDER BY d.deleted_at DESC""").fetchall()


def delete_account(conn, user_id: int, actor_id: int, now=None) -> dict:
    """Returns the user row as it was. Raises ValidationError({"account": ...})."""
    user = conn.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)).fetchone()
    if user is None:
        raise ValidationError({"account": "Account not found."})
    if user["role"] not in ("student", "instructor"):
        raise ValidationError({"account": "Admin accounts can't be deleted here."})
    ts = _ts(now)
    archived = []
    if user["role"] == "instructor":
        archived = [r["id"] for r in conn.execute(
            "SELECT id FROM subjects WHERE instructor_id = ? AND deleted_at IS NULL AND ended_at IS NULL", (user_id,))]
        conn.execute("""UPDATE subjects SET ended_at = ? WHERE instructor_id = ? AND deleted_at IS NULL
                          AND ended_at IS NULL""", (ts, user_id))
    # a student leaves their classes (so class counts stay right); remembered for a restore
    enrollments = [r["id"] for r in conn.execute(
        "SELECT id FROM enrollments WHERE student_id = ? AND status = 'active' AND deleted_at IS NULL", (user_id,))]
    conn.execute("""UPDATE enrollments SET status = 'dropped' WHERE student_id = ? AND status = 'active'
                      AND deleted_at IS NULL""", (user_id,))
    conn.execute("DELETE FROM join_requests WHERE student_id = ?", (user_id,))
    conn.execute(
        """INSERT OR REPLACE INTO deleted_accounts (user_id, username, student_number, rfid_uid, deleted_by, deleted_at, details)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, user["username"], user["student_number"], user["rfid_uid"], actor_id, ts,
         json.dumps({"archived_subjects": archived, "enrollments": enrollments})))
    tag = f"deleted-{user_id}-"
    conn.execute(
        """UPDATE users SET deleted_at = ?, updated_at = ?, username = ?, student_number = ?, rfid_uid = NULL
           WHERE id = ?""",
        (ts, ts, tag + user["username"], (tag + user["student_number"]) if user["student_number"] else None, user_id))
    conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    conn.execute("""UPDATE card_registrations SET status = 'cancelled', active_lock = NULL
                    WHERE student_id = ? AND status IN ('waiting', 'detected')""", (user_id,))
    audit(conn, actor_id, "delete_account", "users", user_id,
          {"self": actor_id == user_id, "role": user["role"], "archived_subjects": archived})
    conn.commit()
    log.warning("account deleted id=%s role=%s by=%s", user_id, user["role"], actor_id)
    return dict(user)


def restore_account(conn, user_id: int, admin_id: int) -> dict:
    """Admin brings a deleted account back with its history. Raises ValidationError if the
    username / student number was registered again in the meantime."""
    saved = conn.execute("SELECT * FROM deleted_accounts WHERE user_id = ?", (user_id,)).fetchone()
    user = conn.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NOT NULL", (user_id,)).fetchone()
    if saved is None or user is None:
        raise ValidationError({"account": "That account isn't in the deleted list."})
    clash = conn.execute("SELECT 1 FROM users WHERE id <> ? AND (username = ? OR (? IS NOT NULL AND student_number = ?))",
                         (user_id, saved["username"], saved["student_number"], saved["student_number"])).fetchone()
    if clash:
        what = "student number" if user["role"] == "student" else "username"
        raise ValidationError({"account": f"Can't restore: that {what} was registered again by a new account."})
    card = saved["rfid_uid"]
    if card and conn.execute("SELECT 1 FROM users WHERE rfid_uid = ?", (card,)).fetchone():
        card = None                                   # the card now belongs to someone else
    restored = conn.execute(
        """UPDATE users SET deleted_at = NULL, updated_at = ?, username = ?, student_number = ?, rfid_uid = ?
           WHERE id = ? AND deleted_at IS NOT NULL""", (_ts(), saved["username"], saved["student_number"], card, user_id))
    if not restored.rowcount:
        conn.rollback()
        raise ValidationError({"account": "That account isn't in the deleted list."})
    try:
        details = json.loads(saved["details"] or "{}")
    except ValueError:
        details = {}
    for sid in details.get("archived_subjects", []):  # only the ones archived by the deletion
        conn.execute("UPDATE subjects SET ended_at = NULL WHERE id = ? AND instructor_id = ?", (sid, user_id))
    for eid in details.get("enrollments", []):        # back into the classes they were in
        conn.execute("UPDATE enrollments SET status = 'active' WHERE id = ? AND student_id = ? AND deleted_at IS NULL",
                     (eid, user_id))
    conn.execute("DELETE FROM deleted_accounts WHERE user_id = ?", (user_id,))
    audit(conn, admin_id, "restore_account", "users", user_id)
    conn.commit()
    log.warning("account restored id=%s by admin=%s", user_id, admin_id)
    return dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def purge_account(conn, user_id: int, admin_id: int, signature_dir: Path) -> int:
    """Permanently delete an account from the deleted list. Return the number of files that could not be removed."""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            """SELECT u.role FROM users u JOIN deleted_accounts d ON d.user_id = u.id
               WHERE u.id = ? AND u.deleted_at IS NOT NULL AND u.role IN ('student', 'instructor')""",
            (user_id,),
        ).fetchone()
        if user is None:
            raise ValidationError({"account": "Only student and instructor accounts in Deleted accounts can be deleted completely."})

        # Record affected file IDs before deleting their rows. All filenames come from app-owned IDs.
        attendance_ids = [row[0] for row in conn.execute(
            """SELECT a.id FROM attendance a JOIN class_sessions cs ON cs.id = a.session_id
               JOIN sections s ON s.id = cs.section_id JOIN subjects sub ON sub.id = s.subject_id
               WHERE a.student_id = ? OR sub.instructor_id = ?""", (user_id, user_id))]
        if user["role"] == "instructor":
            subjects = conn.execute("SELECT id FROM subjects WHERE instructor_id = ?", (user_id,)).fetchall()
            for subject in subjects:
                trash.purge_subject(conn, subject["id"])

        conn.execute("DELETE FROM attendance WHERE student_id = ?", (user_id,))
        conn.execute("DELETE FROM enrollments WHERE student_id = ?", (user_id,))
        conn.execute("DELETE FROM join_requests WHERE student_id = ?", (user_id,))
        conn.execute("DELETE FROM card_registrations WHERE student_id = ?", (user_id,))
        conn.execute("DELETE FROM rfid_scans WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM session_state_changes WHERE changed_by = ?", (user_id,))
        conn.execute("DELETE FROM reports WHERE generated_by = ?", (user_id,))

        # Retain records belonging to others and clear references to the deleted actor.
        conn.execute("UPDATE attendance SET marked_by = NULL WHERE marked_by = ?", (user_id,))
        conn.execute("UPDATE class_sessions SET created_by = NULL WHERE created_by = ?", (user_id,))
        conn.execute("UPDATE schedule_exceptions SET created_by = NULL WHERE created_by = ?", (user_id,))
        conn.execute("UPDATE join_requests SET reviewed_by = NULL WHERE reviewed_by = ?", (user_id,))
        conn.execute("UPDATE courses SET created_by = NULL WHERE created_by = ?", (user_id,))
        conn.execute("UPDATE departments SET created_by = NULL WHERE created_by = ?", (user_id,))
        conn.execute("UPDATE deleted_accounts SET deleted_by = NULL WHERE deleted_by = ?", (user_id,))
        conn.execute("UPDATE audit_log SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE audit_log SET details = NULL WHERE entity = 'users' AND entity_id = ?", (user_id,))
        conn.execute("DELETE FROM app_settings WHERE key = ?", (f"{LAST_BACKUP_KEY}:{user_id}",))
        conn.execute("DELETE FROM deleted_accounts WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))  # cascades to profile picture and recovery codes
        audit(conn, admin_id, "purge_account", "users", user_id, {"role": user["role"]})

    # Only remove images after the database transaction succeeds.
    signature_dir = Path(signature_dir)
    paths = [signature_dir / f"{user_id}.png"]
    paths.extend(signature_dir / checkin_signatures.SUBDIR / f"{aid}.png" for aid in attendance_ids)
    failed = 0
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failed += 1
            log.exception("could not remove signature file %s for purged account %s", path, user_id)
    return failed
