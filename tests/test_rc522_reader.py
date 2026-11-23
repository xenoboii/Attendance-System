"""RC522 reader: the RST wire (GPIO25 = pin 22) must be used whatever pin numbering is active."""
import sys
import types
from unittest.mock import Mock

import pytest

from hardware.rfid_reader import RC522Reader

BCM, BOARD = 11, 10


@pytest.fixture
def fake_hw(monkeypatch):
    """Stand-ins for RPi.GPIO and mfrc522 (they only exist on a Pi)."""
    gpio = types.SimpleNamespace(BCM=BCM, BOARD=BOARD, mode=None)
    gpio.getmode = lambda: gpio.mode
    gpio.setmode = lambda m: setattr(gpio, "mode", m)
    rpi = types.ModuleType("RPi")
    rpi.GPIO = gpio
    chip = Mock(MI_OK=0, PICC_REQIDL=0x26)
    lib = types.ModuleType("mfrc522")
    lib.MFRC522 = Mock(return_value=chip)
    monkeypatch.setitem(sys.modules, "RPi", rpi)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio)
    monkeypatch.setitem(sys.modules, "mfrc522", lib)
    return gpio, lib, chip


def test_bcm_already_set_by_the_buzzer_uses_gpio25(fake_hw):
    gpio, lib, _ = fake_hw
    gpio.mode = BCM                      # gpiozero (buzzer / LEDs) got there first
    RC522Reader()
    lib.MFRC522.assert_called_once_with(pin_mode=BCM, pin_rst=25)   # not the library's GPIO15


def test_nothing_set_yet_chooses_bcm(fake_hw):
    gpio, lib, _ = fake_hw
    RC522Reader()
    assert gpio.mode == BCM
    lib.MFRC522.assert_called_once_with(pin_mode=BCM, pin_rst=25)


def test_board_numbering_uses_physical_pin_22(fake_hw):
    gpio, lib, _ = fake_hw
    gpio.mode = BOARD
    RC522Reader()
    lib.MFRC522.assert_called_once_with(pin_mode=BOARD, pin_rst=22)


def test_card_number_matches_the_old_library_format(fake_hw):
    _, _, chip = fake_hw
    chip.MFRC522_Request.return_value = (0, 0x10)
    chip.MFRC522_Anticoll.return_value = (0, [0x12, 0x34, 0x56, 0x78, 0x08])
    assert RC522Reader().read_uid() == "1234567808"     # = SimpleMFRC522.uid_to_num, in hex


def test_no_card_returns_none(fake_hw):
    _, _, chip = fake_hw
    chip.MFRC522_Request.return_value = (2, None)
    assert RC522Reader().read_uid() is None
    chip.MFRC522_Request.return_value = (0, 0x10)
    chip.MFRC522_Anticoll.return_value = (2, [])
    assert RC522Reader().read_uid() is None
