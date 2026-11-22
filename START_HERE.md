# Start here: run the Attendance System on your laptop

For groupmates who received this folder as a zip. No Raspberry Pi needed: the laptop
simulates the RFID reader and LCD. Windows steps below; Mac/Linux at the end.

## 1. Install Python (once)

1. Download **Python 3.12 or newer** from https://www.python.org/downloads/
2. On the first installer screen, tick **"Add python.exe to PATH"**, then **Install Now**.

## 2. Unzip the folder

Right-click the zip → **Extract All**. Put it somewhere simple, e.g. `Desktop\AttendanceSystem`.
Don't run it from inside the zip.

## 3. Set up (once, needs internet)

Double-click **`setup.bat`**. It creates the Python environment, installs the packages and loads
demo data. Wait for "Setup finished", then press any key.

## 4. Run it

Double-click **`start.bat`** and keep that window open. Then open in your browser:

| Who | Address | Demo login |
|---|---|---|
| Student | http://localhost:5000 | `2026-0001` / `student123` |
| Instructor | http://prof.localhost:5000 | `prof` / `prof123` |
| Dev test page (fake card taps, LCD) | http://localhost:5000/dev | — |

More demo students are listed at the top of `app\seed.py`. To make a new instructor account,
the registration code is `PROF-2026`.

To stop the system, close the `start.bat` window.

## 5. Open it from your phone (optional)

1. Phone and laptop on the **same Wi-Fi** (a phone hotspot works).
2. The `start.bat` window shows a line like `Running on http://192.168.1.23:5000`. Open that
   address on the phone for the **student** site.
3. For the **instructor** site on the phone, open `http://prof.192.168.1.23.nip.io:5000`
   (same numbers, needs mobile data or internet on that Wi-Fi to look the name up).
4. If Windows asks whether Python may use the network, click **Allow**.

## Useful extras

| Want to | Do this |
|---|---|
| Reset to fresh demo data | Delete `data\attendance.db`, then run `setup.bat` again |
| Run the automated tests | In the folder's address bar type `cmd`, Enter, then `venv\Scripts\python -m pytest` |
| Run the load test (many users at once) | `venv\Scripts\python tools\load_test.py` |
| Read how the system works | The user manual your groupmate shared, and `README.md` |
| Set up the real Raspberry Pi | `docs\PI_SETUP.md` |

## If something goes wrong

| Problem | Fix |
|---|---|
| "Python is not installed" | Install it (step 1) with **Add python.exe to PATH** ticked, then run `setup.bat` again |
| Package install failed | Check the internet connection and run `setup.bat` again |
| `start.bat` says "Address already in use" | The system is already running in another window; close it first |
| Phone can't open the page | Same Wi-Fi? Windows Firewall: allow Python on private networks |
| Report's university name isn't Old English font | `app\static\fonts\OLDENGL.TTF` is missing from the zip; ask for it |
| Something else | Look at `logs\app.log` and send the last lines to the team |

## Mac / Linux

```
cd AttendanceSystem
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/flask --app run seed-demo
venv/bin/python run.py
```
