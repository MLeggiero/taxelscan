# rev-4 board tools

The scripts that turned the routed rev-3 board into rev-4, and the parts of
rev-3's router they run on. Everything here uses KiCad's Python module
(`pcbnew`) plus numpy, scipy and shapely:

- **Linux:** KiCad 10's packages install `pcbnew` for the system Python
  (`/usr/bin/python3.12` on Ubuntu 24.04), and `apt install python3-numpy
  python3-scipy python3-shapely python3-matplotlib` covers the rest.
- **Windows:** KiCad's own interpreter, `"C:/Program Files/KiCad/10.0/bin/python.exe"`,
  with the packages in `<checkout>/tmp/usb-power-deps`, as for rev-3.

A fresh KiCad install has no global symbol / footprint library tables until the
GUI's first-run dialog writes them; copy KiCad's templates
(`/usr/share/kicad/template/{fp,sym}-lib-table` to `~/.config/kicad/10.0/` on
Linux) or every DRC run reports each footprint as being from a library it
cannot find.

Build the grid routers once; the scripts expect them in `<checkout>/tmp`
(git-ignored), as `grid_route2` / `grid_route3` (`.exe` on Windows):

    g++ -O2 -std=c++17 -o ../../../tmp/grid_route2 grid_route2.cpp          # Linux / macOS
    g++ -O2 -std=c++17 -o ../../../tmp/grid_route3 grid_route3.cpp
    cl /nologo /O2 /EHsc /std:c++17 grid_route2.cpp /Fe:..\..\..\tmp\grid_route2.exe   # MSVC
    cl /nologo /O2 /EHsc /std:c++17 grid_route3.cpp /Fe:..\..\..\tmp\grid_route3.exe

The full re-route also needs **freerouting 2.4.1** (its executable jar, from
the project's GitHub releases) and a **Java 25** runtime, which that release
requires (`apt install openjdk-25-jre-headless`; older JREs refuse the jar
with a class-version error).

## The rev-4 pipeline

| script | does |
|---|---|
| `eco_rev4.py` | `rev4.kicad_pcb` starts as a copy of `../../rev3/rev3.kicad_pcb`. Rips the copper of every net the change touches, deletes U8 / C8 / R10 / C10 / C26 and the `U8_escape` rule area, re-pins U9 to `rev4.net`, checks every pad against `rev4.net`, saves |
| `place_rev4.py in out [--rail-pin 42\|43]` | the seven moves of `../PLACEMENT.md`: each ADC-input cell (1 nF + 51 Ω) searched as a pair and routed, then the rail monitor; `--rail-pin 43` is the alternative that was measured and not taken |
| `route_rev4.py in out` | AMP_A / AMP_B, R15's +5V, USB_CC_OUT1/2 and the USB_ILIM pair through rev-3's rip-up-and-reroute loop, then prune by KiCad's own dangling report |
| `finalize_rev4.py in` | installs a routed stage as `../rev4.kicad_pcb` with the project and rules restored, then DRC with zones refilled and schematic parity; exit 0 only if 0 unconnected, 0 copper errors and parity clean |
| `via_in_pad.py board...` | the via-in-pad counts `../fab/STACKUP-NOTES.txt` quotes |

## The full re-route (straight routing)

The board as first made kept rev-3's routing wherever the change did not reach,
and two thirds of that copper ran at angles other than 0 / 45 / 90 degrees.
These re-route every signal from scratch: the nets that need the board's
area rules or pairing are routed first by the exact-geometry router, freerouting
routes the rest around them, and the exact-geometry router finishes and checks
what freerouting leaves.

| script | does |
|---|---|
| `freeroute_rev4.py prep\|route\|finish\|all` | the whole sequence below, end to end, in a work directory |
| `preroute_rev4.py stripped out` | on the stripped board, by `rr3` and in this order: VREG_LX (F.Cu, 0.2 mm), USB_D_P/N, the ADC corner (`fr_finish.CORNER`) and RAIL_MON's divider end, VCORE (0.2 mm, kept out from under the QFN body), the GND / +3.3 V pad-to-via ties redrawn octilinear around them, the RS-485 pairs (`pairs_rev4.py`) |
| `pairs_rev4.py in out` | BUS_P/N and SYNC_P/N drawn: four lanes in J3's order across the board, the swaps and the pair crossing at the J4 end, B.Cu for SYNC past the USB-C receptacle; R27.2's ground tie moves into its pad to make room; verified exactly |
| `dsn_prep.py in.dsn out.dsn [--plane-keepouts] [--fid-keepouts] [--fixed NETS]` | rewrites pcbnew's Specctra export for freerouting: the clearance rule areas are not keepouts, In2 is a power layer, the board's net classes (0.3 / 0.4 mm power, 0.1 mm fine-pitch, analog on F.Cu only), the GND / +3.3 V plane ties and the pre-routed nets as keepouts, the fiducials' 0.6 mm clearance |
| `ses_import.py base session out [--fixed=NETS]` | the session onto the base board; every plane tie and pre-routed track missing afterwards is copied back from the base exactly and checked; DRC and the straightness numbers |
| `fr_finish.py in out [--fixed=NETS]` | takes every signal track or via in a copper DRC violation off, joins everything open with `rr3.rrr3` (never ripping the planes' ties, the pre-routed nets or the regulator / crystal nets), prunes, repeats until the DRC is clean; redraws the crystal (XOUT_MCU, XIN) on F.Cu if either has a via, and the op-amp outputs with B.Cu costed; then widens freerouting's 0.10 mm tracks to 0.15 mm wherever they fit, undoing any widening the DRC objects to |
| `straighten_rev4.py in out [--planes-only]` | net-by-net octilinear redraw of an existing routing (kept only where it is no longer and adds at most a via); `--planes-only` redraws the GND / +3.3 V pad-to-via stubs. Tried first on the whole board; one net at a time it was too slow, and the result kept rev-3's topology |
| `rr3.py` | the octilinear router on `lr.Model`: `route3` (one connection), `connect` (a net, nearest-first), `rrr3` (rip-up and reroute) |
| `grid_route3.cpp` | its search: A* over (layer, cell, direction) with a cost per 45- and 90-degree bend and no acute turns, so every segment is horizontal, vertical or diagonal |
| `jag.py board [net...]` | how straight a routing is: copper off the 45-degree grid, segments under 0.1 mm, corners and corners sharper than 90 degrees |

    python3 freeroute_rev4.py all ../rev4.kicad_pcb ../../../tmp/fr \
        --jar freerouting-2.4.1.jar --java /usr/lib/jvm/java-25-openjdk-amd64/bin/java
    python3 finalize_rev4.py ../../../tmp/fr/final.kicad_pcb

The stage boards go to `<checkout>/tmp/` and are not kept.

## Ported from rev-3

From `../../rev3/audit-tools/`, changed only where the original assumed one
Windows checkout:

| module | change |
|---|---|
| `lr.py` | the checkout root is found from this file; kicad-cli and the grid router are looked up per platform; scratch file names carry the process id, so two routers can run at once without overwriting each other's input; the board edge is the outline's centre line (KiCad measures the 0.22 mm copper-to-edge rule from there), not its bounding box, which took in half the 0.1 mm line width; a pad's own local clearance (the fiducials' 0.6 mm) is honoured in the checks and the search masks |
| `rr2.py` | the same scratch-file fix; rev-3's `U8.` fine-pitch prefix and `VREF` protection dropped |
| `grp_route.py` | rev-3's `grp.py`, renamed: on Linux `import grp` finds the standard library's group-database module first |
| `grid_route2.cpp` | a portable `fopen_s` so g++ and clang build it |
| `insp.py`, `rend.py`, `stats.py`, `pour.py`, `reach.py`, `zones_check.py` | dump, render, statistics and plane checks; paths only |

`../../rev3/audit-tools/README.md` documents the models and the rules they
enforce: 0.15 mm everywhere, 0.10 mm when both items are in `U9_escape` or
`U13_escape`, 0.25 mm copper-to-hole and hole-to-hole, 0.22 mm to the edge,
analog nets on F.Cu, In1.Cu never routed.
