// =====================================================================================
//  LSPU Attendance device - chassis (3D-printable), parametric, 3 design samples
// =====================================================================================
//  Open in OpenSCAD (free: https://openscad.org), press F5 to preview, F6 to render,
//  then File > Export > Export as STL.  Print the SHELL and the BASE separately.
//
//  SAMPLE 1 "Console": flat front deck (tap zone, LEDs, button) + slope with the LCD + flat top
//  SAMPLE 2 "Wedge"  : one long slope from the front edge (tap zone, LEDs, button, LCD) + flat top
//  SAMPLE 3 "Step"   : deep front deck + near-vertical LCD panel + tall flat top
//
//  Inside: Raspberry Pi 4 on the Waveshare UPS HAT (D) at the BACK-LEFT (USB/Ethernet out of the
//  LEFT wall, micro-HDMI/USB-C out of the BACK wall, UPS USB-C + switch out of the BACK wall
//  under the HDMI ports), custom PCB (RTC + level converter + wiring) at the back-right, RC522
//  under the tap zone, LCD screwed behind its window, 4216 buzzer screwed under a grille, panel
//  push button (nut-mounted), 2 LEDs, mini fans: INTAKE in the top above the Pi, EXHAUST high
//  on the right wall.
//
//  Every size marked MEASURE varies between sellers: measure your part and change the number.
// =====================================================================================

SAMPLE = 1;           // 1 = Console, 2 = Wedge, 3 = Step
PART   = 0;           // 0 = assembly (shell see-through + all parts), 1 = shell (print),
                      // 2 = base (print), 3 = base + parts without the shell (to look inside)

$fn = 48;

// ------------------------------------------------------------- printing
wall    = 2.5;        // shell wall
floor_t = 3;          // base plate
clr     = 0.4;        // fit clearance base <-> shell
screw_tap = 2.6;      // M3 self-tapping into plastic (or 4.0 for M3 heat-set inserts)
screw_pass = 3.4;     // M3 clearance hole
m25_tap = 2.2;        // M2.5 self-tapping (Pi / UPS)

// ------------------------------------------------------------- parts
pi        = [85, 56];                                       // Raspberry Pi 4 board
pi_holes  = [[3.5,3.5],[61.5,3.5],[3.5,52.5],[61.5,52.5]];  // from the microSD end
pi_port_h = 16;       // USB/Ethernet height above the Pi board
ups_h     = 30;       // MEASURE: UPS HAT (D) with cells, from its standoffs to the Pi board
stand_h   = 5;        // standoffs under the UPS HAT
ups_usb   = [ 15, 12, 6.5];   // MEASURE: UPS USB-C on the left wall: [y from Pi back edge, z above UPS standoffs, width]
ups_sw    = [ 40, 10, 9];     // MEASURE: UPS ON/OFF switch slot: [y, z, width]  (height 6)

lcd       = [98, 60];      // 20x4 LCD board
lcd_holes = [93, 55];      // hole spacing (M3)
lcd_frame = [97.5, 40.5];  // metal frame passes through the window
lcd_so    = 7;             // MEASURE: frame height above the board minus the wall (standoff length)
lcd_depth = 26;            // board + I2C backpack (for checking clearances)

rfid       = [60, 40];                                   // RC522
rfid_holes = [[7,7],[53,7],[7,33],[53,33]];              // MEASURE (they differ between sellers)
rfid_so    = 3;                                          // gap under the tap surface

pcb       = [70, 50];      // custom PCB / perfboard
pcb_holes = [66, 46];

btn_d   = 16.2;            // MEASURE: panel push button (12.2 for 12 mm buttons)
led_d   = 5.2;             // 5 mm LEDs
buzz_d  = 42;              // 4216 buzzer
buzz_ear = 50;             // MEASURE: distance between its two screw ears
fan     = 30;              // MEASURE: mini fan size (25 / 30 / 40)
fan_holes = 24;            // MEASURE: fan hole spacing (25 -> 20, 30 -> 24, 40 -> 32)

// ------------------------------------------------------------- the 3 samples
// [width, depth, deck height, deck end (y), top start (y), top height]
function sample(s) =
    s == 1 ? [220, 180, 40, 65, 125,  90] :
    s == 2 ? [175, 170, 32,  0, 115,  95] :
             [200, 175, 36, 78, 100, 100];

S = sample(SAMPLE);
W = S[0]; D = S[1]; deck_h = S[2]; deck_end = S[3]; top_start = S[4]; top_h = S[5];
slope_ang = atan2(top_h - deck_h, top_start - deck_end);
slope_len = norm([top_start - deck_end, top_h - deck_h]);

// where things go:  [x, y] on the deck  or  [x, v] on the slope (v = distance up the slope)
// Wedge front panel (the slope):      [   LCD   ]  o o   <- green / red LED
//                                      [         ]   O    <- push button
//                                          (TAP ID)           buzzer: right side wall
lcd_v   = slope_len * 0.72;                                               // Wedge LCD height on the slope
lcd_at  = SAMPLE == 2 ? [W/2 - 14, lcd_v] : [140, slope_len * 0.5];       // on the slope
tap_at  = SAMPLE == 2 ? [W/2, 38] : [70, SAMPLE == 1 ? 33 : 40];        // Wedge: centered
on_deck = SAMPLE != 2;     // tap zone, LEDs, button on the deck (1, 3) or on the slope (2)
led_g   = SAMPLE == 1 ? [150, 52] : SAMPLE == 2 ? [W/2 + 44, lcd_v + 14] : [150, 56];
led_r   = [led_g[0] + 16, led_g[1]];
btn_at  = SAMPLE == 1 ? [196, 45] : SAMPLE == 2 ? [W/2 + 52, lcd_v - 8] : [185, 62];
// buzzer: Wedge = in the RIGHT wall, near the front ([y, z]), grille on the right side
buzz_at = SAMPLE == 1 ? [160, 26] : SAMPLE == 3 ? [160, 28] : [60, 30];
buzz_on_side = SAMPLE == 2;
pi_x0 = wall + 1;
pi_y0 = D - wall - 1 - pi[1];
pi_z  = floor_t + stand_h + ups_h;               // Pi board bottom
pcb_at = [W - wall - 4 - pcb[0], D - 20 - pcb[1]];
// fans: INTAKE in the flat top, right above the Pi's processor, blowing down on it;
//       EXHAUST in the right wall, high up (warm air leaves on the right)
soc = [pi_x0 + pi[0] - 29, pi_y0 + pi[1] - 32];      // Pi 4 processor (board turned 180 deg)
fan_in_at  = [soc[0], min(soc[1], D - wall - fan/2 - 1)];   // top:        [x, y]
fan_out_at = [135, top_h - 35];                    // right wall: [y, z]
// UPS HAT (D) USB-C charging port + ON/OFF switch: back wall, under the Pi's micro-HDMI ports.
// One slot covers both (same width as the HDMI opening). MEASURE: [bottom above the UPS
// standoffs, slot height] - make it smaller once you know exactly where they are.
ups_slot = [1, 12];
posts = [[12, 12], [W - 12, 12], [W - 12, D - 12], [W * 0.55, D - 12]];

// ------------------------------------------------------------- frames
module ext(w) { rotate([90, 0, 90]) linear_extrude(w) children(); }      // (y,z) profile along x

function profile() = deck_end > 0
    ? [[0,0],[D,0],[D,top_h],[top_start,top_h],[deck_end,deck_h],[0,deck_h]]
    : [[0,0],[D,0],[D,top_h],[top_start,top_h],[0,deck_h]];

// height of the outer surface at depth y (deck, slope or top)
function surface_z(y) = y < deck_end ? deck_h
                      : y < top_start ? deck_h + (y - deck_end) * tan(slope_ang)
                      : top_h;

// local frame on the slope: x across, y up the slope, z = outward normal (outer surface at z=0)
module on_slope(u, v) { translate([u, deck_end, deck_h]) rotate([slope_ang, 0, 0]) translate([0, v, 0]) children(); }
module on_deck(x, y)  { translate([x, y, deck_h]) children(); }
module on_top(x, y)   { translate([x, y, top_h]) children(); }
module on_face(p, sloped) { if (sloped) on_slope(p[0], p[1]) children(); else on_deck(p[0], p[1]) children(); }
module on_right(y, z) { translate([W, y, z]) rotate([0, 90, 0]) children(); }   // right wall, z = outward
module on_buzz() { if (buzz_on_side) on_right(buzz_at[0], buzz_at[1]) children(); else on_deck(buzz_at[0], buzz_at[1]) children(); }

// ------------------------------------------------------------- shell
module inner2d() {
    union() {
        offset(delta = -wall) polygon(profile());
        translate([wall, -1]) square([D - 2 * wall, wall + 1]);        // open bottom
    }
}

module through(d)  { translate([0, 0, -wall - 1]) cylinder(d = d, h = wall + 2); }
module through_rect(w, h) { translate([-w/2, -h/2, -wall - 1]) cube([w, h, wall + 2]); }

// hanging screw post under a face (local frame): length l, for a screw of diameter d
module post(l, d = screw_tap, od = 6.5) {
    translate([0, 0, -wall - l]) difference() {
        cylinder(d = od, h = l + 0.5);
        translate([0, 0, -1]) cylinder(d = d, h = l);
    }
}

module grille(r, hole = 3.2, pitch = 5) {           // round field of holes (fan / buzzer)
    for (i = [-ceil(r/pitch):ceil(r/pitch)], j = [-ceil(r/pitch):ceil(r/pitch)]) {
        x = i * pitch + (j % 2) * pitch / 2; y = j * pitch * 0.866;
        if (norm([x, y]) < r - hole / 2) translate([x, y, 0]) through(hole);
    }
}

module shell_inside() {
    // LCD: 4 posts behind the window
    on_slope(lcd_at[0], lcd_at[1])
        for (sx = [-1, 1], sy = [-1, 1]) translate([sx * lcd_holes[0]/2, sy * lcd_holes[1]/2, 0]) post(lcd_so);
    // RC522 under the tap zone
    on_face(tap_at, !on_deck)
        for (h = rfid_holes) translate([h[0] - rfid[0]/2, h[1] - rfid[1]/2, 0]) post(rfid_so, m25_tap, 5);
    // buzzer: 2 posts for its ears
    on_buzz() for (s = [-1, 1]) translate(buzz_on_side ? [0, s * buzz_ear/2, 0] : [s * buzz_ear/2, 0, 0]) post(4);
    // base screw columns: from the base up into the deck / slope / top above them (the
    // intersection in shell() trims them to the outside, so they always join the wall)
    for (p = posts) {
        h = surface_z(p[1] + 4) - floor_t;
        translate([p[0], p[1], floor_t]) difference() {
            translate([-4, -4, 0]) cube([8, 8, h + 0.5]);
            translate([0, 0, -1]) cylinder(d = screw_tap, h = 14);
        }
    }
}

module shell_cutouts() {
    // LCD window
    on_slope(lcd_at[0], lcd_at[1]) through_rect(lcd_frame[0], lcd_frame[1]);
    // tap zone: engraved ring (0.8 mm deep) as a target, no hole (reads through 2.5 mm)
    on_face(tap_at, !on_deck) translate([0, 0, -0.8]) difference() {
        cylinder(r = 24, h = 1); translate([0, 0, -1]) cylinder(r = 22.8, h = 3);
    }
    on_face(tap_at, !on_deck) translate([0, -31, -0.8]) linear_extrude(1)
        text("TAP YOUR ID", size = 5, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");
    // LEDs + push button
    on_face(led_g, !on_deck) through(led_d);
    on_face(led_r, !on_deck) through(led_d);
    on_face(btn_at, !on_deck) through(btn_d);
    // buzzer grille
    on_buzz() grille(buzz_d / 2 - 4, 2.5, 4.5);
    // Pi: USB + Ethernet through the LEFT wall, micro-HDMI / USB-C / audio through the BACK wall
    translate([-1, pi_y0 + 1.5, pi_z + 0.5]) cube([wall + 2, pi[1] - 3, pi_port_h + 2.5]);
    translate([pi_x0 + pi[0] - 64, D - wall - 1, pi_z - 1]) cube([58, wall + 2, 9]);
    // UPS HAT (D): USB-C charging port + ON/OFF switch through the BACK wall, under the HDMI opening
    translate([pi_x0 + pi[0] - 64, D - wall - 1, floor_t + stand_h + ups_slot[0]]) cube([58, wall + 2, ups_slot[1]]);
    // fans: intake in the top above the Pi, exhaust high on the right wall (grille + 4 screw holes)
    on_top(fan_in_at[0], fan_in_at[1]) {
        grille(fan / 2 - 1.5, 3.2, 5);
        for (sx = [-1, 1], sy = [-1, 1]) translate([sx * fan_holes/2, sy * fan_holes/2, 0]) through(3.4);
    }
    translate([W, fan_out_at[0], fan_out_at[1]]) rotate([0, 90, 0]) {
        grille(fan / 2 - 1.5, 3.2, 5);
        for (sx = [-1, 1], sy = [-1, 1]) translate([sx * fan_holes/2, sy * fan_holes/2, 0]) through(3.4);
    }
}

module shell() {
    difference() {
        union() {
            difference() {
                ext(W) polygon(profile());
                translate([wall, 0, 0]) ext(W - 2 * wall) inner2d();
            }
            intersection() {                                   // keep posts inside the outline
                shell_inside();
                ext(W) polygon(profile());
            }
        }
        shell_cutouts();
    }
}

// ------------------------------------------------------------- base plate
module pi_frame() {   // Pi coordinates turned 180 deg: USB end at the left wall, ports at the back
    translate([pi_x0 + pi[0], pi_y0 + pi[1], 0]) rotate([0, 0, 180]) children();
}

module base() {
    difference() {
        union() {
            translate([wall + clr, wall + clr, 0]) cube([W - 2 * (wall + clr), D - 2 * (wall + clr), floor_t]);
            // UPS HAT (D) standoffs (same hole pattern as the Pi above it)
            pi_frame() for (h = pi_holes) translate([h[0], h[1], floor_t - 0.5]) cylinder(d = 6, h = stand_h + 0.5);
            // custom PCB standoffs
            for (sx = [0, 1], sy = [0, 1])
                translate([pcb_at[0] + (pcb[0] - pcb_holes[0])/2 + sx * pcb_holes[0],
                           pcb_at[1] + (pcb[1] - pcb_holes[1])/2 + sy * pcb_holes[1], floor_t - 0.5])
                    cylinder(d = 6, h = 6.5);
        }
        pi_frame() for (h = pi_holes) translate([h[0], h[1], 1]) cylinder(d = m25_tap, h = stand_h + floor_t);
        for (sx = [0, 1], sy = [0, 1])
            translate([pcb_at[0] + (pcb[0] - pcb_holes[0])/2 + sx * pcb_holes[0],
                       pcb_at[1] + (pcb[1] - pcb_holes[1])/2 + sy * pcb_holes[1], 1]) cylinder(d = screw_tap, h = 9);
        // screws up into the shell columns (countersunk from below)
        for (p = posts) translate([p[0], p[1], -1]) { cylinder(d = screw_pass, h = floor_t + 2); cylinder(d1 = 6.5, d2 = 3.4, h = 2.6); }
        // vent slots under the Pi and 4 rubber-feet recesses
        for (i = [0:5]) translate([pi_x0 + 12 + i * 11, pi_y0 + 10, -1]) cube([4, 36, floor_t + 2]);
        for (p = [[15, 25], [W - 15, 25], [15, D - 15], [W - 15, D - 15]])
            translate([p[0], p[1], -0.01]) cylinder(d = 10.5, h = 1.2);
    }
}

// ------------------------------------------------------------- stand-in parts (assembly view)
module parts() {
    color("#2f7d3a") pi_frame() translate([0, 0, pi_z]) cube([pi[0], pi[1], 1.6]);                 // Pi
    color("#9aa3a8") pi_frame() translate([pi[0] - 21, 2, pi_z + 1.6]) cube([22, pi[1] - 4, pi_port_h]);  // USB/Eth
    color("#1f4fa8") pi_frame() translate([0, 0, floor_t + stand_h]) cube([pi[0], pi[1], 3]);      // UPS board
    color("#c7553a") pi_frame() for (i = [0, 1])                                                   // 2 x 21700
        translate([7, 14 + i * 24, floor_t + stand_h + 14]) rotate([0, 90, 0]) cylinder(d = 21, h = 70);
    color("#1d5c2d") translate([pcb_at[0], pcb_at[1], floor_t + 6]) cube([pcb[0], pcb[1], 1.6]);   // custom PCB
    color("#2d7a3f") on_slope(lcd_at[0], lcd_at[1]) translate([-lcd[0]/2, -lcd[1]/2, -wall - lcd_so - 1.6])
        cube([lcd[0], lcd[1], 1.6]);
    color("#2b2b2b") on_slope(lcd_at[0], lcd_at[1]) translate([-lcd_frame[0]/2, -lcd_frame[1]/2, -wall - lcd_so])
        cube([lcd_frame[0], lcd_frame[1], lcd_so + wall - 0.2]);
    color("#2a59c9") on_face(tap_at, !on_deck) translate([-rfid[0]/2, -rfid[1]/2, -wall - rfid_so - 1.6])
        cube([rfid[0], rfid[1], 1.6]);
    color("#222") on_buzz() translate([0, 0, -wall - 16]) cylinder(d = buzz_d, h = 16);
    color("#bbb") on_face(btn_at, !on_deck) translate([0, 0, -18]) cylinder(d = btn_d - 0.2, h = 20);
    color("#3c3") on_face(led_g, !on_deck) { translate([0, 0, -8]) cylinder(d = 5, h = 8); sphere(d = 5); }
    color("#e33") on_face(led_r, !on_deck) { translate([0, 0, -8]) cylinder(d = 5, h = 8); sphere(d = 5); }
    color("#444") translate([fan_in_at[0] - fan/2, fan_in_at[1] - fan/2, top_h - wall - 10]) cube([fan, fan, 10]);
    color("#444") translate([W - wall - 10, fan_out_at[0] - fan/2, fan_out_at[1] - fan/2]) cube([10, fan, fan]);
    details();
}

// look-alike details for the pictures only (not printed, not used for fit)
module details() {
    // Pi: GPIO header + processor
    color("#111") pi_frame() translate([7, pi[1] - 6, pi_z + 1.6]) cube([51, 5, 8.5]);
    color("#b8b8b8") pi_frame() translate([24, 22, pi_z + 1.6]) cube([15, 15, 1.4]);
    // LCD: blue backlit glass inside the black bezel (front) + blue I2C backpack (back)
    color("#3a6cff") on_slope(lcd_at[0], lcd_at[1]) translate([-38, -13, -0.2]) cube([76, 26, 0.3]);
    color("#1450c8") on_slope(lcd_at[0], lcd_at[1]) translate([-21, -9, -wall - lcd_so - 1.6 - 9]) cube([42, 18, 9]);
    // RC522: rectangular copper antenna loops at one end, chip + 8-pin header at the other
    on_face(tap_at, !on_deck) translate([0, 0, -wall - rfid_so + 0.05]) {
        color("#c8963c") for (k = [0, 2.5]) translate([-28 + k + 2, -17 + k, 0]) difference() {
            cube([34 - 2 * k, 34 - 2 * k, 0.4]); translate([1.2, 1.2, -1]) cube([34 - 2 * k - 2.4, 34 - 2 * k - 2.4, 3]);
        }
        color("#151515") translate([12, -5, 0]) cube([8, 8, 1.2]);
        color("#d4b000") translate([25, -16, 0]) cube([2.5, 32, 2.5]);
    }
    // custom PCB: DS3231 RTC with coin cell + level converter
    color("#1a3fb0") translate([pcb_at[0] + 6, pcb_at[1] + 8, floor_t + 7.6]) cube([38, 22, 2]);
    color("#c9c9c9") translate([pcb_at[0] + 25, pcb_at[1] + 19, floor_t + 9.6]) cylinder(d = 20, h = 3.2);
    color("#7a2fb0") translate([pcb_at[0] + 50, pcb_at[1] + 12, floor_t + 7.6]) cube([14, 13, 2]);
    // fans: blade disc + hub (intake: on its top face, the side that faces the top grille)
    translate([fan_in_at[0], fan_in_at[1], top_h - wall]) {
        color("#262626") cylinder(d = fan - 3, h = 0.4);
        color("#5a5a5a") cylinder(d = 11, h = 0.7);
    }
    // buzzer: mounting plate with its two screw ears (against the right wall)
    color("#1a1a1a") on_buzz() translate([-3, -(buzz_ear + 8)/2, -wall - 2]) cube([6, buzz_ear + 8, 2]);
    for (x = [W - wall - 10.4, W - wall - 0.4]) translate([x, fan_out_at[0], fan_out_at[1]]) rotate([0, 90, 0]) {
        color("#262626") cylinder(d = fan - 3, h = 0.8);
        color("#5a5a5a") translate([0, 0, -0.3]) cylinder(d = 11, h = 1.4);
    }
}

// ------------------------------------------------------------- output
if (PART == 1) shell();
else if (PART == 2) base();
else if (PART == 3) { base(); parts(); }
else {
    base();
    parts();
    %shell();          // see-through in the preview (F5)
}
