"""
Load test: many students using the system AT THE SAME TIME.

Runs the real app on the production server (waitress, like on the Pi) with a throw-away database
- your real data is never touched. Every student is a separate client with its own login cookie.

    python tools/load_test.py               # 50 students, 4 server threads (Pi default)
    python tools/load_test.py --students 80 --threads 4

Rounds:
  1. Login rush        - everyone logs in at the same moment
  2. Check-in rush     - everyone presses "Check in" at the same moment (prof's live page refreshing too)
  3. Double tap        - everyone presses it AGAIN at once -> must all be refused, still 1 record each
  4. Card + button     - students tap their RFID card AND press the web button at the same instant
                         (two processes writing at once, like the Pi) -> exactly 1 record each
Then it checks the database: no lost check-ins, no duplicates.
"""
import argparse
import http.cookiejar
import re
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# web check-in must be signed: a 1x1 PNG stands in for the drawing
SIGNATURE = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
             "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
sys.path.insert(0, str(ROOT))

from waitress.server import create_server            # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app                            # noqa: E402
from app.db import connect, init_db                   # noqa: E402
from app.seed import seed_demo                        # noqa: E402
from app.services import attendance_service as attendance   # noqa: E402

PASSWORD = "loadtest123"


# ---------------------------------------------------------------- setup
def build_app(tmp: Path, students: int, card_students: int):
    app = create_app({"TESTING": True, "DATABASE": tmp / "load.db", "DATA_DIR": tmp,
                      "SIGNATURE_DIR": tmp / "sig", "REPORT_DIR": tmp / "rep", "LOG_DIR": tmp / "logs"})
    init_db(app.config["DATABASE"], reset=True)
    conn = connect(app.config["DATABASE"])
    seed_demo(conn)                       # prof + IT312 BSIT 3A with a session open as PRESENT now
    pw = generate_password_hash(PASSWORD)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(1, students + card_students + 1):
        number = f"LT-{i:04d}"
        card = f"LTCARD{i:04d}" if i > students else None      # the last group has RFID cards
        cur = conn.execute(
            """INSERT INTO users (role, username, password_hash, first_name, last_name, student_number,
                                  rfid_uid, signature_path)
               VALUES ('student', ?, ?, 'Load', ?, ?, ?, 'x')""",
            (number, pw, f"Student{i:04d}", number, card))
        conn.execute("INSERT INTO enrollments (section_id, student_id, enrolled_at) VALUES (1, ?, ?)",
                     (cur.lastrowid, yesterday))
    conn.commit()
    session_id = conn.execute(
        """SELECT id FROM class_sessions WHERE section_id = 1 AND type <> 'async'
           AND session_date = date('now','localtime') ORDER BY id LIMIT 1""").fetchone()[0]
    conn.close()
    return app, session_id


class Client:
    """One browser: its own cookies, reads the CSRF token like the real page does."""

    def __init__(self, base, host):
        self.base, self.host = base, host
        self.jar = http.cookiejar.CookieJar()

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect)
        self.csrf = None

    def request(self, method, path, data=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, method=method, headers={"Host": self.host})
        t0 = time.perf_counter()
        try:
            r = self.opener.open(req, timeout=60)
            status, text = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:            # 302 lands here because redirects are off
            status, text = e.code, e.read().decode("utf-8", "replace")
        ms = (time.perf_counter() - t0) * 1000
        m = re.search(r'name="csrf-token" content="([^"]+)"', text)
        if m:
            self.csrf = m.group(1)
        return status, ms

    def login(self, number, password=PASSWORD, home="/student/"):
        """Log in, then open the dashboard like a browser does (it carries the new form token)."""
        self.request("GET", "/login")
        status, ms = self.request("POST", "/login", {"login": number, "password": password, "csrf_token": self.csrf})
        self.request("GET", home)
        return status, ms


def rush(n, fn):
    """Run fn(i) in n threads that all start at the same instant. Returns [(ok, ms), ...]."""
    gate = threading.Barrier(n)
    out = [None] * n

    def worker(i):
        gate.wait()
        try:
            out[i] = fn(i)
        except Exception as e:                         # a crash counts as a failure
            out[i] = (False, 0.0, repr(e))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out, time.perf_counter() - t0


def report(title, results, wall):
    ok = [r for r in results if r and r[0]]
    ms = sorted(r[1] for r in results if r)
    p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else 0
    print(f"\n{title}")
    print(f"  requests: {len(results)}   ok: {len(ok)}   failed: {len(results) - len(ok)}")
    print(f"  response time  avg {statistics.mean(ms):.0f} ms   95% under {p95:.0f} ms   slowest {ms[-1]:.0f} ms")
    print(f"  all {len(results)} finished in {wall:.2f} s")
    for r in results:
        if r and not r[0] and len(r) > 2:
            print("  error:", r[2])
            break
    return len(ok) == len(results)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=50, help="students using the website (default 50)")
    ap.add_argument("--card-students", type=int, default=20, help="students racing card vs button (default 20)")
    ap.add_argument("--threads", type=int, default=4, help="waitress threads (the Pi default is 4)")
    a = ap.parse_args()
    import logging
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)    # "Task queue depth is N" = just busy

    tmp = Path(tempfile.mkdtemp(prefix="attendance-load-"))
    app, session_id = build_app(tmp, a.students, a.card_students)
    server = create_server(app, host="127.0.0.1", port=0, threads=a.threads)
    threading.Thread(target=server.run, daemon=True).start()
    base, host = f"http://127.0.0.1:{server.effective_port}", "attendance.lan"
    print(f"Server: waitress, {a.threads} threads  |  {a.students} web students + {a.card_students} card/web racers")
    print(f"Throw-away database: {tmp / 'load.db'}")

    all_ok = True
    clients = [Client(base, host) for _ in range(a.students)]

    # 1. login rush
    res, wall = rush(a.students, lambda i: (lambda s: (s[0] == 302, s[1]))(clients[i].login(f"LT-{i + 1:04d}")))
    all_ok &= report("1. Login rush (everyone logs in at once)", res, wall)

    # 2. check-in rush, while the prof's live page keeps refreshing
    prof = Client(base, "prof.attendance.lan")
    prof.login("prof", "prof123", home="/instructor/")
    stop, prof_ms = threading.Event(), []

    def prof_live():
        while not stop.is_set():
            s, ms = prof.request("GET", f"/instructor/sessions/{session_id}/live")
            prof_ms.append((s == 200, ms))
    pt = threading.Thread(target=prof_live)
    pt.start()
    res, wall = rush(a.students, lambda i: (lambda s: (s[0] == 302, s[1]))(
        clients[i].request("POST", "/student/checkin", {"csrf_token": clients[i].csrf, "signature": SIGNATURE})))
    stop.set()
    pt.join()
    all_ok &= report("2. Check-in rush (everyone presses Check in at once)", res, wall)
    print(f"  prof's live list refreshed {len(prof_ms)} times meanwhile, "
          f"all ok: {all(p[0] for p in prof_ms)}, slowest {max(p[1] for p in prof_ms):.0f} ms")

    # 3. double tap
    res, wall = rush(a.students, lambda i: (lambda s: (s[0] == 302, s[1]))(
        clients[i].request("POST", "/student/checkin", {"csrf_token": clients[i].csrf, "signature": SIGNATURE})))
    all_ok &= report("3. Double tap (everyone presses it again at once)", res, wall)

    # 4. RFID card + web button at the same instant (separate DB connection = the hardware process)
    racers = [Client(base, host) for _ in range(a.card_students)]
    first = a.students + 1
    for i, c in enumerate(racers):
        c.login(f"LT-{first + i:04d}")
    outcomes = []

    def race(i):
        if i % 2 == 0:                              # even: web button
            s = racers[i // 2].request("POST", "/student/checkin", {"csrf_token": racers[i // 2].csrf, "signature": SIGNATURE})
            return (s[0] == 302, s[1])
        conn = connect(app.config["DATABASE"])     # odd: card tap on the reader
        t0 = time.perf_counter()
        r = attendance.rfid_tap(conn, f"LTCARD{first + i // 2:04d}")
        conn.close()
        outcomes.append(r["result"])
        return (r["result"] in ("present", "late", "duplicate"), (time.perf_counter() - t0) * 1000)
    res, wall = rush(a.card_students * 2, race)
    all_ok &= report("4. Card tap + web button at the same instant (same student)", res, wall)

    # ---- the database must be exactly right
    conn = connect(app.config["DATABASE"])
    rows = conn.execute(
        """SELECT u.student_number, COUNT(*) AS n FROM attendance a JOIN users u ON u.id = a.student_id
           WHERE a.session_id = ? AND a.deleted_at IS NULL AND u.student_number LIKE 'LT-%'
           GROUP BY a.student_id""", (session_id,)).fetchall()
    expected = a.students + a.card_students
    dupes = [r["student_number"] for r in rows if r["n"] > 1]
    print("\nDatabase check")
    print(f"  students checked in: {len(rows)} of {expected}   duplicates: {len(dupes)}")
    all_ok &= len(rows) == expected and not dupes
    server.close()

    print("\nRESULT:", "PASS - nothing lost, nothing doubled" if all_ok else "FAIL - see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
