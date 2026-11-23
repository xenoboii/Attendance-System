"""
Demo data for testing without real users or hardware.

TEST ACCOUNTS (demo only - never use on the real system):
    instructor : prof        / prof123
    students   : 2026-0001   / student123   Maria Santos   card CARD0001
                 2026-0002   / student123   Jose Reyes     card CARD0002
                 2026-0003   / student123   Ana Lim        card CARD0003
                 2026-0004   / student123   Mark Tan       (no card - use to test card registration)

Sessions created for TODAY:
    IT312 BSIT 3A  auto mode, starts 1 minute before you ran seed-demo -> open as PRESENT now
    IT312 BSIT 3A  async "Module 1 activity" -> everyone already marked present
"""
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

STUDENTS = [
    ("2026-0001", "Maria", "Santos", "CARD0001"),
    ("2026-0002", "Jose", "Reyes", "CARD0002"),
    ("2026-0003", "Ana", "Lim", "CARD0003"),
    ("2026-0004", "Mark", "Tan", None),
]


def seed_demo(conn, now: datetime | None = None) -> None:
    now = now or datetime.now()
    x = conn.execute

    x("""INSERT INTO users (id, role, username, password_hash, first_name, last_name)
         VALUES (1, 'instructor', 'prof', ?, 'Juan', 'Dela Cruz')""",
      (generate_password_hash("prof123"),))

    student_pw = generate_password_hash("student123")
    for number, first, last, card in STUDENTS:
        x("""INSERT INTO users (role, username, password_hash, first_name, last_name,
                                student_number, rfid_uid)
             VALUES ('student', ?, ?, ?, ?, ?, ?)""",
          (number, student_pw, first, last, number, card))

    x("""INSERT INTO subjects (id, instructor_id, code, title, school_year, semester)
         VALUES (1, 1, 'IT312', 'Embedded Systems', '2026-2027', '1st')""")
    x("INSERT INTO sections (id, subject_id, name, join_code) VALUES (1, 1, 'BSIT 3A', 'JOIN3A')")
    # enrolled a week ago, so they count for today's demo classes
    x("""INSERT INTO enrollments (section_id, student_id, enrolled_at)
         SELECT 1, id, ? FROM users WHERE role = 'student'""",
      ((now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),))

    start = (now - timedelta(minutes=1)).replace(second=0, microsecond=0)
    x("""INSERT INTO class_sessions (section_id, session_date, start_time, end_time, type, created_by, created_at)
         VALUES (1, ?, ?, ?, 'regular', 1, ?)""",
      (start.strftime("%Y-%m-%d"), start.strftime("%H:%M"),
       (start + timedelta(hours=1)).strftime("%H:%M"),
       (start - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")))   # added before class
    conn.commit()
    # an async activity, created the normal way: everyone enrolled is marked present
    from .services.sessions import create_session
    create_session(conn, 1, 1, {"type": "async", "session_date": now.strftime("%Y-%m-%d"),
                                "title": "Module 1 activity"}, now)
