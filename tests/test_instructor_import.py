"""Instructor imports their own-subjects backup into a rebuilt device; students claim by registering."""
import io
import zipfile
from datetime import datetime

import pytest

from app import create_app
from app.db import connect, init_db
from app.migrations import MIGRATIONS
from app.seed import seed_demo
from app.services import accounts, backup, classes, instructor_import, sessions
from app.services.accounts import ValidationError

from .test_auth import PROF_SITE, login

PNG = b"\x89PNG\r\n\x1a\n fake"
N = len(MIGRATIONS)
ACADEMIC = {"program": "BSCPE", "year_level": "4", "section_letter": "A"}   # required when registering
PROF_FORM = {"username": "newprof", "first_name": "Juan", "last_name": "Dela Cruz", "password": "newpass123",
             "confirm_password": "newpass123", "registration_code": "X"}


def make_backup(app):
    """A backup of the demo instructor's subjects, with a stored signature and a check-in drawing."""
    cfg = app.config
    d = cfg["SIGNATURE_DIR"]
    (d / "checkins").mkdir(parents=True, exist_ok=True)
    with connect(cfg["DATABASE"]) as c:
        c.execute("UPDATE users SET signature_path = 'signatures/2.png', rfid_uid = COALESCE(rfid_uid, 'CARDAAAA') WHERE id = 2")
        att = c.execute("SELECT id FROM attendance WHERE student_id = 2 LIMIT 1").fetchone()
        if att is None:
            ses = c.execute("SELECT id FROM class_sessions WHERE section_id = 1 LIMIT 1").fetchone()[0]
            att = (c.execute("INSERT INTO attendance (session_id, student_id, status, method) VALUES (?, 2, 'present', 'web')",
                             (ses,)).lastrowid,)
        c.execute("UPDATE attendance SET signature_path = ? WHERE id = ?", (f"signatures/checkins/{att[0]}.png", att[0]))
        c.commit()
        (d / "2.png").write_bytes(PNG)
        (d / "checkins" / f"{att[0]}.png").write_bytes(PNG + b"drawn")
        prof = dict(c.execute("SELECT * FROM users WHERE id = 1").fetchone())
        data = backup.create_for_instructor(c, cfg["DATABASE"], d, prof)
        counts = {"attendance": c.execute("""SELECT COUNT(*) FROM attendance a JOIN class_sessions cs ON cs.id = a.session_id
                                             JOIN sections s ON s.id = cs.section_id JOIN subjects sub ON sub.id = s.subject_id
                                             WHERE sub.instructor_id = 1""").fetchone()[0],
                  "subjects": c.execute("SELECT COUNT(*) FROM subjects WHERE instructor_id = 1 AND deleted_at IS NULL").fetchone()[0]}
    return data, counts


@pytest.fixture
def fresh(tmp_path):
    """A rebuilt device: empty database, one newly registered instructor."""
    path = tmp_path / "fresh" / "new.db"
    init_db(path)
    app = create_app({"TESTING": True, "SIMULATE_HARDWARE": True, "DATABASE": path, "CSRF_ENABLED": False,
                      "DATA_DIR": tmp_path / "fresh", "SIGNATURE_DIR": tmp_path / "fresh" / "sig",
                      "REPORT_DIR": tmp_path / "fresh" / "rep", "INSTRUCTOR_REGISTRATION_CODE": "X"})
    with connect(path) as c:
        prof = accounts.register_instructor(c, PROF_FORM, "X")
    return app, prof


def test_import_rebuilds_subjects_and_creates_unclaimed_students(app, fresh):
    with connect(app.config["DATABASE"]) as source:
        classes.add_schedule_exception(source, 1, 1, {"exception_date": "2026-11-20", "as_async": "1",
                                                        "title": "Weekly reading"}, now=datetime(2026, 11, 16, 8))
    data, counts = make_backup(app)
    new_app, prof = fresh
    cfg = new_app.config
    with connect(cfg["DATABASE"]) as c:
        c.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('class_sessions', 100)")
        c.commit()
        r = instructor_import.import_backup(c, data, prof, cfg["SIGNATURE_DIR"], N)
        assert r["subjects"] == counts["subjects"] and r["attendance"] == counts["attendance"] and r["sections"] >= 1
        assert r["students_new"] >= 4 and r["students_matched"] == 0 and not r["nothing_new"]
        subs = classes.list_subjects(c, prof)
        assert [s["code"] for s in subs] and all(s["instructor_id"] == prof for s in subs)
        maria = c.execute("SELECT * FROM users WHERE student_number = '2026-0001'").fetchone()
        assert maria["password_hash"] == "" and accounts.is_unclaimed(maria)          # no password came across
        assert maria["rfid_uid"] and maria["signature_path"] == f"signatures/{maria['id']}.png"
        assert (cfg["SIGNATURE_DIR"] / f"{maria['id']}.png").read_bytes() == PNG
        drawn = c.execute("SELECT id, signature_path FROM attendance WHERE signature_path IS NOT NULL").fetchall()
        assert len(drawn) == 1 and (cfg["SIGNATURE_DIR"] / "checkins" / f"{drawn[0]['id']}.png").read_bytes() == PNG + b"drawn"
        section = c.execute("SELECT s.id FROM sections s JOIN subjects sub ON sub.id = s.subject_id WHERE sub.instructor_id = ?",
                            (prof,)).fetchone()[0]
        assert maria["id"] in [s["id"] for s in classes.list_students(c, section)]
        linked = c.execute("""SELECT cs.id, cs.title FROM schedule_exceptions se
                              JOIN class_sessions cs ON cs.id = se.async_session_id
                              WHERE se.section_id = ? AND cs.section_id = se.section_id""", (section,)).fetchone()
        assert linked["id"] > 100 and linked["title"] == "Weekly reading"
        assert accounts.authenticate(c, "2026-0001", "") is None and accounts.authenticate(c, "2026-0001", "student123") is None
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        # importing the same file again adds nothing
        again = instructor_import.import_backup(c, data, prof, cfg["SIGNATURE_DIR"], N)
        assert again["nothing_new"] and again["subjects"] == 0 and len(again["subjects_skipped"]) == counts["subjects"]
        assert len(classes.list_subjects(c, prof)) == len(subs)


def test_student_claims_the_record_by_registering(app, fresh):
    data, _ = make_backup(app)
    new_app, prof = fresh
    with connect(new_app.config["DATABASE"]) as c:
        instructor_import.import_backup(c, data, prof, new_app.config["SIGNATURE_DIR"], N)
        before = c.execute("SELECT id FROM users WHERE student_number = '2026-0001'").fetchone()[0]
    client = new_app.test_client()
    r = client.post("/register/student", data={"student_number": "2026-0001", "first_name": "Maria", "last_name": "Santos",
                                               "password": "brandnew123", "confirm_password": "brandnew123", **ACADEMIC})
    assert r.status_code == 302 and "/backup-codes" in r.location                    # same flow as a new student
    with connect(new_app.config["DATABASE"]) as c:
        row = c.execute("SELECT * FROM users WHERE student_number = '2026-0001'").fetchone()
        assert row["id"] == before and row["password_hash"] != "" and not accounts.is_unclaimed(row)
        assert c.execute("SELECT COUNT(*) FROM users WHERE student_number = '2026-0001'").fetchone()[0] == 1
    client.post("/backup-codes/done")
    page = client.get("/student/classes")                                            # no signature step: it was restored
    assert page.status_code == 200 and b"IT312" in page.data
    # a second person can't claim it again
    other = new_app.test_client()
    again = other.post("/register/student", data={"student_number": "2026-0001", "first_name": "X", "last_name": "Y",
                                                  "password": "another1234", "confirm_password": "another1234", **ACADEMIC})
    assert b"already registered" in again.data


def test_student_who_registered_first_is_matched_and_keeps_their_own_things(app, fresh):
    data, _ = make_backup(app)
    new_app, prof = fresh
    cfg = new_app.config
    with connect(cfg["DATABASE"]) as c:
        sid = accounts.register_student(c, {"student_number": "2026-0001", "first_name": "Maria", "last_name": "Santos",
                                            "password": "mine12345", "confirm_password": "mine12345", **ACADEMIC})
        cfg["SIGNATURE_DIR"].mkdir(parents=True, exist_ok=True)
        (cfg["SIGNATURE_DIR"] / f"{sid}.png").write_bytes(b"NEW SIGNATURE")
        c.execute("UPDATE users SET signature_path = ? WHERE id = ?", (f"signatures/{sid}.png", sid))
        c.commit()
        r = instructor_import.import_backup(c, data, prof, cfg["SIGNATURE_DIR"], N)
        assert r["students_matched"] == 1
        row = c.execute("SELECT * FROM users WHERE id = ?", (sid,)).fetchone()
        assert accounts.authenticate(c, "2026-0001", "mine12345") is not None            # password untouched
        assert (cfg["SIGNATURE_DIR"] / f"{sid}.png").read_bytes() == b"NEW SIGNATURE"   # their new signature wins
        assert row["rfid_uid"]                                                          # missing card filled from the file
        assert c.execute("SELECT COUNT(*) FROM enrollments WHERE student_id = ?", (sid,)).fetchone()[0] >= 1
        assert c.execute("SELECT COUNT(*) FROM attendance WHERE student_id = ?", (sid,)).fetchone()[0] >= 1
        assert sessions.student_section_stats(c, sid, c.execute(
            "SELECT section_id FROM enrollments WHERE student_id = ?", (sid,)).fetchone()[0])["history"]


def test_wrong_files_are_refused_and_nothing_changes(app, fresh):
    data, _ = make_backup(app)
    full = backup.create(app.config["DATABASE"], app.config["SIGNATURE_DIR"])
    new_app, prof = fresh
    with connect(new_app.config["DATABASE"]) as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        for bad, word in ((b"", "Choose"), (b"junk", "not a backup file"), (full, "admin's full backup")):
            with pytest.raises(ValidationError) as e:
                instructor_import.import_backup(c, bad, prof, new_app.config["SIGNATURE_DIR"], N)
            assert word in e.value.errors["file"]
        with pytest.raises(ValidationError):
            instructor_import.import_backup(c, data, prof, new_app.config["SIGNATURE_DIR"], N - 1)   # newer than the system
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == users
        assert classes.list_subjects(c, prof) == []


def test_import_form_in_settings(app, fresh):
    data, _ = make_backup(app)
    new_app, prof = fresh
    client = new_app.test_client()
    login(client, "newprof", "newpass123", PROF_SITE)
    assert b"Import my subjects" in client.get("/settings/", base_url=PROF_SITE).data

    def post(password="newpass123", content=data):
        return client.post("/settings/import", base_url=PROF_SITE, content_type="multipart/form-data",
                           data={"password": password, "file": (io.BytesIO(content), "mine.zip")}, follow_redirects=True)
    assert b"Wrong password. Nothing was imported." in post(password="nope").data
    assert b"not a backup file" in post(content=b"junk").data
    ok = post()
    assert b"Imported" in ok.data and b"IT312" in ok.data                            # lands on My subjects
    assert b"Nothing to import" in post().data
    # students can't use it
    with connect(new_app.config["DATABASE"]) as c:
        c.execute("UPDATE users SET password_hash = ? WHERE student_number = '2026-0002'",
                  (__import__("werkzeug.security", fromlist=["x"]).generate_password_hash("student999"),))
        c.commit()
    student = new_app.test_client()
    login(student, "2026-0002", "student999")
    with connect(new_app.config["DATABASE"]) as c:
        c.execute("UPDATE users SET signature_path = 'signatures/x.png' WHERE student_number = '2026-0002'")
        c.commit()
    assert student.post("/settings/import", data={"password": "student999"}).status_code == 403


def test_join_code_is_replaced_only_when_taken(app, fresh):
    data, _ = make_backup(app)
    new_app, prof = fresh
    with connect(new_app.config["DATABASE"]) as c:
        other = accounts.register_instructor(c, {**PROF_FORM, "username": "second"}, "X")
        sub = c.execute("INSERT INTO subjects (instructor_id, code, title, school_year, semester) VALUES (?, 'ZZ1', 'T', '2026-2027', '1st')",
                        (other,)).lastrowid
        c.execute("INSERT INTO sections (subject_id, name, join_code) VALUES (?, 'BSXX 1A', 'JOIN3A')", (sub,))
        c.commit()
        instructor_import.import_backup(c, data, prof, new_app.config["SIGNATURE_DIR"], N)
        codes = [r[0] for r in c.execute("SELECT join_code FROM sections")]
        assert len(codes) == len(set(codes)) and codes.count("JOIN3A") == 1


def test_admin_merges_a_mistyped_registration(app, fresh):
    """Student registered as 2026-0011 instead of 2026-0001: the admin merges the imported record."""
    from app.services import student_support
    data, _ = make_backup(app)
    new_app, prof = fresh
    cfg = new_app.config
    with connect(cfg["DATABASE"]) as c:
        instructor_import.import_backup(c, data, prof, cfg["SIGNATURE_DIR"], N)
        typo = accounts.register_student(c, {"student_number": "2026-0011", "first_name": "Maria", "last_name": "Santos",
                                             "password": "mine12345", "confirm_password": "mine12345", **ACADEMIC})
        admin = accounts.create_admin(c, {"username": "admin", "first_name": "Dev", "last_name": "Support",
                                          "password": "dev-password-123", "confirm_password": "dev-password-123"})
        imported = c.execute("SELECT * FROM users WHERE student_number = '2026-0001'").fetchone()
        records = c.execute("SELECT COUNT(*) FROM attendance WHERE student_id = ?", (imported["id"],)).fetchone()[0]
        assert records >= 1 and imported["id"] in [u["id"] for u in student_support.unclaimed_records(c)]
        with pytest.raises(ValidationError):                                       # a registered account is not a source
            student_support.merge_unclaimed(c, admin, imported["id"], typo, cfg["SIGNATURE_DIR"])
    client = new_app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = admin
    page = client.get(f"/admin/students/{typo}").data
    assert b"Merge an imported record" in page and b"2026-0001" in page
    assert b"hasn&#39;t registered yet" in client.get(f"/admin/students/{imported['id']}").data or \
        b"hasn't registered yet" in client.get(f"/admin/students/{imported['id']}").data
    assert client.post(f"/admin/students/{typo}/merge", data={"source_id": ""}).status_code == 302   # nothing chosen
    r = client.post(f"/admin/students/{typo}/merge", data={"source_id": imported["id"]}, follow_redirects=True)
    assert b"Merged." in r.data and b"Merge an imported record" in r.data            # other unclaimed records remain
    with connect(cfg["DATABASE"]) as c:
        me = c.execute("SELECT * FROM users WHERE id = ?", (typo,)).fetchone()
        assert me["student_number"] == "2026-0001" and me["username"] == "2026-0001"
        assert accounts.authenticate(c, "2026-0001", "mine12345") is not None         # same password, right number
        assert accounts.authenticate(c, "2026-0011", "mine12345") is None
        assert c.execute("SELECT COUNT(*) FROM attendance WHERE student_id = ?", (typo,)).fetchone()[0] == records
        assert c.execute("SELECT COUNT(*) FROM attendance WHERE student_id = ?", (imported["id"],)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM enrollments WHERE student_id = ? AND status = 'active'", (typo,)).fetchone()[0] >= 1
        assert me["rfid_uid"] == imported["rfid_uid"] and me["signature_path"] == f"signatures/{typo}.png"
        assert (cfg["SIGNATURE_DIR"] / f"{typo}.png").exists()
        gone = c.execute("SELECT deleted_at, rfid_uid FROM users WHERE id = ?", (imported["id"],)).fetchone()
        assert gone["deleted_at"] and gone["rfid_uid"] is None
        assert c.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'merge_imported_record'").fetchone()[0] == 1
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    student = new_app.test_client()
    login(student, "2026-0001", "mine12345")
    assert b"IT312" in student.get("/student/classes").data                          # classes are there, no signature step
    prof_client = new_app.test_client()
    login(prof_client, "newprof", "newpass123", PROF_SITE)
    assert prof_client.post(f"/admin/students/{typo}/merge", data={"source_id": 1}, base_url=PROF_SITE).status_code == 403
