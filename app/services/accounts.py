"""
Accounts: registration, login, signatures. Plain Python (no Flask) so it can be tested directly.
"""
import base64
import logging
import re
from datetime import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger(__name__)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_SIGNATURE_BYTES = 500_000
MIN_PASSWORD = 8
STUDENT_NUMBER_RE = re.compile(r"^[A-Za-z0-9-]{4,20}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")


class ValidationError(Exception):
    """Holds a dict of field -> message to show next to the form fields."""

    def __init__(self, errors: dict):
        super().__init__("; ".join(str(v) for v in errors.values()))
        self.errors = errors


def _ts(dt=None):
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _clean(form: dict, key: str) -> str:
    return (form.get(key) or "").strip()


def _validate_common(form: dict, errors: dict) -> None:
    if not _clean(form, "first_name"):
        errors["first_name"] = "First name is required."
    if not _clean(form, "last_name"):
        errors["last_name"] = "Last name is required."
    pw = form.get("password") or ""
    if len(pw) < MIN_PASSWORD:
        errors["password"] = f"Password must be at least {MIN_PASSWORD} characters."
    elif pw != (form.get("confirm_password") or ""):
        errors["confirm_password"] = "Passwords do not match."
    email = _clean(form, "email")
    if email and "@" not in email:
        errors["email"] = "Enter a valid email or leave it blank."


def is_unclaimed(user) -> bool:
    """A student record created by an instructor's import that nobody has registered yet."""
    return user["role"] == "student" and user["password_hash"] == "" and user["deleted_at"] is None


def register_student(conn, form: dict) -> int:
    """Create a student account. Username = student number. Returns the new user id."""
    errors = {}
    number = _clean(form, "student_number").upper()
    if not STUDENT_NUMBER_RE.match(number):
        errors["student_number"] = "Use 4-20 letters, numbers or dashes (e.g. 2026-0001)."
    _validate_common(form, errors)
    # course + year + section, the same choices (and checks) as My account; saved locked
    blank = {"program": None, "year_level": None, "section_letter": None}
    academic = academic_values(conn, blank, form, errors)
    courses_exist = conn.execute("SELECT 1 FROM courses WHERE is_active = 1").fetchone()
    if courses_exist and not academic["program"]:
        for key in ("program", "year_level", "section_letter"):
            errors.setdefault(key, "Choose your course, year, and section.")
    existing = None if errors else conn.execute(
        "SELECT * FROM users WHERE student_number = ? OR username = ?", (number, number)).fetchone()
    # An instructor's import can create the student before they register ("unclaimed": no
    # password yet). Registering with the same student number claims that record, so their
    # classes, attendance, card and signature are already there.
    unclaimed = existing is not None and is_unclaimed(existing)
    if existing is not None and not unclaimed:
        errors["student_number"] = "This student number is already registered."
    if errors:
        raise ValidationError(errors)
    locked = int(all(academic.values()))          # same as My account: locked once all three are set
    if unclaimed:
        # what the student enters wins over the imported record (an empty choice keeps the old value)
        conn.execute(
            """UPDATE users SET password_hash = ?, first_name = ?, middle_name = ?, last_name = ?, suffix = ?,
                                email = COALESCE(?, email), username = ?, student_number = ?,
                                program = COALESCE(?, program), year_level = COALESCE(?, year_level),
                                section_letter = COALESCE(?, section_letter),
                                academic_locked = MAX(academic_locked, ?), updated_at = ?
               WHERE id = ? AND password_hash = ''""",
            (generate_password_hash(form["password"]), _clean(form, "first_name"),
             _clean(form, "middle_name") or None, _clean(form, "last_name"), _clean(form, "suffix") or None,
             _clean(form, "email") or None, number, number,
             academic["program"], academic["year_level"], academic["section_letter"], locked,
             _ts(), existing["id"]))
        conn.commit()
        log.info("student claimed imported record id=%s number=%s", existing["id"], number)
        return existing["id"]

    cur = conn.execute(
        """INSERT INTO users (role, username, password_hash, first_name, middle_name,
                              last_name, suffix, email, student_number,
                              program, year_level, section_letter, academic_locked)
           VALUES ('student', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (number, generate_password_hash(form["password"]), _clean(form, "first_name"),
         _clean(form, "middle_name") or None, _clean(form, "last_name"),
         _clean(form, "suffix") or None, _clean(form, "email") or None, number,
         academic["program"], academic["year_level"], academic["section_letter"], locked),
    )
    conn.commit()
    log.info("student registered id=%s number=%s", cur.lastrowid, number)
    return cur.lastrowid


def register_instructor(conn, form: dict, required_code: str) -> int:
    errors = {}
    username = _clean(form, "username").lower()
    if not USERNAME_RE.match(username):
        errors["username"] = "Use 3-30 letters, numbers, dots, dashes or underscores."
    if _clean(form, "registration_code") != required_code:
        errors["registration_code"] = "Wrong instructor registration code."
    _validate_common(form, errors)
    if not errors and conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        errors["username"] = "This username is already taken."
    if errors:
        raise ValidationError(errors)

    cur = conn.execute(
        """INSERT INTO users (role, username, password_hash, first_name, middle_name,
                              last_name, suffix, email)
           VALUES ('instructor', ?, ?, ?, ?, ?, ?, ?)""",
        (username, generate_password_hash(form["password"]), _clean(form, "first_name"),
         _clean(form, "middle_name") or None, _clean(form, "last_name"),
         _clean(form, "suffix") or None, _clean(form, "email") or None),
    )
    conn.commit()
    log.info("instructor registered id=%s username=%s", cur.lastrowid, username)
    return cur.lastrowid


def create_admin(conn, form: dict) -> int:
    """Local provisioning only. Public registration never accepts a supplied role."""
    errors = {}
    username = _clean(form, "username").lower()
    if not USERNAME_RE.fullmatch(username):
        errors["username"] = "Use 3-30 letters, numbers, dots, dashes or underscores."
    _validate_common(form, errors)
    if conn.execute("SELECT 1 FROM users WHERE lower(username) = ?", (username,)).fetchone():
        errors["username"] = "This username is already taken."
    if errors:
        raise ValidationError(errors)
    with conn:
        cur = conn.execute(
            """INSERT INTO users (role, username, password_hash, first_name, last_name)
               VALUES ('admin', ?, ?, ?, ?)""",
            (username, generate_password_hash(form["password"]),
             _clean(form, "first_name"), _clean(form, "last_name")))
    log.info("admin created id=%s username=%s", cur.lastrowid, username)
    return cur.lastrowid


def authenticate(conn, login: str, password: str):
    """Login with username OR student number. Returns the user row or None."""
    login = (login or "").strip()
    user = conn.execute(
        """SELECT * FROM users
           WHERE (lower(username) = lower(?) OR upper(student_number) = upper(?))
             AND deleted_at IS NULL""",
        (login, login),
    ).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password or ""):
        log.info("login failed for %r", login)
        return None
    if not user["is_active"]:
        log.info("login blocked, inactive account id=%s", user["id"])
        return None
    log.info("login ok id=%s role=%s", user["id"], user["role"])
    return user


def get_user(conn, user_id: int):
    return conn.execute(
        "SELECT * FROM users WHERE id = ? AND deleted_at IS NULL AND is_active = 1", (user_id,)
    ).fetchone()


def needs_signature(user) -> bool:
    return user["role"] == "student" and (not user["signature_path"] or bool(user["must_resign"]))


def decode_signature(data_url: str) -> bytes:
    """PNG data URL from the signature pad -> PNG bytes, or ValidationError({"signature": ...})."""
    prefix = "data:image/png;base64,"
    if not data_url or not data_url.startswith(prefix):
        raise ValidationError({"signature": "Please draw your signature first."})
    try:
        png = base64.b64decode(data_url[len(prefix):], validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValidationError({"signature": "The signature image is invalid. Please try again."})
    if not png.startswith(PNG_MAGIC):
        raise ValidationError({"signature": "The signature image is invalid. Please try again."})
    if len(png) > MAX_SIGNATURE_BYTES:
        raise ValidationError({"signature": "The signature image is too large."})
    return png


def save_signature(conn, user_id: int, data_url: str, signature_dir: Path) -> str:
    """Save a PNG data URL from the signature pad. Returns the relative path."""
    png = decode_signature(data_url)
    signature_dir.mkdir(parents=True, exist_ok=True)
    (signature_dir / f"{user_id}.png").write_bytes(png)
    rel = f"signatures/{user_id}.png"
    conn.execute(
        """UPDATE users SET signature_path = ?, signature_signed_at = ?, must_resign = 0,
                            updated_at = ? WHERE id = ?""",
        (rel, _ts(), _ts(), user_id),
    )
    conn.commit()
    log.info("signature saved for user=%s (%d bytes)", user_id, len(png))
    return rel


# ================================================================ account page
CONTACT_RE = re.compile(r"^(09\d{9}|\+639\d{9})$")   # PH mobile: 09171234567 or +639171234567
PROFILE_LIMITS = {"program": 20, "department": 80, "title": 20}
SECTION_LETTERS = tuple("ABCDEFG")


def academic_values(conn, user, form, errors):
    """Validate the shared course/year/section choices, preserving unchanged legacy values."""
    program = " ".join(str(form.get("program", user["program"]) or "").upper().split())
    year = str(form.get("year_level", user["year_level"]) or "").strip()
    letter = str(form.get("section_letter", user["section_letter"]) or "").strip().upper()
    if program and program != (user["program"] or "").upper() and not conn.execute(
            "SELECT 1 FROM courses WHERE code = ? AND is_active = 1", (program,)).fetchone():
        errors["program"] = "Choose an available course. Ask the admin to add a missing course."
    if len(program) > 10:
        errors["program"] = "Use the course abbreviation (max 10 characters), e.g. BSCPE."
    if year and year not in ("1", "2", "3", "4", "5"):
        errors["year_level"] = "Choose a year level."
    if letter and letter not in SECTION_LETTERS and letter != user["section_letter"]:
        errors["section_letter"] = "Choose a section from A to G."
    if program or year or letter:
        for key, value in (("program", program), ("year_level", year), ("section_letter", letter)):
            if not value:
                errors[key] = "Choose your course, year, and section together."
    return {"program": program or None,
            "year_level": int(year) if year in ("1", "2", "3", "4", "5") else None,
            "section_letter": letter or None}


def update_profile(conn, user, form: dict) -> None:
    """Students: program, year level, contact, email (name + student number are locked).
    Instructors: name, title, department, contact, email."""
    errors, values = {}, {}

    contact = _clean(form, "contact_number").replace(" ", "").replace("-", "")
    if contact and not CONTACT_RE.match(contact):
        errors["contact_number"] = "Use 09XXXXXXXXX or +639XXXXXXXXX."
    values["contact_number"] = contact or None

    email = _clean(form, "email")
    if email and "@" not in email:
        errors["email"] = "Enter a valid email or leave it blank."
    values["email"] = email or None

    if user["role"] == "student":
        academic = academic_values(conn, user, form, errors)
        # Read the latest lock, rather than trusting a row read before another request saved it.
        latest = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if latest["academic_locked"]:
            for key, value in academic.items():
                if value != latest[key]:
                    errors[key] = "Your course, year, and section are locked. Ask the admin to correct them."
        values.update(academic)
        values["academic_locked"] = int(bool(latest["academic_locked"] or all(academic.values())))
    else:
        for key in ("first_name", "last_name"):
            if not _clean(form, key):
                errors[key] = "This field is required."
            values[key] = _clean(form, key)
        values["middle_name"] = _clean(form, "middle_name") or None
        values["suffix"] = _clean(form, "suffix") or None
        for key in ("title", "department"):
            v = _clean(form, key)
            if len(v) > PROFILE_LIMITS[key]:
                errors[key] = f"Max {PROFILE_LIMITS[key]} characters."
            values[key] = v or None
        # department comes from the admin's list (dropdown); an older / disabled value may stay as it is
        dept = values["department"]
        if dept and dept != (user["department"] or "") and not conn.execute(
                "SELECT 1 FROM departments WHERE name = ? AND is_active = 1", (dept,)).fetchone():
            errors["department"] = "Choose a department from the list."

    if errors:
        raise ValidationError(errors)
    sets = ", ".join(f"{k} = ?" for k in values)
    if user["role"] == "student":
        cur = conn.execute(f"""UPDATE users SET {sets}, updated_at = ? WHERE id = ?
            AND (academic_locked = 0 OR (program IS ? AND year_level IS ? AND section_letter IS ?))""",
            (*values.values(), _ts(), user["id"], academic["program"], academic["year_level"], academic["section_letter"]))
        if not cur.rowcount:
            conn.rollback()
            raise ValidationError({"program": "Your academic details changed. Refresh and contact the admin."})
    else:
        conn.execute(f"UPDATE users SET {sets}, updated_at = ? WHERE id = ?",
                     (*values.values(), _ts(), user["id"]))
    conn.commit()
    log.info("profile updated user=%s fields=%s", user["id"], list(values))


def change_password(conn, user, form: dict) -> None:
    errors = {}
    if not check_password_hash(user["password_hash"], form.get("current_password") or ""):
        errors["current_password"] = "Your current password is wrong."
    new = form.get("new_password") or ""
    if len(new) < MIN_PASSWORD:
        errors["new_password"] = f"Password must be at least {MIN_PASSWORD} characters."
    elif new != (form.get("confirm_new_password") or ""):
        errors["confirm_new_password"] = "Passwords do not match."
    if errors:
        raise ValidationError(errors)
    conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                 (generate_password_hash(new), _ts(), user["id"]))
    conn.commit()
    log.info("password changed user=%s", user["id"])


def student_identity_values(conn, student_id, form):
    errors = {}
    number = _clean(form, "student_number").upper()
    if not STUDENT_NUMBER_RE.match(number):
        errors["student_number"] = "Use 4-20 letters, numbers or dashes (e.g. 2026-0001)."
    elif conn.execute("SELECT 1 FROM users WHERE (upper(student_number) = ? OR upper(username) = ?) AND id <> ?",
                      (number, number, student_id)).fetchone():
        errors["student_number"] = "Another account already uses this student number."
    for key in ("first_name", "last_name"):
        if not _clean(form, key):
            errors[key] = "This field is required."
    if errors:
        raise ValidationError(errors)
    return {"first_name": _clean(form, "first_name"), "middle_name": _clean(form, "middle_name") or None,
            "last_name": _clean(form, "last_name"), "suffix": _clean(form, "suffix") or None,
            "student_number": number, "username": number}


def correct_student_identity(conn, student_id: int, form: dict) -> None:
    """Admin fixes a student's name / student number. Username follows the number."""
    values = student_identity_values(conn, student_id, form)
    number = values["student_number"]
    conn.execute(
        """UPDATE users SET first_name = ?, middle_name = ?, last_name = ?, suffix = ?,
                            student_number = ?, username = ?, updated_at = ?
           WHERE id = ? AND role = 'student'""",
        (_clean(form, "first_name"), _clean(form, "middle_name") or None, _clean(form, "last_name"),
         _clean(form, "suffix") or None, number, number, _ts(), student_id))
    conn.commit()
    log.info("identity corrected for student=%s", student_id)


def remove_card(conn, student_id: int) -> str | None:
    """Unlink a lost / replaced RFID card. Returns the old card ID."""
    row = conn.execute("SELECT rfid_uid FROM users WHERE id = ?", (student_id,)).fetchone()
    conn.execute("UPDATE users SET rfid_uid = NULL, updated_at = ? WHERE id = ?", (_ts(), student_id))
    conn.commit()
    log.info("rfid card removed for student=%s (was %s)", student_id, row["rfid_uid"] if row else None)
    return row["rfid_uid"] if row else None


THEMES = ("system", "light", "dark")
TEXT_SIZES = ("normal", "large")
# Settings -> Color. 'none' = plain light / dark, no tint. 'default' = the portal's own color. The colors themselves are in style.css
# (html[data-accent=...]); keep both lists in sync.
ACCENTS = ("none", "default", "blue", "green", "yellow", "orange", "red", "pink", "purple", "maroon", "gray")


def update_settings(conn, user, form: dict) -> None:
    """Settings page: theme, accent color, text size, and (instructors) report signature default."""
    theme = form.get("theme", "system")
    size = form.get("text_size", "normal")
    accent = form.get("accent") or (user["accent"] if "accent" in user.keys() else "default")
    if theme not in THEMES or size not in TEXT_SIZES or accent not in ACCENTS:
        raise ValidationError({"settings": "Invalid choice."})
    sign = 1 if (user["role"] != "student" and form.get("report_sign_default") == "1") else 0
    conn.execute("UPDATE users SET theme = ?, text_size = ?, accent = ?, report_sign_default = ?, updated_at = ? WHERE id = ?",
                 (theme, size, accent, sign, _ts(), user["id"]))
    conn.commit()


def can_change_signature(user) -> bool:
    """Students sign once (or when the admin resets it); instructors any time."""
    return user["role"] != "student" or needs_signature(user)


def full_name(user) -> str:
    parts = [user["first_name"], user["middle_name"] and f'{user["middle_name"][0]}.',
             user["last_name"], user["suffix"]]
    return " ".join(p for p in parts if p)
