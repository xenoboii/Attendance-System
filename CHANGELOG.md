# Changelog

What changed in the Attendance System, newest first. Written for the group, not as code notes.

## November 21, 2026

- **Charging indicator fixed for the Waveshare UPS HAT (D).** Its battery chip reads charging
  current as negative (measured: about -1800 with the charger in), so the device never showed
  charging. The direction is now flipped automatically for the chip at 0x43
  (`BATTERY_CURRENT_INVERT=0/1` overrides it), and "plugged in" also shows when the cells are full
  and charging has stopped (any time the cells aren't being drained).

## November 20, 2026

- **LCD battery icon:** a small battery (self-drawn 5x8 character, 0-4 bars) in the last column of
  line 1, next to "ATTENDANCE SYSTEM"; a lightning bolt while charging. Line 3 is now empty except
  "LOW BATTERY 9%" at 10% and below (not charging).

- **Admin → Device settings** (new page): change the **instructor registration code** (saved in the
  database; the old fixed code in /etc/attendance.env is only the default), and the **hotspot
  Wi-Fi name and password** (needs the admin's password; every phone is disconnected and must
  join the new Wi-Fi). One-time setup on the Pi: `bash deploy/device-settings.sh`.
- **"Sync time from this phone" fixed after turning on the RTC:** saving into the RTC used
  `hwclock`, which Raspberry Pi OS Lite may not have, and its failure made the whole sync fail.
  The helper now writes the RTC directly if `hwclock` is missing, and an RTC problem only adds a
  warning to the log (the clock is still set). Reinstall the helper: see docs/PI_SETUP.md.
- **RTC falling behind explained + fixed:** the Pi wasn't using the RTC (it restored the last
  saved time at each power-on). `bash deploy/rtc-setup.sh` turns it on; the hardware check now
  says whether the RTC is in use and how far it is from the Pi's time.

- **Hardware check command:** `bash deploy/check-hardware.sh` on the Pi checks I2C/SPI, the LCD,
  card reader, UPS (and cell voltage), RTC, buzzer, LEDs, shutdown button, power and services,
  and prints ✓ / ✗ with what to fix. `--quick` skips the tap / press / beep tests.

- **PDF: "College of" line.** With the department printed it is now one clean, centered line,
  e.g. "College of Engineering" with only "Engineering" underlined, no long blank line. Without it the sheet keeps
  "College of ______" to fill in by hand.
- **Excel removed completely:** no Excel button on the section page and none inside the
  instructor's own backup. Instructors use the PDF report only.
- **Shutdown button fixed.** The reader now checks the button pin itself several times a second.
  Before, it relied on pin-change callbacks that the Pi's newer kernel doesn't support with the
  installed GPIO library, so holding the button did nothing.
- **Low-battery beep.** Low and not charging: beep-beep-beep every 2 minutes at 20% and below,
  every 30 seconds at 10% and below. Plugging in the charger stops it.
- **LOADING screen at power-on is back** (set up by `bash deploy/power-button.sh`).
- **UPS HAT (D) with two 21700 cells:** guide and wiring diagram updated (side by side = still
  `BATTERY_CELLS_SERIES=1`).
- **Attendance report window is wider:** class dates on the left, blank dates and print options
  on the right; full screen with the parts stacked on phones and tablets. The page count and
  Preview / Download stay at the bottom.

- **Student registration asks for course, year and section** (same dropdowns as My account:
  courses from the admin's list, 1st–5th year, sections A–G). Required, and locked once saved;
  only the admin can change them later. If the admin hasn't set up any courses yet, they're
  optional so students can still register.

## November 19, 2026

- **Report: blank dates to fill in on paper.** In the Attendance report window, "Blank dates" adds
  one or more dates (+ Add date, like Add schedule). Each prints as an empty column with every
  student's name, so the instructor can print the sheet and have students sign by hand. They can
  be mixed with real class dates, or used alone (the report button now works before any class).
- **Every enrolled student is listed on every session.** Students without a record show "—"
  until the class ends, then Absent. A student who joined the section after that class stays "—"
  and is never counted absent for it. (Before, they were hidden, so a session could show only the
  students who had checked in, or "No students enrolled".)

- **"RFID card" is now "school ID" everywhere users see it.** The school ID is the RFID card with
  the student's printed info. LCD idle screen: "TAP YOUR ID". Web pages say "School ID" /
  "Register school ID" / "Tap your school ID", and the "Card" labels (How column, student list,
  My account reminder, class history) say "ID". Nothing changed in how cards work.

- **Check-out (stops "check in from outside, then leave").** In a session's Check-in control
  card, a **Check-out** dropdown (Not required / Required) sits between Switch to Late and End
  session. When required, the instructor presses **Open check-out** near the end of class:
  check-in closes, and a card tap or the dashboard's **Check out** button (no signature) checks
  the student out. When the session ends, anyone who checked in but didn't check out is marked
  **absent** with a "No check-out" tag (the instructor can still change it). If check-out was
  never opened, nobody is marked absent for it. The roster shows each check-out time; the LCD
  shows "CHECKED OUT". Needs a database update (applied automatically on restart).
- **Shutdown button with confirmation.** As soon as the button is pressed the LCD shows
  "Keep holding the button... 3s 2s 1s" (letting go early cancels). Held 3 s, the LCD asks
  "SHUT DOWN? Press the button again to confirm". Press again within 10 s to shut down; otherwise
  it cancels and goes back to the normal screen, so an accidental press can't turn the device
  off. Then "SHUTTING DOWN / Do NOT switch off yet", and "Safe to switch OFF" once all data is
  saved (10–20 s). The button is now read by the RFID service instead of the Pi's built-in
  shutdown setting; set it up with `bash deploy/power-button.sh` and reboot.
- **Card registration: "already registered" message.** If a student registering a card taps a
  card that already belongs to another account, the registration page now says so (with the
  card ID) instead of waiting silently. The tap still counts as that account's attendance.
  The LCD shows "ALREADY REGISTERED", the owner's name and check-in result, and
  "Tap a different card".
- **RC522 reset pin** set explicitly to pin 22 (GPIO25): the reader library had picked pin 10
  because the buzzer/LEDs start first, so the reader never woke up inside the system.

- **Power-system changes undone.** The shutdown button, the LCD power screens (Loading,
  Shutting down, Safe to switch OFF, Wi-Fi starting) and the RC522 reset-pin change were removed;
  the RFID reader and LCD code are back to how they were on November 16.
- **Wiring diagram** in `docs/wiring-diagram.png` / `.svg` (redraw: `python tools/make_wiring_diagram.py`).
- Guide: buzzer choices (active 3–24V, louder with a transistor), LCD logic level converter,
  Waveshare UPS HAT (D) settings, updating the Pi over Wi-Fi or the Ethernet cable.

## November 16, 2026

- **Merge an imported record (admin).** For a student who, after a device rebuild, registered with
  a mistyped student number and so got an empty account. In **Student support**, open the account
  the student actually uses, pick their imported (not yet registered) record, and merge: the
  classes and attendance move into the account, it takes the correct student number, and it gets
  the card / signature only if it had none. The password stays. It can't be undone.
- `docs/PI_SETUP.md` now covers creating the admin account, the Backup & restore page and the
  admin recovery commands.
- Full system documentation written (shared separately as a document).

## November 15, 2026 (evening)

Tests at the end of the session: **410 passing** (last full run). Styling-only changes made after
that run were checked in the browser instead. Nothing here has been tried on a real Raspberry Pi yet.

After pulling these changes, **restart the server once** so the new database tables are created.

### Students

- **My classes page.** New menu item under Dashboard. It holds the join-code box, join requests,
  the class cards and Leave class. Cards in a row are the same height.
- **Dashboard calendar.** A month calendar on the left with dots for class days, make-up / async
  sessions and no-class days. Picking a date shows that day's classes in the card on the right
  (cancelled ones are struck through with the reason).
- **Class page layout.** Subject code, section and instructor on one line, Leave class at the far
  right. Below: a Schedule card (scrolls if long), the attendance rate, and Present / Late /
  Absent / Excused as a 2 x 2 block.
- **Drop warning.** My classes warns one absence before the instructor's limit, and at the limit.
- **Ended classes.** A class whose semester was ended shows "Semester ended" and no longer appears
  in the calendar or today's classes.
- **Fixed layout on desktop.** Dashboard and My classes fit the window; only the cards scroll.

### Instructors

- **End semester.** Button on the subject page and in each section's Settings. It ends the whole
  subject: the weekly schedule stops creating classes, students can't check in or join, and the
  subject moves to **Archive** (new menu item). Archived subjects are read-only, but the attendance
  report and Excel export still work. **Reopen** undoes it.
- **Copy to new term.** From Archive or an ended subject: a new subject for the chosen term with
  the same sections and weekly schedule, new join codes and no students.
- **Drop rule.** "Drop rule: absences" in section Settings (default 4, 0 = off). The Students tab
  flags **For dropping** at the limit and **1 absence left** just before it. The instructor decides:
  **Drop from class**, and a **Dropped** list to put someone back.
- **Excel export.** An **Excel** button beside **Attendance report**: one row per student, one
  column per class date (P / L / A / E), totals and rate. Needs no extra software on the Pi.
- **Department.** A dropdown (managed by the admin) instead of a text box.
- **Attendance sheet (PDF).** The "College of" underline starts right after the words, the
  department is centred on it, and the report pop-up has a tick box to print it or leave it blank.
- **Backup of my subjects** (Settings). Downloads a .zip with only that instructor's subjects,
  sections, schedules, attendance and their students' signatures, plus an Excel file per section.
  Other instructors' subjects and all passwords are left out.
- **Import my subjects** (Settings). For a rebuilt device: brings the subjects back from that
  .zip under the account that imports. Nothing existing is replaced; importing twice adds nothing.
- **Delete my account** (My account). Password + tick box. The instructor's active subjects are
  archived.
- **Small fixes.** Upcoming / Ended lists stay open or folded after marking a date; the student
  summary tiles share one row.

### Admin

- **Overview.** New first page: counts, storage (used / free, low-space warning), device status,
  Needs attention, recent activity. Each **Open** button lists the actual people. Fits the window.
- **Courses** moved to its own menu item.
- **Departments.** Add, disable and enable the colleges instructors can pick. Starts with 13
  colleges that still need checking against the campus list.
- **Backup codes.** Lists every student and instructor with codes left. **Issue a code** gives one
  single-use code to someone who lost theirs.
- **Signature retention.** "Keep check-in signatures for N days" on the Overview (7 to 730,
  default 60). Saving applies it at once.
- **Backup & restore.** Download one .zip with the whole database and all signatures. Restore
  replaces everything from such a file; it needs the admin password, a tick box, checks the file
  first, and keeps a safety copy of the current state in `data/backups/` (last 3). The Overview
  reminds when the last backup is 7 or more days old.
- **Accounts.** Delete a student or instructor account, and restore deleted ones (deleted by the
  admin or by the owner).
- **Commands.** `flask list-admins` shows admin usernames.

### Accounts and recovery

- **Students claim an imported record.** When an instructor's import creates a student who hasn't
  registered yet, registering with the same student number sets their password and their classes,
  history, card and signature are already there.
- **Deleting is reversible.** The account can't log in and its student number / username / card
  are freed, but the records stay so the admin can restore it. If the same number registers again
  first, the old account can no longer be restored.
- **Forgot password page** now says to ask the admin for a one-time backup code.

### Technical notes

- New database migrations **19 to 23**: `departments`, `subjects.ended_at`,
  `sections.drop_absences`, `app_settings`, `deleted_accounts`.
- `requirements.txt` now needs **Flask 3.1 or newer** (for the large restore / import uploads).
- New services: `terms`, `excel`, `admin_overview`, `app_settings`, `backup`,
  `instructor_import`, `account_deletion`, `calendar_view`, `departments`.

### Known gaps

- The admin can't import an instructor's file on their behalf.
- Deleted accounts are never purged for good.
- Not yet tried in a browser on the real server: Import my subjects, the admin Accounts page, a
  real backup restore, the archive / drop-rule screens. The Excel file hasn't been opened in Excel.
- The demo subject with the offensive title should be renamed, and the instructor registration
  code `PROF-2026` changed, before the Pi goes live.
