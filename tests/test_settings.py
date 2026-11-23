"""Settings page: theme, text size, report signature default."""
from app.db import connect
from app.migrations import MIGRATIONS

from .test_auth import PROF_SITE, give_signature, login


def test_settings_columns_exist(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    assert {"theme", "text_size", "report_sign_default"} <= cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_settings_page_and_save(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    page = client.get("/settings/").data
    assert b'name="theme"' in page and b'name="text_size"' in page and b'name="accent"' in page
    assert b"report_sign_default" not in page                         # instructors only
    r = client.post("/settings/", data={"theme": "dark", "text_size": "large", "accent": "purple"},
                    headers={"X-Requested-With": "fetch"})
    assert r.get_json() == {"ok": True}
    u = connect(app.config["DATABASE"]).execute("SELECT theme, text_size, accent FROM users WHERE id = 2").fetchone()
    assert tuple(u) == ("dark", "large", "purple")
    # every page now starts in dark mode with the chosen color (applied before drawing)
    assert b'window.applyAppearance("dark", "large", "purple")' in client.get("/student/").data
    # a save without a color (older page) keeps the saved one
    client.post("/settings/", data={"theme": "light", "text_size": "normal"}, headers={"X-Requested-With": "fetch"})
    u = connect(app.config["DATABASE"]).execute("SELECT theme, accent FROM users WHERE id = 2").fetchone()
    assert tuple(u) == ("light", "purple")


def test_invalid_settings_rejected(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    assert client.post("/settings/", data={"theme": "purple", "text_size": "normal"}).status_code == 400
    assert client.post("/settings/", data={"theme": "light", "text_size": "normal", "accent": "neon"}).status_code == 400


def test_instructor_report_default_prechecks_option(app, client):
    c = connect(app.config["DATABASE"])
    c.execute("UPDATE users SET signature_path = 'x' WHERE id = 1")
    c.commit()
    login(client, "prof", "prof123", PROF_SITE)
    client.post("/settings/", base_url=PROF_SITE, data={"theme": "system", "text_size": "normal", "report_sign_default": "1"})
    assert c.execute("SELECT report_sign_default FROM users WHERE id = 1").fetchone()[0] == 1
    page = client.get("/instructor/sections/1", base_url=PROF_SITE).data.decode()
    sign_box = page[page.index('name="sign"') - 120: page.index('name="sign"') + 200]
    assert "checked" in sign_box


def test_card_registration_is_in_my_account(app, client):
    c = connect(app.config["DATABASE"])
    c.execute("UPDATE users SET rfid_uid = NULL WHERE id = 2")
    c.commit()
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    page = client.get("/account/").data
    assert b'id="card-modal"' in page and b'data-action="start"' in page and b"card_registration.js" in page
    # the old page address now opens My account with the pop-up
    assert client.get("/student/card").location.endswith("/account/?card=1")
    side = client.get("/student/").data
    assert b'href="/student/card"' not in side                         # no separate sidebar item
    assert b">ID</span>" in side                                      # reminder on My account


def test_no_card_popup_once_registered(app, client):
    give_signature(app, 2)                                               # Maria already has CARD0001
    login(client, "2026-0001", "student123")
    assert b'id="card-modal"' not in client.get("/account/").data


def test_sidebar_has_settings(app, client):
    give_signature(app, 2)
    login(client, "2026-0001", "student123")
    assert b'href="/settings/"' in client.get("/student/").data
