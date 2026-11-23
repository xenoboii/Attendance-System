"""Student Program + Year + Section, excused = present, and the auto secret key."""
import sqlite3

import pytest

from app.migrations import MIGRATIONS
from app.services import accounts, sessions
from app.services.accounts import ValidationError

MARIA = 2


def test_profile_saves_section_letter(conn):
    user = accounts.get_user(conn, MARIA)
    accounts.update_profile(conn, user, {"program": "bscpe", "year_level": "4", "section_letter": "a"})
    u = accounts.get_user(conn, MARIA)
    assert (u["program"], u["year_level"], u["section_letter"]) == ("BSCPE", 4, "A")
    with pytest.raises(ValidationError) as e:
        accounts.update_profile(conn, u, {"program": "Bachelor of Science in CpE", "section_letter": "A-1"})
    assert {"program", "section_letter"} <= set(e.value.errors)


def test_course_section_filter(app):
    f = app.jinja_env.filters["course_section"]
    assert f({"program": "BSCPE", "year_level": 4, "section_letter": "A"}) == "BSCPE 4A"
    assert f({"program": "BSCPE", "year_level": 4, "section_letter": None}) == "BSCPE 4"
    assert f({"program": "BSCPE", "year_level": None, "section_letter": "A"}) == "BSCPE"
    assert f({"program": None, "year_level": None}) == ""


def test_migration_splits_program_typed_as_bscpe_4a():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE users (id INTEGER, role TEXT, program TEXT, year_level INTEGER)")
    db.executemany("INSERT INTO users VALUES (?, 'student', ?, ?)",
                   [(1, "BSCPE 4A", 4), (2, "BSCPE", 4), (3, "BSIT 3b", None)])
    db.executescript(MIGRATIONS[5])
    rows = db.execute("SELECT id, program, year_level, section_letter FROM users ORDER BY id").fetchall()
    assert rows == [(1, "BSCPE", 4, "A"), (2, "BSCPE", 4, None), (3, "BSIT", 3, "B")]


def test_excused_counts_as_present_but_is_shown_separately():
    c = sessions.summarize([{"status": "present"}, {"status": "excused"}, {"status": "absent"}])
    # rate: present + excused out of 3 held; the numbers on screen stay separate
    assert c["rate"] == 67 and c["present"] == 1 and c["excused"] == 1 and c["absent"] == 1


def test_secret_key_is_random_and_kept(tmp_path, monkeypatch):
    import config
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr(config, "_DATA_DIR", tmp_path)
    k1 = config._secret_key()
    assert len(k1) == 64 and k1 != "dev-only-change-this-on-the-pi"
    assert config._secret_key() == k1                         # same key after a restart
    assert (tmp_path / "secret_key").read_text().strip() == k1
