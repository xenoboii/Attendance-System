#!/usr/bin/env bash
# Lets the admin change the hotspot Wi-Fi name and password from the website
# (Admin -> Device settings). Run once on the Pi (no internet needed; safe to run again):
#
#   cd ~/AttendanceSystem
#   bash deploy/device-settings.sh
#
# It installs a tiny root helper that can ONLY change the attendance-ap hotspot, and allows the
# website to run just that one command. (The instructor registration code needs no setup.)
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(whoami)"

echo "==> Installing /usr/local/sbin/attendance-set-wifi"
sudo install -m 0755 -o root -g root "$APP_DIR/deploy/attendance-set-wifi" /usr/local/sbin/attendance-set-wifi

echo "==> Allowing the website to run only that command"
echo "$APP_USER ALL=(root) NOPASSWD: /usr/local/sbin/attendance-set-wifi" | sudo tee /etc/sudoers.d/attendance-wifi >/dev/null
sudo chmod 0440 /etc/sudoers.d/attendance-wifi
sudo visudo -cf /etc/sudoers.d/attendance-wifi

sudo systemctl restart attendance-web
echo
echo "==> Done. Admin -> Device settings can now change the Wi-Fi name and password."
