"""review_rev4.py in.kicad_pcb out.kicad_pcb [--stages edge,vias,reroute,widen,corners] - the design-review changes.

An experienced electrical engineer looked over the routed board (21 October
2026) and made six comments. ../REVIEW.md measures each one on the board and
says which hold; this script makes the layout changes for the ones that do,
on the installed board, with the same exact-geometry model (lr) and octilinear
router (rr3) as the rest of the pipeline. Every segment and via it adds is
checked exactly before it goes on the board; finalize_rev4.py then runs KiCad's
DRC with zones refilled and the schematic parity check. In this order:

  edge     "Some of your components are pretty close to the edge." C39 (the
           0805 harness capacitor), R1, R17 and D3 had copper 0.27-0.37 mm from
           the outline: inside JLCPCB's 0.4 mm for a V-scored edge, with a mask
           web there that its +-0.2 mm routing tolerance can cut through. Each
           moves straight in, to 0.5 mm (C39, an MLCC, to 0.75 mm: flex
           cracking starts at board edges), and its ties are drawn again. The
           board's copper-to-edge rule goes from 0.22 to 0.40 mm with it, so
           the vias that sat closer than that (J4's shield tabs', R1's, one on
           +5V) move in the next stage
  vias     "Move your vias out of any component pads." 98 plane-tie vias had
           their holes in, or within 0.10 mm of, an SMD pad's mask opening:
           the board relied on epoxy-filled, capped vias (a paid option on a
           4-layer JLCPCB board) to keep solder out of them. Each moves to the
           nearest place where its hole is at least 0.10 mm (JLCPCB's minimum
           mask bridge) from every opening, preferring one where its whole
           land is, with a straight or one-bend octilinear tie; the vias are
           tented, so mask then covers them. A move that leaves any track end
           of the net hanging (another via's tie stood on one of this one's)
           is undone. Where signal copper boxes a via in, up to two signal
           nets in the way are taken up and routed again round it (the best
           place for each set of nets in the way, up to three, in turn); failing
           that, the via goes and each pad that loses its way to the plane is
           tied by a routed F.Cu track to a neighbour's via. The last few get
           a wider search, up to three nets taken up, and LAST_LOOSE (the
           debug and address lines, USB_ILIM, the damper link) may move too.
           The exposed pads' vias (U9's 7, U12's 2) stay: the RP2350 and
           TPS62162 layouts put them there
  reroute  "Some of your signal lines change size randomly" and "any high speed
           traces should be above a ground pour ... keep them on the top
           layer." Every signal net with 0.10 mm track away from the fine-pitch
           pins (freerouting's class width, which fr_finish.py's widening
           could not always undo) is taken up and routed again at 0.15 mm,
           end to end; the fast logic nets (the 595s' clock and data, the
           RS-485 transceivers' logic side) with B.Cu costed, since B.Cu runs
           over the +3.3 V plane and each F-to-B via moves their return current
           between planes. Kept only if the net is whole, has no more vias and
           is at most a third longer (plus 2 mm); otherwise put back
  widen    what is left at 0.10 mm, widened to 0.15 mm where it fits; a segment
           packed against a neighbour is nudged up to 0.075 mm aside, its
           joints sliding along the segments either side so every angle stays.
           Each run of 0.10 mm still outside the escape areas is then taken up
           on its own and its ends joined again at 0.15 mm in a small window,
           on the same terms. (Taking side-by-side nets up together - the four
           mux selects - was tried: the rip-up loop could not put back the
           column lines it moved, in half an hour.) What is left is listed by
           name in the board rule "0.15 mm outside the escape areas"
  corners  "There are right angles on your bus lines." Every bare 90-degree
           corner (two segments of one net meeting off any pad or via) is cut
           to two 45-degree bends where the exact check allows, largest
           chamfer first; the core regulator's and the ADC corner's drawn
           nets aside, and never leaving a 0.10 mm segment wholly outside the
           escape areas.

After every rip-up, trim() cuts the free track ends the rip-up loop leaves back to
the last point where something still attaches (KiCad's dangling report names whole
segments, and taking one off whole can cut the T it carries), and the result is
kept only if every net it touched is whole, with no stub left. A nudge or a chamfer
that would leave a track end hanging (another track's T on a segment that moves)
is undone. At the end, dedupe() takes off the segments that lie wholly on another of
their net (a moved via's tie drawn over an old one), where that leaves no end free,
and undangle() any segment KiCad's own connectivity still calls dangling (one doubled
along another of its net), where the net stays whole without it. Nothing else is
pruned: KiCad's DRC reports.
"""
import argparse
import collections
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import shapely
from shapely.geometry import Point, LineString, box
from shapely.ops import unary_union
import lr
import rr2
import rr3
import grp_route as grp     # not "grp": on Unix that name is the stdlib group database
import straighten_rev4 as st
import fr_finish
import preroute_rev4

K = lr.K
mm = K.ToMM
PLANE = ("GND", "+3.3V")
EDGE_NEW = 0.40          # copper to the outline's centre line: JLCPCB's V-cut minimum (routed edges: 0.20)
WEB = 0.10               # via hole to any SMD pad's mask opening: JLCPCB's minimum solder-mask bridge
EP = {("U9", "61"), ("U12", "9")}
# never taken up to make room: the planes, the drawn and pre-routed nets, the supplies, the analog chain
PROTECT = set(rr2.PROTECT) | set(preroute_rev4.FIXED) | fr_finish.SENSITIVE | {"USB_SNUB", "USB_BUS_SW"}
DRAWN = {"VREG_LX", "VREG_AVDD", "SW_NODE", "VCORE"}     # the regulator corner, as the minimal design: never chamfered
# copper to the edge each part moves to, and the direction it moves in
EDGE_PARTS = {"C39": (0.75, (1, 0)), "R1": (0.5, (-1, 0)), "R17": (0.5, (0, -1)), "D3": (0.5, (0, -1))}
# the fast logic nets: GPIO edges of a nanosecond or two
FAST = ("ROW_CLK", "ROW_DATA", "ROW_LATCH", "ROW_CLK_MCU", "ROW_LATCH_MCU", "BUS_DI", "BUS_RO", "BUS_DE",
        "SYNC_OUT", "SYNC_DE", "SR_CHAIN_1", "SR_CHAIN_2", "SR_CHAIN_3")


def plen(p):
    return sum(math.dist(a, b) for a, b in zip(p, p[1:]))


def octi_paths(f, q):
    """The one- or two-segment paths from f to q at 0 / 45 / 90 degrees."""
    dx, dy = q[0] - f[0], q[1] - f[1]
    if abs(dx) < 1e-6 or abs(dy) < 1e-6 or abs(abs(dx) - abs(dy)) < 1e-6:
        return [[f, q]]
    d = min(abs(dx), abs(dy))
    mx, my = math.copysign(d, dx), math.copysign(d, dy)
    return [[f, (f[0] + mx, f[1] + my), q], [f, (q[0] - mx, q[1] - my), q]]


def path_ok(m, net, l, p, w, ign):
    return all(math.dist(a, b) < 1e-6 or not m.check_track(net, l, a, b, w, ign) for a, b in zip(p, p[1:]))


def segs_of(p, l):
    return [(l, a, b) for a, b in zip(p, p[1:]) if math.dist(a, b) > 1e-6]


# ---------------------------------------------------------------- pads and openings
def openings(m):
    """Every SMD copper pad's solder-mask opening on F: (ref, num, net, polygon, centre)."""
    out = []
    for f in m.b.GetFootprints():
        for p in f.Pads():
            if p.GetAttribute() != K.PAD_ATTRIB_SMD or not p.IsOnLayer(K.F_Mask) or not p.IsOnLayer(K.F_Cu):
                continue
            g = lr.polyset(p.GetEffectivePolygon(K.F_Cu, K.ERROR_OUTSIDE))
            e = mm(p.GetSolderMaskExpansion(K.F_Mask))
            out.append((f.GetReference(), p.GetNumber(), p.GetNetname(), g.buffer(e) if e > 0 else g,
                        (mm(p.GetPosition().x), mm(p.GetPosition().y))))
    return out


class Openings:
    def __init__(self, m):
        self.ops = openings(m)
        self.tree = shapely.STRtree([o[3] for o in self.ops])

    def near(self, g, d):
        return [self.ops[k] for k in self.tree.query(g.buffer(d))]

    def web(self, xy, drill):
        """distance from a hole at xy to the nearest opening other than an exposed pad's, and that opening"""
        h = Point(xy).buffer(drill / 2, 32)
        best = (9.0, None)
        for o in self.near(h, 1.0):
            if (o[0], o[1]) in EP:
                continue
            d = o[3].distance(h)
            if d < best[0]:
                best = (d, o)
        return best

    def in_ep(self, xy, drill):
        h = Point(xy).buffer(drill / 2, 32)
        return any((o[0], o[1]) in EP and o[3].intersects(h) for o in self.near(h, 0.0))


def pad_clear(m, it):
    """A pad's clearance to every other net's copper and hole, and to the edge (as lr.check_track does)."""
    g, bad = it["geom"], []
    for l in it["lay"]:
        if l not in m.by_layer:
            continue
        its, tree = m.by_layer[l]
        for k in tree.query(g.buffer(0.65)):
            o = its[k]
            if o["uuid"] == it["uuid"] or (o["net"] and o["net"] == it["net"]):
                continue
            need = max(lr.FINE if (it["in_area"] and o["in_area"]) else lr.RULE, o.get("clr", 0.0), it.get("clr", 0.0))
            d = g.distance(o["geom"])
            if d < need - 1e-4:
                bad.append((round(d, 4), need, o["net"], o["ref"]))
    hs, ht = m.holes
    for k in ht.query(g.buffer(lr.HOLE + 0.02)):
        o = hs[k]
        if o["uuid"] != it["uuid"] and not (o["net"] and o["net"] == it["net"]) and g.distance(o["hole"]) < lr.HOLE - 1e-4:
            bad.append((round(g.distance(o["hole"]), 4), lr.HOLE, o["net"], o["ref"] + "(hole)"))
    x0, y0, x1, y1 = m.edge
    bx = g.bounds
    de = min(bx[0] - x0, bx[1] - y0, x1 - bx[2], y1 - bx[3])
    if de < lr.EDGE - 1e-4:
        bad.append((round(de, 4), lr.EDGE, "", "edge"))
    return bad


def courtyards(m):
    out = {}
    for f in m.b.GetFootprints():
        cy = f.GetCourtyard(K.F_CrtYd)
        if cy.OutlineCount():
            out[f.GetReference()] = lr.polyset(cy)
    return out


# ---------------------------------------------------------------- plane-tie vias
def pad_under(m, net, p, tol=0.02):
    """the net's pad whose copper p lies on, or None"""
    for it in m.near(Point(p).buffer(tol), 0.0, kinds=("pad",)):
        if it["net"] == net and it["geom"] is not None and it["geom"].distance(Point(p)) < tol:
            return it
    return None


def attached(m, v):
    """Tracks of v's net that touch its land: [(track, far end)], far end None when the
    track runs through the via rather than ending in it - or when it starts on the pad
    the via sits in and goes somewhere else: that is the pad's track (a capacitor's to
    its IC pin), not the via's tie, and it stays."""
    r = v["size"] / 2 + 0.005
    out = []
    for it in m.near(Point(v["xy"]).buffer(r), 0.0, kinds=("track",)):
        if it["net"] != v["net"]:
            continue
        da, dc = math.dist(it["a"], v["xy"]), math.dist(it["c"], v["xy"])
        far = (it["c"] if da < dc else it["a"]) if min(da, dc) <= r else None
        if far is not None:
            pn, pf = pad_under(m, v["net"], it["a"] if far is it["c"] else it["c"]), pad_under(m, v["net"], far)
            if pn is not None and (pf is None or pf["uuid"] != pn["uuid"]):
                far = None
        out.append((it, far))
    return out


def sources(m, v, O):
    """Where the via's ties must reach once it moves: (layer, point, width, through-track or None).
    A track ending in the via is redrawn from its far end; a track running through it is
    met by a new tie from the point on it nearest the via's new place; with no track at
    all (the via joined its pad by overlapping it) a tie comes from the pad's centre."""
    out = []
    for t, far in attached(m, v):
        l = next(iter(t["lay"]))
        out.append((l, far, t["w"], None if far is not None else t))
    if not out:
        h = Point(v["xy"]).buffer(v["drill"] / 2, 32)
        for o in O.near(h, 0.0):
            if o[2] == v["net"] and o[3].intersects(h):
                out.append(("F", o[4], None, None))
    return out


def real(m, net, srcs, ign):
    """The sources something still holds on to once the via and its ties go: a pad, a
    via or a track of the net there that is not being taken off. A tie whose far end
    only met another of the via's own ties was a stub, and needs no successor."""
    keep = []
    for l, far, w, thru in srcs:
        if thru is not None:
            keep.append((l, far, w, thru))
            continue
        pt = Point(far)
        its, tree = m.by_layer[l]
        for k in tree.query(pt.buffer(0.06)):
            o = its[k]
            if o["uuid"] in ign or o["net"] != net:
                continue
            if o["geom"].distance(pt) < (0.02 if o["kind"] == "track" else 0.05):
                keep.append((l, far, w, thru))
                break
    return keep


def loose_ends(m, nets, region=None):
    """(track, end) for every track end of the nets joined to nothing (grp_route._attached's
    test - a pad or via under the end, another track's end or body - on the model's spatial
    index), within region if one is given."""
    nets = set(nets)
    for l in lr.LAYERS:
        its, tree = m.by_layer[l]
        for it in its:
            if it["kind"] != "track" or it["net"] not in nets or (region is not None and not it["geom"].intersects(region)):
                continue
            for p in (it["a"], it["c"]):
                near = [its[k] for k in tree.query(Point(p).buffer(0.06)) if its[k]["net"] == it["net"]]
                if not grp._attached(it, p, near, 0.02):
                    yield it, p


def free_ends(m, nets, region=None):
    return {(it["net"], round(p[0], 3), round(p[1], 3)) for it, p in loose_ends(m, nets, region)}


def trim(m, nets, rounds=60):
    """Every free track end of the nets cut back to the farthest point along its track where
    something of the net still attaches - another track's end, a via's or a pad's centre on
    it, the T a rip-up leaves - or the track taken off if nothing does. KiCad's own dangling
    report names whole segments, and taking one off whole can cut the T it carries."""
    nets = set(nets)
    for _ in range(rounds):
        # the track whose end is free, not just any track ending there: a layer change
        # puts another layer's track end on the same point
        ends = sorted(((it["net"], round(p[0], 3), round(p[1], 3), k, it) for it, p in loose_ends(m, nets)
                       for k in (0 if p is it["a"] else 1,)), key=lambda e: e[:4])
        if not ends:
            return True
        net, _fx, _fy, k, tr = ends[0]
        free, keep = (tr["a"], tr["c"]) if k == 0 else (tr["c"], tr["a"])
        line = LineString([keep, free])
        best = None
        for o in m.near(line.buffer(tr["w"] / 2), 0.0, layers=list(tr["lay"])):
            if o is tr or o["net"] != net:
                continue
            for q in ([o["a"], o["c"]] if o["kind"] == "track" else [o["xy"]]):
                if line.distance(Point(q)) < tr["w"] / 2 - 1e-4:
                    s = line.project(Point(q))
                    if s > 1e-3 and (best is None or s > best):
                        best = s
        l = next(iter(tr["lay"]))
        m.remove([tr["uuid"]])
        if best is not None and best < line.length - 1e-4:
            e = line.interpolate(best)
            m.add(net, [(l, keep, (round(float(e.x), 4), round(float(e.y), 4)))], [], tr["w"])
        m.index()
    return not free_ends(m, nets)


def whole(m, nets):
    return all(len(rr2.comps(m, n)) <= 1 for n in nets if n not in PLANE)


def dedupe(m, log):
    """Track segments lying wholly on the copper of another, at least as wide, of their own
    net and layer - a moved via's tie drawn over an old one, a rip-up's join beside the
    track it joins. Each goes if no track end comes free and the net does not come apart."""
    gone = 0
    for net in sorted({it["net"] for it in m.items if it["kind"] == "track"}):
        tried = set()
        while True:
            ts = [it for it in m.items if it["kind"] == "track" and it["net"] == net]
            t = next((t for t in ts if (t["a"], t["c"]) not in tried and any(
                o is not t and o["lay"] == t["lay"] and o["w"] >= t["w"] - 1e-4 and
                o["geom"].buffer(1e-3).contains(t["geom"]) for o in ts)), None)
            if t is None:
                break
            tried.add((t["a"], t["c"]))
            n0, e0, snap = len(rr2.comps(m, net)), free_ends(m, [net]), st.snapshot([t])
            m.remove([t["uuid"]])
            m.index()
            if len(rr2.comps(m, net)) <= n0 and not (free_ends(m, [net]) - e0):
                gone += 1
            else:
                st.restore(m, net, snap)
    log("dedupe: %d track segment(s) lying on another of their net taken off" % gone)


def kicad_dangling(m):
    """[(net, end)] of the track ends KiCad's DRC reports as dangling, by KiCad's own
    connectivity: tracks ending in pads aside, as the DRC has it. KiCad credits a track
    that touches both ends of a short segment to the nearer end only, so a segment
    doubled along another of its net can read as dangling where free_ends sees it joined."""
    m.b.BuildConnectivity()
    c = m.b.GetConnectivity()
    out = []
    for t in m.b.GetTracks():
        p = K.VECTOR2I(0, 0)
        if t.Type() == K.PCB_TRACE_T and c.TestTrackEndpointDangling(t, True, p):
            out.append((t.GetNetname(), (round(mm(p.x), 4), round(mm(p.y), 4))))
    return out


def undangle(m, log):
    """Each track KiCad calls dangling taken off, and what that leaves hanging trimmed back,
    where the net stays whole without it; otherwise the net is put back and it is reported."""
    gone, left = 0, []
    for _ in range(40):
        bad = [d for d in kicad_dangling(m) if d not in left]
        if not bad:
            break
        net, p = bad[0]
        tr = min((it for it in m.items if it["kind"] == "track" and it["net"] == net),
                 key=lambda it: min(math.dist(it["a"], p), math.dist(it["c"], p)))
        n0, snaps = len(rr2.comps(m, net)), snapshot_all(m)
        m.remove([tr["uuid"]])
        m.index()
        trim(m, [net])
        if len(rr2.comps(m, net)) <= n0 and (net, p) not in kicad_dangling(m):
            gone += 1
        else:
            restore_nets(m, [net], snaps)
            left.append((net, p))
    log("undangle: %d segment(s) KiCad calls dangling taken off%s" % (
        gone, "; left: %s" % left if left else ""))


def pieces(m, net, srcs, own):
    """For each source, the piece of the net it lands on once the via and its own ties go
    (connected copper, KiCad's way, as rr2.comps), so that each piece gets one tie."""
    its = [it for it in m.items if it["net"] == net and it["uuid"] not in own and rr2.geo(it) is not None]
    geoms = [rr2.geo(it) for it in its]
    tree = shapely.STRtree(geoms)
    parent = list(range(len(its)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            j = int(j)
            if j > i and its[i]["lay"] & its[j]["lay"] and rr2.joined(its[i], g, its[j], geoms[j]):
                parent[find(i)] = find(j)
    idx = {it["uuid"]: k for k, it in enumerate(its)}
    out = []
    for n, (l, far, w, thru) in enumerate(srcs):
        if thru is not None and thru["uuid"] in idx:
            out.append(find(idx[thru["uuid"]]))
            continue
        hit = [int(k) for k in tree.query(Point(far).buffer(0.02)) if l in its[int(k)]["lay"] and
               geoms[int(k)].distance(Point(far)) < 0.02]
        out.append(find(hit[0]) if hit else ("alone", n))
    return out


def tie_plan(m, net, srcs, q, ign, groups=None):
    """Octilinear ties to a via at q, one from each piece of the net (the source in it
    that ties shortest), or None. groups: pieces() of the sources; each its own without."""
    def tie(l, far, w, thru):
        if thru is not None:                      # a T onto the through-track, square to it where possible
            line = LineString([thru["a"], thru["c"]])
            s = line.interpolate(line.project(Point(q)))
            s = (round(float(s.x), 4), round(float(s.y), 4))
            for p in sorted(octi_paths(s, q), key=plen):
                if path_ok(m, net, l, p, thru["w"], ign):
                    return (l, p, thru["w"])
            return None
        for ww in ((w,) if w else (0.25, 0.2, 0.15)):
            for p in sorted(octi_paths(far, q), key=plen):
                if path_ok(m, net, l, p, ww, ign):
                    return (l, p, ww)
        return None

    def reach(src):
        l, far, w, thru = src
        if thru is not None:
            return LineString([thru["a"], thru["c"]]).distance(Point(q))
        return math.dist(far, q)
    by = collections.defaultdict(list)
    for k, src in enumerate(srcs):
        by[groups[k] if groups else k].append(src)
    plan = []
    for _g, members in by.items():
        got = None
        for src in sorted(members, key=reach):
            got = tie(*src)
            if got:
                break
        if not got:
            return None
        plan.append(got)
    return plan


def via_cells(m, v, O, win, ign, g=0.025):
    """Grid cells where v could go: clear of everything (ign aside) and WEB from every opening.
    Returns [(tier, x, y)], tier 0 when the whole land is WEB clear of every opening."""
    M = m.masks(v["net"], win, g, 0.15, ign, (v["size"], v["drill"]))
    X, Y = M["X"], M["Y"]
    near = [o[3] for o in O.near(box(*win), 1.0) if (o[0], o[1]) not in EP]
    U = unary_union(near) if near else Point(-99, -99)
    hole_bad = shapely.intersects_xy(U.buffer(WEB + v["drill"] / 2), X, Y)
    land_bad = shapely.intersects_xy(U.buffer(WEB + v["size"] / 2), X, Y)
    ok = M["via_ok"] & ~hole_bad
    ii, jj = np.nonzero(ok)
    return [(0 if not land_bad[i, j] else 1, round(float(X[i, j]), 4), round(float(Y[i, j]), 4)) for i, j in zip(ii, jj)]


def relocate(m, v, O, R=1.6, budget=600):
    """Places out of every pad opening for v, best first: yields (q, plan, tier, ign)."""
    net, (x, y) = v["net"], v["xy"]
    ign = frozenset([v["uuid"]] + [t["uuid"] for t, far in attached(m, v) if far is not None])
    srcs = real(m, net, sources(m, v, O), ign)
    if not srcs:
        return
    groups = pieces(m, net, srcs, ign)
    cells = via_cells(m, v, O, (x - R, y - R, x + R, y + R), ign)

    def cost(c):
        q = (c[1], c[2])
        best = {}
        for (_l, far, _w, thru), g in zip(srcs, groups):
            d = (LineString([thru["a"], thru["c"]]).distance(Point(q)) if thru is not None else
                 min(plen(p) for p in octi_paths(far, q)))
            best[g] = min(best.get(g, d), d)
        return (c[0], round(sum(best.values()), 3))
    cells.sort(key=cost)
    for c in cells[:budget]:
        q = (c[1], c[2])
        if m.check_via(net, q[0], q[1], v["size"], v["drill"], ign):
            continue
        plan = tie_plan(m, net, srcs, q, ign, groups)
        if plan is not None:
            yield q, plan, c[0], ign


def apply_move(m, v, q, plan, ign):
    """v to q with its ties; undone (returns False) if that leaves any track end of the
    net hanging that was not before - a tie of another via standing on one of v's."""
    region = box(min(v["xy"][0], q[0]) - 2.0, min(v["xy"][1], q[1]) - 2.0,
                 max(v["xy"][0], q[0]) + 2.0, max(v["xy"][1], q[1]) + 2.0)
    before = free_ends(m, [v["net"]], region)
    snap = st.snapshot([m.uuid[u] for u in ign if u in m.uuid])
    had = {t.m_Uuid.AsString() for t in m.b.GetTracks()}
    m.remove(list(ign))
    for l, p, w in plan:
        m.add(v["net"], segs_of(p, l), [], w)
    m.add(v["net"], [], [q], 0.15, (v["size"], v["drill"]))
    m.index()
    if free_ends(m, [v["net"]], region) - before:
        m.remove([t.m_Uuid.AsString() for t in m.b.GetTracks() if t.m_Uuid.AsString() not in had])
        m.index()
        st.restore(m, v["net"], snap)
        return False
    return True


def snapshot_all(m):
    snaps = collections.defaultdict(list)
    for it in m.items:
        if it["kind"] in ("track", "via"):
            snaps[it["net"]] += st.snapshot([it])
    return snaps


def restore_nets(m, nets, snaps):
    m.remove([it["uuid"] for it in m.items if it["kind"] in ("track", "via") and it["net"] in nets])
    m.index()
    for n in sorted(nets):
        st.restore(m, n, snaps.get(n, []))


def width_of(m, net):
    return 0.15 if net not in fr_finish.WIDTH or fr_finish.WIDTH[net] <= 0.15 else fr_finish.WIDTH[net]


def layers_of(net):
    return ("F",) if net in fr_finish.F_ONLY else ("F", "B")


# pre-routed only for the order the rip-up loop needed (preroute_rev4.py), electrically
# slow: the last pass for a boxed-in via may take these up too
LOOSE = {"SWCLK", "SWDIO", "RUN", "ADDR0", "ADDR1", "ADDR2"}
# and, for the very last vias, the short nets beside them: U14's current-limit pin
# (R27.2 sits between it and the RS-485 lanes), the hot-plug damper's link (C42.2) and
# the board's own 5 V feed to the buck beside J4.2 (30 mA; 0.5 mm only where it fitted)
# and the row excitation rail beside U4.13 (DC: the reading is ratiometric to it)
LAST_LOOSE = LOOSE | {"USB_ILIM", "USB_SNUB", "+5V", "ROW_VCC"}
# lines where a via or two more costs nothing: flags, straps, enables, reset and the LED,
# which change state a few times a second at most, and the 595s' latch, which only copies
# the shift register to the outputs - a ringing edge that latches twice latches the same
# data. Not ROW_CLK, where it would shift the chain twice, nor BOOTSEL, the flash's live
# chip select
VIA_OK = {"USB_PWR_FAULT", "USB_BUS_EN", "USB_ILIM_HI", "USB_ILIM_LOW", "USB_CC_OUT1", "USB_CC_OUT2",
          "ADDR0", "ADDR1", "ADDR2", "RUN", "STATUS", "LED_A", "ROW_LATCH"}


def no_worse(m, nets, snaps, slack=1.3):
    """the nets a rip-up routed again: no more vias, and not much longer, than they were
    (a VIA_OK line may take up to four more vias)"""
    for n in nets:
        if n in PLANE:
            continue
        old = snaps.get(n, [])
        v0, L0 = sum(1 for t in old if t[0] == "via"), sum(math.dist(t[2], t[3]) for t in old if t[0] == "track")
        new = st.net_copper(m, n)
        v1, L1 = sum(1 for it in new if it["kind"] == "via"), st.jag_of(new)[0]
        if v1 > v0 + (4 if n in VIA_OK else 0) or L1 > slack * L0 + 2.0:
            return False
    return True


def relocate_ripping(m, v, O, log, R=1.6, max_nets=2, loose=frozenset(), tries=3):
    """v boxed in by signal copper: take what is in the way up, place v, route the rest again.
    The best place for each set of nets in the way is tried in turn, fewest nets first."""
    net, (x, y) = v["net"], v["xy"]
    own = frozenset([v["uuid"]] + [t["uuid"] for t, far in attached(m, v) if far is not None])
    srcs = real(m, net, sources(m, v, O), own)
    if not srcs:
        return False
    groups = pieces(m, net, srcs, own)
    wb = box(x - R, y - R, x + R, y + R)
    soft = frozenset(it["uuid"] for it in m.items if it["kind"] in ("track", "via") and
                     (it["net"] not in PROTECT or it["net"] in loose) and it["net"] != net and it["geom"].intersects(wb))
    cells = via_cells(m, v, O, (x - R, y - R, x + R, y + R), own | soft)
    best = {}
    for c in sorted(cells, key=lambda c: (c[0], math.dist((c[1], c[2]), (x, y))))[:400]:
        q = (c[1], c[2])
        if m.check_via(net, q[0], q[1], v["size"], v["drill"], own | soft):
            continue
        plan = tie_plan(m, net, srcs, q, own | soft, groups)
        if plan is None:
            continue
        hit = in_way(m, v, q, plan, own)
        nets = frozenset(m.uuid[u]["net"] for u in hit if u in m.uuid)
        key = (len(nets), len(hit), c[0], math.dist(q, (x, y)))
        if nets not in best or key < best[nets][0]:
            best[nets] = (key, q, plan)
    order = sorted((b for b in best.values() if b[0][0] <= max_nets), key=lambda b: b[0])[:tries]
    if not order:
        log("    %s via at (%.2f, %.2f): %s" % (net, x, y, "no place within %.1f mm even with signal tracks taken up" % R
                                               if not best else "every place takes up %d nets or more" %
                                               min(b[0][0] for b in best.values())))
        return False
    for _key, q, plan in order:
        # a try undone puts everything back as new items: look the via up again
        v = next((it for it in m.items if it["kind"] == "via" and math.dist(it["xy"], (x, y)) < 1e-4), None)
        if v is None:
            return False
        if rip_and_move(m, v, q, plan, log, loose):
            return True
    return False


def in_way(m, v, q, plan, own):
    """uuids of the other nets' copper that v at q and its ties would hit"""
    net = v["net"]
    hit = {b[4] for b in m.check_via(net, q[0], q[1], v["size"], v["drill"], own)}
    for l, p, w in plan:
        for a, b in zip(p, p[1:]):
            if math.dist(a, b) > 1e-6:
                hit |= {bb[4] for bb in m.check_track(net, l, a, b, w, own)}
    return hit


def rip_and_move(m, v, q, plan, log, loose):
    """v to q with its ties, the copper in the way taken up and routed again round it;
    everything put back unless every net it touched comes out whole, unstubbed and no worse."""
    net, (x, y) = v["net"], v["xy"]
    own = frozenset([v["uuid"]] + [t["uuid"] for t, far in attached(m, v) if far is not None])
    hit = in_way(m, v, q, plan, own)
    nets = {m.uuid[u]["net"] for u in hit if u in m.uuid}
    snaps = snapshot_all(m)
    ends0 = free_ends(m, {it["net"] for it in m.items if it["kind"] == "track"})
    m.remove(list(hit))
    m.index()
    # what the cut leaves hanging goes too, back to a pad, a via or a junction, so the
    # router joins whole pieces and no stub is left beside a new T
    grp.peel(m, sorted(nets))
    why = None if apply_move(m, v, q, plan, own) else "its ties would leave a track end hanging"
    res, touched = dict(left={}, touched=[]), set(nets)
    if why is None:
        res = rr3.rrr3(m, sorted(nets), protect=(PROTECT - set(loose)) | {net}, layers_fn=layers_of,
                       width_fn=lambda n: width_of(m, n),
                       log=lambda s: None, max_iter=30 + 10 * len(nets), max_rip=3)
        touched = set(res["touched"]) | nets
        trim(m, touched | {net})
        stubs = free_ends(m, sorted(touched | {net})) - ends0
        why = ("no way round it for %s" % sorted(res["left"]) if res["left"] else
               "the reroute left a stub at %s" % sorted(stubs)[:2] if stubs else
               "a net it moved is not whole" if not whole(m, touched) else
               "the nets it moved came out longer or with more vias" if not no_worse(m, touched, snaps) else None)
    if why:
        restore_nets(m, touched | {net}, snaps)
        log("    %s via at (%.2f, %.2f): %s; put back" % (net, x, y, why))
        return False
    log("  moved %-6s via (%.2f, %.2f) -> (%.2f, %.2f)%s" % (
        net, x, y, q[0], q[1], (", %s taken up and routed again round it" % ", ".join(sorted(touched))) if hit else ""))
    return True


def plane_reach(comp):
    """a piece of a plane net reaches the plane through a via or a through-hole pad of its own"""
    return any(it["kind"] == "via" or (it["kind"] == "pad" and it["hole"] is not None) for it in comp)


def retie(m, v, O, log, R=3.0, loose=frozenset()):
    """Last resort for a via with no place out of the pads around it: take it off, and tie
    each pad that loses its way to the plane, by a routed F.Cu track, to the nearest copper
    of the net that has a via of its own - an IC's ground pin to its neighbour's via. Up to
    two signal nets in the way may be taken up and routed again (B.Cu costed)."""
    net, (x, y) = v["net"], v["xy"]
    own = [v["uuid"]] + [t["uuid"] for t, far in attached(m, v) if far is not None]
    snaps = snapshot_all(m)
    ends0 = free_ends(m, {it["net"] for it in m.items if it["kind"] == "track"})
    m.remove(own)
    m.index()
    near = box(x - 2.0, y - 2.0, x + 2.0, y + 2.0)
    cs = [c for c in rr2.comps(m, net) if any(it["geom"] is not None and it["geom"].intersects(near) for it in c)]
    orphans = [c for c in cs if not plane_reach(c) and any(it["kind"] == "pad" for it in c)]
    bpen = {"B": [(box(*m.edge), 40)]}
    ripped, ok = set(), True
    for comp in orphans:
        og = shapely.union_all([rr2.geo(it) for it in comp])
        targets = [it for c in cs if plane_reach(c) for it in c if it["kind"] in ("track", "via", "pad")
                   and rr2.geo(it).distance(og) < R]
        if not targets:
            ok = False
            break
        bx = shapely.union_all([og] + [rr2.geo(it) for it in targets]).bounds
        win = (bx[0] - 0.8, bx[1] - 0.8, bx[2] + 0.8, bx[3] + 0.8)
        start = next(it for it in comp if it["kind"] == "pad")
        r = None
        for w in (0.2, 0.15):
            r = rr3.route3(m, net, tterms(comp, w), tterms(targets, w), win, w, ("F",), align=start["xy"])
            if r and not r[2]:
                break
        if not (r and not r[2]):
            soft = frozenset(it["uuid"] for it in m.items if it["kind"] in ("track", "via") and it["net"] != net
                             and (it["net"] not in RIP_NEVER or it["net"] in loose) and it["geom"].intersects(box(*win)))
            w = 0.15
            r = rr3.route3(m, net, tterms(comp, w), tterms(targets, w), win, w, ("F",), align=start["xy"],
                           ignore=soft, soft=60)
            if not (r and not r[2]):
                ok = False
                break
            hit = rr2.conflicts(m, net, r[0], r[1], w)
            rip = [u for u in hit if u in soft]
            nets = {m.uuid[u]["net"] for u in rip}
            if len(nets) > 2:
                ok = False
                break
            m.remove(rip)
            m.index()
            grp.peel(m, sorted(nets))
            ripped |= nets
        rr2.commit(m, net, r[0], r[1], w)
    if ok and ripped:
        res = rr3.rrr3(m, sorted(ripped), protect=(RIP_NEVER - set(loose)) | {net}, layers_fn=layers_of,
                       width_fn=lambda n: ladder(m, n, snaps)[-1], log=lambda s: None, max_iter=40, max_rip=3,
                       extra_pen=bpen)
        ripped |= set(res["touched"])
        ok = not res["left"]
    if ok:
        trim(m, ripped | {net})
        ok = not (free_ends(m, ripped | {net}) - ends0) and whole(m, ripped) and no_worse(m, ripped, snaps)
    if not ok:
        restore_nets(m, ripped | {net}, snaps)
        log("    %s via at (%.2f, %.2f): no tie to the plane's copper round it; put back" % (net, x, y))
        return False
    log("  %s via at (%.2f, %.2f) taken off; %d pad group(s) tied to a neighbour's via%s" % (
        net, x, y, len(orphans), (", %s routed again" % ", ".join(sorted(ripped))) if ripped else ""))
    return True


def vias(m, log):
    O = Openings(m)
    todo = []
    for v in [it for it in m.items if it["kind"] == "via"]:
        if O.in_ep(v["xy"], v["drill"]):
            continue
        d, o = O.web(v["xy"], v["drill"])
        x0, y0, x1, y1 = m.edge
        de = min(v["xy"][0] - x0, v["xy"][1] - y0, x1 - v["xy"][0], y1 - v["xy"][1]) - v["size"] / 2
        if d < WEB - 1e-4 or de < lr.EDGE - 1e-4:
            todo.append((v["uuid"], "%s.%s" % (o[0], o[1]) if (o and d < WEB) else "edge"))
    log("vias: %d with a hole within %.2f mm of an SMD pad opening, or within %.2f mm of the edge" % (len(todo), WEB, lr.EDGE))
    tiers, boxed = collections.Counter(), []
    for u, why in todo:
        v = m.uuid.get(u)
        if v is None:
            continue
        xy, done, tried = v["xy"], None, set()
        for _ in range(25):
            # a move undone by apply_move puts the via and its ties back as new items:
            # look them up again before the next candidate
            v = next((it for it in m.items if it["kind"] == "via" and math.dist(it["xy"], xy) < 1e-4), None)
            c = next((c for c in relocate(m, v, O) if c[0] not in tried), None) if v else None
            if c is None:
                break
            q, plan, tier, ign = c
            tried.add(q)
            if apply_move(m, v, q, plan, ign):
                done = (q, plan, tier)
                break
        if done is None:
            boxed.append((xy, why))
            continue
        q, plan, tier = done
        tiers[tier] += 1
        log("  moved %-6s via %-7s (%.2f, %.2f) -> (%.2f, %.2f), %.2f mm; ties %.2f mm%s" % (
            v["net"], why, v["xy"][0], v["xy"][1], q[0], q[1], math.dist(v["xy"], q),
            sum(plen(p) for _l, p, _w in plan), "" if tier == 0 else "; hole clear, land touches the opening"))
    log("vias: %d moved (%d with the whole land clear of every opening); %d boxed in" % (
        sum(tiers.values()), tiers[0], len(boxed)))
    for xy, why in boxed:
        v = next((it for it in m.items if it["kind"] == "via" and math.dist(it["xy"], xy) < 1e-4), None)
        if v is None:
            continue
        log("  %s via %s at (%.2f, %.2f): boxed in" % (v["net"], why, v["xy"][0], v["xy"][1]))
        if not relocate_ripping(m, v, O, log) and v["net"] in PLANE:
            v = next((it for it in m.items if it["kind"] == "via" and math.dist(it["xy"], xy) < 1e-4), None)
            if v is not None:
                retie(m, v, O, log)
    O = Openings(m)
    still = [(v["net"], v["xy"]) for v in m.items if v["kind"] == "via" and not O.in_ep(v["xy"], v["drill"])
             and O.web(v["xy"], v["drill"])[0] < WEB - 1e-4]
    # the last few: further afield, up to three nets taken up, the loose pre-routed ones too;
    # again while that frees any, since one via's way out can open another's
    for _ in range(3):
        before = len(still)
        for net, xy in still:
            v = next((it for it in m.items if it["kind"] == "via" and math.dist(it["xy"], xy) < 1e-4), None)
            if v is None:
                continue
            log("  %s via at (%.2f, %.2f): last try" % (net, xy[0], xy[1]))
            if not relocate_ripping(m, v, O, log, R=2.2, max_nets=3, loose=LAST_LOOSE) and net in PLANE:
                v = next((it for it in m.items if it["kind"] == "via" and math.dist(it["xy"], xy) < 1e-4), None)
                if v is not None:
                    retie(m, v, O, log, R=4.0, loose=LAST_LOOSE)
        O = Openings(m)
        still = [(v["net"], v["xy"]) for v in m.items if v["kind"] == "via" and not O.in_ep(v["xy"], v["drill"])
                 and O.web(v["xy"], v["drill"])[0] < WEB - 1e-4]
        if not still or len(still) == before:
            break
    log("vias: %d still within %.2f mm of an opening: %s" % (len(still), WEB, still))
    return still


# ---------------------------------------------------------------- parts at the edge
def tie_tracks(m, pad):
    """Tracks of pad's net with an end inside the pad's copper."""
    return [it for it in m.items if it["kind"] == "track" and it["net"] == pad["net"] and
            (pad["geom"].distance(Point(it["a"])) < 1e-3 or pad["geom"].distance(Point(it["c"])) < 1e-3)]


def plane_vias_of(m, pad, ties):
    """the plane-net vias the pad's ties run to, or that overlap the pad"""
    out = []
    for it in m.items:
        if it["kind"] != "via" or it["net"] != pad["net"]:
            continue
        land = Point(it["xy"]).buffer(it["size"] / 2 + 0.005)
        if land.intersects(pad["geom"]) or any(land.contains(Point(t["a"])) or land.contains(Point(t["c"])) for t in ties):
            out.append(it)
    return out


def place_tie_via(m, pad, O, log, R=1.6):
    """A new plane via for a pad that has none: nearest clean place, octilinear tie from the pad's centre."""
    net = pad["net"]
    fake = dict(net=net, xy=pad["xy"], size=0.5, drill=0.3, uuid="new")
    srcs = [("F", pad["xy"], None, None)]
    x, y = pad["xy"]
    cells = via_cells(m, fake, O, (x - R, y - R, x + R, y + R), frozenset())
    cells.sort(key=lambda c: (c[0], min(plen(p) for p in octi_paths(pad["xy"], (c[1], c[2])))))
    for c in cells[:600]:
        q = (c[1], c[2])
        if m.check_via(net, q[0], q[1], 0.5, 0.3):
            continue
        plan = tie_plan(m, net, srcs, q, frozenset())
        if plan:
            for l, p, w in plan:
                m.add(net, segs_of(p, l), [], w)
            m.add(net, [], [q], 0.15, (0.5, 0.3))
            m.index()
            return q
    return None


def tterms(items, w):
    """rr3.terminals, but a track is reached anywhere inside its copper: rr3 shrinks a
    track as wide as the route to a sliver that a 0.025 mm grid can miss altogether."""
    out = []
    for it in items:
        if it["kind"] == "track":
            l = next(iter(it["lay"]))
            r = max(0.012, it["w"] / 2 - 0.012)
            g = LineString([it["a"], it["c"]]).buffer(r) if math.dist(it["a"], it["c"]) > 1e-6 else Point(it["a"]).buffer(r)
            if l in lr.LAYERS:
                out.append((l, g, None))
        else:
            out += rr3.terminals([it], w)
    return out


def join(m, net, w, layers, margin=2.0, **kw):
    """rr3.connect with tterms: the net's pieces joined nearest-first, octilinear."""
    for _ in range(20):
        cs = rr2.comps(m, net)
        if len(cs) <= 1:
            return True
        main = cs[0]
        mg = shapely.union_all([rr2.geo(it) for it in main])
        other = min(cs[1:], key=lambda c: mg.distance(shapely.union_all([rr2.geo(it) for it in c])))
        og = shapely.union_all([rr2.geo(it) for it in other])
        d0 = mg.distance(og)
        near = [it for it in main if rr2.geo(it).distance(og) <= d0 + 3.0] or main
        bx = shapely.union_all([rr2.geo(it) for it in near] + [og]).bounds
        start = next((it for it in other if it["kind"] == "pad"), None)
        r = None
        for mg_ in (margin, margin + 2.0):
            win = (bx[0] - mg_, bx[1] - mg_, bx[2] + mg_, bx[3] + mg_)
            area = (win[2] - win[0]) * (win[3] - win[1])
            for g in ((0.05, 0.025) if area > 250 else (0.025,)):
                r = rr3.route3(m, net, tterms(other, w), tterms(near, w), win, w, layers, g=g,
                               align=start["xy"] if start else None, **kw)
                if r and not r[2]:
                    break
            if r and not r[2]:
                break
        if not (r and not r[2]):
            return False
        rr2.commit(m, net, r[0], r[1], w)
    return len(rr2.comps(m, net)) <= 1


# what an edge part's move may take up and route again: not the planes, not the drawn nets
RIP_NEVER = set(PLANE) | set(preroute_rev4.FIXED) | fr_finish.SENSITIVE | DRAWN


def ladder(m, net, snaps):
    if net in fr_finish.WIDEN:
        return tuple(fr_finish.WIDEN[net]) + (0.3,)
    w = max([t[4] for t in snaps.get(net, []) if t[0] == "track"] or [0.15])
    return tuple(sorted({max(w, 0.15), 0.15}, reverse=True))


def rejoin(m, nets, snaps, log):
    """Join every piece of each net again, widest of its widths first; a net with no way
    left gets rrr3, which may take up signal nets (never RIP_NEVER) and route them again."""
    stuck = []
    for net in nets:
        if not any(join(m, net, w, layers_of(net)) for w in ladder(m, net, snaps)):
            stuck.append(net)
    if stuck:
        res = rr3.rrr3(m, stuck, protect=RIP_NEVER | set(PROTECT) - set(stuck), layers_fn=layers_of,
                       width_fn=lambda n: ladder(m, n, snaps)[-1], log=lambda s: None, max_iter=40, max_rip=3)
        if res["left"]:
            return False, set(res["touched"]) | set(stuck)
        return True, set(res["touched"])
    return True, set()


def move_part(m, ref, target, u, log):
    """Move a part straight in from the edge until its copper is `target` from it, and tie it again."""
    fp = m.b.FindFootprintByReference(ref)
    pads = [it for it in m.items if it["kind"] == "pad" and it["ref"].split(".")[0] == ref]
    x0, y0, x1, y1 = m.edge
    g = unary_union([p["geom"] for p in pads])
    bx = g.bounds
    now = min(bx[0] - x0, bx[1] - y0, x1 - bx[2], y1 - bx[3])
    need = target - now
    if need <= 0:
        return True
    snaps = snapshot_all(m)
    cys0 = courtyards(m)
    ends0 = free_ends(m, sorted({it["net"] for it in m.items if it["kind"] == "track"} - set(PLANE)))
    for step in [need + k * 0.05 for k in range(0, 7)]:
        dx, dy = u[0] * step, u[1] * step
        sig = collections.defaultdict(list)
        rm, plane_pads = [], []
        for p in pads:
            ties = tie_tracks(m, p)
            if p["net"] in PLANE:
                pv = plane_vias_of(m, p, ties)
                rm += [t["uuid"] for t in ties] + [v["uuid"] for v in pv]
                # the vias' other ties go too: they ran only to this pad
                for v in pv:
                    rm += [t["uuid"] for t, far in attached(m, v) if far is not None]
                plane_pads.append(p["ref"])
            else:
                rm += [t["uuid"] for t in ties]
                sig[p["net"]].append(p["ref"])
        m.remove(list(set(rm)))
        fp.SetPosition(fp.GetPosition() + K.VECTOR2I(K.FromMM(dx), K.FromMM(dy)))
        m.index()
        moved = [it for it in m.items if it["kind"] == "pad" and it["ref"].split(".")[0] == ref]
        # other nets' tracks and vias now in the pads' way are taken up, if they may be
        hit = {}
        for p in moved:
            for o in m.near(p["geom"], 0.65, layers=list(p["lay"])):
                if o["kind"] in ("track", "via") and o["net"] != p["net"]:
                    need_ = max(lr.FINE if (p["in_area"] and o["in_area"]) else lr.RULE, o.get("clr", 0.0))
                    if p["geom"].distance(o["geom"]) < need_ - 1e-4 or (o["hole"] is not None and p["geom"].distance(o["hole"]) < lr.HOLE):
                        hit[o["uuid"]] = o
        unrippable = sorted({o["net"] for o in hit.values() if o["net"] in RIP_NEVER})
        if hit and not unrippable:
            m.remove(list(hit))
            m.index()
            grp.peel(m, sorted({o["net"] for o in hit.values()}))
        bad = [b for p in moved for b in pad_clear(m, p)]
        cys = courtyards(m)
        over = [r for r, c in cys.items() if r != ref and c.intersection(cys[ref]).area > 1e-4 and
                c.intersection(cys0[ref]).area <= 1e-4]
        ok = not bad and not over
        why = ("pads: %s" % bad[:3]) if bad else ("courtyard over %s" % over) if over else ""
        touched = set(sig) | {o["net"] for o in hit.values()}
        if ok:
            rr2.delete_dead(m, sorted(touched))
            ok, more = rejoin(m, sorted(touched), snaps, log)
            touched |= more
            if not ok:
                why = "%s not joined again" % sorted(more)
            elif not trim(m, touched) or (free_ends(m, sorted(touched)) - ends0) or not whole(m, touched):
                ok, why = False, "a stub left: %s" % sorted(free_ends(m, sorted(touched)) - ends0)[:3]
        if ok:
            O = Openings(m)
            for pr in plane_pads:
                p = next(it for it in m.items if it["kind"] == "pad" and it["ref"] == pr)
                if place_tie_via(m, p, O, log) is None:
                    ok, why = False, "no plane via for %s" % pr
                    break
        if not ok:
            log("    %s %.2f mm in: %s" % (ref, step, why))
        if ok:
            pads2 = [it for it in m.items if it["kind"] == "pad" and it["ref"].split(".")[0] == ref]
            bx = unary_union([p["geom"] for p in pads2]).bounds
            log("  %-4s moved %.2f mm in: copper %.2f -> %.2f mm from the edge" % (
                ref, step, now, min(bx[0] - x0, bx[1] - y0, x1 - bx[2], y1 - bx[3])))
            return True
        # undo: the part back, every net it touched as it was
        fp.SetPosition(fp.GetPosition() - K.VECTOR2I(K.FromMM(dx), K.FromMM(dy)))
        m.index()
        restore_nets(m, {p["net"] for p in pads} | touched, snaps)
    log("  %-4s could not move in to %.2f mm" % (ref, target))
    return False


def edge_copper(m, log):
    """Tracks and vias closer to the edge than the new rule, of nets that may be routed
    again (the plane ties' vias move in the next stage): taken up and the net joined again."""
    x0, y0, x1, y1 = m.edge
    hit = [it for it in m.items if it["kind"] in ("track", "via") and it["net"] not in RIP_NEVER and
           min(it["geom"].bounds[0] - x0, it["geom"].bounds[1] - y0, x1 - it["geom"].bounds[2],
               y1 - it["geom"].bounds[3]) < lr.EDGE - 1e-4]
    nets = sorted({it["net"] for it in hit})
    if not hit:
        return True
    snaps = snapshot_all(m)
    ends0 = free_ends(m, nets)
    m.remove([it["uuid"] for it in hit])
    m.index()
    grp.peel(m, nets)
    ok, more = rejoin(m, nets, snaps, log)
    if ok and (not trim(m, set(nets) | more) or free_ends(m, sorted(set(nets) | more)) - ends0
               or not whole(m, set(nets) | more)):
        ok = False
    if not ok:
        restore_nets(m, set(nets) | more, snaps)
        log("  %s: the copper within %.2f mm of the edge could not be routed again; left" % (nets, lr.EDGE))
        return False
    log("  %d track(s) / via(s) of %s within %.2f mm of the edge taken up; joined again" % (len(hit), nets, lr.EDGE))
    return True


def edge(m, log):
    ok = True
    for ref, (target, u) in EDGE_PARTS.items():
        ok &= move_part(m, ref, target, u, log)
    ok &= edge_copper(m, log)
    return ok


# ---------------------------------------------------------------- re-route
def neckdown(m, d=0.6):
    """Where a 0.10 mm track is needed: within d of a 0.4 mm-pitch pad (U9, U13)."""
    return unary_union([it["geom"].buffer(d) for it in m.items if it["kind"] == "pad" and it["ref"].startswith(("U9.", "U13."))
                        and it["geom"] is not None and not it["ref"].endswith(".61")])


def thin_far(m, nd):
    """Signal nets with 0.10 mm copper away from the fine-pitch pins, and how much."""
    out = collections.Counter()
    for it in m.items:
        if it["kind"] == "track" and it["w"] < 0.149 and it["net"] not in PROTECT:
            g = LineString([it["a"], it["c"]]) if math.dist(it["a"], it["c"]) > 1e-6 else None
            if g is not None:
                out[it["net"]] += g.difference(nd).length
    return {n: L for n, L in out.items() if L > 0.05}


def blen(items, l="B"):
    return sum(math.dist(it["a"], it["c"]) for it in items if it["kind"] == "track" and l in it["lay"])


def reroute(m, log):
    nd = neckdown(m)
    thin = thin_far(m, nd)
    fast = [n for n in FAST if blen(st.net_copper(m, n)) > 0.05]
    nets = sorted(set(thin) | set(fast), key=lambda n: (n not in fast, n))
    log("reroute: %d net(s) with 0.10 mm track away from the fine-pitch pins %s; %d fast net(s) on B.Cu %s"
        % (len(thin), sorted(thin), len(fast), fast))
    bpen = {"B": [(box(*m.edge), 40)]}
    for net in nets:
        items = st.net_copper(m, net)
        v0, L0, B0 = sum(1 for it in items if it["kind"] == "via"), st.jag_of(items)[0], blen(items)
        snap = st.snapshot(items)
        m.remove([it["uuid"] for it in items])
        m.index()
        isfast = net in fast
        ok, _ = rr3.connect(m, net, 0.15, layers_of(net), margin=2.0, extra_pen=bpen if isfast else None)
        new = st.net_copper(m, net)
        v1, L1, B1 = sum(1 for it in new if it["kind"] == "via"), st.jag_of(new)[0], blen(new)
        better = ok and v1 <= v0 and L1 <= 1.34 * L0 + 2.0 and (B1 < B0 - 0.3 if isfast and net not in thin else True)
        if better:
            log("  %-14s %5.1f -> %5.1f mm, vias %d -> %d, B.Cu %4.1f -> %4.1f mm, all 0.15 mm"
                % (net, L0, L1, v0, v1, B0, B1))
            continue
        m.remove([it["uuid"] for it in new])
        m.index()
        st.restore(m, net, snap)
        log("  %-14s kept as it was (%s)" % (net, "no way at 0.15 mm" if not ok else
                                               "%.1f mm, %d vias, B.Cu %.1f mm" % (L1, v1, B1)))


def guarded(m, net, old, segs):
    """Replace the tracks `old` by `segs` [(layer, a, c, width)], undone (False) if that
    leaves any track end of the net hanging that was not before - another track's T
    standing on a segment that moved."""
    olds = [m.uuid[u] for u in old]
    g = unary_union([it["geom"] for it in olds] + [lr.seg_geom(a, c, w) for _l, a, c, w in segs])
    region = g.buffer(0.5)
    before = free_ends(m, [net], region)
    snap = st.snapshot(olds)
    had = {t.m_Uuid.AsString() for t in m.b.GetTracks()}
    m.remove(old)
    for l, a, c, w in segs:
        m.add(net, [(l, a, c)], [], w)
    m.index()
    if free_ends(m, [net], region) - before:
        m.remove([t.m_Uuid.AsString() for t in m.b.GetTracks() if t.m_Uuid.AsString() not in had])
        m.index()
        st.restore(m, net, snap)
        return False
    return True


def joint_of(m, it, p):
    """the one other segment of it's net meeting it at a bare two-way joint p, or None"""
    l = next(iter(it["lay"]))
    its, tree = m.by_layer[l]
    pt = Point(p)
    other = None
    for k in tree.query(pt.buffer(0.03)):
        o = its[k]
        if o is it or o["net"] != it["net"]:
            continue
        if o["kind"] != "track":
            if o["geom"].distance(pt) < 0.05:
                return None                    # a pad or via holds this end
            continue
        ends = [q for q in (o["a"], o["c"]) if math.dist(q, p) < 1e-4]
        if not ends:
            if o["geom"].distance(pt) < o["w"] / 2:
                return None                    # a T
            continue
        if other is not None:
            return None                        # three or more meet here
        other = o
    return other


def nudge(m, log, net_ok, offsets=(0.025, 0.05, 0.075), w_to=0.15, passes=4):
    """A 0.10 mm segment packed against its neighbours: moved square to itself by up to
    0.075 mm - its joints sliding along the segments either side, so every angle stays -
    and widened to 0.15 mm. Only segments both of whose ends are bare two-way joints."""
    tracks = lambda: {t.m_Uuid.AsString(): t for t in m.b.GetTracks()}
    done = 0
    for _ in range(passes):
        changed = 0
        for it in [it for it in m.items if it["kind"] == "track" and it["w"] < w_to - 0.001 and net_ok(it["net"])]:
            if it["uuid"] not in m.uuid:
                continue
            l, a, c = next(iter(it["lay"])), it["a"], it["c"]
            L = math.dist(a, c)
            if L < 0.05:
                continue
            ja, jc = joint_of(m, it, a), joint_of(m, it, c)
            if ja is None or jc is None:
                continue
            u = ((c[0] - a[0]) / L, (c[1] - a[1]) / L)
            nrm = (-u[1], u[0])
            fa = ja["c"] if math.dist(ja["a"], a) < 1e-4 else ja["a"]
            fc = jc["c"] if math.dist(jc["a"], c) < 1e-4 else jc["a"]

            def meet(p0, d0, p1, d1):
                den = d0[0] * d1[1] - d0[1] * d1[0]
                if abs(den) < 1e-9:
                    return None
                t = ((p1[0] - p0[0]) * d1[1] - (p1[1] - p0[1]) * d1[0]) / den
                return (round(p0[0] + t * d0[0], 4), round(p0[1] + t * d0[1], 4))
            ign = frozenset((it["uuid"], ja["uuid"], jc["uuid"]))
            got = None
            for d in [s * o for o in offsets for s in (1, -1)]:
                s0 = (a[0] + d * nrm[0], a[1] + d * nrm[1])
                na = meet(s0, u, fa, (a[0] - fa[0], a[1] - fa[1]))
                nc = meet(s0, u, fc, (c[0] - fc[0], c[1] - fc[1]))
                if na is None or nc is None:
                    continue
                # the joints may slide, not pass the far ends or swap over
                if (math.dist(na, fa) < 0.02 or math.dist(nc, fc) < 0.02 or
                        ((na[0] - fa[0]) * (a[0] - fa[0]) + (na[1] - fa[1]) * (a[1] - fa[1])) <= 0 or
                        ((nc[0] - fc[0]) * (c[0] - fc[0]) + (nc[1] - fc[1]) * (c[1] - fc[1])) <= 0 or
                        ((nc[0] - na[0]) * u[0] + (nc[1] - na[1]) * u[1]) < 0.02):
                    continue
                if (not m.check_track(it["net"], l, na, nc, w_to, ign) and not m.check_track(it["net"], l, fa, na, ja["w"], ign)
                        and not m.check_track(it["net"], l, fc, nc, jc["w"], ign)):
                    got = (na, nc)
                    break
            if got is None:
                continue
            na, nc = got
            if guarded(m, it["net"], list(ign), [(l, fa, na, ja["w"]), (l, na, nc, w_to), (l, nc, fc, jc["w"])]):
                changed += 1
        done += changed
        if not changed:
            break
    return done


def thin_chain(m, it):
    """the run of 0.10 mm segments of it's net through bare two-way joints that holds it"""
    chain, todo = {it["uuid"]: it}, [it]
    while todo:
        cur = todo.pop()
        for p in (cur["a"], cur["c"]):
            o = joint_of(m, cur, p)
            if o is not None and o["w"] < 0.149 and o["uuid"] not in chain:
                chain[o["uuid"]] = o
                todo.append(o)
    return list(chain.values())


def local_widen(m, log, margin=1.5):
    """Each run of 0.10 mm track left outside the escape areas, taken up on its own and its
    two ends joined again at 0.15 mm in a small window round it; put back unless the net is
    whole, with no more vias, at most a third longer, and no stub."""
    esc = escape_areas(m)
    done = tried = 0
    key = lambda t: (t["net"], round(t["a"][0], 3), round(t["a"][1], 3), round(t["c"][0], 3), round(t["c"][1], 3))
    seen = set()                    # by geometry: a put-back chain comes back as new items
    while True:
        it = next((it for it in m.items if it["kind"] == "track" and it["w"] < 0.149 and it["net"] not in PLANE
                   and it["net"] not in DRAWN and not it["geom"].intersects(esc) and key(it) not in seen), None)
        if it is None:
            break
        chain = thin_chain(m, it)
        seen |= {key(c) for c in chain}
        net = it["net"]
        tried += 1
        snaps = snapshot_all(m)
        ends0 = free_ends(m, [net])
        v0 = sum(1 for x in st.net_copper(m, net) if x["kind"] == "via")
        L0 = st.jag_of(st.net_copper(m, net))[0]
        m.remove([c["uuid"] for c in chain])
        m.index()
        ok = join(m, net, 0.15, layers_of(net), margin=margin)
        if ok:
            trim(m, [net])
            new = st.net_copper(m, net)
            v1, L1 = sum(1 for x in new if x["kind"] == "via"), st.jag_of(new)[0]
            ok = whole(m, [net]) and v1 <= v0 and L1 <= 1.34 * L0 + 1.0 and not (free_ends(m, [net]) - ends0)
        if not ok:
            restore_nets(m, {net}, snaps)
            continue
        done += 1
    log("local widen: %d of %d run(s) of 0.10 mm track outside the escape areas routed again at 0.15 mm" % (done, tried))


def in_place(m):
    """every 0.10 mm signal track that has room for 0.15 mm, widened where it lies"""
    n = 0
    for it in [it for it in m.items if it["kind"] == "track" and it["w"] < 0.149 and it["net"] not in PLANE]:
        if it["uuid"] in m.uuid and not m.check_track(it["net"], next(iter(it["lay"])), it["a"], it["c"], 0.15):
            {t.m_Uuid.AsString(): t for t in m.b.GetTracks()}[it["uuid"]].SetWidth(K.FromMM(0.15))
            m.index()
            n += 1
    return n


def widen(m, log):
    """0.10 mm tracks of signal nets widened to 0.15 mm where they fit; then the ones
    packed against a neighbour nudged aside and widened; what is left outside the escape
    areas routed again on its own."""
    n = in_place(m)
    k = nudge(m, log, lambda net: net not in PLANE and net not in DRAWN)
    n += in_place(m)
    # what is still thin outside the escape areas: each run on its own, then once more
    # in place and nudged
    if outside_thin(m):
        local_widen(m, log)
    k += nudge(m, log, lambda net: net not in PLANE and net not in DRAWN)
    n += in_place(m)
    left = sum(math.dist(it["a"], it["c"]) for it in m.items if it["kind"] == "track" and it["w"] < 0.149)
    out = outside_thin(m)
    log("widen: %d track(s) to 0.15 mm in place, %d nudged aside and widened; %.1f mm of 0.10 mm track left, "
        "%.1f mm of it outside the escape areas %s" % (n, k, left, sum(out.values()), sorted(out)))


def escape_areas(m):
    return unary_union([lr.polyset(z.Outline()) for z in m.b.Zones() if z.GetIsRuleArea() and
                        z.GetZoneName() in ("U9_escape", "U13_escape")])


def outside_thin(m):
    """0.10 mm track wholly outside the escape areas (the board rule wants 0.15 there), by net"""
    esc = escape_areas(m)
    out = collections.Counter()
    for it in m.items:
        if it["kind"] == "track" and it["w"] < 0.149 and not it["geom"].intersects(esc):
            out[it["net"]] += math.dist(it["a"], it["c"])
    return dict(out)


# ---------------------------------------------------------------- corners
def joints(m):
    key = lambda p: (round(p[0], 4), round(p[1], 4))
    ends = collections.defaultdict(list)
    for it in m.items:
        if it["kind"] == "track" and math.dist(it["a"], it["c"]) > 1e-6:
            for p in (it["a"], it["c"]):
                ends[(it["net"], next(iter(it["lay"])), key(p))].append(it)
    return ends


def bare(m, net, l, p):
    its, tree = m.by_layer[l]
    pt = Point(p)
    return not any(its[k]["kind"] in ("via", "pad") and its[k]["net"] == net and its[k]["geom"].intersects(pt.buffer(0.01))
                   for k in tree.query(pt.buffer(0.01)))


def turn(p, a, c):
    u = (a[0] - p[0], a[1] - p[1])
    v = (c[0] - p[0], c[1] - p[1])
    cos = (u[0] * v[0] + u[1] * v[1]) / (math.hypot(*u) * math.hypot(*v))
    return 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def corners(m, log, sizes=(0.6, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1, 0.075, 0.05)):
    done = failed = 0
    seen = set()
    esc = escape_areas(m)
    keep = DRAWN | set(fr_finish.CORNER)          # the regulator and the ADC corner stay as drawn
    while True:
        ends = joints(m)
        todo = None
        for (net, l, p), e in ends.items():
            if len(e) != 2 or net in keep or (net, l, p) in seen:
                continue
            a = e[0]["c"] if (round(e[0]["a"][0], 4), round(e[0]["a"][1], 4)) == p else e[0]["a"]
            c = e[1]["c"] if (round(e[1]["a"][0], 4), round(e[1]["a"][1], 4)) == p else e[1]["a"]
            if abs(turn(p, a, c) - 90.0) < 1.0 and bare(m, net, l, p):
                todo = (net, l, p, e, a, c)
                break
        if todo is None:
            break
        net, l, p, e, a, c = todo
        seen.add((net, l, p))
        la, lc = math.dist(p, a), math.dist(p, c)
        ua = ((a[0] - p[0]) / la, (a[1] - p[1]) / la)
        uc = ((c[0] - p[0]) / lc, (c[1] - p[1]) / lc)
        wa, wc = e[0]["w"], e[1]["w"]
        ign = frozenset(it["uuid"] for it in e)
        got = None
        for s in sizes:
            if s > la - 0.02 or s > lc - 0.02:
                continue
            p1 = (round(p[0] + ua[0] * s, 4), round(p[1] + ua[1] * s, 4))
            p2 = (round(p[0] + uc[0] * s, 4), round(p[1] + uc[1] * s, 4))
            w = min(wa, wc)
            # no 0.10 mm segment may end up wholly outside the escape areas (the board rule)
            if any(ww < 0.149 and not lr.seg_geom(q0, q1, ww).intersects(esc)
                   for q0, q1, ww in ((a, p1, wa), (p1, p2, w), (p2, c, wc))):
                continue
            if (not m.check_track(net, l, a, p1, wa, ign) and not m.check_track(net, l, p1, p2, w, ign)
                    and not m.check_track(net, l, p2, c, wc, ign)):
                got = (s, p1, p2, w)
                break
        if got is None:
            failed += 1
            continue
        s, p1, p2, w = got
        if guarded(m, net, list(ign), [(l, a, p1, wa), (l, p1, p2, w), (l, p2, c, wc)]):
            done += 1
        else:
            failed += 1
    log("corners: %d right angle(s) cut to two 45-degree bends; %d with no room" % (done, failed))
    return done, failed


STAGES = ("edge", "vias", "reroute", "widen", "corners")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--stages", default=",".join(STAGES))
    a = ap.parse_args()
    lr.EDGE = EDGE_NEW
    lr.PRO_REF = os.path.join(lr.ROOT, "boards", "rev4", "rev4.kicad_pro")
    log = lambda s: print(s, flush=True)
    m = lr.Model(a.src)
    for s in a.stages.split(","):
        log("== %s" % s)
        {"edge": edge, "vias": vias, "reroute": reroute, "widen": widen, "corners": corners}[s](m, log)
        m.save(a.dst)
        m.save(os.path.splitext(a.dst)[0] + ".%s.kicad_pcb" % s)       # each stage as it left the board
    trim(m, {it["net"] for it in m.items if it["kind"] == "track"})
    dedupe(m, log)
    undangle(m, log)
    m.save(a.dst)
    d, c = lr.drc(a.dst, "review")
    lr.summary(d, c)
    m.save(a.dst)
    return 0 if not d["unconnected_items"] and not any(k in fr_finish.COPPER for k in c) else 1


if __name__ == "__main__":
    sys.exit(main())
