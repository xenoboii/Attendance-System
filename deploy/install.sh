#!/usr/bin/env bash
# One-time setup ON THE RASPBERRY PI (needs internet - run it before turning on the hotspot).
#
#   cd ~/AttendanceSystem
#   bash deploy/install.sh
#
# What it does: system packages, SPI + I2C on, Python venv + packages, settings file,
# database, and two services that start on every boot:
#   attendance-web   the website (waitress, port 80)
#   attendance-rfid  RFID reader + LCD + buzzer + LEDs
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(whoami)"
echo "==> Installing from $APP_DIR as user $APP_USER"

echo "==> System packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev python3-pip i2c-tools git

echo "==> Turning on SPI (RFID) and I2C (LCD)"
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0
sudo usermod -aG gpio,spi,i2c "$APP_USER" || true

echo "==> Python packages"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements-pi.txt"
# Raspberry Pi 5: the old RPi.GPIO (used by the RC522 library) doesn't work there; this drop-in does
if grep -q "Raspberry Pi 5" /proc/device-tree/model 2>/dev/null; then
  "$APP_DIR/venv/bin/pip" uninstall -y RPi.GPIO || true
  "$APP_DIR/venv/bin/pip" install rpi-lgpio
fi

echo "==> Settings: /etc/attendance.env"
if [ ! -f /etc/attendance.env ]; then
  sudo cp "$APP_DIR/deploy/attendance.env.example" /etc/attendance.env
  echo "    created - edit it to change the instructor registration code"
else
  echo "    already exists - left as is"
fi

echo "==> Database"
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
set -a; . /etc/attendance.env; set +a
( cd "$APP_DIR" && "$APP_DIR/venv/bin/flask" --app run init-db )

echo "==> \"Sync time from this phone\" (backup for the RTC module)"
sudo install -m 0755 -o root -g root "$APP_DIR/deploy/attendance-set-time" /usr/local/sbin/attendance-set-time
# the app may run ONLY these as root: set the clock, and shut down safely when the battery is empty
echo "$APP_USER ALL=(root) NOPASSWD: /usr/local/sbin/attendance-set-time, /usr/sbin/shutdown -h now" | sudo tee /etc/sudoers.d/attendance-time >/dev/null
sudo chmod 0440 /etc/sudoers.d/attendance-time
sudo visudo -cf /etc/sudoers.d/attendance-time

echo "==> Admin -> Device settings: change the hotspot Wi-Fi from the website"
sudo install -m 0755 -o root -g root "$APP_DIR/deploy/attendance-set-wifi" /usr/local/sbin/attendance-set-wifi
echo "$APP_USER ALL=(root) NOPASSWD: /usr/local/sbin/attendance-set-wifi" | sudo tee /etc/sudoers.d/attendance-wifi >/dev/null
sudo chmod 0440 /etc/sudoers.d/attendance-wifi
sudo visudo -cf /etc/sudoers.d/attendance-wifi

echo "==> Services (start on boot)"
sudo tee /etc/systemd/system/attendance-web.service >/dev/null <<EOF
[Unit]
Description=Attendance System - website
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/attendance.env
ExecStart=$APP_DIR/venv/bin/waitress-serve --host 0.0.0.0 --port 80 --threads 4 run:app
# allow port 80 without running as root
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/attendance-rfid.service >/dev/null <<EOF
[Unit]
Description=Attendance System - RFID reader, LCD, buzzer, LEDs
After=attendance-web.service

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/attendance.env
ExecStart=$APP_DIR/venv/bin/python -m hardware.rfid_reader
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now attendance-web attendance-rfid

echo
echo "==> Done."
echo "    Website:   http://$(hostname -I | awk '{print $1}')   (from a device on the same network)"
echo "    Status:    sudo systemctl status attendance-web attendance-rfid"
echo "    Logs:      $APP_DIR/logs/app.log  and  $APP_DIR/logs/hardware.log"
echo "    Next:      reboot once (for SPI/I2C), test the hardware, then run deploy/hotspot.sh"
