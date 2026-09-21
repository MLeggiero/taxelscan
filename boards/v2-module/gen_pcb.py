#!/usr/bin/env python3
"""Generate the module's PCB by transforming rev-1's routed board.

The same argument as gen_module.py and gen_schematic.py, and the strongest
case of the three: rev-1's board was fabricated, assembled and measured. Its
row fanout, mux breakout and sense routing are known to work at the geometry
they are drawn at. Redrawing them would throw that away and prove nothing.

    ./gen_pcb.py        writes module.kicad_pcb, then checks it with KiCad

rev-1 IS NOT MODIFIED. It is opened read-only and stays exactly as it was
built - it is still the board to make for single-sensor use, and it is the
source all three generators derive from, so it has to stay pristine.

WHAT SURVIVES. Every track on a net whose topology did not change: all 32 rows,
all 32 columns, the shift-register chain, the mux selects, ROW_CLK and
ROW_LATCH downstream of R3/R4. That is roughly 95% of rev-1's routing, carried
over at its exact coordinates.

WHAT DOES NOT. A1 (the MCU) and R5 (the 0R bridge) are removed with the
routing on the nets they terminated, because those nets now run to J3 instead
and have to be redrawn. GND and +3.3V are not deleted but split: each track
and via is assigned to PWR_GND/AGND or ROW_VCC/AVCC by which side's pad it
lies nearest. Anything too close to call is listed rather than guessed at.

THE GROUND SPLIT is a horizontal line at SPLIT_Y. Above it the row drivers and
their decoupling return on PWR_GND and sit under ROW_VCC; below it the
pulldowns, muxes and buffer return on AGND under AVCC. The two never meet on
this board - they are joined at the hub, which is the whole point. C7 and C8
move down into the analog half: they are AVCC/AGND decoupling that rev-1 could
place anywhere because its ground was unified, and C8 in particular sat at
(125, 113.8), deep inside the row-driver block.
"""
import glob
import io
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

import sexpdata
from sexpdata import Symbol

HERE = os.path.dirname(os.path.abspath(__file__))
REV1 = os.path.join(HERE, "../rev1/taxelscan.kicad_pcb")
OUT = os.path.join(HERE, "module.kicad_pcb")
NETLIST = os.path.join(HERE, "module.net")

# Which side of the split each component's return belongs on - the same set
# gen_schematic.py uses, for the same reason. See README.md.
PWR_GND_REFS = {"U1", "U2", "U3", "U4", "C1", "C2", "C3", "C4", "C9"}

GONE = ("A1", "R5")            # the MCU, and the 0R that bridged +3.3V

# The analog/power boundary. Chosen so C9 (bulk on ROW_VCC, reaching y=122.22)
# stays above it and U7's highest pin (y=123.02) stays below.
SPLIT_Y = 122.6

# Decoupling that has to move: both are AVCC/AGND, both sat in the row-driver
# block where a unified ground made it harmless. They connect only to plane
# nets, so moving them costs no routing.
# Each entry is where the part should go and how far it may be nudged to find
# free copper. A decoupling capacitor 14 mm from the chip it decouples is not
# decoupling anything, so the radius is tight and a miss is reported rather
# than silently satisfied somewhere useless.
MOVE = {"C7": (113.5, 128.0, 0, 4.0),      # decouples U7, must stay beside it
        "C8": (110.5, 134.5, 0, 6.0)}      # decouples U5

# C9 grows from an 0603 land to an 0805 one when it becomes 22 uF, and the
# bigger part would reach across SPLIT_Y at rev-1's y=121. It stays on
# PWR_GND, so it moves up far enough to clear the line.
RESIZE = {"C9": (127.0, 120.0)}

# The area A1 vacated, laid out. J3 goes on the right edge where the MCU was,
# because that is where its signals already converge.
NEW_PARTS = [
    # ref, footprint,                          x,     y,     rot
    ("R6",  "R_0603_1608Metric",              135.0, 124.0, 0),
    ("R7",  "R_0603_1608Metric",              135.0, 127.0, 0),
    ("R8",  "R_0603_1608Metric",              131.0, 116.0, 0),
    ("TP1", "TestPoint_Pad_D1.0mm",           130.0, 108.0, 0),
    ("TP2", "TestPoint_Pad_D1.0mm",           134.0, 108.0, 0),
    ("TP3", "TestPoint_Pad_D1.0mm",           138.0, 108.0, 0),
    ("TP4", "TestPoint_Pad_D1.0mm",           142.0, 108.0, 0),
    ("J3",  "Hirose_FH12-20S-0.5SH_1x20-1MP_P0.50mm_Horizontal", 145.0, 122.0, 90),
]

# Mounting holes: H1 and H2 sat at y=102.5, above the new top edge, so they
# come down. H3 is already inside and does not move.
MOVE_HOLES = {"H1": (102.8, 108.0, 0, 2.0), "H2": (147.0, 108.0, 0, 2.0)}

MARGIN = 0.75                  # copper-to-edge


# --------------------------------------------------------------------------
# s-expression helpers. sexpdata round-trips a KiCad 10 board losslessly -
# verified by re-running DRC on the round-tripped file and getting the same
# zero violations, with every one of its 74,500 numeric tokens unchanged.

def kids(node, key):
    return [x for x in node if isinstance(x, list) and x and str(x[0]) == key]


def kid(node, key):
    hit = kids(node, key)
    return hit[0] if hit else None


def val(node, key, i=1, default=None):
    hit = kid(node, key)
    return hit[i] if hit is not None and len(hit) > i else default


def net_of(node):
    hit = kid(node, "net")
    return str(hit[1]) if hit is not None and len(hit) > 1 else None


def set_net(node, name):
    hit = kid(node, "net")
    if hit is not None:
        hit[1] = name
    else:
        node.append([Symbol("net"), name])


def ref_of(fp):
    return next((str(p[2]) for p in kids(fp, "property")
                 if str(p[1]) == "Reference"), "")


def set_prop(fp, key, value):
    for p in kids(fp, "property"):
        if str(p[1]) == key:
            p[2] = value
            return
    fp.append([Symbol("property"), key, value])


def drop(tree, node):
    for i, x in enumerate(tree):
        if x is node:
            del tree[i]
            return True
    return False


def pad_xy(fp, pad):
    """Absolute position of a pad. Pad coords are footprint-local; the
    footprint's own angle rotates them."""
    fat = kid(fp, "at")
    fx, fy = fat[1], fat[2]
    rot = math.radians(fat[3] if len(fat) > 3 else 0)
    pa = kid(pad, "at")
    px, py = pa[1], pa[2]
    return (fx + px * math.cos(rot) - py * math.sin(rot),
            fy + px * math.sin(rot) + py * math.cos(rot))


def item_xy(item):
    p = kid(item, "at") or kid(item, "start")
    return (p[1], p[2])


# --------------------------------------------------------------------------

CELL = 0.25                    # occupancy grid resolution, mm


def mark(occ, x0, y0, x1, y1):
    for ix in range(int(x0 / CELL), int(x1 / CELL) + 1):
        for iy in range(int(y0 / CELL), int(y1 / CELL) + 1):
            occ.add((ix, iy))


def occupancy(board, by_ref, skip=()):
    """Every square of the board that already has copper on it.

    Placing by eye does not work here: rev-1's routing fills most of the
    board, and the first two positions I picked for C8 and H1 both landed on
    live tracks. This lets the generator say where it wants a part and then
    take the nearest place that is actually empty.
    """
    occ = set()
    for ref, fp in by_ref.items():
        if ref in skip:
            continue
        for pad in kids(fp, "pad"):
            x, y = pad_xy(fp, pad)
            sz = kid(pad, "size")
            r = max(sz[1], sz[2]) / 2 if sz else 0.5
            mark(occ, x - r, y - r, x + r, y + r)
    for s in kids(board, "segment"):
        a, b = kid(s, "start"), kid(s, "end")
        w = (val(s, "width") or 0.15) / 2
        steps = max(1, int(math.hypot(b[1] - a[1], b[2] - a[2]) / CELL))
        for i in range(steps + 1):
            t = i / steps
            x, y = a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t
            mark(occ, x - w, y - w, x + w, y + w)
    for v in kids(board, "via"):
        p = kid(v, "at")
        r = (val(v, "size") or 0.6) / 2
        mark(occ, p[1] - r, p[2] - r, p[1] + r, p[2] + r)
    return occ


def clear_spot(occ, w, h, want, region, gap=0.3):
    """Nearest position to `want` with nothing under a w x h box, or None.

    None matters: returning the wanted position on failure would make a
    collision look like a success, which is exactly the bug this had first
    time round - C7, C8 and both mounting holes came back 'placed' while
    sitting on live tracks.
    """
    px, py = want
    x0, x1, y0, y1 = region
    hw, hh = w / 2 + gap, h / 2 + gap
    best = None
    for iy in range(int(y0 / CELL), int(y1 / CELL) + 1):
        for ix in range(int(x0 / CELL), int(x1 / CELL) + 1):
            cx, cy = ix * CELL, iy * CELL
            if cx - hw < x0 or cx + hw > x1 or cy - hh < y0 or cy + hh > y1:
                continue
            d = math.hypot(cx - px, cy - py)
            if best is not None and d >= best[0]:
                continue
            if any((jx, jy) in occ
                   for jx in range(int((cx - hw) / CELL), int((cx + hw) / CELL) + 1)
                   for jy in range(int((cy - hh) / CELL), int((cy + hh) / CELL) + 1)):
                continue
            best = (d, cx, cy)
    return (best[1], best[2]) if best else None


def fp_size(fp):
    """Bounding box of a footprint's pads, in its own unrotated frame."""
    xs, ys = [], []
    for pad in kids(fp, "pad"):
        pa, sz = kid(pad, "at"), kid(pad, "size")
        r = max(sz[1], sz[2]) / 2 if sz else 0.5
        xs += [pa[1] - r, pa[1] + r]
        ys += [pa[2] - r, pa[2] + r]
    return (max(xs) - min(xs), max(ys) - min(ys)) if xs else (1.0, 1.0)


def load_netlist(path=NETLIST):
    """(ref, pin) -> net name, from the netlist both other generators check."""
    d = sexpdata.loads(io.open(path, encoding="utf-8").read())
    out = {}
    for n in kids(kid(d, "nets"), "net"):
        name = str(val(n, "name"))
        for node in kids(n, "node"):
            out[(str(val(node, "ref")), str(val(node, "pin")))] = name
    return out


def load_bom(path=os.path.join(HERE, "BOM.csv")):
    """ref -> (value, footprint), expanding 'U1-U4' and 'J1,J2'."""
    import csv
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for tok in (t.strip() for t in row["Reference"].split(",")):
                m = re.fullmatch(r"([A-Za-z#]+)(\d+)-(?:[A-Za-z#]+)?(\d+)", tok)
                refs = ([m.group(1) + str(i)
                         for i in range(int(m.group(2)), int(m.group(3)) + 1)]
                        if m else ([tok] if tok else []))
                for ref in refs:
                    out[ref] = (row["Value"], row["Footprint"])
    return out


def find_footprint_dir():
    for pat in (r"C:\Program Files\KiCad\*\share\kicad\footprints",
                "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints",
                "/usr/share/kicad/footprints"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def reuuid(node, seed):
    """Give every uuid in a subtree a fresh deterministic value.

    Deterministic because the generator has to produce the same file twice -
    a diff that churns on every run is a diff nobody reads. Python's hash() is
    salted per process, so crc32 rather than hash().
    """
    import zlib
    n = [0]

    def walk(x):
        if not isinstance(x, list) or not x:
            return
        if str(x[0]) == "uuid" and len(x) > 1:
            n[0] += 1
            h = zlib.crc32(("%s-%d" % (seed, n[0])).encode())
            x[1] = "%08x-0000-4000-8000-%012x" % (h, h)
        for c in x:
            walk(c)
    walk(node)


def load_stock(name, libname):
    """One stock KiCad footprint, turned into a board footprint.

    A .kicad_mod carries version/generator headers that a footprint inside a
    board does not, and its name is bare rather than Library:Name.
    """
    root = find_footprint_dir()
    if root is None:
        raise SystemExit("  KiCad's footprint libraries were not found")
    hits = glob.glob(os.path.join(root, "*.pretty", name + ".kicad_mod"))
    if not hits:
        raise SystemExit("  footprint not in any stock library: " + name)
    fp = sexpdata.loads(io.open(hits[0], encoding="utf-8").read())
    for k in ("version", "generator", "generator_version"):
        for stale in list(kids(fp, k)):
            drop(fp, stale)
    fp[1] = libname + ":" + name
    if kid(fp, "uuid") is None:
        fp.insert(2, [Symbol("uuid"), "0"])
    return fp


def place(fp, ref, x, y, rot):
    """Position a footprint tree and fold its rotation into every pad.

    The (at) has to come after the name - element 0 is the footprint keyword
    and element 1 is its library id, and KiCad's parser wants a string there.
    """
    at = kid(fp, "at")
    if at is None:
        fp.insert(2, [Symbol("at"), x, y, rot])
    else:
        at[1:] = [x, y, rot]
    if rot:
        for pad in kids(fp, "pad"):
            pa = kid(pad, "at")
            if len(pa) > 3:
                pa[3] = (pa[3] + rot) % 360
            else:
                pa.append(rot % 360)
    set_prop(fp, "Reference", ref)
    reuuid(fp, ref)
    return fp


def main():
    pin_nets = load_netlist()
    board = sexpdata.loads(io.open(REV1, encoding="utf-8").read())
    fps = kids(board, "footprint")
    by_ref = {ref_of(f): f for f in fps}

    # ---- 1. what the removed parts took with them -------------------------
    doomed_nets = set()
    for ref in GONE:
        fp = by_ref.get(ref)
        if fp is None:
            raise SystemExit("  %s is not on rev-1's board" % ref)
        for pad in kids(fp, "pad"):
            if net_of(pad):
                doomed_nets.add(net_of(pad))
        drop(board, fp)
        del by_ref[ref]

    # GND and +3.3V are on that list but are not deleted - they are split.
    resplit = {n for n in doomed_nets if n in ("GND", "+3.3V")}
    doomed_nets -= resplit
    doomed_nets |= {n for n in
                    {net_of(s) for s in kids(board, "segment")} |
                    {net_of(v) for v in kids(board, "via")}
                    if n and n.startswith("unconnected-")}

    # ---- 2. where each half of the split lives ----------------------------
    # Every GND/+3.3V pad, tagged with the side it is now on, so a track can
    # be assigned by which side's pad it lies nearest.
    anchors = []
    for ref, fp in by_ref.items():
        upper = ref in PWR_GND_REFS
        for pad in kids(fp, "pad"):
            old = net_of(pad)
            if old not in ("GND", "+3.3V"):
                continue
            x, y = pad_xy(fp, pad)
            if ref in MOVE:                # C7/C8 are about to move down
                upper = False
                x, y = MOVE[ref][0], MOVE[ref][1]
            anchors.append((x, y, old, upper))

    def side_of(x, y, old):
        near = [a for a in anchors if a[2] == old]
        best = min(near, key=lambda a: math.hypot(a[0] - x, a[1] - y))
        rival = [a for a in near if a[3] != best[3]]
        gap = (min(math.hypot(a[0] - x, a[1] - y) for a in rival)
               - math.hypot(best[0] - x, best[1] - y)) if rival else 99.0
        return best[3], gap

    rename = {("GND", True): "PWR_GND", ("GND", False): "AGND",
              ("+3.3V", True): "ROW_VCC", ("+3.3V", False): "AVCC"}

    # ---- 3. the routing ---------------------------------------------------
    killed, split, unsure = 0, 0, []
    for item in list(kids(board, "segment")) + list(kids(board, "via")):
        n = net_of(item)
        if n in doomed_nets:
            drop(board, item)
            killed += 1
        elif n in resplit:
            x, y = item_xy(item)
            upper, gap = side_of(x, y, n)
            set_net(item, rename[(n, upper)])
            split += 1
            if gap < 1.5:
                unsure.append((rename[(n, upper)], round(x, 2), round(y, 2),
                               round(gap, 2)))

    # ---- 4. move and place, onto copper that is actually free -------------
    # Every position below is a preference, not a coordinate. The generator
    # takes the nearest genuinely empty spot to it, because rev-1's routing
    # fills most of this board and eyeballing a gap does not work.
    moving = set(MOVE) | set(MOVE_HOLES) | set(RESIZE)
    occ = occupancy(board, by_ref, skip=moving)
    placed, stuck = [], []

    def put(fp, ref, want, region, rot=0, radius=6.0):
        w, h = fp_size(fp)
        if rot % 180:
            w, h = h, w
        box = (max(region[0], want[0] - radius), min(region[1], want[0] + radius),
               max(region[2], want[1] - radius), min(region[3], want[1] + radius))
        spot = clear_spot(occ, w, h, want, box)
        if spot is None:                   # nothing free inside the radius
            x, y = want
            stuck.append("%s wants (%.1f, %.1f) - no clear %.1f x %.1f mm within "
                         "%.0f mm; placed anyway, reroute locally in pcbnew"
                         % (ref, x, y, w, h, radius))
            moved = 0.0
        else:
            x, y = spot
            moved = math.hypot(x - want[0], y - want[1])
        if spot is not None and moved > 0.3:
            placed.append("%s (%.1f, %.1f) -> (%.1f, %.1f)"
                          % (ref, want[0], want[1], x, y))
        place(fp, ref, x, y, rot)
        mark(occ, x - w / 2 - 0.3, y - h / 2 - 0.3, x + w / 2 + 0.3, y + h / 2 + 0.3)
        return fp

    ANALOG = (101.5, 148.5, SPLIT_Y + 1.0, 138.0)
    FREED = (128.5, 149.0, 105.5, 138.0)
    TOP = (101.5, 148.5, 105.5, 121.0)

    for ref, (x, y, rot, rad) in MOVE.items():
        put(by_ref[ref], ref, (x, y), ANALOG, rot, rad)
    for ref, (x, y, rot, rad) in MOVE_HOLES.items():
        put(by_ref[ref], ref, (x, y), TOP, rot, rad)

    # ---- 5. the new parts -------------------------------------------------
    # R6/R7/R8 are cloned from rev-1's own R1 rather than loaded from stock,
    # so the land pattern on this board is the one that was actually built.
    r_template = by_ref["R1"]
    for ref, fpname, x, y, rot in NEW_PARTS:
        if fpname.startswith("R_0603"):
            import copy
            fp = copy.deepcopy(r_template)
            for p in kids(fp, "pad"):
                hit = kid(p, "net")
                if hit is not None:
                    p.remove(hit)
        else:
            lib = "TestPoint" if fpname.startswith("TestPoint") else "Connector_FFC-FPC"
            fp = load_stock(fpname, lib)
        put(fp, ref, (x, y), FREED, rot, 12.0)
        board.append(fp)
        by_ref[ref] = fp

    # Values and land patterns come from BOM.csv, the same authority the
    # schematic takes them from, so the board cannot drift from it the way the
    # schematic did. C9 is why this matters on copper as well as on paper: it
    # grew from 10 uF to 22 uF, which is an 0805 part, and rev-1's board has it
    # on an 0603 land. Inheriting that would have put the specified capacitor
    # on pads it does not fit.
    bom = load_bom()
    swapped = []
    for ref in sorted(by_ref):
        fp = by_ref[ref]
        if ref not in bom:
            continue
        set_prop(fp, "Value", bom[ref][0])
        lib, want = bom[ref][1].split(":")
        if want == str(fp[1]).split(":")[-1]:
            # rev-1 stores footprint names bare. The schematic gives them
            # Library:Name, and KiCad's board/schematic parity check compares
            # the two strings, so every part on the board disagreed with its
            # own symbol until the prefix went back on.
            fp[1] = bom[ref][1]
            continue
        old_at = kid(fp, "at")
        x, y, rot = old_at[1], old_at[2], (old_at[3] if len(old_at) > 3 else 0)
        x, y = RESIZE.get(ref, (x, y))
        nets = {str(p[1]): net_of(p) for p in kids(fp, "pad")}
        new = load_stock(want, lib)
        drop(board, fp)
        put(new, ref, (x, y), TOP, rot)
        set_prop(new, "Value", bom[ref][0])
        for pad in kids(new, "pad"):
            if nets.get(str(pad[1])):
                set_net(pad, nets[str(pad[1])])
        board.append(new)
        by_ref[ref] = new
        swapped.append("%s: %s -> %s" % (ref, str(fp[1]).split(":")[-1], want))
    wrong_fp = []

    # ---- 6. every pad takes its net from module.net -----------------------
    # The netlist is the authority, exactly as it is for the schematic. MP
    # pads are the FFC shells: the symbols have no such pin so the netlist
    # says nothing about them, and they go to AGND by decision, not default.
    assigned, shells, orphans = 0, 0, []
    for ref, fp in by_ref.items():
        for pad in kids(fp, "pad"):
            num = str(pad[1])
            want = pin_nets.get((ref, num))
            if want:
                set_net(pad, want)
                assigned += 1
            elif num == "MP" and ref in ("J1", "J2", "J3"):
                set_net(pad, "AGND")
                shells += 1
            else:
                hit = kid(pad, "net")
                if hit is not None:
                    pad.remove(hit)
                if ref.startswith(("H", "TP")) is False and num != "MP":
                    orphans.append("%s.%s" % (ref, num))

    # ---- 6b. copper left hanging by the deletions -------------------------
    orphan_v, orphan_t = prune_stubs(board, by_ref)

    # ---- 7. outline and zones ---------------------------------------------
    x0, x1, y0, y1 = new_outline(board, by_ref)
    rebuild_zones(board, x0, x1, y0, y1)

    io.open(OUT, "w", encoding="utf-8", newline="\n").write(sexpdata.dumps(board))
    print("wrote " + os.path.relpath(OUT))
    print("  removed %s and %d tracks/vias on the nets they terminated"
          % (" and ".join(GONE), killed))
    print("  split %d GND/+3.3V tracks and vias across the two halves" % split)
    print("  kept %d segments and %d vias of rev-1's routing untouched"
          % (len(kids(board, "segment")), len(kids(board, "via"))))
    print("  %d pads took their net from module.net, %d shells to AGND"
          % (assigned, shells))
    print("  removed %d stranded vias and %d stranded track segments"
          % (orphan_v, orphan_t))
    print("  board is %.1f x %.1f mm (rev-1 was 52.0 x 46.0)" % (x1 - x0, y1 - y0))
    if unsure:
        print("  %d tracks too close to call - CHECK THESE BY HAND:" % len(unsure))
        for n, x, y, g in unsure:
            print("    %s @(%.2f, %.2f), rival %0.2f mm further" % (n, x, y, g))
    if orphans:
        print("  pads with no net in module.net: " + ", ".join(orphans))
    if swapped:
        print("  land pattern taken from BOM.csv: " + "; ".join(swapped))
    if placed:
        print("  nudged onto free copper: " + "; ".join(placed))
    if stuck:
        print("  %d part(s) NEED HAND PLACEMENT:" % len(stuck))
        for m in stuck:
            print("    " + m)
    return check()


def prune_stubs(board, by_ref):
    """Delete vias and segments that the removed parts left connected to air.

    Taking A1 out stranded the vias its tracks used to reach. A via touching
    nothing but a zone is legitimate - that is how a plane is stitched - so
    only vias with no pad and no track at them go, and removing a via can
    strand the segment that fed it, hence the fixpoint.
    """
    def q(p):
        return (round(p[1], 3), round(p[2], 3))

    padpts = set()
    for fp in by_ref.values():
        for pad in kids(fp, "pad"):
            x, y = pad_xy(fp, pad)
            padpts.add((round(x, 3), round(y, 3)))

    dead = set()
    segs = kids(board, "segment")
    vias = kids(board, "via")
    while True:
        live_s = [x for x in segs if id(x) not in dead]
        ends = {}
        for sg in live_s:
            for k in ("start", "end"):
                ends.setdefault(q(kid(sg, k)), []).append(sg)
        gone = {id(v) for v in vias if id(v) not in dead
                and q(kid(v, "at")) not in padpts
                and q(kid(v, "at")) not in ends}
        viapts = {q(kid(v, "at")) for v in vias if id(v) not in dead | gone}
        for sg in live_s:
            free = 0
            for k in ("start", "end"):
                p = q(kid(sg, k))
                if p in padpts or p in viapts:
                    continue
                if len(ends.get(p, [])) > 1:
                    continue
                free += 1
            if free == 2:              # both ends attached to nothing at all
                gone.add(id(sg))
        if not gone:
            break
        dead |= gone
    nv = sum(1 for v in vias if id(v) in dead)
    nt = sum(1 for x in segs if id(x) in dead)
    for i in range(len(board) - 1, -1, -1):
        if id(board[i]) in dead:
            del board[i]
    return nv, nt


def new_outline(board, by_ref):
    """Shrink the edge onto what is actually left, plus a margin."""
    xs, ys = [], []
    for fp in by_ref.values():
        for pad in kids(fp, "pad"):
            x, y = pad_xy(fp, pad)
            sz = kid(pad, "size")
            r = max(sz[1], sz[2]) / 2 if sz else 1.0
            xs += [x - r, x + r]
            ys += [y - r, y + r]
    for item in kids(board, "segment") + kids(board, "via"):
        for k in ("start", "end", "at"):
            p = kid(item, k)
            if p:
                xs.append(p[1])
                ys.append(p[2])
    x0, x1 = min(xs) - MARGIN, max(xs) + MARGIN
    y0, y1 = min(ys) - MARGIN, max(ys) + MARGIN

    for g in list(kids(board, "gr_line")) + list(kids(board, "gr_arc")):
        if str(val(g, "layer")) == "Edge.Cuts":
            drop(board, g)
    for (ax, ay), (bx, by) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                               ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        board.append([Symbol("gr_line"),
                      [Symbol("start"), round(ax, 3), round(ay, 3)],
                      [Symbol("end"), round(bx, 3), round(by, 3)],
                      [Symbol("stroke"), [Symbol("width"), 0.1],
                       [Symbol("type"), Symbol("default")]],
                      [Symbol("layer"), "Edge.Cuts"],
                      [Symbol("uuid"), "edge-%.0f-%.0f-%.0f-%.0f"
                       % (ax * 10, ay * 10, bx * 10, by * 10)]])
    return x0, x1, y0, y1


def rebuild_zones(board, x0, x1, y0, y1):
    """One zone per net per layer, split at SPLIT_Y.

    rev-1 had four: GND on B.Cu and In1.Cu, +3.3V on In2.Cu, ROW_VCC on B.Cu.
    Each ground zone becomes two, and so does the +3.3V one. Stale fills go -
    KiCad refills on open, and carrying rev-1's fill outlines would be a lie
    about copper that no longer exists.
    """
    import copy
    made = []
    for z in list(kids(board, "zone")):
        old = net_of(z)
        if old not in ("GND", "+3.3V"):
            continue
        drop(board, z)
        for upper, name in ((True, "PWR_GND" if old == "GND" else "ROW_VCC"),
                            (False, "AGND" if old == "GND" else "AVCC")):
            nz = copy.deepcopy(z)
            set_net(nz, name)
            for k in ("net_name", "filled_polygon"):
                for stale in list(kids(nz, k)):
                    drop(nz, stale)
            nz.append([Symbol("net_name"), name])
            ya, yb = (y0, SPLIT_Y) if upper else (SPLIT_Y, y1)
            for poly in list(kids(nz, "polygon")):
                drop(nz, poly)
            nz.append([Symbol("polygon"),
                       [Symbol("pts")] + [[Symbol("xy"), round(px, 3), round(py, 3)]
                                          for px, py in ((x0, ya), (x1, ya),
                                                         (x1, yb), (x0, yb))]])
            for u in kids(nz, "uuid"):
                u[1] = "zone-%s-%s" % (name.lower(), str(val(nz, "layer")).replace(".", ""))
            board.append(nz)
            made.append("%s on %s" % (name, val(nz, "layer")))
    print("  zones: " + ", ".join(made))


def find_cli():
    found = shutil.which("kicad-cli")
    if found:
        return found
    for pat in (r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
                "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
                "/usr/lib/kicad/bin/kicad-cli"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def check():
    """Every pad's net against module.net, then KiCad's own DRC.

    The pad check is the PCB's version of the netlist comparison the schematic
    gets: it is what makes the board and the verified design the same circuit
    rather than two things that look alike. DRC is the board's ERC.
    """
    pin_nets = load_netlist()
    board = sexpdata.loads(io.open(OUT, encoding="utf-8").read())
    wrong, seen = [], set()
    for fp in kids(board, "footprint"):
        ref = ref_of(fp)
        for pad in kids(fp, "pad"):
            key = (ref, str(pad[1]))
            want = pin_nets.get(key)
            if want is None:
                continue
            seen.add(key)
            if net_of(pad) != want:
                wrong.append("%s.%s is %r, module.net says %r"
                             % (ref, key[1], net_of(pad), want))
    missing = sorted(k for k in pin_nets if k not in seen)
    rc = 0
    print("")
    if wrong or missing:
        rc = 1
        print("  %d PAD(S) DISAGREE with module.net:" % (len(wrong) + len(missing)))
        for w in wrong[:20]:
            print("    " + w)
        for m in missing[:20]:
            print("    %s.%s has no pad on the board" % m)
    else:
        print("  all %d pads in module.net are on the board with the right net"
              % len(pin_nets))

    cli = find_cli()
    if not cli:
        print("  kicad-cli not found: SKIPPING DRC")
        return rc
    tmp = tempfile.mkdtemp(prefix="taxelscan-")
    try:
        rpt = os.path.join(tmp, "drc.rpt")
        # --refill-zones matters: an unfilled board reports every plane
        # connection as unconnected, so the count would be meaningless.
        # --save-board keeps the fill in the file that ships.
        # --schematic-parity is KiCad checking the board against
        # module.kicad_sch itself, which is a stronger statement than this
        # script comparing both to module.net.
        subprocess.run([cli, "pcb", "drc", "--output", rpt, "--refill-zones",
                        "--save-board", "--schematic-parity", "--severity-all",
                        OUT], capture_output=True)
        text = io.open(rpt, encoding="utf-8").read() if os.path.exists(rpt) else ""
        v = re.search(r"Found (\d+) DRC violations", text)
        u = re.search(r"Found (\d+) unconnected", text)
        nv = int(v.group(1)) if v else -1
        nu = int(u.group(1)) if u else -1
        print("  DRC: %d violations, %d unconnected items" % (nv, nu))
        kinds = {}
        for k in re.findall(r"^\[(\w+)\]", text, re.M):
            kinds[k] = kinds.get(k, 0) + 1
        for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print("    %-28s %d" % (k, n))
        if nv:
            rc = 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
