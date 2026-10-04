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

Build the grid router once; the scripts expect it in `<checkout>/tmp`
(git-ignored), as `grid_route2` or `grid_route2.exe`:

    g++ -O2 -std=c++17 -o ../../../tmp/grid_route2 grid_route2.cpp          # Linux / macOS
    cl /nologo /O2 /EHsc /std:c++17 grid_route2.cpp /Fe:..\..\..\tmp\grid_route2.exe   # MSVC

## The rev-4 pipeline

| script | does |
|---|---|
| `eco_rev4.py` | `rev4.kicad_pcb` starts as a copy of `../../rev3/rev3.kicad_pcb`. Rips the copper of every net the change touches, deletes U8 / C8 / R10 / C10 / C26 and the `U8_escape` rule area, re-pins U9 to `rev4.net`, checks every pad against `rev4.net`, saves |
| `place_rev4.py in out [--rail-pin 42\|43]` | the seven moves of `../PLACEMENT.md`: each ADC-input cell (1 nF + 51 Ω) searched as a pair and routed, then the rail monitor; `--rail-pin 43` is the alternative that was measured and not taken |
| `route_rev4.py in out` | AMP_A / AMP_B, R15's +5V, USB_CC_OUT1/2 and the USB_ILIM pair through rev-3's rip-up-and-reroute loop, then prune by KiCad's own dangling report |
| `finalize_rev4.py in` | installs a routed stage as `../rev4.kicad_pcb` with the project and rules restored, then DRC with zones refilled and schematic parity; exit 0 only if 0 unconnected, 0 copper errors and parity clean |
| `via_in_pad.py board...` | the via-in-pad counts `../fab/STACKUP-NOTES.txt` quotes |

The stage boards go to `<checkout>/tmp/` and are not kept.

## Ported from rev-3

From `../../rev3/audit-tools/`, changed only where the original assumed one
Windows checkout:

| module | change |
|---|---|
| `lr.py` | the checkout root is found from this file; kicad-cli and the grid router are looked up per platform; scratch file names carry the process id, so two routers can run at once without overwriting each other's input |
| `rr2.py` | the same scratch-file fix; rev-3's `U8.` fine-pitch prefix and `VREF` protection dropped |
| `grp_route.py` | rev-3's `grp.py`, renamed: on Linux `import grp` finds the standard library's group-database module first |
| `grid_route2.cpp` | a portable `fopen_s` so g++ and clang build it |
| `insp.py`, `rend.py`, `stats.py`, `pour.py`, `reach.py`, `zones_check.py` | dump, render, statistics and plane checks; paths only |

`../../rev3/audit-tools/README.md` documents the models and the rules they
enforce: 0.15 mm everywhere, 0.10 mm when both items are in `U9_escape` or
`U13_escape`, 0.25 mm copper-to-hole and hole-to-hole, 0.22 mm to the edge,
analog nets on F.Cu, In1.Cu never routed.
