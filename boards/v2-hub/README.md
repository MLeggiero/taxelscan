# Hub

The board the eight front-end modules plug into. It holds the MCU that the
modules no longer have: it generates one set of scan control signals, fans them
out so all eight mats step in lockstep, and reads sixteen buffered analog lines
back. `scan.h` in the firmware already describes this hardware — `MAX_SENSORS`
is 8, and the mats "share the row walk and the mux select lines, so a frame
covers all of them at once" — so the contract this board has to meet was
written down before the board existed.

`hub.net` and `BOM.csv` are generated:

    ./gen_hub.py            writes both, then checks them
    ./check_faults.py       breaks the generator 13 ways to prove the checks work

Standard library only. It reads KiCad's symbol libraries directly, so it needs
KiCad installed but no Python packages.

## Why this one is written out rather than derived

The module could be *derived* — it is rev-1 with the MCU cut out, so every
connection in it came from a circuit already measured on hardware. The hub has
no predecessor, so `gen_hub.py` states it longhand.

What it still refuses to do is retype pin numbers. Every pin is looked up **by
name in KiCad's own symbol libraries** at generation time, so `p("U2", "RBIAS")`
returns the real pin 32 of the QFN-32 and `p("U2", "RBAIS")` stops the
generator. That is the same instinct as the other two boards — take the numbers
from something that already knows them — applied to the only source available
here.

Three parts changed from the plan, all so the pin numbers could be looked up
instead of copied out of a datasheet:

| Plan | Built | Why |
|---|---|---|
| USB3320C | **USB3300-EZK** | Same SMSC ULPI family, same QFN-32, same role — and KiCad has the symbol |
| 4 × 74LVC16244A | **8 × SN74LVC244A** | One octal per module. Eight TSSOP-20s carry the same 56 branches in *less* total area than four TSSOP-48s (229 mm² against 305), and one package beside each connector keeps a module's seven branches together instead of interleaving eight modules across four wide parts |
| 8 × TPS2553 | **4 × TPS2561** | A dual of the same thing |

rev-1 already orders an `SN74LVC595A` against a `74HC595D` symbol, so using a
`74HC244` symbol for an LVC part is what this repo does anyway.

## The pin assignment, which is the actual content

Everything else follows from it, and it is over-constrained.

**ULPI is not a choice.** OTG_HS in ULPI mode has one mapping on LQFP144, and
DIR and NXT are the tight ones: their alternates are PI11 and PH4, and this
package carries neither port I nor PH2–PH5. So twelve pins are fixed before any
decision is made — and **seven of them are ADC-capable**: PA3, PA5, PB0, PB1,
PC0, and the two `_C` pads PC2/PC3.

**That leaves the sense lines three pins short.** What can still reach ADC1 or
ADC2 is PA0, PA1, PA2, PA4, PA6, PA7, PC1, PC4, PC5, PF11, PF12, PF13, PF14 —
thirteen pins for sixteen sense lines, with no way to move ULPI and free more.

So the split is forced:

| | Pins | Carries |
|---|---|---|
| **ADC1** | PA0 PA1 PA2 PA4 PA6 PA7 PC4 PC5 | bank A, one per module |
| **ADC3** | PF3 PF4 PF5 PF6 PF7 PF8 PF9 PF10 | bank B, one per module |
| **ADC2** | PF13 | the housekeeping mux output |

ADC3 owns eight pins no other ADC can reach, which is what makes this work. The
cost is real and it lands in firmware: **ADC3 sits in power domain D3, so its
BDMA can only write SRAM4**, and a frame therefore arrives in two memories.

On *this* part that is only plumbing, because the H743's ADC3 is 16-bit like
the other two. On an H723 it is 12-bit and the same split would silently halve
bank B's resolution — which is a reason to keep the H743 rather than a
detail of it.

### The rest

The scan control group **reproduces rev-1's GPIO layout on purpose** — latch,
clock, data, then the four mux selects contiguous above them:

| rev-1 | hub | |
|---|---|---|
| GPIO1 | PE1 | ROW_LATCH |
| GPIO2 | PE2 | ROW_CLK |
| GPIO3 | PE3 | ROW_DATA |
| GPIO4–7 | PE4–PE7 | MUX_S0–S3 |

`scan.h` sets S0..S3 with a single store so no intermediate code ever appears
on the selects. Keeping the bit positions means that line ports to `GPIOE->ODR`
unchanged rather than being rewritten and re-reasoned.

Port D is the power-control port: **the low byte enables the eight modules and
the high byte reads their faults**, so each is one register access — and the
fault vector says *which* module, for the same reason the conditioning
telemetry is per mat rather than summed.

94 of 144 pins are used. Of the 50 free, four are spare ADC inputs (PC1, PF11,
PF12, PF14) and PA11/PA12 are the full-speed USB pair, so a non-ULPI fallback
port stays possible.

## The cable, and why the check for it is indirect

Each connector's pinout is the module's `J3` pinout, wired from
`../v2-module/module.net` rather than retyped — so if the module's cable ever
changes, this generator fails instead of producing a hub that looks right.

A mirrored connector is the worst plausible fault on this board: swap
`SENSE_A` and `SENSE_B` and every module reads its two banks crossed, on all
eight cables, in a way that looks like a sensor problem. **Comparing hub net
names to module net names cannot catch it** — both sides of the comparison come
from the same table, so mirroring the table moves them together. That check is
a tautology, and it passed the fault happily.

So the checks follow the copper instead:

| Pin carries | What is asserted |
|---|---|
| `SENSE_A_OUT` | the net reaches an **ADC1** pin |
| `SENSE_B_OUT` | the net reaches an **ADC3** pin |
| `ROW_VCC` | the net reaches a **current-limited switch output** |
| `ROW_VCC_SENSE` | the net reaches a **housekeeping mux input** |
| the seven control lines | traced back through the series resistor **and the buffer** to the MCU pin `CONTROL` names for that signal |
| grounds and `AVCC` | are the global nets of those names |

The buffer step has the same trap one level down. Which output a '244 input
drives is a fact about the part, and walking it with the same two lists that
wired it is circular again — so the A→Y pairing is recovered from the symbol's
own pin **names** (`nAm` drives `nYm`) and `BUF_IN`/`BUF_OUT` are checked
against it rather than trusted.

## Grounds

The two grounds meet **once**, at `R20`, a 0 Ω link in 0805 so it is not the
limiting conductor for the row return. One component rather than a plane
overlap, so the star point is a place on the board instead of an intention.

One thing is worth being plain about. `VSSA` is bonded to `VSS` inside the
package — giving them separate nets would be a fiction layout could not honour
— so **the ADC references PWR_GND while the sense signals return on AGND.**
The offset between them is AVCC's return current across the link: 24 mA × ~20 mΩ
≈ 0.5 mV, about 10 LSB at 16 bits.

That is removed by the dark reference, which is measured every frame through
the same path and subtracted. The assumption it rests on is that the offset is
**static** — true because AVCC's load is eight muxes and eight op-amps and does
not move with pressure. If anything dynamic is ever added to AVCC, this stops
being free.

Separately: the mux select lines are driven from buffers on PWR_GND into muxes
on AGND, so each transition puts ~100 pC into the analog return. It settles
long before the sample — that is what `settleUs = 15` is for — and the module
already had this property.

## Power

    350 + 240 + 24 = 614 mA on 3.3 V, 496 mA from the 5 V input

5 V straight to an LDO would burn 1.7 V × 0.6 A = 1 W in a SOT-23, so the buck
drops to 3.64 V first and each LDO keeps 340 mV of headroom — about 200 mW
total. `AVCC` gets the low-noise part (LP5907, 6.5 µVrms) because it feeds the
op-amps at the far end of eight cables.

Only `ROW_VCC` is switched per module. It is the rail with 22 µF of bulk on the
far end of 0.41 Ω of cable, so it is the one whose hot-plug inrush is otherwise
unlimited; `AVCC`'s 400 nF is not worth interrupting, and breaking the analog
rail on one module would disturb the others.

### A correction to the plan

The plan justified the separate 5 V input as *"~572 mA total. USB's 500 mA
default is not enough."* That compares a **3.3 V** total against a **5 V**
limit. The buck steps down, so the input current is the smaller number: 614 mA
of 3.3 V load is about **496 mA at 5 V — marginally under** the bus allowance.

The input stays, for the reason that actually holds. 500 mA is what a device
may draw *after* enumerating; before that it may take 100 mA. This board has to
bring up an MCU and a high-speed PHY to enumerate at all, and the modules pull
row current as soon as they are enabled. Sitting one percent under the
post-enumeration limit, on estimates this rough, is not a power supply.

## Verify

`./gen_hub.py` ends by asserting the whole design — 222 nets, 151 parts, 865
connections — and `./check_faults.py` then breaks the generator on purpose to
show the assertions bite. **All 13 injected faults are caught:**

| Injected fault | Caught as |
|---|---|
| SENSE_A and SENSE_B swapped in the cable map | `J1.7 is SENSE_A_OUT on the module, but M0_SENSE_B does not reach an ADC1 pin` |
| Two mux selects swapped in the cable map | `tracing M0_MUX_S1 back through its resistor and buffer lands on PE5, not PE4` |
| Two buffer outputs crossed | `1A0 drives 1Y0, but the fanout wires it to 1Y1` |
| A sense line moved to a pin with no ADC | `PE12 cannot reach ADC3` |
| Bank B put on ADC2 pins, which cannot hold eight | `PF11 cannot reach ADC3` |
| A module enable landed on a mux select pin | `PE4 is on both MUX_S0 and M0_EN` |
| One branch taken to the connector unterminated | `tracing M0_ROW_DATA back through its resistor and buffer lands on nothing` |
| The two grounds bridged a second time | `PWR_GND and AGND meet at 2 places` |
| A sense line left without its 1 nF terminator | `M0_SENSE_A has 0 terminations, want 1` |
| The analog regulator returned on the digital ground | `the AVCC regulator does not return on AGND` |
| One module's rail fed from the LDO, unswitched | `M0_ROW_VCC does not reach a current-limited switch output` |
| An MCU ground pin left off the plane | `MCU VSS pin(s) ['130'] are not on PWR_GND` |
| A pin name misspelt | `U2 (Interface_USB:USB3300-EZK) has no pin named 'RBAIS'` |

The 1 nF terminators are here because the module's README asks for them at this
end — 51 Ω × 1.05 nF is 54 ns against a 15 µs dwell — so that requirement is
now a part on a board rather than a sentence.

## Still to do

**The pin assignment needs confirming in CubeMX before layout.** Package pin
*numbers* are looked up from KiCad and are right. What is asserted from
knowledge rather than from a file is the **alternate-function mapping** — which
ports carry ULPI, and which reach which ADC. Two things corroborate it: the
plan independently recorded that ULPI costs seven ADC-capable pins, and this
assignment produces exactly seven; and the resulting shortfall is what forces
ADC3. Neither is a substitute for opening CubeMX against the exact part.

Two component values are estimates and are marked in the BOM:

- `R1/R4/R7/R10` 150 kΩ — the TPS2561 current limit. Read off the datasheet
  curve for the limit you want, which should sit above the 22 µF inrush and
  below whatever the cable survives.
- `L1` 2.2 µH and the 37.4 k/10 k divider — standard TPS563201 values for
  3.64 V, worth checking against the ripple you want.

Then: schematic and PCB. Neither exists yet. The schematic needs custom symbols
only if the substitutions above are reverted; as generated, every part has a
stock KiCad symbol.
