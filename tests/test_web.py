import json
import re

from app.db import connect


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["database"] == "ok"


def test_dev_page_loads(client):
    r = client.get("/dev/")
    assert r.status_code == 200
    assert b"Dev Test Page" in r.data


def test_dev_page_is_separate_and_local_only(app, client):
    # other devices on the network can't open it
    r = client.get("/dev/", environ_base={"REMOTE_ADDR": "192.168.4.23"})
    assert r.status_code == 404
    # and it isn't linked from the instructor or student portals
    from .test_auth import PROF_SITE, give_signature, login
    login(client, "prof", "prof123", PROF_SITE)
    assert b'href="/dev/"' not in client.get("/instructor/", base_url=PROF_SITE).data
    give_signature(app, 2)
    student = app.test_client()
    login(student, "2026-0001", "student123")
    assert b'href="/dev/"' not in student.get("/student/").data


def test_dev_tap_records_attendance(app, client):
    r = client.post("/dev/tap", data={"uid": "card0001"}, follow_redirects=True)
    assert r.status_code == 200
    assert b"CHECKED IN: PRESENT" in r.data
    conn = connect(app.config["DATABASE"])
    assert conn.execute("SELECT method FROM attendance WHERE session_id = 1").fetchone()["method"] == "rfid"


def test_dev_unknown_card_shows_enroll_message(client):
    r = client.post("/dev/tap", data={"uid": "XYZ"}, follow_redirects=True)
    assert b"CARD NOT REGISTERED" in r.data


def lcd_preview(response):
    payload = re.search(r'<script id="lcd-preview-data" type="application/json">(.*?)</script>',
                        response.get_data(as_text=True), re.S)
    return json.loads(payload.group(1))


def test_dev_tap_has_countdown_then_idle(client):
    response = client.post("/dev/tap", data={"uid": "XYZ"}, follow_redirects=True)
    preview = lcd_preview(response)
    assert [frame[-1][-3:] for frame in preview["frames"]] == [" 2s", " 1s"]
    for frame in preview["frames"]:
        assert "CARD NOT REGISTERED" in frame[0]
        assert "ID: XYZ" in frame[-1]
        assert all(len(line) == 20 for line in frame)
    assert "TAP YOUR ID" in preview["ready"][-1]
    assert b"js/dev_lcd.js" in response.data
    # Refreshing the page must not replay the previous scan.
    assert lcd_preview(client.get("/dev/"))["frames"] == []


def test_dev_lcd_returns_to_active_registration(client):
    client.post("/dev/card/5/start")
    response = client.post("/dev/tap", data={"uid": "MARKNEW"}, follow_redirects=True)
    preview = lcd_preview(response)
    assert "CARD DETECTED" in preview["ready"][0]
    status = client.get("/dev/lcd")
    assert status.status_code == 200
    assert "CARD DETECTED" in status.get_json()["lines"][0]
    assert status.headers["Cache-Control"] == "no-store"
    assert client.get("/dev/lcd", environ_base={"REMOTE_ADDR": "192.168.4.23"}).status_code == 404


def test_dev_card_registration(app, client):
    client.post("/dev/card/5/start")
    client.post("/dev/tap", data={"uid": "MARKNEW"})
    client.post("/dev/card/5/confirm")
    conn = connect(app.config["DATABASE"])
    assert conn.execute("SELECT rfid_uid FROM users WHERE id = 5").fetchone()[0] == "MARKNEW"
