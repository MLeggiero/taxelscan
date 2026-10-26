# Verification scripts

What `../VERIFICATION.md` measured and simulated, so every number in it can be re-run.
The layout scripts need KiCad's Python (`pcbnew`) and the board's own tools in `../tools`;
the simulations need `ngspice` (`apt install ngspice`) and numpy.

| script | measures |
|---|---|
| `netdiff.py` | rev-4's netlist against rev-3's: removed parts and nets, moved pins, nets whose pins differ |
| `layout/decap.py board...` | every IC supply pin to its nearest decoupling capacitor along copper: on its own layer without a via, or through vias; and each capacitor's GND pad to its plane via |
| `layout/gndpath.py board a b ...` | copper path length (and vias) between pairs of pads on one net |
| `layout/vreg_loop.py board` | the RP2354A core regulator's switching loop, pin 48 → L1 → COUT → GND → pin 47, along its F.Cu copper: the area its centre line encloses (the minimal design: 2.28 mm²) |
| `layout/powerpath.py board` | DC resistance between power terminals (tracks and vias solved as a network; 35 um outer, 17.5 um inner copper) and the narrowest track of each net |
| `layout/aggr.py board [gap]` | other nets' copper within `gap` mm (default 0.30) of each sensitive net on the same layer, with the parallel length and the closest gap |
| `layout/refplane.py board` | the share of each critical net's track over solid plane: In1 GND under F.Cu, In2 +3.3V under B.Cu (zones refilled first) |
| `layout/review.py board...` | the six comments of the 21 October design review (`../REVIEW.md`), measured: vias in SMD pad openings, signal width changes, +3.3 V track widths, bare corners by angle, signal copper on B.Cu, copper to the edge |
| `layout/copper.py board [netlist]` | connectivity, shorts and spacing from the copper shapes alone (shapely, not KiCad's DRC or connectivity): every netlist net one piece with all its pads, no two nets touching, spacing against the board's rules per layer, holes to copper and to each other, copper to the outline, every pad's net against `rev4.net` |
| `layout/etest.py [gerbers] [netlist] [board]` | a bare-board e-test of the files the fab gets: gerbv renders the copper layers and drill files at 10 µm, and each IPC-D-356 pad's piece of copper is checked against `rev4.net` for opens, shorts and stray copper; drill hits per tool. Needs `gerbv` (`apt install gerbv`) |
| `layout/vias.py [board] [gerbers]` | every via: the layers it joins, its plane landing, tenting and paste over its hole (from the mask and paste Gerbers), vias under parts, and the current each carries on the power paths at their worst design current |
| `analog/run_chain.py` | the mux-switch step through the whole chain (595 row, taxels and sneak paths, column capacitance, CD74HC4067, R1 10k, TLV9062 x6, R21 / C31, the ADC's sampling capacitor): time to 0.5 LSB at 12 bits, per column capacitance |
| `analog/run_row.py` | the same for a row switch |
| `analog/run_loop.py` | U7's loop gain and phase margin into R21 / C31, across model corners |
| `analog/run_carry.py` | round-robin ADC1 / ADC3 carry-over on the single SAR, with and without the 1 nF reservoirs |
| `analog/noise*.cir`, `ratio*.cir`, `opamp_step.cir` | front-end noise, rail-ripple cancellation between excitation and reference, the amplifier's step |
| `analog/dc_range.py`, `budget.py` | signal range (rest, press, clipping) and the frame-time budget from the simulated settle times |
| `analog/low_r.py` | what a lower-resistance mat does to the board: a DC solve of the whole mat with the 595s, muxes, pull-downs and U7, from a dead short to 1 MΩ, scanning and at power-up; the lowest taxel resistance each rating allows |
| `power/hotplug.py`, `damper_sweep.py`, `harness.py` | USB-C hot-plug ringing at +5V_USB and U12's input with and without the first damper (1 R + 10 µF), the damper values, a board plugged onto a live harness |
| `power/damper.py`, `r38.cir` | the fitted damper (0.68 R + 22 µF) against the first, per supply voltage and cable, at +5V_USB (U14's 7 V) and U12's input; R38's pulse |

`analog/tlv9062.lib` is a behavioural TLV9062 (datasheet GBW, slew rate, output resistance,
rail limits), not TI's model. Run the analog drivers from `analog/` (the decks include the
library by relative path); they write their decks and results beside themselves, which
`.gitignore` keeps out of the repository.
