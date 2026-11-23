"""'Sync time from this phone' - backup for the RTC module."""
import subprocess
import time

import pytest

from app.services import clock
from app.services.accounts import ValidationError

from .test_auth import PROF_SITE, login


def test_laptop_never_changes_its_clock(monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    r = clock.set_time(int(time.time() * 1000) + 3_600_000, enabled=False)
    assert r == {"changed": False, "simulated": True, "offset_s": 3600} and not called


def test_pi_runs_only_the_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **k: calls.append(args))
    target = int(time.time()) + 600
    r = clock.set_time(target * 1000, enabled=True)
    assert r["changed"] and calls == [["sudo", "-n", clock.HELPER, str(target)]]


@pytest.mark.parametrize("bad", ["abc", None, 0, 4_200_000_000_000])
def test_rejects_nonsense_times(bad):
    with pytest.raises(ValidationError):
        clock.set_time(bad, enabled=False)


def test_status_notices_clock_earlier_than_last_checkin(conn):
    conn.execute("UPDATE attendance SET checked_in_at = '2099-01-01 08:00:00' WHERE id = "
                 "(SELECT id FROM attendance LIMIT 1)")
    assert clock.status(conn)["behind_last_record"] is True


def test_routes_instructor_only(app, client):
    assert client.get("/instructor/clock", base_url=PROF_SITE).status_code == 302      # not logged in
    login(client, "prof", "prof123", PROF_SITE)
    j = client.get("/instructor/clock", base_url=PROF_SITE).get_json()
    assert abs(j["now_ms"] - time.time() * 1000) < 5000
    r = client.post("/instructor/clock", json={"epoch_ms": int(time.time() * 1000)}, base_url=PROF_SITE)
    assert r.get_json()["ok"] and r.get_json()["simulated"]                              # tests = laptop mode
    bad = client.post("/instructor/clock", json={"epoch_ms": 5}, base_url=PROF_SITE)
    assert bad.status_code == 400 and "date looks wrong" in bad.get_json()["error"]
    page = client.get("/settings/", base_url=PROF_SITE).data
    assert b"Device clock" in page and b"Sync time from this phone" in page
    assert b"data-clock-warning" in client.get("/instructor/", base_url=PROF_SITE).data


def test_students_dont_see_it(app, client):
    from .test_auth import give_signature
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    assert b"data-clock-warning" not in client.get("/student/").data
    assert client.get("/instructor/clock").status_code == 403


def test_auto_sync_only_when_off_and_no_class_open(app, client, monkeypatch):
    from app.services import sessions
    login(client, "prof", "prof123", PROF_SITE)
    now_ms = int(time.time() * 1000)
    post = lambda ms: client.post("/instructor/clock", json={"epoch_ms": ms, "auto": True},
                                  base_url=PROF_SITE).get_json()
    monkeypatch.setattr(sessions, "any_open_for_checkin", lambda db, now=None: False)
    j = post(now_ms + 5_000)                                   # close enough: nothing happens
    assert j["ok"] and not j["changed"] and not j["held"] and "simulated" not in j
    j = post(now_ms + 600_000)                                 # 10 min off: synced (simulated in tests)
    assert j["ok"] and j["simulated"] and j["offset_s"] in (599, 600, 601)
    monkeypatch.setattr(sessions, "any_open_for_checkin", lambda db, now=None: True)
    j = post(now_ms + 600_000)                                 # class is open: held for the instructor
    assert j["ok"] and j["held"] and not j["changed"]
    manual = client.post("/instructor/clock", json={"epoch_ms": now_ms + 600_000}, base_url=PROF_SITE).get_json()
    assert manual["simulated"]                                 # the button still works mid-class


def test_auto_sync_is_instructor_only(app, client):
    from .test_auth import give_signature
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    r = client.post("/instructor/clock", json={"epoch_ms": int(time.time() * 1000) + 600_000, "auto": True})
    assert r.status_code == 403


def test_any_open_for_checkin(conn):
    from datetime import datetime, timedelta
    from app.services import sessions
    row = conn.execute("SELECT * FROM class_sessions WHERE deleted_at IS NULL AND start_time IS NOT NULL LIMIT 1").fetchone()
    start = datetime.strptime(row["session_date"] + " " + row["start_time"][:5], "%Y-%m-%d %H:%M")
    conn.execute("UPDATE class_sessions SET checkin_mode = 'auto', ended_at = NULL WHERE id = ?", (row["id"],))
    assert sessions.any_open_for_checkin(conn, start + timedelta(minutes=1)) is True
    assert sessions.any_open_for_checkin(conn, start - timedelta(hours=5)) is False