# LSPU Attendance System: User Manual

This manual explains every feature of the attendance system, step by step. It has three parts,
one for each kind of user:

- **Part 1: Students**
- **Part 2: Instructors**
- **Part 3: Admin** (including the commands used on the device)

Read **Before you start** first: it applies to everyone.

---

## Before you start (everyone)

### What the system is

The attendance system is a small device (a Raspberry Pi in a case) with:

- a **card reader** under the "TAP YOUR ID" circle, where students tap their school ID,
- a **screen (LCD)** that shows the result of each tap,
- a **green and a red light** and a **buzzer** that confirm each tap,
- a **push button** for shutting the device down safely,
- its **own Wi-Fi network**, and a website that runs on the device itself.

The device does **not** need internet. Phones and laptops connect to the device's Wi-Fi and open
the website from there.

### How to connect

1. On your phone or laptop, open the Wi-Fi list and join the **device's Wi-Fi** (for example
   `LSPU-Attendance`). Ask your instructor or the admin for the name and password.
2. Turn off **mobile data** while you use the system. Some phones also need **Private DNS** turned
   off (Settings → Network → Private DNS → Off).
3. Open the browser and go to the address for your role:

| Who | Address |
|---|---|
| Students | **http://attendance.lan** |
| Instructors and the admin | **http://prof.attendance.lan** |

If the name doesn't open, check that mobile data and Private DNS are off. Students can also type
the number **http://10.42.0.1**. Instructors and the admin must use the name
(**prof.attendance.lan**), because the number opens the student site.

### Things every account has

| Feature | Where | What it does |
|---|---|---|
| **Theme** | Login pages (sun / moon button) and **Settings → Appearance** | Light, Dark, or System (follows your phone). |
| **Text size** | **Settings → Appearance** | Normal or Large (easier to read on small screens). |
| **Profile picture** | **My account**, the pencil on the picture | JPG, PNG or WEBP, up to 2 MB and 12 megapixels. Drag and zoom to crop, then **Save picture**. Click the picture to see it bigger; its **⋯** menu has **Download** and **Remove picture**. |
| **Change password** | **My account → Change password** | Type the current password, then the new one twice (at least 8 characters). |
| **Backup codes** | Downloaded as a PDF when you register; new ones in **My account → Backup codes** | Ten one-time codes. Each one lets you reset a forgotten password once. |
| **Log out** | The arrow button at the bottom of the side menu | Ends your session on this phone. |

Settings save by themselves the moment you change them ("Saved" appears briefly).

### Forgot your password?

1. On the login page, click **Forgot password?**
2. Type your **student number** (students) or **username** (instructors).
3. Type one **backup code** from your PDF (for example `K7QM-2XPA`). Each code works only once.
4. Type the new password twice (at least 8 characters) and click **Reset password**.
5. Log in with the new password.

**Lost the backup codes too?** Ask the admin for a one-time code (Part 3, *Backup codes*), then do
the steps above with that code. After logging in, make a new set of codes in **My account**.

### Device lights, sounds and screen

When a school ID is tapped on the device:

| Result | LCD shows | Buzzer | Light |
|---|---|---|---|
| Checked in on time | `CHECKED IN: PRESENT`, name, class, time | 1 short beep | Green, 2 seconds |
| Checked in late | `CHECKED IN: LATE`, name, class, time | 2 short beeps | Green blinks twice |
| Checked out | `CHECKED OUT`, name, class, time | 1 short beep | Green |
| Card registered | `CARD REGISTERED` | 3 short beeps | Green |
| Already checked in | `ALREADY CHECKED IN`, status and time | 1 long beep | Red, 2 seconds |
| Card not registered | `CARD NOT REGISTERED` and the card ID | 1 long beep | Red |
| No class open | `CHECK-IN CLOSED` | 1 long beep | Red |
| Already checked out / not checked in | `ALREADY CHECKED OUT` / `NOT CHECKED IN` | 1 long beep | Red |
| A card that belongs to someone else, tapped during a card registration | `ALREADY REGISTERED` and `Tap a different card` | 1 long beep | Red |

The result stays on the screen for a few seconds with a countdown, then the screen goes back to:

```
 ATTENDANCE SYSTEM  ▮      <- battery icon in the last column (⚡ when charging)
 Fri Nov 21  07:02 AM
                           <- "LOW BATTERY 9%" appears here when nearly empty
     TAP YOUR ID
```

Other screens: `LOADING · Please wait...` for about a minute after the device is switched on, and
the shutdown screens described in Part 3.

---

# Part 1: Students

## 1.1 Create your account

1. Join the device's Wi-Fi and open **http://attendance.lan**.
2. Click **Register as student**.
3. Fill in the form:

| Field | Rules |
|---|---|
| **Student number** | 4–20 letters, numbers or dashes, e.g. `2026-0001`. You log in with this. It can't be one that is already registered. |
| **First name, middle name, last name, suffix** | Use your **real name**. It is printed on your instructor's attendance reports. |
| **Course / Year / Section** | Pick all three (e.g. BSCPE, 4th, A). **Choose carefully**: after this, only the admin can change them. |
| **Email** | Optional. |
| **Password** | At least 8 characters, typed twice. |

4. Click **Create account**. A box reminds you about the backup codes; click **Create account** again.
5. **Step 2: Backup codes.** A PDF with 10 codes downloads by itself (check your Downloads
   folder; click **Didn't start? Download** if it didn't). Keep it safe. Tick **I saved my backup
   codes**, then click **Continue to signature**.
6. **Step 3: Signature.** Draw your signature with your finger or mouse above the line.
   **Preview** shows how it will look, **Clear** starts over. Click **Save signature**.
   This signature is printed on the attendance sheet whenever you tap your ID. You only do this
   once; to change it later you must ask the admin to reset it.

You land on your **Dashboard**. If no courses are set up yet, the course fields can be left
blank and filled in later in **My account**.

## 1.2 Register your school ID (once)

Your school ID has a chip inside. Register it once and then just tap it for every class.

1. Stand next to the attendance device.
2. Open **My account** (the side menu shows an **ID** badge until you've done this), or click the
   **School ID: Not set up** tile on the dashboard.
3. Click **Register card**, then **Start**. The reader is now reserved for you for **60 seconds**
   (the LCD shows `CARD REGISTRATION · Tap your card now`).
4. Tap your ID on the **TAP YOUR ID** circle.
5. The website shows **Card detected** with the card's ID. If it's your card, click
   **Yes, register it**. If not, click **No, tap again**. You have 60 seconds to confirm.
6. **Your card is registered.** The device beeps 3 times and shows `CARD REGISTERED`. Click **Done**.

Good to know:

- Only **one student can register at a time**. If the reader is busy, wait a moment.
- Classmates can still tap for attendance while you register.
- If you tap a card that belongs to someone else, the website and the LCD say
  **already registered**: tap your own card instead.
- **Lost or replaced your ID?** Ask the admin to remove the old one, then register the new one here.

## 1.3 Join a class

Your instructor gives you a **join code** (letters and numbers, e.g. `K7Q2MX`).

1. Open **My classes**.
2. Type the code in the **Join code** box.
3. Click **Join**.
   - If your course, year and section match the class, you are in right away.
   - If they don't match (or the instructor requires approval for everyone), you'll see a message.
     Click **Request approval** instead. The request appears under **Requests** as
     *Awaiting instructor approval*, then *Approved* or *Declined*.

While a request is pending or declined you are not in the class: you can't see it or check in.
Your instructor can also add you directly using your student number.

## 1.4 Check in

Check-in opens a few minutes before your class starts (your instructor sets the exact times).
There are two ways to check in:

**A. Tap your school ID on the device (fastest).**
Tap the ID on the **TAP YOUR ID** circle. Watch the LCD and listen for the beep (see the table in
*Before you start*). No signing is needed: your saved signature goes on the sheet.

**B. Check in on the website.**

1. Open your **Dashboard**. The big card at the top shows the check-in state:

| Card says | Meaning |
|---|---|
| **Open · Present** | Check in now and you are marked PRESENT. |
| **Open · Late** | The grace period is over; you will be marked LATE. |
| **Closed** | No class is open right now. |
| **Done** | You're checked in. |
| **Check-out open** | Your instructor opened check-out (see 1.5). |

2. If your instructor turned on a **check-in code**, a box appears: type the 4-digit code shown on
   the instructor's screen.
3. Click **Check in**. A pop-up asks you to **sign**. Draw your signature and click
   **Sign & check in**. (The website asks you to sign each time; the ID tap doesn't.)
4. A message confirms: *You're checked in as PRESENT* (or *LATE*).

The dashboard card refreshes by itself every few seconds, so you don't need to reload the page.

## 1.5 Check out (only when your instructor requires it)

Some classes require check-out so that students who leave early are caught. When the instructor
opens check-out near the end of class:

- **Tap your ID** on the device again (LCD: `CHECKED OUT`), **or**
- press **Check out** on your dashboard (no signature needed).

**If you checked in but don't check out, you are marked ABSENT** when the instructor ends the class.

## 1.6 Your dashboard

| Part | What it shows |
|---|---|
| Check-in card | See 1.4 and 1.5. |
| **Classes / Present / Late** tiles | Your totals across all classes. |
| **School ID** tile | *Registered* or *Not set up* (click it to register). |
| **Calendar** | Your class days for the month. Use the arrows to change month. Click a day to see that day's classes on the right, including **No class** days (holidays, suspended classes), **Make-up classes** and **Async activities**. |

## 1.7 My classes and your attendance record

**My classes** shows one tile per class with:

- subject code, title, section and instructor,
- the weekly schedule (day, time, room),
- your **attendance rate** bar and number of absences (green 90% and up, orange 80–89%, red below 80%),
- warnings when your instructor set an absence limit:
  *One more absence and you may be dropped* and *You reached N absences, the limit for this class.
  See your instructor.*
- **Semester ended** on classes that are finished.

Click a tile to open the class:

- **Schedule** of the class,
- **Attendance rate** (Excused counts as present in your rate),
- totals for **Present, Late, Absent, Excused**,
- **Attendance history**: every class date, its type (Regular / Make-up / Async), your status, and
  how you checked in (time and **ID** or **Web**; *Set by instructor*; *Async activity*;
  *No class · credited present*). Future dates show **Upcoming**.

**Leave class:** the **Leave class** button (on the tile or the class page). Your records are kept.
Joining again follows the instructor's approval setting.

## 1.8 My account

| Section | What you can do |
|---|---|
| **Attendance ID** | See your registered ID (shown masked) and your signature. Register an ID if you have none. |
| **Profile** | Your name and student number are **locked** (only the admin can correct them, because they appear on official reports). Course / Year / Section can be set once if empty, then they lock. You can edit **Mobile number** (`09XXXXXXXXX` or `+639XXXXXXXXX`) and **Email**, then **Save profile**. |
| **Change password** | See *Before you start*. |
| **Delete my account** | Logs you out and removes you from your classes' lists. Type your password and tick **Yes, delete my account**. Only the admin can bring it back. |

---

# Part 2: Instructors

## 2.1 Create your instructor account

1. Join the device's Wi-Fi and open **http://prof.attendance.lan**.
2. Click **Register as instructor**.
3. Fill in:
   - **Instructor registration code**: get it from the admin.
   - **Username**: 3–30 letters, numbers, dots, dashes or underscores (e.g. `jdelacruz`). You log in with it.
   - First, middle, last name and suffix; email (optional); password (at least 8 characters, twice).
4. Click **Create instructor account**. A PDF with **10 backup codes** downloads: keep it safe,
   tick **I saved my backup codes**, click **Continue**.

Then open **My account** and set:

- **Title** (e.g. Engr., Prof.), which appears before your name,
- **Department** (pick your college from the list), printed on the report's **College of** line,
- **Signature** (optional), which can be printed on the reports you download,
- mobile number and email.

## 2.2 The main screens

| Menu | What's there |
|---|---|
| **My subjects** | Your subjects, today's classes, and the totals (subjects, sections, students, classes today). |
| **My account** | Profile, signature, backup codes, delete account. |
| **Archive** | Subjects whose semester you ended. |
| **Recently deleted** | Deleted items you can restore for 30 days. |
| **Settings** | Appearance, report signature default, device battery and clock, backup of your subjects. |

**Today's classes** (on My subjects) is split into **Current** (check-in open),
**Upcoming**, and **Ended**, each with a status chip and the number checked in. Click a class to manage it.

The **device battery** shows in the side menu and the top bar. A yellow bar appears at the top
when the battery is low (20% or less, not charging) or when the device's clock is wrong (see 2.12).

## 2.3 Subjects

**Create a subject:** **My subjects → New subject**, then fill in:

| Field | Example |
|---|---|
| Subject code | `CPE 412` |
| Subject title | `Embedded Systems` |
| School year | `2026-2027` |
| Semester | 1st, 2nd or Summer |
| Units | `3` (optional) |
| Description | optional |

Click **Save subject**. The subject page opens.

On the subject page:

- **Edit** changes the fields above.
- **Add section** (see 2.4).
- **End semester** (see 2.11).
- **Delete** moves the subject and all its sections to **Recently deleted** (restorable for 30 days).

## 2.4 Sections

**Add a section:** on the subject page click **Add section**, choose **Course**, **Year** (1st–5th)
and **Section** (A–G). The name is built from them, e.g. **BSCPE 4A**. Click **Add section**.

The section list shows students, weekly schedule, sessions and the **join code**. Click a row to open it.

At the top of a section page:

- **Join code** with **Copy** and **New code**. A new code makes the old one stop working.
- **Attendance report** (see 2.10).
- Four tabs: **Attendance**, **Students**, **Schedule**, **Settings**.

## 2.5 Section settings (Settings tab)

| Setting | Choices / meaning |
|---|---|
| **Section** | Change the course, year and section letters. |
| **Approval to join** | **Students with different course, year, or section** (default): matching students join directly, others need approval. **Every student**: everyone needs approval. **Anyone with the code**: anyone joins directly. |
| **Default check-in mode** | **Automatic** or **Manual** for classes made from the weekly schedule. |
| **Opens before / Grace period / Closes after** | Minutes relative to the class start. Defaults: **5 / 15 / 30**. Example under the fields: *For a 7:00 class: opens 6:55, late from 7:15, closes 7:30.* |
| **Drop rule: absences** | 0–30. A student with this many absences is flagged **For dropping** in the Students tab; one absence before it, **1 absence left**. 0 = no rule. You decide whether to drop. |

Click **Save settings**.

Below: **End of semester** (2.11) and **Danger zone → Delete this section** (restorable for 30 days).

## 2.6 Students (Students tab)

**Students join themselves** with the join code (1.3). You can also click **Add students** and
paste student numbers (one per line; commas and spaces also work), then **Add to BSCPE 4A**.
Students must have created their account first.

**Requests** (left panel): students who need approval, with their course/section and reason.
Click **Approve** or **Decline**.

The student list shows for each student:

| Column | Meaning |
|---|---|
| Student / Student no. | Name, course-year-section, number. |
| **P · L · A** | Present · Late · Absent totals, plus **For dropping** / **1 absence left** chips (drop rule). |
| **ID** | **Yes** if the student registered a school ID, **None** if not. |
| **Signature** | **Signed**, **Missing**, or **Re-sign** (the admin reset it). |

The **⋯** menu on each row:

- **View student**: attendance rate, totals, contact details, ID and signature status, and the
  student's other classes with you. Corrections to names, IDs or signatures are done by the admin.
- **Drop from class**: removes the student from the list; records are kept. Dropped students
  (and students who left by themselves) are listed under the **Dropped** button, where
  **Put back** returns them.
- **Remove from section**: goes to **Recently deleted** (restorable for 30 days).

## 2.7 Weekly schedule, no-class days and vacation (Schedule tab)

**Add schedule:** pick the **Day**, **Start** and **End** time and **Room** (optional).
Click **Add more schedule** to add more days at once (up to 7); each new row copies the previous
one with the next day (handy for MWF / TTh). Click **Add schedule**.
Classes are then **created automatically** on those days.

Each schedule row's **⋯** menu: **Edit schedule** or **Remove schedule** (past classes stay in the
report; restorable for 30 days).

**Mark no class** (one date): pick the **Date** and a **Reason** (quick buttons: Holiday,
Suspended classes, No class).
- Tick **Still mark everyone present for this date** if the class shouldn't count as missed
  (e.g. covered on another day). A column is added with everyone present.
- Tick **Mark it on all my sections too** to apply it to all your sections.

**Vacation** (a date range): pick **Start date** and **End date** (both included), an optional
reason, and optionally **Apply to all my sections**. Classes in the range are skipped without
absences. A range that already has opened classes or attendance is refused.

No-class and vacation dates are listed under **No class days**; **Remove** brings the class back.

## 2.8 The Attendance tab

Three lists:

1. **Open now** (only while check-in is open): date, time, type, status, how many checked in,
   **Manage**.
2. **Upcoming** (foldable): the next 4 classes (**Show all upcoming** for more), from the weekly
   schedule and the ones you added. The first one says **Next · today / tomorrow / in N days**.
   Each weekly class day has a menu to mark it:
   - **Holiday / Suspended classes / No class**,
   - **Async activity…** (see below),
   - **Regular** to undo a mark.
3. **Ended** (foldable): finished classes, newest first, with Present / Late / Absent counts.
   Each one is a date column in the report.

**Add session** (for a class that isn't on the weekly schedule):

| Field | Choices |
|---|---|
| Type | **Regular**, **Make-up**, or **Async** |
| Check-in mode | **Automatic** (follows the start time and the section's window) or **Manual** |
| Date, Start, End (optional) | For Async: **Completion time (optional)** instead |
| Title | Optional (required for Async), e.g. *Module 3 quiz* |

**Async activity** = a class done outside the room (homework, online). Nobody checks in:
every enrolled student is marked **Present** automatically. Mark individual students **A** (didn't
submit) or **E** (excused) afterwards. It moves to Ended at the completion time (or on the date).
To turn an async activity back into a normal class, use its menu: **Regular (undo async)**.

## 2.9 Running a class (the session page)

Open a class from **Today's classes** or the Attendance tab.

**Top bar:** the status chip (**Not open yet**, **Open · Present**, **Open · Late**,
**Check-out open**, **Closed**), the automatic times (*Auto: opens 6:55, late 7:15, closes 7:30*),
and two dropdowns:

- **No check-out / Check-out required**,
- **Automatic / Manual** check-in mode. Manual only if the class has no start time. Locked after the class ends.

**Count tiles:** **Present**, **Late**, **Not yet** (becomes **Absent** after the class ends),
**Excused**. Click a tile to see the list of those students.

**Roster:** every enrolled student. Status shows **—** until they check in (and stays — for
students who joined the section after that class). **Time** shows check-in time and check-out
time (**Out 9:58 AM**), or **No check-out** / **Not out yet**. **How**: **ID**, **Web**, **By you**,
**Auto (async)** or **No class (credited)**.

**Mark by hand:** the **P · L · A · E** buttons (Present, Late, Absent, Excused) and **×** (clear).
They save instantly. The page refreshes by itself every 3 seconds.

**Check-in control** (side card):

| Button | Does |
|---|---|
| **Open now · Present** | Opens check-in immediately; students are marked Present. |
| **Open now · Late** | Opens check-in; students are marked Late. |
| **Switch to Late** / **Back to Present** | Changes what new check-ins get. |
| **Open check-out** | Only with *Check-out required*. Closes check-in; students tap again or press Check out. |
| **Back to check-in** | Leaves check-out and reopens check-in. |
| **End session** | Closes check-in. Students with no record become **Absent**; with check-out required, students who didn't check out also become **Absent**. |
| **Reopen check-in** | After the end, opens it again (Present). |
| **Async activity…** | Turns this class into an async activity: check-in closes and students without a record are marked Present. |

In **Automatic** mode you don't need to press anything: check-in opens, turns Late and closes by
itself at the section's times. **Manual** mode waits for your buttons.

**Web check-in code** (side card): **Require a code** shows a 4-digit code that students must type
to check in on the web (stops students who aren't in the room). **New code** changes it;
**Turn off** removes it. ID taps never need the code.

**Remove this column → Delete session**: for a class added by mistake. It disappears from reports
and goes to **Recently deleted** (30 days).

### Check-out, step by step

1. Before or during class, set the dropdown to **Check-out required**.
2. Near the end of class, press **Open check-out** and confirm.
3. Students tap their ID again or press **Check out**. The control shows *N of M out*.
4. Press **End session**. Anyone who checked in but didn't check out becomes **Absent**
   (shown as **No check-out** in the roster).

## 2.10 Attendance report (PDF)

On the section page click **Attendance report**:

- **Class dates**: tick the ended or current classes to include (some are ticked for you).
  Upcoming classes can't be included. The counter shows how many are picked.
- **Async activities (extra columns)**: tick the async activities to include.
- **Blank dates**: **Add date** adds an empty column for a date, with every student's name, to
  print the sheet and have students sign by hand (up to 12). This works even before any class.
- **Print options**:
  - **Department**: print your department on the **College of** line (centered and underlined,
    e.g. *College of Engineering*). Leave it off to keep the line blank.
  - **Instructor signature**: put your saved signature in the **Prof./Instructor's Signature** row.
    The default comes from **Settings → Attendance reports**.
- The bottom line tells you how many pages the PDF will have (6 dates and 40 students per sheet).
- **Preview** opens it in a new tab; **Download PDF** saves it.

What each student's cell shows:

| Status | Cell |
|---|---|
| Present | The student's signature (the one drawn at web check-in, or the stored one for an ID tap); **P** if there is no signature |
| Late | The signature with a small **L**; **L** if there is no signature |
| Absent | **A** (and the absences are counted in the last columns) |
| Excused | **E** |
| Blank date / not enrolled yet | Empty |

## 2.11 End of semester, archive and new term

- **End semester** (subject page or section Settings): all sections stop creating classes and the
  subject moves to **Archive**. Records and reports are kept; nothing can be changed.
- In **Archive**: **Open**, **Reopen** (schedules start creating classes again), or
  **Copy to new term**: choose the new **School year** and **Semester**. It creates a new subject
  with the same sections and schedules, with new join codes. No students are copied; they join again.

## 2.12 Settings

| Setting | What it does |
|---|---|
| **Theme / Text size** | See *Before you start*. |
| **Instructor signature on reports** | Default for the report's signature row: **Leave blank** or **Put my signature** (needs a saved signature). |
| **Attendance device → Battery** | Percentage and state: *Charging from USB-C*, *Running on battery*, *Low* (20%), *Almost empty* (10%). The device shuts down by itself at 5% if not charging. |
| **Device clock / This phone** | The device's time compared with your phone. **Sync time from this phone** sets the device's clock (and its clock module) from your phone. Make sure your phone's time is right first. The device also corrects itself from an instructor's phone when it is more than a minute off, except while a class is taking check-ins. |
| **Backup of my subjects** | **Download backup** (type your password): one .zip with only your subjects, sections, schedules, attendance and your students' signatures. Do it weekly; keep it off the device. |
| **Import my subjects** | For a rebuilt device: choose your backup .zip and type your password. It only adds what is missing. Students who register again with the same student number get their classes and history back. |

## 2.13 Recently deleted

Deleted subjects, sections, schedules, sessions and removed students stay here for **30 days**.
Each row shows when it will be deleted for good. **Restore** brings it back; **⋯ → Delete forever**
removes it immediately (cannot be undone).

## 2.14 Delete my account

**My account → Delete my account**: type your password and tick the box. Your subjects are
archived and you can't log in. The admin can restore it.

---

# Part 3: Admin

The admin manages accounts, the course and department lists, backups and the device.
There is **no admin registration page**: the admin account is made with a command on the device
(3.9). The admin logs in at **http://prof.attendance.lan** and lands on **Overview**.

## 3.1 Overview

| Card | Shows |
|---|---|
| Tiles | Students, Instructors, Active subjects, Sections, Classes today, Check-ins today. |
| **Storage** | Used / free space, database size, signatures size. A warning appears when space is low. |
| **Keep check-in signatures for** | Days (7–730, default 60) that signatures drawn at web check-in are kept before they are deleted to save space. Records stay; the sheet then uses the student's stored signature. Click **Save**. |
| **Attendance device** | Battery %, charging, device time, card taps today. |
| **Needs attention** | Backup overdue, students without an ID or signature, instructors without a department. **Open** lists who. |
| **Recent activity** | The latest actions (who did what, when). |

## 3.2 Courses

The list of courses students and sections can pick (e.g. BSCPE, BSECE).

- **Add course**: type the abbreviation (up to 10 characters) and click **Add course**.
- **⋯ → Disable / Enable**: a disabled course can't be picked anymore, but stays on existing
  profiles and reports.
- **⋯ → Remove**: removes it from the list; existing profiles and classes keep their saved labels.

## 3.3 Departments

The colleges instructors pick in **My account → Department** (printed on reports).
**Add**, **Enable / Disable** and **Remove** work the same as courses.

## 3.4 Student support

Search by name or student number, then open a student to:

- **Correct student information**: student number, name, suffix, mobile, email, course / year / section.
  Click **Save corrections**. (Students can't change these themselves.)
- **Remove card**: unlinks a lost or replaced school ID so the student can register a new one.
- **Reset signature**: lets the student draw a new signature (they are asked at their next login).
- **Merge an imported record**: after a device rebuild, an instructor's import brings back
  students who haven't registered yet. If a student registered with a **mistyped** student
  number, choose their imported record and click **Merge into this account**: their classes and
  attendance move over and the account takes the correct number. Cannot be undone.

## 3.5 Accounts

- Search, then **Delete** a student or instructor account. They can't log in anymore;
  an instructor's subjects are archived.
- **Deleted accounts** lists accounts deleted by you or by their owner, with who deleted them:
  - **Restore** brings the account back with its history.
  - **Delete Permanently** erases it: for a student, their attendance history, classes and
    signatures; for an instructor, all their subjects, sections and records. Cannot be undone.

## 3.6 Backup codes (help desk)

When a student or instructor lost their backup codes:

1. Open **Backup codes**, search for the person (the list shows how many codes they have left).
2. **Make sure it is really them asking**, then click **Issue a code** and confirm.
3. Give them the one code shown (it is shown only once), then click **Done**.
4. They use it on **Forgot password?**, log in, and make a new set in **My account**.

## 3.7 Backup & restore

- **Download backup**: one .zip with all accounts, classes, attendance, settings, profile
  pictures and signatures. Do it **every week** and keep it on a laptop or flash drive.
  The page and the Overview remind you after 7 days.
- **Restore from a backup**: choose the .zip, type your admin password, tick
  **I understand this replaces all current data**, click **Restore**.
  Everything on the device is replaced; anything recorded after that backup is lost.
  The current data is saved first as a safety copy in `data/backups` (listed on the page).
  Everyone must log in again.

## 3.8 Device settings

- **Instructor registration code**: **Show** reveals the current code. Type a new one
  (4–30 letters, numbers, dots, dashes or underscores) and click **Save**. Existing instructors
  aren't affected.
- **Device Wi-Fi (hotspot)**: type the **New Wi-Fi name**, the **New Wi-Fi password** twice
  (8–63 characters) and **your admin password**, then **Change Wi-Fi**.
  **Every phone is disconnected** after about 5 seconds and must join the new Wi-Fi.
  Tell instructors and students the new details. (First time only, the device needs the setup
  command `bash deploy/device-settings.sh`; the page says so if it's missing.)

## 3.9 Switching the device on and off

| To | Do |
|---|---|
| **Turn on** | Flip the UPS switch ON. The LCD shows `LOADING`, then `TAP YOUR ID` after about a minute. |
| **Turn off** | **Hold** the push button: the LCD counts `3s 2s 1s` (let go = cancel). Then it asks `SHUT DOWN? Press the button again to confirm` with a 10-second countdown. **Press again**: `SHUTTING DOWN · Do NOT switch off yet`. After 10–20 s: `Safe to switch OFF` → flip the UPS switch off. If you don't press again, it cancels. |
| **Charge** | Plug a 5V 3A USB-C adapter into the UPS port at the back. The battery icon shows ⚡. |
| **Low battery** | At 20% the buzzer beeps (beep-beep-beep) every 2 minutes, at 10% every 30 seconds, and the LCD shows `LOW BATTERY`. At 5% (not charging) the device shuts itself down safely. |

Never flip the switch off while the device is running (it can damage the SD card): always use the
button first.

## 3.10 Commands on the device

These are typed in a terminal on a laptop. They need the laptop connected to the device.

### Connect to the device

1. Join the device's Wi-Fi (or plug in the Ethernet cable).
2. Open **PowerShell** on the laptop and type:

| Connection | Command |
|---|---|
| Device Wi-Fi | `ssh attendance@10.42.0.1` |
| Ethernet cable | `ssh attendance@192.168.137.2` |

3. Type the device's password. Then go to the project folder:

```bash
cd ~/AttendanceSystem
```

Commands that change accounts need the device settings loaded first. This prefix does it:
`set -a && . /etc/attendance.env && set +a &&` (it's included below).

### Admin accounts

| Task | Command |
|---|---|
| **Create an admin** (asks for first name, last name, password) | `set -a && . /etc/attendance.env && set +a && venv/bin/flask --app run create-admin <username>` |
| **Forgot the admin username** (lists admin accounts) | `set -a && . /etc/attendance.env && set +a && venv/bin/flask --app run list-admins` |
| **Forgot a password and the backup codes** (prints a temporary password; works for any account) | `set -a && . /etc/attendance.env && set +a && venv/bin/flask --app run reset-password <username>` |

### Checking and fixing the device

| Task | Command |
|---|---|
| **Check all hardware** (LCD, card reader, UPS, battery, clock, buzzer, lights, button, power) | `bash deploy/check-hardware.sh` |
| Same, without the parts that need you (tap, button) | `bash deploy/check-hardware.sh --quick` |
| Are the website and the reader running? | `sudo systemctl status attendance-web attendance-rfid` |
| Restart them (after an update or a settings change) | `sudo systemctl restart attendance-web attendance-rfid` |
| Watch the card reader live (Ctrl+C to stop) | `journalctl -u attendance-rfid -f` |
| Website log (last lines) | `tail -n 50 logs/app.log` |
| Reader / LCD log | `tail -n 50 logs/hardware.log` |
| Which modules answer on I2C (LCD `27`, clock `UU`/`68`, UPS `43`) | `i2cdetect -y 1` |
| Power problems? (`0x0` = fine) | `vcgencmd get_throttled` |
| Set the device clock from its own time into the clock module | `sudo /usr/local/sbin/attendance-set-time $(date +%s)` |
| Show the device time | `date` |

### Settings and setup

| Task | Command |
|---|---|
| Edit the device settings (battery, LCD address, button, etc.) | `sudo nano /etc/attendance.env` (Ctrl+O, Enter to save, Ctrl+X to exit; then restart the services) |
| Turn on the device's own Wi-Fi / change it from the terminal | `bash deploy/hotspot.sh "Wi-Fi name" "password"` |
| Allow Wi-Fi changes from the admin website (once) | `bash deploy/device-settings.sh` |
| Set up the clock module (once, then reboot) | `bash deploy/rtc-setup.sh` |
| Set up the shutdown button and LCD screens (once, then reboot) | `bash deploy/power-button.sh` |
| Full install on a new SD card | `bash deploy/install.sh` |

### Power

| Task | Command |
|---|---|
| Shut down safely | `sudo poweroff` |
| Restart | `sudo reboot` |

### Backups and updates (typed on the laptop, not on the device)

Copy all the device's data to the laptop (a second backup):

```powershell
scp -r attendance@10.42.0.1:~/AttendanceSystem/data ./attendance-backup
```

Send an updated version of the system from the laptop (in the project folder). **Never copy the
`data` folder to the device**: it would replace the real records.

```powershell
scp -r app hardware deploy docs tools config.py run.py requirements.txt requirements-pi.txt CHANGELOG.md README.md attendance@10.42.0.1:~/AttendanceSystem/
```

Then on the device: `sudo systemctl restart attendance-web attendance-rfid`.

### Commands to never run on the device

| Command | Why |
|---|---|
| `flask --app run seed-demo` | **Deletes the whole database** and fills it with demo data. For laptop testing only. |
| `flask --app run init-db --reset` | **Deletes the whole database.** |

## 3.11 Troubleshooting

| Problem | What to do |
|---|---|
| Website doesn't open | Phone really on the device's Wi-Fi? Mobile data and Private DNS off? Try `http://10.42.0.1`. Then `sudo systemctl status attendance-web`. |
| Taps do nothing | `bash deploy/check-hardware.sh`; look at `logs/hardware.log`. |
| LCD lit but blank | Turn the small contrast screw on the back of the LCD; run the hardware check. |
| LCD or clock stopped answering | A loose or wrong wire on SDA/SCL blocks all of them; check `i2cdetect -y 1` and the wiring. |
| Times are wrong | Instructor: **Settings → Sync time from this phone**. If it keeps happening, the clock module's battery is empty or it isn't set up (`bash deploy/rtc-setup.sh`). |
| Battery not shown on the website | Check the battery settings in `/etc/attendance.env` (e.g. `BATTERY_BACKEND=ina219`, `BATTERY_I2C_ADDRESS=0x43`), then restart the services. |
| A student lost their ID | **Student support → Remove card**; the student registers the new one. |
| Someone is locked out | **Backup codes → Issue a code**. Admin locked out: `reset-password` command. |
| Random freezes or lightning icon | Battery low or adapter too weak: charge, use a 5V 3A adapter (`vcgencmd get_throttled`). |
