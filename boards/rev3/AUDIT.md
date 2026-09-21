# rev-3 design audit — 10 September 2026

Scope: the current `rev3.kicad_sch`, `rev3.net`, `BOM.csv` and `rev3.kicad_pcb`
(54 × 36 mm, 105 parts, USB-power revision) checked against the intent in
`README.md`, `USB_POWER.md`, `PLACEMENT.md` and the part datasheets. Every
number below was measured from the files with a script, or read from the
datasheet named; nothing is copied from the older notes.

Tools run: `gen_rev3.py` (all 136 nets check out), `kicad-cli sch erc`,
`kicad-cli sch export netlist`, `kicad-cli pcb drc --schematic-parity
--refill-zones`, plus a geometry pass over the board file (pad-to-pad
distances, per-net length per layer, via counts, edge gaps between tracks,
zone fill areas).

## 1. State of the board

**The PCB is mid-reroute and cannot be built as saved.** KiCad DRC on the
current file:

| check | result |
|---|---|
| unconnected items | **30** |
| dangling tracks / dangling vias | 54 / 25 |
| copper clearance, shorts, crossings, edge clearance | 0 |
| silkscreen | 3 overlap, 5 over-copper (R1/R2 refs over U14 and R27) |
| schematic parity | 197 `/NAME` vs `NAME` label-prefix warnings, 2 SBU no-connects; **no real net conflicts** |

The 30 opens are not only in the new USB-power cluster. They include
`SENSE_A` (the bank-A sense node itself), `XIN`, `XOUT`, `VREF`, `ADC_B`,
`ROW_DATA`, `BUS_DI`, `BUS_RO`, `ADC_SCK`, `SWCLK`, `ADDR0`, `ADDR2`,
`BOOTSEL_SW`, four `+3.3V` gaps, and all of `USB_BUS_EN`, `USB_PWR_FAULT`,
`USB_ILIM_HI`, `USB_ILIM_LOW`, `USB_CC_OUT1/2`, `USB_CC2`, `USB_VBUS_DET`,
`+5V_USB`, `U13` GND. `ROUTING_STATUS.md` already says the pre-USB-power
board was the last clean checkpoint; this confirms it.

Planes are healthy: `In1.Cu` is one GND polygon (1586 mm²) with no signal
tracks on it; `In2.Cu` +3.3V is 1312 mm² in 12 fragments (the largest 1261
mm²), cut by 105 mm of `+5V_BUS`, 35 mm of `USB_BUS_SW` and 40 mm of +3.3V
track. All 105 footprints are on `F.Cu`.

## 2. What is right (checked, not assumed)

**Schematic and netlist.** The generator's 136 nets match KiCad's export of
the current sheet pin-for-pin; the 16 extra schematic nets are single-pin
no-connects. Every schematic value and footprint matches `BOM.csv`. ERC: 0
errors, 7 `lib_symbol_mismatch` warnings (the known KiCad-7-format cost).

**Row/column matrix.** 32 rows each reach a 595 output and J1; 32 columns
split 0–15 on U5, 16–31 on U6 and reach J2; `MUX_S0..S3` on GPIO 4–7;
595 `OE` low, `SRCLR` high, chain in order; U4 `QH'` open as intended.

**Front end topology.** Non-inverting gain of 6 per bank (`R6/R7`, `R8/R9`),
feedback closed at the op-amp output, 51 Ω then 1 nF at each ADC pin,
sense nodes carry exactly {pulldown, mux common, op-amp +}. VREF tapped from
`ROW_VCC` through 10 Ω / 10 µF + 100 nF (1.6 kHz corner), so the ratiometric
argument holds. `U8` MSOP-10 pin map (CONV 1, CH0 2, CH1 3, GND 4/5, SDI 6,
SDO 7, SCK 8, VCC 9, VREF 10) matches the wiring.

**MCU.** All IOVDD/DVDD/QSPI_IOVDD/USB_OTP_VDD pins on their rails, three
DVDD pins each with a 100 nF plus 4.7 µF at the inductor, `VREG_FB` on the
core rail, 3.3 µH on `VREG_LX`, 27 Ω USB series resistors (design guide value,
placed 3.4–4.1 mm from the pins), `ADC_AVDD` behind 10 Ω / 100 nF, RUN pulled
up, BOOTSEL through 1 kΩ (design guide value), QSPI data/clock left open as
the guide says for RP2354. UART1 TX/RX on GPIO 8/9, SPI0 RX/CSn/SCK/TX on
GPIO 16–19, ADC0 on GPIO 26: all valid function-select positions.
**Thermal pad: 9 GND vias inside the EP on the 1.13 mm grid** — the README's
"only four fit" is out of date.

**RS-485.** U10 DE tied to ~RE, U11 DE/DI/~RE strapped to GND, J3 and J4
wired identically, pairs on pins 3/4 and 5/6, 120 Ω positions across each.

**USB power (against the TI datasheets, text extracted today).**

- TUSB320LAI X2QFN pin map matches (CC1 1, CC2 2, PORT 3, VBUS_DET 4,
  ADDR 5, INT_N/OUT3 6, OUT1 7, OUT2 8, ID 9, GND 10, EN_N 11, VDD 12).
  PORT = GND is UFP/sink; ADDR floating is GPIO mode; VBUS_DET resistor spec
  is 855–887–920 kΩ, 887 kΩ fitted; VDD range 2.7–5 V; OUT1/OUT2 are
  open-drain and have 10 k pull-ups. INT_N/OUT3 and ID may float (audio
  accessory / DFP-only functions).
- **Datasheet Table 3 (GPIO mode): OUT1/OUT2 = H/H unattached, H/L default
  current attached, L/H 1.5 A, L/L 3 A.** `usb_power_policy.h` decodes
  exactly this (`attached = !(OUT1 && OUT2)`, `!OUT1 → High`, else Low if
  configured ≥ 500 mA). Correct.
- TPS2553 DBV pin map (IN 1, GND 2, EN 3, FAULT 4, ILIM 5, OUT 6) matches the
  project symbol; EN is active-high with VIH ≤ 1.1 V; 0.1 µF at IN (C42,
  1.9 mm away); R_ILIM 82.5 k and 41.25 k are inside the 15–232 kΩ range; the
  three limit equations in `USB_POWER.md` are the datasheet's verbatim
  (321 mA / 632 mA nominal). The R27/R28/Q1 two-level scheme is the
  datasheet's own Figure 24. Reverse comparator trips at 135 mV after 4 ms,
  which is the stated reason for D4, and FAULT deglitch is 7.5 ms, inside
  the firmware's 50 ms grace. UVLO 2.45 V.
- TLV62569 DBV pin map (EN 1, GND 2, SW 3, VIN 4, FB 5) matches; V_FB is
  0.6 V so 180 k / 40.2 k gives 3.29 V; R2 = 40.2 k is under the 200 kΩ
  limit; 2.2 µH and 22 µF are inside the datasheet matrix; 4.7 µF input is
  "sufficient for most applications"; EN tied to VIN is allowed (0–VIN).
- Q1 SOT-323 G/S/D = 1/2/3, D4 K/A = 1/2, D1/D2 anodes on the sources and
  cathodes on `+5V`: OR-ing, not paralleling.

## 3. Findings, most serious first

### 3.1 The bank-A sense node is routed the length of the board beside the RS-485 line

`SENSE_A` is the 10 kΩ-ish, highest-impedance node on the board. As routed
it is **99 mm long with 6 vias**, 48 mm of it on `B.Cu`, spanning x 107–150
mm. For **34 mm it runs parallel to `BUS_A` at a 0.15–0.18 mm edge gap** on
the same layer, plus 9 mm beside `BUS_B`, and **16.7 mm beside `SENSE_B` at
0.16 mm** (bank-to-bank crosstalk). `BUS_A` is a 20 Mbaud, 3 V-swing line.
`SENSE_B` is 104 mm with 11 vias, **19 mm of it on `In2.Cu`** (the power
layer `reserve_planes.py` was written to keep analog nets off), and on the
adjacent-layer test it lies over `ROW_6`, `ADC_A`, `VCORE`, `ROW_22` and
`MUX_S2` runs.

Cause: placement, not routing. The signal flow is J2 (top right) → U5/U6
(top right, centre) → **U7 at the far left** (109, 123) → R21/R22 (left) →
U8 (bottom centre). U5.1 to U7.3 is 44 mm as the crow flies; U6.1 to U7.5 is
26 mm. R1/R2, the sense pulldowns, sit at (128–130, 131) beside U14 and J5,
**28 mm from U5.1 and 23 mm from U7.3**, so the "node" is a 100 mm loop with
its three components at three corners.

Fix: move U7, R1, R2, R6–R9, R21, R22 into the band directly below U5/U6
(where U11, D1, D2, C9, R15, R16 are now), with R1/R2 at U7's inputs and the
mux commons reaching U7 in a few mm on `F.Cu` over the ground plane; put U8
beside that. This is the placement pass the README already calls for, and it
is the one change that most improves what the board exists to measure.

### 3.2 ADC_A is 137 mm long with 14 vias

`ADC_A` (R21 → C31/U8.2) should be a 2 mm stub with the capacitor at the pin.
It is **137 mm with 14 vias** and six dangling stubs (`ROUTING_STATUS.md`
recorded 109 mm / 6 vias; it has regressed). R21 is 14.5 mm from C31 because
R21/R22 were placed at the amplifier instead of at the ADC. The node after
the 51 Ω is low impedance, so this is less sensitive than 3.1, but it is the
16-bit input and it runs beside `SENSE_B` for 7 mm at 0.15 mm and over
`BOOTSEL` (the flash chip-select) and `SYNC_A` on the adjacent layer.

Fix: R21/R22 go against C31/C32 at U8 pins 2/3; the long run becomes
`AMP_A/B` (op-amp output, low impedance), and it should still be routed on
`F.Cu` over ground rather than the tour it takes now.

### 3.3 RP2350 regulator: two datasheet-required parts are missing

Per *Hardware design with RP2350* (Release 3, Aug 2026), sections 2.1–2.2:

- **VREG_AVDD (pin 46) "is very sensitive to noise and therefore needs to be
  filtered … an RC filter of 33 Ω and 4.7 µF".** rev-3 ties pin 46 straight
  to `+3.3V` with only C16 (100 nF) nearby. Add 33 Ω + 4.7 µF.
- **VREG_VIN (pin 49) needs "a large capacitor (C6 = 4.7 µF) close to the
  input".** rev-3 has only C17 (100 nF) on pin 49.
- The guide specifies a polarity-marked inductor, **Abracon
  AOTA-B201610S3R3-101-T (0806)**, and says that with other parts or layouts
  "you do so at your own risk", because a wrong-way-round inductor field
  upsets the output capacitor and the control loop. L1 is a generic 0805
  3.3 µH with a blank LCSC field. Specify the Abracon part and copy the
  guide's layout (it is in the RP2350 datasheet's *External components and
  PCB layout* section).
- VREG_PGND (pin 47) has its nearest GND via 0.9 mm away; the guide wants the
  switching return to go straight to that pin. Give C19's ground pad and
  pin 47 a shared via cluster.
- L1.1 to pin 48 is 4.1 mm and `VREG_LX` is 5.6 mm of 0.15 mm track (the
  switching node). Closer and wider.

### 3.4 Crystal circuit departs from the design guide with no compensating analysis

The guide "insists (well, strongly suggests)" the ABM8-272-T3 (12 MHz,
C_L 10 pF, ESR ≤ 50 Ω) with 15 pF each side **and a 1 kΩ series resistor on
XOUT** to prevent overdrive at 3.3 V IOVDD; any deviation "will require
extensive testing". rev-3: Y1 is LCSC C9002 (a 3225 12 MHz part; confirm its
C_L, believed 20 pF, and ESR), 15 pF caps, **no series resistor**. If C_L is
20 pF the 15 pF caps under-load it (7.5 pF + strays ≈ 10.5 pF), which pulls
the frequency; USB FS tolerates ±2500 ppm so it will probably enumerate, but
the drive-level risk is real. Layout: Y1 is 8–9 mm from pins 21/22, `XOUT` is
16.8 mm with a via, C20 is 3.5 mm from Y1.1. Move Y1 to U9's bottom edge with
C20/C21 flanking it, add R on XOUT.

### 3.5 Decoupling: the capacitors are close now, but half the pins do not go through them

Nearest same-net 100 nF pad 1 to each IC supply pin (all now ≤ 1.9 mm for U9,
≤ 3.7 mm elsewhere), and whether the pin reaches that pad on `F.Cu` copper
without a via:

| pin | rail | cap | mm | F.Cu path |
|---|---|---|---|---|
| U9.1 | +3.3V | C33 | 1.45 | no |
| U9.11 | +3.3V | C34 | 1.60 | no |
| U9.20 | +3.3V | C28 | 1.48 | no |
| U9.45 | +3.3V | C15 | 1.57 | no |
| U9.46 VREG_AVDD | +3.3V | C16 | 1.26 | no |
| U9.53/54 | +3.3V | C18 | 1.3 | no |
| U9.6 DVDD | VCORE | C24 | 1.92 | **no** (5.9 mm of F.Cu ending in two vias) |
| U9.50 VREG_FB | VCORE | C19 | 3.03 | no |
| U9.30, U9.38, U9.49 | +3.3V | C13, C14, C17 | 1.3–1.6 | yes |
| U9.23, U9.39 | VCORE | C25, C35 | 1.6 | yes |
| U9.44 | ADC_AVDD | C36 | 1.53 | yes |
| U5.24 / U6.24 | +3.3V | C29 / C15 | 3.7 / 3.0 | no |
| U7.8, U8.9, U10.8, U11.8 | +3.3V | C7, C8, C7, C6 | 1.5–1.9 | no |
| U1–U4 .16 | ROW_VCC | C1–C4 | 1.0–1.6 | yes |
| U12.4, U14.1, U13.12 | +5V, +5V_USB, +3.3V | C22, C42, C40 | 2.1, 1.9, 1.3 | yes |

"No" means the pin drops a via to the `In2.Cu` pour and the capacitor drops
its own via somewhere else, which `PLACEMENT.md` names as the pattern that
"passes DRC and leaves the loop equal to the distance between the two vias".
With +3.3V on the third layer the loop goes through ~1.2 mm of via each way.
It is not catastrophic for IOVDD, but VCORE (U9.6) and VREG_VIN (U9.49) are
the pins with real di/dt and should be wired pin → cap → via. Every cap's GND
pad does have a via within 0.4 mm, which is good.

Also: the design guide says pins 53/54 may share one cap (rev-3 does), and
that everything else wants one per pin (rev-3 does).

### 3.6 5 V buck: the loop is spread over 10 mm

TLV62569 layout rule: "input/output capacitors and the inductor as close as
possible to the IC". Measured: C27 (HF input) 3.2 mm from VIN with its GND
pad 5.1 mm from U12 GND; C22 2.1 mm; **L2 9.6 mm from the SW pin, `SW_NODE`
10.8 mm of 0.15 mm track**; C23 5.0 mm from L2.2; R18's tap 7.8 mm from
C23 (the FB sense point should be the output capacitor). At 1.5 MHz the SW
node is the board's loudest net and it sits 3 mm from the USB-C data lines'
route. Re-cluster U12, C22/C27, L2, C23, R18/R19 in a tight group; the
datasheet also suggests a feed-forward capacitor across R1 (6.8 pF for
R2 = 100 k) which is optional here.

### 3.7 Differential pairs are not routed as pairs, and KiCad cannot recognise them

| pair | lengths | same-layer edge gap min / median | notes |
|---|---|---|---|
| USB_DP_C / USB_DM_C | 23.3 / 20.6 mm, 2 vias each | 0.34 / 1.4 mm | 12–15 mm on `B.Cu`, whose reference is the fragmented +3.3V pour with `+5V_BUS` on it |
| USB_DP / USB_DM | 4.4 / 9.3 mm | 0.25 / 1.9 mm | |
| BUS_A / BUS_B | 88.6 / 57.4 mm | 0.40 / 3.5 mm | A runs beside SENSE_A (3.1) |
| SYNC_A / SYNC_B | 74.0 / 60.2 mm | 0.27 / 2.9 mm | 27 mm of SYNC_A on `In2.Cu` |

The project's netclass carries diff-pair width 0.2 / gap 0.25 but no net is
assigned to it, and the names (`_DP/_DM`, `_A/_B`) are not suffixes KiCad's
pair router recognises (`_P/_N`, `+/-`). The RP2350 guide asks for ~90 Ω
differential over "a solid, uninterrupted area of ground copper, stretching
the entire length of the track" and notes FS USB is forgiving. On JLCPCB's
7628 four-layer stack that is roughly 0.15 mm width / 0.15 mm gap on `F.Cu`
over `In1.Cu` (confirm with their calculator). Recommendation: rename to
`USB_D_P/_N`, `BUS_P/_N`, `SYNC_P/_N`, assign the diff-pair class, and route
all three pairs on `F.Cu` only. For RS-485 the on-board segment is
electrically short at 20 Mbaud, so this is about the sense-node adjacency
and tidiness more than impedance.

### 3.8 RAIL_MON has no capacitor and a 32 kΩ source

`R15/R16` (100 k / 47 k) present ~32 kΩ to the RP2350 ADC with no capacitor,
and on the adjacent-layer test `RAIL_MON` lies over `ADC_SCK` for most of its
12.5 mm. Add 100 nF at U9.40; it also makes the divider a proper charge
reservoir for the ADC's sample capacitor. (The README notes this monitor is
now redundant with the ratiometric VREF; keeping it is fine.)

### 3.9 BOM: dielectric and availability contradictions

- The BOM rule is "X7R, 25 V, never X5R", but the LCSC numbers on C9/C10
  (C15850), C23 (C45783) and C19 (C19666) resolve, as far as I recall, to
  Samsung X5R parts. Either relax the note (X5R is fine here; DC-bias
  derating is part-specific, not dielectric-class-specific) or change the
  numbers. Confirm on LCSC.
- C26 is "100 nF **C0G** 0402" (C1634). A 100 nF C0G does not exist in
  0402 at any useful voltage; confirm what C1634 actually is, and if C0G is
  wanted use 0603/0805 or 10 nF C0G beside C10.
- C31/C32 1 nF C0G (C1523) and C20/C21 15 pF C0G (C1548): confirm the
  dielectric on the ordered numbers.
- Seven lines still have no LCSC number (U8, U14, D4, L1, L2, J1/J2, thin-film
  R1/R2, R6/R8, R7/R9, C22, C37, C41, R27/R28, R29/R30, R34); `make_fab.py`
  hard-stops on these.
- U1–U4 use a `74HC595` symbol for `SN74LVC595A` and U10/U11 a `MAX3485`
  symbol for `SN65HVD75`: both pin-compatible, as the README argues.

### 3.10 Smaller layout items

- U13 (TUSB320) GND pins have their nearest GND via 1.5–2.9 mm away and
  pin 3 is open; the CC lines are 4.3 mm from J5 (fine).
- U11.6 is 9.4 mm from J3.5 but U10.6 is 39 mm from J3.3 and 14 mm from
  J4.3: the data transceiver's stub is long; a placement between the two
  connectors would halve it.
- ROW_CLK_MCU runs 33.6 mm on `In2.Cu`; BOOTSEL (~QSPI_SS to the internal
  flash) 43 mm on `In2.Cu` and 74 mm total with 7 vias for a switch that is
  10 mm from the pin. Both are digital and tolerable, but both cut the +3.3V
  pour and BOOTSEL is the aggressor under ADC_B.
- `+5V` (post-diode, into the buck) is 0.15 mm everywhere; at ~100 mA per
  board that is thermally fine (0.7 A capacity) but 0.3 mm costs nothing.
  `+5V_BUS` is 0.3–0.5 mm end to end and `+5V_USB` is 0.3–0.5 mm except the
  R34 stub: good.
- Y1's 12 MHz lines `XIN`/`XOUT` are on `B.Cu` in part, over the +3.3V pour;
  keep them on `F.Cu` over ground when Y1 moves.
- The silk warnings are all R1/R2 reference text over U14/R27 pads: they
  follow the placement move in 3.1.

## 4. Datasheet items still unverified

- **LTC1865L supply range and V_REF ≤ V_CC** at 3.3 V: the PDF could not be
  fetched today (analog.com timed out; mirrors refused). The part is sold as
  the "3 V" version and the schematic keeps V_REF ≤ V_CC by construction
  (both from `+3.3V`, VREF through 10 Ω + R5). Still read the table before
  freezing.
- Y1 (LCSC C9002) load capacitance and ESR (3.4).
- Dielectric of the LCSC capacitor numbers listed in 3.9.
- LCSC C144206 for U12 and C132554 for U13 map to the exact suffixes ordered
  (TLV62569DBVR and TUSB320LAIRWBR, not TUSB320RWBR).
- The rows-per-board throughput: 32 × 32 = 1024 conversions per frame on a
  150 ksps two-channel SAR is 6.8 ms of conversion alone plus 512 settling
  windows of ~6 µs; 80 fps is reachable but not with margin to spare. Worth a
  timing budget in the firmware plan before the LTC1865L is final.

## 5. Suggested order of work

1. Placement pass on the analog chain (3.1, 3.2): U7, R1/R2, R6–R9, R21/R22,
   U8 clustered under the muxes; then Y1 and the buck cluster (3.4, 3.6).
2. Schematic additions: 33 Ω + 4.7 µF on VREG_AVDD, 4.7 µF on VREG_VIN, 1 kΩ
   on XOUT, 100 nF on RAIL_MON; specify the Abracon inductor (3.3, 3.4, 3.8).
   `gen_rev3.py`'s rail checks will want the new parts registered.
3. Rename and class the three differential pairs; route them on `F.Cu`
   over ground (3.7).
4. Re-route with the analog nets and clocks confined to `F.Cu`; wire the
   di/dt supply pins through their capacitors (3.5).
5. Close the 30 opens, refill zones, fresh DRC, regenerate `fab/`.
6. Resolve the BOM part-number questions (3.9) before the assembly order.

## 6. State reached, 14 September 2026

Everything in sections 3 and 5 that lives in the schematic, netlist, BOM,
firmware pin map and docs is done and verified (`gen_rev3.py`: all 138 nets
check out; `check_faults.py`: 32 of 32; ERC 0 errors; netlist matches the
schematic). The PCB side did not converge on the 54 × 36 mm outline — four
rip-and-reroute cycles ended at 19 opens with shorts — so the board was
**grown to 70 × 38 mm** and fully re-placed (connectors keep their edge
offsets; see the README's "Grown board" note and `placement_grow.png`), then
routed with the staged local router + freerouting + maze router pipeline in
`audit-tools/`.

Native KiCad 10 DRC on `rev3.kicad_pcb`, zones filled:

| | |
|---|---|
| unconnected | **5** (U9.5 ROW_DATA, U9.12 BUS_DI, U9.34 ADDR2, U13.4 USB_VBUS_DET, C17.2 GND) |
| copper errors (clearance, shorts, hole clearance, dangling) | **0** |
| silkscreen warnings | 4 (U12/U9 outlines over adjacent cap pads, R1 reference over U5 pads) |
| tracks / vias | 4033 / 308 (108 GND stitches, 35 +3.3V plane drops) |

Against the audit findings (section 3):

| finding | now |
|---|---|
| 3.1 SENSE_A 99 mm beside RS-485 BUS_A | SENSE_A 12 mm, SENSE_B 16 mm, F.Cu only, no vias, nowhere near the pairs |
| 3.2 ADC_A 137 mm / 14 vias | ADC_A 3.1 mm, ADC_B 2.6 mm, F.Cu only |
| 3.3/3.8 VREG_AVDD filter, VREG_VIN cap, Abracon L1 | placed at the pins; L1→C19 4.7 mm direct, VREG_LX 4.7 mm |
| 3.4 crystal | XIN 10.3 mm, XOUT 3.4 mm + XOUT_MCU 3.9 mm through R36, all F.Cu |
| 3.5 supply pins bypassing their caps | every RP2354A supply pin routes straight into its 0201; pad 1 at the pin (0201 flip bug fixed) |
| 3.6 buck loop | U12, L2, C22/C27, C23/C41 in one 10 × 6 mm block, SW node 4.5 mm |
| 3.7 differential pairs named and classed | done; coupling see below |
| 3.9 BOM / dielectric | done in BOM.csv |

Not yet good, in order of importance:

1. The five sealed pins above. They need a human with the interactive
   router: rip the neighbouring 0.15 mm trace, escape the pin at 0.1 mm inside
   the rule area, put the neighbour back.
2. `BUS_P` (62 mm F.Cu) and `BUS_N` (55 mm B.Cu) are not coupled — the N hug
   failed and it went to the other side. `SYNC_P/N` run 46 / 44 mm on In2 and
   cut the +3.3V pour. Both pairs want re-laying by hand as pairs on F.Cu
   along the bottom lane, which is what the lane was left free for.
3. `USBC_D_P/N` run 16 / 13 mm on B.Cu from R23/R24 to J5 (references the
   +3.3V pour, not ground). Short and 12 Mbit/s, so tolerable, but F.Cu is
   available now that the corridor at x 136–138 exists.
4. `VCORE` carries 22 mm on In2 (the C25 trunk) — a slot in the +3.3V pour
   under the MCU; DRC reports the pour still contiguous.
5. `+5V` runs 83 mm on B.Cu along the bottom edge from D1 to D2/R15: DC,
   0.3 mm, fine, just long.
6. Fabrication outputs (`fab/`, `GERBER OUTPUT/`) predate all of this;
   regenerate with `make_fab.py` after the hand pass.

## 7. State reached, 15 September 2026

The hand pass §6 called for was done with the exact-geometry router and stage
scripts in `audit-tools/` (second table of its README) rather than the interactive
router. The result is installed as `rev3.kicad_pcb`; the 14 September board is
`backups/implement-2026-09-15/rev3.kicad_pcb.before`. Native KiCad 10 DRC, zones
filled, schematic parity:

| | |
|---|---|
| unconnected | **0** |
| copper errors (clearance, shorts, hole clearance, dangling) | **0** |
| silkscreen warnings | 2 (U12 outline over C27, U9 outline over C33) |
| schematic parity | 199 warnings, every one the leading-`/` net-name difference |
| tracks / vias | 3243 / 346 (109 GND stitches, 35 +3.3V plane drops) |
| copper per layer | F.Cu 1652 mm, In2.Cu 289 mm, B.Cu 713 mm, In1.Cu 0 |

Against §6's list:

1. **The five sealed pins are routed.**
   - C17.2 has a 0.1 mm link to C18.2.
   - USB_VBUS_DET went in after USB_CC1/2 moved aside.
   - BUS_DI went in after R18 was turned round (FB 12.2 → 2.4 mm).
   - ROW_DATA went in after R3 and R4 swapped places.
   - ADDR2 is routed as part of a rebuilt U9 corner. The corner had fewer exits
     than pins until one of ADC_SDO's vias moved; its escapes are laid by hand
     (`audit-tools/strapgeo.py`).

   R18, R3 and R4 are same-value, non-polar parts, so the netlist is unchanged.
2. **The pairs are re-laid by hand as coupled pairs**, on F.Cu with B.Cu crossings,
   nothing on In2. BUS is 65.8 / 66.1 mm (skew 0.34 mm, 2 vias per conductor);
   SYNC is 60.7 / 62.4 mm (skew 1.65 mm, SYNC_P 4 vias and SYNC_N 2). The crossings
   are forced: J3 and J4 are mirror images, so each pair swaps P/N once and the two
   pairs cross once.
3. **`USBC_D_P/N` are unchanged** on B.Cu (22.1 / 18.0 mm): ADC_SCK, ADC_SDI and
   ADDR1 now cross the F.Cu corridor.
4. **`VCORE`** keeps its 21.7 mm In2 trunk.
5. **`+5V`** is 91.8 mm (B.Cu 54.9, F.Cu 36.8) with 9 vias, still long.
6. **`fab/` is regenerated from this board.** The bare-board gerbers are complete;
   assembly is still blocked on 24 BOM lines without an LCSC number
   (`fab/NEEDS-PARTS.txt`).

Found on the way:

- **Signal hops on In2 cut the +3.3V pour into islands** while every connection
  still passed DRC. At one point the MCU's eight plane drops sat on a 21 mm²
  island. MUX_S1 and USB_ILIM_LOW were re-routed to fix it.
  - The main piece now holds 28 of the 35 +3.3V vias; the 14 September board had 30.
  - Two new islands remain (10.5 and 3.7 mm², three vias). The In2 hops of
    ADC_SDO, ADC_CONV, ADDR1, USB_BUS_EN and USB_PWR_FAULT cut them off, and none
    of those nets routes on F.Cu/B.Cu alone.
- **ADC_CONV runs 59.6 mm with 9 vias round the MCU**, and ADC_SDO has 23 mm on
  In2. These are the routes U9's corner could take; they are worth revisiting.
- **The DRC false alarm.** The first automated passes on this board reported
  ~500 clearance errors that were not real. pcbnew had written a default project
  file beside a board saved under a new name, and kicad-cli read it instead of
  the real one. `audit-tools/README.md` describes the guard.

### 7.1 Design review, 16 September 2026

The board was reviewed end to end — analog chain and acquisition timing, power
path and harness budget, digital pin map against the firmware, and layout for
what DRC cannot see. Four blockers came out of it, **none in the routing**:

1. **No rev-3 acquisition firmware.** `scan.cpp` reads the RP2350's internal ADC
   on GPIO26/27; this board puts the signal on an external LTC1865L on SPI0, with
   GPIO26 unconnected and GPIO27 carrying `USB_CC_OUT1`. The rev-1 sketch also
   drives GPIO22/23 — here `ADDR2` and the open-drain `USB_PWR_FAULT`. Do not
   flash it to a rev-3 board.
2. **U7's order code did not match its footprint.** `TLV9062IDR` is SOIC-8 on
   1.27 mm; the board has MSOP-8 on 0.65 mm. Corrected in `gen_rev3.py` to the
   DGK (VSSOP-8) part; its LCSC number needs confirming before ordering.
3. **The RP2354A's exposed pad had no via** — the whole chip's ground return was
   one 0.10 mm trace, against this document's own "9 GND vias inside the EP".
   Fixed: seven vias now sit in the pad.
4. **Assembly remains blocked** on 25 BOM lines without an LCSC number.

Also fixed in the board: both 5 V rails moved off In2, where 0.5 oz copper at
0.30 mm carries only ~0.3 A against up to 0.7 A of chain current; `USB_BUS_SW`
widened; three fiducials added; 32 reference designators made visible.

Also corrected in `gen_rev3.py`: the feedback divider now has a **value**
assertion (topology alone accepted E24 parts that would put the rail at 3.62 V,
the MCU's absolute maximum), C23 and C37 carry voltage ratings, L2 carries a
saturation-current requirement, and the address-strap comment describes the
solder jumpers rather than the DIP switch they replaced.

What the review left open is tabulated in
[ROUTING_STATUS.md](ROUTING_STATUS.md) — the power budget (eight boards need a
1.5 A source; five or six on a default port), the harness drop (373–504 mV, not
206 mV), 60 vias inside SMD pads, the 0.10 mm annular ring, the crystal's
clearance to 5 V, and the fact that at 80 fps the chain is noise-limited near
11.9 noise-free bits rather than 16.

### 7.2 Compaction, 17 September 2026

The routed board was pushed together from 70 × 38 mm to **60.3 × 35.9 mm**
(18.6 % less area). It stays single-sided with the USB-C connector on the board,
and nothing was re-routed. `audit-tools/compact_lp.py` treats each move as a linear
programme over how far every part, via and track corner shifts along one axis.
Each pad, via, hole and courtyard is an exact shape, and each track is two round
ends plus a band that may turn a few degrees. Clearance is checked row by row at
every corner of either shape, so the result either holds everywhere or the
programme refuses it. 35 passes, toward each edge in turn, converged.

Verified in place: 0 unconnected, 0 copper errors, schematic parity only the
net-name prefixes, the +3.3V pour's main piece still holding 27 of 35.

Two things the rules alone would have got wrong, and are now constraints:

1. **Pad-specific clearances.** The fiducials carry a 0.6 mm local clearance and
   a 0.5 mm mask margin; a pass that used only the netclass clearance ran
   +5V_BUS through FID1's keep-out.
2. **The FFC connectors' board-edge reference.** J1/J2's footprints mark the
   board edge on F.Fab; the original placement had those marks exactly on the
   edge. Compaction had let the top edge slide 0.38 mm under the connectors,
   so the edge was put back on the marks and later passes held J1/J2 to it.

Found on the way: the vias inside SMD pads were 62 on the 16 September board,
not the 60 the fab notes said. Compaction made it 63 (a GND via now in C10's GND
pad), and `make_fab.py` says 63. The same notes still described 5 V runs on In2
widened to 0.6 mm, which stopped being true on 16 September; corrected.

### 7.3 Review of parts, circuit and JLCPCB manufacture, 17 September 2026

The compacted board was checked again from the start: every part, the design
intent, every connection, and whether JLCPCB can fabricate and assemble it.
Three checks ran independently:
- a pin-by-pin datasheet review of every IC;
- an LCSC/JLCPCB lookup of every BOM line;
- a survey of JLCPCB's current published limits.

The board's own geometry was measured against those limits, and every part
number used below was looked up again before it went into `gen_rev3.py`. The
owner added a requirement during the review: **all components very high quality,
all resistors thin film** ("silicon film").

**The BOM would have built dead boards.** Ten of the 28 LCSC numbers that were
filled in ordered something else:

| Ref | Was | Which is | Consequence | Now |
|---|---|---|---|---|
| U1–U4 | C6062 | 74LVC138 3-to-8 decoder, 0 stock | outputs fight ROW_VCC and the MCU; no rows driven | C52287685 TI SN74LVC595APWR |
| U12 | C144206 | MIC2954 LDO, SOT-223 | does not fit; no 3.3 V | C141836 TI TLV62569DBVR |
| R18 / R19 | C25811 / C25752 | 200k 0603 / 12k | divider asks for ~10.6 V: the buck runs flat out and puts VIN on the 3.63 V-max MCU | C852573 / C852775 Yageo thin film |
| J3, J4 | C160404 | JST SM04B-SRSS-TB, 4-pin 1.0 mm | harness connectors unusable | C133065 JST SM06B-GHS-TB |
| D1, D2 | C261671 | does not exist; SS0520 is not made in SOD-323 | nothing feeds +5V | C179428 Nexperia PMEG2005AEA |
| R10, R26 | C25104 | 330R | VREF and ADC_AVDD sag with load | C705629 Yageo thin film 10R |
| R16 | C25819 | 47k in 0603 | wrong package | C728561 Yageo thin film |
| C31, C32 | C1523 | 1 nF X7R | the ADC charge reservoir must be C0G | C437434 Murata C0G, AEC-Q200 |
| SW1 | C139797 | ALPS SKRPACE010 | does not fit the B3U footprint | C231329 Omron B3U-1000P |
| D3 | C72043 | discontinued | not placed | C125098 Lite-On LTST-C191KGKT |

`gen_rev3.py` checked the value text, not what the number buys. All 71 lines now
carry numbers that were looked up on 17 September:
- **Resistors:** thin film at 0.1 % / 25 ppm/°C, Vishay TNPW and Yageo RT, with
  the Vishay thin-film 0R jumper for R5.
  - R34 is the one exception. No 0402 thin-film 887k exists, so it is now 866k
    at 0.5 % / 50 ppm, still inside TI's 855–920k window.
- **Capacitors:** Murata, Samsung and TDK; C0G on the crystal, VREF and ADC
  reservoir; AEC-Q200 where it cost nothing.
- **Other parts:**
  - L2: Murata DFE322520FD, Isat 5 A; the TLV62569 current limit is 3 A minimum.
  - Genuine Hirose, JST, Omron, Abracon, Würth and Nexperia parts.
  - U8 is the −40 to 85 °C LTC1865LIMS.

**Circuit findings** (the pin maps of every IC match their datasheets; these are
what does not):

1. **L1 was the wrong way round — fixed.** The RP2350 datasheet (section 6.3.8,
   Figures 23 and 25) puts the inductor's polarity dot on the output end; current
   leaves through the marked end. The footprint's pad 1 is the dot, and pad 1 was
   on `VREG_LX`. L1 is turned 180° with its pin nets swapped; the pads are
   identical, so no copper moved. `check_faults.py` gained a probe for it.
2. **C8, the LTC1865L's VCC bypass, is now 1 µF — fixed.** The datasheet asks
   for at least 1 µF, and the nearest bulk capacitor is 33 mm away.
3. **R11/R12 were assembled on every board — fixed in `make_fab.py`.** Eight
   terminators put 15 Ω across each pair, against the SN65HVD75's 54 Ω minimum.
   The default files leave them out; `-end` files include them.
4. **Nothing could drive the SYNC pair — fixed in 7.4.** U11's DE and DI are strapped to
   GND on every board so that a board cannot desynchronise the harness, but
   `USB_POWER.md` makes the master one of these boards. Either give U11.3 a GPIO
   with a pull-down (a circuit and layout change), or carry frame-start on the
   data bus.
5. **Hot plug may exceed the TLV62569's 6 V absolute maximum — open.** An LC
   estimate of a 1 m USB cable into ~4 µF of ceramic peaks at 6.2–7.3 V; from a
   live harness, 5.5–6.7 V. Measure U12 VIN; add a damped bulk capacitor if it
   overshoots.
6. **20 Mbaud is not available on the hardware UART — open (firmware).** The
   RP2350 UART tops out at 9.375 Mbaud; the README's bandwidth table needs PIO
   or a lower rate.
7. **Minor:**
   - no pull-down on BUS_DE and no pull-up on BUS_RO;
   - the 595 outputs are undefined at power-up (clock zeros first);
   - mux leakage is up to ±8 µA hot, not ±1 µA;
   - J6's order differs from Raspberry Pi's debug connector, with no 100 Ω
     series resistors;
   - no ESD protection on USB or the mat;
   - GPIO1 is the Pico SDK's default UART0 RX;
   - the U7 value field said TLV9062IDR (fixed).

**JLCPCB fabrication**, measured on the board against their published limits:

| item | board | JLCPCB | verdict |
|---|---|---|---|
| trace / space | 0.10 / 0.1007 mm (escape areas) | 0.09 / 0.09; +20 % below 3.5 mil | OK, no surcharge |
| via | 0.30 / 0.50 mm (314), 0.30 / 0.60 (33) | pad ≥ hole + 0.10 | OK |
| component holes | J6 1.0 / 1.7 mm (ring 0.35); J5 slots 0.6 mm (ring 0.20) | ring ≥ 0.15 (0.20 recommended); plated slot ≥ 0.35–0.65 | OK; the 0.6 mm slots are JLCPCB's own USB-C part |
| hole to copper | ≥ 0.35 mm (PTH), ≥ 0.25 (NPTH) | ≥ 0.28 / 0.20 | OK |
| copper to edge | 0.27 mm to the outline centre line | ≥ 0.20 | OK |
| pad-to-pad (mask dam) | 0.15 mm (U9) | 0.15 pad-pad, dam ≥ 0.10 | OK |
| silkscreen | lines 0.12 mm, text 0.8 / 0.12 mm | line ≥ 0.15, text ≥ 1.0 (0.8 high-precision) | thin; may print faint or be clipped |
| vias in pads | 153 holes wholly (19) or partly (134) in pad openings | filled and capped is paid on 4 layers, not automatic | **order Epoxy Filled & Capped** |

**JLCPCB assembly:**
- **Standard PCBA is required.** The 0201 capacitors rule out Economic.
  JLCPCB adds rails. J1/J2 overhang the edge by 1.5 mm, so expect a fixture fee.
- **Placement preview.** Check L1's dot and pin 1 of U9, U12 and U14. Their
  silkscreen marks touch neighbouring pads and may be clipped, and JLCPCB orients
  by silkscreen.
- **Part spacing** is tighter than JLCPCB's recommended minimum in 28 places:
  - the MCU's 0201 bypass capacitors at 0.48–0.87 mm from the QFN (1.0 recommended);
  - U1–U4 at 0.39 mm (0.5);
  - eight chip-to-SOP pairs at 0.34–0.40 mm (0.4).

  These are recommendations, not limits, but they are where bridging would show.
- **Tombstoning risk.** 42 0201/0402 parts have a via hole on one pad only;
  filled vias remove most of it.
- **Low stock** on the day:
  - J1/J2: 44 pieces, enough for 22 boards;
  - U8: 23;
  - R34: 550; R23/R24: 2800; R21/R22: 3700; U13: 902.
- **Cost.** Every part is now Extended: 44 of 47 unique parts. Standard PCBA
  charges the same loading fee for Basic and Extended, so the fee barely moves.
  Assembly fees come to about $112 for five boards, and parts to about $33 a
  board, of which U8 is $18.51.

**What changed in the files:**
- `gen_rev3.py`: parts, the L1 pins and three checks.
- `check_faults.py`: 32 faults plus the baseline, all behaving.
- `layout_schematic.py`: L1 drawn turned.
- Regenerated: `rev3.net`, `BOM.csv`, `rev3.kicad_sch` (138/138 nets, ERC 0
  errors) and `rev3.pdf`.
- `rev3.kicad_pcb`: L1 turned and its pad nets swapped, six value fields, L1's
  label moved. DRC 0 unconnected / 0 copper errors; parity only the net-name
  prefixes.
- `make_fab.py`: end-board set, order notes, corrected via notes.
- Backups are in `backups/review-2026-09-17/`.

### 7.4 SYNC driver, 17 September 2026

Finding 4 of 7.3 is fixed in hardware. The master is one of these boards, so the
sync driver cannot be strapped off on all of them. It is now switchable, and still
off unless firmware says otherwise:

- **U11.3 (`DE`)** is on GPIO13, the new net `SYNC_DE`.
- **R37**, 10 kΩ thin film (the same reel as every 10k), pulls `DE` low. A board in
  reset, a blank board and every non-master board is receive-only.
- **U11.4 (`DI`)** stays on GND, so a driven pair can only pull SYNC low. The
  frame-start is a low pulse on an otherwise failsafe-high pair.
- **U11.2 (`~RE`)** stays on GND, so every board listens, the master included.
  The master can time its own scan off the edge it receives.

Why hardware rather than a frame-start message on the data bus: the data bus
carries no more either way, but the sync edge now arrives independently of bus
traffic. With full maps at 60 Hz the data bus is busy for most of each frame. A late
poll cycle then delays delivery, not sampling.

- **Board.**
  - R37 is at (111.40, 124.60), rotated 90°, beside U11; its ground is a short
    trace to U11.4's via.
  - `SYNC_DE` runs 14.4 mm at 0.1 mm with 2 vias and 5 mm on In2.
  - The +3.3V pour keeps 27 of 35 on its main piece, now 1669 mm², and GND keeps
    one piece.
  - DRC: 0 unconnected, 0 copper errors; parity only the net-name prefixes.
  - A clean F/B route would have meant moving BUS_DE, BUS_DI and MUX_S1.
- **Generator.**
  - The check now requires `DI` on GND, `~RE` on GND, and `DE` on its GPIO with a
    pull-down of 10k or less to GND.
  - `check_faults.py` probes a missing pull-down and `DI` taken off GND: 33
    faults plus the baseline, all behaving.
- **Counts.** 139 nets, 111 parts, 501 connections; 348 vias.
- **Firmware.** The master drives GPIO13 high for the frame-start pulse, and all
  boards take the falling edge on `SYNC_OUT` (GPIO11). Keep GPIO13 low, or leave
  it an input, on every other board.
- **Backups.** The board before this change is in `backups/sync-de-2026-09-17/`.

## 8. Independent verification, 21 September 2026

The whole design was checked again from the files, without trusting any number
in this document, with the question "does rev-3 do at least what rev-1 does for
noise, accuracy and repeatability, and can the fab package be ordered as is".

**Re-run and passing on the saved files:**

| check | result |
|---|---|
| `gen_rev3.py` | 139 nets, 111 parts, 501 connections, all checks pass |
| `check_faults.py` | 34 of 34 behave (33 injected faults caught, baseline clean) |
| `kicad-cli sch erc` | 0 errors, 7 `lib_symbol_mismatch` warnings (the KiCad-7-format cost) |
| schematic export vs `rev3.net` | 139 nets, identical pin membership on every one |
| `kicad-cli pcb drc --schematic-parity --refill-zones --all-track-errors` | 0 unconnected, 0 copper errors, 6 silkscreen warnings, parity only the leading-`/` names plus J5's two SBU no-connects |
| `fab/` vs the board | gerbers and drills regenerated and diffed: identical except the timestamp; `rev3-bom*.csv` identical to `BOM.csv`; every CPL position matches the board to 1 µm; all 72 BOM lines carry an LCSC number |

**Measured on the board (own geometry pass, not the older tools' numbers):**

- The analog chain is on `F.Cu` over the unbroken ground plane with no vias:
  `SENSE_A` 11.5 mm, `SENSE_B` 14.8 mm, `ADC_A` 3.0, `ADC_B` 2.5, `VREF` 6.5,
  `XIN` 10.2, `XOUT` 3.2 (`AMP_B` alone has a 4.5 mm `B.Cu` hop, after the
  gain stage where the node is low impedance). Under each of these the GND fill
  is solid to within 0.1 mm² per net.
- Every piece of copper within 0.30 mm of an analog net on the same layer was
  listed. Beside the sense nodes there was exactly one digital item: the
  `STATUS` LED via, 0.101 mm from U7 pin 5. Everything else beside `SENSE_*`,
  `GAIN_*`, `AMP_*` and `ADC_*` is the analog chain itself, ground or +3.3V.
  `VREF` runs 16 mm at 0.15 mm beside `+5V_BUS` (DC, filtered by R10/C10) and
  `XIN`/`XOUT` sit 0.16–0.18 mm from `+5V` for 11 mm (DC; the 16 September
  review's crystal item, unchanged).
- Power widths: `+5V_BUS`, `+5V_USB` 0.30 mm on the outer layers only,
  `USB_BUS_SW` 0.40 mm, `+5V` 0.20–0.30 mm, `SW_NODE` 0.30 mm, `VREG_LX`
  0.10–0.20 mm and 4.7 mm long. Nothing carrying 5 V is on `In2.Cu`.
- Decoupling: every RP2354A supply pin reaches its 0201 on `F.Cu` without a
  via at 1.1–1.5 mm, each cap's GND pad has a via within 0.06–0.76 mm
  (C17's shares C18's, 1.16 mm). U9.50 `VREG_FB` reaches C19 at 3.4 mm through
  a via, as before.
- The +3.3V pour: 27 of 35 plane drops on the main piece. The eight on islands
  belong to three groups that also reach the main piece through `F.Cu`/`B.Cu`
  track (U9.30/U13/R32/R33 via one via and 12.7 mm of track; U9.11/R20/R31 via
  one via and 6.8 mm; U9.38/45/49/53 via three vias and 16.7 mm). Every one has
  its 100 nF at the pin, so this is acceptable for IOVDD; it is not a fault.
- JLCPCB limits re-checked against their current capability page: trace/space
  0.10/0.10 mm outer and 0.09 inner against our 0.1007 minimum; via 0.30/0.50
  and 0.30/0.60 against their 0.15/0.25 minimum; via hole-to-hole 0.25 mm
  same-net and 0.35 different-net against 0.2; NPTH-to-track 0.256 mm against
  0.2; copper to edge 0.27 mm against 0.2; mask dam 0.15 mm against 0.10;
  plated slots 0.6 mm against 0.35. Silkscreen strokes are 0.12 mm and text
  0.8 mm against their 0.15 / 1.0 recommendation: it may print faint, and
  nothing electrical depends on it.

**Datasheet items closed:**

- **LTC1865L** (18645lfs): V<sub>CC</sub> 2.7–3.6 V; the MSOP-10 reference
  input "can operate with reference voltages from 1 V to V<sub>CC</sub>";
  inputs to V<sub>CC</sub> + 0.05 V; f<sub>SCK</sub> 8 MHz; t<sub>CONV</sub>
  4.66 µs max; 12 pF sample capacitor; "bypass the V<sub>CC</sub> and
  V<sub>REF</sub> pins directly to the analog ground plane with a minimum of
  1 µF" — C8 is 1 µF and C10 is 10 µF. The 3.3 V supply and 3.3 V ratiometric
  reference are inside the table. Channel bits: S/D=1, O/S=0 selects CH0,
  O/S=1 selects CH1, single-ended to GND, as the firmware plan says.
- **RP2350 datasheet 6.3.8**, read from the figures rather than the text: in
  Figure 23 the orientation indicator sits on the pad that feeds
  C<sub>OUT</sub> and the V<sub>OUT</sub> vias, and Figure 25 shows current
  entering at "+" and leaving at the marked "−" end. The dot goes on the
  output. L1's pad 1 (the dot) is on `VCORE`: correct since 17 September.
  Figure 24 shows a layer-2 cut-out under the whole inductor and the LX trace,
  which this board did not have; it does now (below).

**Changed in the board today, each on a scratch copy, DRC-clean, then installed
with a backup in `backups/status-via-2026-09-21/`:**

1. The `STATUS` via at (152.48, 122.39) moved to (151.98, 122.39), the
   `F.Cu` and `B.Cu` segments that met it shortened and re-aimed; via size
   0.5 mm. Gap to U7.5 0.10 → 0.65 mm. Checked with the exact-geometry model
   before and by KiCad after.
2. Rule area `VREG_LX_cutout` on `In1.Cu`, no copper pour, over L1's body and
   pads, the `VREG_LX` trace and U9 pin 48, dilated 0.15 mm: 10.3 mm², x
   124.14–128.80, y 112.34–117.74. The GND fill went from 1850.9 to
   1841.7 mm² and is still one piece; no GND via lies in the hole (a `VCORE`
   and a `MUX_S3` via pass through it, which is fine).

`fab/` was regenerated after both.

**Still open after section 8:** hot-plug overshoot at U12, the buck input
capacitors 4.1–4.4 mm from U12's VIN pin, ADC_CONV's 59 mm route, no ESD parts,
the power budget for eight boards, and the firmware. Section 9 closes the first,
second and fourth.

## 9. Protection, damping and the buck input, 21 September 2026

The owner asked for the open items to be fixed before moving on. Three are
board changes and are done; two are not board defects and are recorded as such.

### 9.1 What was decided, and why

| item | decision | reason |
|---|---|---|
| Hot-plug overshoot | **Series RC damper** `R38` 1 Ω + `C46` 10 µF across `+5V_USB` | The ring is cable inductance (~1 µH/m) into ~6 µF of ceramic; a bare capacitor lowers the frequency but adds no loss, and no TVS clamps below 6 V. The damping resistance wants to be near √(L/C) ≈ 0.4 Ω; 1 Ω is the nearest stocked thin-film value (0603; no 0402 1 Ω thin film exists). It adds 10 µF to the attach capacitance, ~16 µF total against USB's 10 µF guideline, which the owner accepted over an undamped regulator input. Bring-up still measures U12 VIN |
| USB ESD | **`D5` USBLC6-2P6** on D+, D− and VBUS, connector side of R23/R24 | The RP2354A's USB pins are HBM-rated only. The two-line array with pass-through pins keeps the lines unstubbed; SOT-666 was the only package with a pocket 3 mm from J5 (no SOT-23-6 spot within 8 mm without moving parts) |
| RS-485 ESD | **none** | SN65HVD75 datasheet, ESD Ratings table: bus pins ±12 kV IEC 61000-4-2 contact and air, ±4 kV IEC 61000-4-4 EFT, ±15 kV HBM. An SM712 would add nothing an indoor robot harness needs |
| CC1/CC2 ESD | **none** | TUSB320LAI: ±7 kV HBM on all pins, no IEC rating. A four-line array would not fit beside J5; noted as residual |
| Mat FFC lines | **none** | The 1 MΩ film is the series element: an 8 kV discharge through it is 8 mA into a 2 kV-HBM pin |
| Buck input loop | **`C27` moved** to 1.8 mm from U12 VIN (pad 1 on the VIN trace, own GND via) | It sat 4.1 mm of trace beyond the EN pin. C22 (4.7 µF) stays at 4.4 mm; the 100 nF is the part that matters at the switching edge |
| Eight boards on a 500 mA port | **not a board fault** | The budget (282–367 mA in low mode) is the TPS2553's limit times the USB 2.0 grant. A 1.5 A or 3 A Type-C source is detected by U13 and the firmware raises the limit to 632 mA. Fewer boards draw less. Nothing to change on the board |
| rev-3 firmware | **separate work** | `firmware/rev3/PLAN.md` is the specification; milestone M0.1 needs CMake and Ninja installed, which needs the owner's go-ahead |

### 9.2 Netlist and schematic

`gen_rev3.py` gained D5, R38 and C46 (140 nets, 114 parts, 511 connections) and
checks that the TVS is on the connector side with both pass-through pins per
line, that VBUS clamps to GND, that `USB_SNUB` carries exactly R38.2 and C46.1,
and that the values are 1 R / 10 µF. `check_faults.py` probes the TVS moved to
the MCU side and the damper resistor shorted out: 36 of 36 behave.
`gen_schematic.py` rebuilt the sheet with the same UUIDs on every old symbol:
140 of 140 nets match, ERC 0 errors, 7 `lib_symbol_mismatch` warnings as before.

### 9.3 Board

Done on scratch copies with the exact-geometry tools, then installed
(`backups/esd-2026-09-21/`).

- **C27**: old stubs and via removed; placed at (103.54, 120.22) rotated so
  pad 1 lies on the existing 0.3 mm `+5V` trace 1.8 mm from pin 4; pad 2 to a
  new ground via at (103.54, 121.35).
- **D5** at (137.40, 126.00), rotated 180°: the connector-side copper of
  `USBC_D_P`/`USBC_D_N` (everything south of y = 124.5) and of `USB_CC2` was
  ripped and re-routed; U13 pin 2's own 0.4 mm-pitch escape was kept because the
  router cannot recreate it. Routing order mattered: CC2 first, then the pair.
  Result: `USB_CC2` 6.9 mm / 2 vias, `USBC_D_N` 18.4 mm / 3 vias, `USBC_D_P`
  30.7 mm / 4 vias (3.6 mm of it on In2), D+ and D− each attached at both
  pass-through pins, skew 12.3 mm (80 ps, against USB full-speed's 80 ns bit).
- **R38** at (141.55, 124.30) and **C46** at (145.70, 125.10): `USB_SNUB` 1.7 mm
  on F.Cu, `+5V_USB` 42.5 mm / 4 vias, C46's ground to a stitched via.
- Of the four D5 positions and five C46 positions that cleared every courtyard
  and pad, the first three D5 positions could not reach ground from pin 2; the
  fourth routed everything. R38's first spot failed KiCad DRC against C42's
  courtyard and a ground via (the joint search had used a looser model); the
  second, 0.08 mm west, passed.
- Labels: D4 and D5 moved to clear spots; 33 others unchanged.
- DRC: 0 unconnected, 0 copper errors, 3 silkscreen overlaps (U1–U4 outlines,
  unchanged) and 2 silk-over-copper (U9 over C33, U14 over C42; U12 over C27 is
  gone). Parity: the name prefixes and J5's two SBU no-connects. 3004 tracks,
  352 vias, GND plane 1840 mm² in one piece, +3.3V main piece 1665 mm² holding
  27 of 35. The analog aggressor scan is unchanged.
- `fab/`: 75 BOM lines, all with LCSC numbers; 109 parts on a middle board,
  111 on an end board. `ORDER-NOTES.txt` adds D5's orientation to the
  placement-preview list and the new low-stock lines.

**Still open:** ADC_CONV's 59 mm route, CC1/CC2 with HBM-only protection, the
inrush guideline exceeded by the damper, the eight-board power budget (a source
choice), the hot-plug measurement itself, and the firmware.
