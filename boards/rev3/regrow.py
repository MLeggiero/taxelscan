#!/usr/bin/env python3
"""Widen the board outline, and take the edge-mounted connector with it.

    ./regrow.py            says what it would do
    ./regrow.py --apply    does it, after writing rev3.kicad_pcb.bak

WHY. 49 x 36 mm is full: 78.8% courtyard density, and the audit in README.md
found 22 of 23 IC supply pins with no decoupling capacitor within 2 mm and
nowhere to put one. The fix is area, and the question is which edge to buy it
on - because every edge of this board has a connector mounted ON it, so growth
always costs a connector move.

    top     J1, J2 - the two 32-way FFC connectors. Moving them means
            re-routing the entire row and column fanout. Out of the question.
    bottom  J3, J4 and J5 - both bus connectors and the USB-C receptacle.
            Three parts, ~30 pads.
    left    J4 alone, 7 pads.
    right   J3 alone, 7 pads.

So it grows sideways, and only J3 moves. 49 -> 52 mm is 1872 mm2, still 22%
under rev-1's 2392, and it drops density to 74.3%. A long board rather than a
deep one was called for at the start of this design, which is lucky, because
it is also the cheap direction.

The zones follow the outline, and every net J3 touches is ripped so the router
can put it back - a connector moved 3 mm with its old copper still attached is
seven shorts.
"""
import io
import math
import os
import re
import shutil
import sys

import gen_pcb as P
import gen_schematic as S
import maze_route as M
import sync_pcb as SY

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")

GROW = 3.0                  # mm added to the right-hand edge
EDGE_PART = "J3"            # the connector that lives on that edge


def main():
    apply = "--apply" in sys.argv
    text = io.open(BOARD, encoding="utf-8").read()
    ex = SY.board_extent(text)
    old_x = ex[2]
    new_x = old_x + GROW
    print("  outline  %.0f x %.0f -> %.0f x %.0f mm  (%.0f -> %.0f mm2)"
          % (ex[2] - ex[0], ex[3] - ex[1], new_x - ex[0], ex[3] - ex[1],
             (ex[2] - ex[0]) * (ex[3] - ex[1]),
             (new_x - ex[0]) * (ex[3] - ex[1])))

    # 1. The Edge.Cuts lines. Only the x coordinates that sit ON the old right
    #    edge move; everything else is left alone.
    out, moved = [], 0
    i = 0
    for m in re.finditer(r"\n\t\(gr_(?:line|rect|poly)", text):
        st = m.start() + 1
        if st < i:
            continue
        end = P.close_of(text, st)
        blk = text[st:end]
        if '(layer "Edge.Cuts")' in blk:
            def bump(mo):
                x = float(mo.group(2))
                if abs(x - old_x) < 0.01:
                    return "%s%g %s" % (mo.group(1), new_x, mo.group(3))
                return mo.group(0)
            blk, n = re.subn(r"((?:start|end|mid|xy) )([-\d.]+) ([-\d.]+)",
                             bump, blk)
            moved += sum(1 for _ in re.finditer(r"%g " % new_x, blk))
        out.append(text[i:st])
        out.append(blk)
        i = end
    out.append(text[i:])
    text = "".join(out)
    print("  edge     right-hand segments moved to x = %g" % new_x)

    # 2. The zones follow, or the planes stop 3 mm short of the new edge.
    out, i = [], 0
    zn = 0
    for m in re.finditer(r"\n\t\(zone\b", text):
        st = m.start() + 1
        if st < i:
            continue
        end = P.close_of(text, st)
        blk = text[st:end]
        poly = re.search(r"\(polygon\s*\(pts(.*?)\)\s*\)", blk, re.S)
        if poly:
            def bumpz(mo):
                x = float(mo.group(1))
                if abs(x - (old_x - P.EDGE)) < 0.01:
                    return "(xy %g %s)" % (new_x - P.EDGE, mo.group(2))
                return mo.group(0)
            newp = re.sub(r"\(xy ([-\d.]+) ([-\d.]+)\)", bumpz, poly.group(0))
            blk = blk[:poly.start()] + newp + blk[poly.end():]
            zn += 1
        out.append(text[i:st])
        out.append(blk)
        i = end
    out.append(text[i:])
    text = "".join(out)
    print("  zones    %d outline(s) widened to x = %g" % (zn, new_x - P.EDGE))

    # 3. The connector on that edge moves with it.
    fps = SY.footprints(text)
    j3 = next((f for f in fps if f[2] == EDGE_PART), None)
    if not j3:
        print("  %s not found - nothing moved" % EDGE_PART)
        return 1
    st, end, ref, lib, x, y, rot = j3
    blk = text[st:end]
    at = re.search(r"(\n\t{1,3}\(at )([-\d.]+)( [-\d.]+(?: [-\d.]+)?\))", blk)
    blk = blk[:at.start()] + at.group(1) + ("%g" % (x + GROW)) + at.group(3) \
        + blk[at.end():]
    text = text[:st] + blk + text[end:]
    print("  part     %s %.2f -> %.2f mm, so it stays on the edge"
          % (EDGE_PART, x - P.OX, x + GROW - P.OX))

    # 4. Rip what the move invalidates, and only that.
    #
    # "Every net the connector touches" is too blunt: J3 is on GND, and GND is
    # 207 segments and 71 vias including every plane stitch on the board.
    # Signal nets on a 6-pin connector are small and get pulled whole; the
    # pour nets keep everything except the copper the connector's NEW body now
    # sits on, which is a short rather than a connection.
    netmap = S.load_net()
    pours = {n for n, _v in M.PLANE_NETS}
    nets = {n for n in netmap.get(EDGE_PART, {}).values() if n not in pours}
    board = M.Board(text)
    j3new = next(f for f in board.fps if f[2] == EDGE_PART)
    body = P.courtyard(P.find_footprint(j3new[3]), j3new[4], j3new[5],
                       j3new[6])

    def inside(x, y):
        return body and body[0] <= x <= body[2] and body[1] <= y <= body[3]

    kill_t = [k for k, t in enumerate(board.tracks)
              if t[7] is not None and (t[0] in nets or
                                       (t[0] in pours and
                                        (inside(t[2], t[3]) or
                                         inside(t[4], t[5]))))]
    kill_v = [k for k, v in enumerate(board.vias)
              if v[4] is not None and (v[0] in nets or
                                       (v[0] in pours and inside(v[1], v[2])))]
    print("  rip      %d segment(s) and %d via(s): %s whole, plus pour copper "
          "under the connector's new body"
          % (len(kill_t), len(kill_v), ", ".join(sorted(nets))))
    spans = [board.tracks[k][7] for k in kill_t] + [board.vias[k][4]
                                                    for k in kill_v]
    for a, b in sorted(spans, reverse=True):
        text = text[:a - 1] + text[b:]

    if not apply:
        print(chr(10) + "dry run. re-run with --apply to write it.")
        return 0
    shutil.copy(BOARD, BOARD + ".bak")
    io.open(BOARD, "w", encoding="utf-8", newline=chr(10)).write(text)
    print(chr(10) + "wrote rev3.kicad_pcb (previous version saved as .bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
