# Routing status — rev-4, 8 October 2026

`rev4.kicad_pcb` is **re-routed from scratch** for straight routing: every
signal track runs at 0, 45 or 90 degrees. This is the third re-route. It
follows the fixes to `VERIFICATION.md`'s findings (7 October): the RP2354A's
core regulator re-placed as Raspberry Pi's RP2350A minimal design, U12 changed
to the TPS62162 in TI's layout, R18 / R19 removed, J6 re-pinned, and R30 / R31
moved under U14's pins. `tools/fix_rev4.py` made those placement changes
([PLACEMENT.md](PLACEMENT.md)) and drew their GND / +3.3 V copper. Every other
footprint is where it was.

The pre-route now lays 30 nets before Freerouting runs, against 14: the
regulator's own nets as the minimal design draws them, the buck's input loop,
USB_D on F.Cu to the resistors' new place, the crystal, USB_ILIM, and the
corner south and east of the RP2354A. That corner holds the address lines, the
debug lines and the TUSB320's nets. With J6 in Raspberry Pi's order, it was
more than Freerouting and the rip-up loop could finish.

The first rev-4 kept rev-3's routing wherever the ADC change did not reach,
and rev-3's copper was jagged: grid-router staircases simplified by line of
sight, then a compaction pass (17 September) that let every corner drift. Two
thirds of it ran at arbitrary angles. That routing is in the history (commit
`1b49e55`, summarised at the end of this file). The first re-route (`a47fcb2`)
left the USB-C data lines to Freerouting; the second (`7ca63a4`) pre-routed
them as a pair and tidied what the rip-up loop left behind.

![before and after](pcb_routing_before_after.png)

## Verified

Native KiCad **10.0.6** DRC with zones refilled and schematic parity against
`rev4.kicad_sch`, run by `tools/finalize_rev4.py`:

    unconnected   0
    copper        0   (clearance, shorts, hole clearance, widths, dangling: all zero)
    silkscreen    5 warnings - rev-3's same five: U1-U4's outlines touch in three
                  places; U14's over C42 pad 1, U9's over C33 pad 1
    parity        199 items, every one the leading-'/' local-label name or one of
                  J5's two SBU pins the sheet leaves open - the same as before.
                  Every net has the same pad membership as rev4.net

    ./gen_rev4.py         134 of 134 nets check out (FB went with R18 / R19)
    ./check_faults.py     42 of 42 behave (41 injected faults + the clean baseline)

17 parts moved and two went (PLACEMENT.md), so `fab/` was regenerated: the
CPLs, BOMs and every gerber changed with them.

## Before and after

| | first rev-4 (rev-3's routing) | `7ca63a4` (before the fixes) | now |
|---|---|---|---|
| copper off the 0 / 45 / 90-degree grid | **1575 of 2348 mm (67 %)** | 5.7 of 2311 mm (0.2 %) | **0.8 of 2291 mm (0.03 %)**, all of it GND pad-to-via ties |
| segments shorter than 0.1 mm | 675 | 107 | 69 |
| track segments | 2318 | 1588 | 1418 |
| corners / corners sharper than 90° | 1745 / 36 | 1023 / 4 | 882 / 1 |
| copper per layer | F 1513, In2 125, B 710 mm | F 1436, In2 0, B 875 mm | F 1397, **In2 0**, B 894 mm |
| vias | 336 (112 GND, 34 +3.3 V, 190 signal) | 311 (112 GND, 34 +3.3 V, 165 signal) | **300 (104 GND, 32 +3.3 V, 164 signal)** |
| copper at the 0.10 mm minimum | 543 mm | 208 mm | 233 mm |
| via holes in SMD pad openings (`tools/via_in_pad.py`) | 136: 17 wholly, 119 partly; 37 one-pad passives | 120: 11 wholly, 109 partly; 35 | 107: 11 wholly, 96 partly; 26 |

`tools/jag.py` measures the first three rows. A segment counts as off the grid
when its direction is more than 1 degree from a multiple of 45.

## How it was routed

`tools/fix_rev4.py` on the board of `7ca63a4`, then `tools/freeroute_rev4.py
all`, then `tools/finalize_rev4.py`. Freerouting does the bulk. The
exact-geometry router (`tools/lr.py`, the model rev-3's router used, with
octilinear search added in `tools/rr3.py` and `tools/grid_route3.cpp`) routes
what Freerouting cannot be trusted with, checks every segment it adds against
the board's real rules, and finishes the job.

1. **Strip.** `fix_rev4.py` takes every signal track and via off (1498
   items), and the plane ties of the parts that move (55). The strip keeps the
   136 GND / +3.3 V vias and their 230 pad-to-via ties.
2. **Pre-route** (`tools/preroute_rev4.py`), on the empty board, in this order:
   - **The core regulator, drawn as the minimal design.** `VREG_LX` runs from
     pin 48 straight up between CIN's and COUT's pads into L1: 3.3 mm on F.Cu,
     no via, 0.2 mm off the pin and 0.3 mm on. VCORE's L1-to-COUT tie and its
     two vias west of COUT; `VREG_FB` from COUT's pad (2.4 mm); `VREG_AVDD` to
     CFILT, R35 and C16. Then `SW_NODE`, the buck's switch pin to L2 (2.5 mm,
     F.Cu, 0.3 mm), and the buck's +5 V input at the pins: C27 across VIN /
     PGND, EN tied to VIN, on to C22.
   - `USB_D_P` / `USB_D_N`, drawn north-west on F.Cu to R23 / R24 beside
     VREG_FB, as the minimal design runs them: 4.1 / 3.8 mm, no via (they
     were 5.7 / 4.8 mm with 2 / 2 vias).
   - **The ADC corner**, drawn: `ADC_B`, `RAIL_MON` and `ADC_A` leave pins 43,
     42 and 41 side by side, and `RAIL_MON` in the middle passes between C32
     and C31 (0.38 mm apart) and under R22. Then `RAIL_MON`'s divider end,
     C45 to R15 / R16.
   - The rest of `VCORE` at 0.2 mm (28.3 mm, 5 vias), kept out from under the
     QFN body on F.Cu and from under L1 and the LX track on B.Cu.
   - **The USB-C data pair**, `USBC_D_P` / `USBC_D_N`: J5 through the ESD array
     D5 to R23 / R24, now 4 mm further west, clear of L1 and the LX track: 31.0
     / 27.1 mm, 2 / 2 vias. J5's data pins interleave (B7 D-, A6 D+, A7 D-, B6
     D+, 0.5 mm apart), so each line bridges its two pins, D- over the pin row
     and D+ under it; the long run goes on B.Cu, the two lines side by side.
     First D5.5, the array's VBUS pin, which gets a via in the middle of D5's
     body and a B.Cu tie to C38.
   - **J5's VBUS pins**: A4/B9 and A9/B4 tied under the pin row on F.Cu,
     0.3 mm, and on to R34 south of the receptacle's west shield pin.
   - **The plane ties**, redrawn octilinear around all of that.
   - **The RS-485 pairs**, drawn (`tools/pairs_rev4.py`, below).
   - **The crystal** (new): `XOUT_MCU` 3.9, `XIN` 9.9 and `XOUT` 3.0 mm, on F.Cu
     without a via. With the debug lines laid first, they cut across the
     crystal's corridor and the rip-up loop took `XIN` through 4 vias.
   - **The corner south-east of U9** (new). `ADDR0`-`ADDR2` (pins 32-34 to the
     address jumpers) and `USB_CC_OUT1` (pin 29 to R32 and the TUSB320) are
     drawn after `7ca63a4`'s routing, which was Freerouting's, straightened;
     the parts there have not moved. `ADDR0` / `ADDR1` drop to B.Cu in U9's pin
     ring and `ADDR2` just east of pin 34, and the three run to the jumpers
     side by side on B.Cu (15.1-16.5 mm, 2 vias each). `USB_CC_OUT1` drops
     under pin 29 and crosses under the east pin row (10.9 mm, 2 vias). Then
     the TUSB320's other nets by `rr3`: `USB_CC_OUT2` (10.8 mm, no via),
     `USB_VBUS_DET` (3.0 mm), `USB_CC2` (8.2 mm, under the USB-C pair) and
     `USB_CC1` (5.3 mm), 2 vias each for the CC lines. Left to Freerouting,
     the rip-up loop could not finish this corner: `USB_CC1` and `USB_CC2`,
     then `USB_CC_OUT1` and `USB_CC_OUT2`, ripped each other round after
     round. Laid one net at a time instead, in every order tried, one of the
     eleven was left with no way.
   - `USB_ILIM` (new): U14's pin to R28 / R27 across the pairs' lane, 13.9 mm
     with 2 vias (it was 12.8 mm with 4). Left to Freerouting, neither it nor
     the rip-up loop found a way round the pairs.
   - **The debug lines** (new): `SWCLK` 21.2 mm (1 via), `SWDIO` 18.9 mm (1),
     `RUN` 21.4 mm (2), to J6 and R20; SWCLK first.
3. **Freerouting 2.4.1** on the remaining 152 connections, given a DSN
   rewritten by `tools/dsn_prep.py`:
   - In2 is a power layer, so no signal goes on the +3.3 V plane.
   - The clearance rule areas are not keepouts, which KiCad's exporter makes
     them.
   - The board's net classes: 0.3 / 0.4 mm power, 0.10 mm for anything on the
     0.4 mm-pitch parts, SENSE / GAIN / ADC / RAIL_MON on F.Cu only, 0.15 mm
     clearance everywhere.
   - The fiducials' 0.6 mm clearance.
   - The plane ties and the 30 pre-routed nets as keepouts; the two VBUS
     ties as protected wiring, which it connects to but does not move.

   Fan-out and automatic neck-down are off: neck-down took tracks to 0.075 mm.
   24 passes and the optimiser, 5 minutes, 6 connections left open (after the
   import, KiCad's DRC counted 3 unconnected items).
4. **Import** (`tools/ses_import.py`). The session onto the board. The plane
   ties and the pre-routed copper come back from the base exactly, and so does
   the placement: KiCad's importer applies the session's placement too, which
   the DSN carries to 0.1 µm. KiCad's DRC found 0 copper violations at this
   point.
5. **Finish** (`tools/fr_finish.py`):
   - The open connections (`+5V`, `+5V_USB`, `ROW_22`) were joined by
     octilinear rip-up and reroute in one round. The planes' ties, the
     pre-routed nets and the regulator / crystal / `+5V_USB` nets are never
     ripped. Two nets that keep ripping each other up now take turns: one must
     go round the other, and if it cannot, they swap once (`rr3.rrr3`).
   - **The crystal**: pre-routed, so nothing to do. The step now puts back
     everything it touched when it cannot finish; one run of the earlier
     pre-route had left `XIN` and `XOUT_MCU` open there.
   - **The op-amp outputs**: `AMP_A` / `AMP_B` had 27.3 mm on B.Cu, over the
     +3.3 V plane instead of ground. They are redrawn with B.Cu costed, so they
     use it only to cross: 2.4 mm.
   - **Tidy**: every signal net with a via, taken off alone and routed again,
     kept only if that saves a via (at most a quarter longer) or 1 mm at the
     same vias; two passes. 13 nets redrawn, 8 vias fewer: `BOOTSEL`, `COL_0`
     and `COL_1` 2 to 0, `COL_18` 4 to 2; the others 1-4 mm shorter.
   - **Detours**: a short net more than twice its pads' spanning tree, ripped
     with one or two of the nets between its pads. None this time.
   - **Staircases** (new): where `rr3`'s 0.025 mm grid stepped round a curve
     (a keep-out's rounded corner), it left short horizontal and vertical
     segments in turn. One such run, the USB-C D+ line's 8 steps past L1 on
     B.Cu, is now one diagonal, merged with the segments either side.
   - **Widths**: Freerouting routes a class at one width end to end, so every
     net touching the RP2354A or the TUSB320 was 0.10 mm from pin to far end;
     156 tracks (280 mm) were widened back to 0.15 mm. The 5 V rails, at
     their class's 0.30 mm, now go up to 0.60 mm (`+5V_BUS`) or 0.50 mm
     (`+5V`, `+5V_USB`) wherever they fit: 60 tracks, 108 mm. KiCad's DRC
     objected to none.

   The 5 V widening was added after the run and applied to the finished board
   with `fr_finish.py --tidy-only`, whose tidy found nothing more to do.

The search in `rr3` is over (layer, cell, direction). A bend costs extra (45°
less than 90°), turns sharper than 90° are not allowed, and segments are only
ever merged runs of one direction. So everything it draws is octilinear by
construction, and every segment and via is re-checked exactly before it goes on
the board. The checks are 0.15 mm, 0.10 mm inside `U9_escape` / `U13_escape`,
a pad's own clearance (the fiducials' 0.6 mm), 0.25 mm to holes and 0.22 mm to
the edge.

## The RS-485 pairs

BUS and SYNC (RS-485; the SN65HVD75 is rated to 20 Mbit/s, the RP2350's UART to
9.375 Mbaud, so the firmware plan runs the bus as a PIO UART at 12.5 Mbaud) run
from J4 at the left edge to J3 at the right. J3 and
J4 are mirror images, so the four conductors reach J3 in the reverse of their
order at J4: each pair swaps P and N once, and the two pairs cross once. rev-3
laid them by hand for that reason, and its compaction then bent them off the
grid. A search with a lane per conductor, and N costed to follow P, still made
a tangle of the crowded J4 end, so they are drawn again for this placement
(`tools/pairs_rev4.py`):

- **Lanes.** Four straight F.Cu lanes from the transceivers to the USB-C
  receptacle, in J3's order from the top: SYNC_N, SYNC_P, BUS_N, BUS_P. Pitch
  is 0.30 mm within a pair and 0.35 mm between pairs. The corridor between the
  crystal's pads and the receptacle's front shield pins is 1.0 mm wide.
- **J4 end.** SYNC's legs drop to B.Cu beside J4 and run there coupled, under
  BUS's legs, coming up beside R12 (the pair crossing). SYNC swaps over and
  under R12.2. BUS_P threads the 0.26 mm between U10's pins and R11; BUS_N
  hops past it on B.Cu (BUS's swap).
- **J3 end.** SYNC continues on B.Cu, which is nearly empty there. BUS stays
  on F.Cu under R28 / R27, and BUS_N climbs between them to J3.4. R27.2's
  ground via stood in its way, so it moved into the pad; the board orders
  epoxy-filled, capped vias anyway (`fab/STACKUP-NOTES.txt`).

| pair | first rev-4 | re-routed |
|---|---|---|
| BUS | 61.0 / 61.0 mm, skew 0.02 mm, 2 / 2 vias, 74 % coupled | 53.8 / 54.4 mm, skew 0.55 mm, **0 / 2 vias**, 74 % coupled, P all on F.Cu |
| SYNC | 54.3 / 55.5 mm, skew 1.25 mm, 4 / 2 vias, 68 % coupled | 54.6 / 52.3 mm, skew 2.27 mm, 4 / 4 vias, **79 % coupled** |

"Coupled" is the share of N's length that runs at the pair's pitch beside P,
on P's layer. 2.3 mm of skew is about 15 ps, far inside a 50 ns bit.

## Other nets that changed character

| net | first rev-4 | `7ca63a4` | now |
|---|---|---|---|
| `VCORE` | 37.1 mm, 7 vias, 21.6 mm on In2, mostly 0.15 mm | 28.8 mm, 5 vias, none on In2, 0.20 mm | 28.3 mm, 5 vias, 0.20 mm, from the minimal design's two vias |
| `VREG_LX` | 4.7 mm F.Cu, partly 0.10 mm | 4.4 mm F.Cu, 0.20 mm | **3.3 mm** F.Cu, 0.2 / 0.3 mm, straight up between CIN and COUT |
| `SW_NODE` | – | 4.3 mm F.Cu | 2.5 mm F.Cu, 0.3 mm |
| `XIN` / `XOUT` / `XOUT_MCU` | 10.2 / 3.2 / 3.9 mm F.Cu, 0.10 mm | 10.6 / 3.1 / 4.0 mm F.Cu, no vias | 9.9 / 3.0 / 3.9 mm F.Cu, no vias, 0.15 mm (pre-routed) |
| `USB_D_P` / `USB_D_N` | 5.7 / 4.6 mm, 2 / 2 vias | 5.7 / 4.8 mm, 2 / 2 vias | **4.1 / 3.8 mm, no via**, F.Cu |
| `USBC_D_P` / `USBC_D_N` | 30.7 / 18.4 mm, 4 / 3 vias, 3.6 mm on In2 | 23.7 / 18.6 mm, 2 / 2 vias | 30.9 / 27.1 mm, 2 / 2 vias (R23 / R24 moved 4 mm west), side by side on B.Cu, skew 3.8 mm |
| `USB_ILIM` | 9.7 mm, no via | 12.8 mm, 4 vias | 13.9 mm, 2 vias |
| `USB_BUS_EN` / `USB_PWR_FAULT` | – | 37.8 / 39.8 mm, 4 / 4 vias | 19.7 / 26.4 mm, 2 / 0 vias (R30 / R31 now at U14) |
| `ADDR0` / `ADDR1` / `ADDR2` | – | 16.9 / 16.0 / 15.1 mm, 2 vias each | 16.5 / 16.1 / 15.1 mm, 2 vias each (drawn) |
| `SWCLK` / `SWDIO` / `RUN` | – | 20.2 / 21.5 / 22.2 mm, 2 / 3 / 2 vias | 21.2 / 18.9 / 21.4 mm, 1 / 1 / 2 vias |
| `AMP_A` / `AMP_B` | 20.8 / 20.6 mm, 2 / 2 vias | 22.9 / 29.2 mm, 2 / 0 vias | 24.0 / 28.5 mm, 2 / 2 vias, 1.0 / 1.4 mm on B.Cu |
| `+5V` | 87.3 mm, 9 vias, 23 mm of it at 0.2 mm | 86.8 mm, 2 vias, all 0.3 mm | 99.6 mm, 2 vias, 0.3 mm, 38 mm of it at 0.5 |
| `+5V_BUS` | 75.3 mm, 6 vias | 84.7 mm, 2 vias, 0.3 mm | 111.0 mm, 3 vias, 0.3 mm, 37 mm of it at 0.6: over the top of the board |
| `+5V_USB` | 42.5 mm, 4 vias, 5.6 mm at 0.15 mm | 48.8 mm, 3 vias | 58.4 mm, 4 vias, 0.3 mm, 24 mm of it at 0.5, but for D5.5's 0.9 mm tie at 0.15 |
| `MUX_S0` - `MUX_S3` | 42.5 / 60.7 / 40.6 / 40.8 mm, 9 / 8 / 5 / 5 vias, 40 mm on In2 | 62.4 / 66.7 / 70.5 / 53.4 mm, 4 / 4 / 4 / 5 vias | 69.8 / 59.4 / 50.0 / 38.7 mm, 5 / 3 / 2 / 2 vias |

SENSE_A / SENSE_B / GAIN_A / GAIN_B, ADC_A / ADC_B and RAIL_MON are on F.Cu
only, over the unbroken ground plane, as before.

## Planes

| | first rev-4 | `7ca63a4` | now |
|---|---|---|---|
| In2.Cu copper (signals on the +3.3 V plane layer) | 125 mm | 0 | **0** |
| +3.3 V pour on In2 | 2 pieces; main 1740.2 mm², 33 of its 34 vias | 1 piece, 1813.2 mm², all 34 vias | **1 piece, 1819.5 mm², all 32 vias** |
| GND plane on In1 | 1849.5 mm², one piece (+2 slivers of 1.2 mm²) | 1860.5 mm², all 112 GND vias (+ the same 2 slivers) | 1864.4 mm², all 104 GND vias (+ 2 slivers, 0.8 / 1.2 mm²) |

The plane vias are fewer (104 GND, 32 +3.3 V): R18 / R19 went, and the
regulator corner reaches its planes at the minimal design's points.

## Open items

From this re-route:

- **The harness pass-through is longer.** Freerouting took `+5V_BUS` over the
  top of the board this time: 111 mm against 85. Widened to 0.6 mm where it
  fits, J4.1 to J3.1 is 134 mΩ, against 101 on `7ca63a4`: 94 mV per board at
  the harness's 0.7 A, instead of 71. Pre-routing it straight across at 0.6 mm,
  as the other critical nets are, would bring it back.
- `MUX_S0` - `MUX_S3` are still long (39-70 mm, 2-5 vias, none on In2), and
  `ROW_CLK` has 4 vias. All static or slow signals.
- The corner south-east of U9 is drawn, not searched: re-placing anything there
  means re-drawing `preroute_rev4.SOUTH_EAST`, as with the ADC corner and the
  RS-485 pairs.

Carried from rev-3, unchanged (see `../rev3/ROUTING_STATUS.md`): the crystal's
5 V proximity and missing guard, the USB policy's missing retry path, and
`RAIL_MON` watching `+5V` after the OR rather than `+5V_BUS`.

Closed by rev-4: **`ADC_CONV`** (59.6 mm, 9 vias, 40 % of its B.Cu run without
a reference plane, 0.18 mm from `VREG_LX`) no longer exists. The **firmware**
item's "no LTC1865L driver in the repo" no longer applies either: the rev-1
internal-ADC path is the starting point, on ADC1 / ADC3, with
`firmware/rev3_power` built with `-DTAXELSCAN_BOARD_REV=4`.

New to check at bring-up:

- **Bank-to-bank carry-over** through the shared SAR, with the 1 nF reservoirs
  at the pins. rev-1's test 0c (press a wired column, watch its partner)
  decides whether `adcDiscard` is still needed.
- **ADC_AVDD as the measurement reference**: scope it under a full scan. R26 /
  C36 were sized for an ADC that read a DC monitor.

## Reproducing it

    cd tools
    g++ -O2 -std=c++17 -o ../../../tmp/grid_route3 grid_route3.cpp
    git show 7ca63a4:boards/rev4/rev4.kicad_pcb > ../../../tmp/r4_7ca63a4.kicad_pcb
    python3 fix_rev4.py ../../../tmp/r4_7ca63a4.kicad_pcb ../../../tmp/fix/eco.kicad_pcb
    python3 freeroute_rev4.py all ../../../tmp/fix/eco.kicad_pcb ../../../tmp/fr \
        --jar freerouting-2.4.1.jar --java /usr/lib/jvm/java-25-openjdk-amd64/bin/java
    python3 finalize_rev4.py ../../../tmp/fr/final.kicad_pcb
    cd .. && python3 make_fab.py

`fix_rev4.py` runs once, on the board before the fixes, after `gen_rev4.py` has
written `rev4.net`. To re-route the installed board as it is, give
`freeroute_rev4.py all` `../rev4.kicad_pcb`: the strip keeps its plane ties.
Freerouting's optimiser runs on three threads, so a second run will not give
the same tracks, only a board of the same kind (two runs of this pipeline both
finished clean; this is the one whose op-amp outputs had fewer vias).
`tools/README.md` has the setup: KiCad's Python, Java 25, and the Freerouting
jar.

## History: the first rev-4 routing

rev-4 began as rev-3's routed board. U8 and its four support parts were
removed, seven parts moved beside the RP2354A's ADC pins (`PLACEMENT.md`), and
only the nets the change touched were re-routed, with rev-3's rip-up loop:
`ADC_A` / `ADC_B`, `AMP_A` / `AMP_B`, `RAIL_MON`, `USB_CC_OUT1` / `USB_CC_OUT2`
(moved to GPIO18 / 19), R15's `+5V` feed, and the `USB_ILIM` pair, which went
to 0.15 mm once `U8_escape` was gone. **FID2** moved 0.11 mm to keep its 0.6 mm
clearance from `AMP_A`. That board passed the same DRC and parity checks. The
re-route above replaced all of its copper and kept its placement, FID2's move
included.
