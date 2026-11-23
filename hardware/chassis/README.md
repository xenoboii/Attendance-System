# Attendance device chassis (3D print)

Two printed parts per sample: the **shell** (walls, slope, top) and the **base** (floor plate with
standoffs). The base screws up into the shell with 4 × M3 screws from underneath.

| Sample | Shape | Size (W × D × H) |
|---|---|---|
| 1 Console | flat front deck (tap zone, LEDs, button) + slope with the LCD + flat top | 220 × 180 × 90 mm |
| 2 Wedge | one long slope from the front edge (LCD + LEDs + button on top, tap zone + buzzer below) + flat top | 175 × 170 × 95 mm |
| 3 Step | deep front deck + near-vertical LCD panel + tall flat top | 200 × 175 × 100 mm |

**Chosen design: Sample 2 Wedge** (`stl/sample2-wedge-shell.stl` + `stl/sample2-wedge-base.stl`).
Front panel: LCD with the green/red LEDs and the push button to its right; the tap zone centered
below. Buzzer grille in the right side wall, near the front. Intake fan in the top right above the Pi's processor, exhaust fan in the
right wall. UPS USB-C + switch come out of the back wall, in a slot under the Pi's HDMI ports.

### Components mounted in the Wedge (labeled picture: `preview/wedge-components-labeled.png`)

| # | Component | Where | How it's held |
|---|---|---|---|
| 1 | Raspberry Pi 4 | back-left, on top of the UPS HAT | M2.5 screws into the UPS standoffs |
| 2 | Waveshare UPS HAT (D) + 2 × 21700 cells | under the Pi | 4 × M2.5 standoffs on the base |
| 3 | 20x4 LCD with I2C backpack | front panel, upper | 4 × M3 × 6 into the posts behind the window |
| 4 | RC522 RFID reader | under the "TAP YOUR ID" zone, front panel center | 4 × M2.5 × 5 into the posts |
| 5 | Custom PCB (DS3231 RTC, logic level converter, wiring) | back-right, on the base | 4 × M3 × 6 on the base standoffs |
| 6 | Push button (16 mm panel mount) | front panel, right of the LCD | its own nut, from inside |
| 7 | Green LED + red LED (5 mm) | front panel, above the button | push-fit, a drop of glue |
| 8 | Buzzer (42 mm) | right wall, near the front | 2 × M3 × 6 through its ears |
| 9 | Intake fan (30 mm) | flat top, above the Pi's processor | 4 × M3 (or the fan's screws) |
| 10 | Exhaust fan (30 mm) | right wall, high, toward the back | 4 × M3 (or the fan's screws) |
| 11 | Base plate | bottom | 4 × M3 × 8 countersunk into the cover, 4 × 10 mm rubber feet |

Openings: Pi USB + Ethernet (left wall); Pi micro-HDMI + USB-C (back wall, upper slot);
UPS USB-C charging + ON/OFF switch (back wall, lower slot).

Files: `stl/sampleN-<name>-shell.stl`, `stl/sampleN-<name>-base.stl`; pictures in `preview/`.

## Before printing: measure these (sizes differ between sellers)

Change the numbers at the top of `chassis.scad` (free OpenSCAD: openscad.org), then export again:
F6 (render) → File → Export → STL. Set `SAMPLE` (1/2/3) and `PART` (1 = shell, 2 = base).

| Setting | What | Default |
|---|---|---|
| `fan`, `fan_holes` | mini fan size and screw spacing | 30 mm, 24 mm |
| `ups_h` | UPS HAT (D) with the 21700 cells: height from its standoffs to the Pi board | 30 mm |
| `ups_slot` | back-wall slot for the UPS USB-C port + ON/OFF switch: [bottom above the UPS standoffs, height] | 1, 12 mm |
| `lcd_so` | LCD: height of the metal frame above the board, minus 2.5 mm wall | 7 mm |
| `rfid_holes` | RC522 screw holes | 7 mm from edges |
| `btn_d` | push button panel hole (16.2 for 16 mm, 12.2 for 12 mm) | 16.2 mm |
| `buzz_ear` | distance between the 4216 buzzer's two screw ears | 50 mm |

A quick test print of only a small part (e.g. a 10 mm slice through the LCD window) saves filament.

## Print settings (suggested)

- PLA or PETG, 0.2 mm layers, 3 walls, 15-20 % infill.
- **Shell**: open side (bottom) down on the bed. Supports for the slope/top only if your slicer
  says the overhang needs them (the Console and Step tops are flat bridges inside).
- **Base**: flat, no supports.

## Hardware

- 4 × M3 × 8 screws: base → shell (countersunk holes from below; self-tapping into 2.6 mm holes,
  or use M3 heat-set inserts and change `screw_tap` to 4.0)
- Raspberry Pi 4 + Waveshare UPS HAT (D): 4 × M2.5 standoffs/screws on the base
- LCD: 4 × M3 × 6 into the posts behind the window
- RC522: 4 × M2.5 × 5 into the posts under the tap zone
- Custom PCB: 4 × M3 × 6 on the base standoffs
- Buzzer: 2 × M3 × 6 through its ears
- Fans: 4 × M3 (or the fan's own screws) each; intake in the top above the Pi, exhaust high on the right wall
- Push button: its own nut, from inside; LEDs: 5 mm, push-fit (a drop of glue holds them)
- 4 × 10 mm rubber feet in the base recesses
