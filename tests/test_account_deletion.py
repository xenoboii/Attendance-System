"""Deleting an account (self or admin) and restoring it (admin)."""
import pytest

from app.db import connect
from app.services import account_deletion, accounts, classes
from app.services import attendance_service as attendance
from app.services.accounts import ValidationError

from .conftest import JOSE, MARIA, at
from .test_auth import PROF_SITE, give_signature, login

PROF = 1
ADMIN = {"username": "admin", "first_name": "Dev", "last_name": "Support",
         "password": "dev-password-123", "confirm_password": "dev-password-123"}
NEW = {"student_number": "2026-0001", "first_name": "New", "last_name": "Person",
       "program": "BSCPE", "year_level": "4", "section_letter": "A",
       "password": "brandnew123", "confirm_password": "brandnew123"}


def test_deleted_student_is_gone_everywhere_and_number_is_free(conn):
    before = len(classes.list_students(conn, 1))
    user = account_deletion.delete_account(conn, MARIA, MARIA)
    assert user["student_number"] == "2026-0001"
    assert accounts.authenticate(conn, "2026-0001", "student123") is None
    assert accounts.get_user(conn, MARIA) is None
    assert len(classes.list_students(conn, 1)) == before - 1
    assert attendance.rfid_tap(conn, "CARD0001", at("07:00"))["result"] == "unknown_card"     # card freed
    assert conn.execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id = ?", (MARIA,)).fetchone()[0] == 0
    assert [d["user_id"] for d in account_deletion.list_deleted(conn)] == [MARIA]
    with pytest.raises(ValidationError):
        account_deletion.delete_account(conn, MARIA, MARIA)                                  # already deleted
    new_id = accounts.register_student(conn, NEW)                                            # same number, new person
    assert new_id != MARIA
    with pytest.raises(ValidationError) as e:
        account_deletion.restore_account(conn, MARIA, PROF)
    assert "registered again" in e.value.errors["account"]


def test_restore_brings_back_login_classes_and_card(conn):
    account_deletion.delete_account(conn, JOSE, PROF)
    back = account_deletion.restore_account(conn, JOSE, PROF)
    assert back["student_number"] == "2026-0002" and back["deleted_at"] is None and back["rfid_uid"] == "CARD0002"
    assert accounts.authenticate(conn, "2026-0002", "student123") is not None                # same password
    assert JOSE in [s["id"] for s in classes.list_students(conn, 1)]
    assert account_deletion.list_deleted(conn) == []
    with pytest.raises(ValidationError):
        account_deletion.restore_account(conn, JOSE, PROF)


def test_deleting_an_instructor_archives_their_subjects(conn):
    subjects = [s["id"] for s in classes.list_subjects(conn, PROF)]
    assert subjects
    account_deletion.delete_account(conn, PROF, PROF)
    assert classes.list_subjects(conn, PROF) == []
    assert sorted(s["id"] for s in classes.list_subjects(conn, PROF, archived=True)) == sorted(subjects)
    assert attendance.rfid_tap(conn, "CARD0001", at("07:00"))["result"] == "no_open_session"   # no classes run
    account_deletion.restore_account(conn, PROF, PROF)
    assert sorted(s["id"] for s in classes.list_subjects(conn, PROF)) == sorted(subjects)


def test_admin_accounts_cannot_be_deleted_here(conn):
    admin = accounts.create_admin(conn, ADMIN)
    with pytest.raises(ValidationError):
        account_deletion.delete_account(conn, admin, admin)


def test_student_deletes_own_account(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    page = client.get("/account/").data
    assert b'id="delete-account"' in page and b"Delete my account" in page
    assert b"Wrong password." in client.post("/account/delete", data={"password": "nope", "confirm": "1"}).data
    assert b"Tick the box" in client.post("/account/delete", data={"password": "student123"}).data
    r = client.post("/account/delete", data={"password": "student123", "confirm": "1"})
    assert r.status_code == 302 and r.location.endswith("/login")
    assert client.get("/student/").status_code == 302                                         # logged out
    assert b"Wrong" in login(client, "2026-0001", "student123").data or client.get("/student/").status_code == 302
    with connect(app.config["DATABASE"]) as c:
        assert c.execute("SELECT deleted_by FROM deleted_accounts WHERE user_id = 2").fetchone()[0] == 2


def test_instructor_deletes_own_account(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    assert b"Your subjects are archived" in client.get("/account/", base_url=PROF_SITE).data
    r = client.post("/account/delete", data={"password": "prof123", "confirm": "1"}, base_url=PROF_SITE)
    assert r.status_code == 302
    assert client.get("/instructor/", base_url=PROF_SITE).status_code == 302


def test_admin_deletes_and_restores(app, client):
    with connect(app.config["DATABASE"]) as c:
        admin = accounts.create_admin(c, ADMIN)
    with client.session_transaction() as s:
        s["user_id"] = admin
    page = client.get("/admin/accounts").data
    assert b"2026-0001" in page and b"prof" in page and b"No deleted accounts." in page
    assert b"Delete my account" not in client.get("/account/").data                           # admins don't get it
    assert client.post("/account/delete", data={"password": ADMIN["password"], "confirm": "1"}).status_code == 403
    assert client.post("/admin/accounts/2/delete").status_code == 302
    page = client.get("/admin/accounts").data
    assert b"Restore" in page and page.count(b"2026-0001") == 1                               # only in the Deleted list
    assert client.post(f"/admin/accounts/{admin}/delete", follow_redirects=True).data.count(b"can&#39;t be deleted") + \
        client.post(f"/admin/accounts/{admin}/delete", follow_redirects=True).data.count(b"can't be deleted") >= 1
    assert client.post("/admin/accounts/2/restore").status_code == 302
    assert b"No deleted accounts." in client.get("/admin/accounts").data
    student = app.test_client()
    give_signature(app, 2)
    assert login(student, "2026-0001", "student123").location.endswith("/student/")
    prof = app.test_client()
    login(prof, "prof", "prof123", PROF_SITE)
    assert prof.get("/admin/accounts", base_url=PROF_SITE).status_code == 403
    assert prof.post("/admin/accounts/2/delete", base_url=PROF_SITE).status_code == 403


@pytest.mark.parametrize("user_id", [MARIA, PROF])
def test_permanent_deletion_removes_only_affected_accounts_and_history(app, client, admin_account, user_id):
    admin = admin_account["id"]
    signature_dir = app.config["SIGNATURE_DIR"]
    checkins = signature_dir / "checkins"
    checkins.mkdir()
    signature = signature_dir / f"{user_id}.png"
    signature.write_bytes(b"account signature")
    with connect(app.config["DATABASE"]) as db:
        maria_record, jose_record = [db.execute("SELECT id FROM attendance WHERE student_id = ?", (sid,)).fetchone()[0]
                                    for sid in (MARIA, JOSE)]
        for record in (maria_record, jose_record):
            (checkins / f"{record}.png").write_bytes(b"check-in signature")
        db.execute("INSERT INTO profile_photos (user_id, image, revision) VALUES (?, ?, 'test')", (user_id, b"picture"))
        db.execute("INSERT INTO schedule_exceptions (section_id, exception_date, reason, created_by, is_vacation) "
                   "VALUES (1, '2026-12-20', 'Vacation', 1, 1)")
        db.execute("INSERT INTO join_requests (section_id, student_id, reviewed_by) VALUES (1, ?, 1)", (JOSE,))

    with client.session_transaction() as session:
        session["user_id"] = admin
    endpoint = f"/admin/accounts/{user_id}/purge"
    student = app.test_client()
    give_signature(app, JOSE)
    login(student, "2026-0002", "student123")
    assert student.post(endpoint).status_code == 403
    assert b"Only student and instructor accounts in Deleted accounts" in client.post(endpoint, follow_redirects=True).data
    assert client.post(f"/admin/accounts/{user_id}/delete").status_code == 302
    assert f'action="{endpoint}"'.encode() in client.get("/admin/accounts").data

    app.config["CSRF_ENABLED"] = True
    assert client.post(endpoint).status_code == 400
    with client.session_transaction() as session:
        token = session["_csrf"]
    assert b"Account permanently deleted." in client.post(endpoint, data={"csrf_token": token}, follow_redirects=True).data
    assert not signature.exists()
    assert not (checkins / f"{maria_record}.png").exists()
    assert (checkins / f"{jose_record}.png").exists() == (user_id == MARIA)
    with connect(app.config["DATABASE"]) as db:
        assert db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone() is None
        assert db.execute("SELECT id FROM users WHERE id = ?", (JOSE,)).fetchone() is not None
        assert db.execute("SELECT 1 FROM profile_photos WHERE user_id = ?", (user_id,)).fetchone() is None
        assert db.execute("SELECT 1 FROM deleted_accounts WHERE user_id = ?", (user_id,)).fetchone() is None
        assert (db.execute("SELECT 1 FROM subjects WHERE id = 1").fetchone() is not None) == (user_id == MARIA)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
