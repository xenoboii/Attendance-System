#!/usr/bin/env bash
# Turn the Pi into its own Wi-Fi network (no router, no internet needed).
# Phones join it and open  http://attendance.lan  (students) or  http://prof.attendance.lan
#
#   bash deploy/hotspot.sh                         # network "LSPU-Attendance", password asked
#   bash deploy/hotspot.sh "MyName" "mypassword"
#
# WARNING: after this the Pi's Wi-Fi is the hotspot, so it no longer joins your home Wi-Fi.
# Run install.sh FIRST (it needs internet). To undo:  sudo nmcli con delete attendance-ap
set -euo pipefail

SSID="${1:-LSPU-Attendance}"
PASS="${2:-}"
if [ -z "$PASS" ]; then
  read -rsp "Hotspot password (at least 8 characters): " PASS; echo
fi
[ ${#PASS} -ge 8 ] || { echo "Password must be at least 8 characters"; exit 1; }

echo "==> Local names: attendance.lan and prof.attendance.lan -> this Pi (10.42.0.1)"
sudo mkdir -p /etc/NetworkManager/dnsmasq-shared.d
sudo tee /etc/NetworkManager/dnsmasq-shared.d/attendance.conf >/dev/null <<EOF
address=/attendance.lan/10.42.0.1
address=/prof.attendance.lan/10.42.0.1
# Every other name also points here. Phones test for internet by fetching a known address
# (connectivitycheck.gstatic.com, captive.apple.com ...); the website answers those tests
# (app/routes/main.py), so a phone with mobile data ON still keeps using this Wi-Fi.
address=/#/10.42.0.1
EOF

echo "==> Creating hotspot \"$SSID\""
sudo nmcli con delete attendance-ap >/dev/null 2>&1 || true
sudo nmcli con add type wifi ifname wlan0 con-name attendance-ap autoconnect yes ssid "$SSID" \
  802-11-wireless.mode ap 802-11-wireless.band bg \
  ipv4.method shared ipv4.addresses 10.42.0.1/24 \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASS"
# Plain WPA2 (AES) only, protected management frames off, a fixed 2.4 GHz channel: without
# these many phones see the network but fail to join ("incorrect password" / "couldn't connect").
sudo nmcli con modify attendance-ap wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
  wifi-sec.pmf disable 802-11-wireless.channel 6
sudo nmcli con modify attendance-ap connection.autoconnect-priority 100
sudo nmcli con up attendance-ap

echo
echo "==> Hotspot is on. Join \"$SSID\" on a phone, then open:"
echo "    Students:     http://attendance.lan"
echo "    Instructors:  http://prof.attendance.lan"
echo "    (SSH from a laptop on the hotspot:  ssh $(whoami)@10.42.0.1)"
