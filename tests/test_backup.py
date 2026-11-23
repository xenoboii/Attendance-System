"""Admin backup (one .zip) and restore."""
import io
import json
import zipfile
from datetime import datetime, timedelta

import pytest

from app.db import connect
from app.migrations import MIGRATIONS
from app.services import accounts, backup
from app.services.accounts import ValidationError

from .test_auth import PROF_SITE, login

ADMIN = {"username": "admin", "first_name": "Dev", "last_name": "Support",
         "password": "dev-password-123", "confirm_password": "dev-password-123"}
PNG = b"\x89PNG\r\n\x1a\n fake"


def _admin(app, client):
    with connect(app.config["DATABASE"]) as c:
        uid = accounts.create_admin(c, ADMIN)
    with client.session_transaction() as s:
        s["user_id"] = uid
    return uid


def _sigs(app):
    d = app.config["SIGNATURE_DIR"]
    (d / "checkins").mkdir(parents=True, exist_ok=True)
    (d / "2.png").write_bytes(PNG)
    (d / "checkins" / "7.png").write_bytes(PNG)
    (d / "notes.txt").write_text("not a signature")
    return d


def test_backup_contains_database_and_signatures(app):
    d = _sigs(app)
    data = backup.create(app.config["DATABASE"], d, datetime(2026, 11, 15, 21, 0))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
        assert z.read("attendance.db").startswith(b"SQLite format 3")
    assert names == {"manifest.json", "attendance.db", "signatures/2.png", "signatures/checkins/7.png"}
    assert manifest["format"] == backup.FORMAT and manifest["database_version"] == len(MIGRATIONS)
    assert manifest["signatures"] == 2 and manifest["created_at"] == "2026-11-15 21:00:00"
    info = backup.inspect(data, len(MIGRATIONS))
    assert info["counts"]["users"] >= 5 and info["signatures"] == 2


@pytest.mark.parametrize("make", [
    lambda good: b"",
    lambda good: b"this is not a zip",
    lambda good: _zip({"hello.txt": b"hi"}),
    lambda good: _zip({"manifest.json": b"{}", "attendance.db": b"nope"}),
    lambda good: _zip({"manifest.json": json.dumps({"format": backup.FORMAT}).encode(), "attendance.db": b"not sqlite"}),
])
def test_bad_files_are_refused(app, make):
    good = backup.create(app.config["DATABASE"], app.config["SIGNATURE_DIR"])
    with pytest.raises(ValidationError):
        backup.inspect(make(good), len(MIGRATIONS))


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def test_newer_backup_is_refused(app):
    data = backup.create(app.config["DATABASE"], app.config["SIGNATURE_DIR"])
    with pytest.raises(ValidationError) as e:
        backup.inspect(data, len(MIGRATIONS) - 1)
    assert "newer version" in e.value.errors["file"]


def test_restore_brings_back_data_and_keeps_a_safety_copy(app, tmp_path):
    cfg = app.config
    d = _sigs(app)
    saved = backup.create(cfg["DATABASE"], d)
    # things change after the backup ...
    with connect(cfg["DATABASE"]) as c:
        c.execute("UPDATE users SET first_name = 'Changed' WHERE id = 2")
        c.execute("DELETE FROM attendance")
        c.commit()
    (d / "2.png").unlink()
    (d / "checkins" / "99.png").write_bytes(PNG)
    evil = _zip({"../evil.png": PNG, "signatures/../../evil2.png": PNG})        # nothing odd is ever written
    with pytest.raises(ValidationError):
        backup.restore(evil, cfg["DATABASE"], d, cfg["DATA_DIR"], len(MIGRATIONS))
    info = backup.restore(saved, cfg["DATABASE"], d, cfg["DATA_DIR"], len(MIGRATIONS))
    with connect(cfg["DATABASE"]) as c:
        assert c.execute("SELECT first_name FROM users WHERE id = 2").fetchone()[0] != "Changed"
        assert c.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] > 0
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (d / "2.png").exists() and (d / "checkins" / "7.png").exists() and not (d / "checkins" / "99.png").exists()
    assert (d / "notes.txt").exists() and not (tmp_path.parent / "evil.png").exists()
    safety = cfg["DATA_DIR"] / "backups" / info["safety_copy"]
    assert safety.exists()
    with zipfile.ZipFile(safety) as z:                                           # holds the state before the restore
        assert "signatures/checkins/99.png" in z.namelist()
    for i in range(5):                                                           # only the last few are kept
        backup.restore(saved, cfg["DATABASE"], d, cfg["DATA_DIR"], len(MIGRATIONS), datetime(2027, 1, 1, 0, 0, i))
    assert len(list((cfg["DATA_DIR"] / "backups").glob("before-restore-*.zip"))) == backup.SAFETY_KEEP


def test_backup_page_download_and_reminder(app, client):
    _admin(app, client)
    page = client.get("/admin/backup").data
    assert b"Download backup" in page and b"Restore from a backup" in page and b"Never" in page
    assert b"No backup yet" in client.get("/admin/").data                         # overview nags
    r = client.post("/admin/backup/download")
    assert r.status_code == 200 and r.data[:2] == b"PK" and r.headers["Content-Disposition"].endswith('.zip')
    assert "no-store" in r.headers["Cache-Control"]
    assert b"today" in client.get("/admin/backup").data and b"No backup yet" not in client.get("/admin/").data
    with connect(app.config["DATABASE"]) as c:
        st = backup.status(c, app.config["DATA_DIR"], datetime.now() + timedelta(days=8))
    assert st["overdue"] and st["days"] == 8


def test_restore_route_needs_password_tick_and_a_real_file(app, client):
    _admin(app, client)
    good = client.post("/admin/backup/download").data
    with connect(app.config["DATABASE"]) as c:
        c.execute("UPDATE users SET first_name = 'Changed' WHERE id = 2")
        c.commit()

    def post(**over):
        data = {"password": ADMIN["password"], "confirm": "1", "file": (io.BytesIO(good), "backup.zip")}
        data.update(over)
        return client.post("/admin/backup/restore", data=data, content_type="multipart/form-data")

    assert b"Wrong password." in post(password="nope").data
    assert b"Tick the box" in post(confirm="").data
    assert b"not a backup file" in post(file=(io.BytesIO(b"junk"), "x.zip")).data
    with connect(app.config["DATABASE"]) as c:
        assert c.execute("SELECT first_name FROM users WHERE id = 2").fetchone()[0] == "Changed"   # untouched so far
    r = post()
    assert r.status_code == 302 and r.location.endswith("/login")
    with connect(app.config["DATABASE"]) as c:
        assert c.execute("SELECT first_name FROM users WHERE id = 2").fetchone()[0] != "Changed"
        assert c.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'restore_backup'").fetchone()[0] == 1
    assert client.get("/admin/backup").status_code == 302                          # logged out: log in again
    assert login(client, "admin", ADMIN["password"], PROF_SITE).location.endswith("/admin/")


def test_admin_backup_pages_are_admin_only(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    assert client.get("/admin/backup", base_url=PROF_SITE).status_code == 403
    assert client.post("/admin/backup/download", base_url=PROF_SITE).status_code == 403
    assert client.post("/admin/backup/restore", base_url=PROF_SITE).status_code == 403


def _second_instructor(app):
    """Another instructor with a subject, a section and one student of their own."""
    from werkzeug.security import generate_password_hash
    with connect(app.config["DATABASE"]) as c:
        other = c.execute("INSERT INTO users (role, username, password_hash, first_name, last_name) "
                          "VALUES ('instructor', 'other', ?, 'Other', 'Prof')", (generate_password_hash("other12345"),)).lastrowid
        sub = c.execute("INSERT INTO subjects (instructor_id, code, title, school_year, semester) "
                        "VALUES (?, 'SECRET9', 'Other Subject', '2026-2027', '1st')", (other,)).lastrowid
        sec = c.execute("INSERT INTO sections (subject_id, name, join_code) VALUES (?, 'BSXX 1A', 'ZZZZ99')", (sub,)).lastrowid
        stu = c.execute("INSERT INTO users (role, username, student_number, password_hash, first_name, last_name) "
                        "VALUES ('student', '2026-8888', '2026-8888', 'h', 'Only', 'Theirs')").lastrowid
        c.execute("INSERT INTO enrollments (section_id, student_id) VALUES (?, ?)", (sec, stu))
        c.commit()
    return other, stu


def test_instructor_backup_has_only_their_own_classes(app, client):
    import sqlite3
    other, their_student = _second_instructor(app)
    d = _sigs(app)                                                               # 2.png = my student, checkins/7.png = nobody's
    (d / f"{their_student}.png").write_bytes(PNG)
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/settings/", base_url=PROF_SITE).data
    assert b'id="backup"' in page and b"Download my subjects" in page and b"Never backed up" in page
    wrong = client.post("/settings/backup", data={"password": "nope"}, base_url=PROF_SITE)
    assert wrong.status_code == 302 and "/settings/" in wrong.location             # nothing downloaded
    r = client.post("/settings/backup", data={"password": "prof123"}, base_url=PROF_SITE)
    assert r.status_code == 200 and r.data[:2] == b"PK" and "no-store" in r.headers["Cache-Control"]
    assert "my-subjects-backup-prof-" in r.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json"))
        db_bytes = z.read("my-classes.db")
    assert manifest["format"] == backup.INSTRUCTOR_FORMAT and manifest["instructor"] == "prof"
    assert not any(n.endswith(".xlsx") for n in names)                          # PDF-only system: no Excel
    assert "signatures/2.png" in names and f"signatures/{their_student}.png" not in names
    assert "signatures/checkins/7.png" not in names
    assert b"SECRET9" not in db_bytes and b"Only" + b"\x00" not in db_bytes and b"ZZZZ99" not in db_bytes
    tmp = app.config["DATA_DIR"] / "mine-check.db"
    tmp.write_bytes(db_bytes)
    c = sqlite3.connect(str(tmp))
    assert {r[0] for r in c.execute("SELECT instructor_id FROM subjects")} == {1}
    assert c.execute("SELECT COUNT(*) FROM users WHERE id IN (?, ?)", (other, their_student)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM users WHERE role = 'student'").fetchone()[0] >= 4          # my students are there
    assert c.execute("SELECT COUNT(*) FROM users WHERE password_hash <> ''").fetchone()[0] == 0       # no passwords
    assert c.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] > 0
    assert c.execute("SELECT COUNT(*) FROM recovery_codes").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
    c.close()
    # the device itself is untouched, and this file can't be used to restore the device
    with connect(app.config["DATABASE"]) as live:
        assert live.execute("SELECT COUNT(*) FROM subjects WHERE code = 'SECRET9'").fetchone()[0] == 1
        assert live.execute("SELECT password_hash FROM users WHERE id = 1").fetchone()[0] != ""
    with pytest.raises(ValidationError) as e:
        backup.inspect(r.data, len(MIGRATIONS))
    assert "own-subjects backup" in e.value.errors["file"]
    # their own "last backup" date; the admin's device-wide reminder is not cleared by it
    assert b"Never backed up" not in client.get("/settings/", base_url=PROF_SITE).data
    with connect(app.config["DATABASE"]) as live:
        assert backup.status(live, app.config["DATA_DIR"])["last"] is None


def test_students_get_no_backup(app, client):
    from .test_auth import give_signature
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    assert b'id="backup"' not in client.get("/settings/").data
    assert client.post("/settings/backup", data={"password": "student123"}).status_code == 403
