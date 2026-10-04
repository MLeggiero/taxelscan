# rev-4 — rev-3 without the external ADC

rev-4 is the rev-3 board (one mat per board, eight boards per harness) with
the LTC1865L 16-bit converter taken out. Both sense banks are read by the
RP2354A's own 12-bit ADC. Everything else — the row drive, the muxes, the ×6
gain stage, the RS-485 harness, the USB-C power path — is rev-3's, at rev-3's
positions, on rev-3's 60.3 × 35.9 mm outline.

| file | what it is |
|---|---|
| `gen_rev4.py`, `check_faults.py` | the netlist generator with its assertions, and the fault-injection proof that they bite (39 faults, all caught). `./gen_rev4.py` writes `rev4.net` and `BOM.csv` |
| `rev4.kicad_pro`, `rev4.kicad_sch`, `rev4.kicad_pcb`, `rev4.kicad_dru`, `fp-lib-table`, `sym-lib-table`, `TaxelScanPower.kicad_sym` | the KiCad 10 project |
| `rev4.net`, `BOM.csv` | generated: the netlist and the bill of materials with LCSC numbers and notes |
| `gen_schematic.py`, `layout_schematic.py`, `make_fab.py`, `kicad_tools.py` | the schematic generator and its drawing, the JLCPCB exporter, pcbnew helpers — rev-3's, retargeted |
| `fab/` | the JLCPCB package from `make_fab.py`: gerbers, drills, `rev4-gerbers.zip`, middle-board and end-board BOM/CPL sets, drawings, order and stackup notes |
| `PLACEMENT.md` | the placement analysis: what removing U8 freed, every move considered, which were made and why |
| `ROUTING_STATUS.md` | the routed board: how it was re-routed for straight routing, DRC, parity, plane health, open items |
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
and rejected (moving U7 west, and re-compacting the outline for 0.9 mm).

**Silkscreen.** The back reads `TaxelScan v4`, so the two revisions can be
told apart on the bench; nothing else on either silkscreen changed.

**Routing.** Re-routed from scratch so that every track runs at 0, 45 or 90
degrees: rev-3's routing, which the first rev-4 kept, had 67 % of its copper at
arbitrary angles; now 0.2 % (5.7 mm of plane ties). Freerouting did the bulk;
the nets it cannot be trusted with - the RS-485 pairs, USB_D, the ADC corner,
VCORE, VREG_LX, the crystal - were laid by the board's own exact-geometry
router, octilinear. Nothing routes on In2 any more (it had 125 mm), so the
+3.3 V plane is one piece; the 5 V rails are 0.3 mm throughout and VCORE 0.2 mm.
`ROUTING_STATUS.md` has the method and every number; `pcb_routing_before_after.png`
shows it.

**Rules.** The `U8_escape` rule area and its two custom rules are gone. Six
spots near R22 and D4 had been legal only at that area's 0.10 mm; R22 moved,
and the `USB_ILIM` / `USB_ILIM_LOW` segments were re-routed at the board's
0.15 mm.

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
  ~11.9 noise-free bits at 80 fps — about what the 12-bit rev-1 chain gave — so
  the LTC1865L bought roughly 3× lower noise, not 16 usable bits. That 3× is what
  rev-4 gives up. The ×6 gain stage, which is where rev-3's sensitivity gain over
  rev-1 comes from, stays.
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

    ./gen_rev4.py                  rev4.net + BOM.csv: 135 of 135 nets check out
    ./check_faults.py              39 injected faults all caught, clean baseline passes (40 of 40)
    ./gen_schematic.py             rev4.kicad_sch: 135 of 135 nets match, before and after the
                                   KiCad 10 upgrade; 109 parts agree with BOM.csv; ERC 0 errors
    tools/eco_rev4.py              rev3.kicad_pcb -> rev4.kicad_pcb: rip, delete, re-pin
    tools/place_rev4.py            the seven moves, each placed and routed
    tools/route_rev4.py            AMP_A/B, +5V, USB_CC_OUT1/2, the ILIM fixes, FID2; prune
    tools/freeroute_rev4.py all    the re-route: strip, pre-route, freerouting, import, finish
    tools/finalize_rev4.py         install, then KiCad DRC with zones refilled + schematic parity
    ./make_fab.py                  fab/

| Check (KiCad 10.0.6) | Result |
|---|---|
| DRC, zones refilled | 0 unconnected, 0 copper errors; rev-3's same 5 silkscreen warnings |
| Schematic parity | every net's pads match; the only items are rev-3's net-name prefix and J5's two open SBU pins |
| ERC | 0 errors |
| Routing | 5.7 of 2394 mm (0.2 %) off 0 / 45 / 90 degrees, all of it plane ties (first rev-4: 67 %); nothing on In2 |
| +3.3 V plane (In2) | 1 piece, 1800 mm², all 34 vias (first rev-4: 2 pieces, 1740 mm², 33 of 34; rev-3: 7 pieces, 1665 mm², 27 of 35) |
| GND plane (In1) | one piece, 1847 mm², all 112 vias |
| Fab package | 70 BOM lines, every one with an LCSC number; 104 / 106 parts in the CPLs; both planes in the gerbers |
| Firmware pin map | `firmware/rev3_power` native test passes for rev-3 and rev-4 |

What is still open, and what to measure at bring-up: `ROUTING_STATUS.md`.

The board scripts run with KiCad's Python (`pcbnew`); `tools/README.md` says how.
The intermediate boards live in `<checkout>/tmp/` (git-ignored).
