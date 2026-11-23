"""
"Safe to switch OFF" on the LCD at the very end of a shutdown.

Any shutdown (the confirmed shutdown button, an empty battery, `sudo shutdown now`) first stops
the website and the RFID service; the RFID service has already shown "SHUTTING DOWN / Do NOT
switch off yet". Once they have stopped (all data saved), systemd runs this as the stop step of
the attendance-lcd-off service (deploy/power-button.sh) and the LCD says it is safe to flip
the power switch. That is why it appears 10-20 s after the button: it waits for the saving.

    python -m hardware.power_screen      (does nothing unless the Pi is shutting down)
    python -m hardware.power_screen boot "LOADING / Please wait..." right after power-on

Deliberately does not import config.py: it runs as root during shutdown and only needs the
LCD address.
"""
import os
import subprocess
import sys
import time

from .lcd_display import Lcd, loading_screen, safe_off_screen, shutting_down_screen


def shutdown_kind() -> str | None:
    """'off' (shutdown / power off), 'reboot', or None when the Pi is NOT shutting down
    (e.g. only the service was restarted after an update) or we can't tell."""
    try:
        state = subprocess.run(["systemctl", "is-system-running"], capture_output=True,
                               text=True, timeout=3).stdout.strip()
        if state != "stopping":
            return None
        jobs = subprocess.run(["systemctl", "list-jobs", "--no-legend", "--no-pager"],
                              capture_output=True, text=True, timeout=3).stdout
    except Exception:          # not on a Pi (laptop / Windows) or systemctl unavailable
        return None
    if "reboot.target" in jobs or "kexec.target" in jobs:
        return "reboot"
    return "off"


def _lcd() -> Lcd:
    return Lcd(int(os.environ.get("LCD_I2C_ADDRESS", "0x27"), 16))


def _wait_for_i2c(port: int = 1, seconds: float = 15) -> None:
    """At power-on this runs very early, possibly before the I2C bus (/dev/i2c-1) exists."""
    if not sys.platform.startswith("linux"):
        return
    end = time.monotonic() + seconds
    while not os.path.exists(f"/dev/i2c-{port}") and time.monotonic() < end:
        time.sleep(0.5)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["boot"]:            # attendance-lcd-boot service, seconds after power-on
        _wait_for_i2c()
        _lcd().show(loading_screen())
        return
    kind = shutdown_kind()
    if kind is None:
        return
    _lcd().show(safe_off_screen() if kind == "off" else shutting_down_screen("reboot"))


if __name__ == "__main__":
    main()
