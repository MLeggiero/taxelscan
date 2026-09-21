#!/usr/bin/env python3
"""Connect specific pad/via/track pairs DRC reports as unconnected.

    ./bridge_nets.py            says what it would do
    ./bridge_nets.py --apply    does it, after writing rev3.kicad_pcb.bak

Unlike stitch_gnd.py, this is not searching for anywhere to land a via - the
DRC report already names both endpoints exactly, usually a few mm apart. This
tries a direct F.Cu track first, then two L-shaped alternatives if the direct
line is blocked, checking each against every OTHER net's copper before
accepting it. `pairs()` below is read straight off `kicad-cli pcb drc`'s own
"Missing connection between items" output for the gaps that were left after
GND and +3.3V were stitched to their planes.
"""
import io
import math
import os
import re
import shutil
import sys
import uuid as _uuid

import gen_pcb as P
import gen_schematic as S
import sync_pcb as SY
import stitch_gnd as SG

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")
TRACK_W = 0.15
CLEARANCE = 0.2

def pairs(path=BOARD, skip=("GND",)):
    """(net, x1, y1, x2, y2) for every gap DRC names, GND handled elsewhere.

    This was a hand-transcribed list of twelve. It was right on the day it was
    written and wrong on every day after, because each routing pass changes
    which gaps are left - so the router kept being handed coordinates of
    connections it had already made and of copper that no longer existed.
    Asking DRC each time costs one cached pass and cannot drift.

    GND is skipped because it does not want point-to-point bridging at all: any
    via reaches the plane, so `maze_route.py` routes it to the nearest legal
    via site instead, which is a shorter path and one that always exists.
    """
    return [(net, x1, y1, x2, y2)
            for net, x1, y1, _r1, _p1, x2, y2, _r2, _p2 in SG.drc_pairs(path)
            if net not in skip]


def path_clear(net, points, pads, vias, segs, true_ends):
    """Is every point of this polyline clear of other-net copper?

    `true_ends` are the ORIGINAL two coordinates being bridged (not an
    L-shape's bend point) - each is itself an existing pad or via, so its own
    footprint already legally coexists with whatever sits near it today. The
    first version checked full clearance starting at distance zero from that
    point, which meant "is the SOURCE PAD's own copper too close to a nearby
    unrelated track" - a question about copper that was already there and
    already DRC-clean, not about the new track. Every one of the 12 bridges
    failed on exactly that, including a 1.2 mm gap with nothing genuinely in
    the way. Samples inside PAD_STANDOFF of a true end are skipped; beyond
    that, the new copper is really "new" and gets checked properly.
    """
    r = TRACK_W / 2.0 + CLEARANCE
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        length = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(length / 0.1))
        for k in range(steps + 1):
            t = k / steps
            sx, sy = x1 + t * (x2 - x1), y1 + t * (y2 - y1)
            if any(math.hypot(sx - ex, sy - ey) < PAD_STANDOFF
                  for ex, ey in true_ends):
                continue
            for ref, pin, px, py, pw, ph, pang, pnet in pads:
                if SG.point_rect_dist(sx, sy, px, py, pw, ph, pang) < r:
                    return False
            for vx, vy, vs in vias:
                if math.hypot(sx - vx, sy - vy) < r + vs / 2.0:
                    return False
            for sx1, sy1, sx2, sy2, w in segs:
                if SG.point_seg_dist(sx, sy, sx1, sy1, sx2, sy2) < r + w / 2.0:
                    return False
    return True


PAD_STANDOFF = 0.4


def route(net, x1, y1, x2, y2, pads, vias, segs):
    """The direct line, else one of two L-shapes. None found -> None.

    Both ENDPOINTS are themselves an existing pad or via - that is the whole
    reason they appear in a DRC "missing connection" pair - so they show up
    in `pads`/`vias` too. Without excluding them, the very first sample point
    of any route is on top of its own start, which is the same self-blocking
    bug stitch_gnd.py had before its own-net fix, just for an endpoint
    instead of an anchor pad.
    """
    tol = 0.05
    near_ends = lambda px, py: (math.hypot(px - x1, py - y1) < tol
                                or math.hypot(px - x2, py - y2) < tol)
    pads = [p for p in pads if not near_ends(p[2], p[3])]
    vias = [v for v in vias if not near_ends(v[0], v[1])]
    true_ends = [(x1, y1), (x2, y2)]
    for points in ([(x1, y1), (x2, y2)],
                   [(x1, y1), (x2, y1), (x2, y2)],
                   [(x1, y1), (x1, y2), (x2, y2)]):
        if path_clear(net, points, pads, vias, segs, true_ends):
            return points
    return None


def emit(net, points):
    NL, TAB = chr(10), chr(9)
    parts = []
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        parts.append(NL.join([
            TAB + "(segment",
            TAB * 2 + "(start %g %g)" % (ax, ay),
            TAB * 2 + "(end %g %g)" % (bx, by),
            TAB * 2 + "(width %g)" % TRACK_W,
            TAB * 2 + '(layer "F.Cu")',
            TAB * 2 + '(net "%s")' % net,
            TAB * 2 + '(uuid "%s")' % _uuid.uuid4(),
            TAB + ")"]))
    return NL.join(parts)


def main():
    apply = "--apply" in sys.argv
    force = "--force" in sys.argv
    text = io.open(BOARD, encoding="utf-8").read()
    fps = SY.footprints(text)
    bom = S.load_bom()
    ex = SY.board_extent(text)
    pads = SG.all_pads(text, fps, S.load_net())

    placed, failed, new_blocks = [], [], []
    todo = pairs()
    for net, x1, y1, x2, y2 in todo:
        label = "%-12s (%.2f,%.2f) <-> (%.2f,%.2f)" % (net, x1, y1, x2, y2)
        if force:
            # These 12 gaps are all short (1-9 mm) between two points DRC
            # itself named as needing a connection. The own-geometry model
            # above is a useful filter but has already proven imperfect twice
            # this session in exactly this kind of dense area - so instead of
            # tuning it a third time, draw the direct line unconditionally and
            # let KiCad's real DRC be the judge, the same approach that
            # actually worked for stitch_gnd.py's harder cases.
            points = [(x1, y1), (x2, y2)]
        else:
            vias, segs = SG.obstacles(text, fps, bom, ex, net)
            points = route(net, x1, y1, x2, y2, pads, vias, segs)
        if points:
            shape = "direct" if len(points) == 2 else "L-shape"
            new_blocks.append(emit(net, points))
            placed.append((net, points))
            print("  bridge  %s  [%s]" % (label, shape))
        else:
            failed.append(label)
            print("  FAIL    %s  no clear path" % label)

    print()
    print("  %d bridge(s) placed, %d could not be placed automatically"
          % (len(placed), len(failed)))
    if failed:
        for f in failed:
            print("    " + f)

    if not apply:
        print(chr(10) + "dry run. re-run with --apply to write it.")
        return 1 if failed else 0

    tail = text.rindex(")")
    text = text[:tail] + chr(10).join(new_blocks) + chr(10) + text[tail:]
    shutil.copy(BOARD, BOARD + ".bak")
    io.open(BOARD, "w", encoding="utf-8", newline=chr(10)).write(text)
    print(chr(10) + "wrote rev3.kicad_pcb (previous version saved as .bak)")
    return P.check() or (1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
