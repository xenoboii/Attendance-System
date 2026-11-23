"""The device answers phones' "is there internet?" checks, so they stay on its Wi-Fi."""
from .test_auth import login


def test_probe_answers(client):
    # whatever name the phone asked for resolves to the device, so the Host header is foreign
    r = client.get("/generate_204", base_url="http://connectivitycheck.gstatic.com")
    assert r.status_code == 204 and r.data == b""
    assert client.get("/gen_204", base_url="http://www.google.com").status_code == 204
    apple = client.get("/hotspot-detect.html", base_url="http://captive.apple.com")
    assert apple.status_code == 200 and b"<TITLE>Success</TITLE>" in apple.data and b"<BODY>Success</BODY>" in apple.data
    assert client.get("/library/test/success.html", base_url="http://www.apple.com").status_code == 200
    win = client.get("/connecttest.txt", base_url="http://www.msftconnecttest.com")
    assert win.data == b"Microsoft Connect Test"
    assert client.get("/ncsi.txt", base_url="http://www.msftncsi.com").data == b"Microsoft NCSI"
    assert client.get("/success.txt", base_url="http://detectportal.firefox.com").data == b"success\n"


def test_probes_work_for_a_student_who_still_has_to_sign(app, client):
    """A logged-in student with no signature is redirected everywhere else - but not here."""
    login(client, "2026-0001", "student123")
    assert client.get("/student/").status_code == 302
    assert client.get("/generate_204").status_code == 204
    assert client.get("/hotspot-detect.html").status_code == 200


def test_other_names_still_reach_the_login_page(client):
    r = client.get("/", base_url="http://some-random-site.example")
    assert r.status_code == 302 and r.location.endswith("/login")
