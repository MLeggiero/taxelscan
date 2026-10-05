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
| `rev3/` | The current design (**TaxelScan v3**). One 32 × 32 carbon-nanotube mat per board, RP2354A + 16-bit LTC1865L, RS-485 so eight boards share one harness. 60.3 × 35.9 mm, 4 layers, parts on top only. | **Routed and verified, not yet built (28 September 2026).** 140 nets, 114 parts in the netlist (109 placed on a middle board, 111 on an end board), 3,004 track segments, 352 vias. KiCad 10.0.5: 0 unconnected, 0 copper DRC errors, schematic parity clean apart from net-name prefixes, ERC 0 errors; 36 of 36 fault probes behave. `fab/` holds the JLCPCB package; every BOM line has an LCSC number. Open: sensor, hot-plug and harness-drop measurements, and the firmware ([routing status](rev3/ROUTING_STATUS.md), [audit](rev3/AUDIT.md) sections 8–9) |
| `rev4/` | rev-3 **without the external ADC**: the LTC1865L and its reference network (U8, C8, R10, C10, C26) removed, both sense banks on the RP2354A's internal 12-bit ADC (GPIO27/ADC1, GPIO29/ADC3). Seven parts moved beside the MCU's ADC pins; same 60.3 × 35.9 mm outline and connectors. | **Routed and verified, not built (5 October 2026).** 135 nets, 109 parts in the netlist (104 placed on a middle board, 106 on an end board). Re-routed from scratch for straight 0 / 45 / 90-degree routing (0.2 % of the copper off that grid, against rev-3's 67 %; nothing on the +3.3 V plane layer; both USB pairs pre-routed as pairs). KiCad 10.0.6: 0 unconnected, 0 copper DRC errors, parity clean apart from net-name prefixes, ERC 0 errors; 39 of 39 fault probes caught. `fab/` holds the JLCPCB package. About $14.60 of parts a board against rev-3's $33.08 ([placement analysis](rev4/PLACEMENT.md), [routing status](rev4/ROUTING_STATUS.md)) |
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
