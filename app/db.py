"""
Database helpers shared by the web app and the hardware process.

    connect(path)  -> plain connection (hardware, tests, scripts)
    get_db()       -> per-request connection inside Flask
    init_db(path)  -> create tables from schema.sql
"""
import logging
import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = Path(__file__).resolve().parent / "schema.sql"
log = logging.getLogger(__name__)


def connect(path) -> sqlite3.Connection:
    # timeout: wait up to 10s if the other process (web / hardware) is writing
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path, reset: bool = False) -> None:
    path = Path(path)
    if reset and path.exists():
        path.unlink()
        for extra in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            if extra.exists():
                extra.unlink()
    if path.exists():
        raise FileExistsError(f"{path} already exists (use --reset to recreate it)")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    from .migrations import migrate
    migrate(conn)
    conn.close()
    log.info("database created at %s", path)


def upgrade_db(path) -> None:
    """Bring an existing database up to date (runs on every startup, safe to repeat)."""
    if Path(path).exists():
        from .migrations import migrate
        conn = connect(path)
        migrate(conn)
        conn.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
