# Boards

Each directory is a self-contained KiCad project. Symbol and footprint
libraries that more than one board uses live in `../libraries/` and are
referenced from each board's `fp-lib-table` / `sym-lib-table` as
`${KIPRJMOD}/../../libraries/...`, so there is one copy of FlexiTac rather than
one per board.

| Board | What it is | State |
|---|---|---|
| `rev1/` | The shipped single-sensor reader: XIAO RP2350, 4× SN74LVC595A, 2× CD74HC4067, TLV9062. 52 × 46 mm, 4 layer. | Built and measured on hardware. **Do not modify** — it is still the board to build for single-sensor use, and all three v2 generators derive from it |
| `fork-adapter/` | Passive adapter for two-finger use | Built |
| `rev3/` | The current design. One 32×32 mat per board, rev-1's analog chain rescaled for a 1 MΩ carbon-nanotube sensor, RP2354A + 16-bit LTC1865L, and RS-485 so eight boards share one harness. 60.3 × 35.9 mm (compacted on 17 September from 70 × 38 mm, which grew from 54 × 36 mm on 14 September), 4 layer, single-sided assembly. | **Routed and compacted (17 September):** 0 unconnected items and 0 copper DRC errors in KiCad 10.0.5 with zones filled; 110 parts plus 3 fiducials, 3,065 track segments, 347 vias; RS-485 pairs coupled. Schematic parity shows only the net-name prefix warnings; six silkscreen warnings remain. The 16 September design review's layout fixes are in. On 17 September a parts, circuit and JLCPCB manufacturability review corrected ten wrong LCSC numbers, moved every resistor to thin film, turned the core-regulator inductor L1 the right way round, and split the assembly files into middle and end boards (terminators). `fab/` is regenerated, and every BOM line has a verified LCSC number. Order with epoxy-filled, capped vias and Standard PCBA (`rev3/fab/ORDER-NOTES.txt`). The SYNC pair's driver enable is now on GPIO13 with a pull-down, so the master can send frame-starts. On 21 September every check was re-run independently from the files (AUDIT.md section 8): generator, fault probes, ERC, netlist match, DRC with parity, fab package diffed against the board, JLCPCB limits, and the two datasheet items that had been assumed (LTC1865L supply/reference range; the inductor dot on the output end) read from the sources. Layout fixes followed: the LED via moved off the bank-B sense input, the datasheet's layer-2 cut-out under the core-regulator switch node, a 1 Ω + 10 µF hot-plug damper and a USBLC6-2P6 ESD array at the USB-C connector, and the buck's 100 nF input capacitor moved to its VIN pin (AUDIT.md section 9). Still open: hot-plug overshoot is unmeasured (the damper is on the board for it), and there is no firmware; see [routing status](rev3/ROUTING_STATUS.md). Front end not yet measured on hardware |
| `v2-module/` | Front-end module for the 8-mat reader. One per mat: rev-1's analog chain with the MCU replaced by a 20-way cable to the hub. | **Shelved** — see below. Schematic was clean on nets, BOM and ERC; PCB generated from rev-1's routed board, 49.7 x 40.6 mm, routing unfinished |
| `v2-hub/` | The hub the eight modules plug into: STM32H743 + USB3300 ULPI PHY, 8 cable connectors, per-module current limiting, power tree. | **Shelved** — see below. Netlist and BOM were generated and checked, 222 nets, 151 parts, pin assignment resolved. No schematic or PCB |

## Why v2 is shelved

The sensor changed to a **1 MΩ carbon-nanotube film**, roughly 300× the source
impedance the v2 split was designed around. That split put 500 mm of cable
between the sensor and the amplifier, and `v2-module/README.md` spends most of
its length on the mitigations that required — 51 Ω isolation, split grounds, a
Kelvin tap, 1 nF terminators at the hub. Every one of those gets 300× harder
at the new impedance.

`rev3/` puts the converter back beside the sensor and moves the multi-board
problem onto a differential bus instead of onto analog wiring. The two
generators are kept rather than deleted: their method — derive from rev-1's
measured netlist, assert every claim, inject faults to prove the assertions
bite — is what `rev3/gen_rev3.py` and `rev3/check_faults.py` follow.

## What stays local

`rev3/backups/` and the repository-level `tmp/` are listed in `.gitignore`. The
first holds the dated board and project copies the rev-3 notes cite as recovery
points; the second is the scratch area of the rev-3 routing tools (router runs,
DRC reports, a private copy of their Python dependencies, the grid-router
binary). Everything the notes describe as a result is committed; those two
folders are the working state behind it and stay on the machine the work was
done on.

## Opening v2-module

    boards/v2-module/module.kicad_pro

It is written in KiCad 7 format; KiCad 8 or newer will offer to upgrade it on
open, which is expected. See `v2-module/README.md` for how it is generated and
what has and has not been checked.

## Note on rev1's library table

`rev1/fp-lib-table` used to list a `Samacsys` footprint library that does not
exist in the repository — only `Samacsys.kicad_sym` and `Samacsys.3dshapes` are
present — so KiCad warned about it on every open. Nothing in rev-1's schematic
or board referenced a `Samacsys:` footprint, so the entry was dead weight
rather than a missing file, and it has been removed. That is the only change
ever made to `rev1/`, and it alters no netlist, footprint or output.
