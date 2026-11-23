-- =====================================================================
--  RPi Attendance System - SQLite schema
--  Soft delete: rows with deleted_at IS NOT NULL are treated as deleted.
--  All timestamps stored as ISO-8601 text in local time (RTC-backed).
-- =====================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;   -- safer against sudden power loss

-- ---------------------------------------------------------------------
-- USERS (instructors + students + admin in one table)
-- ---------------------------------------------------------------------
CREATE TABLE users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    role                TEXT    NOT NULL CHECK (role IN ('admin','instructor','student')),
    username            TEXT    NOT NULL UNIQUE,
    password_hash       TEXT    NOT NULL,

    first_name          TEXT    NOT NULL,
    middle_name         TEXT,
    last_name           TEXT    NOT NULL,
    suffix              TEXT,                       -- Jr., III, etc.
    email               TEXT,

    student_number      TEXT    UNIQUE,             -- students only
    rfid_uid            TEXT    UNIQUE,             -- card ID; NULL until a card is assigned

    -- one-time signature captured at registration
    signature_path      TEXT,                       -- e.g. signatures/42.png
    signature_signed_at TEXT,
    must_resign         INTEGER NOT NULL DEFAULT 0, -- instructor reset -> student re-signs on next login

    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at          TEXT,
    deleted_at          TEXT,

    CHECK (role <> 'student' OR student_number IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- SUBJECTS (owned by an instructor)
-- ---------------------------------------------------------------------
CREATE TABLE subjects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    instructor_id   INTEGER NOT NULL REFERENCES users(id),
    code            TEXT    NOT NULL,               -- e.g. IT 312
    title           TEXT    NOT NULL,               -- e.g. Embedded Systems
    description     TEXT,
    units           REAL,
    school_year     TEXT    NOT NULL,               -- e.g. 2026-2027
    semester        TEXT    NOT NULL CHECK (semester IN ('1st','2nd','Summer')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    deleted_at      TEXT,
    UNIQUE (instructor_id, code, school_year, semester)
);

-- ---------------------------------------------------------------------
-- SECTIONS (a subject can have many sections; this is the "class")
-- ---------------------------------------------------------------------
CREATE TABLE sections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id      INTEGER NOT NULL REFERENCES subjects(id),
    name            TEXT    NOT NULL,               -- e.g. BSIT 3A
    join_code       TEXT    UNIQUE,                 -- students can self-enroll with this

    -- default check-in settings, copied into each new class_session
    default_checkin_mode        TEXT    NOT NULL DEFAULT 'auto'
                                    CHECK (default_checkin_mode IN ('auto','manual')),
    default_open_before_minutes INTEGER NOT NULL DEFAULT 5,   -- 7:00 class -> opens 6:55
    default_grace_minutes       INTEGER NOT NULL DEFAULT 15,  -- present until 7:15
    default_close_after_minutes INTEGER NOT NULL DEFAULT 30,  -- late until 7:30, then disabled

    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    deleted_at      TEXT,
    UNIQUE (subject_id, name)
);

-- ---------------------------------------------------------------------
-- ENROLLMENTS (student <-> section)
-- ---------------------------------------------------------------------
CREATE TABLE enrollments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id      INTEGER NOT NULL REFERENCES sections(id),
    student_id      INTEGER NOT NULL REFERENCES users(id),
    status          TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active','dropped')),
    enrolled_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    deleted_at      TEXT,
    UNIQUE (section_id, student_id)
);

-- ---------------------------------------------------------------------
-- SCHEDULES (recurring weekly slots of a section)
-- ---------------------------------------------------------------------
CREATE TABLE schedules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id      INTEGER NOT NULL REFERENCES sections(id),
    day_of_week     INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6), -- 0 = Monday
    start_time      TEXT    NOT NULL,               -- HH:MM
    end_time        TEXT    NOT NULL,               -- HH:MM
    room            TEXT,
    deleted_at      TEXT,
    CHECK (end_time > start_time)
);

-- ---------------------------------------------------------------------
-- CLASS SESSIONS (one actual meeting = one date column in the report)
-- ---------------------------------------------------------------------
CREATE TABLE class_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id          INTEGER NOT NULL REFERENCES sections(id),
    schedule_id         INTEGER REFERENCES schedules(id),  -- NULL for async/make-up
    session_date        TEXT    NOT NULL,                  -- YYYY-MM-DD
    start_time          TEXT,
    end_time            TEXT,
    type                TEXT    NOT NULL DEFAULT 'regular'
                            CHECK (type IN ('regular','async','makeup')),
    title               TEXT,                              -- e.g. "Async: Module 3"

    -- check-in control (applies to BOTH RFID and web check-in)
    --   auto  : state is computed from start_time + the minute settings below
    --   manual: state is whatever the instructor set in manual_state
    checkin_mode        TEXT    NOT NULL DEFAULT 'auto'
                            CHECK (checkin_mode IN ('auto','manual')),
    manual_state        TEXT    NOT NULL DEFAULT 'disabled'
                            CHECK (manual_state IN ('disabled','present','late')),
    open_before_minutes INTEGER NOT NULL DEFAULT 5,
    grace_minutes       INTEGER NOT NULL DEFAULT 15,
    close_after_minutes INTEGER NOT NULL DEFAULT 30,
    checkin_code        TEXT,                              -- rotating code for web check-in

    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    deleted_at          TEXT,

    CHECK (checkin_mode = 'manual' OR start_time IS NOT NULL),   -- auto needs a start time
    CHECK (open_before_minutes >= 0
           AND grace_minutes >= 0
           AND close_after_minutes >= grace_minutes)
);

-- ---------------------------------------------------------------------
-- CHECK-IN STATE LOG (every mode/state change: auto->manual, enable late, disable...)
-- ---------------------------------------------------------------------
CREATE TABLE session_state_changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES class_sessions(id),
    changed_by      INTEGER NOT NULL REFERENCES users(id),
    checkin_mode    TEXT    NOT NULL,
    manual_state    TEXT    NOT NULL,
    changed_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------
-- ATTENDANCE (one row per student per session)
-- ---------------------------------------------------------------------
CREATE TABLE attendance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES class_sessions(id),
    student_id      INTEGER NOT NULL REFERENCES users(id),
    status          TEXT    NOT NULL CHECK (status IN ('present','late','absent','excused')),
    method          TEXT    NOT NULL CHECK (method IN ('rfid','web','manual')),
    checked_in_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    ip_address      TEXT,                           -- web check-in only
    device_info     TEXT,                           -- user agent, to spot shared phones
    marked_by       INTEGER REFERENCES users(id),   -- instructor, for manual edits
    note            TEXT,
    deleted_at      TEXT,
    UNIQUE (session_id, student_id)                 -- no double check-in
);

-- ---------------------------------------------------------------------
-- RFID SCAN LOG (every tap, even failed ones - useful for card setup + debugging)
-- ---------------------------------------------------------------------
CREATE TABLE rfid_scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rfid_uid        TEXT    NOT NULL,
    user_id         INTEGER REFERENCES users(id),   -- NULL if card is unknown
    session_id      INTEGER REFERENCES class_sessions(id),
    result          TEXT    NOT NULL CHECK (result IN
                        ('ok','late','duplicate','unknown_card','not_enrolled','no_open_session',
                         'card_registration','card_in_use')),
    scanned_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------
-- CARD REGISTRATION (student self-registers their RFID card, one at a time)
--   waiting   -> reader is waiting for a tap (countdown)
--   detected  -> a card was tapped, waiting for the student to approve on the website
--   confirmed / cancelled / expired -> finished, lock released
-- active_lock is 1 while waiting/detected and NULL otherwise. Because it is UNIQUE,
-- the database itself guarantees only ONE registration can be in progress.
-- ---------------------------------------------------------------------
CREATE TABLE card_registrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES users(id),
    status          TEXT    NOT NULL DEFAULT 'waiting'
                        CHECK (status IN ('waiting','detected','confirmed','cancelled','expired')),
    active_lock     INTEGER UNIQUE CHECK (active_lock IS NULL OR active_lock = 1),
    detected_uid    TEXT,
    message         TEXT,                           -- e.g. "That card belongs to another account"
    started_at      TEXT    NOT NULL,
    expires_at      TEXT    NOT NULL,               -- tap deadline, then approve deadline
    detected_at     TEXT,
    finished_at     TEXT
);

-- ---------------------------------------------------------------------
-- AUDIT LOG (who deleted/edited what - backs the delete function)
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id),
    action          TEXT    NOT NULL,               -- delete, restore, update, reset_signature...
    entity          TEXT    NOT NULL,               -- table name
    entity_id       INTEGER NOT NULL,
    details         TEXT,                           -- JSON snapshot
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------
-- GENERATED REPORTS (optional history of PDFs)
-- ---------------------------------------------------------------------
CREATE TABLE reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id      INTEGER NOT NULL REFERENCES sections(id),
    generated_by    INTEGER NOT NULL REFERENCES users(id),
    file_path       TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE report_sessions (                      -- which date columns went into the report, in order
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    session_id      INTEGER NOT NULL REFERENCES class_sessions(id),
    column_order    INTEGER NOT NULL,
    PRIMARY KEY (report_id, session_id)
);

-- ---------------------------------------------------------------------
-- INDEXES
-- ---------------------------------------------------------------------
CREATE INDEX idx_subjects_instructor   ON subjects(instructor_id);
CREATE INDEX idx_sections_subject      ON sections(subject_id);
CREATE INDEX idx_enrollments_student   ON enrollments(student_id);
CREATE INDEX idx_schedules_section     ON schedules(section_id);
CREATE INDEX idx_sessions_section_date ON class_sessions(section_id, session_date);
CREATE INDEX idx_sessions_date         ON class_sessions(session_date);
CREATE INDEX idx_state_changes_session ON session_state_changes(session_id);
CREATE INDEX idx_attendance_student    ON attendance(student_id);
CREATE INDEX idx_rfid_scans_time       ON rfid_scans(scanned_at);
