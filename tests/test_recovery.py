"""Backup codes + "Forgot password?" for instructors."""
import re

import pytest

from app.services import recovery
from app.services.accounts import ValidationError, authenticate

from .test_auth import NEW_PROF, PROF_SITE, login

PROF = 1


def _reset(code, pw="brandnew123", username="prof"):
    return {"username": username, "code": code, "new_password": pw, "confirm_new_password": pw}


def test_generate_makes_ten_readable_codes(conn):
    codes = recovery.generate(conn, PROF)
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(re.fullmatch(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", c) for c in codes)
    stored = [r[0] for r in conn.execute("SELECT code_hash FROM recovery_codes WHERE user_id = ?", (PROF,))]
    assert len(stored) == 10 and not set(codes) & set(stored)          # only hashes are stored
    assert recovery.status(conn, PROF)["unused"] == 10


def test_code_resets_password_once(conn):
    codes = recovery.generate(conn, PROF)
    recovery.reset_password(conn, _reset(codes[3].lower().replace("-", " ")))   # forgiving format
    assert authenticate(conn, "prof", "brandnew123")
    assert recovery.status(conn, PROF)["unused"] == 9
    with pytest.raises(ValidationError):                                # each code works once
        recovery.reset_password(conn, _reset(codes[3], "another123"))


def test_new_set_replaces_old(conn):
    old = recovery.generate(conn, PROF)
    recovery.generate(conn, PROF)
    with pytest.raises(ValidationError):
        recovery.reset_password(conn, _reset(old[0]))


def test_wrong_code_or_username_same_message(conn):
    codes = recovery.generate(conn, PROF)
    with pytest.raises(ValidationError) as a:
        recovery.reset_password(conn, _reset("AAAA-BBBB"))
    with pytest.raises(ValidationError) as b:
        recovery.reset_password(conn, _reset(codes[0], username="nobody"))
    assert a.value.errors == b.value.errors


def test_students_use_student_number_on_student_site(conn):
    student = conn.execute("SELECT id, student_number FROM users WHERE role = 'student'").fetchone()
    codes = recovery.generate(conn, student["id"])
    with pytest.raises(ValidationError):                         # not on the prof site
        recovery.reset_password(conn, _reset(codes[0], username=student["student_number"]))
    recovery.reset_password(conn, _reset(codes[0], username=student["student_number"]), student=True)
    assert authenticate(conn, student["student_number"], "brandnew123")


def test_prof_codes_dont_work_on_student_site(conn):
    codes = recovery.generate(conn, PROF)
    with pytest.raises(ValidationError) as e:
        recovery.reset_password(conn, _reset(codes[0]), student=True)
    assert "student number" in e.value.errors["code"]


def test_pdf_has_the_codes():
    user = {"first_name": "Maria", "last_name": "Reyes", "middle_name": None, "suffix": None,
            "username": "mreyes", "title": None}
    pdf = recovery.build_pdf(user, ["ABCD-EFGH"] * 10)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


def test_temporary_password_from_cli(app):
    r = app.test_cli_runner().invoke(args=["reset-password", "prof"])
    assert r.exit_code == 0 and "Temporary password" in r.output
    temp = r.output.split(": ")[1].split()[0]
    from app.db import connect
    assert authenticate(connect(app.config["DATABASE"]), "prof", temp)
    assert app.test_cli_runner().invoke(args=["reset-password", "ghost"]).exit_code != 0


# ---------------------------------------------------------------- web flow
def test_register_shows_codes_then_pdf_then_continue(client):
    page = client.get("/register/instructor", base_url=PROF_SITE).data
    assert b"backup codes" in page and b"data-confirm" in page                 # the pop-up
    client.post("/register/instructor", data=NEW_PROF, base_url=PROF_SITE)
    page = client.get("/backup-codes", base_url=PROF_SITE).data.decode()
    codes = re.findall(r'<span class="font-monospace">([A-Z2-9]{4}-[A-Z2-9]{4})</span>', page)
    assert len(codes) == 10
    pdf = client.get("/backup-codes.pdf", base_url=PROF_SITE)
    assert pdf.mimetype == "application/pdf" and "attachment" in pdf.headers["Content-Disposition"]
    assert "no-store" in pdf.headers["Cache-Control"]
    assert client.post("/backup-codes/done", base_url=PROF_SITE).location.endswith("/instructor/")
    # shown once: gone afterwards
    assert client.get("/backup-codes", base_url=PROF_SITE).location.endswith("/account/")
    assert client.get("/backup-codes.pdf", base_url=PROF_SITE).status_code == 404
    # and they work on Forgot password?
    client.post("/logout", base_url=PROF_SITE)
    r = client.post("/forgot-password", data=_reset(codes[0], username="mreyes"), base_url=PROF_SITE)
    assert r.location.endswith("/login")
    assert login(client, "mreyes", "brandnew123", PROF_SITE).location.endswith("/instructor/")


def test_generate_new_codes_needs_password(client):
    login(client, "prof", "prof123", PROF_SITE)
    page = client.get("/account/", base_url=PROF_SITE).data
    assert b"Backup codes" in page and b"Generate new codes" in page
    bad = client.post("/account/backup-codes", data={"password": "nope"}, base_url=PROF_SITE)
    assert bad.status_code == 400 and b"Wrong password" in bad.data and b"data-open-on-load" in bad.data
    ok = client.post("/account/backup-codes", data={"password": "prof123"}, base_url=PROF_SITE)
    assert ok.location.endswith("/backup-codes")


def test_forgot_password_on_both_sites(client):
    assert b"Forgot password?" in client.get("/login", base_url=PROF_SITE).data
    assert b"Forgot password?" in client.get("/login").data
    assert b"Student number" in client.get("/forgot-password").data
    assert b"Username" in client.get("/forgot-password", base_url=PROF_SITE).data


def test_student_registration_codes_then_signature(client):
    from .test_auth import NEW_STUDENT
    page = client.get("/register/student").data
    assert b"Backup codes" in page and b"data-confirm" in page
    client.post("/register/student", data=NEW_STUDENT)
    page = client.get("/backup-codes").data.decode()
    assert "Continue to signature" in page
    codes = re.findall(r'<span class="font-monospace">([A-Z2-9]{4}-[A-Z2-9]{4})</span>', page)
    assert len(codes) == 10 and client.get("/backup-codes.pdf").mimetype == "application/pdf"
    assert client.post("/backup-codes/done").location.endswith("/signature")
    client.post("/logout")
    r = client.post("/forgot-password", data=_reset(codes[0], username=NEW_STUDENT["student_number"]))
    assert r.location.endswith("/login")


def test_student_account_has_backup_codes_card(app, client):
    from .test_auth import give_signature
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    page = client.get("/account/").data
    assert b"Backup codes" in page and b"Generate new codes" in page
    assert client.post("/account/backup-codes", data={"password": "student123"}).location.endswith("/backup-codes")
