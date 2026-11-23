"""Web check-in is signed on the spot; card taps use the stored signature; drawings last 60 days."""
import base64
from datetime import timedelta

from PIL import Image

from app.db import connect
from app.services import attendance_service as attendance
from app.services import checkin_signatures as cs
from app.services import report

from .conftest import JOSE, MARIA, at
from .test_auth import TINY_PNG, give_signature, login

PNG = base64.b64decode(TINY_PNG.split(",")[1])


def db(app):
    return connect(app.config["DATABASE"])


def test_web_checkin_without_signature_is_refused(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    r = client.post("/student/checkin", follow_redirects=True)
    assert b"Sign first to check in." in r.data
    assert client.get("/student/checkin/status").get_json()["variant"] == "present"     # still not checked in
    r = client.post("/student/checkin", data={"signature": "data:image/png;base64,AAAA"}, follow_redirects=True)
    assert b"invalid" in r.data
    assert db(app).execute("SELECT COUNT(*) FROM attendance WHERE student_id = 2 AND method = 'web'").fetchone()[0] == 0


def test_signed_web_checkin_saves_the_drawing(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    client.post("/student/checkin", data={"signature": TINY_PNG})
    row = db(app).execute("SELECT id, signature_path FROM attendance WHERE student_id = 2 AND method = 'web'").fetchone()
    assert row["signature_path"] == f"signatures/checkins/{row['id']}.png"
    assert (app.config["SIGNATURE_DIR"] / "checkins" / f"{row['id']}.png").read_bytes() == PNG
    # a second (duplicate) press doesn't write another file
    client.post("/student/checkin", data={"signature": TINY_PNG})
    assert len(list((app.config["SIGNATURE_DIR"] / "checkins").glob("*.png"))) == 1


def test_dashboard_has_the_sign_popup(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    page = client.get("/student/").data
    assert b'id="sign-modal"' in page and b"Sign &amp; check in" in page and b'id="signature-canvas"' in page


def test_card_tap_needs_no_signature(conn, tmp_path):
    r = attendance.rfid_tap(conn, "CARD0001", at("07:00"))
    assert r["result"] == "present"
    assert conn.execute("SELECT signature_path FROM attendance WHERE session_id = 1 AND student_id = ?", (MARIA,)).fetchone()[0] is None
    assert cs.files_for_sessions(conn, [1], tmp_path) == {}


def _checkin_with_drawing(conn, tmp_path, student, when):
    attendance.web_check_in(conn, student, now=when)
    cs.save(conn, 1, student, PNG, tmp_path)


def test_report_prefers_the_checkin_drawing(conn, tmp_path):
    attendance.rfid_tap(conn, "CARD0001", at("07:00"))                 # Maria: card -> stored signature
    _checkin_with_drawing(conn, tmp_path, JOSE, at("07:01"))           # Jose: web -> drawn just now
    files = cs.files_for_sessions(conn, [1], tmp_path)
    assert list(files) == [(JOSE, 1)]
    Image.new("RGBA", (40, 12), (10, 20, 90, 255)).save(tmp_path / f"{MARIA}.png")
    conn.execute("UPDATE users SET signature_path = 'x' WHERE id = ?", (MARIA,))
    conn.commit()
    chosen = report.validate_selection(conn, 1, [1], at("08:00"))
    section = dict(__import__("app.services.classes", fromlist=["x"]).get_section(conn, 1, 1))
    instructor = dict(conn.execute("SELECT * FROM users WHERE id = 1").fetchone())
    both = report.build_pdf(conn, section, instructor, chosen, tmp_path, now=at("08:00"))
    files[(JOSE, 1)].unlink()                                          # drawing gone, Jose has none stored -> "P"
    one = report.build_pdf(conn, section, instructor, chosen, tmp_path, now=at("08:00"))
    assert both.count(b"/Subtype /Image") > one.count(b"/Subtype /Image")


def test_drawings_are_deleted_after_60_days(conn, tmp_path):
    _checkin_with_drawing(conn, tmp_path, JOSE, at("07:01"))
    f = cs.files_for_sessions(conn, [1], tmp_path)[(JOSE, 1)]
    assert cs.purge_old(conn, tmp_path, 60, at("07:01") + timedelta(days=59)) == 0 and f.exists()
    assert cs.purge_old(conn, tmp_path, 60, at("07:01") + timedelta(days=61)) == 1 and not f.exists()
    row = conn.execute("SELECT status, signature_path FROM attendance WHERE session_id = 1 AND student_id = ?", (JOSE,)).fetchone()
    assert row["status"] == "present" and row["signature_path"] is None           # the record itself stays


def test_cleared_then_card_tap_drops_the_old_drawing(conn, tmp_path):
    from app.services import sessions
    _checkin_with_drawing(conn, tmp_path, JOSE, at("07:01"))
    sessions.mark(conn, 1, 1, JOSE, "clear", at("07:02"))
    attendance.rfid_tap(conn, "CARD0002", at("07:03"))
    row = conn.execute("SELECT method, signature_path FROM attendance WHERE session_id = 1 AND student_id = ?", (JOSE,)).fetchone()
    assert row["method"] == "rfid" and row["signature_path"] is None
