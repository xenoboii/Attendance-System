"""
Database migrations: small, numbered changes applied on top of schema.sql.

The database remembers which ones it already has (PRAGMA user_version), so
existing databases on the laptop or the Pi are upgraded automatically on startup
and NO data is lost. To change the database later: append a new entry to
MIGRATIONS - never edit an old one.
"""
import logging

log = logging.getLogger(__name__)

MIGRATIONS = [
    # 1: profile fields for the account page
    """
    ALTER TABLE users ADD COLUMN program TEXT;          -- student: BSIT, BSCS ...
    ALTER TABLE users ADD COLUMN year_level INTEGER;    -- student: 1-5
    ALTER TABLE users ADD COLUMN contact_number TEXT;
    ALTER TABLE users ADD COLUMN department TEXT;       -- instructor: shown on reports
    ALTER TABLE users ADD COLUMN title TEXT;            -- instructor: Engr., Prof., Dr. ...
    """,
    # 2: when a manual session was first opened (so we know when it has ended -> absences)
    """
    ALTER TABLE class_sessions ADD COLUMN opened_at TEXT;
    CREATE INDEX IF NOT EXISTS idx_sessions_schedule_date ON class_sessions(schedule_id, session_date);
    """,
    # 3: "End session" - once set, check-in stays closed no matter the mode (until reopened)
    """
    ALTER TABLE class_sessions ADD COLUMN ended_at TEXT;
    """,
    # 4: personal settings (Settings page)
    """
    ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'system';          -- system | light | dark
    ALTER TABLE users ADD COLUMN text_size TEXT NOT NULL DEFAULT 'normal';      -- normal | large
    ALTER TABLE users ADD COLUMN report_sign_default INTEGER NOT NULL DEFAULT 0;
    """,
    # 5: backup codes for "Forgot password?" (only the hash is stored, each code works once)
    """
    CREATE TABLE IF NOT EXISTS recovery_codes (
        id         INTEGER PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code_hash  TEXT NOT NULL,
        created_at TEXT NOT NULL,
        used_at    TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_recovery_codes_user ON recovery_codes(user_id);
    """,
    # 6: student's section letter (Program BSCPE + Year 4 + Section A -> "BSCPE 4A").
    #    Students who typed "BSCPE 4A" into Program get it split up automatically.
    """
    ALTER TABLE users ADD COLUMN section_letter TEXT;
    UPDATE users
       SET year_level = CAST(substr(program, -2, 1) AS INTEGER),
           section_letter = upper(substr(program, -1)),
           program = trim(substr(program, 1, length(program) - 3))
     WHERE role = 'student' AND upper(program) GLOB '* [1-5][A-Z]';
    """,
    # 7: device readings the hardware process shares with the website (battery %, charging ...)
    """
    CREATE TABLE IF NOT EXISTS device_status (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        updated_at TEXT NOT NULL
    );
    """,
    # 8: "No class" days (holiday, suspended classes ...) on a section's weekly schedule.
    #    A date here is skipped when sessions are created automatically for that day.
    """
    CREATE TABLE IF NOT EXISTS schedule_exceptions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id      INTEGER NOT NULL REFERENCES sections(id),
        exception_date  TEXT NOT NULL,
        reason          TEXT NOT NULL,
        created_by      INTEGER REFERENCES users(id),
        created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE (section_id, exception_date)
    );
    CREATE INDEX IF NOT EXISTS idx_schedule_exceptions_section ON schedule_exceptions(section_id, exception_date);
    """,
    # 9: "No class, but still mark everyone present" (e.g. the Saturday class is cancelled,
    #    and the instructor covers it together with Monday's class, so Saturday still counts).
    """
    ALTER TABLE schedule_exceptions ADD COLUMN mark_present INTEGER NOT NULL DEFAULT 0;
    """,
    # 10: web check-ins are signed on the spot. The drawing is a small PNG file kept for
    #     60 days (see services/checkin_signatures.py); card taps use the stored signature.
    """
    ALTER TABLE attendance ADD COLUMN signature_path TEXT;
    """,
    # 11: add the Developer role. Rebuild users with foreign keys temporarily disabled
    # by migrate(), so existing accounts and all references retain their IDs.
    """
    CREATE TABLE users_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL CHECK (role IN ('admin','instructor','student','developer')),
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        first_name TEXT NOT NULL,
        middle_name TEXT,
        last_name TEXT NOT NULL,
        suffix TEXT,
        email TEXT,
        student_number TEXT UNIQUE,
        rfid_uid TEXT UNIQUE,
        signature_path TEXT,
        signature_signed_at TEXT,
        must_resign INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT,
        deleted_at TEXT,
        program TEXT,
        year_level INTEGER,
        contact_number TEXT,
        department TEXT,
        title TEXT,
        theme TEXT NOT NULL DEFAULT 'system',
        text_size TEXT NOT NULL DEFAULT 'normal',
        report_sign_default INTEGER NOT NULL DEFAULT 0,
        section_letter TEXT,
        CHECK (role <> 'student' OR student_number IS NOT NULL)
    );
    INSERT INTO users_new SELECT * FROM users;
    UPDATE sqlite_sequence SET seq = MAX(seq, COALESCE(
        (SELECT seq FROM sqlite_sequence WHERE name = 'users'), 0))
        WHERE name = 'users_new';
    DROP TABLE users;
    ALTER TABLE users_new RENAME TO users;
    """,
    # 12: course abbreviations, managed by Developers. Keep existing profile values.
    """
    CREATE TABLE courses (
        code TEXT PRIMARY KEY COLLATE NOCASE,
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        created_by INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT
    );
    INSERT INTO courses (code) VALUES
        ('BSABE'), ('BSAEROE'), ('BSCERE'), ('BSCHE'), ('BSCE'), ('BSCPE'),
        ('BSEE'), ('BSECE'), ('BSGE'), ('BSGEOE'), ('BSIE'), ('BSMARE'),
        ('BSMATE'), ('BSME'), ('BSMECE'), ('BSMETE'), ('BSEM'), ('BSPETE'), ('BSSE'),
        ('BSIT'), ('BSCS');
    INSERT OR IGNORE INTO courses (code)
        SELECT DISTINCT upper(trim(program)) FROM users
        WHERE program IS NOT NULL AND trim(program) <> '';
    """,
    # 13: freeze academic identity after setup, and hold sit-in requests outside enrollments.
    """
    ALTER TABLE users ADD COLUMN academic_locked INTEGER NOT NULL DEFAULT 0;
    UPDATE users SET academic_locked = 1
        WHERE role = 'student' AND coalesce(trim(program), '') <> ''
          AND year_level BETWEEN 1 AND 5 AND coalesce(trim(section_letter), '') <> '';
    CREATE TABLE join_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL REFERENCES sections(id),
        student_id INTEGER NOT NULL REFERENCES users(id),
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        reviewed_at TEXT,
        reviewed_by INTEGER REFERENCES users(id),
        UNIQUE(section_id, student_id)
    );
    CREATE INDEX idx_join_requests_section_status ON join_requests(section_id, status);
    """,
    # 14: the support role is called Admin. Existing Developer accounts keep their IDs.
    """
    UPDATE users SET role = 'admin' WHERE role = 'developer';
    """,
    # 15: instructors may require approval even for matching students.
    """
    ALTER TABLE sections ADD COLUMN join_approval TEXT NOT NULL DEFAULT 'outsiders'
        CHECK (join_approval IN ('outsiders', 'everyone'));
    """,
    # 16: allow instructors to admit any student's academic section with the code.
    """
    CREATE TABLE sections_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER NOT NULL REFERENCES subjects(id),
        name TEXT NOT NULL,
        join_code TEXT UNIQUE,
        default_checkin_mode TEXT NOT NULL DEFAULT 'auto' CHECK(default_checkin_mode IN ('auto','manual')),
        default_open_before_minutes INTEGER NOT NULL DEFAULT 5,
        default_grace_minutes INTEGER NOT NULL DEFAULT 15,
        default_close_after_minutes INTEGER NOT NULL DEFAULT 30,
        created_at TEXT NOT NULL DEFAULT(datetime('now','localtime')),
        deleted_at TEXT,
        join_approval TEXT NOT NULL DEFAULT 'outsiders' CHECK(join_approval IN ('outsiders','everyone','anyone')),
        UNIQUE(subject_id, name)
    );
    INSERT INTO sections_new SELECT * FROM sections;
    UPDATE sqlite_sequence SET seq = MAX(seq, COALESCE(
        (SELECT seq FROM sqlite_sequence WHERE name='sections'), 0)) WHERE name='sections_new';
    DROP TABLE sections;
    ALTER TABLE sections_new RENAME TO sections;
    CREATE INDEX idx_sections_subject ON sections(subject_id);
    """,
    # 17: distinguish vacation dates from other no-class exceptions.
    """
    ALTER TABLE schedule_exceptions ADD COLUMN is_vacation INTEGER NOT NULL DEFAULT 0 CHECK(is_vacation IN (0,1));
    """,
    # 18: small, normalized profile pictures; database backups include the pictures.
    """
    CREATE TABLE profile_photos (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        image BLOB NOT NULL,
        revision TEXT NOT NULL
    );
    """,
    # 19: instructor departments (colleges), managed by the admin like courses. Instructors pick
    #     one from a dropdown; a department someone typed in earlier is kept.
    """
    CREATE TABLE departments (
        name TEXT PRIMARY KEY COLLATE NOCASE,
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        created_by INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT
    );
    INSERT INTO departments (name) VALUES
        ('College of Agriculture'),
        ('College of Arts and Sciences'),
        ('College of Business Administration and Accountancy'),
        ('College of Computer Studies'),
        ('College of Criminal Justice Education'),
        ('College of Engineering'),
        ('College of Fisheries'),
        ('College of Food Nutrition and Dietetics'),
        ('College of Hospitality Management and Tourism'),
        ('College of Industrial Technology'),
        ('College of Law'),
        ('College of Nursing and Allied Health'),
        ('College of Teacher Education');
    INSERT OR IGNORE INTO departments (name)
        SELECT DISTINCT trim(department) FROM users
        WHERE department IS NOT NULL AND trim(department) <> '';
    """,
    # 20: "End semester": an ended subject is archived (no new sessions, no check-in, read-only).
    """
    ALTER TABLE subjects ADD COLUMN ended_at TEXT;
    """,
    # 21: drop rule - the instructor sets how many absences get a student dropped (0 = no rule).
    """
    ALTER TABLE sections ADD COLUMN drop_absences INTEGER NOT NULL DEFAULT 4;
    """,
    # 22: settings the admin can change (e.g. how long check-in signatures are kept).
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        updated_at TEXT NOT NULL
    );
    """,
    # 23: deleted accounts - what the account's username / student number / card were, so the
    #     admin can restore it (the users row keeps a "deleted-<id>-..." name meanwhile).
    """
    CREATE TABLE IF NOT EXISTS deleted_accounts (
        user_id        INTEGER PRIMARY KEY REFERENCES users(id),
        username       TEXT NOT NULL,
        student_number TEXT,
        rfid_uid       TEXT,
        deleted_by     INTEGER REFERENCES users(id),
        deleted_at     TEXT NOT NULL,
        details        TEXT
    );
    """,
    # 24: link a day-wide async exception to its activity, even with multiple sessions on the date.
    """
    ALTER TABLE schedule_exceptions ADD COLUMN async_session_id INTEGER
        REFERENCES class_sessions(id) ON DELETE SET NULL;
    UPDATE schedule_exceptions SET async_session_id = (
        SELECT CASE WHEN COUNT(*) = 1 THEN MAX(cs.id) END FROM class_sessions cs
        WHERE cs.section_id = schedule_exceptions.section_id AND cs.session_date = schedule_exceptions.exception_date
          AND cs.type = 'async' AND cs.schedule_id IS NULL AND cs.deleted_at IS NULL
    ) WHERE reason = 'Async';
    UPDATE class_sessions SET start_time = (SELECT start_time FROM schedules WHERE id = class_sessions.schedule_id),
                              end_time = (SELECT end_time FROM schedules WHERE id = class_sessions.schedule_id)
    WHERE type = 'async' AND schedule_id IS NOT NULL AND start_time IS NULL;
    """,
    # 25: check-out. The instructor can require it per session and opens it near the end of
    #     class; students who checked in but didn't check out become absent when it ends
    #     (checkout_done_at = that rule was applied, so it only runs once).
    """
    ALTER TABLE class_sessions ADD COLUMN require_checkout INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE class_sessions ADD COLUMN checkout_open_at TEXT;
    ALTER TABLE class_sessions ADD COLUMN checkout_done_at TEXT;
    ALTER TABLE attendance ADD COLUMN checked_out_at TEXT;
    """,
    # 26: accent color (Settings -> Color). 'default' = the portal's own color
    #     (indigo for students, teal for instructors).
    """
    ALTER TABLE users ADD COLUMN accent TEXT NOT NULL DEFAULT 'default';
    """,
]


def migrate(conn) -> int:
    """Apply missing migrations. Returns the database version afterwards."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in enumerate(MIGRATIONS, start=1):
        if number <= version:
            continue
        rebuilding_table = number in (11, 16)
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        if rebuilding_table:
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript(f"BEGIN; {sql} PRAGMA user_version = {number};")
            if rebuilding_table and conn.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError(f"Foreign key check failed during migration {number}")
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception("migration %s failed", number)
            raise
        finally:
            if rebuilding_table:
                conn.execute(f"PRAGMA foreign_keys = {int(foreign_keys)}")
        log.info("database migrated to version %s", number)
        version = number
    return version
