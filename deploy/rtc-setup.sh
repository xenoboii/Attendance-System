#!/usr/bin/env bash
# Make the Pi USE the DS3231 RTC module, so the time stays right while it's off.
# Run once on the Pi (no internet needed; safe to run again), then reboot:
#
#   cd ~/AttendanceSystem
#   bash deploy/rtc-setup.sh
#   sudo reboot
#
# Why: without this the Pi ignores the RTC. At power-on it restores the time it saved at the last
# shutdown ("fake-hwclock") and carries on from there, so after being off for 3 hours it is
# 3 hours behind - and "Sync time from this phone" can't save into the RTC either.
set -euo pipefail

BOOT_CFG=/boot/firmware/config.txt
[ -f "$BOOT_CFG" ] || BOOT_CFG=/boot/config.txt

echo "==> Turning on the RTC driver (DS3231 at I2C 0x68)"
if grep -q '^dtoverlay=i2c-rtc' "$BOOT_CFG"; then
  echo "    already in $BOOT_CFG"
else
  echo "dtoverlay=i2c-rtc,ds3231" | sudo tee -a "$BOOT_CFG" >/dev/null
fi

echo "==> Stopping fake-hwclock (it would put the old saved time back at every boot)"
sudo systemctl disable --now fake-hwclock 2>/dev/null || true
sudo systemctl mask fake-hwclock 2>/dev/null || true

echo
echo "==> Done. Now:"
echo "    1. sudo reboot"
echo "    2. Log in as an instructor on your phone (the time syncs from the phone and is saved"
echo "       into the RTC), or if the Pi's time is already right:"
echo "       sudo /usr/local/sbin/attendance-set-time \$(date +%s)"
echo "    3. Check: bash deploy/check-hardware.sh --quick   says the RTC time matches the Pi"
