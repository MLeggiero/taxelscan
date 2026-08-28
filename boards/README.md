# Boards

Each directory is a self-contained KiCad project. Symbol and footprint
libraries that more than one board uses live in `../libraries/` and are
referenced from each board's `fp-lib-table` / `sym-lib-table` as
`${KIPRJMOD}/../../libraries/...`, so there is one copy of FlexiTac rather than
one per board.

| Board | What it is | State |
|---|---|---|
| `rev1/` | The shipped single-sensor reader: XIAO RP2350, 4× SN74LVC595A, 2× CD74HC4067, TLV9062. 52 × 46 mm, 4 layer. | Built and measured on hardware |
| `fork-adapter/` | Passive adapter for two-finger use | Built |
| `v2-module/` | Front-end module for the 8-mat reader. One per mat: rev-1's analog chain with the MCU replaced by a 20-way cable to the hub. | Schematic generated and checked; **no PCB yet** |
| `v2-hub/` | The hub the eight modules plug into: STM32H743 + USB3320C, 8 cable connectors, power tree. | **Not started** — design is in the plan only |

## Opening v2-module

    boards/v2-module/module.kicad_pro

It is written in KiCad 7 format; KiCad 8 or newer will offer to upgrade it on
open, which is expected. See `v2-module/README.md` for how it is generated and
what has and has not been checked.

## Note on rev1's library table

`rev1/fp-lib-table` lists a `Samacsys` footprint library that does not exist in
the repository — only `Samacsys.kicad_sym` and `Samacsys.3dshapes` are present.
That predates this reorganisation and was left alone rather than silently
changed; KiCad will warn about it on open.
