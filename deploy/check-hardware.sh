#!/usr/bin/env bash
# Check every hardware part (LCD, card reader, UPS, RTC, buzzer, LEDs, button, power).
#
#   bash ~/AttendanceSystem/deploy/check-hardware.sh            # full check
#   bash ~/AttendanceSystem/deploy/check-hardware.sh --quick    # no beeps / lights / button / card test
#
# The card reader service is paused while testing (they can't both use the parts) and is
# always started again at the end, even if the check is stopped with Ctrl+C.
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

restart_reader() {
  sudo systemctl start attendance-rfid 2>/dev/null && echo && echo "(card reader service started again)"
}
trap restart_reader EXIT

sudo systemctl stop attendance-rfid 2>/dev/null || true
set -a; [ -f /etc/attendance.env ] && . /etc/attendance.env; set +a
"$APP_DIR/venv/bin/python" tools/check_hardware.py "$@"
