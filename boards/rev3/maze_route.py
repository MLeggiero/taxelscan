#!/usr/bin/env python3
"""Finish the routing with a grid maze router (A*), not straight lines.

    ./maze_route.py            says what it would do
    ./maze_route.py --apply    does it, after writing rev3.kicad_pcb.bak

WHY THIS EXISTS. `stitch_gnd.py` and `bridge_nets.py` both try to connect two
points with a straight line or a single bend, and on the dense half of this
board that fails: 29 GND islands and 12 other gaps had no such path, confirmed
by testing candidate rings out to 20 mm and by real DRC finding 16 genuine
shorts when the straight lines were forced through anyway. The problem is not
the clearance model - it is that a router has to be able to go AROUND things,
and neither of those scripts can.

This one rasterises the board onto a 0.1 mm grid, one occupancy mask per
copper layer, and runs A* from source to target with layer changes costed as
vias. It can therefore route the way a person would: out, around, and back.

WHAT IT ROUTES

  GND      each island to the nearest point where a via is legal. Any via
           reaches In1.Cu, which is one contiguous pour, so "reach a legal via
           site" is the whole job - no need to find another GND pad.
  +3.3V    the same, except a fresh via is NOT allowed. In2.Cu carries a
           +3.3V pour too, but the 220 segments routed on that layer cut it
           into eleven separate polygons, so a via there can land in a
           fragment that connects to nothing. +3.3V has to reach copper KiCad
           already considers connected.
  others   point to point, between the two endpoints DRC names.

In1.Cu is never a routing layer here: it is the ground plane, and the entire
argument for four layers on a megohm-impedance board is that it stays whole.
F.Cu, In2.Cu and B.Cu are fair game, which is what makes the detours possible
- B.Cu in particular is nearly empty.

WHERE THE WORK LIST COMES FROM. `kicad-cli pcb drc`, every run. Both the older
hand-transcribed list in `bridge_nets.PAIRS` and `stitch_gnd.net_islands()`'s
endpoint-union model disagreed with KiCad about what was actually unconnected,
in opposite directions, and both were wrong the moment the board changed.

The analog nets (SENSE_*, GAIN_*, AMP_*, ADC_*, VREF) are deliberately absent
from everything below. They are already routed, they belong on F.Cu over solid
ground, and they are not something to hand to a grid search.
"""
import heapq
import io
import math
import os
import re
import shutil
import sys
import time
import uuid as _uuid

import numpy as np

import gen_pcb as P
import gen_schematic as S
import sync_pcb as SY
import stitch_gnd as SG

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")

GRID = 0.05                      # mm per cell
TRACK_W = 0.15
RULE = 0.15                      # netclass Default clearance, the real rule
HOLE_RULE = 0.25                 # min_hole_clearance, copper to drill edge
VIA_SIZE = 0.5
VIA_DRILL = 0.3

# There is no rasterisation margin any more: `copper_masks()` computes the
# exact distance to every shape rather than dilating a bitmap, so the rule is
# the rule. SAFETY is only there to absorb the 0.0001 mm the emitted
# coordinates are rounded to, with a wide margin.
#
# The two constants this replaces, 0.22 and then 0.19, were both guesses at
# the error in a bitmap model, and both happened to come out as the SAME five
# cells of dilation - which is why swapping one for the other changed nothing
# and real clearances stayed at 0.102-0.144 mm against a 0.150 rule.
SAFETY = 0.002

# Board edge. How far a CENTRE has to stay back depends on how wide the thing
# is, so one figure for both - as `EDGE_KEEPOUT = 0.35` was - is either too
# tight for a via or needlessly wide for a track. It was 0.25 mm short for a
# 0.6 mm via, which is exactly the copper_edge_clearance a re-routed COL_6
# came back with.
#
# 0.22 mm, not the 0.3 mm KiCad's rule block used to ask for, and this is a
# deliberate decision rather than a slipped constant. COL_6 ends on a 0.5 mm
# pitch FFC pin 1.5 mm from the right edge, and its escape is sealed by its
# own neighbours' fanout: it routes at 0.22 and does not at 0.24. The choice
# is one dead sensor column against 0.08 mm of edge margin. JLCPCB's minimum
# trace-to-outline for a ROUTED edge - which this board has - is 0.2 mm, so
# 0.22 keeps a little margin over their stated capability and none over their
# recommendation. `rev3.kicad_pro` carries the same 0.22 so DRC judges the
# board by the rule it was actually designed to.
EDGE_RULE = 0.22
EDGE_TRACK = TRACK_W / 2.0 + EDGE_RULE + SAFETY
EDGE_VIA = VIA_SIZE / 2.0 + EDGE_RULE + SAFETY

# Routing layers, in board order. In1.Cu (the GND plane) is NEVER here: it is
# the reference the whole four-layer argument rests on for a megohm-impedance
# front end, and it stays whole.
#
# In2.Cu is a judgement call with a measured price, not a default. It carries
# the +3.3V pour that 138 stitching vias land in, and letting the router cross
# it put 147 segments through it: the pour dropped 1574.5 -> 1482.9 mm2 and
# three stitching vias were orphaned into fragments, taking +3.3V from 0
# unconnected to 3. It is also the reference plane for every B.Cu segment, so
# slotting it degrades the return path for half the routing.
#
# Against that, dropping it costs six signal connections outright - two full
# two-layer passes converged at 14 where the three-layer pass reached 11 (8
# once the self-inflicted +3.3V orphans are discounted). gen_pcb.py declares
# In2.Cu as `mixed`, so mixed use was the original intent; it was changed to
# `power` only to stop freerouting treating the pour as a signal-layer
# obstacle, which was a fix for a different problem.
LAYERS = ["F.Cu", "In2.Cu", "B.Cu"]
VIA_COST = 12                    # in cells; discourages gratuitous layer hops
# Neighbouring nets tried per walled-in pad. Each try is a full mask rebuild
# plus an A* plus a re-route, so this is the run time: at 20, a pass over a
# board with 40 freshly-ripped signal nets ran for over an hour without
# finishing. 4 finds nearly all the same rips - the useful candidate is
# almost always among the first few, because rip_nets() already sorts by how
# little copper the net has.
#
# Tried at 10 and put back to 4, with the measurement to justify each move.
# 10 was set because ROW_18 has to cross SIX rippable nets in 3.35 mm, so at
# 4 tries it could never clear that corridor. It bought exactly ONE connection
# (14 -> 13), because the real limit is not how many candidates are tried but
# that they are tried ONE AT A TIME - see the loop at rip_nets()'s only two
# call sites. Six nets walling in one pocket cannot be cleared by any number
# of single-net rips, so raising this does not address the case it was raised
# for.
#
# It is also not free. At 10 with In2.Cu back in LAYERS the pass ran 71
# minutes without writing anything and had to be killed; at 4 on the same
# three layers it finishes in about 15. Three layers roughly triples the A*
# state space, so the two settings multiply.
RIP_TRIES = 4

# How many nets the corridor-clear fallback may pull at once. This is the
# capability the single-net loop above structurally lacks: it tries one
# candidate, routes, re-routes, and moves on, so a pad walled in by SIX nets
# (ROW_18, 3.35 mm) can never be freed however many candidates are tried -
# measured at RIP_TRIES 4 and 10, converging at 14 and 13. Clearing the whole
# corridor and putting each net back in turn is bounded work - one extra
# build+A* per stuck connection plus at most MULTI_RIP re-routes - and the
# re-routes are the easy kind, because rip_nets() already sorts by how little
# copper a net has and the six walling ROW_18 in are two-pad row lines.
MULTI_RIP = 12

# Wall-clock budget per stuck connection for the corridor clear, in seconds.
# The protect-and-retry loop is bounded by MULTI_RIP attempts, but each
# attempt is two mask builds plus up to MULTI_RIP full re-routes, so the
# worst case per connection is minutes and the worst case per pass is hours
# with no output to show for it. A connection that will not close inside
# this budget is a placement problem wearing a routing problem's clothes;
# say so and move to the next one.
CLEAR_BUDGET = 240

# (net, may a fresh via be dropped straight onto the pour?). In1.Cu's GND pour
# is one contiguous polygon, so any via reaches all of it. In2.Cu's +3.3V pour
# is cut into eleven by the segments routed on that layer, so a via there can
# land in a fragment that goes nowhere - +3.3V has to reach copper that is
# already connected instead.
PLANE_NETS = [("GND", True), ("+3.3V", False)]


def disc(r_cells):
    """Offsets covering a filled disc of the given radius, in cells."""
    out = []
    r = int(math.ceil(r_cells))
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r_cells * r_cells:
                out.append((dy, dx))
    return out


def dilate(mask, r_cells):
    """Binary dilation by a disc. numpy shifts - scipy is not installed."""
    if r_cells <= 0:
        return mask
    out = np.zeros_like(mask)
    h, w = mask.shape
    for dy, dx in disc(r_cells):
        ys0, ys1 = max(0, dy), min(h, h + dy)
        xs0, xs1 = max(0, dx), min(w, w + dx)
        yd0, yd1 = max(0, -dy), min(h, h - dy)
        xd0, xd1 = max(0, -dx), min(w, w - dx)
        out[ys0:ys1, xs0:xs1] |= mask[yd0:yd1, xd0:xd1]
    return out


class Board:
    def __init__(self, text):
        self.text = text
        self.fps = SY.footprints(text)
        self.ex = SY.board_extent(text)
        self.ox, self.oy = self.ex[0], self.ex[1]
        self.w = int(math.ceil((self.ex[2] - self.ex[0]) / GRID)) + 1
        self.h = int(math.ceil((self.ex[3] - self.ex[1]) / GRID)) + 1
        self.netmap = S.load_net()
        self._collect()

    # ---------------------------------------------------------------- geometry
    def cell(self, x, y):
        return (int(round((y - self.oy) / GRID)), int(round((x - self.ox) / GRID)))

    def world(self, i, j):
        return (self.ox + j * GRID, self.oy + i * GRID)

    def _collect(self):
        """Every piece of copper on the board, as (net, layer, shape)."""
        self.pads, self.tracks, self.vias = [], [], []
        for st, end, ref, lib, x, y, rot in self.fps:
            blk = self.text[st:end]
            ang = math.radians(rot)
            ca, sa = math.cos(ang), math.sin(ang)
            for pm in re.finditer(r'\(pad "([^"]*)"', blk):
                pblk = blk[pm.start():P.close_of(blk, pm.start())]
                at = re.search(r"\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)", pblk)
                sz = re.search(r"\(size ([\d.]+) ([\d.]+)\)", pblk)
                lay = re.search(r'\(layers?\s+((?:"[^"]+"\s*)+)\)', pblk)
                if not at or not sz:
                    continue
                # A drilled pad is governed by the HOLE rule as well as the
                # copper rule, and on a pad with no annular ring the hole rule
                # is the binding one. J5's mounting hole is (size 0.65)
                # (drill 0.65): copper wants 0.15 mm, the hole wants 0.25, and
                # modelling only the copper let a SYNC_B track run 0.10 mm too
                # close - a real hole_clearance error DRC caught and the
                # router could not see.
                dr = re.search(r"\(drill (?:oval )?([\d.]+)(?: ([\d.]+))?\)",
                               pblk)
                grow = 0.0
                if dr:
                    hr = max(float(dr.group(1)),
                             float(dr.group(2)) if dr.group(2) else 0.0) / 2.0
                    cr = min(float(sz.group(1)), float(sz.group(2))) / 2.0
                    grow = max(0.0, hr + HOLE_RULE - cr - RULE)
                px, py = float(at.group(1)), float(at.group(2))
                # A pad's own angle is ABSOLUTE - it already contains the
                # footprint's rotation - and an absent angle means the pad
                # simply follows the footprint. Subtracting `ang` here, as
                # this did, rotated every pad on a rotated part by the wrong
                # amount: J4's 2.7 x 1.0 mm mounting pad was modelled 1.0 x
                # 2.7, so the router "reached" it with a via sitting 0.45 mm
                # off its real edge and reported the island connected while
                # KiCad called the via dangling. Same trap as the pad SHAPES
                # in gen_pcb.py, one layer down.
                pang = math.radians(float(at.group(3))) if at.group(3) \
                    else ang
                wx, wy = x + px * ca + py * sa, y - px * sa + py * ca
                net = self.netmap.get(ref, {}).get(pm.group(1))
                layers = lay.group(1) if lay else ""
                all_cu = "*.Cu" in layers
                on = LAYERS if all_cu else [l for l in LAYERS if l in layers]
                self.pads.append((net, on, wx, wy,
                                  float(sz.group(1)) + 2 * grow,
                                  float(sz.group(2)) + 2 * grow, pang))
        for m in re.finditer(r"\n\t\(segment\b", self.text):
            st = m.start() + 1
            blk = self.text[st:P.close_of(self.text, st)]
            s = re.search(r"\(start ([-\d.]+) ([-\d.]+)\)", blk)
            e = re.search(r"\(end ([-\d.]+) ([-\d.]+)\)", blk)
            w = re.search(r"\(width ([\d.]+)\)", blk)
            n = re.search(r'\(net "([^"]*)"\)', blk)
            l = re.search(r'\(layer "([^"]+)"\)', blk)
            if s and e:
                # The text span rides along so a segment can be RIPPED OUT
                # later. Nothing else needs it, and it is None for copper this
                # run creates, which is never a rip-up candidate.
                self.tracks.append((n.group(1) if n else "", l.group(1),
                                    float(s.group(1)), float(s.group(2)),
                                    float(e.group(1)), float(e.group(2)),
                                    float(w.group(1)) if w else TRACK_W,
                                    (st, P.close_of(self.text, st))))
        for m in re.finditer(r"\n\t\(via\b", self.text):
            st = m.start() + 1
            end = P.close_of(self.text, st)
            blk = self.text[st:end]
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)\)", blk)
            sz = re.search(r"\(size ([\d.]+)\)", blk)
            n = re.search(r'\(net "([^"]*)"\)', blk)
            if at:
                self.vias.append((n.group(1) if n else "", float(at.group(1)),
                                  float(at.group(2)),
                                  float(sz.group(1)) if sz else VIA_SIZE,
                                  (st, end)))

    # ------------------------------------------------------------ rasterising
    def _blank(self):
        return np.zeros((self.h, self.w), dtype=bool)

    def _stamp_rect(self, mask, cx, cy, w, h, ang):
        i0, j0 = self.cell(cx, cy)
        rad = math.hypot(w, h) / 2.0 / GRID + 2
        ca, sa = math.cos(-ang), math.sin(-ang)
        for di in range(-int(rad), int(rad) + 1):
            for dj in range(-int(rad), int(rad) + 1):
                i, j = i0 + di, j0 + dj
                if not (0 <= i < self.h and 0 <= j < self.w):
                    continue
                wx, wy = self.world(i, j)
                dx, dy = wx - cx, wy - cy
                lx = dx * ca - dy * sa
                ly = dx * sa + dy * ca
                if abs(lx) <= w / 2.0 and abs(ly) <= h / 2.0:
                    mask[i, j] = True

    def _stamp_seg(self, mask, x1, y1, x2, y2, w):
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) / (GRID / 2)))
        off = disc(w / 2.0 / GRID)
        for k in range(n + 1):
            t = k / n
            i0, j0 = self.cell(x1 + t * (x2 - x1), y1 + t * (y2 - y1))
            for di, dj in off:
                i, j = i0 + di, j0 + dj
                if 0 <= i < self.h and 0 <= j < self.w:
                    mask[i, j] = True

    def _win(self, cx, cy, reach):
        """Index window covering (cx,cy) +- reach, plus the cell grids in it."""
        i0 = max(0, int(math.floor((cy - reach - self.oy) / GRID)))
        i1 = min(self.h - 1, int(math.ceil((cy + reach - self.oy) / GRID)))
        j0 = max(0, int(math.floor((cx - reach - self.ox) / GRID)))
        j1 = min(self.w - 1, int(math.ceil((cx + reach - self.ox) / GRID)))
        if i0 > i1 or j0 > j1:
            return None
        ys = (self.oy + np.arange(i0, i1 + 1) * GRID)[:, None]
        xs = (self.ox + np.arange(j0, j1 + 1) * GRID)[None, :]
        return i0, i1, j0, j1, xs, ys

    def _dist_rect(self, fld, cx, cy, w, h, ang, reach):
        win = self._win(cx, cy, math.hypot(w, h) / 2.0 + reach)
        if not win:
            return
        i0, i1, j0, j1, xs, ys = win
        ca, sa = math.cos(-ang), math.sin(-ang)
        dx, dy = xs - cx, ys - cy
        lx = np.abs(dx * ca - dy * sa) - w / 2.0
        ly = np.abs(dx * sa + dy * ca) - h / 2.0
        d = np.hypot(np.maximum(lx, 0.0), np.maximum(ly, 0.0))
        sub = fld[i0:i1 + 1, j0:j1 + 1]
        np.minimum(sub, d, out=sub)

    def _dist_seg(self, fld, x1, y1, x2, y2, w, reach):
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        half = math.hypot(x2 - x1, y2 - y1) / 2.0
        win = self._win(cx, cy, half + w / 2.0 + reach)
        if not win:
            return
        i0, i1, j0, j1, xs, ys = win
        ax, ay = x2 - x1, y2 - y1
        L2 = ax * ax + ay * ay
        px, py = xs - x1, ys - y1
        if L2 == 0.0:
            d = np.hypot(px, py)
        else:
            t = np.clip((px * ax + py * ay) / L2, 0.0, 1.0)
            d = np.hypot(px - t * ax, py - t * ay)
        d = np.maximum(d - w / 2.0, 0.0)
        sub = fld[i0:i1 + 1, j0:j1 + 1]
        np.minimum(sub, d, out=sub)

    def copper_masks(self, net, drop=(), drop_vias=()):
        """(dist[layer], own[layer]) - EXACT mm to the nearest foreign copper
        on each layer, and a boolean mask of this net's own copper.

        This used to be two boolean masks, with foreign copper dilated by a
        disc to make the keep-out. That model was wrong twice over - shapes
        under-drawn by up to a cell diagonal, and `disc(r)` reaching only
        floor(r) cells - and the two errors together produced real clearances
        of 0.102 mm against a 0.150 rule. Paying for them in the radius costs
        0.075 mm of corridor on every side, which on 0.65 mm pitch is the
        difference between a ground pin having a way out and not.

        A distance field has neither error. Every shape here is convex - a
        rotated rectangle, a capsule, a circle - so the distance from a cell
        centre to it is exact and cheap, and the minimum over shapes is the
        distance to the copper. It is also faster than dilating: a few hundred
        small windows instead of ~400 full-array shifts.

        The straight run BETWEEN two adjacent cell centres needs no separate
        check: for a convex shape the distance along a segment is minimised at
        an endpoint, so two safe endpoints make a safe segment.

        `drop` / `drop_vias` are indices to pretend are not there, which is how
        rip-up-and-reroute asks "would this pad have a way out if that net
        moved?".
        """
        INF = np.float32(1e6)
        reach = VIA_SIZE / 2.0 + RULE + 0.05      # the largest gap we care about
        dist = {l: np.full((self.h, self.w), INF, np.float32) for l in LAYERS}
        own = {l: self._blank() for l in LAYERS}
        for n, on, x, y, w, h, a in self.pads:
            for l in on:
                if n == net:
                    self._stamp_rect(own[l], x, y, w, h, a)
                else:
                    self._dist_rect(dist[l], x, y, w, h, a, reach)
        for k, (n, l, x1, y1, x2, y2, w, _span) in enumerate(self.tracks):
            if l not in LAYERS or k in drop:
                continue
            if n == net:
                self._stamp_seg(own[l], x1, y1, x2, y2, w)
            else:
                self._dist_seg(dist[l], x1, y1, x2, y2, w, reach)
        for k, (n, x, y, s, _sp) in enumerate(self.vias):
            if k in drop_vias:
                continue
            for l in LAYERS:
                if n == net:
                    self._stamp_seg(own[l], x, y, x, y, s)
                else:
                    self._dist_seg(dist[l], x, y, x, y, s, reach)
        return dist, own

    def build(self, net, drop=(), drop_vias=()):
        """Per-layer boolean 'a track centre may sit here', plus via legality."""
        dist, own = self.copper_masks(net, drop, drop_vias)
        need_t = TRACK_W / 2.0 + RULE + SAFETY
        need_v = VIA_SIZE / 2.0 + RULE + SAFETY
        free = {}
        for l in LAYERS:
            free[l] = (dist[l] >= need_t) | own[l]
        # Board edge, one margin per width.
        edge = self._blank()
        m = int(math.ceil(EDGE_TRACK / GRID))
        edge[:m, :] = edge[-m:, :] = True
        edge[:, :m] = edge[:, -m:] = True
        for l in LAYERS:
            # A pad is where it is. Masking the net's OWN copper with the edge
            # band made any pad inside that band unreachable, so COL_6 - whose
            # far end is a connector pin near the right edge - reported "no
            # route" for a connection that had been routed for weeks. The band
            # constrains where NEW copper may go, not where existing copper is.
            free[l] = (free[l] & ~edge) | own[l]
        vedge = self._blank()
        m = int(math.ceil(EDGE_VIA / GRID))
        vedge[:m, :] = vedge[-m:, :] = True
        vedge[:, :m] = vedge[:, -m:] = True

        # A via needs room on EVERY layer, and space from other vias.
        via_ok = ~vedge
        for l in LAYERS:
            via_ok &= dist[l] >= need_v
        for k, (n, x, y, s, _sp) in enumerate(self.vias):
            if k in drop_vias:
                continue
            i, j = self.cell(x, y)
            for di, dj in disc((s / 2.0 + VIA_SIZE / 2.0 + RULE) / GRID + 1):
                if 0 <= i + di < self.h and 0 <= j + dj < self.w:
                    via_ok[i + di, j + dj] = False
        return free, via_ok, own


def astar(free, via_ok, starts, goals, goal_test=None):
    """Multi-source, multi-goal A* over (layer, i, j). Returns a path or None."""
    h, w = free[LAYERS[0]].shape
    li = {l: k for k, l in enumerate(LAYERS)}
    dist = {}
    prev = {}
    pq = []
    for (l, i, j) in starts:
        dist[(l, i, j)] = 0
        heapq.heappush(pq, (0, 0, (l, i, j)))
    goalset = set(goals) if goals else None

    def hcost(node):
        if not goalset:
            return 0
        _l, i, j = node
        return min(abs(i - gi) + abs(j - gj) for (_gl, gi, gj) in goalset)

    steps = [(-1, 0, 10), (1, 0, 10), (0, -1, 10), (0, 1, 10),
             (-1, -1, 14), (-1, 1, 14), (1, -1, 14), (1, 1, 14)]
    seen = set()
    while pq:
        _f, g, node = heapq.heappop(pq)
        if node in seen:
            continue
        seen.add(node)
        if (goalset and node in goalset) or (goal_test and goal_test(node)):
            path = [node]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return path[::-1]
        l, i, j = node
        for di, dj, cost in steps:
            ni, nj = i + di, j + dj
            if not (0 <= ni < h and 0 <= nj < w):
                continue
            if not free[l][ni, nj]:
                continue
            nd = g + cost
            key = (l, ni, nj)
            if nd < dist.get(key, 1 << 30):
                dist[key] = nd
                prev[key] = node
                heapq.heappush(pq, (nd + hcost(key), nd, key))
        if via_ok[i, j]:
            for l2 in LAYERS:
                if l2 == l or not free[l2][i, j]:
                    continue
                nd = g + VIA_COST * 10
                key = (l2, i, j)
                if nd < dist.get(key, 1 << 30):
                    dist[key] = nd
                    prev[key] = node
                    heapq.heappush(pq, (nd + hcost(key), nd, key))
    return None


def emit(board, net, path):
    """Path of (layer,i,j) -> track segments and vias, collapsing straight runs."""
    NL, TAB = chr(10), chr(9)
    out = []
    run = [path[0]]
    for node in path[1:]:
        if node[0] != run[-1][0]:            # layer change: via
            for pts in (run,):
                pass
            out.append(("seg", run))
            out.append(("via", node))
            run = [node]
        else:
            run.append(node)
    out.append(("seg", run))

    blocks = []
    for kind, item in out:
        if kind == "via":
            l, i, j = item
            x, y = board.world(i, j)
            blocks.append(NL.join([
                TAB + "(via",
                TAB * 2 + "(at %g %g)" % (round(x, 4), round(y, 4)),
                TAB * 2 + "(size %g)" % VIA_SIZE,
                TAB * 2 + "(drill %g)" % VIA_DRILL,
                TAB * 2 + '(layers "F.Cu" "B.Cu")',
                TAB * 2 + '(net "%s")' % net,
                TAB * 2 + '(uuid "%s")' % _uuid.uuid4(),
                TAB + ")"]))
            continue
        pts = item
        if len(pts) < 2:
            continue
        # collapse collinear runs so the file gets a few long tracks, not 400
        keep = [pts[0]]
        for k in range(1, len(pts) - 1):
            a, b, c = pts[k - 1], pts[k], pts[k + 1]
            if (b[1] - a[1], b[2] - a[2]) != (c[1] - b[1], c[2] - b[2]):
                keep.append(b)
        keep.append(pts[-1])
        lay = pts[0][0]
        for a, b in zip(keep, keep[1:]):
            ax, ay = board.world(a[1], a[2])
            bx, by = board.world(b[1], b[2])
            # Never emit a zero-length track. Identical endpoints make
            # freerouting's combine_at_end() treat the trace as its own
            # neighbour and recurse until the JVM stack dies. Compared at the
            # precision actually written, because that is where two distinct
            # points can collapse into one.
            if ("%g %g" % (round(ax, 4), round(ay, 4))
                    == "%g %g" % (round(bx, 4), round(by, 4))):
                continue
            blocks.append(NL.join([
                TAB + "(segment",
                TAB * 2 + "(start %g %g)" % (round(ax, 4), round(ay, 4)),
                TAB * 2 + "(end %g %g)" % (round(bx, 4), round(by, 4)),
                TAB * 2 + "(width %g)" % TRACK_W,
                TAB * 2 + '(layer "%s")' % lay,
                TAB * 2 + '(net "%s")' % net,
                TAB * 2 + '(uuid "%s")' % _uuid.uuid4(),
                TAB + ")"]))
    return blocks


def plane_targets(board, net):
    """Every `net` item KiCad calls unconnected, one start cell each.

    Grouping into islands first is deliberately NOT done here. `flood_own()`
    already expands each start cell through all of its own copper before A*
    runs, so two anchors that share an island produce the same search; and
    once the first one lands, the whole merged island is marked connected, so
    the second finishes immediately. Pre-grouping only added a second, weaker
    connectivity model that disagreed with KiCad's - the same disagreement
    that had `stitch_gnd.py` chasing 28 islands when there were 16 gaps.
    """
    out = []
    for x, y, ref, pin in SG.drc_unconnected(BOARD, net):
        i, j = board.cell(x, y)
        if 0 <= i < board.h and 0 <= j < board.w:
            out.append(("%s.%s" % (ref, pin) if ref else
                        "copper at (%.2f, %.2f)" % (x, y), [(i, j)]))
    return out


# Nets that are never ripped up. A grid search optimises for length and via
# count and knows nothing about why any of these is shaped the way it is, so
# it may route AROUND them but never move them.
#
#   analog chain      placed and routed the way rev-1 proved works: short, on
#                     F.Cu, over solid ground.
#   ROW_VCC           VREF is tapped off it through R10/C10. That tap is what
#                     makes the conversion ratiometric and deletes rev-1's
#                     firmware rail-sag correction; a long thin re-route puts
#                     IR drop between the divider and its own reference.
#   VREG_LX           the buck's switching node. It is the one net on the
#                     board where loop area is the specification.
#   power rails       widened or short on purpose, and +3.3V/GND also belong
#                     to zones, which this router does not model.
#   USB_DM/DP,
#   BUS_*, SYNC_*     differential pairs; routed as pairs or not at all.
#
#   VCORE             REMOVED, and the reason is worth keeping. It was listed
#                     while its copper was hand-laid. After the re-layout every
#                     VCORE segment on the board is autorouter output, and the
#                     net itself was one of the last five left open - so the
#                     entry protected a broken state. Worse, its 103 tracks
#                     and 11 vias were the one seal on VREG_LX: with every
#                     rippable net dropped VREG_LX still had no path, with
#                     VCORE alone dropped it routes in 5.58 mm on F.Cu without
#                     a via (measured in lxwhat.py, all seven combinations).
#                     Put it back once VCORE is placed deliberately again.
#   ADC_A/ADC_B ONLY, not an "ADC_" prefix. The prefix also catches ADC_SCK,
#                     ADC_SDI, ADC_SDO and ADC_CONV, which are ordinary
#                     digital SPI signals with no placement requirement at
#                     all. protect_dsn.py spells this distinction out and gets
#                     it right; this list did not, and the cost was real - 13
#                     of the 53 "protected" items walling in the VCORE route
#                     were SPI, and ADC_CONV was itself one of the connections
#                     left unrouted.
NEVER_RIP = ("SENSE_", "GAIN_", "AMP_", "ADC_A", "ADC_B", "VREF",
             "XIN", "XOUT", "ROW_VCC", "VREG_LX", "FB",
             "+3.3V", "+5V", "GND", "USB_D", "BUS_", "SYNC_")


def pocket(board, free, via_ok, own, cells):
    """Everywhere a track starting at `cells` can reach without a rip-up."""
    starts = flood_own(own, free, cells)
    seen, stack = set(starts), list(starts)
    while stack:
        l, i, j = stack.pop()
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)):
            ni, nj = i + di, j + dj
            if (0 <= ni < board.h and 0 <= nj < board.w
                    and (l, ni, nj) not in seen and free[l][ni, nj]):
                seen.add((l, ni, nj))
                stack.append((l, ni, nj))
        for l2 in LAYERS:
            if l2 != l and (l2, i, j) not in seen and free[l2][i, j] \
                    and via_ok[i, j]:
                seen.add((l2, i, j))
                stack.append((l2, i, j))
    return seen, starts


def rip_nets(board, net, seen, reach=1.2):
    """Nets walling in a pocket, fewest segments first.

    Fewest segments is a decent proxy for "cheapest to put back somewhere
    else": a two-pad row line re-routes in one A* call, a long chain does not.
    """
    order = {}
    for k in rip_candidates(board, net, seen, reach):
        n = board.tracks[k][0]
        order.setdefault(n, 0)
    for n in order:
        order[n] = len(net_copper(board, n)[0])
    return sorted(order, key=lambda n: order[n])


def corridor_nets(board, net, path, reach=0.45):
    """Nets whose rippable copper stands within `reach` mm of `path`, cheapest
    to put back first.

    rip_nets() looks at the pocket around ONE endpoint, which is the right
    question for a pad boxed in by its own neighbours and the wrong one for a
    13 mm run walled in along its whole length - MUX_S2 and SWDIO fail that
    way. This takes the path that WOULD exist with every rippable net gone
    (the upper bound measured in upperbound.py: 9 of 10 stuck connections
    have one) and names exactly the nets standing on it, so the corridor
    clear pulls what is in the way and nothing else.
    """
    r = int(math.ceil(reach / GRID))
    cells = set()
    for _l, i, j in path:
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                cells.add((i + di, j + dj))
    hit = set()
    for n, l, x1, y1, x2, y2, w, span in board.tracks:
        if span is None or n == net or l not in LAYERS or n in hit:
            continue
        if any(n.startswith(p) for p in NEVER_RIP):
            continue
        a, b = board.cell(x1, y1), board.cell(x2, y2)
        steps = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1])))
        for s in range(steps + 1):
            c = (round(a[0] + (b[0] - a[0]) * s / steps),
                 round(a[1] + (b[1] - a[1]) * s / steps))
            if c in cells:
                hit.add(n)
                break
    return sorted(hit, key=lambda n: len(net_copper(board, n)[0]))


def rip_candidates(board, net, seen, reach=1.2):
    """Segments walling in a pocket, cheapest to put back first.

    A pad on 0.65 mm pitch cannot escape between its own neighbours - the gap
    is 0.25 mm and a 0.15 mm track with 0.15 mm either side needs 0.45 - so
    the only ways out are past the end of the pin row, and on this board a
    fanout bus runs along exactly there. Eight ground pins ended up in pockets
    the size of their own pad. Something has to move, and the cheapest thing
    to move is the shortest segment touching the pocket wall: ripping one
    segment leaves exactly two loose ends, which is an ordinary point-to-point
    route for the same A* that is already running.
    """
    if not seen:
        return []
    ii = [i for _l, i, _j in seen]
    jj = [j for _l, _i, j in seen]
    r = reach / GRID
    lo_i, hi_i = min(ii) - r, max(ii) + r
    lo_j, hi_j = min(jj) - r, max(jj) + r
    out = []
    for k, (n, l, x1, y1, x2, y2, w, span) in enumerate(board.tracks):
        if span is None or n == net or l not in LAYERS:
            continue
        if any(n.startswith(p) for p in NEVER_RIP):
            continue
        a, b = board.cell(x1, y1), board.cell(x2, y2)
        if max(a[0], b[0]) < lo_i or min(a[0], b[0]) > hi_i:
            continue
        if max(a[1], b[1]) < lo_j or min(a[1], b[1]) > hi_j:
            continue
        out.append((math.hypot(x2 - x1, y2 - y1), k))
    out.sort()
    return [k for _len, k in out]


def block_vias(board, via_ok, path):
    """Stop a later via landing on one this path just placed at a layer change.

    Only the GND phase's terminal via was doing this. A route that hops layers
    mid-way places vias too, and two of them in the same hole is a
    `hole_to_hole` violation that DRC finds and the router never sees.
    """
    for a, b in zip(path, path[1:]):
        if a[0] == b[0]:
            continue
        for di, dj in disc((VIA_SIZE + RULE) / GRID + 1):
            i, j = b[1] + di, b[2] + dj
            if 0 <= i < board.h and 0 <= j < board.w:
                via_ok[i, j] = False


def net_copper(board, net):
    """(track indices, via indices) of everything already on `net`.

    Only copper that came from the FILE - anything this run placed has no text
    span, cannot be deleted, and is not a rip-up candidate.
    """
    ts = {k for k, t in enumerate(board.tracks)
          if t[0] == net and t[7] is not None}
    vs = {k for k, v in enumerate(board.vias)
          if v[0] == net and v[4] is not None}
    return ts, vs


def local_rip(board, net, seen, reach=1.4):
    """(segments to cut, the two ends to rejoin) for one net near a pocket.

    Ripping a WHOLE net is often refusable: `reroute_net` has to put all of it
    back, and for a 30-segment chain across the board that frequently fails,
    so the rip is rolled back and the pad stays unconnected. Most of the time
    the net only needs to bulge around the pocket by a fraction of a
    millimetre. Cutting the few segments that pass close by leaves exactly two
    loose ends, and rejoining those is one ordinary A* call over a board that
    now has the escape route in it.

    Returns None when the cut does not leave exactly two ends - a fork, or a
    piece that falls off entirely - because that is no longer a repair this
    can reason about.
    """
    if not seen:
        return None
    ii = [i for _l, i, _j in seen]
    jj = [j for _l, _i, j in seen]
    r = reach / GRID
    lo_i, hi_i, lo_j, hi_j = min(ii) - r, max(ii) + r, min(jj) - r, max(jj) + r
    cut, keep = set(), []
    for k, (n, l, x1, y1, x2, y2, w, span) in enumerate(board.tracks):
        if n != net or span is None or l not in LAYERS:
            continue
        a, b = board.cell(x1, y1), board.cell(x2, y2)
        inside = (lo_i <= (a[0] + b[0]) / 2.0 <= hi_i
                  and lo_j <= (a[1] + b[1]) / 2.0 <= hi_j)
        if inside:
            cut.add(k)
        else:
            keep.append(k)
    if not cut:
        return None
    # Endpoints of the cut piece that the REST of the net still owns: those
    # are what has to be joined back up.
    ends = []
    for k in cut:
        _n, l, x1, y1, x2, y2, w, _s = board.tracks[k]
        for x, y in ((x1, y1), (x2, y2)):
            held = any(SG.point_seg_dist(x, y, board.tracks[j][2],
                                         board.tracks[j][3],
                                         board.tracks[j][4],
                                         board.tracks[j][5])
                       <= board.tracks[j][6] / 2.0 + 0.02 for j in keep)
            if not held:
                held = any(vn == net and math.hypot(x - vx, y - vy) <= vs / 2.0
                           for vn, vx, vy, vs, _vp in board.vias)
            if not held:
                held = any(pn == net and SG.point_rect_dist(
                    x, y, px, py, pw, ph, math.degrees(pa)) <= w / 2.0
                    for pn, on, px, py, pw, ph, pa in board.pads)
            if held:
                ends.append((round(x, 3), round(y, 3)))
    ends = sorted(set(ends))
    if len(ends) != 2:
        return None
    return cut, ends


def rejoin(board, net, drop, drop_vias, ends):
    """Reconnect the two loose ends a local rip left. Blocks, or None."""
    free, via_ok, own = board.build(net, drop=drop, drop_vias=drop_vias)
    (x1, y1), (x2, y2) = ends
    starts = flood_own(own, free, [board.cell(x1, y1)])
    goals = set(flood_own(own, free, [board.cell(x2, y2)]))
    if not starts or not goals:
        return None
    if set(starts) & goals:
        return []                       # the cut did not actually separate them
    path = astar(free, via_ok, starts, goals)
    if not path:
        return None
    blocks = emit(board, net, path)
    register(board, net, path, None)
    block_vias(board, via_ok, path)
    return blocks


def snap_ends(path, s, g):
    """Make a path physically start and end on the points it was asked for.

    A* starts and stops on ANY cell of an endpoint's own copper, and for a
    RoundRect pad `own` includes the bounding-box corners, which hold no
    copper at all. A track that stops on one is dangling to KiCad: that is
    how L1.2 came back "joined" by the Prim and open in DRC, 0.7 mm from its
    pad, with a 0.49 mm stub pointing at a rounded-off corner. Extending to
    the DRC-named point - the pad centre - costs one segment over the pad's
    own copper, which cannot conflict with anything the corner cell was
    already clear of.
    """
    if s and (path[0][1], path[0][2]) != (s[0], s[1]):
        path = [(path[0][0], s[0], s[1])] + path
    if g and (path[-1][1], path[-1][2]) != (g[0], g[1]):
        path = path + [(path[-1][0], g[0], g[1])]
    return path


def reroute_net(board, net, drop, drop_vias):
    """Re-connect every pad on `net` from scratch. Blocks, or None if stuck.

    Ripping one SEGMENT out of a wall achieves nothing - the wall is a
    polyline and its shortest pieces are 0.1 mm bends, so all twelve tried for
    each pad failed. The unit that matters is the whole net: pull all of its
    copper, and the corridor it was occupying opens up properly. Putting it
    back is then an ordinary routing problem with the whole board available,
    and B.Cu is nearly empty, so the net almost always comes back on a
    different layer rather than in the way.

    Pads are joined in nearest-first order from the first one, which is a
    plain Prim tree - enough for the 2-4 pad nets that get ripped here.
    """
    pads = [(x, y) for n, on, x, y, w, h, a in board.pads if n == net]
    if len(pads) < 2:
        return [], 0
    free, via_ok, own = board.build(net, drop=drop, drop_vias=drop_vias)
    cells = [board.cell(x, y) for x, y in pads]
    joined = set(flood_own(own, free, [cells[0]]))
    if not joined:
        return None, len(cells) - 1
    # Returns (blocks, pads it could NOT join). It used to return None on the
    # first failure, which means "the net must come back complete" - and a
    # net that was already open before the rip can never satisfy that. VCORE
    # was one pair open; the VREG_LX corridor clear ripped it, routed VREG_LX,
    # then rolled everything back because VCORE would not come back to a
    # state it was never in. The caller compares the count against what the
    # net had before (prior_open) and decides.
    blocks, failed = [], 0
    for target in cells[1:]:
        goals = set(flood_own(own, free, [target]))
        if not goals:
            failed += 1
            continue
        if joined & goals:
            joined |= goals
            continue
        path = astar(free, via_ok, list(joined), goals)
        if not path:
            failed += 1
            continue
        # The target is a pad centre; the very first path also leaves from
        # pads[0], whose flood is all `joined` holds at that point.
        path = snap_ends(path, cells[0] if not blocks else None, target)
        blocks += emit(board, net, path)
        register(board, net, path, None)
        block_vias(board, via_ok, path)
        for node in path:
            own[node[0]][node[1], node[2]] = True
        joined |= goals
        joined |= set(flood_own(own, free, [(i, j) for _l, i, j in path]))
    return blocks, failed


def mark_connected(own, free, connected, path):
    """Fold a finished route into the net's copper, island and all.

    Marking only the path's own cells was not enough. A route that lands on an
    already-stitched island connects that ENTIRE island to the plane, but the
    next anchor sitting on the same island a millimetre away saw only the thin
    line of cells the path happened to cross, decided it was still unconnected,
    and searched again. Re-flooding through `own` after the merge records what
    actually just became true.
    """
    for node in path:
        own[node[0]][node[1], node[2]] = True
    for l, i, j in flood_own(own, free, [(i, j) for _l, i, j in path]):
        connected[i, j] = True


def flood_own(own, free, seeds):
    """Every cell reachable from `seeds` through this net's OWN copper.

    A pad centre is a poor place to start a search: at 0.65 mm pitch the
    neighbouring pins' clearance haloes can seal it in completely, and A*
    reports "no route" for a pin whose own pad extends 1.5 mm into open space.
    Starting from the WHOLE island - every cell of its pads and its existing
    tracks - gives the router all the escape points the copper really has.
    """
    h, w = own[LAYERS[0]].shape
    seen = set()
    stack = []
    for l in LAYERS:
        for (i, j) in seeds:
            if 0 <= i < h and 0 <= j < w and own[l][i, j]:
                stack.append((l, i, j))
                seen.add((l, i, j))
    while stack:
        l, i, j = stack.pop()
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)):
            ni, nj = i + di, j + dj
            if not (0 <= ni < h and 0 <= nj < w):
                continue
            if (l, ni, nj) in seen or not own[l][ni, nj]:
                continue
            seen.add((l, ni, nj))
            stack.append((l, ni, nj))
    return [n for n in seen if free[n[0]][n[1], n[2]]]


def register(board, net, path, via_xy):
    """Add what was just routed to the board model.

    Without this, masks built for the NEXT net do not know about copper this
    run has already placed - which is how a +3.3V track ended up shorted to a
    GND via that had been dropped minutes earlier in the same run.
    """
    for a, b in zip(path, path[1:]):
        if a[0] != b[0]:
            # A layer change emits a via, and the model has to know: without
            # it a later route can drop its own via on the same spot.
            vx, vy = board.world(b[1], b[2])
            board.vias.append((net, vx, vy, VIA_SIZE, None))
            continue
        ax, ay = board.world(a[1], a[2])
        bx, by = board.world(b[1], b[2])
        board.tracks.append((net, a[0], ax, ay, bx, by, TRACK_W, None))
    if via_xy:
        board.vias.append((net, via_xy[0], via_xy[1], VIA_SIZE, None))


def main():
    apply = "--apply" in sys.argv
    text = io.open(BOARD, encoding="utf-8").read()
    board = Board(text)
    print("  grid %d x %d cells at %.2f mm" % (board.w, board.h, GRID))

    # How many pairs each net had open BEFORE this pass. A ripped net that
    # comes back no worse than this has come back; demanding better is what
    # made every corridor clear that touched an already-open net roll back.
    prior_open = {}
    import bridge_nets as _BN          # BN itself is bound later in main()
    for n, *_rest in _BN.pairs(skip=()):
        prior_open[n] = prior_open.get(n, 0) + 1

    def came_back(cand, back, nf):
        """Accept a re-route unless it left the net worse than it was."""
        return back is not None and nf <= prior_open.get(cand, 0)

    all_blocks, failed = [], []
    plane_done = p2p_done = joined = 0
    ripped, ripped_vias, ripped_nets = set(), set(), set()
    islands = []

    # ---- the plane nets: every island back onto its own pour ----------------
    #
    # Masks are built ONCE per net, not per island. Newly routed copper of the
    # SAME net is not an obstacle to the next route - two islands merging is a
    # bonus, not a fault. Only new VIA positions are tracked, because two
    # holes cannot overlap whatever their net.
    for net, fresh_via in PLANE_NETS:
        free, via_ok, own = board.build(net)

        # Which copper already reaches the rest of the net. DRC names every
        # item that does NOT, so everything else does - that is the honest
        # seed, and it is KiCad's own answer rather than a second opinion.
        connected = np.zeros((board.h, board.w), dtype=bool)
        anchors = [board.cell(x, y)
                   for x, y, _r, _p in SG.drc_unconnected(BOARD, net)]
        stranded = set(flood_own(own, free, anchors))
        for l in LAYERS:
            for i, j in zip(*np.nonzero(own[l])):
                if (l, i, j) not in stranded:
                    connected[i, j] = True
        print("  %s: %d via site(s), %d cell(s) already on the pour"
              % (net, int(via_ok.sum()) if fresh_via else 0,
                 int(connected.sum())))
        # A fresh via is only as good as the pour it lands in. In2.Cu's +3.3V
        # pour gets cut into fragments by whatever is routed ON that layer, so
        # on a routed board a via there can reach a piece that goes nowhere -
        # hence fresh_via is False for +3.3V. GND's In1.Cu pour is never
        # routed on, so it is always one polygon.
        #
        # The exception is a board with nothing connected yet. Then the pour
        # is uncut BY DEFINITION - there are no segments on In2.Cu to cut it -
        # and refusing a fresh via leaves the net with no goal at all: on the
        # first pass over a freshly placed board this scored +3.3V 0 of 24
        # while GND managed 85 of 85, purely because GND was allowed a via and
        # +3.3V was not.
        if not fresh_via and connected.any():
            via_ok = np.zeros_like(via_ok)
        elif not fresh_via:
            print("       (pour is uncut - allowing fresh vias this pass)")
        islands = plane_targets(board, net)
        before = plane_done
        for label, cells in islands:
            starts = flood_own(own, free, cells)
            if not starts:
                failed.append("%s %s (pad cell blocked)" % (net, label))
                continue

            def reachable(n):
                return via_ok[n[1], n[2]] or connected[n[1], n[2]]

            def commit(path):
                """Emit the route, plus a via if it did not land on the pour."""
                blocks = emit(board, net, path)
                l, i, j = path[-1]
                x, y = board.world(i, j)
                NL, TAB = chr(10), chr(9)
                via_xy = None
                if not connected[i, j]:
                    blocks.append(NL.join([
                        TAB + "(via",
                        TAB * 2 + "(at %g %g)" % (round(x, 4), round(y, 4)),
                        TAB * 2 + "(size %g)" % VIA_SIZE,
                        TAB * 2 + "(drill %g)" % VIA_DRILL,
                        TAB * 2 + '(layers "F.Cu" "B.Cu")',
                        TAB * 2 + '(net "%s")' % net,
                        TAB * 2 + '(uuid "%s")' % _uuid.uuid4(),
                        TAB + ")"]))
                    # keep later vias off this one
                    for di, dj in disc((VIA_SIZE + RULE) / GRID + 1):
                        if 0 <= i + di < board.h and 0 <= j + dj < board.w:
                            via_ok[i + di, j + dj] = False
                    via_xy = (x, y)
                block_vias(board, via_ok, path)
                mark_connected(own, free, connected, path)
                register(board, net, path, via_xy)
                return blocks

            path = astar(free, via_ok, starts, None, goal_test=reachable)
            if path:
                all_blocks += commit(path)
                plane_done += 1
                continue

            # Walled in. Pull a whole neighbouring net out of the way, let this
            # pad out, and put that net back somewhere else.
            #
            # ORDER MATTERS, and getting it wrong cost 53 shorts on the first
            # attempt: the escape route has to be COMMITTED to the board model
            # before the ripped net is re-routed, or the two are each planned
            # against a board that does not contain the other and they cross. So
            # commit, then re-route, then roll back both if the re-route fails -
            # a rip that cannot be put back must leave no trace.
            seen, _ = pocket(board, free, via_ok, own, cells)
            done = False
            for cand in rip_nets(board, net, seen)[:RIP_TRIES]:
                if cand in ripped_nets:
                    continue
                # Cheapest cut first: just the span passing the pocket, whose
                # two loose ends are one A* call to rejoin. Only if that leaves
                # a shape this cannot reason about does the whole net go.
                plans = []
                loc = local_rip(board, cand, seen)
                if loc:
                    plans.append(("locally", loc[0], set(), loc[1]))
                ts, vs = net_copper(board, cand)
                plans.append(("entirely", ts, vs, None))
                for how, dts, dvs, ends in plans:
                    d_t, d_v = ripped | dts, ripped_vias | dvs
                    f2, v2, o2 = board.build(net, drop=d_t, drop_vias=d_v)
                    p2 = astar(f2, v2, flood_own(o2, f2, cells), None,
                               goal_test=lambda n: v2[n[1], n[2]]
                               or connected[n[1], n[2]])
                    if not p2:
                        continue
                    keep = (free, via_ok, own, connected.copy(),
                            len(board.tracks), len(board.vias), len(all_blocks))
                    free, via_ok, own = f2, v2, o2
                    all_blocks += commit(p2)
                    if ends:
                        back = rejoin(board, cand, d_t, d_v, ends)
                    else:
                        back, nf = reroute_net(board, cand, d_t, d_v)
                        if not came_back(cand, back, nf):
                            back = None
                    if back is None:
                        free, via_ok, own, connected = keep[:4]
                        del board.tracks[keep[4]:]
                        del board.vias[keep[5]:]
                        del all_blocks[keep[6]:]
                        continue
                    ripped_nets.add(cand)
                    ripped, ripped_vias = d_t, d_v
                    all_blocks += back
                    print("  rip   %-12s %d segment(s) pulled %s and re-routed,"
                          " to let %s reach the plane"
                          % (cand, len(dts), how, label))
                    done = True
                    break
                if done:
                    break
            if done:
                plane_done += 1
            else:
                failed.append("%s %s (no route)" % (net, label))
        print("  %s: %d of %d island(s) routed"
              % (net, plane_done - before, len(islands)))


    # ---- the point-to-point gaps -------------------------------------------
    #
    # Anything ripped up above is put back here, ahead of the gaps DRC named,
    # because a loose end this run created is a regression and a gap that was
    # already there is not. `drop` keeps the ripped copper out of every mask
    # from now on - without it the re-route would be told to avoid the very
    # segment it is replacing.
    # The pour nets are NOT skipped here any more. Routing an island to its
    # plane is the right first move and closes most of them, but it leaves the
    # cases where two bits of the same net need joining to each other and
    # neither can reach the pour - so the point-to-point pass runs as a
    # fallback over every pair DRC named, and simply reports "already joined"
    # for the ones the plane phase has since fixed.
    import bridge_nets as BN
    pours = {n for n, _v in PLANE_NETS}
    # Sorted by net so the masks are built once per net rather than once per
    # pair. Within one net only that net's OWN copper is added as it routes,
    # which the cached masks can absorb; crossing to a different net always
    # rebuilds, so no route is ever planned against a stale obstacle set.
    todo = sorted(BN.pairs(skip=()))
    only = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")]
    if only:
        want = set(only[0].split(","))
        todo = [tt for tt in todo if tt[0] in want]
        print("  --only: %d connection(s) on %s" % (len(todo), ", ".join(sorted(want))))
    cur, free, via_ok, own = None, None, None, None
    for net, x1, y1, x2, y2 in todo:
        if net != cur:
            cur = net
            free, via_ok, own = board.build(net, drop=ripped,
                                            drop_vias=ripped_vias)
        si, sj = board.cell(x1, y1)
        gi, gj = board.cell(x2, y2)
        # Start and finish on the net's OWN copper, not on whatever layer
        # happens to be free at that cell. Using `free` here let COL_19 start
        # on the In2.Cu plane layer directly beneath U6 pad 6 - a track on a
        # layer the pad is not on, with no via - which DRC then reported as
        # dangling and `close_gaps.py` deleted, so the two tools took turns
        # routing and deleting the same net. flood_own also hands A* every
        # escape point the existing copper has, not just the centre cell.
        starts = flood_own(own, free, [(si, sj)])
        goals = set(flood_own(own, free, [(gi, gj)]))
        label = "%-12s (%.2f,%.2f)->(%.2f,%.2f)" % (net, x1, y1, x2, y2)
        if not starts or not goals:
            failed.append(label + " (endpoint cell blocked)")
            continue
        if set(starts) & goals:
            joined += 1
            continue
        path = astar(free, via_ok, starts, goals)
        if path:
            path = snap_ends(path, (si, sj), (gi, gj))
            all_blocks += emit(board, net, path)
            register(board, net, path, None)
            block_vias(board, via_ok, path)
            for l, i, j in path:
                own[l][i, j] = True
            p2p_done += 1
            continue

        if net == "GND":   # +3.3V may rip; only GND floods the board
            # The plane pass already had its chance to move things for this
            # net. Re-running rip-up here means flooding the whole board from
            # a GND endpoint, once per pair, which is minutes of work for a
            # net whose real answer was "reach the pour" and did not.
            failed.append(label + " (no route)")
            continue

        # Same rip-up as the GND phase, same commit-then-re-route order. The
        # pocket is taken from whichever end is more boxed in, since that is
        # the end that needs the room.
        ends = sorted(((si, sj), (gi, gj)),
                      key=lambda c: len(pocket(board, free, via_ok, own,
                                               [c])[0]))
        seen, _ = pocket(board, free, via_ok, own, [ends[0]])
        done = False
        for cand in rip_nets(board, net, seen)[:RIP_TRIES]:
            if cand in ripped_nets or cand == net:
                continue
            plans = []
            loc = local_rip(board, cand, seen)
            if loc:
                plans.append(("locally", loc[0], set(), loc[1]))
            ts, vs = net_copper(board, cand)
            plans.append(("entirely", ts, vs, None))
            for how, dts, dvs, ends in plans:
                d_t, d_v = ripped | dts, ripped_vias | dvs
                f2, v2, o2 = board.build(net, drop=d_t, drop_vias=d_v)
                s2 = flood_own(o2, f2, [(si, sj)])
                g2 = set(flood_own(o2, f2, [(gi, gj)]))
                p2 = astar(f2, v2, s2, g2) if s2 and g2 else None
                if not p2:
                    continue
                p2 = snap_ends(p2, (si, sj), (gi, gj))
                keep = len(board.tracks), len(board.vias), len(all_blocks)
                all_blocks += emit(board, net, p2)
                register(board, net, p2, None)
                block_vias(board, v2, p2)
                if ends:
                    back = rejoin(board, cand, d_t, d_v, ends)
                else:
                    back, nf = reroute_net(board, cand, d_t, d_v)
                    if not came_back(cand, back, nf):
                        back = None
                if back is None:
                    del board.tracks[keep[0]:]
                    del board.vias[keep[1]:]
                    del all_blocks[keep[2]:]
                    continue
                ripped_nets.add(cand)
                ripped, ripped_vias = d_t, d_v
                all_blocks += back
                print("  rip   %-12s %d segment(s) pulled %s and re-routed, to"
                      " clear %s" % (cand, len(dts), how, label.strip()))
                done = True
                break
            if done:
                break

        # Corridor clear. Every rippable neighbour comes out together, the
        # stuck net goes in first while the corridor is empty, then each
        # ripped net is put back in turn with the board as it now stands -
        # reroute_net() rebuilds its masks from live state on every call, so
        # the second re-route sees the first one's copper. If any of them
        # cannot come back the whole thing is rolled back; a re-route that
        # strands a net that WAS connected would be a regression dressed up
        # as progress.
        if not done:
            # Which nets to pull is decided by the path that WOULD exist, not
            # by what sits near one endpoint: drop every rippable net, find
            # that path, and name the nets standing on it. If one of those
            # cannot be put back once the stuck net has taken the corridor,
            # PROTECT it and try again - the stuck net then has to route
            # around it and the rip set shrinks by one, so this converges on
            # the largest set for which everyone comes back. The first run of
            # this fallback found a path for all nine stuck connections and
            # rolled every one of them back on exactly that failure.
            prot = lambda n: any(n.startswith(p) for p in NEVER_RIP)
            exclude = set()
            t0 = time.time()
            for _attempt in range(MULTI_RIP):
                if time.time() - t0 > CLEAR_BUDGET:
                    print("  slow  %-12s corridor clear past its %ds budget "
                          "after %d attempt(s) - moving on"
                          % (net, CLEAR_BUDGET, _attempt))
                    break
                all_t = ripped | {k for k, t in enumerate(board.tracks)
                                  if t[7] is not None and t[0] != net
                                  and not prot(t[0]) and t[0] not in exclude}
                all_v = ripped_vias | {k for k, v in enumerate(board.vias)
                                       if v[4] is not None and v[0] != net
                                       and not prot(v[0]) and v[0] not in exclude}
                f0, v0, o0 = board.build(net, drop=all_t, drop_vias=all_v)
                s0 = flood_own(o0, f0, [(si, sj)])
                g0 = set(flood_own(o0, f0, [(gi, gj)]))
                p0 = astar(f0, v0, s0, g0) if s0 and g0 else None
                if not p0:
                    if not exclude:
                        print("  hard  %-12s no path even with every rippable "
                              "net gone - placement, not routing" % net)
                    else:
                        print("  undo  %-12s no path once %s protected"
                              % (net, ", ".join(sorted(exclude))))
                    break
                cands = [c for c in corridor_nets(board, net, p0)
                         if c not in ripped_nets and c != net
                         and c not in exclude][:MULTI_RIP]
                d_t, d_v = set(ripped), set(ripped_vias)
                for c in cands:
                    ts, vs = net_copper(board, c)
                    d_t |= ts
                    d_v |= vs
                f2, v2, o2 = board.build(net, drop=d_t, drop_vias=d_v)
                s2 = flood_own(o2, f2, [(si, sj)])
                g2 = set(flood_own(o2, f2, [(gi, gj)]))
                p2 = astar(f2, v2, s2, g2) if s2 and g2 else None
                if not p2:
                    print("  undo  %-12s no path with %s pulled (%d net(s))"
                          % (net, ", ".join(cands) or "nothing", len(cands)))
                    break
                p2 = snap_ends(p2, (si, sj), (gi, gj))
                keep = len(board.tracks), len(board.vias), len(all_blocks)
                all_blocks += emit(board, net, p2)
                register(board, net, p2, None)
                block_vias(board, v2, p2)
                # Hardest first: the net with the most copper gets the board
                # while there is still room in it. Cheapest-first is the right
                # order for CHOOSING what to rip, not for putting it back.
                backs, bad = [], None
                for c in reversed(cands):
                    back, nf = reroute_net(board, c, d_t, d_v)
                    if not came_back(c, back, nf):
                        bad = c
                        break
                    backs += back
                if bad is None:
                    ripped_nets.update(cands)
                    ripped, ripped_vias = d_t, d_v
                    all_blocks += backs
                    print("  rip   %d net(s) pulled together (%s) and "
                          "re-routed, to clear %s"
                          % (len(cands), ", ".join(cands), label.strip()))
                    done = True
                    break
                print("  undo  %-12s cleared %d net(s) but %s could not be "
                      "re-routed - protecting it and retrying"
                      % (net, len(cands), bad))
                del board.tracks[keep[0]:]
                del board.vias[keep[1]:]
                del all_blocks[keep[2]:]
                exclude.add(bad)
        if done:
            p2p_done += 1
        else:
            failed.append(label + " (no route)")
    print("  point-to-point: %d of %d routed, %d already joined by "
          "the plane pass" % (p2p_done, len(todo) - joined, joined))

    print()
    print("  %d connection(s) routed, %d net(s) ripped up and re-routed "
          "(%s), %d failed"
          % (plane_done + p2p_done, len(ripped_nets),
             ", ".join(sorted(ripped_nets)) or "none", len(failed)))
    for f in failed:
        print("    FAIL " + f)

    if not apply:
        print(chr(10) + "dry run. re-run with --apply to write it.")
        return 0

    # Deletions first, highest offset first so earlier spans stay valid.
    spans = [board.tracks[k][7] for k in ripped]
    spans += [board.vias[k][4] for k in ripped_vias]
    for st, end in sorted(spans, reverse=True):
        text = text[:st - 1] + text[end:]     # st-1 eats the leading newline
    tail = text.rindex(")")
    text = text[:tail] + chr(10).join(all_blocks) + chr(10) + text[tail:]
    shutil.copy(BOARD, BOARD + ".bak")
    io.open(BOARD, "w", encoding="utf-8", newline=chr(10)).write(text)
    print(chr(10) + "wrote rev3.kicad_pcb (previous version saved as .bak)")
    return P.check()


if __name__ == "__main__":
    sys.exit(main())
