"""Admin -> Device settings: instructor registration code and hotspot Wi-Fi name / password."""
import subprocess

import pytest

from app.db import connect
from app.services import device_settings
from app.services.accounts import ValidationError

from .test_admin import ADMIN, admin  # noqa: F401  (fixture)
from .test_auth import NEW_PROF, PROF_SITE, login


# ---------------------------------------------------------------- instructor registration code
def test_code_defaults_to_the_settings_file_then_admin_can_change_it(conn):
    assert device_settings.instructor_code(conn, "PROF-2026") == "PROF-2026"
    device_settings.set_instructor_code(conn, 1, "  LSPU-2027 ")
    assert device_settings.instructor_code(conn, "PROF-2026") == "LSPU-2027"


@pytest.mark.parametrize("bad", ["", "abc", "has space", "x" * 31, "semi;colon"])
def test_code_validation(conn, bad):
    with pytest.raises(ValidationError):
        device_settings.set_instructor_code(conn, 1, bad)


def test_new_code_is_what_instructors_must_type(app, client, admin):
    login(client, "admin", ADMIN["password"], PROF_SITE)
    r = client.post("/admin/device/instructor-code", data={"code": "NEW-CODE-1"}, base_url=PROF_SITE)
    assert r.status_code == 302
    client.post("/logout", base_url=PROF_SITE)
    old = client.post("/register/instructor", data=NEW_PROF, base_url=PROF_SITE)          # PROF-2026
    assert b"Wrong instructor registration code" in old.data
    new = client.post("/register/instructor", data={**NEW_PROF, "registration_code": "NEW-CODE-1"},
                      base_url=PROF_SITE)
    assert new.status_code == 302


# ---------------------------------------------------------------- hotspot Wi-Fi
@pytest.mark.parametrize("name, pw, confirm, field", [
    ("", "password123", "password123", "wifi_name"),
    ("x" * 33, "password123", "password123", "wifi_name"),
    ("Bad;Name", "password123", "password123", "wifi_name"),
    ("LSPU-Attendance", "short", "short", "wifi_password"),
    ("LSPU-Attendance", "password123", "password124", "wifi_confirm"),
])
def test_wifi_validation(name, pw, confirm, field):
    with pytest.raises(ValidationError) as e:
        device_settings.validate_wifi(name, pw, confirm)
    assert field in e.value.errors


def test_wifi_change_runs_only_the_helper_and_never_logs_the_password(conn, monkeypatch):
    monkeypatch.setattr(device_settings, "wifi_available", lambda: True)
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")
    name = device_settings.set_wifi(conn, 1, " CPE Room 3 ", "secret-pass-9", "secret-pass-9", runner=runner)
    assert name == "CPE Room 3"
    assert calls == [["sudo", "-n", device_settings.WIFI_HELPER, "CPE Room 3", "secret-pass-9"]]
    logged = conn.execute("SELECT details FROM audit_log WHERE action = 'set_hotspot'").fetchone()[0]
    assert "CPE Room 3" in logged and "secret-pass-9" not in logged


def test_wifi_change_reports_helper_errors(conn, monkeypatch):
    monkeypatch.setattr(device_settings, "wifi_available", lambda: True)
    runner = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, stdout="", stderr="the hotspot isn't set up")
    with pytest.raises(ValidationError, match="isn't set up"):
        device_settings.set_wifi(conn, 1, "Room", "password123", "password123", runner=runner)


def test_wifi_needs_the_one_time_setup(conn, monkeypatch):
    monkeypatch.setattr(device_settings, "wifi_available", lambda: False)
    with pytest.raises(ValidationError, match="device-settings.sh"):
        device_settings.set_wifi(conn, 1, "Room", "password123", "password123")


def test_device_page_is_admin_only_and_asks_for_the_admin_password(app, client, admin, monkeypatch):
    assert client.get("/admin/device", base_url=PROF_SITE).status_code == 302          # not logged in
    login(client, "admin", ADMIN["password"], PROF_SITE)
    page = client.get("/admin/device", base_url=PROF_SITE)
    assert page.status_code == 200 and b"Instructor registration code" in page.data and b"Device Wi-Fi" in page.data
    assert b"Device settings" in page.data                                                # sidebar link
    monkeypatch.setattr(device_settings, "wifi_available", lambda: True)
    monkeypatch.setattr(device_settings, "wifi_name", lambda: "LSPU-Attendance")
    wrong = client.post("/admin/device/wifi", base_url=PROF_SITE, data={
        "wifi_name": "Room", "wifi_password": "password123", "wifi_confirm": "password123",
        "admin_password": "nope"})
    assert wrong.status_code == 400 and b"Wrong password" in wrong.data
    monkeypatch.setattr(device_settings, "set_wifi", lambda *a, **k: "Room 3")
    ok = client.post("/admin/device/wifi", base_url=PROF_SITE, data={
        "wifi_name": "Room 3", "wifi_password": "password123", "wifi_confirm": "password123",
        "admin_password": ADMIN["password"]})
    assert ok.status_code == 200 and b"Room 3" in ok.data and b"disconnected" in ok.data


def test_instructors_cant_open_device_settings(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    assert client.get("/admin/device", base_url=PROF_SITE).status_code == 403
