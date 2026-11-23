"""
Signatures drawn at check-in.

  web check-in  -> the student must sign on the spot; that drawing goes in the report cell
  card tap      -> no signing; the report uses the signature stored at registration

A drawing is one small PNG per check-in (data/signatures/checkins/<attendance id>.png). To keep
the SD card from filling up they are deleted after CHECKIN_SIGNATURE_DAYS (60) days - the
attendance record itself stays, and the report then shows the stored signature instead.
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .accounts import ValidationError, decode_signature

log = logging.getLogger(__name__)

SUBDIR = "checkins"
MAX_BYTES = 120_000           # the page shrinks the drawing first; a real one is 3-15 KB


def decode(data_url: str) -> bytes:
    """Check the drawing BEFORE recording attendance, so an unsigned web check-in is refused."""
    if not data_url:
        raise ValidationError({"signature": "Sign first to check in."})
    png = decode_signature(data_url)
    if len(png) > MAX_BYTES:
        raise ValidationError({"signature": "The signature image is too large. Clear it and sign again."})
    return png


def save(conn, session_id: int, student_id: int, png: bytes, signature_dir: Path) -> str | None:
    """Attach the drawing to the check-in that was just recorded. Returns the relative path."""
    row = conn.execute("SELECT id FROM attendance WHERE session_id = ? AND student_id = ? AND deleted_at IS NULL",
                       (session_id, student_id)).fetchone()
    if row is None:
        return None
    folder = Path(signature_dir) / SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{row['id']}.png").write_bytes(png)
    rel = f"signatures/{SUBDIR}/{row['id']}.png"
    conn.execute("UPDATE attendance SET signature_path = ? WHERE id = ?", (rel, row["id"]))
    conn.commit()
    return rel


def files_for_sessions(conn, session_ids, signature_dir: Path) -> dict:
    """{(student_id, session_id): Path} for check-ins that still have their drawing."""
    out = {}
    if not session_ids:
        return out
    q = ",".join("?" * len(session_ids))
    for r in conn.execute(
            f"""SELECT id, session_id, student_id FROM attendance
                WHERE session_id IN ({q}) AND signature_path IS NOT NULL AND deleted_at IS NULL""",
            list(session_ids)):
        p = Path(signature_dir) / SUBDIR / f"{r['id']}.png"
        if p.exists():
            out[(r["student_id"], r["session_id"])] = p
    return out


def purge_old(conn, signature_dir: Path, days: int = 60, now=None) -> int:
    """Delete drawings older than `days`. Returns how many files were removed."""
    now = now or datetime.now()
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    folder = Path(signature_dir) / SUBDIR
    removed = 0
    rows = conn.execute("SELECT id FROM attendance WHERE signature_path IS NOT NULL AND checked_in_at < ?",
                        (cutoff,)).fetchall()
    for r in rows:
        p = folder / f"{r['id']}.png"
        try:
            p.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            log.exception("could not delete %s", p)
            continue
        conn.execute("UPDATE attendance SET signature_path = NULL WHERE id = ?", (r["id"],))
    conn.commit()
    # leftovers with no record any more (record deleted for good): go by the file's age
    if folder.exists():
        known = {str(r[0]) for r in conn.execute("SELECT id FROM attendance WHERE signature_path IS NOT NULL")}
        limit = now.timestamp() - days * 86400
        for p in folder.glob("*.png"):
            try:
                if p.stem not in known and p.stat().st_mtime < limit:
                    p.unlink()
                    removed += 1
            except OSError:
                log.exception("could not delete %s", p)
    if removed:
        log.info("check-in signatures: %s older than %s days deleted", removed, days)
    return removed
