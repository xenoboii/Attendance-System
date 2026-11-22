# RPi Attendance System

Flask + SQLite attendance system with RFID and web check-in, built to run on a
Raspberry Pi with its own Wi-Fi access point. It runs fully on a laptop with
**simulated hardware** (no RFID reader or LCD needed).

> **New to the project?** Read [START_HERE.md](START_HERE.md): install Python, double-click
> `setup.bat`, then `start.bat`. Raspberry Pi setup: [docs/PI_SETUP.md](docs/PI_SETUP.md).

## Setup (laptop, VS Code terminal)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
flask --app run seed-demo
python run.py
```

### Report files (not pip packages - copy them with the project)

| File | What for |
|---|---|
| `app/static/fonts/OLDENGL.TTF` | Old English Text MT, the university name on the attendance sheet. Licensed Microsoft Office font: it's kept out of git, so copy it yourself (from `C:\Windows\Fonts\OLDENGL.TTF`). Without it the report uses a plain serif font. |
| `app/static/img/school_logo.png` | LSPU seal on the attendance sheet |

If either is missing, the app writes a warning to `logs/app.log` at startup.

| Who | Laptop | Raspberry Pi |
|---|---|---|
| Students | http://localhost:5000 | http://attendance.lan |
| Instructors | http://prof.localhost:5000 | http://prof.attendance.lan |
| Dev test page | http://localhost:5000/dev | (off when `SIMULATE_HARDWARE=0`) |

Instructor registration only exists on the instructor address and needs the
code in `INSTRUCTOR_REGISTRATION_CODE` (config.py). Demo accounts (test only) are
listed at the top of `app/seed.py`.

### Admin portal and courses

Admins sign in through the instructor address and land at `/admin/`.
Only the **Admin** role can add courses and enable/disable course abbreviations.
Students select an available course in **My account**; disabling a course preserves
existing profiles and report labels. **Student support** lets Admin correct names,
student numbers, course/year/section and contact details, reset signatures, and
unlink lost RFID cards. Instructors can view student information but cannot make
these corrections. Students still draw their own initial/replacement signatures
and scan their own initial/replacement cards after Admin authorizes a replacement.

All roles can edit their own profile picture using the pencil on the picture in
**My account**. Upload JPG, PNG or WEBP (up to 2 MB and 12 megapixels), then drag and
zoom to choose the crop before saving. Click the saved picture for a larger preview;
its three-dot menu offers Download and Remove. Pictures are stored in the database,
so database backups include them. Student identity and locked academic details still
require Admin corrections.

Course choices come from the same catalog in student profiles and the instructor's
**Add section** form. Section choices are **A–G**, with years **1–5**.
Students save course, year, and section together once; later changes require Admin.
A join code only admits a student directly when all three saved values match the
section and its approval policy allows it. In section **Settings → Approval to join**,
choose **Every student** to require approval even for matching students, or
**Anyone with the code** to allow direct entry regardless of course/year/section.
The default only requires approval when course, year, or section differs. **Request approval**
submits a pending request, reviewed by the section's instructor under **Students → Requests**.
The instructor can approve or decline any request. Pending/declined students are not
enrolled and cannot view the class or take attendance in it. Existing enrollments
are preserved. Initial academic details are self-reported; Admin can correct them.

In **Schedule → Vacation**, choose an inclusive start/end date to skip classes without
absences, for this section or all your sections. Vacation dates are listed under
**No class days** and can be removed there. A range containing opened sessions or
recorded attendance is refused. Upcoming shows the next four entries; Upcoming and
Ended are foldable. The Attendance tab uses normal page scrolling; student rosters,
schedules, and student attendance history scroll inside their own panels on desktop.

Create a named Admin account from the project directory (the command prompts
for first name, last name, and a password with confirmation):

```powershell
.\venv\Scripts\flask.exe --app run create-admin admin
```

On the Pi, use `venv/bin/flask --app run create-admin admin` after loading
`/etc/attendance.env`. Admin accounts have no public registration or default password.
Backup codes can be generated in **My account** after signing in.

The initial engineering abbreviations use uppercase spelling for consistency:
`BSABE`, `BSAEROE`, `BSCERE`, `BSCHE`, `BSCE`, `BSCPE`, `BSEE`, `BSECE`, `BSGE`,
`BSGEOE`, `BSIE`, `BSMARE`, `BSMATE`, `BSME`, `BSMECE`, `BSMETE`, `BSEM`, `BSPETE`, `BSSE`.
This is a Philippine starter list, not a universal list of all engineering degrees;
schools may use different abbreviations. Sources: [DepEd career course list](https://sites.google.com/deped.gov.ph/career-one-stop-shop/college)
and [SLU engineering programs](https://www.slu.edu.ph/sea/).
The existing demo programs `BSIT` and `BSCS`, plus any courses already saved in
student profiles, are retained in the catalog.

On the Pi, both names point to the Pi itself via dnsmasq:
`address=/attendance.lan/192.168.4.1` (this also covers `prof.attendance.lan`).

## Project layout

```
config.py              all settings (override with environment variables)
run.py                 start the web app
app/
  __init__.py          create_app(): config, logging, error handler, CLI commands
  db.py                database connection + init
  schema.sql           all tables
  seed.py              demo data
  services/            BUSINESS LOGIC - plain Python, no Flask, fully tested
    checkin_state.py       disabled / present / late (auto + manual mode)
    attendance_service.py  check-in for RFID + web, button status
    card_registration.py   one-at-a-time card registration, handle_tap()
  routes/              web pages (Flask blueprints)
    main.py                /  and  /health
    dev.py                 /dev test page (only when SIMULATE_HARDWARE=1)
  templates/           HTML (Jinja)
  static/              CSS, JS, images (served locally - works offline)
hardware/              Pi hardware (simulated on the laptop)
  lcd_display.py           4x20 LCD screens (prints to terminal if no LCD)
  rfid_reader.py           reader loop + buzzer
tests/                 pytest tests (each test uses a fresh temporary database)
tools/load_test.py     many students logging in / checking in at the same moment
data/                  database, signatures, reports   (git-ignored)
logs/                  app.log, hardware.log           (git-ignored)
```

## Testing without hardware

| What | How |
|---|---|
| Automated tests | `pytest` (or `pytest -v` to see each test name) |
| Many users at once (load test) | `python tools/load_test.py` (50 students) or `--students 100 --card-students 30` — uses a throw-away database |
| Browser test page | `python run.py` → http://localhost:5000/dev — tap fake cards, see the LCD, press web check-in, run card registration, switch auto/manual |
| Terminal RFID simulator | `python -m hardware.rfid_reader` — type a UID (e.g. `CARD0001`) + Enter |
| Fresh demo data | `flask --app run seed-demo` (deletes the current database!) |

Run the web app and the terminal simulator at the same time (two terminals) to
see both processes sharing the same database, like on the Pi.

## When something goes wrong

1. **Read the log** — `logs/app.log` (web) or `logs/hardware.log` (RFID/LCD).
   Every request, check-in, tap and error (with full traceback) is there.
2. **Check `/health`** — shows whether the database is reachable and how many sessions exist today.
3. **Run `pytest`** — if a test fails, the bug is in `app/services/` (logic), not the web page.
4. **Look at the `rfid_scans` table / "Last 10 RFID taps"** on `/dev` — every tap is
   recorded with its result (`ok`, `late`, `duplicate`, `unknown_card`, `no_open_session`...).
5. More detail: `set LOG_LEVEL=DEBUG` before `python run.py`.

Where to look by symptom:

| Symptom | Look in |
|---|---|
| Wrong present/late/disabled | `app/services/checkin_state.py`, session settings in `/dev` |
| Tap does nothing / wrong result | `logs/hardware.log`, `card_registration.handle_tap()` |
| Page error 500 | `logs/app.log` (traceback) |
| LCD text cut off | `hardware/lcd_display.py`, `pytest tests/test_lcd.py` |
| "database is locked" | another program has the DB open; keep `data/` out of OneDrive sync |

## On the Raspberry Pi

Full step-by-step guide with the wiring / pin map: **[docs/PI_SETUP.md](docs/PI_SETUP.md)**.
Short version, on the Pi:

```
bash deploy/install.sh      # packages, SPI/I2C, database, services that start on boot
bash deploy/hotspot.sh      # the Pi's own Wi-Fi: attendance.lan / prof.attendance.lan
```

Pins (BCM): RC522 on SPI0 (CE0, RST=GPIO25) · LCD on I2C (GPIO2/3) · buzzer GPIO18 ·
green LED GPIO23 · red LED GPIO24.
