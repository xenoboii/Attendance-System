# Raspberry Pi setup: from the laptop to a working device

Follow the parts in order. Part A–C need **internet** (home Wi-Fi). Part F turns the Pi
into its own Wi-Fi network, after which it no longer uses your home Wi-Fi.

| Part | What | Time |
|---|---|---|
| A | Prepare the SD card | 15 min |
| B | Copy the system from the laptop to the Pi | 5 min |
| C | Install (one script) | 15–25 min |
| D | Wire the hardware (pin map) | 30 min |
| E | Test each part | 10 min |
| F | Turn on the Pi's own Wi-Fi | 5 min |
| G | Daily use, updates, backups | — |

---

## What you need

| Item | Notes |
|---|---|
| Raspberry Pi 4 (2 GB+) or Pi 5 | |
| microSD card, 16 GB+ (you have 128 GB) | |
| 18650 UPS board for Raspberry Pi | Holds 2 cells, charges them from USB-C, gives 5V 3A, runs the Pi while charging |
| 2 × 18650 cells | Protected, known brand, ≈ 3,000 mAh each (about 3–4 hours on battery) |
| USB-C adapter, 5V 3A | Official 15W Pi supply or similar; plugs into the UPS board |
| Rocker power switch + short USB-C cable | Between the UPS board's 5V output and the Pi's USB-C port |
| Pi 4 cooler (heatsink + small fan) | Clips onto the Pi; fan wires to pins 2 (5V) and 30 (GND) |
| RC522 RFID module + cards/tags | 13.56 MHz (MIFARE) |
| 20x4 LCD with **I2C backpack** (PCF8574) | Usually address 0x27 (sometimes 0x3F) |
| Active buzzer (3.3–5V) | "Active" = beeps by itself when powered |
| 1 green LED + 1 red LED | 5 mm |
| 2 × resistors 220–330 Ω | One per LED, **required** (protects the LED and the Pi) |
| Jumper wires (female–female / female–male), breadboard | |
| DS3231 RTC module (Pi 4) or RTC battery (Pi 5) | Keeps the correct **time** with no internet. Attendance depends on the clock. (Backup: "Sync time from this phone" on the prof site.) |
| A laptop on the same Wi-Fi | For setup over SSH |

---

## A. Prepare the SD card (on the laptop)

1. Install **Raspberry Pi Imager** (raspberrypi.com/software).
2. Choose device → your Pi model. OS → **Raspberry Pi OS Lite (64-bit)**. Storage → the SD card.
3. Click **Edit settings** (the gear) and set:
   - Hostname: `attendance`
   - Username / password: e.g. `admin` / a password you'll remember
   - Wi-Fi: your **home** Wi-Fi name and password, country **PH**
   - Time zone: **Asia/Manila**
   - Services tab: **Enable SSH** (password authentication)
4. Write, put the card in the Pi, power it on, wait ~2 minutes.
5. From the laptop (PowerShell), connect:
   ```bash
   ssh admin@attendance.local
   ```
   (If `attendance.local` isn't found, look up the Pi's IP in your router/phone hotspot list and use `ssh admin@<ip>`.)

## B. Copy the system from the laptop to the Pi

In **PowerShell on the laptop**, in the folder that **contains** `AttendanceSystem`
(your Desktop):

```bash
tar --exclude=venv --exclude=data --exclude=logs --exclude=__pycache__ --exclude=.pytest_cache -czf attendance.tgz AttendanceSystem
```

```bash
scp attendance.tgz admin@attendance.local:~
```

Then **on the Pi** (the SSH window):

```bash
tar -xzf attendance.tgz && cd AttendanceSystem
```

This brings the code **plus** the report font and LSPU seal (`app/static/fonts/OLDENGL.TTF`,
`app/static/img/school_logo.png`). It does **not** copy your laptop's test database, so the Pi starts empty and clean.
To bring your test data along anyway, also copy `data/attendance.db` and `data/signatures/`.

> If a script later fails with `$'\r': command not found`, run `sed -i 's/\r$//' deploy/*.sh` once (Windows line endings).

## C. Install (one script)

On the Pi, inside `~/AttendanceSystem`:

```bash
bash deploy/install.sh
```

It installs everything, turns on SPI + I2C, creates the database, and adds two services that
**start automatically on every boot**:

| Service | What it runs |
|---|---|
| `attendance-web` | the website on port 80 (waitress, 4 threads) |
| `attendance-rfid` | RFID reader + LCD + buzzer + LEDs |

Then edit the settings (at least the instructor registration code):

```bash
sudo nano /etc/attendance.env
```

Restart the website so it picks up the new settings:

```bash
sudo systemctl restart attendance-web attendance-rfid
```

**Create the admin account** (you choose the username; it asks for a name and a password):

```bash
cd ~/AttendanceSystem && set -a && . /etc/attendance.env && set +a && venv/bin/flask --app run create-admin yourusername
```

The admin logs in at the **instructor** address. There is no admin registration page on the
website; this command is the only way to make one.

Check the website from the laptop before going further: open `http://attendance.local` (student
login) and `http://prof.attendance.local`. If the second name doesn't open yet, that's fine: it
works once the Pi's own Wi-Fi is on (part F).

Then shut down **before wiring**:

```bash
sudo poweroff
```

## D. Wire the hardware: pin map

**Always wire with the power unplugged.** GPIO numbers below are **BCM** numbers (what the code
uses); "Pin" is the physical pin number on the 40-pin header (pin 1 is the corner pin nearest the
SD card side, marked with a square pad).

**Full picture:** [wiring-diagram.png](wiring-diagram.png) (or `wiring-diagram.svg`, made by
`python tools/make_wiring_diagram.py`). The tables below match it. Any GND pin works for any
ground wire; these are just the ones the diagram uses.

```
   RTC VCC          3V3  (1) (2)  5V        -> 5V rail: converter HV, LCD VCC, buzzer +
   RTC SDA    GPIO2 SDA  (3) (4)  5V        <- fan +
   RTC SCL    GPIO3 SCL  (5) (6)  GND       <- fan -
                  GPIO4  (7) (8)  GPIO14
   RTC GND          GND  (9) (10) GPIO15
                 GPIO17 (11) (12) GPIO18    <- BUZZER (via 1 kΩ + transistor, or straight to +)
                 GPIO27 (13) (14) GND       <- transistor emitter (or buzzer -)
                 GPIO22 (15) (16) GPIO23    <- GREEN LED (through 330 Ω)
  RC522 3.3V        3V3 (17) (18) GPIO24    <- RED LED (through 330 Ω)
  RC522 MOSI     GPIO10 (19) (20) GND       <- both LEDs' short legs
  RC522 MISO      GPIO9 (21) (22) GPIO25    <- RC522 RST
  RC522 SCK      GPIO11 (23) (24) GPIO8     <- RC522 SDA (chip select)
  RC522 GND         GND (25) (26) GPIO7
   ...
  BUTTON         GPIO26 (37) (38) GPIO20
  BUTTON            GND (39) (40) GPIO21
```

The LCD reaches SDA/SCL through the RTC's pass-through pins and the level converter (see the
LCD section); the UPS HAT (D) touches pins 2/4, 6, 3 and 5 from underneath with its spring pins.

### RC522 RFID reader (SPI) — 3.3V ONLY

| RC522 pin | Pi pin | GPIO |
|---|---|---|
| SDA (SS) | 24 | GPIO8 (CE0) |
| SCK | 23 | GPIO11 |
| MOSI | 19 | GPIO10 |
| MISO | 21 | GPIO9 |
| IRQ | — | not connected |
| GND | 25 | GND (any GND pin) |
| RST | 22 | GPIO25 |
| 3.3V | 17 (or 1) | 3.3V — **never 5V, it will burn the module** |

### 20x4 LCD with I2C backpack

| LCD backpack | Pi pin | GPIO |
|---|---|---|
| GND | 9 | GND |
| VCC | 2 | 5V |
| SDA | 3 | GPIO2 (SDA) |
| SCL | 5 | GPIO3 (SCL) |

(This is the direct wiring without a converter. With the converter, use the table below.)

Turn the small blue screw (contrast) on the backpack if the screen is lit but shows nothing.

**With a logic level converter (recommended).** The LCD runs on 5V and its backpack pulls SDA/SCL
up to 5V, but the Pi's pins are 3.3V only. It usually "works" wired straight, but slowly stresses
the Pi. A **4-channel I2C logic level converter (BSS138)** sits between them and translates:

| Converter pin | Goes to |
|---|---|
| **LV** | 3.3V: the RTC's pass-through **VCC** (= Pi pin 1) |
| **GND** (LV side) | the RTC's pass-through **GND** (= Pi pin 9) |
| **LV1** | the RTC's pass-through **SDA** (= Pi pin 3) |
| **LV2** | the RTC's pass-through **SCL** (= Pi pin 5) |
| **HV** | **5V rail** (Pi pin 2) |
| **HV1** | LCD **SDA** |
| **HV2** | LCD **SCL** |
| **GND** (HV side) | LCD **GND** |

LCD **VCC → 5V rail** (pin 2). The 5V rail is one wire from pin 2 split three ways (converter
HV, LCD VCC, buzzer +) with Y-wires or a perfboard strip. Without an RTC, wire LV/GND/LV1/LV2
straight to pins 1/9/3/5.
Only the LCD goes on the HV (5V) side; the UPS board and RTC stay on the Pi's own SDA/SCL (3.3V).
Nothing changes in the software: `i2cdetect -y 1` still shows the LCD at `27`.

### Buzzer (active)

Use an **active** buzzer, e.g. a **3–24V active piezo buzzer** (the large 4216 type, "continuous"
sound, not "intermittent"). Active = beeps by itself when powered; the code just turns the pin on
and off. A **passive** buzzer or bare piezo disc only clicks. To tell them apart: active has a
sealed black bottom (often a "remove seal" sticker); passive shows a green board. Or touch + to
3.3V and − to GND: a steady beep = active.

**Simple (quieter):** straight on the pin. The pin gives only 3.3V, the bottom of a 3–24V
buzzer's range, so it is quiet. Try this first.

| Buzzer | Pi pin | GPIO |
|---|---|---|
| + (red wire / longer leg) | 12 | GPIO18 |
| − (black) | 14 | GND |

**Louder (recommended for a classroom):** let a transistor switch the buzzer on **5V**.
Parts: 1 × NPN transistor (S8050 or 2N2222), 1 × 1 kΩ resistor.

```
Pi pin 12 (GPIO18) ── 1 kΩ ── transistor BASE
                              transistor EMITTER ── GND (pin 14)
Pi pin 2 (5V) ── buzzer RED (+)
                 buzzer BLACK (−) ── transistor COLLECTOR
```

Check E-B-C leg order in your transistor's datasheet (it differs by brand). No code change.

**No-solder option:** a 3-pin **active buzzer module** (VCC, GND, I/O; the transistor is on the
board): VCC → 5V (pin 2), GND → GND, I/O → pin 12. Many are **low-level trigger** (beep when the
pin is OFF); with one of those the buzzer would beep all the time, so it needs a setting change
first.

**Test** (the reader service must be stopped so the pin is free):

```bash
sudo systemctl stop attendance-rfid
cd ~/AttendanceSystem && venv/bin/python -c "from gpiozero import Buzzer; from time import sleep; b=Buzzer(18); b.on(); sleep(0.3); b.off()"
sudo systemctl start attendance-rfid
```

One short beep = good. Silence or beep-beep-beep = wrong buzzer type (passive / intermittent).

### Green and red LEDs

| LED | Pi pin | GPIO | Wiring |
|---|---|---|---|
| Green (long leg +) | 16 | GPIO23 | pin 16 → **330 Ω resistor** → long leg; short leg → GND |
| Red (long leg +) | 18 | GPIO24 | pin 18 → **330 Ω resistor** → long leg; short leg → GND |
| Both short legs (−) | 20 | GND | (share one GND) |

What they do:

| Tap result | LCD | Buzzer | LED |
|---|---|---|---|
| Present | "PRESENT" | 1 short beep | **green** on 2 s |
| Late | "LATE" | 2 short beeps | **green** blinks twice |
| Card registered | "CARD REGISTERED" | 3 short beeps | **green** on 2 s |
| Already checked in / unknown card / no class open | the reason | 1 long beep | **red** on 2 s |
| Error | "ERROR" | 1 long beep | **red** fast blink |

### UPS board (battery %)

**Waveshare UPS HAT (D) (the one in the diagram): no wires.** It mounts **under** the Pi; its
spring pins press into the underside of the header (5V, GND, SDA, SCL). Charge it through its
own USB-C, use its ON/OFF switch as the power switch, and plug nothing into the Pi's USB-C.
**Two 21700 cells**, same brand/type/charge, + and − to the holder marks. Side by side
(parallel) they stay at ~3.7V, so `BATTERY_CELLS_SERIES=1`; they just last about twice as long.
Settings: `BATTERY_BACKEND=ina219`, `BATTERY_I2C_ADDRESS=0x43`, `BATTERY_CELLS_SERIES=1`.
Check: Settings → Attendance device shows about **3.6–4.2V**. If it shows **7–8.4V** the cells are
in series: set `BATTERY_CELLS_SERIES=2`.
After a shutdown the device stays off, even when charging, until the switch is flipped off and on. First time cells go in: press its **BOOT** button once.

Other UPS boards with I2C wires:

| UPS pin | Pi pin | |
|---|---|---|
| SDA | 3 | GPIO2, shared with LCD + RTC (I2C 0x36 or 0x42) |
| SCL | 5 | GPIO3, shared with LCD + RTC |
| GND | 30 | GND (any GND pin) |
| Power-loss signal (optional) | 31 | GPIO6, only if your board has one (X728) |

If your UPS board is a HAT that plugs onto all 40 pins and feeds the Pi through the GPIO 5V pins,
do **not** also plug USB-C into the Pi, and check its manual for which pins it uses.

### Two parts for the PCB

1. **I2C level shifter.** The LCD backpack runs on 5V and pulls SDA/SCL up to 5V; the Pi's pins are 3.3V only.
   Put a 2-channel BSS138 level shifter between the Pi (3.3V side) and the LCD (5V side), or remove the
   backpack's two pull-up resistors.
2. **Buzzer transistor.** Makes the buzzer louder (5V instead of the pin's 3.3V) and keeps the
   current off the Pi pin. Wiring in **Buzzer** above.

### Cooler fan

| Fan wire | Pi pin | |
|---|---|---|
| Red (+) | 4 | 5V (pin 2 is the 5V rail for the LCD side and buzzer) |
| Black (−) | 6 | GND |

### Power wiring

| From | To | How |
|---|---|---|
| USB-C adapter, 5V 3A | UPS board USB-C input | the port on the outside of the housing |
| 2 × 18650 cells | UPS board holders | check + and − against the holder marks |
| UPS board 5V output | power switch | red wire through the switch, black straight on |
| power switch | Pi USB-C power port | short USB-C cable or pigtail |

Power the Pi through its USB-C port only; never feed 5V into the GPIO pins at the same time.
Charge the cells only on the UPS board.

### Shutdown button (with confirmation)

Flipping the power switch while the Pi runs is like pulling the plug and can damage the SD card,
so the case has a push button that shuts the Pi down safely first.

| Button leg | Pi pin | |
|---|---|---|
| 1 | 37 | GPIO26 |
| 2 | 39 | GND |

No resistor needed. Set it up once (no internet needed), then reboot:

```bash
cd ~/AttendanceSystem
bash deploy/power-button.sh
sudo reboot
```

| You do | LCD |
|---|---|
| Press and hold | right away `SHUT DOWN · Keep holding the button... 3s 2s 1s` (let go = cancel) |
| Held **3 s** | `SHUT DOWN? Press the button again to confirm · Cancels in 10s` |
| **Press it again** within 10 s | `SHUTTING DOWN · Do NOT switch off yet`, then after 10–20 s `Safe to switch OFF` → flip the UPS switch off |
| Don't press again | Cancelled: back to `Tap your RFID card` |
| Turn on | Flip the UPS switch on |

An accidental bump or hold can't turn the device off: it always needs the second press.
"Safe to switch OFF" waits until everything has stopped and all data is saved, which is why it
takes 10–20 s. After it appears the Pi is fully off; only the power switch starts it again.
The button is read by the attendance-rfid service; `SHUTDOWN_BUTTON_GPIO=` (empty) in
`/etc/attendance.env` turns it off.

### Battery percentage

If your UPS board has a battery chip, the LCD shows `Battery 82%` and the instructor site shows
a battery badge, a low-battery warning (20%) and the level in **Settings**. At 5% and not charging,
the Pi shuts itself down safely. To turn it on:

1. Connect the UPS board's I2C pins (if it has them) to the same SDA/SCL line as the LCD, and run
   `i2cdetect -y 1`. A new address shows which chip it has: **36** = MAX17040, **42** (or 40/41/43) = INA219.
2. `sudo nano /etc/attendance.env` and set `BATTERY_BACKEND=max17040` or `BATTERY_BACKEND=ina219`
   (plus `BATTERY_I2C_ADDRESS` if it isn't 0x42, and `BATTERY_PLD_GPIO=6` for a Geekworm X728).
3. `sudo systemctl restart attendance-rfid attendance-web`

No chip found → leave `BATTERY_BACKEND=none`; everything else works, the badge just stays hidden.

### RTC: correct time without internet (DS3231, needed on a Pi 4)

The Pi has no clock that keeps running while unplugged, and with no internet it can't fix
itself. Present / late / absent all depend on the time, so add a **DS3231** module
(use a rechargeable **LIR2032** battery in the common blue ZS-042 board).
It shares the I2C pins with the LCD (different addresses: LCD 0x27, RTC 0x68).

| DS3231 | Pi pin | GPIO |
|---|---|---|
| VCC | 1 | 3.3V |
| GND | 25 | GND |
| SDA | 3 | GPIO2 (shared with the LCD) |
| SCL | 5 | GPIO3 (shared with the LCD) |

Shared connections (LCD + RTC on SDA/SCL, both LEDs on GND pin 20) only fit one jumper per pin,
so join them on the PCB (or a breadboard / perfboard while testing). All the Pi's GND pins
(6, 9, 14, 20, 25, 30, 34, 39) are the same ground.

**Turn it on (once; no internet needed).** Without this the Pi ignores the RTC: at power-on it
restores the time saved at the last shutdown and carries on, so it falls behind by however long
it was off, and syncing from a phone can't save into the RTC either.

```bash
cd ~/AttendanceSystem && bash deploy/rtc-setup.sh
```
```bash
sudo reboot
```

Then set the right time once: log in as an instructor on a phone with the correct time (it syncs
and saves into the RTC), or on the Pi, if its time is already right:
`sudo /usr/local/sbin/attendance-set-time $(date +%s)`.

Check: `i2cdetect -y 1` shows **UU** at 68, and `bash deploy/check-hardware.sh --quick` says
"RTC in use by the Pi" and "matches the Pi".
(Pi 5 instead: plug the official RTC battery into "BAT" and add `dtparam=rtc_bbat_vchg=3000000`
to `/boot/firmware/config.txt`.)

**Backup if the RTC fails or its battery dies:** the prof site shows a yellow
"The device's clock is wrong" bar whenever the Pi's time differs from the phone's by more than
2 minutes. Tap **Sync time from this phone** (also in **Settings → Device clock**) and the Pi's
clock **and** the RTC module are set to the phone's time. Make sure the phone's time is right first.

## E. Test each part

**One command checks everything** (I2C/SPI, LCD, card reader, UPS + cell voltage, RTC, buzzer,
LEDs, shutdown button, power, services) and prints ✓ / ✗ with a hint for each problem:

```bash
bash ~/AttendanceSystem/deploy/check-hardware.sh
```

It pauses the card reader while testing and starts it again at the end. It asks you to tap a card
and press the shutdown button; it turns the buzzer and each LED on briefly so you can hear / see
them, and shows "HARDWARE TEST" on the LCD. `--quick` skips the parts that need you.

The manual checks, one part at a time — power on, SSH in again, `cd ~/AttendanceSystem`, then:

```bash
i2cdetect -y 1
```
→ must show `27` (or `3f`) for the LCD (and `68` if you added the RTC).

```bash
venv/bin/python -c "from gpiozero import LED; from time import sleep; g,r=LED(23),LED(24); g.on(); sleep(1); g.off(); r.on(); sleep(1); r.off()"
```
→ green lights 1 s, then red 1 s. If not: LED backwards (swap legs) or no resistor/GND.

```bash
sudo systemctl status attendance-web attendance-rfid
```
→ both **active (running)**. Then tap a card: the LCD should change, beep, and light an LED.
Problems? See `logs/hardware.log` and `logs/app.log`.

## F. Turn on the Pi's own Wi-Fi

When everything works:

```bash
bash deploy/hotspot.sh "LSPU-Attendance" "your-password"
```

Your SSH connection drops (the Pi left your home Wi-Fi). Now on any phone or laptop:

1. Join Wi-Fi **LSPU-Attendance**
2. Students: **http://attendance.lan** · Instructors: **http://prof.attendance.lan**
3. SSH from a laptop on that Wi-Fi: `ssh admin@10.42.0.1`

The hotspot comes back by itself on every boot.

**Change the Wi-Fi name / password later from the website:** run once
`cd ~/AttendanceSystem && bash deploy/device-settings.sh` (installs a small helper that can only
change the hotspot). Then the admin opens **Device settings**, types the new name and password
(plus their own admin password) and saves. Every phone is disconnected after ~5 seconds and must
join the new Wi-Fi. The same page changes the **instructor registration code**.

## G. Daily use, updates, backups

**Start / stop:** flip the UPS switch on; everything starts in ~1 minute. To turn off: hold the
shutdown button 3 s, press it again, wait for `Safe to switch OFF`, then flip the switch
(or `sudo poweroff` over SSH).

### Updating the code from the laptop

Copy only the code. **Never copy the `data` folder**: it holds the database, signatures and
secret key, and the laptop's copy would replace the real records on the Pi. `venv` and `logs`
stay on the Pi too. Replace `admin` with your Pi username in all commands below.

#### Option 1: over Wi-Fi (no cable, the normal way)

1. On the laptop, join the **LSPU-Attendance** Wi-Fi (the laptop has no internet while on it).
2. In PowerShell, in the project folder:
   ```bash
   cd "C:\Users\Krizza P. Vallente\OneDrive\Desktop\AttendanceSystem"
   ```
   ```bash
   scp -r app hardware deploy docs tools config.py run.py requirements.txt requirements-pi.txt CHANGELOG.md README.md admin@10.42.0.1:~/AttendanceSystem/
   ```
3. Apply it:
   ```bash
   ssh admin@10.42.0.1
   ```
   ```bash
   sudo systemctl restart attendance-web attendance-rfid
   ```
4. Switch the laptop back to your normal Wi-Fi.

#### Option 2: with the Ethernet cable (hotspot broken, or the Pi needs internet)

Use this when phones/laptop can't see **LSPU-Attendance**, or an update needs new packages.

1. Plug the cable between the laptop and the Pi. Keep the laptop on its normal Wi-Fi.
2. Share the laptop's internet with the cable (only needed once, or if the Pi needs internet):
   **Win + R** → `ncpa.cpl` → right-click your **Wi-Fi** adapter → **Properties** → **Sharing** →
   tick **Allow other network users to connect…** → pick the **Ethernet** adapter → **OK**.
3. Turn on the Pi and wait ~1 minute. Its address on the cable is usually `192.168.137.2`.
   If that doesn't answer, find it with `arp -a` and look under the `192.168.137.1` interface
   for an address like `192.168.137.x` (type *dynamic*).
4. Copy and apply, the same as Option 1 with the cable address:
   ```bash
   scp -r app hardware deploy docs tools config.py run.py requirements.txt requirements-pi.txt CHANGELOG.md README.md admin@192.168.137.2:~/AttendanceSystem/
   ```
   ```bash
   ssh admin@192.168.137.2
   ```
   ```bash
   sudo systemctl restart attendance-web attendance-rfid
   ```
5. If the update changed `requirements.txt` (new packages), run this while on the cable:
   ```bash
   cd ~/AttendanceSystem && venv/bin/pip install -r requirements-pi.txt
   ```
6. Hotspot broken? Re-run it from here: `bash deploy/hotspot.sh`.

#### Notes for both

- Database changes in an update apply by themselves on the restart.
- An update that adds a setup script under `deploy/` says so in `CHANGELOG.md`; run it once on
  the Pi after copying.
- `Permission denied` → wrong username/password. `Connection refused` / `timed out` → the Pi
  is still booting, or the laptop isn't on the Pi's Wi-Fi / cable.
- Check it worked: open the website on a phone, and `sudo systemctl status attendance-web attendance-rfid`
  should say **active (running)** for both.

**Backup** (do it weekly): log in as the admin → **Backup & restore** → **Download backup**. That
one .zip holds the whole database and all signatures, and the same page restores it on a rebuilt
device. Instructors can also download a backup of their own subjects from **Settings**.

Copying the folder still works as a second copy:

```bash
scp -r admin@10.42.0.1:~/AttendanceSystem/data ./attendance-backup
```

**Load test on the Pi itself** (real Pi numbers for the thesis):
```bash
venv/bin/python tools/load_test.py
```

**A student or instructor lost their backup codes:** the admin opens **Backup codes** and issues a
one-time code.

**The admin forgot the password** (prints a temporary one):
```bash
cd ~/AttendanceSystem && set -a && . /etc/attendance.env && set +a && venv/bin/flask --app run reset-password <username>
```

**The admin forgot the username too:**
```bash
cd ~/AttendanceSystem && set -a && . /etc/attendance.env && set +a && venv/bin/flask --app run list-admins
```

## Troubleshooting

First run `bash ~/AttendanceSystem/deploy/check-hardware.sh`: it names the part that isn't
answering and what to check.

| Problem | Check |
|---|---|
| Website doesn't open | `sudo systemctl status attendance-web`, `logs/app.log`; phone really joined LSPU-Attendance? |
| `attendance.lan` doesn't open but `http://10.42.0.1` does | Phone is using mobile data / private DNS: turn off mobile data and "Private DNS" |
| LCD blank or blocks | Contrast screw; `i2cdetect -y 1` address matches `LCD_I2C_ADDRESS` in `/etc/attendance.env` |
| Card taps do nothing | RC522 on 3.3V? SPI on (`ls /dev/spidev0.0`)? `logs/hardware.log` |
| Times are wrong | Prof site → Settings → Device clock → **Sync time from this phone**. If it keeps happening, the RTC battery is empty or the RTC isn't set up (part D) |
| Sync time says "refused" | `sudo cat /etc/sudoers.d/attendance-time` must exist (run `deploy/install.sh` again); see `logs/app.log` |
| Random freezes / lightning icon | Cells low or adapter too weak: charge the cells, use a 5V 3A adapter |
| Cooler fan doesn't spin | Red wire on pin 2, black on pin 30 |
| Pi 5: RFID error about RPi.GPIO | install.sh installs `rpi-lgpio` for Pi 5; run `venv/bin/pip install rpi-lgpio` |
