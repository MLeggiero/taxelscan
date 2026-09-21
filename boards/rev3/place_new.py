#!/usr/bin/env python3
"""Find where a new part can actually go on a board that is already routed.

    ./place_new.py            prints a SEED table for sync_pcb.py

WHY. `sync_pcb.py`'s packer tests a candidate position against other parts'
COURTYARDS and nothing else. That is the right test for an unrouted board and
badly wrong for this one: at 78.8% courtyard density and 1589 tracks, a
courtyard-free 1 x 2 mm gap is usually full of copper. Placing the eight new
parts on courtyard evidence alone produced 23 `shorting_items` and 29
`solder_mask_bridge` - every one of them a fresh pad dropped on top of somebody
else's track.

So this asks the question the board actually poses: is there room for THIS
part's pads here, given every piece of copper already down? It reuses
`maze_route.Board`'s exact distance field, so "room" means the same 0.15 mm the
router means, measured against real pad and track geometry rather than a
bitmap.

Same-net copper is not an obstacle. A +3.3V capacitor pad landing on a +3.3V
track is not a fault, it is a connection - and refusing those would rule out
most of the good positions, since the best place for a decoupling cap is
exactly where its rail already runs.
"""
import io
import math
import os
import re
import sys

import numpy as np

import gen_pcb as P
import gen_schematic as S
import maze_route as M
import sync_pcb as SY

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")

# ref -> (target x, target y, what it is next to). Board coordinates, origin
# subtracted - the same frame README.md's adjacency table uses.
TARGETS = {
    "C37": (None, None, "J5 VBUS bulk"),
    "C38": (None, None, "J5 VBUS high frequency"),
    "C39": (None, None, "J4 harness-entry bulk"),
}
# Where those three actually want to be, resolved from the connector pins at
# run time rather than typed as coordinates - the connectors have moved once
# already (regrow.py slid J3 onto the new edge) and hard numbers would rot.
TARGET_PIN = {"C37": ("J5", "A4"), "C38": ("J5", "A4"), "C39": ("J4", "1")}
STEP = 0.25            # placement grid, mm
SPAN = 14.0            # how far from the target to look


def pads_of(lib):
    """[(pin, dx, dy, w, h)] for a footprint, at zero rotation."""
    path = P.find_footprint(lib)
    t = io.open(path, encoding="utf-8").read()
    out = []
    for pm in re.finditer(r'\(pad "([^"]*)"', t):
        blk = t[pm.start():P.close_of(t, pm.start())]
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", blk)
        sz = re.search(r"\(size ([\d.]+) ([\d.]+)\)", blk)
        if at and sz:
            out.append((pm.group(1), float(at.group(1)), float(at.group(2)),
                        float(sz.group(1)), float(sz.group(2))))
    return out


def main():
    text = io.open(BOARD, encoding="utf-8").read()
    board = M.Board(text)
    netmap = S.load_net()
    bom = S.load_bom()
    ex = board.ex

    # One distance field per net a new pad lands on. `copper_masks` returns
    # distance to everything NOT on that net, which is exactly the question.
    fields = {}
    for ref in TARGETS:
        for pin in netmap.get(ref, {}):
            net = netmap[ref][pin]
            if net not in fields:
                fields[net] = board.copper_masks(net)[0]

    # Courtyards, so two parts still cannot overlap.
    boxes = {}
    for st, end, r, lib, x, y, rot in board.fps:
        b = P.courtyard(P.find_footprint(lib), x, y, rot)
        if b:
            boxes[r] = b

    # resolve the connector pins
    for ref, (pref, ppin) in TARGET_PIN.items():
        for st, end, r, lib, x, y, rot in board.fps:
            if r != pref:
                continue
            blk = board.text[st:end]
            ang = math.radians(rot)
            ca, sa = math.cos(ang), math.sin(ang)
            pat = (r'\(pad "%s"' % re.escape(ppin)
                   + r'(?:[^\n]*\n){0,3}?[^\n]*'
                   + r'\(at ([-\d.]+) ([-\d.]+)')
            m = re.search(pat, blk)
            if m:
                px, py = float(m.group(1)), float(m.group(2))
                TARGETS[ref] = (x + px * ca + py * sa - P.OX,
                                y - px * sa + py * ca - P.OY,
                                TARGETS[ref][2])

    print("  target                      placed at        from pin  note")
    seeds = {}
    for ref in sorted(TARGETS, key=lambda r: TARGETS[r][2]):
        tx, ty, what = TARGETS[ref]
        lib = bom[ref][1]
        pads = pads_of(lib)
        best = None
        span = SPAN
        while best is None and span <= SPAN * 2:
          for rot in (0, 90):
              ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
              n = int(span / STEP)
              for di in range(-n, n + 1):
                  for dj in range(-n, n + 1):
                      x = round((tx + dj * STEP) * 4) / 4 + 100
                      y = round((ty + di * STEP) * 4) / 4 + 100
                      d = math.hypot(x - 100 - tx, y - 100 - ty)
                      if best and d >= best[0]:
                          continue
                      box = P.courtyard(P.find_footprint(lib), x, y, rot)
                      if not box:
                          continue
                      if (box[0] < ex[0] + 0.5 or box[1] < ex[1] + 0.5
                              or box[2] > ex[2] - 0.5 or box[3] > ex[3] - 0.5):
                          continue
                      # NOT P.hits(): that one works in board-LOCAL coordinates
                      # and compares against BW/BH, so handing it the absolute
                      # boxes this file uses makes every position on the board
                      # read as "off the board". The bounds check above already
                      # does the outline; this is only part-versus-part.
                      if any(min(box[2], o[2]) - max(box[0], o[0]) > -0.15
                             and min(box[3], o[3]) - max(box[1], o[1]) > -0.15
                             for o in boxes.values() if o):
                          continue
                      ok = True
                      for pin, px, py, pw, ph in pads:
                          wx = x + px * ca + py * sa
                          wy = y - px * sa + py * ca
                          net = netmap.get(ref, {}).get(pin)
                          fld = fields.get(net)
                          if fld is None:
                              continue
                          # Every point OF THE PAD has to clear foreign copper by
                          # the rule. Testing the pad's circumscribed circle at
                          # its centre instead - 0.42 mm for a 0402 whose real
                          # half-width is 0.27 - reported "no spot on the whole
                          # board" for all eight parts, which was the model
                          # talking, not the board.
                          if rot % 180:
                              pw, ph = ph, pw
                          hi = int(ph / 2.0 / M.GRID)
                          hj = int(pw / 2.0 / M.GRID)
                          ci, cj = board.cell(wx, wy)
                          i0, i1, j0, j1 = ci - hi, ci + hi, cj - hj, cj + hj
                          if not (0 <= i0 and i1 < board.h
                                  and 0 <= j0 and j1 < board.w):
                              ok = False
                              break
                          if fld[M.LAYERS[0]][i0:i1 + 1, j0:j1 + 1].min() < M.RULE:
                              ok = False
                              break
                      if ok:
                          best = (d, x - 100, y - 100, rot)
          span *= 2
        if best:
            d, x, y, rot = best
            boxes[ref] = P.courtyard(P.find_footprint(lib), x + 100, y + 100, rot)
            seeds[ref] = (x, y, rot)
            print("  %-4s %-22s (%5.2f, %5.2f) r%-3d %5.2f mm"
                  % (ref, what, x, y, rot, d))
        else:
            print("  %-4s %-22s NO SPOT within %.0f mm that clears the copper"
                  % (ref, what, SPAN * 2))
    print()
    print("SEED = {")
    for ref in sorted(seeds):
        if ref in seeds:
            x, y, rot = seeds[ref]
            print('    "%s": (%.2f, %.2f),   # %s' % (ref, x, y, TARGETS[ref][2]))
    print("}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
