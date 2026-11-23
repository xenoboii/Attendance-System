#!/usr/bin/env bash
# Shutdown push button with confirmation + LCD power screens ("LOADING" at power-on,
# "Safe to switch OFF" at shutdown).
# Run once on the Pi (no internet needed; safe to run again):
#
#   cd ~/AttendanceSystem
#   bash deploy/power-button.sh
#   sudo reboot
#
# Wiring: push button between pin 37 (GPIO26) and pin 39 (GND).
# Use: hold 3 s -> LCD "SHUT DOWN? Press again to confirm" -> press within 10 s -> shuts down.
#      No second press -> cancelled, back to the normal screen.
# The button is read by the attendance-rfid service (hardware/rfid_reader.py), not by the kernel.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Removing the old instant-shutdown setting (it would skip the confirmation)"
for BOOT_CFG in /boot/firmware/config.txt /boot/config.txt; do
  [ -f "$BOOT_CFG" ] && sudo sed -i '/^dtoverlay=gpio-shutdown/d' "$BOOT_CFG"
done

echo "==> \"LOADING / Please wait...\" on the LCD a few seconds after power-on"
# Starts as early as possible (no waiting for the network), before the RFID service, which
# replaces it with "TAP YOUR ID" once it is ready.
sudo tee /etc/systemd/system/attendance-lcd-boot.service >/dev/null <<EOF
[Unit]
Description=Attendance System - "LOADING" on the LCD right after power-on
DefaultDependencies=no
After=local-fs.target systemd-modules-load.service
Before=attendance-rfid.service

[Service]
Type=oneshot
WorkingDirectory=$APP_DIR
EnvironmentFile=-/etc/attendance.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$APP_DIR/venv/bin/python -m hardware.power_screen boot
TimeoutStartSec=25

[Install]
WantedBy=multi-user.target
EOF

echo "==> \"Safe to switch OFF\" on the LCD at the end of every shutdown"
# Ordered BEFORE the website and RFID services, so at shutdown those stop first (data saved),
# and only then this service's stop step writes the message.
sudo tee /etc/systemd/system/attendance-lcd-off.service >/dev/null <<EOF
[Unit]
Description=Attendance System - "Safe to switch OFF" on the LCD at shutdown
Before=attendance-web.service attendance-rfid.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/attendance.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/bin/true
ExecStop=$APP_DIR/venv/bin/python -m hardware.power_screen
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now attendance-lcd-off
sudo systemctl enable attendance-lcd-boot
sudo systemctl restart attendance-rfid

echo
echo "==> Done. Reboot once so the old setting is gone:  sudo reboot"
echo "    Then: hold the button 3 s, press it again within 10 s to shut down."
