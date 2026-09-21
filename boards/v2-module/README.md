# Front-end module

One of these sits at each mat. It is the rev-1 reader board with the MCU
removed and a cable put in its place — same row drivers, same muxes, same
buffer, same footprints — so there is no new analog to validate, only new
wiring. It can be bench-tested against a XIAO RP2350 running rev-1 firmware
before the hub exists.

`module.net` is generated, not drawn: `./gen_module.py` transforms rev-1's
exported netlist (`../rev1/outputs/sch_final.net`) and applies the changes below.
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

## The schematic

`module.kicad_sch` is generated the same way and for the same reason:
`./gen_schematic.py` transforms rev-1's schematic rather than redrawing it, so
every 595, mux and buffer keeps the position, orientation and labels it was
verified with. rev-1 was itself produced by a generator (`flexitac_gen`), so
this fits the existing workflow rather than fighting it.

It is written in **KiCad 7 format on purpose**. kiutils writes that natively,
`kicad-cli` can then load it and export a netlist to check, and KiCad 10 opens
and upgrades it on the way in. Emitting v10 directly would mean nothing in this
toolchain could verify it.

**Power symbols become global labels.** Partly because kiutils' round-trip does
not preserve whatever makes an implicit power net resolve — rev-1's three power
nets come back as `<NO NET>` — but mostly because this board has *two* grounds.
PWR_GND carries up to 30 mA of press-correlated row current and AGND must carry
none; two identical-looking ground symbols is exactly how that distinction gets
lost at layout. Named labels put it on the sheet where it can be seen.

### Verification

`./gen_schematic.py` ends by handing the generated file to **KiCad itself** and
asking three questions:

    KiCad loads it and exports 87 nets
    all 87 match module.net exactly
    all 30 parts match BOM.csv in value and footprint
    ERC: 0 errors, 5 warnings

These are worth more than everything before them, which is only the script
agreeing with itself. This is KiCad's own parser, connectivity engine and rule
checker reading what was written.

Each has a blind spot the others cover, and both of the first two have already
been caught out by the same component:

- **nets** caught C9 on the first run — 85/87, because the bulk cap had moved
  to ROW_VCC in `module.net` but stayed on the analog rail here.
- **parts** caught C9 again, much later. Fixing its *net* left its *value*
  alone, so the sheet still said 10 µF on an 0603 land while `BOM.csv` said
  22 µF on an 0805 — below the ~15 µF the transient needs, on a pad the part
  does not fit. A netlist comparison cannot see a value, and ERC does not read
  them. `BOM.csv` is now the single source of truth for what each part *is*,
  applied by the generator rather than kept in step by hand.
- **ERC** had never run at all until 2026-08-28, because KiCad 7's `kicad-cli`
  had no `erc` subcommand. The first run found 29 violations. Four rails were
  undriven — rev-1's lone PWR_FLAG left with the `#FLG` symbols and nothing
  replaced it — and nineteen more were fragments of deleted parts: the wires,
  labels and no-connect flags of the XIAO, R5 and that PWR_FLAG, still on the
  sheet after the symbols went. Debris hanging off an otherwise correct net
  does not change what the net joins, which is exactly why the netlist check
  never saw it, and why deleting it is safe — the 87 still have to match
  afterwards.

The five remaining warnings are all `lib_symbol_mismatch`, on the four test
points and J3. They are true and deliberate: `Connector:TestPoint` is
synthesised because rev-1 has nothing close enough to reuse, and `Conn_01x20`
is derived from rev-1's proven 32-way part so the pin geometry cannot disagree
with it. Both therefore differ from KiCad's current stock symbols. Silencing a
warning that is telling the truth seemed worse than explaining it here.

`module.pdf` is the rendered sheet, for looking at without KiCad installed.

### Running the generators

Both need Python packages that are not in the standard library:

    pip install kiutils sexpdata

`gen_schematic.py` finds `kicad-cli` on PATH, and failing that in KiCad's usual
install location on Windows, macOS and Linux — it is not on PATH on Windows.
Without it the script still writes the file but skips every check and says so.

### Opening it

    boards/v2-module/module.kicad_pro

KiCad 8 or newer will offer to upgrade the file format on open; that is
expected. The only non-stock library it needs is `FlexiTac`, which lives in
`../../libraries/` and is wired up in this project's `fp-lib-table` and
`sym-lib-table` — everything else ships with KiCad.

## The PCB

`module.kicad_pcb` is generated too, and from the strongest source of the
three: rev-1's board was fabricated, assembled and measured, so its row
fanout, mux breakout and sense routing are known to work *at the geometry they
are drawn at*. `./gen_pcb.py` transforms it. **rev-1 is opened read-only and
is not modified** — it remains the board to build for single-sensor use.

    ./gen_pcb.py        writes module.kicad_pcb, then checks it with KiCad

| | |
|---|---|
| **Kept** | 890 track segments and 152 vias at rev-1's exact coordinates — all 32 rows, all 32 columns, the shift-register chain, the mux selects, ROW_CLK and ROW_LATCH below R3/R4 |
| **Removed** | A1 and R5, and the 142 tracks and vias on the nets they terminated — those nets run to J3 now and have to be redrawn |
| **Split** | 102 GND and +3.3V tracks and vias, each assigned to PWR_GND/AGND or ROW_VCC/AVCC by which side's pad it lies nearest. Two were too close to call and are printed for review rather than guessed |
| **Board** | 49.7 × 40.6 mm, down from rev-1's 52 × 46 — **16% smaller** |

The width is set by J1 and J2, which span 47.7 mm side by side. Losing the
XIAO buys height and interior room, not width; getting below ~49 mm would mean
putting the two sensor connectors on opposite edges, which is a different
board rather than a smaller one.

### The ground split on copper

Six zones where rev-1 had four: PWR_GND and AGND on In1.Cu and B.Cu, ROW_VCC
and AVCC on In2.Cu, divided at y = 122.6 mm. Above the line the row drivers
and their decoupling; below it the pulldowns, muxes and buffer. **The two
grounds never meet on this board** — they are joined at the hub, which is the
entire point of splitting them.

C7 and C8 move down into the analog half. They are AVCC/AGND decoupling that
rev-1 could put anywhere because its ground was unified; C8 in particular sat
at (125, 113.8), deep inside the row-driver block. C9 moves too, because at
22 µF it needs an 0805 land and the larger part reached across the split line.

### Verify

    all 239 pads in module.net are on the board with the right net
    DRC: 57 violations, 56 unconnected items

The pad check is the board's version of the schematic's netlist comparison,
and it is what makes the board and the verified design the same circuit rather
than two things that resemble each other. DRC runs with `--refill-zones`,
because an unfilled board reports every plane connection as unconnected, and
with `--schematic-parity`, which is KiCad comparing the board against
`module.kicad_sch` directly.

`pcb_top.png` is the render.

## Still to do

**Routing, in pcbnew.** The generator does placement, nets and planes; it does
not route, and the remaining work is the interactive kind:

| | |
|---|---|
| **56 unconnected items** | J3's twenty nets, R6/R7/R8, and the four test points. Everything else is already routed |
| **3 parts need hand placement** | C7, C8 and H1 have no clear space at the position they belong in — C8 currently overlaps U5. Each needs moving with a local reroute; the generator prints them on every run rather than quietly placing them somewhere useless |
| **8 solder_mask_bridge** | J3's 0.5 mm pitch. J1 and J2 have the same pitch and pass, so this is a mask rule to set, not a layout fault |
| **11 net_conflict** | The J1/J2/J3 shell pads, tied to AGND on the board but absent from the schematic — `Conn_01x32` has no such pin. Worth adding to the symbols so the two stop disagreeing |
| **13 silk warnings, 7 track_dangling, 2 via_dangling** | Cosmetic and cleanup |

The schematic is checked three ways on every run — connectivity against
`module.net`, parts against `BOM.csv`, and ERC — and passes all three with no
errors.
