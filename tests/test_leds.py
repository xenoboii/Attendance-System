"""Green LED = accepted, red LED = rejected (simulated - no GPIO needed)."""
from hardware.rfid_reader import Leds


def test_led_colors():
    for ok in ("present", "late", "registration"):
        assert Leds.color_for(ok) == "green"
    for bad in ("duplicate", "unknown_card", "no_open_session", "error"):
        assert Leds.color_for(bad) == "red"


def test_simulated_leds_print(capsys):
    leds = Leds(simulate=True, green_pin=23, red_pin=24)
    leds.show("late")
    leds.show("unknown_card")
    out = capsys.readouterr().out
    assert "GREEN LED x2 blink" in out and "RED LED" in out
