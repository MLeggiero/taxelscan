# Hand-placement spec — decoupling

Coordinates are KiCad absolute mm, the numbers pcbnew shows in its status bar.
The board origin is (100, 100), so subtract 100 for board-relative.

Every capacitor here is a 0402. **Pad 1 is the supply pad, pad 2 is GND** — that
is how `gen_rev3.py` wires all of them, so the rotations below are chosen to put
pad 1 nearest the pin it serves.

## The rule these numbers come from

A decoupling capacitor works by being a lower-impedance path than the loop back
to the bulk. The capacitor is milliohms; the loop is inductance. At 10 mm of
0.15 mm trace the loop is ~10 nH — about 63 Ω at 100 MHz — and the capacitor
stops mattering. At 0.7 mm it is ~1.4 nH, and it works.

So two things matter, in this order:

1. **Pad 1 within ~1 mm of the pin**, on a direct trace, no vias in between.
2. **A GND via on pad 2**, within 0.5 mm. The return path is half the loop, and
   a cap that reaches the plane 5 mm away through a shared trace has undone
   most of what step 1 bought.

## Order of work

Do U9 first — it is the only part on the board with real di/dt. Then the rest.

### Step 1: clear the ring around U9

These parts sit where U9's decoupling has to go. Move them before placing any
capacitor:

| part | why it is in the way | where to send it |
|---|---|---|
| `R17` | left edge, blocks C24 and C34 | it is the status-LED resistor, DC — anywhere |
| `U10` | left edge, blocks C33 | slide left ~2 mm if it will go |
| `U8` | below, blocks C11, C25, C13 | slide right ~2.5 mm, under U9 pins 27–31, which also shortens the ADC bus |
| `C19` | above, blocks C17, C18, C28 | it belongs at L1's output — see the note at the end |
| `U6` | above-right, blocks C16 | slide right if it will go |

`JP1`–`JP3` and `R15`/`R16` are already moved out to x ≈ 50 on the widened
board, which is what freed U9's right edge.

### Step 2: U9's fourteen capacitors

| cap | pin | rail | place centre at | rotation | pad-1 gap | GND via at |
|---|---|---|---|---|---|---|
| `C33` | U9.1 | +3.3V | 117.915, 117.400 | 180 | 0.70 | 117.435, 117.400 |
| `C24` | U9.6 | VCORE | 117.915, 119.400 | 180 | 0.70 | 117.435, 119.400 |
| `C34` | U9.11 | +3.3V | 117.915, 121.400 | 180 | 0.70 | 117.435, 121.400 |
| `C11` | U9.20 | +3.3V | 121.350, 124.835 | 270 | 0.70 | 121.350, 125.315 |
| `C25` | U9.23 | VCORE | 122.550, 124.835 | 270 | 0.70 | 122.550, 125.315 |
| `C13` | U9.30 | +3.3V | 125.350, 124.835 | 270 | 0.70 | 125.350, 125.315 |
| `C14` | U9.38 | +3.3V | 127.185, 120.200 | 0 | 0.70 | 127.665, 120.200 |
| `C35` | U9.39 | VCORE | 127.185, 119.800 | 0 | 0.70 | 127.665, 119.800 |
| `C36` | U9.44 | ADC_AVDD | 127.185, 117.800 | 0 | 0.70 | 127.665, 117.800 |
| `C15` | U9.45 | +3.3V | 127.185, 117.400 | 0 | 0.70 | 127.665, 117.400 |
| `C16` | U9.46 | +3.3V | 125.350, 115.565 | 90 | 0.70 | 125.350, 115.085 |
| `C17` | U9.49 | +3.3V | 124.150, 115.565 | 90 | 0.70 | 124.150, 115.085 |
| `C18` | U9.53 | +3.3V | 122.550, 115.565 | 90 | 0.70 | 122.550, 115.085 |
| `C28` | U9.54 | +3.3V | 122.150, 115.565 | 90 | 0.70 | 122.150, 115.085 |

`R26` (10 Ω) goes between +3.3V and `C36`, anywhere convenient on U9's right
side — it is a series element, so only `C36`'s position matters.

**These positions put each capacitor inside U9's courtyard.** That is
deliberate and it is what a 0.70 mm gap costs; KiCad will report
`courtyards_overlap` for all fourteen. Either waive those, or shrink U9's
courtyard to its body. Do not move the capacitors out to satisfy the rule —
the courtyard is a mechanical keep-out for placement machines, and a 0402
tucked against a QFN is well inside what JLCPCB places every day.

### Step 3: everyone else

Each of these is blocked by the part named. Move that part first, or shift the
capacitor along the same edge until it fits.

| cap | pin | rail | place centre at | rotation | pad-1 gap | GND via at | blocked by |
|---|---|---|---|---|---|---|---|
| `C1` | U1.16 | ROW_VCC | 125.022, 107.450 | 0 | 1.04 | 125.502, 107.450 | U6 |
| `C2` | U2.16 | ROW_VCC | 108.885, 113.000 | 0 | 1.04 | 109.365, 113.000 | R11, U3 |
| `C3` | U3.16 | ROW_VCC | 117.022, 107.450 | 0 | 1.04 | 117.502, 107.450 | U1 |
| `C4` | U4.16 | ROW_VCC | 109.022, 107.350 | 0 | 1.04 | 109.502, 107.350 | U3 |
| `C5` | U5.24 | +3.3V | 140.500, 116.135 | 270 | 0.50 | 140.500, 116.615 | — |
| `C6` | U6.24 | +3.3V | 126.000, 115.985 | 270 | 0.50 | 126.000, 116.465 | U9 |
| `C7` | U7.8 | +3.3V | 113.822, 120.025 | 0 | 1.12 | 114.302, 120.025 | R18 |
| `C8` | U8.9 | +3.3V | 126.535, 126.000 | 0 | 1.05 | 127.015, 126.000 | C10 |
| `C12` | U10.8 | +3.3V | 109.765, 117.500 | 180 | 1.28 | 109.285, 117.500 | R3 |
| `C29` | U11.8 | +3.3V | 139.740, 122.000 | 180 | 1.28 | 139.260, 122.000 | D1 |

`C1`–`C4` are the worst offenders as the board stands — 17 to 22 mm from the
595s they serve. They are also the least urgent: at 1 MΩ a driven row sources
106 µA, so the shift registers have almost no dynamic current. Do U9 first.

## The one that is not a capacitor

**`L1` belongs against `U9` pin 48 (`VREG_LX`), at roughly (124.0, 114.5),
rotated 90.** It is currently at (114.06, 125.00) — 13.3 mm away, diagonally
opposite — and that single distance causes three separate problems:

- the buck's switching loop is the whole width of the MCU;
- `VCORE` has to cross B.Cu *underneath* U9 to get from L1 to the DVDD pins,
  which is where the thermal vias go, so only 4 of the 9 fit;
- `C19` (4.7 µF) has to follow L1, and it is currently blocking three of U9's
  top-edge capacitors.

The obstacle is `R8`, `R9` and `R16` in the band between U1 and U9 (y ≈ 13–16).
`R16` is already evicted. Move `R8` and `R9` — both are gain-network resistors
on the analog side and want to be near U7, not here — and L1 fits at
1.7 × 3.5 mm rotated 90.

With L1 there, put `C19` immediately beside it at L1's *output* end, and tie
U9 pin 50 (`VREG_FB`) to that node with a short trace. Then re-add the full
3 × 3 thermal via grid under U9, on the 1.13 mm paste-window centres:

    (122.55, 120.20)  and  (122.55 ± 1.13, 120.20 ± 1.13)

0.3 mm drill, 0.6 mm pad, tented on the bottom side so paste cannot run
through. Pin 61 is the RP2354A's only ground connection.

## After placing

    ./close_gaps.py --apply       # sweep dead copper and orphaned stubs
    ./maze_route.py --apply       # re-stitch the pours, route what is open
    "C:/Program Files/KiCad/10.0/bin/python.exe" kicad_tools.py fill
    ./make_fab.py

`maze_route.py` re-stitches the planes well — it took 92 GND islands to 91 in
one pass — but it cannot route into a pin that its own neighbours have sealed
in, which is most of U9. Expect to draw the short runs from each capacitor to
its pin by hand; they are 0.7 mm and there is nothing to route around.

---

# The rest of the capacitors

The fourteen above plus the ten in step 3 are the *decoupling*. These eleven are
the ones whose position is part of the circuit rather than part of the layout —
each is doing a specific job at a specific pin, and moving it changes what it
does.

## Distance is the function — do these carefully

| cap | at | centre (mm) | rot | pad-1 gap | GND via at |
|---|---|---|---|---|---|
| `C31` | U8.2 `CH0` | 119.470, 128.500 | 180 | 1.05 | 118.990, 128.500 |
| `C32` | U8.3 `CH1` | 119.470, 129.000 | 180 | 1.05 | 118.990, 129.000 |
| `C26` | U8.10 `VREF` | 126.730, 128.000 | 0 | 1.05 | 127.210, 128.000 |
| `C27` | U12.4 `VIN` | 135.580, 119.000 | 0 | 0.96 | 136.060, 119.000 |

**`C31` / `C32` — the SAR's charge reservoir.** These are the whole reason
`R21`/`R22` exist. The LTC1865L samples onto a switched capacitor and throws
charge back at whatever drives it; 51 Ω × 1 nF settles that in ~51 ns against a
~1 µs acquisition window. The network only works if the capacitor is at the
**ADC pin** — the resistor goes between the amplifier and the cap, not between
the cap and the pin. Put C31/C32 hard against U8 pins 2 and 3, and R21/R22
behind them toward U7.

**`C26` — VREF, C0G.** The reference for a 16-bit converter. Every millivolt
of noise here is a count of error, and it is the one error the dark reference
cannot subtract, because it scales the reading rather than offsetting it.

**`C27` — the buck's input.** This is the highest di/dt loop on the board: it
carries the switched current, not the average. The loop is **U12 pin 4 (VIN) →
C27 → U12 pin 2 (GND)**, and U12's pins are at (134.14, 119.00) and
(131.86, 118.05). Keep that triangle as small as you can and give C27's ground
pad its own via. Note pin 1 is `EN`, not VIN — VIN is pin 4.

## Bulk — behind the HF cap, same net, less fussy

| cap | value | behind | centre (mm) | rot | GND via at |
|---|---|---|---|---|---|
| `C10` | 10 µF | C26, on VREF | 129.920, 128.000 | 0 | 130.870, 128.000 |
| `C22` | 22 µF | C27, on +5V | 138.770, 119.000 | 0 | 139.720, 119.000 |
| `C23` | 22 µF | L2.2, buck output | 137.500, 112.875 | 270 | 137.500, 113.825 |
| `C9` | 10 µF | C23, on +3.3V | 137.500, 115.595 | 270 | 137.500, 116.545 |

Bulk caps hold the rail up over microseconds; they do not have to be at a pin,
they have to be on the right side of the inductor. The ordering matters more
than the millimetres: **small and fast nearest the pin, large and slow behind
it.** A 22 µF 0805 in front of a 100 nF 0402 wastes the 0402, because the
0805's own inductance is then in series with it.

## These two follow a part that has to move first

**`C20` / `C21` — crystal load, 15 pF C0G.** They belong flanking `Y1`, one on
each of XIN and XOUT, as symmetric as you can make them, with their ground pads
tied to a *local* ground — a via right at each ground pad, not a trace running
off to the plane elsewhere. Y1's pins 2 and 4 are its own ground; tie those to
the same local ground.

Y1 is currently at (102.90, 121.35), which is ~19 mm from U9 pins 21/22. It has
to come to U9's bottom edge first — target roughly (120.0, 126.5) — and that
needs `U8` to slide right, which it wants to do anyway. Once Y1 lands, put C20
and C21 within 2 mm of it, one per pin, and keep XIN/XOUT away from everything
else: they are the only two nets on the board that will radiate.

**`C19` — VCORE bulk, 4.7 µF.** It sits at `L1`'s **output** end, and U9 pin 50
(`VREG_FB`) taps that same node with a short trace. Against L1's current
position that would be (116.638, 125.000) rot 0 — but L1 has to move to U9 pin
48 first, so place C19 after L1 and keep it within ~1 mm of L1's pin 2.

## Summary — what each of the 35 capacitors is for

| group | caps | rule |
|---|---|---|
| IC decoupling | C1–C8, C11–C18, C24, C25, C28, C29, C33–C36 | one per supply pin, ≤1 mm, own GND via |
| ADC reservoir | C31, C32 | hard against U8 pins 2 and 3 |
| VREF | C26 then C10 | C0G first, bulk behind |
| Buck input | C27 then C22 | smallest possible VIN–GND loop |
| Buck output | C23, C9 | after L2 |
| Core rail | C19 | at L1's output, with VREG_FB |
| Crystal | C20, C21 | flanking Y1, local ground |

---

# Wiring the supply pins to their capacitors

Placement got 16 of 24 supply pins within 2.5 mm. This is the other half of the
job: the trace that connects them. A capacitor 1 mm from a pin, reached by the
wrong path, is worth no more than one 10 mm away.

## The rule, for every one of them

    IC supply pin ──F.Cu, no via── cap pad 1 │ cap pad 2 ──via── GND plane

Four things, and all four matter:

1. **No via between the pin and the cap.** A via is ~0.3-0.5 nH, which is the
   same order as the whole 1 mm trace you just bought. Keep the run on F.Cu.
2. **The rail reaches the pin THROUGH the capacitor**, not in parallel with it.
   What a plane invites you to do instead - drop a via to +3.3V at the pin, and
   let the cap find the plane somewhere else - passes DRC and leaves the loop
   equal to the distance between the two vias. Same netlist, no decoupling.
3. **A via on the cap's own GND pad**, 0.3 mm drill / 0.6 mm pad, on the pad or
   within 0.3 mm of it. The return is half the loop. This is the step people
   skip.
4. **Widen once clear.** Leave the pin at 0.15 mm because that is all a 0.4 mm
   pitch QFN allows, then go to 0.3 mm as soon as you are past the pin field.

Where two pins share one capacitor - which is unavoidable at 0.4 mm pitch,
since two 0402s will not fit under two pins 0.4 mm apart - run both pins to the
same pad 1. They meet at the capacitor, which is the point.

**The ground half of the loop is the thermal via grid.** For U9 the return runs
cap → via → In1.Cu → EP vias → chip. Four of the nine are in; put the rest back
once L1 moves, or the shortest possible cap trace still returns through one
corner via.

## Eleven capacitors still to move

Nothing else needs to move. These are the pins still further than 2.5 mm from a
capacitor on their rail, and there is exactly one free capacitor for each.

| cap | serves | rail | move to | rot | pad-1 gap | GND via at |
|---|---|---|---|---|---|---|
| `C1` | U1.16 | ROW_VCC | 125.017, 107.450 | 0 | 1.04 | 125.498, 107.450 |
| `C4` | U2.16 | ROW_VCC | 108.880, 113.000 | 0 | 1.04 | 109.360, 113.000 |
| `C3` | U3.16 | ROW_VCC | 117.017, 107.450 | 0 | 1.04 | 117.498, 107.450 |
| `C2` | U4.16 | ROW_VCC | 109.017, 107.350 | 0 | 1.04 | 109.498, 107.350 |
| `C7` | U10.8 | +3.3V | 109.295, 117.500 | 180 | 1.27 | 108.815, 117.500 |
| `C6` | U11.8 | +3.3V | 141.295, 121.000 | 180 | 1.28 | 140.815, 121.000 |
| `C29` | U5.24 | +3.3V | 140.500, 116.130 | 270 | 0.50 | 140.500, 116.610 |
| `C8` | U8.9 | +3.3V | 126.730, 128.500 | 0 | 1.05 | 127.210, 128.500 |
| `C36` | U9.44 | ADC_AVDD | 127.180, 118.320 | 0 | 0.70 | 127.660, 118.320 |
| `C15` | U9.45 | +3.3V | 127.180, 117.920 | 0 | 0.70 | 127.660, 117.920 |
| `C28` | U9.20 | +3.3V | 121.350, 125.350 | 270 | 0.70 | 121.350, 125.830 |

`C1`-`C4` are one per 595, in reference order onto U1-U4. `C15` and `C28` are
relief: `C16` currently serves three pins on its own and `C11` two.

## The runs already in place — draw these as they stand

| pin | cap | from | to (cap pad 1) | len | GND via at |
|---|---|---|---|---|---|
| U9.1 | C33 | 119.100, 117.920 | 118.000, 116.980 | 1.45 | 118.000, 116.020 |
| U9.6 | C24 | 119.100, 119.920 | 117.500, 119.500 | 1.65 | 117.500, 118.540 |
| U9.11 | C34 | 119.100, 121.920 | 117.500, 122.040 | 1.60 | 117.500, 123.000 |
| U9.20 | C11 | 121.350, 124.170 | 120.500, 125.540 | 1.61 | 120.500, 126.500 |
| U9.23 | C25 | 122.550, 124.170 | 122.500, 125.560 | 1.39 | 122.500, 126.520 |
| U9.30 | C13 | 125.350, 124.170 | 125.500, 125.520 | 1.36 | 125.500, 126.480 |
| U9.38 | C14 | 126.000, 120.720 | 127.520, 121.000 | 1.55 | 128.480, 121.000 |
| U9.39 | C35 | 126.000, 120.320 | 127.520, 120.000 | 1.55 | 128.480, 120.000 |
| U9.46 | C16 | 125.350, 117.270 | 125.500, 116.020 | 1.26 | 125.500, 115.060 |
| U9.49 | C17 | 124.150, 117.270 | 124.000, 116.000 | 1.28 | 124.000, 115.040 |
| U9.53 | C18 | 122.550, 117.270 | 122.500, 116.000 | 1.27 | 122.500, 115.040 |
| U9.54 | C18 | 122.150, 117.270 | 122.500, 116.000 | 1.32 | shares C18 with U9.53 |
| U7.8 | C12 | 113.020, 121.200 | 115.000, 120.520 | 2.09 | 115.000, 121.480 |
| U6.24 | C16 | 127.500, 114.900 | 125.500, 116.020 | 2.29 | shares C16 |

## Order

1. Move the eleven above.
2. Draw all 24 pin-to-cap runs and drop every GND via. By hand — they are
   1-2 mm each with nothing to route around, and doing them first means the
   router has to work around them rather than through them.
3. Then `./maze_route.py --apply` for everything else, `./close_gaps.py
   --apply`, `kicad_tools.py fill`, `./make_fab.py`.

The router will not touch what is already down, so the decoupling geometry is
locked in before the 400-odd signal connections start competing for space.
