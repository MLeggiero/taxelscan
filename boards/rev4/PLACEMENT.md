# rev-4 placement analysis — what moves once the external ADC is gone

rev-4 is rev-3 with U8 (LTC1865L, 16-bit SPI ADC) and the four parts that
served only it removed: C8 (its 1 µF VCC bypass) and R10 / C10 / C26 (its
reference filter, tapped off ROW_VCC). Both sense banks are read by the
RP2354A's own 12-bit ADC. This file records what that frees, every placement
change considered, and which were made. The routed result and its checks are
in [ROUTING_STATUS.md](ROUTING_STATUS.md).

All coordinates are KiCad board coordinates in mm (y grows downward), as in
`../rev3/PLACEMENT.md`.

## What came off the board

| Part | Package | Courtyard | Where it was |
|---|---|---|---|
| U8 LTC1865L | MSOP-10 | 21.7 mm² | (150.09, 122.09), right of the analog chain under U5/U7 |
| C8 1 µF | 0402 | 1.7 mm² | (150.65, 126.19) |
| C10 10 µF | 0805 | 6.7 mm² | (150.98, 128.07) |
| C26 10 nF C0G | 0402 | 1.7 mm² | (152.60, 126.34) |
| R10 10 Ω | 0402 | 1.7 mm² | (150.44, 129.75) |
| **total** | | **33.5 mm²** | the block x 148–153, y 119–130 |

With them went five nets — `ADC_CONV`, `ADC_SCK`, `ADC_SDI`, `ADC_SDO`,
`VREF` — and their copper: **136.8 mm of track and 15 vias**. 41.6 mm of that
ran on In2.Cu, the +3.3 V plane layer (`ADC_SDO` 23.1 mm, `ADC_CONV` 18.5 mm),
and `ADC_CONV` alone was 59.3 mm with 9 vias, 40 % of its B.Cu run without a
reference plane and 0.18 mm from `VREG_LX` — the open item
`../rev3/ROUTING_STATUS.md` listed as "re-route with the ADC group". It does not
need re-routing; it no longer exists. The `U8_escape` rule area (0.10 mm
clearance around the MSOP-10's 0.5 mm pitch) went with U8, and so did the two
custom rules in `rev4.kicad_dru` that referred to it.

## The pin map decides the corner

The RP2354A's ADC inputs are GPIO26–29, pins 40–43, all on the east side of
the QFN — the side that faces the analog section. rev-3 used them like this:

| pin | GPIO | rev-3 | rev-4 |
|---|---|---|---|
| 40 | GPIO26 / ADC0 | unused — C35's VCORE stub at pin 39 shuts its escape | unused, same reason |
| 41 | GPIO27 / ADC1 | `USB_CC_OUT1` | **`ADC_A`** (bank A, COL_0–15) |
| 42 | GPIO28 / ADC2 | `RAIL_MON` | `RAIL_MON` |
| 43 | GPIO29 / ADC3 | `USB_CC_OUT2` | **`ADC_B`** (bank B, COL_16–31) |
| 29 | GPIO18 | `ADC_SCK` | **`USB_CC_OUT1`** |
| 31 | GPIO19 | `ADC_SDI` | **`USB_CC_OUT2`** |
| 27, 28 | GPIO16, 17 | `ADC_SDO`, `ADC_CONV` | unused |

The TUSB320's two status outputs are slow, static open-drain lines and can sit
on any GPIO; they take the bottom-right pins the SPI bus vacated, which are the
QFN pins nearest U13. `firmware/rev3_power/usb_power_policy.h` carries both maps
behind `TAXELSCAN_BOARD_REV`.

**RAIL_MON on pin 42 or pin 43?** Putting the 5 V monitor on the outer pin
(GPIO29) would let the two sensor banks escape side by side, and that looked
like it might make the corner easier. `tools/place_rev4.py --rail-pin 43` ran the
whole placement both ways on the same board:

| | bank B pin→C / R→C | bank A pin→C / R→C | RAIL_MON, all three legs |
|---|---|---|---|
| RAIL_MON on GPIO28 (pin 42), rev-3's | 3.95 / 0.53 mm | 3.96 / 0.80 mm | 11.66 mm |
| RAIL_MON on GPIO29 (pin 43) | 3.95 / 0.53 mm | 4.01 / 0.56 mm | 12.50 mm |

No difference worth a change, so RAIL_MON stays on GPIO28 and keeps rev-3's
ADC2 reading. As placed it also runs *between* the two sensor channels for the
whole length of the escape, a quiet DC line with 100 nF on it separating bank A
from bank B.

## Every move considered

### 1. The ADC-input cells (R21/C31, R22/C32) — moved, to the MCU

In rev-3 these sat hard against U8's CH0/CH1 at x 148.6–151.4, y 118. With U8
gone they would be 18–20 mm from the converter. The 1 nF C0G is the charge
reservoir for the sampling capacitor, so it belongs at the sampling pin, and
the 51 Ω beside it; the long run from U7 is then the op-amp's low-impedance
output, `AMP_A` / `AMP_B`, which is the right thing to make long.

The search (`tools/place_rev4.py`) tried every legal spot on a 0.1 mm grid in
four rotations — courtyards clear, 0.15 mm (0.10 mm inside `U9_escape`) to
other nets' copper, 0.25 mm to holes, the QFN fan-out kept clear, a GND via
within 1.2 mm of every GND pad and never in the pad — and *routed* the best
candidates, F.Cu only, keeping the shortest routed result. Each capacitor was
searched together with its resistor: placing C32 first and R22 afterwards had
boxed C32's pad in so that no position for R22 could reach it, which is what the
first version of the script did. A pair was accepted only if the resistor's
amplifier-side pad could still be reached from the east.

| Part | rev-3 | rev-4 | routed |
|---|---|---|---|
| C32 1 nF, bank B | (149.55, 118.08) 90° | (134.50, 118.50) 90° | U9.43 → C32.1 3.95 mm, GND via (134.50, 117.42) |
| R22 51 Ω | (148.59, 118.07) −90° | (136.00, 119.28) 180° | R22.2 → C32.1 0.53 mm |
| C31 1 nF, bank A | (150.47, 118.02) 90° | (134.50, 120.40) 270° | U9.41 → C31.1 3.96 mm, GND via (134.50, 121.48) |
| R21 51 Ω | (151.39, 118.06) −90° | (136.00, 120.92) 180° | R21.2 → C31.1 0.80 mm |

rev-3's ADC nets were 3.0 / 2.5 mm from reservoir to U8; rev-4's are 4.5 / 4.8 mm
in total from the 51 Ω to the MCU pin, of which 3.95 mm is the 0.10 mm trace from
the reservoir to the pin. At 0.10 mm over the unbroken In1 ground that is about
0.4 pF and 3 nH: nothing, against a 1 nF reservoir and a 2 µs conversion.

### 2. The rail monitor (R15, R16, C45) — moved, out of the sensors' way

It occupied exactly the space the sensor cells needed: x 134.4–135.7,
y 117.6–121.5, the first free ground east of the escape channel. It is a
100 k / 47 k divider with a 100 nF reservoir reading a DC rail, so it takes
second claim on the corner and was re-placed after the cells:

| Part | rev-3 | rev-4 | routed |
|---|---|---|---|
| C45 100 nF | (134.40, 121.00) −90° | (137.90, 119.80) 0° | U9.42 → C45.1 8.32 mm, GND via (138.98, 119.80) |
| R16 47 k | (135.70, 118.10) 90° | (137.90, 120.80) 0° | R16.1 → C45.1 1.70 mm, GND via (139.01, 120.80) |
| R15 100 k | (135.70, 120.50) −90° | (139.90, 119.10) 180° | R15.2 → C45.1 1.64 mm |

### 3. The pin map — changed (above)

### 4. U7 and its gain network — kept

U7 could move west into U8's space, toward the MCU, and shorten `AMP_A` /
`AMP_B` by ~8 mm. That is the wrong trade. U7's inputs are `SENSE_A` /
`SENSE_B`, the highest-impedance nodes on the board, and their length (12 /
16 mm from the mux commons) is what sets settling and pick-up; U7 sits where it
does to keep them short. The outputs it would shorten are low-impedance. U7,
R1/R2, R6–R9 and C7 stay exactly where rev-3 put them.

### 5. The muxes, row drivers, MCU and connectors — kept

Nothing about the change touches their reasons. J1/J2 are the FFC sockets the
sensor tails mate with, J3/J4 the harness, J5 the USB-C, J6 the debug header:
all mechanical interfaces at fixed offsets from their edges.

### 6. The outline — kept at 60.3 × 35.9 mm

rev-3's 17 September compaction stopped along x at the row
U13 / J5 / C37 / U14 / D4 / **C10** / J3. C10 is gone, but the same notes record
a what-if run without C10, R10 and C26 that gained only **0.9 mm** before the
MCU-to-mux routing became the limit — 1.5 % of the width, for a pass that
moves every part, track corner and via on the board and re-opens every
clearance it ever proved. Not worth it; the outline, the mounting points and the
connector positions are rev-3's.

### 7. U8's old block — left open

x 148–153, y 119–130 is now empty copper over the ground plane. Nothing on the
board wants to move into it; it is routing room, and the routing uses it (see
ROUTING_STATUS.md).

## After the verification: the regulator corner and the buck (7 October 2026)

`VERIFICATION.md` found the RP2354A's core regulator laid out unlike the RP2350
datasheet's Figure 26, and U12's 6 V input rating inside the reach of a USB-C
hot plug. `tools/fix_rev4.py` re-placed those two corners, and moved R30 / R31
for the re-route (below). Nothing else moved.

**The regulator corner** is now Raspberry Pi's RP2350A minimal design, part
for part. Each part sits at that design's offset from U9's pin 48, the
VREG_LX pin at (128.506, 117.201):

- C44 (CIN) at (0, −1.16) straddles pins 49 / 47; C19 (COUT) is at
  (0, −2.10); L1 is at (0, −3.75) with its dot to the west, over COUT's VCORE
  pad.
- CFILT (C43) and its 33 Ω (R35) are at (+2.2, −1.62) and (+2.2, −3.48).
- R23 / R24 go north-west of pins 51 / 52, at the minimal design's R7 / R8.
- C18 sits north-west of pin 54, serving pins 53 and 54, which are tied in
  the ring.
- C16 sits beside C43, and C17 at R35's +3.3 V pad.

C19, C44 and C43 went from 0603 to 0402: CIN and COUT on a project land with
the minimal design's 0.56 mm gap between the pads, so VREG_LX can run between
them.

**The buck** is the TPS62162 in a WSON-8, in TI's layout: C27 across VIN /
PGND on the left, C22 under the IC, L2 east of SW and C23 east of L2. R18 /
R19 are gone, since the TPS62162 has a fixed output.

| part | was | now | |
|---|---|---|---|
| C16 | (129.450, 115.600) 90° | (131.600, 115.580) 270° | |
| C17 | (128.099, 115.600) 90° | (131.950, 113.210) 0° | |
| C18 | (126.500, 115.600) 90° | (125.100, 116.100) 180° | |
| C19 | (124.000, 115.500) 180° | (128.506, 115.101) 0° | 0603 → 0402, wide gap |
| C43 | (130.850, 113.952) 90° | (130.706, 115.580) 270° | 0603 → 0402 |
| C44 | (123.090, 113.400) 180° | (128.506, 116.041) 0° | 0603 → 0402, wide gap |
| L1 | (126.050, 113.600) 0° | (128.506, 113.451) 0° | |
| R23 | (128.940, 113.500) 0° | (125.406, 113.250) 90° | |
| R24 | (130.950, 116.000) 0° | (126.406, 113.250) 90° | |
| R35 | (130.415, 111.496) 270° | (130.706, 113.720) 270° | |
| U12 | (106.425, 120.172) 180° | (106.400, 120.750) 0° | TLV62569 SOT-23-5 → TPS62162 WSON-8 |
| C22 | (109.196, 120.396) 90° | (105.600, 122.850) 0° | |
| C23 | (114.048, 121.398) 90° | (113.000, 122.550) 0° | |
| C27 | (103.540, 120.220) 270° | (104.420, 120.300) 90° | |
| L2 | (111.497, 120.906) 270° | (109.600, 121.300) 270° | |
| R18, R19 | (104.788, 123.264), (104.769, 122.333) | removed | |
| R30 | (120.850, 124.500) 180° | (143.000, 129.950) 180° | under U14 pin 3 |
| R31 | (120.850, 125.600) 180° | (145.700, 130.150) 180° | under U14 pin 4 |

**R30 / R31** are U14's EN pull-down and FAULT pull-up. rev-3 left them west of
U9, so `USB_BUS_EN` and `USB_PWR_FAULT` ran from U9's east side under the MCU
and back to U14 (38–52 mm, 4–6 vias). In this re-route the rip-up loop could not
fit both of them, plus `USB_CC_OUT2`, past U9's east side among the pre-routed
nets. Now each resistor sits under its pin, with a straight via to its plane;
U14's reference moved below them.

R38 and C46 kept their places with new values (0.68 Ω, 22 µF). J6 kept its
place with new pin nets. The new silkscreen is a `1` beside pin 1 of J3 and
J4 and `SC GND SD RUN` above J6.

## What did not move

Everything else: 97 of the 104 parts on a middle board are at rev-3's exact
position and rotation. One fiducial, FID2, moved 0.11 mm to keep its 0.6 mm
clearance from the re-routed `AMP_A`; ROUTING_STATUS.md has the detail.

The board was afterwards re-routed from scratch for straight 0 / 45 / 90-degree
routing (ROUTING_STATUS.md). That kept this placement exactly - every footprint
and pad at the same position and rotation, to the nanometre - and changed one
thing that touches a part: R27.2's ground via now sits in its pad, to let the
re-laid `BUS_N` pass over R27.
