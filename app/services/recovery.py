"""
Backup codes for account recovery (the Pi has no internet, so there is no reset e-mail).

    - 10 codes like "K7QM-2XPA", made when an instructor registers (and again whenever they
      click "Generate new codes" in My account - the old set stops working, like Discord).
    - Only a hash of each code is stored; the plain codes are shown ONCE and saved as a PDF.
    - "Forgot password?" on the login page: username + one unused code + new password.
      Each code works once.
"""
import io
import logging
import secrets
from datetime import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from .accounts import MIN_PASSWORD, ValidationError, _ts, full_name

log = logging.getLogger(__name__)

CODE_COUNT = 10
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O, 1/I - easy to read off paper


def _new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _normalize(code: str) -> str:
    """'k7qm 2xpa' / 'K7QM2XPA' / 'K7QM-2XPA' -> 'K7QM-2XPA'"""
    raw = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else ""


def generate(conn, user_id: int, now=None) -> list[str]:
    """Make a fresh set of codes (the old set stops working). Returns the plain codes."""
    codes = [_new_code() for _ in range(CODE_COUNT)]
    ts = _ts(now)
    conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO recovery_codes (user_id, code_hash, created_at) VALUES (?, ?, ?)",
        [(user_id, generate_password_hash(c), ts) for c in codes])
    conn.commit()
    log.info("backup codes generated user=%s", user_id)
    return codes


def accounts_overview(conn, query: str = "", limit: int = 100) -> list[dict]:
    """Admin help desk: students + instructors with how many unused codes each still has.
    The codes themselves can't be listed - only their hashes are stored - so the admin
    issues ONE new single-use code for someone who lost theirs (see admin_issue)."""
    needle = "%" + (query or "").strip() + "%"
    rows = conn.execute(
        """SELECT u.id, u.role, u.username, u.student_number, u.first_name, u.last_name,
                  COUNT(rc.id) AS total, COALESCE(SUM(rc.used_at IS NULL), 0) AS unused,
                  MAX(rc.created_at) AS created_at
           FROM users u LEFT JOIN recovery_codes rc ON rc.user_id = u.id
           WHERE u.role IN ('student', 'instructor') AND u.deleted_at IS NULL
             AND (u.username LIKE ? OR u.student_number LIKE ? OR u.first_name LIKE ? OR u.last_name LIKE ?)
           GROUP BY u.id ORDER BY u.role, u.last_name, u.first_name LIMIT ?""",
        (needle, needle, needle, needle, limit)).fetchall()
    return [dict(r) for r in rows]


def admin_issue(conn, admin_id: int, user_id: int, now=None):
    """Admin gives a student / instructor who lost their codes ONE single-use code. They use it
    on "Forgot password?" to get back in, then make their own new set in My account.
    Codes they still have keep working. Returns (user row, [code]) or None if not allowed."""
    user = conn.execute("SELECT * FROM users WHERE id = ? AND role IN ('student', 'instructor') "
                        "AND deleted_at IS NULL", (user_id,)).fetchone()
    if user is None:
        return None
    codes = [_new_code()]
    conn.execute("INSERT INTO recovery_codes (user_id, code_hash, created_at) VALUES (?, ?, ?)",
                 (user_id, generate_password_hash(codes[0]), _ts(now)))
    from .classes import audit
    audit(conn, admin_id, "issue_backup_codes", "users", user_id, {"count": len(codes)})
    conn.commit()
    log.warning("backup codes issued by admin=%s for user=%s", admin_id, user_id)
    return user, codes


def status(conn, user_id: int) -> dict:
    """{'total': 10, 'unused': 7, 'created_at': '...'} (total 0 = never generated)."""
    row = conn.execute(
        """SELECT COUNT(*) AS total, SUM(used_at IS NULL) AS unused, MAX(created_at) AS created_at
           FROM recovery_codes WHERE user_id = ?""", (user_id,)).fetchone()
    return {"total": row["total"], "unused": row["unused"] or 0, "created_at": row["created_at"]}


def reset_password(conn, form: dict, now=None, *, student: bool = False) -> int:
    """'Forgot password?': username (instructor) or student number (student) + unused backup code
    + new password. student=True on the student site, False on the prof site. Returns the user id."""
    errors = {}
    login = (form.get("username") or "").strip().lower()
    code = _normalize(form.get("code"))
    new = form.get("new_password") or ""
    if not login:
        errors["username"] = "Enter your student number." if student else "Enter your username."
    if not code:
        errors["code"] = "Backup codes look like K7QM-2XPA."
    if len(new) < MIN_PASSWORD:
        errors["new_password"] = f"Password must be at least {MIN_PASSWORD} characters."
    elif new != (form.get("confirm_new_password") or ""):
        errors["confirm_new_password"] = "Passwords do not match."
    if errors:
        raise ValidationError(errors)

    # each site only recovers its own kind of account (same rule as logging in)
    user = conn.execute(
        f"""SELECT id FROM users
            WHERE (lower(username) = ? OR lower(student_number) = ?)
              AND role {"= 'student'" if student else "IN ('instructor', 'admin')"}
              AND deleted_at IS NULL AND is_active = 1""", (login, login)).fetchone()
    match = None
    if user:
        for row in conn.execute("SELECT id, code_hash FROM recovery_codes "
                                "WHERE user_id = ? AND used_at IS NULL", (user["id"],)):
            if check_password_hash(row["code_hash"], code):
                match = row["id"]
                break
    if match is None:
        # same message either way, so nobody can find out which usernames exist
        log.info("password reset failed for %r", login)
        raise ValidationError({"code": f"Wrong {'student number' if student else 'username'} or "
                                       "backup code, or that code was already used."})

    ts = _ts(now)
    conn.execute("UPDATE recovery_codes SET used_at = ? WHERE id = ?", (ts, match))
    conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                 (generate_password_hash(new), ts, user["id"]))
    conn.commit()
    log.info("password reset with backup code user=%s", user["id"])
    return user["id"]


def temporary_password(conn, username: str) -> str:
    """Last resort, run ON THE PI: `flask reset-password <username>`. Returns the new password."""
    user = conn.execute("SELECT id FROM users WHERE lower(username) = lower(?) AND deleted_at IS NULL",
                        (username.strip(),)).fetchone()
    if user is None:
        raise ValidationError({"username": f"No account with username {username!r}."})
    temp = "".join(secrets.choice(ALPHABET.lower()) for _ in range(10))
    conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                 (generate_password_hash(temp), _ts(), user["id"]))
    conn.commit()
    log.warning("password reset from the command line user=%s", user["id"])
    return temp


def filename(user) -> str:
    return f"backup-codes-{user['username']}.pdf"


def build_pdf(user, codes: list[str], *, site: str = "", school_font_files=None, now=None) -> bytes:
    """One A4 page: whose codes, the 10 codes in two columns, how to use them."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from .report import _school_font

    now = now or datetime.now()
    school = _school_font(school_font_files)
    st = {
        "school": ParagraphStyle("s", fontName=school, fontSize=16, leading=20, alignment=TA_CENTER),
        "title": ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=15, leading=19, alignment=TA_CENTER),
        "sub": ParagraphStyle("u", fontName="Helvetica", fontSize=10, leading=13, alignment=TA_CENTER,
                              textColor=colors.HexColor("#555555")),
        "body": ParagraphStyle("b", fontName="Helvetica", fontSize=10.5, leading=15),
        "warn": ParagraphStyle("w", fontName="Helvetica-Bold", fontSize=10.5, leading=15,
                               textColor=colors.HexColor("#9a3412")),
    }
    who = [["Name", full_name(user)],
           ["Student no." if user.get("role") == "student" else "Username", user["username"]],
           ["Generated", now.strftime("%B %d, %Y  %I:%M %p").replace(" 0", " ")]]
    who_t = Table(who, colWidths=[1.2 * inch, 4.2 * inch], hAlign="CENTER")
    who_t.setStyle(TableStyle([
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 10.5), ("FONT", (1, 0), (1, -1), "Helvetica", 10.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))

    half = (len(codes) + 1) // 2
    rows = [[f"{i + 1}.", codes[i], f"{i + half + 1}." if i + half < len(codes) else "",
             codes[i + half] if i + half < len(codes) else ""] for i in range(half)]
    codes_t = Table(rows, colWidths=[0.45 * inch, 2.2 * inch, 0.45 * inch, 2.2 * inch],
                    rowHeights=0.42 * inch, hAlign="CENTER")
    codes_t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 11), ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.grey),
        ("FONT", (1, 0), (1, -1), "Courier-Bold", 17), ("FONT", (3, 0), (3, -1), "Courier-Bold", 17),
        ("ALIGN", (0, 0), (0, -1), "RIGHT"), ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#999999")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f6f6")),
        ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, -1), (-1, -1), 8)]))

    where = f" at <b>{site}</b>" if site else ""
    is_student = user.get("role") == "student"
    login_name = "student number" if is_student else "username"
    steps = [
        f"1. Open the {'student' if is_student else 'instructor'} login page{where} "
        "and click <b>Forgot password?</b>",
        f"2. Enter your {login_name}, ONE of the codes above, and your new password.",
        "3. Each code works only once. Cross it out after you use it.",
        "4. Running low? Log in and go to <b>My account → Backup codes → Generate new codes</b>. "
        "The new set replaces this one, so this page will stop working.",
    ]
    doc = SimpleDocTemplate(buf := io.BytesIO(), pagesize=A4, leftMargin=inch, rightMargin=inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title="Backup codes", author="Attendance System")
    story = [Paragraph("Laguna State Polytechnic University", st["school"]),
             Paragraph("Attendance System", st["sub"]), Spacer(1, 18),
             Paragraph("Account Backup Codes", st["title"]), Spacer(1, 4),
             Paragraph("Use one of these if you forget your password.", st["sub"]), Spacer(1, 16),
             who_t, Spacer(1, 14), codes_t, Spacer(1, 20),
             Paragraph("How to use", st["warn"].clone("h", textColor=colors.black)), Spacer(1, 2)]
    story += [Paragraph(s, st["body"]) for s in steps]
    story += [Spacer(1, 14),
              Paragraph("Keep this file private. Anyone with your username and one of these codes "
                        "can change your password. Print it or save it somewhere safe.", st["warn"])]
    doc.build(story)
    return buf.getvalue()
