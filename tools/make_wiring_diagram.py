"""
Draws docs/wiring-diagram.svg: every part of the attendance device and the Pi pin it goes to.
Run again after changing a pin:   python tools/make_wiring_diagram.py
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "wiring-diagram.svg"

C = {"5v": "#d62828", "3v3": "#f77f00", "gnd": "#222222", "sda": "#1d70b8", "scl": "#c99a00",
     "sig": "#2a9d8f", "spi": "#7b2cbf", "unused": "#c8c8c8"}

PINS = {1: "3.3V", 2: "5V", 3: "SDA", 4: "5V", 5: "SCL", 6: "GND", 7: "GPIO4", 8: "GPIO14",
        9: "GND", 10: "GPIO15", 11: "GPIO17", 12: "GPIO18", 13: "GPIO27", 14: "GND", 15: "GPIO22",
        16: "GPIO23", 17: "3.3V", 18: "GPIO24", 19: "MOSI", 20: "GND", 21: "MISO", 22: "GPIO25",
        23: "SCLK", 24: "CE0", 25: "GND", 26: "GPIO7", 27: "ID_SD", 28: "ID_SC", 29: "GPIO5",
        30: "GND", 31: "GPIO6", 32: "GPIO12", 33: "GPIO13", 34: "GND", 35: "GPIO19", 36: "GPIO16",
        37: "GPIO26", 38: "GPIO20", 39: "GND", 40: "GPIO21"}
USED = {1: "3v3", 2: "5v", 3: "sda", 4: "5v", 5: "scl", 6: "gnd", 9: "gnd", 12: "sig", 14: "gnd",
        16: "sig", 17: "3v3", 18: "sig", 19: "spi", 20: "gnd", 21: "spi", 22: "sig", 23: "spi",
        24: "spi", 25: "gnd", 37: "sig", 39: "gnd"}

HX0, HX1 = 630, 870          # header box left / right edge (wires attach here)


def py(pin: int) -> int:
    return 195 + ((pin - 1) // 2) * 24


out: list[str] = []
a = out.append


def line(points, color, width=2.6, dash=None):
    pts = " ".join(f"{x},{y}" for x, y in points)
    d = f' stroke-dasharray="{dash}"' if dash else ""
    a(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{width}" '
      f'stroke-linejoin="round" stroke-linecap="round"{d}/>')


def dot(x, y, color):
    a(f'<circle cx="{x}" cy="{y}" r="4.2" fill="{color}"/>')


def text(x, y, s, size=12, anchor="start", weight="normal", color="#222", italic=False):
    st = ' font-style="italic"' if italic else ""
    a(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" '
      f'fill="{color}"{st}>{s}</text>')


def box(x0, y0, x1, y1, fill="#ffffff", stroke="#555", r=8, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    a(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{r}" fill="{fill}" '
      f'stroke="{stroke}" stroke-width="1.6"{d}/>')


def resistor(x0, y, color):
    """60 px wide zigzag, left end at x0."""
    a(f'<path d="M{x0},{y} l5,-6 l10,12 l10,-12 l10,12 l10,-12 l10,12 l5,-6" fill="none" '
      f'stroke="{color}" stroke-width="2.2" stroke-linejoin="round"/>')


def led(x, y, fill):
    a(f'<polygon points="{x},{y - 9} {x},{y + 9} {x + 20},{y}" fill="{fill}" stroke="#222" stroke-width="1.4"/>')
    a(f'<line x1="{x + 20}" y1="{y - 9}" x2="{x + 20}" y2="{y + 9}" stroke="#222" stroke-width="2.2"/>')
    for dx in (6, 13):
        a(f'<line x1="{x + dx}" y1="{y - 11}" x2="{x + dx + 7}" y2="{y - 19}" stroke="{fill}" stroke-width="1.6"/>')


# ------------------------------------------------------------------ header
def header():
    box(HX0, 177, HX1, 669, fill="#f4f6f8", stroke="#333", r=10)
    text(750, 163, "Raspberry Pi GPIO header", 14, "middle", "bold")
    for pin in range(1, 41):
        y = py(pin)
        used = USED.get(pin)
        col = C[used] if used else C["unused"]
        left = pin % 2 == 1
        cx = 730 if left else 770
        a(f'<circle cx="{cx}" cy="{y}" r="7" fill="{col}" stroke="#333" stroke-width="1"/>')
        tcol = "#222" if used else "#9a9a9a"
        w = "bold" if used else "normal"
        if left:
            text(716, y + 4, str(pin), 10, "end", w, tcol)
            text(640, y + 4, PINS[pin], 11, "start", w, tcol)
        else:
            text(784, y + 4, str(pin), 10, "start", w, tcol)
            text(860, y + 4, PINS[pin], 11, "end", w, tcol)
    text(750, 686, "pin 1 = square pad, end nearest the microSD slot", 10, "middle", color="#555", italic=True)


# ------------------------------------------------------------------ left side
def rtc():
    box(330, 180, 530, 310, fill="#eef4fb")
    text(340, 225, "RTC DS3231", 13, weight="bold")
    text(340, 242, "(ZS-042 board)", 10, color="#555")
    text(340, 258, "I2C 0x68 + 0x57", 10, color="#555")
    text(340, 274, "LIR2032 coin cell", 10, color="#555")
    for y, lab in ((195, "VCC"), (219, "SDA"), (243, "SCL"), (291, "GND")):
        text(522, y + 4, lab, 10, "end", "bold")
    line([(530, 195), (HX0, 195)], C["3v3"])
    line([(530, 219), (HX0, 219)], C["sda"])
    line([(530, 243), (HX0, 243)], C["scl"])
    line([(530, 291), (HX0, 291)], C["gnd"])
    # pass-through end -> level converter (over the top of the header)
    for x, y, lab, col in ((360, 35, "VCC", "3v3"), (400, 55, "GND", "gnd"),
                           (440, 75, "SDA", "sda"), (480, 95, "SCL", "scl")):
        text(x, 196, lab, 9, "middle", "bold")
        line([(x, 180), (x, y), (980, y)], C[col])
    text(420, 22, "RTC pass-through end (other side of the board)", 10, "middle", color="#555", italic=True)


def rc522():
    box(330, 375, 530, 560, fill="#f3eefb")
    text(345, 450, "RC522", 14, weight="bold")
    text(345, 467, "RFID reader", 11)
    text(345, 484, "3.3V ONLY", 10, weight="bold", color=C["5v"])
    for y, lab in ((387, "3.3V"), (411, "MOSI"), (435, "MISO"), (459, "SCK"), (483, "GND")):
        text(522, y + 4, lab, 10, "end", "bold")
    line([(530, 387), (HX0, 387)], C["3v3"])
    line([(530, 411), (HX0, 411)], C["spi"])
    line([(530, 435), (HX0, 435)], C["spi"])
    line([(530, 459), (HX0, 459)], C["spi"])
    line([(530, 483), (HX0, 483)], C["gnd"])
    text(338, 514, "SDA (SS)", 10, weight="bold")
    text(338, 534, "RST", 10, weight="bold")
    # pins 24 and 22 are on the other row: routed under the header
    line([(HX1, py(24)), (890, py(24)), (890, 700), (315, 700), (315, 510), (330, 510)], C["spi"])
    line([(HX1, py(22)), (900, py(22)), (900, 715), (300, 715), (300, 530), (330, 530)], C["sig"])


def button():
    box(470, 600, 600, 676, fill="#fff7e6")
    a('<rect x="478" y="606" width="78" height="20" rx="4" fill="#222"/>')
    text(517, 620, "HOLD 3s · PRESS", 8.5, "middle", "bold", "#ffffff")
    text(535, 660, "SHUTDOWN", 10, "middle", "bold")
    text(535, 671, "hold 3 s, press again", 8, "middle", color="#555")
    # push-button symbol
    a('<line x1="590" y1="627" x2="575" y2="627" stroke="#222" stroke-width="2"/>')
    a('<line x1="590" y1="651" x2="575" y2="651" stroke="#222" stroke-width="2"/>')
    a('<line x1="575" y1="627" x2="575" y2="633" stroke="#222" stroke-width="2"/>')
    a('<line x1="575" y1="651" x2="575" y2="645" stroke="#222" stroke-width="2"/>')
    a('<line x1="561" y1="625" x2="561" y2="653" stroke="#222" stroke-width="2.4"/>')
    a('<line x1="561" y1="639" x2="545" y2="639" stroke="#222" stroke-width="2"/>')
    a('<line x1="545" y1="632" x2="545" y2="646" stroke="#222" stroke-width="2"/>')
    line([(590, 627), (HX0, 627)], C["sig"])
    line([(590, 651), (HX0, 651)], C["gnd"])


# ------------------------------------------------------------------ right side
def lcd_chain():
    # level converter
    box(980, 20, 1150, 112, fill="#eefaf3")
    text(1065, 48, "Logic level", 11, "middle", "bold")
    text(1065, 62, "converter", 11, "middle", "bold")
    text(1065, 77, "BSS138 3.3V⇄5V", 9, "middle", color="#555")
    for y, lab in ((35, "LV"), (55, "GND"), (75, "LV1"), (95, "LV2")):
        text(986, y + 4, lab, 9, weight="bold")
    for y, lab in ((45, "HV1"), (65, "HV2"), (85, "GND")):
        text(1144, y + 4, lab, 9, "end", "bold")
    text(1065, 106, "HV", 9, "middle", "bold")
    # LCD
    box(1210, 20, 1460, 112, fill="#f2f7ee")
    a('<rect x="1290" y="30" width="160" height="50" rx="3" fill="#7fbf5a" stroke="#3c6e27"/>')
    text(1370, 50, "ATTENDANCE SYSTEM", 9, "middle", "bold", "#1d3a12")
    text(1370, 66, "Tap your RFID card", 9, "middle", color="#1d3a12")
    text(1370, 96, "20×4 LCD + I2C backpack (0x27)", 10, "middle", "bold")
    for y, lab in ((45, "SDA"), (65, "SCL"), (85, "GND")):
        text(1216, y + 4, lab, 9, weight="bold")
    text(1250, 106, "VCC", 9, "middle", "bold")
    line([(1150, 45), (1210, 45)], C["sda"])
    line([(1150, 65), (1210, 65)], C["scl"])
    line([(1150, 85), (1210, 85)], C["gnd"])


def five_volt_rail():
    # pin 2 -> 5V rail feeding converter HV, LCD VCC and the buzzer
    line([(HX1, 195), (890, 195), (890, 130), (1320, 130), (1320, 285), (1250, 285)], C["5v"])
    line([(1065, 112), (1065, 130)], C["5v"])
    line([(1250, 112), (1250, 130)], C["5v"])
    dot(1065, 130, C["5v"])
    dot(1250, 130, C["5v"])
    text(1290, 146, "5V rail", 10, "end", "bold", C["5v"])
    text(1290, 158, "(Y-wires / perfboard strip)", 9, "end", color="#555", italic=True)


def fan():
    box(910, 203, 1030, 259, fill="#f6f6f6")
    text(970, 228, "Cooling fan", 11, "middle", "bold")
    text(918, 223, "+", 11, weight="bold")
    text(918, 247, "−", 11, weight="bold")
    line([(HX1, 219), (910, 219)], C["5v"])
    line([(HX1, 243), (910, 243)], C["gnd"])


def buzzer():
    resistor(905, 315, C["sig"])
    text(935, 302, "1 kΩ", 10, "middle", "bold")
    line([(HX1, 315), (905, 315)], C["sig"])
    line([(965, 315), (1030, 315)], C["sig"])
    # NPN transistor
    a('<circle cx="1042" cy="318" r="22" fill="none" stroke="#555" stroke-width="1.2"/>')
    a('<line x1="1030" y1="303" x2="1030" y2="333" stroke="#222" stroke-width="3"/>')
    line([(1030, 309), (1052, 297), (1052, 285), (1150, 285)], C["gnd"], 2.4)     # collector
    line([(1030, 327), (1052, 339)], C["gnd"], 2.4)                                # emitter
    a('<polygon points="1052,339 1041,338 1046,331" fill="#222"/>')
    text(1022, 300, "B", 8, "end", color="#555")
    text(1058, 295, "C", 8, color="#555")
    text(1058, 350, "E", 8, color="#555")
    text(1070, 318, "NPN", 10, weight="bold")
    text(1070, 331, "S8050 / 2N2222", 9, color="#555")
    line([(HX1, 339), (1052, 339)], C["gnd"])
    # buzzer
    box(1150, 270, 1250, 300, fill="#fdeeee", r=14)
    text(1200, 289, "BUZZER", 10, "middle", "bold")
    text(1158, 266, "−", 11, weight="bold")
    text(1240, 266, "+", 11, weight="bold")
    text(1200, 316, "active, 3–24V", 9, "middle", color="#555")


def leds():
    for pin, fill, lab in ((16, "#2fbf4a", "green LED (accepted)"), (18, "#e63946", "red LED (rejected)")):
        y = py(pin)
        line([(HX1, y), (905, y)], C["sig"])
        resistor(905, y, C["sig"])
        line([(965, y), (1000, y)], C["sig"])
        led(1000, y, fill)
        line([(1020, y), (1090, y)], C["gnd"])
        text(1100, y + 4, lab, 10)
    text(935, 352, "330 Ω", 9, "middle", "bold")
    text(935, 400, "330 Ω", 9, "middle", "bold")
    line([(HX1, py(20)), (1090, py(20))], C["gnd"])
    line([(1090, py(16)), (1090, py(20))], C["gnd"])
    dot(1090, py(18), C["gnd"])
    dot(1090, py(20), C["gnd"])


# ------------------------------------------------------------------ UPS, legend, notes
def ups():
    line([(750, 735), (750, 698)], "#555", 2, "6,5")
    a('<polygon points="750,691 744,703 756,703" fill="#555"/>')
    box(470, 735, 1030, 830, fill="#fffbe6", stroke="#b08900", dash="8,5")
    text(750, 757, "Waveshare UPS HAT (D) — mounted UNDER the Pi (no wires)", 13, "middle", "bold")
    text(750, 776, "Spring pins press into the underside of the header: 5V (pins 2/4) · GND (6) · SDA (3) · SCL (5)",
         10.5, "middle")
    text(750, 794, "2 × 21700 cells · charge through the HAT's own USB-C · its ON/OFF switch is the power switch",
         10.5, "middle")
    text(750, 812, "Battery chip INA219 at 0x43 (+ controller 0x2D) · nothing plugs into the Pi's own USB-C port",
         10.5, "middle", color="#8a5a00", weight="bold")


def legend():
    box(30, 180, 285, 410, fill="#ffffff")
    text(45, 202, "Wire colours", 13, weight="bold")
    rows = (("5v", "5V power"), ("3v3", "3.3V power"), ("gnd", "GND (ground)"), ("sda", "I2C SDA (data)"),
            ("scl", "I2C SCL (clock)"), ("spi", "SPI (RFID reader)"), ("sig", "GPIO signal"))
    for i, (k, lab) in enumerate(rows):
        y = 225 + i * 25
        line([(45, y), (85, y)], C[k], 4)
        text(95, y + 4, lab, 11)
    text(45, 400, "● junction = wires joined", 10, color="#555")


def notes():
    box(1060, 440, 1470, 690, fill="#ffffff")
    text(1075, 462, "Notes", 13, weight="bold")
    items = [
        "Any GND pin works for any ground wire.",
        "Pin 2 feeds three parts (converter HV, LCD, buzzer):",
        "   join them with Y-wires or a perfboard strip.",
        "Only the LCD sits on the converter's 5V side;",
        "   RTC and UPS stay on the Pi's 3.3V SDA/SCL.",
        "RC522 on 3.3V only — 5V destroys it.",
        "Check I2C:  i2cdetect -y 1  should show",
        "   27 (LCD) · 2d 43 (UPS) · 57 68 (RTC)",
        "Turn off: hold button 3 s, press again → wait for",
        "   'Safe to switch OFF' → switch the UPS off.",
        "Buzzer quiet enough? It may also go straight",
        "   on pin 12 → + and pin 14 → − (no transistor).",
    ]
    for i, s in enumerate(items):
        text(1075, 484 + i * 18, s, 10.5)


def main():
    W, H = 1500, 930
    a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
      f'font-family="Segoe UI, Arial, Helvetica, sans-serif">')
    a(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    text(30, 38, "Attendance device — wiring diagram", 22, weight="bold")
    text(30, 60, "Raspberry Pi 4 · RC522 · 20×4 I2C LCD (via level converter) · DS3231 RTC · active buzzer · "
                 "LEDs · shutdown button · fan · Waveshare UPS HAT (D)", 12, color="#555")
    a('<g transform="translate(0,80)">')
    for part in (header, rtc, rc522, button, lcd_chain, five_volt_rail, fan, buzzer, leds, ups, legend, notes):
        part()
    a("</g></svg>")
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
