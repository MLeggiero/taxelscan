# Front-end module

One of these sits at each mat. It is the rev-1 reader board with the MCU
removed and a cable put in its place — same row drivers, same muxes, same
buffer, same footprints — so there is no new analog to validate, only new
wiring. It can be bench-tested against a XIAO RP2350 running rev-1 firmware
before the hub exists.

`module.net` is generated, not drawn: `./gen_module.py` transforms rev-1's
exported netlist (`board/outputs/sch_final.net`) and applies the changes below.
For a circuit already measured on hardware, deriving from the proven netlist
beats retyping pin numbers out of datasheets, and it makes the deltas the diff
a reviewer actually reads. Every claim in this file is asserted in the
generator, so a wrong pin fails there rather than at bring-up.

## What changed from rev-1

| Change | Why |
|---|---|
| **R6, R7 — 51R at each buffer output** | The TLV9062 drives **100 pF**. A 500 mm cable is 25–50 pF plus the hub input, so this is required for stability, not decoration. It sits *outside* the feedback loop: the follower's output and inverting input stay tied and the resistor goes from that node to the cable, so the ~0 DC load means no error. |
| **Ground split into PWR_GND and AGND** | Up to 30 mA of row current flows in the cable, and it is *press-correlated* — the worst kind of artifact. ROW_VCC returns on PWR_GND; the pulldowns, muxes and op-amp return on AGND, which carries only ~3 mA. |
| **ROW_VCC on its own conductors; R5 (0R) removed** | Rev-1 bridged +3.3 V to ROW_VCC through a 0R jumper. Here the rail arrives separately so it can sag independently and be measured. |
| **R8 — Kelvin tap at the 595 VCC pins** | Makes the firmware's ratiometric correction *exact*: it reads the numerator of the sense divider, capturing rail sag and cable IR drop together. Referenced to AGND, which is why AGND must stay quiet. |
| **C9 → 22 µF, moved to ROW_VCC** | Sized for the row-change *transient*, not the static drop — the static drop is what R8 corrects. 30 mA settling in ~5 µs within 10 mV needs ~15 µF. |
| **TP1–TP4 test points** | ROW_DATA, ROW_VCC, SENSE_A/B. On the first assembled rev-1 board ROW_DATA was open, and that took a long time to find because every row reading identically looks like a sensor fault rather than a broken trace. |
| **J3 — 20-way cable to the hub** | Replaces the XIAO, which touched exactly 11 signals. |

Unchanged and deliberately so: the SN74LVC595A row drivers (nothing better
exists at 3.3 V), the CD74HC4067 muxes, the 3.3 kΩ pulldowns, and R3/R4.

## Cable pinout — J3

| | | | |
|---|---|---|---|
| 1 ROW_VCC | 6 AGND | 11 AGND | 16 MUX_S2 |
| 2 PWR_GND | 7 **SENSE_A** | 12 ROW_VCC_SENSE | 17 MUX_S3 |
| 3 ROW_VCC | 8 AGND | 13 PWR_GND | 18 ROW_DATA |
| 4 PWR_GND | 9 **SENSE_B** | 14 MUX_S0 | 19 ROW_CLK |
| 5 AVCC | 10 AGND | 15 MUX_S1 | 20 ROW_LATCH |

Each analog line is flanked by AGND; PWR_GND at 13 separates the analog block
from the digital one. Shield to PWR_GND **at the hub only**. FFC is fine for
static routing — use a discrete-wire harness where the cable flexes.

At the hub end, terminate each sense line with **1 nF C0G to AGND** as a charge
reservoir for the ADC sample-and-hold: 51 Ω × 1.05 nF is 54 ns, invisible
against a 15 µs dwell.

## Verify

    ./gen_module.py        regenerates module.net and BOM.csv, and checks both

The checks are falsifiable, which is the only reason to trust them. Injecting
each of these makes the generator fail:

| Injected fault | Caught as |
|---|---|
| 51R shorted, buffer straight to cable | `U7.1 appears on both ADC_A and SENSE_A_OUT` |
| 595 ground left on the analog reference | `595 ground is not on PWR_GND` |
| ROW_VCC sense tap not Kelvin'd | `ROW_VCC_SENSE is not Kelvin-tapped through R8` |
| An analog guard pin reassigned | `SENSE_A_OUT at pin 7 is not flanked by AGND at 8` |
| A column dropped from the mux | `expected 32 COL_n nets, got 31` |

## Still to do

The schematic and PCB. `module.net` is the electrical design and is complete
and checked; there is no `.kicad_sch` or `.kicad_pcb` yet. No KiCad is
installed in this environment, so a schematic generated here could not be
opened, rendered or ERC'd — that step needs a machine with KiCad on it. The
netlist imports directly, and rev-1's symbol and footprint assignments carry
over unchanged (see `BOM.csv`), so that is a mechanical step rather than a
design one.
