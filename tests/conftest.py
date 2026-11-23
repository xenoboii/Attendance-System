"""
Shared test fixtures. Every test gets a FRESH database with the demo data
(app/seed.py), so tests never touch your real data/attendance.db.

Demo ids: instructor=1, Maria=2 (CARD0001), Jose=3 (CARD0002), Ana=4 (CARD0003),
          Mark=5 (no card). Today's class starts at 07:00 (T0 = 07:01).
"""
from datetime import datetime, timedelta

import pytest

from app import create_app
from app.db import connect, init_db
from app.seed import seed_demo

T0 = datetime(2026, 11, 16, 7, 1, 0)   # seed_demo(now=T0) -> class starts 07:00

MARIA, JOSE, ANA, MARK = 2, 3, 4, 5


def at(hhmm: str, seconds: int = 0) -> datetime:
    """at('07:16') -> datetime on the demo day."""
    h, m = map(int, hhmm.split(":"))
    return T0.replace(hour=h, minute=m, second=0) + timedelta(seconds=seconds)


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    c = connect(path)
    seed_demo(c, now=T0)
    yield c
    c.close()


@pytest.fixture
def app(tmp_path):
    path = tmp_path / "web.db"
    init_db(path)
    c = connect(path)
    seed_demo(c)            # real "now", so the dev page shows an open session
    c.close()
    return create_app({
        "TESTING": True, "SIMULATE_HARDWARE": True, "DATABASE": path, "CSRF_ENABLED": False,
        "DATA_DIR": tmp_path, "SIGNATURE_DIR": tmp_path / "sig", "REPORT_DIR": tmp_path / "rep",
    })


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_account(app):
    from app.services import accounts
    form = {"username": "supportadmin", "first_name": "Admin", "last_name": "Support",
            "password": "supportadmin123", "confirm_password": "supportadmin123"}
    with connect(app.config["DATABASE"]) as db:
        form["id"] = accounts.create_admin(db, form)
    return form
