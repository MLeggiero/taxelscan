# Audit tooling, 10–14 September 2026

The scripts that produced the re-placed and re-routed rev-3 board after the
audit in `../AUDIT.md`, and then the grown 70 × 38 mm board. They run with
KiCad's own Python (`"C:/Program Files/KiCad/10.0/bin/python.exe"`) and
expect `../../../tmp/` to hold `grid_route.exe` (the C++ A* from
`tmp/grid_route.cpp`) and the `usb-power-deps` site-packages (numpy, scipy,
shapely, kiutils). Paths are absolute to this checkout; edit `ROOT` at the
top of each file to move them. Freerouting 2.2.4 is expected at
`%LOCALAPPDATA%/freerouting/freerouting.exe`.

| script | job |
|---|---|
| `pcb_audit.py`, `pcb_detail.py` | the measurements behind AUDIT.md: pin-to-cap distances, per-net length per layer, pair gaps, neighbour analysis |
| `place_fix.py` | the first (54 × 36 mm) re-placement: netlist sync, placement table, rule areas, overlap report |
| `place_grow.py` | the grown board: outline, zones, rule areas, placement table, pad-1-faces-its-pin flips, overlap / pad-conflict / cap-distance report, `tmp/placement-grow.png` |
| `route_fix.py` | staged local router: `rip`, `fanout`, `stubs`, `analog`, `pairs`, `power`, `rowcol`, `digital`, `gnd`, `close`, `clean`, `prune`, `drc` |
| `../dsn_keepout.py` | hand the routed copper to freerouting as keepouts and drop the hand-routed nets from its network (freerouting crashes on pre-routed wires) |
| `fr_stage1.sh` | `fanout stubs analog gnd pairs power` + DRC |
| `fr_stage2b.sh [passes]` | keepout DSN → freerouting headless → `import_ses.py --apply --add` → DRC → close 3 → maze → clean → prune → close 2 → prune → probe |
| `fr_all2.sh` | stage 1, strip the fanout stubs, export DSN, `protect_dsn.py --none --no-class`, stage 2 |
| `fr_maze.sh`, `maze_wrap.py` | `maze_route.py --apply` under KiCad's Python (adds `kiutils` to `sys.path`), then clean / prune / close / probe |
| `fix_5v.py` | rip and re-lay +5V on the outer layers around the MCU block |
| `unseal.py [radius]` | rip unrelated copper around DRC-unconnected pads, route those first, re-drop +3.3V vias (trades opens for clearance errors on the last five pins — kept for reference) |
| `probe_all.py` | for every DRC-unconnected pair, whether the start or the goal is sealed |
| `render.py board out.png x0 y0 x1 y1 [px/mm]` | quick copper render: F.Cu red, B.Cu blue, In2 green, vias black |

Run the scripts from `boards/rev3` (the shell scripts `cd` there themselves).
Launch the long ones detached — a foreground shell kills them at ten
minutes — e.g. from PowerShell:

    Start-Process "C:\Program Files\Git\bin\bash.exe" -ArgumentList "C:/.../tmp/fr_all2.sh" -RedirectStandardOutput run.log -WindowStyle Hidden

Rules the routers enforce that KiCad's DRC also checks: 0.15 mm track and
clearance everywhere except inside the `U9_escape`, `U13_escape` and
`U8_escape` rule areas (0.1 / 0.1, see `../rev3.kicad_dru`); analog nets on
`F.Cu` only; `In1.Cu` never routed; `In2.Cu` typed `power` for freerouting so
it routes on `F.Cu`/`B.Cu` only. Note that `pcbnew.SaveBoard()` on a board
loaded without its project rewrites `rev3.kicad_pro` with default rules —
`place_grow.py` restores the project file after saving for that reason.

## Finishing the grown board, 15 September 2026

The second set of tools took the grown board from 5 unconnected to 0. It also re-laid
the RS-485 pairs as pairs and mended the +3.3V pour. They run with KiCad's Python
and find `tmp/usb-power-deps` themselves; KiCad's Python ignores `PYTHONPATH`. They
import each other from this folder and keep scratch files, DRC reports and
`grid_route2.exe` in `<checkout>/tmp`. Build the router once, from a Visual Studio
x64 developer prompt in this folder:

    cl /nologo /O2 /EHsc /std:c++17 grid_route2.cpp /Fe:..\..\..\tmp\grid_route2.exe

| module | job |
|---|---|
| `lr.py` | exact-geometry board model: pad polygons, tracks, vias, drill holes and rule areas per layer; `check_track` / `check_via` against 0.15 mm, 0.1 mm inside the escape areas, 0.25 mm hole clearance and the edge; `masks` (free and via-legal cells for a window); `add`, `remove`, `save`, `drc` |
| `rr2.py` | `route2`: A* on a 0.025 mm grid over F.Cu / In2.Cu / B.Cu through `grid_route2.exe`, with via cost, hugging and keepouts, then line-of-sight simplification and an exact re-check. Also `comps` (connectivity the way KiCad sees it: an end must lie inside the other copper, crossing is not joining), `connect_once`, `commit`, `delete_dead`, `prune` (DRC dangling, then peel), `rrr` |
| `grp.py` | `connect_all`, `peel`, `net_len`, `try_orders` (re-route a group of nets from a clean copy in several orders, keep the best) |
| `pairgeo.py`, `pairs_apply.py` | the hand-laid BUS/SYNC pair geometry and the step that lays it: rip what it hits, re-stitch GND pads first, put displaced nets back on F/B, DRC-driven close loop |
| `strapgeo.py` | hand-laid escapes for U9 pins 32–35 (ADDR0–2, USB_PWR_FAULT), with the reasoning |
| `addr_group.py` | the address-strap group re-route of the first pass |
| `reach.py` | can a net get from one pad to the other at all (free space joined through via-legal cells)? Tells a sealed pin from a bad routing order |
| `pour.py` | the pieces of a plane pour computed from copper geometry, no zone fill; which of the pour net's vias land on the main piece |
| `zones_check.py` | after a real zone fill: every fill polygon with its area and the vias and pads inside it |
| `stats.py` | counts, copper per layer, length / vias / layers per net, pair skew, zone pieces |
| `finalize.py` | R1's reference off U5, zone fill, save, project and rules put back, DRC with schematic parity |
| `insp.py`, `rend.py` | dump a board to JSON; render a window of the dump with chosen nets highlighted |

The `impl_*` files are the stage scripts as they were run, kept as a record. They
read and write stage boards under `tmp/work/` and take the reference project from
`tmp/start-backup/`, neither of which is kept.

| script | stage board | what it did |
|---|---|---|
| `impl_1_c17_vbus` | `stage1_vbus` | C17.2 joined to C18.2 on F.Cu at 0.1 mm; USB_VBUS_DET with local rip-up |
| `impl_2_busdi_r18` | `stage2_busdi` | R18 turned 180°, FB joined R19.1→R18.2 directly, the +3.3V sense re-landed; BUS_DI/BUS_RO/BUS_DE re-routed as a group |
| `impl_3a_r3r4_swap` | `group_base_r34` | R3 and R4 (both 33 Ω) swapped places so ROW_CLK_MCU and ROW_LATCH_MCU stop crossing |
| `impl_3b_rowdata_pocket` | `diag_r34` | the two MCU-side nets routed; U9.5's pocket and via sites measured |
| `impl_3c_rowdata` | `cand_rowdata` | VCORE via hub by C24 lifted, local rip, ROW_DATA under a keepout, best of 12 orders |
| `impl_3d_rowdata_close` | `stage3_rowdata` | ROW_CLK/ROW_LATCH, close loop, prune |
| `impl_4_pairs_addr` | `stage4_pairs`, `stage5_addr` | pairs laid; address straps re-routed with the USB-power signals beside them |
| `impl_5_ccout2_syncjog_r27` | `stage5d` | USB_CC_OUT2 30 → 15 mm; the SYNC B.Cu lanes jog 0.5 mm south under R27 so R27.2 reaches a GND via |
| `impl_6a_corner_spi_transplant` | `stage5t_base` | U9's bottom-right corner ripped (12 nets); stage 3's ADC_SDO/SCK/SDI/CONV copper put back (its order search failed; kept as the record of why) |
| `impl_6b_corner_status_cc_transplant` | `stage5v_base` | stage 3's STATUS, USB_CC_OUT1/2 and USB_BUS_EN copper put back |
| `impl_6c_corner_strap_escapes` | `stage5y_base` (`--verify`) | strapgeo's escapes laid, ADC_SDO's ring via moved; its own outward routing split the pour and was replaced by 6d |
| `impl_6d_corner_route_fb` | `stage6k` | straps, USB_PWR_FAULT and USB_CC_OUT1 routed outward, F/B first: 0 unconnected |
| `impl_7_pour_signals_off_in2` | `stage6m` | per net: lift the In2 copper, route on F/B, keep only if the +3.3V pour improves (only USB_ILIM_LOW moved) |
| `impl_8_pour_mux_s1` | `stage6n` | MUX_S1's In2 hops re-routed clear of the MCU's +3.3V island: main pour 20 → 28 of the 35 +3.3V vias |
| `impl_9_ep_vias_power_fiducials` | `fix5` | the 16 September review fixes: ADDR1/ADDR2/MUX_S0 re-routed clear of the nine via sites in U9's exposed pad, ground vias placed in it, `+5V_USB` taken off In2, `USB_BUS_SW` widened, fiducials placed |
| `impl_10_power_before_strap` | `fix6` | `+5V_BUS` has the stronger claim on the outer layers than ADDR2 does, so it routes first — both 5 V rails end up entirely on F/B, and the fiducials move clear of every courtyard |
| `impl_11_refdes` | `fix8` | 32 reference designators made visible at 0.8 mm, each placed clear of pads, silkscreen, the board edge and the other labels; fiducials marked board-only so schematic parity ignores them |

Then `finalize.py` on the result and `install_board.py` (backup to
`../backups/implement-2026-09-15/`, DRC with parity in place).
`impl_11_refdes.py` is the 0.8 mm placement that produced `fix8`; until
17 September this folder held its 0.5 mm draft under that name by mistake.

What this pass learned:

- **Saving under a new name writes a default project.** `BOARD.Save()` of
  `stageN.kicad_pcb` creates `stageN.kicad_pro` with default rules, and kicad-cli
  reads the project and custom rules named after the board. The result is ~500
  false clearance and ~200 false width errors. `lr.drc()` and `finalize.py` copy
  the reference `.kicad_pro` and `.kicad_dru` under the board's name before DRC.
- **Order searches cannot beat capacity.** U9's corner had three exits for four
  pins until ADC_SDO's ring via moved. Check `reach.py` before searching orders;
  if the pin is shut in, lay the escapes by hand.
- **Signals on In2 split the +3.3V pour** even when DRC is clean. Every island
  keeps a via, so nothing reports as unconnected. Check `pour.py` after any In2
  routing: at one point the MCU's eight plane drops sat on a 21 mm² island.
- **A search window can be too small.** `connect_all`'s window is the terminals'
  bounding box plus 2–4.5 mm. A reachable net can still fail inside it, so widen
  the margin before concluding it is sealed.
- **Widening copper on a shared plane layer costs plane.** Taking the 5 V runs on
  In2 from 0.30 to 0.60 mm fixed their current density and cut the +3.3V pour
  into 45 pieces. Moving them to F/B instead fixed both: outer 1 oz carries ~1 A
  at 0.30 mm, and the pour's main piece grew by 54 mm².
- **Routing order is a claim on space.** ADDR2 and `+5V_BUS` both wanted the
  outer layers; whichever routed first got them. The rail has the stronger claim,
  so it goes first.
- **Silk has its own rules.** Reference designators need the board's minimum text
  height (0.8 mm here) and must clear silkscreen as well as pads, or DRC returns
  them as `text_height` and `silk_overlap`.
- **Fiducials are not schematic parts.** Mark them board-only, and out of the BOM
  and position files, or `--schematic-parity` reports them as extra footprints.

## Compaction, 17 September 2026

These tools took the routed 70 × 38 mm board to 60.3 × 35.9 mm without
re-routing it. They run with KiCad's Python, like the tools above.

| script | job |
|---|---|
| `compact_lp.py BOARD x\|y GMAX [--out OUT]` | one compaction pass: a linear programme over how far parts, vias and track corners shift along the axis toward the low edge (`--from-high`: the high edge). It keeps every connection and every clearance, and applies the largest gain with the least total movement. `--pin-moving` / `--hold` tie parts to the moving or the fixed edge; `--drop-nets` / `--drop-parts` ask what re-routing or moving those would buy; `--report N` prints the constraints that bind |
| `compact_cycle.sh START ROUNDS PREFIX` | passes toward the right, left, bottom and top edges in turn until a round gains under 0.05 mm, J1/J2 held to their edge marks; then `compact_verify.py` |
| `compact_verify.py BOARD [TAG]` | DRC against the board's own project and rules, the outline size, the +3.3V pour's pieces |
| `edge_ffc.py IN OUT` | put the top edge back on the FH12 footprints' board-edge marks |
| `smooth_corners.py IN OUT [DEG]` | straighten free corners sharper than DEG where the straight segment clears |
| `refdes_replace.py IN OUT` | keep each visible label where it clears pads, silkscreen, the edge and other labels; otherwise the nearest clear spot around its part |
| `corner_angles.py BOARD...` | corner-angle histogram, the sharp ones, total length and pair skew, for comparing boards |
| `finalize_compact.py BOARD PRO` | `finalize.py` without its fixed R1 label position |
| `install_board.py BOARD [BACKUP_DIR]` | back up the live board files, install, DRC with parity in place |

The run was:
1. `compact_lp.py` toward the right and bottom edges until it stopped gaining.
2. The same toward the left and top edges.
3. `compact_cycle.sh`, which went six rounds.
4. `edge_ffc.py`, then one more `compact_cycle.sh` round.
5. `smooth_corners.py`, `refdes_replace.py`, `finalize_compact.py` beside the
   sheet, and `install_board.py board compact-2026-09-17`.

How the model works, and what it learned:

- **The shapes are exact, so tight pairs stay legal.** A via, hole or track end
  is a disc: its centre is tested against the other shape grown by radius plus
  clearance, at the centre's row. A track's straight part is a band whose two
  ends move with the track's end nodes. A pad or courtyard is a convex polygon
  that moves with its part. Gaps are linear between corner rows, so checking
  those rows is exact. The first version used 12-sided polygons for round
  things, which overstate them by 3.5 %. Tight pairs then looked like they
  already overlapped, and the fallback glued a via to one end of a track that
  turned into it.
- **A turning track bulges.** If a band may turn by θ, offset its sides by
  half-width / cos θ; that keeps the sides exactly on the true outline. The ends
  need the same factor on their discs. Without it DRC found 0.001 mm errors
  between the two USB lines.
- **Pairs already at minimum clearance must be held to the exact clearance.**
  "No closer than now" is not enough while the model is even slightly
  conservative. Those rows get a heavily penalised slack, so an unreachable
  one cannot make the programme infeasible.
- **Each pass may turn a track 15° from where it is.** Repeated passes loosen
  the board step by step; the left edge kept gaining about 0.4 mm a round
  until round five.
- **Compaction is from one edge; try both.** Keeping the left and top edges
  fixed left 5.8 mm and 1.4 mm on the table.
- **Pads carry their own clearance.** The fiducials have 0.6 mm and a 0.5 mm
  mask margin. `compact_lp.py` takes the larger of the netclass or area
  clearance and the pad's own.
- **Footprints can mark where the edge belongs.** The FH12 footprints do, on
  F.Fab. Once the edge had moved, `edge_ffc.py` put it back and later passes
  held J1/J2 to it.
- **Label text stays out of the model.** Labels are re-placed afterwards, and
  one label had pinned R1 and cost 1.1 mm. Footprint silkscreen outlines do
  stay inside the board.
- **Sharp corners accumulate.** A 0.9 mm stub beside J4 turned a little each
  pass until SYNC_N doubled back at 17°. `smooth_corners.py` straightened it
  and three others.

## Review, 17 September 2026

Tools behind AUDIT.md section 7.3, the parts, circuit and JLCPCB review. They run
with KiCad's Python (the two lookup scripts with any Python 3).

| script | job |
|---|---|
| `dfm_measure.py BOARD` | fabrication geometry from the board: track widths, copper gaps per layer, via sizes and rings, hole-to-hole, copper to edge, silkscreen sizes, mask dams, via-in-pad, courtyard spacing, parts near the edge |
| `dfm_jlcpcb.py BOARD` | JLCPCB-specific checks: component holes to copper, filled vias near holes, vias near mask openings, part spacing against JLCPCB's class table, silkscreen the fab will clip, parts inside the 2.5 mm assembly margin |
| `sync_from_net.py BOARD NET OUT [BOM]` | set footprint values from BOM.csv and pad nets from rev3.net; turns L1 180° (it was written for the polarity fix) |
| `lcsc_lookup.py C12345 ...` | read-only LCSC product lookup: model, brand, package, stock, description |
| `jlc_search.py "keyword" ...` | read-only JLCPCB parts search: code, model, brand, package, stock |

What this review learned:

- **A part number is not a value.** `gen_rev3.py` asserted "180k", but the number
  next to it bought a 200k 0603. Ten numbers were wrong in ways no netlist
  check can see. Look every number up on the day it goes into the BOM, and keep
  the manufacturer part number in the note so a wrong one is visible.
- **The silicon vendor's orientation beats the passive vendor's.** Abracon's
  drawing calls the dot the current-entry end. The RP2350 datasheet's figures put
  it at the output end, and the regulator is theirs.
- **Count via holes in pad openings, not via centres in pads.** The centre test
  found 63; the hole test found 153. Tenting does not reach inside a pad's mask
  opening.
- **A "receive-only" safety strap needs a master that is not one of the boards.**
  U11 was strapped so no board could drive SYNC. Then the USB power work made
  a board the master.

The SYNC driver change (AUDIT.md 7.4) was made by two stage scripts, kept as a record:

| script | what it did |
|---|---|
| `impl_12a_sync_de_prep.py IN OUT R37_UUID` | new net `SYNC_DE` on U9.17 and U11.3; U11.3's ground stub and via removed; R37 copied from R31 and linked to its schematic symbol |
| `impl_12b_sync_de_place_route.py IN OUT` | legal R37 spots near U11 tried in order until `SYNC_DE` routed (F/B, then F/In2/B) and R37.2 reached ground, with the +3.3V pour still holding its 27 connections |

- **Adding nets and footprints from Python needs ownership handed to the board.**
  After `BOARD.Add()` of a new `NETINFO_ITEM` or duplicated `FOOTPRINT`, set
  `.thisown = False`. Otherwise later SWIG calls in the same process return bare
  `SwigPyObject`s. Duplicate footprints with `Cast_to_FOOTPRINT(fp.Duplicate(False))`.
