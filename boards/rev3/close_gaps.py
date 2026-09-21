#!/usr/bin/env python3
"""Deal with the tracks whose vias were deleted out from under them.

    ./close_gaps.py                  says what it would do
    ./close_gaps.py --apply          does it, after writing rev3.kicad_pcb.bak
    ./close_gaps.py --rebuild COL_6  also pulls that net, for maze_route.py

WHY. Most of what DRC still calls unconnected on this board is not missing
routing. It is a track on B.Cu, or on the In2.Cu plane layer, whose end sits
at EXACTLY the same x,y as a pad or a track of the same net on F.Cu, with no
via joining them - and every one is also reported as `track_dangling`. They
are the scars of vias removed by hand and by `reserve_planes.py`'s orphan
sweep while the copper they served stayed put.

Routing over them with `maze_route.py` does not help and did not: it connected
the PADS to their pour, correctly, and the orphaned stub stayed behind as a
second island on the same net, so DRC reported it all over again.

Three repairs, in order of preference:

  a via     at the junction, when a 0.6 mm via is legal there. Cheapest
            possible fix - the routing was right, only the via went missing.
  a delete  of the stub, when the net has a pour. Every pad on GND and +3.3V
            is served by its plane, so an orphaned stub on those nets carries
            nothing; it is dead copper and removing it removes the fault.
  a rebuild of the whole net, when it has no pour. Deleting one segment there
            would strand the next one, so the net's copper goes entirely and
            `maze_route.py` routes it again from its pads. Slower, but it
            cannot leave half a connection behind.

`maze_route.py`'s own distance model does the via legality test, so a via
placed here obeys exactly the same clearance rule as one placed there.
"""
import io
import math
import os
import re
import shutil
import sys
import tempfile
import uuid as _uuid

import gen_pcb as P
import maze_route as M
import stitch_gnd as SG

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")

# How far apart two same-net points on different layers may be and still count
# as "the same point". The real cases are 0.000-0.014 mm apart; the limit sits
# at a tenth of a track width so a genuine gap is never bridged by accident.
JOIN = 0.015
POUR_NETS = {n for n, _v in M.PLANE_NETS}


def dangling(path):
    """[(net, x, y, length)] - every track KiCad calls dangling."""
    NL = chr(10)
    pat = ("^" + re.escape("[track_dangling]") + "[^" + NL + "]*" + NL
           + "[^" + NL + "]*" + NL + "((?:\s*@[^" + NL + "]*" + NL + ")+)")
    out = []
    for m in re.finditer(pat, SG.drc_text(path), re.M):
        for x, y, desc in re.findall(
                r"@\((-?[\d.]+) mm, (-?[\d.]+) mm\): (.*)", m.group(1)):
            nm = re.search(r"\[([^]]*)\]", desc)
            ln = re.search(r"length ([\d.]+) mm", desc)
            out.append((nm.group(1) if nm else "", float(x), float(y),
                        float(ln.group(1)) if ln else 0.0))
    return out


def find_track(board, net, x, y, length):
    """The index of the dangling track DRC just named."""
    for k, (n, l, x1, y1, x2, y2, w, span) in enumerate(board.tracks):
        if n != net or span is None:
            continue
        if abs(math.hypot(x2 - x1, y2 - y1) - length) > 0.002:
            continue
        if min(math.hypot(x1 - x, y1 - y), math.hypot(x2 - x, y2 - y)) < 0.002:
            return k
    return None


def loose_end(board, k):
    """Which end of track k has nothing of its own net on it."""
    n, l, x1, y1, x2, y2, w, _s = board.tracks[k]
    out = []
    for x, y in ((x1, y1), (x2, y2)):
        held = False
        for j, (tn, tl, a, b, c, d, tw, _sp) in enumerate(board.tracks):
            if j == k or tn != n or tl != l:
                continue
            if SG.point_seg_dist(x, y, a, b, c, d) <= tw / 2.0 + JOIN:
                held = True
                break
        if not held:
            for vn, vx, vy, vs, _vp in board.vias:
                if vn == n and math.hypot(x - vx, y - vy) <= vs / 2.0:
                    held = True
                    break
        if not held:
            for pn, on, px, py, pw, ph, pa in board.pads:
                if pn == n and l in on and SG.point_rect_dist(
                        x, y, px, py, pw, ph, math.degrees(pa)) <= JOIN:
                    held = True
                    break
        if not held:
            out.append((x, y))
    return out


def joins_here(board, net, layer, x, y):
    """Is there same-net copper on ANOTHER layer at this exact point?"""
    for n, l, x1, y1, x2, y2, w, _s in board.tracks:
        if n != net or l == layer or l not in M.LAYERS:
            continue
        if min(math.hypot(x1 - x, y1 - y), math.hypot(x2 - x, y2 - y)) <= JOIN:
            return True
    for n, on, px, py, pw, ph, pa in board.pads:
        if n == net or n is None:
            if n == net and layer not in on and SG.point_rect_dist(
                    x, y, px, py, pw, ph, math.degrees(pa)) <= JOIN:
                return True
    return False


def dead_copper(board, net, gone_tracks, gone_vias):
    """(track indices, via indices) of `net` copper that touches no pad at all.

    Deleting one dangling SEGMENT only exposes the next one in the chain, so
    the first version of this walked a 2.45 mm B.Cu stub off the board one
    0.05 mm piece at a time - twelve DRC passes for eight stubs. A whole
    connected component is the right unit: copper that reaches no pad of its
    own net cannot be carrying anything, whatever its shape.

    Only used on the pour nets. There, every pad is served by its plane, so a
    padless component is unambiguously dead. On a signal net the same
    component might be most of a real connection with one via missing, which
    is why those get rebuilt instead.
    """
    idx, parent = {}, {}

    def find(a):
        while parent.get(a, a) != a:
            parent[a] = parent.get(parent[a], parent[a])
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    items = []
    for k, (n, l, x1, y1, x2, y2, w, sp) in enumerate(board.tracks):
        if n == net and k not in gone_tracks:
            items.append(("t", k, l, x1, y1, x2, y2, w))
    for k, (n, x, y, s, sp) in enumerate(board.vias):
        if n == net and k not in gone_vias:
            items.append(("v", k, None, x, y, x, y, s))
    for it in items:
        parent[(it[0], it[1])] = (it[0], it[1])
    for a in range(len(items)):
        ka, ia, la, ax1, ay1, ax2, ay2, aw = items[a]
        for b in range(a + 1, len(items)):
            kb, ib, lb, bx1, by1, bx2, by2, bw = items[b]
            if la and lb and la != lb:
                continue                       # two tracks, different layers
            near = min(SG.point_seg_dist(bx1, by1, ax1, ay1, ax2, ay2),
                       SG.point_seg_dist(bx2, by2, ax1, ay1, ax2, ay2),
                       SG.point_seg_dist(ax1, ay1, bx1, by1, bx2, by2),
                       SG.point_seg_dist(ax2, ay2, bx1, by1, bx2, by2))
            if near <= aw / 2.0 + bw / 2.0 + JOIN:
                union((ka, ia), (kb, ib))

    alive = set()
    for it in items:
        kind, i, l, x1, y1, x2, y2, w = it
        for pn, on, px, py, pw, ph, pa in board.pads:
            if pn != net:
                continue
            if l is not None and l not in on:
                continue
            for x, y in ((x1, y1), (x2, y2)):
                if SG.point_rect_dist(x, y, px, py, pw, ph,
                                      math.degrees(pa)) <= w / 2.0 + JOIN:
                    alive.add(find((kind, i)))
                    break
    dt, dv = set(), set()
    for kind, i, l, x1, y1, x2, y2, w in items:
        if find((kind, i)) not in alive:
            (dt if kind == "t" else dv).add(i)
    return dt, dv


def component_of(board, net, k):
    """Every track index of `net` reachable from track k through its copper."""
    same = [j for j, tr in enumerate(board.tracks)
            if tr[0] == net and tr[7] is not None]
    grp, stack = {k}, [k]
    while stack:
        a = board.tracks[stack.pop()]
        for j in same:
            if j in grp:
                continue
            c = board.tracks[j]
            if a[1] != c[1]:
                continue
            near = min(SG.point_seg_dist(c[2], c[3], a[2], a[3], a[4], a[5]),
                       SG.point_seg_dist(c[4], c[5], a[2], a[3], a[4], a[5]),
                       SG.point_seg_dist(a[2], a[3], c[2], c[3], c[4], c[5]),
                       SG.point_seg_dist(a[4], a[5], c[2], c[3], c[4], c[5]))
            if near <= a[6] / 2.0 + c[6] / 2.0 + JOIN:
                grp.add(j)
                stack.append(j)
    return grp


def redundant(board, text, net, k):
    """Would removing track k's whole component cost any connection?

    `dead_copper` only deletes copper that reaches NO pad. That misses the
    other redundant shape a pour net grows: a chain that still touches one of
    its pads at one end and dangles at the other, left over when its far via
    was deleted. The pad is served by the plane, so the chain carries nothing
    - but proving that from geometry means re-deriving KiCad's connectivity,
    which this file has already been wrong about twice.

    So it asks instead. Cut the component out of a scratch copy, run DRC on
    that, and keep the cut only if the unconnected count did not go up. One
    DRC pass per candidate, and there is rarely more than one.
    """
    grp = component_of(board, net, k)
    before = len(SG.drc_pairs(BOARD))
    out = text
    for st, end in sorted((board.tracks[j][7] for j in grp), reverse=True):
        out = out[:st - 1] + out[end:]
    tmp = tempfile.mkdtemp(prefix="rev3g")
    shutil.copy(os.path.join(HERE, "rev3.kicad_pro"),
                os.path.join(tmp, "rev3.kicad_pro"))
    trial = os.path.join(tmp, "rev3.kicad_pcb")
    io.open(trial, "w", encoding="utf-8", newline=chr(10)).write(out)
    after = len(SG.drc_pairs(trial))
    shutil.rmtree(tmp, ignore_errors=True)
    return grp if after <= before else None


def orphans(board, gone_tracks, gone_vias):
    """Vias that touch no track, no pad and no other via of their own net."""
    out = set()
    for k, (n, x, y, s, span) in enumerate(board.vias):
        if span is None or k in gone_vias:
            continue
        r = s / 2.0
        touch = False
        for j, (tn, l, x1, y1, x2, y2, w, _sp) in enumerate(board.tracks):
            if tn != n or j in gone_tracks:
                continue
            if SG.point_seg_dist(x, y, x1, y1, x2, y2) <= r + w / 2.0:
                touch = True
                break
        if not touch:
            for pn, on, px, py, pw, ph, pa in board.pads:
                if pn == n and SG.point_rect_dist(
                        x, y, px, py, pw, ph, math.degrees(pa)) <= r:
                    touch = True
                    break
        if not touch:
            for j, (vn, vx, vy, vs, _vp) in enumerate(board.vias):
                if j != k and j not in gone_vias and vn == n \
                        and math.hypot(x - vx, y - vy) <= r + vs / 2.0:
                    touch = True
                    break
        if not touch:
            out.add(k)
            print("  delete  %-8s via at (%.3f, %.3f) - touches nothing"
                  % (n, x, y))
    return out


def via_block(net, x, y):
    NL, TAB = chr(10), chr(9)
    return NL.join([
        TAB + "(via",
        TAB * 2 + "(at %g %g)" % (round(x, 4), round(y, 4)),
        TAB * 2 + "(size %g)" % M.VIA_SIZE,
        TAB * 2 + "(drill %g)" % M.VIA_DRILL,
        TAB * 2 + '(layers "F.Cu" "B.Cu")',
        TAB * 2 + '(net "%s")' % net,
        TAB * 2 + '(uuid "%s")' % _uuid.uuid4(),
        TAB + ")"])


def main():
    apply = "--apply" in sys.argv
    text = io.open(BOARD, encoding="utf-8").read()
    board = M.Board(text)

    add, drop_tracks, rebuild = [], set(), set()
    drop_v0 = set()
    # --rebuild NET pulls a net that is not faulty but is badly placed, so
    # maze_route.py can lay it again. COL_6 is the case that wanted it: it
    # runs 0.205 mm from the board edge against a 0.3 mm rule, which builds at
    # JLCPCB's 0.2 mm routed-edge limit with nothing to spare. The router's
    # own edge margins hold 0.3 mm of copper-to-edge for both tracks and
    # vias, so simply re-routing it satisfies the rule.
    for k, a in enumerate(sys.argv):
        if a == "--rebuild" and k + 1 < len(sys.argv):
            rebuild.add(sys.argv[k + 1])
    cache = {}
    for net, x, y, length in dangling(BOARD):
        k = find_track(board, net, x, y, length)
        if k is None:
            print("  ?       %-8s %.1f mm track at (%.3f, %.3f) not matched"
                  % (net, length, x, y))
            continue
        layer = board.tracks[k][1]
        fixed = False
        for ex, ey in loose_end(board, k):
            if not joins_here(board, net, layer, ex, ey):
                continue
            if net not in cache:
                cache[net] = board.build(net)[1]
            via_ok = cache[net]
            i, j = board.cell(ex, ey)
            if 0 <= i < board.h and 0 <= j < board.w and via_ok[i, j]:
                add.append((net, ex, ey))
                for di, dj in M.disc((M.VIA_SIZE + M.RULE) / M.GRID + 1):
                    if 0 <= i + di < board.h and 0 <= j + dj < board.w:
                        via_ok[i + di, j + dj] = False
                print("  via     %-8s at (%.3f, %.3f)" % (net, ex, ey))
                fixed = True
        if fixed:
            continue
        if net not in POUR_NETS:
            rebuild.add(net)
            continue
        grp = redundant(board, text, net, k)
        if grp:
            drop_tracks |= grp
            print("  delete  %-8s %d segment(s) - DRC says the pour already "
                  "carries this" % (net, len(grp)))

    # Every pour net is swept, not just the ones DRC happened to flag. Dead
    # copper does not always dangle - a stub with BOTH ends buried in other
    # copper of its own net raises no `track_dangling` at all, and is just as
    # dead - and the sweep is cheap enough to run unconditionally.
    for net in sorted(POUR_NETS):
        dt, dv = dead_copper(board, net, drop_tracks, set())
        if not (dt or dv):
            continue
        mm = sum(math.hypot(board.tracks[k][4] - board.tracks[k][2],
                            board.tracks[k][5] - board.tracks[k][3])
                 for k in dt)
        drop_tracks |= dt
        drop_v0 |= dv
        print("  delete  %-8s %d segment(s) (%.1f mm) and %d via(s) reaching "
              "no pad - the pour serves these" % (net, len(dt), mm, len(dv)))

    drop_vias = set(drop_v0)
    for net in sorted(rebuild):
        ts, vs = M.net_copper(board, net)
        drop_tracks |= ts
        drop_vias |= vs
        print("  rebuild %-8s - %d segment(s) and %d via(s) removed, "
              "maze_route.py routes it again" % (net, len(ts), len(vs)))

    # A via left holding nothing once the copper around it has gone. Removing
    # the tracks and leaving the vias is what turned COL_19's rebuild into two
    # fresh `via_dangling` faults, so the sweep runs against the board AFTER
    # the deletions above, not before.
    drop_vias |= orphans(board, drop_tracks, drop_vias)

    print()
    print("  %d via(s) to add, %d segment(s) and %d via(s) to delete, "
          "%d net(s) to rebuild"
          % (len(add), len(drop_tracks), len(drop_vias), len(rebuild)))
    if not (add or drop_tracks or drop_vias):
        print("  nothing to do")
        return 0
    if not apply:
        print(chr(10) + "dry run. re-run with --apply to write it.")
        return 0

    spans = [board.tracks[k][7] for k in drop_tracks]
    spans += [board.vias[k][4] for k in drop_vias]
    for st, end in sorted(spans, reverse=True):
        text = text[:st - 1] + text[end:]
    if add:
        tail = text.rindex(")")
        text = text[:tail] \
            + chr(10).join(via_block(n, x, y) for n, x, y in add) \
            + chr(10) + text[tail:]
    shutil.copy(BOARD, BOARD + ".bak")
    io.open(BOARD, "w", encoding="utf-8", newline=chr(10)).write(text)
    print(chr(10) + "wrote rev3.kicad_pcb (previous version saved as .bak)")
    if rebuild:
        print("  now run ./maze_route.py --apply to route %s"
              % ", ".join(sorted(rebuild)))
    return P.check()


if __name__ == "__main__":
    sys.exit(main())
