# Routing status — rev-4, 4 October 2026

`rev4.kicad_pcb` is rev-3's routed board (`../rev3/rev3.kicad_pcb`, 21 September)
with U8 and its four support parts removed, seven parts moved and the nets the
change touches re-routed. Everything else - every other part, track and via -
is where rev-3 put it.

## Verified

Native KiCad **10.0.6** DRC with zones refilled and schematic parity against
`rev4.kicad_sch`, run by `tools/finalize_rev4.py`:

    unconnected   0
    copper        0   (clearance, shorts, hole clearance, widths, dangling: all zero)
    silkscreen    5 warnings - rev-3's same five: U1-U4's outlines touch in three
                  places; U14's over C42 pad 1, U9's over C33 pad 1
    parity        199 items, every one the leading-'/' local-label name or one of
                  J5's two SBU pins the sheet leaves open - the same as rev-3.
                  Every net has the same pad membership as rev4.net

    ./gen_rev4.py         135 of 135 nets check out
    ./check_faults.py     40 of 40 behave (39 injected faults + the clean baseline)
    ./gen_schematic.py    135 of 135 nets match, before and after the KiCad 10
                          upgrade; 109 parts agree with BOM.csv; ERC 0 errors,
                          7 lib_symbol_mismatch warnings (U7, U9, U10, U11, Y1:
                          the generator's flattened copies of library symbols),
                          which rev-3's generator produces identically

With KiCad's standard global library tables installed there are no footprint
warnings at all: every footprint on the board matches its library copy. (Run
without them, KiCad reports one `lib_footprint_issues` warning per footprint
saying it cannot find the library - a property of the machine, not the board.)

## What was re-routed

| net | rev-3 | rev-4 | note |
|---|---|---|---|
| `ADC_A` | 3.0 mm F, to U8 | 4.8 mm F, 0 vias | U9.41 → C31 3.96 mm (0.10 mm), R21 → C31 0.80 mm |
| `ADC_B` | 2.5 mm F, to U8 | 4.5 mm F, 0 vias | U9.43 → C32 3.95 mm (0.10 mm), R22 → C32 0.53 mm |
| `AMP_A` | 6.7 mm F | 20.8 mm, 2 vias | U7.1 → R6 0.9 mm; on to R21 19.9 mm, F except a 1.0 mm B.Cu hop under `SENSE_B` |
| `AMP_B` | 12.4 mm, 2 vias | 20.6 mm, 2 vias | U7.7 → R8 2.8 mm; on to R22 17.9 mm, 12.1 mm of it one straight B.Cu run |
| `RAIL_MON` | 9.0 mm F | 9.3 mm F, 0 vias | U9.42 → C45 8.32 mm, running between the two ADC traces |
| `USB_CC_OUT1` | 14.1 mm, 3 vias (GPIO27) | 11.0 mm, 4 vias (GPIO18) | U9.29 → U13.7 → R32 |
| `USB_CC_OUT2` | 14.7 mm, 2 vias (GPIO29) | 11.9 mm, 4 vias (GPIO19) | U9.31 → U13.8 → R33 |
| `+5V` | 88.5 mm, 9 vias | 87.3 mm, 9 vias | R15's feed re-made (2.5 mm, 1 via) where R15 moved |
| `USB_ILIM` | 9.7 mm | 9.7 mm | its two segments beside D4 re-laid at 0.15 mm |
| `USB_ILIM_LOW` | 32.9 mm, 4 vias | 36.3 mm, 4 vias | its D4 and +5V crossings re-laid at 0.15 mm |

`USB_CC_OUT2` had to rip part of `ADDR1`, `USB_BUS_EN` and `USB_PWR_FAULT` to
get out of the U9/U13 corner, and those then re-routed in turn, as did a few
segments of `ADDR0`, `ADDR2` and `STATUS` (rr2's rip-up loop, 50 iterations,
nothing left open):

| net | rev-3 | rev-4 |
|---|---|---|
| `USB_BUS_EN` | 34.7 mm, 5 vias, 6.9 mm on In2 | 31.3 mm, 4 vias, none on In2 |
| `USB_PWR_FAULT` | 31.2 mm, 6 vias, 11.4 mm on In2 | 30.4 mm, 4 vias, none on In2 |
| `ADDR0` / `ADDR1` / `ADDR2` | 21.8 / 28.2 / 48.4 mm, 4 / 3 / 6 vias | 22.0 / 29.1 / 48.8 mm, 4 / 4 / 6 vias |
| `STATUS` | 46.1 mm, 2 vias | 46.1 mm, 4 vias |

Across the eight signals of that corner rev-4 has 231 mm and 34 vias against
rev-3's 239 mm and 31. A second attempt that ripped only the two status lines
and re-routed them in both orders with everything else held still failed both
times, so the rip-up result stands: the corner is no tighter than rev-3 left it,
just arranged differently.

**FID2** moved 0.11 mm, from (146.75, 119.75) to (146.65, 119.70). Fiducial pads
carry a 0.6 mm local clearance the router model does not know about, and
`AMP_A`'s diagonal had come to 0.53 mm; DRC caught it and
`route_rev4.py`'s fiducial step moved the mark rather than the trace.

## What the removal did to the planes

| | rev-3 | rev-4 |
|---|---|---|
| In2.Cu copper (signals on the +3.3 V plane layer) | 221 mm | **125 mm** |
| +3.3 V pour on In2 | 7 pieces; main 1665.1 mm², 27 of its 35 vias | **2 pieces; main 1740.2 mm², 33 of its 34 vias** |
| GND plane on In1 | 1839.7 mm², one piece (+2 slivers of 1.2 mm²) | 1849.5 mm², one piece (+ the same 2 slivers) |

The In2 copper that went: `ADC_SDO` 23.1 mm, `ADC_CONV` 18.5 mm, `ROW_VCC`'s
31 mm branch that carried rev-3's reference tap to R10, and the In2 hops of
`USB_PWR_FAULT`, `USB_BUS_EN` and `USB_CC_OUT1`. None of the re-routing put
anything back on In2. The one +3.3 V via still off the main pour, at
(127.25, 118.55) between U9's exposed pad and its top pin row, was on a 0.4 mm²
island in rev-3 as well.

## Totals

| | rev-3 | rev-4 |
|---|---|---|
| track segments | 3004 | 2318 (rev-3's removed nets were long staircases of short segments) |
| copper | 2502 mm (F 1546, In2 221, B 735) | 2348 mm (F 1513, In2 125, B 710) |
| vias | 352 (116 GND, 35 +3.3 V) | 336 (112 GND, 34 +3.3 V) |
| via holes in SMD pad openings (`tools/via_in_pad.py`) | 150: 18 wholly, 132 partly; 45 two-pad parts with a hole on one pad | 136: 17 wholly, 119 partly; 37 |

The via-in-pad count is why the board still needs **epoxy-filled and capped
vias**; `fab/STACKUP-NOTES.txt` and `fab/ORDER-NOTES.txt` say so. Every GND via
added beside a moved part is outside its pad.

## Open items carried from rev-3

Unchanged by this revision and still open (see `../rev3/ROUTING_STATUS.md`):
the crystal's 5 V proximity and missing guard, the USB policy's missing retry
path, and `RAIL_MON` watching `+5V` after the OR rather than `+5V_BUS`.

Closed by it: **`ADC_CONV`** (59.6 mm, 9 vias, 40 % of its B.Cu run without a
reference plane, 0.18 mm from `VREG_LX`) no longer exists, and the
**firmware** item's "no LTC1865L driver in the repo" no longer applies — the
rev-1 internal-ADC path is the starting point, on ADC1 / ADC3, with
`firmware/rev3_power` built with `-DTAXELSCAN_BOARD_REV=4`.

New to check at bring-up:

- **Bank-to-bank carry-over** through the shared SAR, with the 1 nF reservoirs
  at the pins: rev-1's test 0c (press a wired column, watch its partner) decides
  whether `adcDiscard` is still needed.
- **ADC_AVDD as the measurement reference**: scope it under a full scan; R26/C36
  were sized for an ADC that read a DC monitor.
