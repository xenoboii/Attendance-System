"""Admin corrections to student records, with an audit trail."""
from . import accounts
from .classes import audit


def students(conn, query=""):
    needle = "%" + (query or "").strip() + "%"
    return conn.execute("""SELECT id, first_name, last_name, student_number, program, year_level, section_letter
        FROM users WHERE role = 'student' AND deleted_at IS NULL
        AND (student_number LIKE ? OR first_name LIKE ? OR last_name LIKE ?)
        ORDER BY last_name, first_name LIMIT 100""", (needle, needle, needle)).fetchall()


def get_student(conn, student_id):
    return conn.execute("SELECT * FROM users WHERE id = ? AND role = 'student' AND deleted_at IS NULL",
                        (student_id,)).fetchone()


def correct(conn, actor_id, student, form):
    errors = {}
    values = accounts.academic_values(conn, student, form, errors)
    contact = (form.get("contact_number", student["contact_number"]) or "").strip().replace(" ", "").replace("-", "")
    email = (form.get("email", student["email"]) or "").strip()
    if contact and not accounts.CONTACT_RE.fullmatch(contact):
        errors["contact_number"] = "Use 09XXXXXXXXX or +639XXXXXXXXX."
    if email and "@" not in email:
        errors["email"] = "Enter a valid email or leave it blank."
    try:
        values.update(accounts.student_identity_values(conn, student["id"], form))
    except accounts.ValidationError as exc:
        errors.update(exc.errors)
    if errors:
        raise accounts.ValidationError(errors)
    values.update(contact_number=contact or None, email=email or None,
                  academic_locked=int(all(values[k] for k in ("program", "year_level", "section_letter"))))
    sets = ", ".join(f"{key} = ?" for key in values)
    with conn:
        conn.execute(f"UPDATE users SET {sets}, updated_at = ? WHERE id = ?",
                     (*values.values(), accounts._ts(), student["id"]))
        audit(conn, actor_id, "correct_student_info", "users", student["id"],
              {"before": {key: student[key] for key in values}, "after": values})


def reset_signature(conn, actor_id, student_id):
    with conn:
        conn.execute("UPDATE users SET must_resign = 1, updated_at = ? WHERE id = ?",
                     (accounts._ts(), student_id))
        audit(conn, actor_id, "reset_signature", "users", student_id)


def remove_card(conn, actor_id, student_id):
    with conn:
        conn.execute("UPDATE users SET rfid_uid = NULL, updated_at = ? WHERE id = ?",
                     (accounts._ts(), student_id))
        conn.execute("UPDATE card_registrations SET status = 'cancelled', active_lock = NULL "
                     "WHERE student_id = ? AND status IN ('waiting', 'detected')", (student_id,))
        audit(conn, actor_id, "remove_card", "users", student_id)


# ---------------------------------------------------------------- merge an imported record
# After a device rebuild, an instructor's import creates students who haven't registered yet
# ("unclaimed": name, number, card, signature, attendance - but no password). A student who then
# registers with a MISTYPED student number gets a separate, empty account. The admin fixes that
# here: the imported history moves into the account the student actually uses, and that account
# takes the correct student number.
def unclaimed_records(conn, exclude_id=None):
    """Imported student records nobody has registered yet, with how much history each holds."""
    return conn.execute(
        """SELECT u.id, u.first_name, u.last_name, u.student_number, u.rfid_uid, u.signature_path,
                  (SELECT COUNT(*) FROM enrollments e WHERE e.student_id = u.id AND e.deleted_at IS NULL) AS classes,
                  (SELECT COUNT(*) FROM attendance a WHERE a.student_id = u.id AND a.deleted_at IS NULL) AS records
           FROM users u WHERE u.role = 'student' AND u.password_hash = '' AND u.deleted_at IS NULL AND u.id IS NOT ?
           ORDER BY u.last_name, u.first_name""", (exclude_id,)).fetchall()


def merge_unclaimed(conn, actor_id, student_id, source_id, signature_dir):
    """Move the unclaimed record `source_id` into the student's real account. Returns a summary.
    The account keeps its password and whatever the student entered; it gains the classes and
    attendance, the correct student number, and a card / signature only if it had none."""
    from pathlib import Path
    target = get_student(conn, student_id)
    source = get_student(conn, source_id)
    if target is None or source is None or student_id == source_id:
        raise accounts.ValidationError({"merge": "Choose the imported record to merge."})
    if not accounts.is_unclaimed(source):
        raise accounts.ValidationError({"merge": "That record is a registered account, not an imported one."})
    if accounts.is_unclaimed(target):
        raise accounts.ValidationError({"merge": "This student hasn't registered yet, so there is nothing to merge into."})

    number = source["student_number"]
    summary = {"number": number, "classes": 0, "records": 0, "card": False, "signature": False}
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    with conn:
        for table in tables:
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            if table == "users" or "student_id" not in cols:
                continue
            # rows the account already has (same class / same session) stay as they are
            moved = conn.execute(f'UPDATE OR IGNORE "{table}" SET student_id = ? WHERE student_id = ?',
                                 (student_id, source_id)).rowcount
            conn.execute(f'DELETE FROM "{table}" WHERE student_id = ?', (source_id,))
            if table == "enrollments":
                summary["classes"] = moved
            elif table == "attendance":
                summary["records"] = moved
        fills = {}
        if not target["rfid_uid"] and source["rfid_uid"]:
            fills["rfid_uid"] = source["rfid_uid"]
            summary["card"] = True
        src_file = Path(signature_dir) / f"{source_id}.png"
        if not target["signature_path"] and source["signature_path"] and src_file.exists():
            (Path(signature_dir) / f"{student_id}.png").write_bytes(src_file.read_bytes())
            fills.update(signature_path=f"signatures/{student_id}.png",
                         signature_signed_at=source["signature_signed_at"], must_resign=0)
            summary["signature"] = True
        # retire the imported record first (frees its student number and card), then take them over
        tag = f"merged-{source_id}-"
        conn.execute("""UPDATE users SET deleted_at = ?, updated_at = ?, username = ?, student_number = ?, rfid_uid = NULL
                        WHERE id = ?""", (accounts._ts(), accounts._ts(), tag + source["username"], tag + number, source_id))
        fills.update(student_number=number, username=number)
        conn.execute(f"UPDATE users SET {', '.join(k + ' = ?' for k in fills)}, updated_at = ? WHERE id = ?",
                     (*fills.values(), accounts._ts(), student_id))
        audit(conn, actor_id, "merge_imported_record", "users", student_id,
              {"from": source_id, "old_number": target["student_number"], **summary})
    return summary
