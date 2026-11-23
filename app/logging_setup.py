"""
Logging to the console AND to logs/<name>.log (rotates at 1 MB, keeps 5 files).

    logs/app.log       -> web app (requests, check-ins, errors with tracebacks)
    logs/hardware.log  -> RFID reader + LCD process
"""
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


class SafeRotatingFileHandler(RotatingFileHandler):
    """Windows can't rename a log file another process still has open, which used to print
    '--- Logging error ---' on every line. If the rename fails, keep writing to the same file
    and try again a minute later instead."""
    _retry_at = 0.0

    def shouldRollover(self, record):
        if time.time() < self._retry_at:
            return False
        return super().shouldRollover(record)

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            self._retry_at = time.time() + 60
            if self.stream is None:
                self.stream = self._open()


def setup_logging(log_dir: Path, name: str, level: str = "INFO", to_file: bool = True) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level.upper())

    # avoid duplicate handlers when Flask reloads
    for h in list(root.handlers):
        if getattr(h, "_attendance", False):
            root.removeHandler(h)

    handlers = [logging.StreamHandler()]
    if to_file:
        handlers.append(SafeRotatingFileHandler(
            log_dir / f"{name}.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8", delay=True))
    for h in handlers:
        h.setFormatter(logging.Formatter(FORMAT))
        h._attendance = True
        root.addHandler(h)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # we log requests ourselves
