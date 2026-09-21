# Routing status — protection, damping and the buck input, 21 September 2026

Later on 21 September three parts were added and one moved, each on a scratch
copy with the exact-geometry router, then installed (`backups/esd-2026-09-21/`);
AUDIT.md section 9 has the reasoning and the numbers.

- **D5** (USBLC6-2P6) at (137.40, 126.00) by the USB-C connector: D+ and D− pass
  through it, VBUS clamps through it. `USB_CC2` and the connector side of the
  pair were re-routed round it: CC2 6.9 mm / 2 vias, D− 18.4 mm / 3 vias, D+
  30.7 mm / 4 vias.
- **R38 + C46** (1 Ω + 10 µF hot-plug damper) at (141.55, 124.30) and
  (145.70, 125.10) beside C42; `USB_SNUB` 1.7 mm.
- **C27** at (103.54, 120.22), 1.8 mm from U12's VIN pin with its own ground via.

DRC after install: 0 unconnected, 0 copper errors, 3 silkscreen overlaps and 2
silk-over-copper warnings (one fewer than before), parity only the name prefixes.
3004 tracks, 352 vias; GND plane 1840 mm² in one piece; +3.3V main piece 1665 mm²
with 27 of 35. `fab/` regenerated: 75 BOM lines, 109 parts on a middle board and
111 on an end board.

## Routing status — verified 21 September 2026

Two small layout changes earlier on 21 September, each made on a scratch copy, checked
with the exact-geometry model and native DRC, then installed with the previous
board in `backups/status-via-2026-09-21/`:

- **STATUS via moved.** The LED line's via sat 0.10 mm from U7 pin 5 (`SENSE_B`,
  the highest-impedance node on the board). It is now 0.65 mm away at
  (151.98, 122.39), and nothing digital lies within 0.3 mm of either sense node.
- **GND-plane cut-out under `VREG_LX`.** RP2350 datasheet 6.3.8.1 / Figure 24
  asks four-layer boards to remove layer-2 copper under the inductor and the
  switch node. Rule area `VREG_LX_cutout` on `In1.Cu`: 10.3 mm², GND fill
  1850.9 → 1841.7 mm², still one piece.

Native KiCad 10.0.5 DRC with zones filled and schematic parity after both:
0 unconnected, 0 copper errors, the same 6 silkscreen warnings, parity only the
net-name prefixes. 3079 tracks, 348 vias. `fab/` regenerated: 106 parts on the
middle-board CPL, 108 on the end boards. AUDIT.md section 8 has the full
verification record, including the LTC1865L and inductor-orientation datasheet
reads.

## Routing status — compacted board, 17 September 2026

`rev3.kicad_pcb` is the grown board of 14–16 September (see the README's
"Grown board" note), fully routed, with the 16 September review's layout fixes.
On 17 September it was compacted from 70 × 38 mm to **60.3 × 35.9 mm** without
re-routing (see "What the 17 September compaction changed" below).
Native KiCad 10.0.5 DRC with zones filled and schematic parity, run in this folder:

    unconnected   0
    copper errors 0   (clearance, shorting, hole clearance, mask bridge, dangling: all zero)
    silkscreen    6 warnings (U1-U4's outlines touch in three places; U12's outline over C27 pad 1,
                  U9's over C33 pad 1, U14's over C42 pad 1)
    tracks 3079   vias 348  (115 GND stitches, 35 +3.3V plane drops)
    copper per layer  F.Cu 1542 mm   In2.Cu 217 mm   B.Cu 723 mm   In1.Cu 0 (ground, unbroken)

Schematic parity: 199 warnings, every one the leading-`/` net-name difference
between the sheet's local labels and the board ("Pad net (ROW_VCC) doesn't match
net given by schematic (/ROW_VCC)"). Every net has the same pad membership; there
is nothing electrical in it. The 70 × 38 mm board of 16 September is backed up as
`backups/compact-2026-09-17/rev3.kicad_pcb.before`, the 14 September board as
`backups/implement-2026-09-15/rev3.kicad_pcb.before`.

## What the 17 September compaction changed

Every part, via and track corner was allowed to move toward an edge, one axis at a
time, as far as a linear programme (`audit-tools/compact_lp.py`) could prove safe.
It keeps every connection and every clearance: 0.15 mm, 0.10 mm for two items in
the same escape area, 0.25 mm copper-to-hole and hole-to-hole, 0.22 mm to the
edge, each pad's own clearance (the fiducials' 0.6 mm), and no overlapping
courtyards. A segment could turn at most 15° per pass (3° when something already
sat within reach of it). 35 passes toward the four edges in turn, until a round
gained under 0.05 mm.

| | 16 September | 17 September |
|---|---|---|
| outline | 70.0 × 38.0 mm, 2660 mm² | **60.3 × 35.9 mm, 2164 mm²** (−18.6 %) |
| edges (board coordinates) | x 93–163, y 100–138 | x 98.81–159.09, y 101.02–136.95 |
| track length | 2622 mm | 2468 mm |
| +3.3V pour | 7 pieces, main 2151 mm², 27 of 35 | 7 pieces, main 1671 mm², 27 of 35 |
| vias inside SMD pads | 62 | 63 (a GND via in C10's GND pad) |
| RS-485 pairs | BUS 66 / 66 mm, SYNC 61 / 62 mm | BUS 61 / 61 mm, SYNC 54 / 56 mm |

- **What held.** No circuit change, no net changed layer, no via was added or
  removed. The analog nets are still F.Cu only, and both 5 V rails still F/B
  only. U9's seven exposed-pad vias moved with the pad. XIN/XOUT, both switch
  nodes and VREF are within 0.25 mm of their old lengths.
- **Connectors.** J1/J2's footprints mark where the board edge belongs; the marks
  still sit on the top edge. J3/J4 (JST-GH), J5 (USB-C) and J6 are now flush
  with their edges instead of 0.3–1.1 mm inside.
- **Geometry.** The passes left four sharpened corners; `smooth_corners.py`
  straightened them. The sharpest corner the compaction made is 50° (SYNC_N
  beside J4); the 21° corner on AMP_B was already there.
- **Labels.** The labels were re-placed where compaction had pushed them onto
  pads or neighbours. All 34 visible labels found a clear spot.
- **Fiducials.** They moved with everything else, to (102.62, 123.00),
  (146.75, 119.75) and (157.84, 102.65).

What limited it, measured when the passes toward the right and bottom edges had
stopped:
- **Along x:** the row above the USB connector, U13 / J5 / C37 / U14 / D4 / C10 / J3.
- **Along y:** the column from U13 through R34 to J5.

A what-if run without C10, R10 and C26 (the VREF filter) gained only 0.9 mm more
in x before the MCU-to-mux routing became the limit. That is not worth moving
analog parts for.

The passes toward the left and top edges then gained another 5.8 mm and 1.4 mm.
The top edge gave 0.38 mm of that back so J1/J2's edge marks sit on it again.
In total: right edge 3.9 mm, left 5.8 mm, bottom 1.1 mm, top 1.0 mm.

On 15 September three parts changed position or orientation, none of them
electrically: R18 (180 k) is turned 180°, and R3 and R4 (both 33 Ω damping) have
swapped places.

On 17 September, after the review, L1 was turned 180° with its pin nets swapped, so
its polarity dot sits on the VCORE end. Its pads are identical, so no copper moved.
The BOM was corrected and upgraded (AUDIT.md 7.3), with 6 value fields changed to
match: U7, U8, D1/D2, R34 and C8.

Also on 17 September, the SYNC driver was made controllable (AUDIT.md 7.4).
- **Net.** U11.3 (`DE`) left GND for a new net, `SYNC_DE`, on U9.17 (GPIO13).
- **R37.** A 10 kΩ thin-film pull-down, placed beside U11 at (111.40, 124.60),
  rotated 90°. It reaches ground through U11.4's existing via.
- **Route.** `SYNC_DE` runs 14.4 mm at 0.1 mm with 2 vias, including 5 mm on
  In2. The +3.3V pour's main piece is 1669 mm² and still holds 27 of 35.
- **Vias.** DE's old ground via came out and two `SYNC_DE` vias went in, so the
  board has 348 vias, 115 of them ground.

## What the 16 September design review changed

A full review — analog chain and timing, power path, digital and firmware
consistency, layout — ran against this board. The layout items it found are
fixed here; the rest are listed under "What is routed but not yet good" and in
AUDIT.md section 7.

| finding | fix |
|---|---|
| **U9's exposed pad had no via.** The RP2354A's entire ground return was one 0.10 mm trace, 5.1 mm long, to a via outside the pad — against `AUDIT.md`'s "9 GND vias inside the EP" and the README's "the thermal vias are not optional — they are the ground path". Pre-existing; the 14 September board had none either. | ADDR1, ADDR2 and MUX_S0 were re-routed clear of the pad's via grid, and **7 ground vias** now sit in the pad. ADDR1 got shorter on the way (48.2 → 28.7 mm), ADDR2 longer (47.4 → 49.4 mm) |
| **The 5 V rails crossed In2 at 0.30 mm.** On 0.5 oz inner copper IPC-2221 gives that ~0.30 A for a 10 °C rise; the chain draws 239–349 mA and the switch allows up to 703 mA. | `+5V_BUS` and `+5V_USB` are now **entirely on F.Cu/B.Cu**, where 1 oz at 0.30 mm carries ~1 A. Both got shorter (79.1 and 37.5 mm), and In2 routing fell from 289 mm to 217 mm, so the pour's main piece *grew* to 2151 mm² |
| `USB_BUS_SW` carried the whole chain current on 0.15 mm | widened to 0.40 mm |
| no fiducials, on a board with a 0.4 mm-pitch QFN-60 and 0201s | three 1 mm fiducials at (99.5, 123.0), (146.8, 119.8), (160.3, 102.3), each clear of copper, holes and courtyards, marked board-only so schematic parity ignores them |
| 101 of 110 reference designators hidden — blind for rework and bring-up | **32 labelled** (every IC, connector, jumper, inductor, diode, transistor, the crystal and the switch) at 0.8 mm, each placed clear of pads, silkscreen, the board edge and the other labels. The passives stay unlabelled; there is no room |

## How the five opens were closed

| net | what sealed it | fix |
|---|---|---|
| GND at C17.2 | no via site within reach between C17, C18 and L1 | a 0.1 mm F.Cu link to C18.2's grounded pad inside `U9_escape` |
| USB_VBUS_DET, R34.2 → U13.4 | U13.3's GND stitch and the +5V_BUS via | local rip-up: USB_CC1/USB_CC2 moved aside; 2.5 mm on F.Cu, no vias |
| BUS_DI, U10.4 → U9.12 | the FB divider's loop round U10.4 | R18 turned 180°, so FB joins R19.1 → R18.2 directly (FB 12.2 → 2.4 mm) and the +3.3V sense lands on R18.1; BUS_DI, BUS_RO and BUS_DE re-routed together. BUS_DI: 21.2 mm, F.Cu, no vias |
| ROW_DATA, U1.14 → U9.5 | ROW_CLK_MCU and ROW_LATCH_MCU crossing in front of pin 5, the VCORE via hub beside C24 | R3 and R4 swapped places so the two MCU-side nets stop crossing; the hub lifted; ROW_DATA drops at the pin and runs on B.Cu (6.5 mm, 2 vias); VCORE and the displaced local nets re-routed in the best of 12 orders |
| ADDR2, U9.34 → JP3.1 | pin 35's escape via | the whole of U9's bottom-right corner re-done (next section); now 49.4 mm, 6 vias, F.Cu/B.Cu only |

### U9's bottom-right corner

The corner holds three groups of pins:

- **Bottom row:** pins 27–29 (ADC_SDO, ADC_CONV, ADC_SCK).
- **Right column:** pins 31–37 (ADC_SDI, ADDR0–2, USB_PWR_FAULT, USB_BUS_EN, STATUS).
- **Higher on the column:** pins 41 and 43 (USB_CC_OUT1/2).

Their destinations lie east (U8, U13, U14) or south-west (JP1–3, R30/R31).
Routing the address straps through the corner first broke ADC_SDO. No search
over routing orders completed, from the four ADC nets up to all twelve nets: the
corner had fewer exits than pins. What worked:

1. **Earlier copper put back.** The ADC_SDO/SCK/SDI/CONV, STATUS, USB_CC_OUT1/2
   and USB_BUS_EN routes from the board before the straps were touched went back
   in unchanged. They had passed DRC then.
2. **One via moved.** ADC_SDO's via inside the pin ring moved 0.18 mm, to
   (128.30, 123.23). That lets two tracks pass along the channel between U9's
   thermal pad and its bottom pin row.
3. **Hand-laid escapes** (`audit-tools/strapgeo.py`):
   - ADDR0 and ADDR2 run west through that channel.
   - ADDR1 takes the F.Cu lane east between USB_BUS_EN and ADC_SCK.
   - USB_PWR_FAULT takes the via site just east of the column.
4. **The rest routed outward on F.Cu/B.Cu.**

Two nets opened along the way and were closed:

- **R27.2 (GND).** Laying the pairs cut R27.2's pad off from ground. The SYNC
  B.Cu lanes now jog 0.5 mm south under R27, pitch kept and equal length added to
  both conductors, so the pad reaches a via.
- **ADC_SDO.** It broke in the address-strap pass and closed again with the
  corner work above.

## Differential pairs

BUS_P/N and SYNC_P/N are re-laid by hand (`audit-tools/pairgeo.py`) as coupled
pairs, 0.15 / 0.15 mm (0.3 mm pitch), on F.Cu over the ground plane except where
they must change layer:

| pair | P | N | skew | vias P / N |
|---|---|---|---|---|
| BUS | 65.8 mm (F 47.9, B 17.9) | 66.1 mm (F 51.3, B 14.8) | 0.34 mm | 2 / 2 |
| SYNC | 60.7 mm (F 44.0, B 16.7) | 62.4 mm (F 46.1, B 16.3) | 1.65 mm | 4 / 2 |

J3 and J4 are mirror images. So each pair has to swap P and N once, and BUS and
SYNC have to cross each other once; that is what the vias are for.

- **BUS** swaps between R11's pads and runs on B.Cu under J6 (x 103.5–114).
- **SYNC** swaps with a hop at R12 and dives after J5 to B.Cu (x 141–155) into J3.

The B.Cu stretches reference In2 (+3.3V) rather than ground. Neither pair uses
In2 any more; SYNC had 46 / 44 mm there.

## What is routed but not yet good

- **The +3.3V pour on In2** fills in 7 pieces. The main piece is **1671 mm²** on
  the compacted board (2151 mm² at 70 × 38 mm) and holds **27 of the 35** +3.3V
  vias; the 14 September board's main piece was 1957 mm² holding 30.
  - Signal hops on In2 split the pour while DRC stays clean: every island keeps a
    via, so nothing reports as unconnected. At one point the MCU's eight plane
    drops sat on a 21 mm² island; re-routing MUX_S1 and USB_ILIM_LOW brought them
    back, and taking both 5 V rails off In2 grew the main piece again.
  - Six islands remain, each tied down by its own via: 3.7 mm² (two vias, under
    U9's left side), 1.4, 0.8 (the two vias feeding U9.30), 0.6, 0.4 and 0.4 mm².
    The In2 hops of ADC_SDO, ADC_CONV, ADDR1, USB_BUS_EN and USB_PWR_FAULT cut
    them off, and none of those nets routes on F.Cu/B.Cu alone.
  - Check with `audit-tools/pour.py` or `zones_check.py`.
- **ADC_CONV** is 59.6 mm with 9 vias round the MCU. That is the route the corner
  needed; it is worth shortening the next time that corner is opened. ADC_SDO has
  23 mm on In2 (28.3 mm total, down from 52.5 mm with 6 vias).
- **USBC_D_P/N** are unchanged: 22.1 / 18.0 mm, mostly B.Cu, skew 4.1 mm. F.Cu
  from R23/R24 to J5 would have to cross ADC_SCK, ADC_SDI and ADDR1, which now
  run east across x 136–138.
- **VCORE** still carries its 21.7 mm In2 trunk from C25.
- **+5V** is 91.8 mm (B.Cu 54.9, F.Cu 36.8) with 9 vias: DC, 0.3 mm, still long.
- **The slow signals are long:** ADDR1 48.2 mm (21.9 on In2), ADDR2 47.4, STATUS
  47.3, USB_BUS_EN 35.9 and USB_PWR_FAULT 32.3 mm. All are static or slow.

## What the review left open

These are not layout problems, and none of them is fixed by the board:

| area | what is wrong | what it needs |
|---|---|---|
| **Firmware** | `scan.cpp` reads the RP2350's internal ADC on GPIO26/27. rev-3 puts the signal on an external LTC1865L on SPI0, GPIO26 is unconnected and GPIO27 is `USB_CC_OUT1` — `adc_gpio_init()` on it breaks the USB CC decode. There is no LTC1865L driver in the repo. The rev-1 sketch also drives GPIO22/23 (its NeoPixel pins), which here are `ADDR2` and the open-drain `USB_PWR_FAULT`. The `int16` frame path clips about 76 kΩ of sensor range once codes are 16-bit | a rev-3 acquisition driver, written against this pin map, with a wider data path. Do not flash the rev-1 sketch to a rev-3 board |
| **BOM** | *Fixed 17 September.* The 25 blank lines are filled, and 10 of the old LCSC numbers turned out to order the wrong part (a 74LVC138 for the 595s, an LDO for the buck, a 12k and a 200k for the buck divider, a 4-pin SH connector for the harness, and others). Every line was re-verified against LCSC and JLCPCB, with thin-film resistors and name-brand capacitors throughout (AUDIT.md 7.3) | re-check the low-stock lines on the order day (`fab/ORDER-NOTES.txt`) |
| **SYNC pair** | *Fixed 17 September.* No board could drive it: U11's `DE` was strapped to GND on every board, yet `USB_POWER.md` makes the master one of these boards. `DE` now sits on GPIO13 (`SYNC_DE`) with `R37` (10 kΩ) holding it low, routed 14.4 mm with 2 vias and a short In2 hop; the +3.3V pour keeps 27 of 35 on its main piece | firmware: the master pulses GPIO13 for each frame-start; every board, the master included, takes the falling edge on `SYNC_OUT` |
| **Hot plug** | *Damped 21 September:* R38 (1 Ω) + C46 (10 µF) in series across `+5V_USB`. The estimate had a 1 m cable into ~4 µF of ceramic peaking at 6.2–7.3 V against the TLV62569's 6 V absolute maximum | scope U12 VIN on hot plug to confirm the damper does its job |
| **Power budget** | eight boards draw 239 mA typical and up to 349 mA worst case against a 282 mA guaranteed minimum trip in low mode | a 1.5 A Type-C source for eight boards; five or six on a default-current port |
| **Harness drop** | including cable and both conductors the drop is 373–504 mV, leaving the last board's regulator input at 3.58–4.03 V against the ~3.6 V it needs. The README's older 206 mV figure counted board copper only | measure it, and set the chain length from the measurement |
| **Via-in-pad** | 152 via holes lie wholly (19) or partly (133) inside SMD pad openings — including all 7 ground vias in U9's exposed pad, which sit in the middle of its paste windows — and 42 small passives have a via hole on only one pad | order the vias **Epoxy Filled & Capped** (paid on 4-layer boards at JLCPCB; `fab/ORDER-NOTES.txt`) |
| **Annular ring** | *Resolved 17 September:* the 0.30 / 0.50 mm vias have a 0.10 mm ring, which is inside JLCPCB's via rule (pad ≥ hole + 0.10 mm) at no extra cost. The 0.13–0.15 mm figure is their minimum for component holes, which here have 0.20–0.35 mm | nothing |
| **Crystal** | `+5V` passes 0.254 mm from XOUT, there is no ground guard, and 5 V crosses under Y1 on B.Cu | re-route 5 V away and guard, next time that corner is opened |
| **Buck input loop** | *Fixed 21 September for the 100 nF:* C27 is 1.8 mm from VIN with its own ground via. C22 (4.7 µF) stays 4.4 mm away | nothing |
| **ADC_CONV** | 59.6 mm, 9 vias, 40% of its B.Cu run with no reference plane under it, and 0.18 mm from `VREG_LX` for 1.2 mm — and it is the SAR's convert strobe | re-route with the ADC group |
| **RAIL_MON** | fitted and routed, but no firmware reads it, and it watches `+5V` after the OR rather than `+5V_BUS` | implement it, or move R15 to `+5V_BUS` so a board can tell whether the harness is already powered |
| **USB policy** | a CC downgrade latches the chain off and nothing calls `retry()`; `USB_ILIM_HI` is GPIO0, which is the Pico SDK's default UART0 TX | add a recovery path; disable stdio-UART or move the pin |
| **Resolution vs frame rate** | at the oversampling 80 fps allows, the chain is noise-limited near 11.9 noise-free bits — about what the 12-bit rev-1 chain gave. The gain from the LTC1865L is ~3× lower noise, not 16 usable bits | pick a point on the fps/bits curve deliberately; band-limit the amplifier to the per-taxel rate |

## What is right

- `SENSE_A` 12 mm, `SENSE_B` 16 mm, `GAIN_*`, `AMP_A`, `ADC_A` 3.1 mm,
  `ADC_B` 2.6 mm, `VREF`, `XIN` 10 mm, `XOUT` 3.4 mm, `VREG_LX` 4.7 mm,
  `VREG_AVDD`, `ADC_AVDD`: all on F.Cu over the unbroken ground plane, no vias
  (AMP_B has one B.Cu hop of 3.9 mm).
- Every RP2354A supply pin runs straight into its 0201 (pad 1 at the pin, GND
  pad stitched), the buck output L1→C19 is 4.7 mm direct, the 3.3 V buck is
  one 10 × 6 mm block, and its feedback divider is now 2.4 mm.
- 109 GND stitching vias; In1.Cu is one polygon with nothing routed on it.

## Fabrication

`make_fab.py` regenerated `fab/` on 17 September, after the review. It contains:
- gerbers and drill files; the plane gerbers are `rev3-GND.gbr` (306 kB) and
  `rev3-PWR.gbr` (414 kB);
- two assembly sets: `rev3-bom.csv` / `rev3-cpl.csv` for middle boards (106
  parts, no R11/R12) and `rev3-bom-end.csv` / `rev3-cpl-end.csv` for the two end
  boards (108 parts);
- `STACKUP-NOTES.txt` and `ORDER-NOTES.txt`: the JLCPCB settings to select,
  including epoxy-filled and capped vias and Standard PCBA, and what to check in
  the placement preview.

Every BOM line has a verified LCSC number, so nothing blocks assembly any more.
The 1 September test export that used to sit in `GERBER OUTPUT/` predates all of this; it now lives in the local-only `backups/gerber-test-2026-09-01/`.

## How it was made

14 September:

1. `audit-tools/place_grow.py` — outline, zones, rule areas, the placement
   table, pad-1-faces-its-pin flips (counting only numbered pads: the KiCad
   0201 footprint has two paste-only pads that silently defeated the earlier
   flip), overlap and pad-conflict report, `tmp/placement-grow.png`.
2. `audit-tools/route_fix.py` stages `fanout stubs analog gnd pairs power` —
   the deliberate copper. Bypass and core-rail stubs are routed before the
   +3.3V pin stubs and XOUT before XIN; both orders matter (each blocked the
   other the first time round).
3. `dsn_keepout.py` — export DSN, hand every existing wire and via to
   freerouting as a keepout and drop the hand-routed nets from its network.
   Freerouting 2.2.4 throws `to_trace_entries is null` on any DSN that
   carries pre-routed wires, and KiCad exports rule areas as keepouts, so
   both are stripped. Units are µm, not 0.1 µm; circle keepouts are read at
   the wrong scale, polygons are fine.
4. `freerouting.exe -de rev3.fr.dsn -do rev3.ses -mp 60 -dct 0 --gui.enabled=false`
   (7–10 min, ~11 of 121 connections left), `import_ses.py --apply --add`.
5. `route_fix.py close 3`, `maze_route.py --apply` (through
   `audit-tools/maze_wrap.py`, which gives KiCad's Python the `kiutils`
   dependency), `clean`, `prune`, `close 2`, `probe_all.py`.
6. `fix_5v.py` re-laid +5V on the outer layers only (a first attempt used In2
   and split the +3.3V pour).

15 September: the exact-geometry router and stage scripts in `audit-tools/`
(second table of its README, with the stage-by-stage record):

1. C17.2 and USB_VBUS_DET.
2. BUS_DI.
3. ROW_DATA.
4. The pairs and address straps.
5. USB_CC_OUT2 shortened, the SYNC jog and R27.2.
6. U9's corner.
7. The +3.3V pour.
8. `finalize.py`, `install_board.py`, `make_fab.py`.

Backups of the 10–14 September stages are in `backups/audit-fixes-2026-09-10/`
(`rev3.kicad_pcb.grow-placed`, `.grow-fixedcopper`, `.grow-fr4-closed`,
`.grow-fr4-maze`, `.grow-fr4-5v`). The 54 × 36 mm board the audit fixes were first
tried on is `rev3.kicad_pcb.routed3-19open`.
