# Verification scripts

What `../VERIFICATION.md` measured and simulated, so every number in it can be re-run.
The layout scripts need KiCad's Python (`pcbnew`) and the board's own tools in `../tools`;
the simulations need `ngspice` (`apt install ngspice`) and numpy.

| script | measures |
|---|---|
| `netdiff.py` | rev-4's netlist against rev-3's: removed parts and nets, moved pins, nets whose pins differ |
| `layout/decap.py board...` | every IC supply pin to its nearest decoupling capacitor along copper: on its own layer without a via, or through vias; and each capacitor's GND pad to its plane via |
| `layout/gndpath.py board a b ...` | copper path length (and vias) between pairs of pads on one net |
| `layout/powerpath.py board` | DC resistance between power terminals (tracks and vias solved as a network; 35 um outer, 17.5 um inner copper) and the narrowest track of each net |
| `layout/aggr.py board [gap]` | other nets' copper within `gap` mm (default 0.30) of each sensitive net on the same layer, with the parallel length and the closest gap |
| `layout/refplane.py board` | the share of each critical net's track over solid plane: In1 GND under F.Cu, In2 +3.3V under B.Cu (zones refilled first) |
| `analog/run_chain.py` | the mux-switch step through the whole chain (595 row, taxels and sneak paths, column capacitance, CD74HC4067, R1 10k, TLV9062 x6, R21 / C31, the ADC's sampling capacitor): time to 0.5 LSB at 12 bits, per column capacitance |
| `analog/run_row.py` | the same for a row switch |
| `analog/run_loop.py` | U7's loop gain and phase margin into R21 / C31, across model corners |
| `analog/run_carry.py` | round-robin ADC1 / ADC3 carry-over on the single SAR, with and without the 1 nF reservoirs |
| `analog/noise*.cir`, `ratio*.cir`, `opamp_step.cir` | front-end noise, rail-ripple cancellation between excitation and reference, the amplifier's step |
| `analog/dc_range.py`, `budget.py` | signal range (rest, press, clipping) and the frame-time budget from the simulated settle times |
| `power/hotplug.py`, `damper_sweep.py`, `harness.py`, `r38.cir` | USB-C hot-plug ringing at +5V_USB and U12's input with and without the R38 / C46 damper, the damper values, a board plugged onto a live harness |

`analog/tlv9062.lib` is a behavioural TLV9062 (datasheet GBW, slew rate, output resistance,
rail limits), not TI's model. Run the analog drivers from `analog/` (the decks include the
library by relative path); they write their decks and results beside themselves, which
`.gitignore` keeps out of the repository.
