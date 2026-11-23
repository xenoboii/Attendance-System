"""Log rotation never breaks logging (Windows: the file can be locked by another process)."""
import logging
import os

from app.logging_setup import SafeRotatingFileHandler


def test_locked_log_file_keeps_logging(tmp_path, monkeypatch):
    h = SafeRotatingFileHandler(tmp_path / "app.log", maxBytes=50, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))

    def locked(*a, **k):
        raise PermissionError(32, "being used by another process")
    monkeypatch.setattr(os, "rename", locked)
    monkeypatch.setattr(os, "replace", locked)

    errors = []
    monkeypatch.setattr(h, "handleError", lambda record: errors.append(record))
    for i in range(10):
        h.emit(logging.makeLogRecord({"msg": f"line {i} " + "x" * 20}))
    h.close()
    assert not errors                                           # no "--- Logging error ---"
    assert "line 9" in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_livereload_is_not_logged(app, client, caplog):
    caplog.set_level(logging.INFO, logger="app")
    client.get("/dev/livereload")
    assert "/dev/livereload" not in caplog.text
