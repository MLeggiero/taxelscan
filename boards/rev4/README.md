# rev-4 — rev-3 without the external ADC

rev-4 is the rev-3 board (one mat per board, eight boards per harness) with
the LTC1865L 16-bit converter taken out. Both sense banks are read by the
RP2354A's own 12-bit ADC. Everything else — the row drive, the muxes, the ×6
gain stage, the RS-485 harness, the USB-C power path — is rev-3's, at rev-3's
positions, on rev-3's 60.3 × 35.9 mm outline.

| file | what it is |
|---|---|
| `gen_rev4.py`, `check_faults.py` | the netlist generator with its assertions, and the fault-injection proof that they bite (41 faults, all caught). `./gen_rev4.py` writes `rev4.net` and `BOM.csv` |
| `rev4.kicad_pro`, `rev4.kicad_sch`, `rev4.kicad_pcb`, `rev4.kicad_dru`, `fp-lib-table`, `sym-lib-table`, `TaxelScanPower.kicad_sym` | the KiCad 10 project |
| `rev4.net`, `BOM.csv` | generated: the netlist and the bill of materials with LCSC numbers and notes |
| `gen_schematic.py`, `layout_schematic.py`, `make_fab.py`, `kicad_tools.py` | the schematic generator and its drawing, the JLCPCB exporter, pcbnew helpers — rev-3's, retargeted |
| `fab/` | the JLCPCB package from `make_fab.py`: gerbers, drills, `rev4-gerbers.zip`, middle-board and end-board BOM/CPL sets, drawings, order and stackup notes |
| `PLACEMENT.md` | the placement analysis: what removing U8 freed, every move considered, which were made and why |
| `ROUTING_STATUS.md` | the routed board: how it was re-routed for straight routing, DRC, parity, plane health, open items |
| `VERIFICATION.md` | the functional check: every IC against its datasheet, the analog chain and hot plug simulated, the layout measured, what to fix before ordering and what to measure at bring-up (scripts in `verify/`) |
| `tools/` | the board-change, placement and routing scripts for this revision, and rev-3's router ported to run anywhere (see `tools/README.md`) |
| `pcb_top.png`, `pcb_rev3_vs_rev4.png`, `pcb_routing_before_after.png` | renders: the board, the ADC corner against rev-3, the routing before and after the re-route |

The design record for everything this revision did not touch — the front-end
reasoning, the USB power design, the audits, the routing history — is in
`../rev3/`, and is not repeated here.

## What changed from rev-3

**Five parts removed.**

| Ref | Part | Job in rev-3 |
|---|---|---|
| U8 | LTC1865LIMS, dual 16-bit SAR, MSOP-10 | the converter, on SPI0 |
| C8 | 1 µF 0402 | U8's VCC bypass |
| R10 | 10 Ω 0402 | U8's reference filter, tapped off ROW_VCC |
| C10 | 10 µF 0805 | the reference reservoir |
| C26 | 10 nF C0G 0402 | the reference's high-frequency bypass |

Parts placed: **109 → 104** on a middle board, **111 → 106** on the two end
boards. Distinct part numbers: **49 → 47** — only the LTC1865LIMS (C580457)
and the 10 nF C0G (C22400107) disappear from the order; C8, R10 and C10 shared
reels with C41, R26 and C9/C39/C46. The low-stock line in rev-3's order notes
(the LTC1865LIMS, 23 in stock) is gone.

**Parts cost.** rev-3's $33.08 per board (10 boards at JLCPCB prices, 28
September 2026) included $18.47 for the LTC1865L. Without it rev-4's parts come
to about **$14.60** a board; the other four parts that went are cents. That is
rev-3's figure less U8's line, not a fresh quote.

**Five nets removed:** `VREF`, `ADC_CONV`, `ADC_SCK`, `ADC_SDI`, `ADC_SDO` —
140 → 135 nets.

**The pin map.** The internal ADC's inputs are GPIO26–29. GPIO26 still cannot
escape (C35, as in rev-3), so:

| signal | rev-3 | rev-4 |
|---|---|---|
| `ADC_A` (bank A) | U8 CH0 | **GPIO27 / ADC1, pin 41** |
| `ADC_B` (bank B) | U8 CH1 | **GPIO29 / ADC3, pin 43** |
| `RAIL_MON` | GPIO28 / ADC2 | GPIO28 / ADC2 (unchanged) |
| `USB_CC_OUT1` | GPIO27 | **GPIO18** (rev-3's `ADC_SCK`) |
| `USB_CC_OUT2` | GPIO29 | **GPIO19** (rev-3's `ADC_SDI`) |
| GPIO16, GPIO17 | `ADC_SDO`, `ADC_CONV` | unused |

`firmware/rev3_power/usb_power_policy.h` takes `-DTAXELSCAN_BOARD_REV=4` for the
rev-4 USB-C pins, and its native test checks both maps at compile time.

**Placement.** Seven parts moved, all in one corner east of the MCU: the two
ADC-input cells (R21/C31, R22/C32) from beside U8 to beside the RP2354A's ADC
pins, and the rail monitor (R15/R16/C45) out of their way. U7 and the gain
network, the muxes, the MCU, the connectors and the outline did not move;
`PLACEMENT.md` has the analysis, including the two moves that were considered
and rejected (moving U7 west, and re-compacting the outline for 0.9 mm). After
the verification, the regulator and buck corners were re-placed and R30 / R31
moved (below).

**Silkscreen.** The back reads `TaxelScan v4`, so the two revisions can be
told apart on the bench. The front has gained a `1` at pin 1 of J3 / J4 and
J6's `SC GND SD RUN` (below).

**Routing.** Re-routed from scratch so that every track runs at 0, 45 or 90
degrees: rev-3's routing, which the first rev-4 kept, had 67 % of its copper at
arbitrary angles; now 0.03 % (0.8 mm of plane ties). Freerouting did the bulk.
The 30 nets it cannot be trusted with were laid first by the board's own
exact-geometry router, octilinear, which also finished and tidied what
Freerouting left:
- the RS-485 pairs and both USB pairs (the USB-C side bridged across J5's
  interleaved pins and run side by side);
- the core regulator as Raspberry Pi lay it out, the buck's input loop, VCORE,
  VREG_LX and SW_NODE;
- the ADC corner, the crystal, J5's VBUS pins and USB_ILIM;
- the corner south and east of the RP2354A, with its address lines, debug
  lines and TUSB320 nets.

Nothing routes on In2 any more (it had 125 mm), so the +3.3 V plane is one
piece. The 5 V rails are 0.3 mm, widened to 0.5-0.6 mm where they fit (bar
D5.5's VBUS tie at 0.15 mm), and VCORE is 0.2 mm.
`ROUTING_STATUS.md` has the method and every number; `pcb_routing_before_after.png`
shows it.

**Rules.** The `U8_escape` rule area and its two custom rules are gone. Six
spots near R22 and D4 had been legal only at that area's 0.10 mm; R22 moved,
and the `USB_ILIM` / `USB_ILIM_LOW` segments were re-routed at the board's
0.15 mm.

## Fixed after the verification (7 October 2026)

`VERIFICATION.md` found three things to deal with before ordering, none of them
new in rev-4, and two smaller ones that were cheap to fix with them. All are
done; the board was re-placed in those two corners (`tools/fix_rev4.py`) and
re-routed from scratch.

**The core regulator, laid out the way Raspberry Pi lay it out** (finding 1).
The RP2350 datasheet (§6.3.8) asks for its Figure 26 "as closely as possible";
rev-3's CIN and COUT sat 5.7–5.9 mm from the pins. Now the parts are at the
offsets from pins 46–50 that Raspberry Pi's RP2350A minimal design uses:

- C44 (CIN) straddles VREG_VIN (pin 49) and VREG_PGND (pin 47); C19 (COUT) is
  beside it; L1 is above, its polarity dot on VCORE. Both capacitors are now
  0402 4.7 µF (Samsung CL05A475MP5NRNC; the minimal design fits Murata's 6.3 V
  GRM155R60J475ME47D) on the minimal design's wide-gap land
  (`FlexiTac:C_0402_1005Metric_WideGap`), so VREG_LX runs straight up from pin
  48 between their pads into L1: 3.3 mm on F.Cu with no via (it was 4.4 mm).
- CIN's and COUT's grounds join pin 47 and reach the plane at one point,
  through two adjacent vias. CFILT (C43, now 0402 too) has its own ground via.
  VREG_FB is taken from COUT's pad, beside LX rather than under it. VCORE
  leaves through two vias west of COUT. In1 is cut away under L1 and the LX
  track (Figure 27).
- The switching loop, LX → L1 → COUT → ground → PGND, encloses 2.6 mm²
  (`verify/layout/vreg_loop.py`), against about 20 mm² before and 2.3 mm² in
  the minimal design measured the same way.
- R23 / R24, the USB series resistors, moved north-west of pins 51 / 52 as in
  the minimal design, so USB_D leaves on F.Cu with no via: 4.1 / 3.8 mm,
  where it was 5.7 / 4.8 mm through two vias each.
- Pins 53 and 54 (USB_OTP_VDD, QSPI_IOVDD) are tied in the ring and share C18,
  1.3 mm of track from pin 54 with its own ground via. That also settles
  finding 4. C17, no longer needed at VREG_VIN,
  decouples the VREG_AVDD filter's +3.3 V input.

**Hot plug** (finding 2). The new simulation (`verify/power/damper.py`) took
the supply to USB-C's 5.5 V maximum. There, with a 1.5 µH captive cable, the
old damper left U12's input at 7.06 V against the TLV62569's 6 V absolute
maximum, and +5V_USB at 7.36 V against U14's 7 V. No damper inside USB's
attach-capacitance limits keeps a 6 V part safe, so:

- U12 is now TI's TPS62162 (LCSC C40256): the fixed 3.3 V member of the
  TPS6216x, 3–17 V in, 20 V absolute maximum. It runs at 100 % duty cycle at
  the far end of the harness and has the same 2.2 µH / 22 µF output as before
  (TI's standard pairing). The fixed output takes R18 / R19 off the board.
  It is laid out as TI's Figure 42: C27 across VIN / PGND at the pins, C22
  beside them, AGND, PGND and C22's ground meeting at the exposed pad (two
  vias), L2 beside SW (2.5 mm of 0.3 mm track, was 4.3 mm), VOS from L2's
  output pad.
- The damper is now R38 0.68 Ω + C46 22 µF (the C23 reel). +5V_USB stays at
  or below 6.78 V in every case simulated: 5.0–5.5 V supplies, 0.5–2 µH
  cables. That is inside U14's 7 V. U12's input now peaks at 6.5 V against
  20 V. The cost is about 5 µF more effective attach capacitance, behind 0.68 Ω.

**The harness cable** (finding 3). J3 and J4 are the same part turned 180° to
each other, so a cable laid straight across between two boards joins pin 1 to
pin 6. That puts +5V_BUS on SYNC_N, and the next board gets no power. The
cable is:

| J3 / J4 pin | net | cable |
|---|---|---|
| 1 | +5V_BUS | marked `1` on the silkscreen at both connectors |
| 2 | GND | |
| 3 / 4 | BUS_P / BUS_N | twisted pair |
| 5 / 6 | SYNC_P / SYNC_N | twisted pair |

It uses JST GH parts: a GHR-06V-S housing at each end, SSHL-002T-P0.2
contacts and 26 AWG wire. It is wired **pin n to pin n**, from J3 of one board
to J4 of the next, so the wires cross between adjacent boards. Ring J3.1 to
J4.1 before connecting power. The end boards carry R11 / R12 (the `-end` CPL).

**The debug header** (finding 10). J6 is now SWCLK, GND, SWDIO, RUN (pins
1–4): Raspberry Pi's 3-pin debug connector order (SC, GND, SD) on pins 1–3,
so the Debug Probe's lead fits pin 1 to pin 1. The silkscreen reads
`SC GND SD RUN` above the pins.

**Firmware** (finding 11). `firmware/rev3_power/usb_power_policy.h` no longer
defaults to rev-3. It stops the build with `#error` unless
`TAXELSCAN_BOARD_REV` is 3 or 4.

**For the re-route**, R30 / R31 (U14's EN pull-down and FAULT pull-up) moved
from west of U9 to under U14's pins 3 and 4. Where rev-3 left them, both nets
crossed under the MCU to reach them (38–52 mm, 4–6 vias), and the rip-up loop
could not fit them past U9's east side. Now they are 20 and 26 mm, with 2 vias
and none.

Placed parts: 104 → 102 on a middle board, 106 → 104 on the two end boards. Nets: 135 → 134 (`FB` went with the divider).
Distinct part numbers: 47 → 46.

## What the internal ADC changes, electrically

- **The reference.** The RP2350-family ADC has no reference of its own and
  converts against its supply pin, `ADC_AVDD`. Here that is +3.3 V through R26
  (10 Ω) with C36 (100 nF) at the pin; ROW_VCC, the row excitation, is +3.3 V
  through R5 (0 Ω). Reference and excitation are two branches of one rail, so
  the reading stays ratiometric, as rev-3's ROW_VCC-tapped VREF made it. The ADC
  draws about 150 µA from `ADC_AVDD` (Raspberry Pi's Pico 2 datasheet quotes the
  30 mV this costs across that board's 201 Ω filter), so R26's drop is ~1.5 mV:
  a fixed 0.05 % gain error, small beside the 0.1 % resistors that set the
  gain. rev-3's R10/C10 held its reference quiet above ~1.6 kHz; R26/C36 corner
  at ~160 kHz, so the reference now tracks the excitation over a wider band -
  rail noise common to both cancels instead of appearing in the reading - while
  R26 still keeps IOVDD switching noise off it.
- **Resolution.** `../rev3/ROUTING_STATUS.md` estimated the 16-bit chain at
  ~11.9 noise-free bits at 80 fps. The internal ADC's ENOB is 9.0 minimum,
  9.5 typical (RP2350 datasheet Table 1685): 1.6-2.3 LSB rms, 8.1-8.6 noise-free
  bits per conversion. That is 10-14× more noise per conversion than rev-3
  expected, before oversampling and the conditioning filters - the same converter
  rev-1, the board that has shipped, reads its mat with. The front end adds only
  0.13 LSB rms (`VERIFICATION.md`). The ×6 gain stage, which is where rev-3's
  sensitivity gain over rev-1 comes from, stays.
- **One converter for both banks.** The internal ADC is a single SAR behind an
  input mux, as on rev-1, where reading bank A then bank B let charge carry over
  and the firmware's `adcDiscard` throws one conversion away after a switch.
  rev-1 had nothing at the pins; here each pin has its 1 nF reservoir, far
  larger than the sampling capacitor, so the carried charge is shared into the
  reservoir instead of landing on a bare high-impedance node. Keep `adcDiscard`
  until bring-up measures it (rev-1's test 0c).
- **Firmware.** No LTC1865L driver to write: `firmware/rev3/PLAN.md` §3.3 and the
  SPI0 transfer sequence fall away. Its count domain (D2) already scales the
  16-bit code down to rev-1's 12-bit count, 806 µV at the ADC, so the internal
  ADC gives the same count with `adcShift = 0`, and the conditioning pipeline and
  the simulator's golden digest are unaffected. rev-1's `scan.cpp` internal-ADC
  path carries over with the new channels (ADC1 / ADC3).

## How it was built, and what was checked

    ./gen_rev4.py                  rev4.net + BOM.csv: 134 of 134 nets check out
    ./check_faults.py              41 injected faults all caught, clean baseline passes (42 of 42)
    ./gen_schematic.py             rev4.kicad_sch: 134 of 134 nets match, before and after the
                                   KiCad 10 upgrade; 107 parts agree with BOM.csv; ERC 0 errors
    tools/eco_rev4.py              rev3.kicad_pcb -> rev4.kicad_pcb: rip, delete, re-pin
    tools/place_rev4.py            the seven moves, each placed and routed
    tools/route_rev4.py            AMP_A/B, +5V, USB_CC_OUT1/2, the ILIM fixes, FID2; prune
    tools/fix_rev4.py              the verification fixes: regulator corner, TPS62162, J6, silk; R30 / R31
    tools/freeroute_rev4.py all    the re-route: strip, pre-route, freerouting, import, finish
    tools/finalize_rev4.py         install, then KiCad DRC with zones refilled + schematic parity
    ./make_fab.py                  fab/

| Check (KiCad 10.0.6) | Result |
|---|---|
| DRC, zones refilled | 0 unconnected, 0 copper errors; rev-3's same 5 silkscreen warnings |
| Schematic parity | every net's pads match; the only items are rev-3's net-name prefix and J5's two open SBU pins |
| ERC | 0 errors |
| Routing | 0.8 of 2291 mm (0.03 %) off 0 / 45 / 90 degrees, all of it plane ties (first rev-4: 67 %); nothing on In2; 164 signal vias; the USB-C data pair 2 / 2 vias, side by side |
| +3.3 V plane (In2) | 1 piece, 1820 mm², all 32 vias (first rev-4: 2 pieces, 1740 mm², 33 of 34; rev-3: 7 pieces, 1665 mm², 27 of 35) |
| GND plane (In1) | one piece, 1864 mm², all 104 vias |
| Fab package | 69 BOM lines, every one with an LCSC number; 102 / 104 parts in the CPLs; both planes in the gerbers |
| Firmware pin map | `firmware/rev3_power` native test passes for rev-3 and rev-4; a build without `TAXELSCAN_BOARD_REV` stops |

What is still open, and what to measure at bring-up: `VERIFICATION.md` and `ROUTING_STATUS.md`.

The board scripts run with KiCad's Python (`pcbnew`); `tools/README.md` says how.
The intermediate boards live in `<checkout>/tmp/` (git-ignored).
