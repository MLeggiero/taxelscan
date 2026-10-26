"""copper.py board [netlist] - connectivity, shorts and spacing from the board's copper shapes alone.

    python3 copper.py ../../rev4.kicad_pcb ../../rev4.net

A check that does not use KiCad's DRC or connectivity engine. pcbnew is used only
to read the items and to fill the zones, as the plotter does before writing
Gerbers. Every copper shape then becomes a shapely polygon on its layer:

  - tracks are stadiums (and arcs, sampled), at their width
  - pads are their effective polygons, on the layers they are flashed on; a
    plated hole that is not flashed on a layer is its barrel there
  - vias are their lands on the layers they are flashed on, otherwise the barrel
  - zone fills are the filled polygons, holes and all
  - footprint copper (the bridged ADDR jumpers) is a net tie between its pads

Two shapes on one layer that touch are joined; a via or a plated hole joins its
layers. Then:

  1. every net of the netlist (default ../../rev4.net) must be one piece that
     holds every one of its pads, and a net's copper with no pad is reported
  2. no two nets may touch (a net tie's two nets aside)
  3. spacing between nets on each layer, against the rules: 0.10 mm where both
     items reach into an escape area (rev4.kicad_dru), 0.15 mm elsewhere, the
     zone's own 0.25 mm for plane fill
  4. plated and unplated holes to other nets' copper on every layer (0.25 mm,
     min_hole_clearance), hole to hole (0.25 mm), and copper to the outline
     (0.40 mm)
  5. every pad's net against the netlist's

Exit 0 only if 1, 2, 3, 4 and 5 hold.
"""
import collections
import math
import os
import re
import sys

import pcbnew as K
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

HERE = os.path.dirname(os.path.abspath(__file__))
mm = K.ToMM
LAYERS = {K.F_Cu: "F.Cu", K.In1_Cu: "In1.Cu", K.In2_Cu: "In2.Cu", K.B_Cu: "B.Cu"}
RULE, FINE, ZONE, HOLE, H2H, EDGE = 0.15, 0.10, 0.25, 0.25, 0.25, 0.40
TOL = 0.001          # shapes closer than this touch (KiCad's internal unit is 1 nm)


def polyset(ps):
    out = []
    for k in range(ps.OutlineCount()):
        o = ps.COutline(k)
        shell = [(mm(o.CPoint(i).x), mm(o.CPoint(i).y)) for i in range(o.PointCount())]
        holes = []
        for h in range(ps.HoleCount(k)):
            hc = ps.CHole(k, h)
            holes.append([(mm(hc.CPoint(i).x), mm(hc.CPoint(i).y)) for i in range(hc.PointCount())])
        if len(shell) >= 3:
            out.append(Polygon(shell, [x for x in holes if len(x) >= 3]).buffer(0))
    return unary_union(out) if out else Polygon()


def xy(v):
    return (mm(v.x), mm(v.y))


def arc_points(t):
    a, m, c = xy(t.GetStart()), xy(t.GetMid()), xy(t.GetEnd())
    ax, ay, bx, by, cx, cy = a[0], a[1], m[0], m[1], c[0], c[1]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [a, c]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.dist((ux, uy), a)
    t0, t1, tm = (math.atan2(p[1] - uy, p[0] - ux) for p in (a, c, m))
    # go from t0 to t1 the way that passes tm
    def ccw(x, y):
        return (y - x) % (2 * math.pi)
    sweep = ccw(t0, t1) if ccw(t0, tm) < ccw(t0, t1) else -ccw(t1, t0)
    n = max(8, int(abs(sweep) * r / 0.01))
    return [(ux + r * math.cos(t0 + sweep * i / n), uy + r * math.sin(t0 + sweep * i / n)) for i in range(n + 1)]


def netlist(path):
    s = open(path, encoding="utf-8").read()
    nets = {}
    for chunk in s.split("(net (code")[1:]:
        name = re.search(r'\(name "([^"]*)"\)', chunk).group(1)
        nets[name.lstrip("/")] = re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', chunk)
    return nets


def shapes(b):
    """every copper shape of a board whose zones are filled: (items, holes, ties, pads, areas)

    items  (layer, geometry, net, label, object id)
    holes  (centre, geometry, net or None, label, plated)
    ties   footprint copper -> the nets it may join (a net tie's pads)
    pads   (ref, number) -> [(object id, net)]
    areas  the escape rule areas by name
    """
    areas = {z.GetZoneName(): polyset(z.Outline()) for z in b.Zones() if z.GetIsRuleArea() and z.GetZoneName().endswith("_escape")}

    items = []          # (layer, geometry, net, label, object id)
    holes = []          # (centre, radius or geometry, net or None, label, plated)
    ties = {}           # object id -> the nets it may join
    pads = {}           # (ref, number) -> (object id, net)

    def add(layer, g, net, label, oid):
        if not g.is_empty:
            items.append((layer, g, net, label, oid))

    for t in b.GetTracks():
        oid = ("t", t.m_Uuid.AsString())
        if t.Type() == K.PCB_VIA_T:
            c = xy(t.GetPosition())
            r_hole = mm(t.GetDrillValue()) / 2
            holes.append((c, Point(c).buffer(r_hole, 64), t.GetNetname(), "via %s at (%.3f, %.3f)" % (t.GetNetname(), c[0], c[1]), True))
            for lid, ln in LAYERS.items():
                if t.IsOnLayer(lid):
                    r = mm(t.GetWidth(lid)) / 2 if t.FlashLayer(lid) else r_hole
                    add(ln, Point(c).buffer(r, 64), t.GetNetname(), "via %s" % t.GetNetname(), oid)
        else:
            ln = LAYERS.get(t.GetLayer())
            if ln is None:
                continue
            pts = arc_points(t) if t.Type() == K.PCB_ARC_T else [xy(t.GetStart()), xy(t.GetEnd())]
            g = (LineString(pts) if pts[0] != pts[-1] or len(pts) > 2 else Point(pts[0])).buffer(mm(t.GetWidth()) / 2, 32)
            add(ln, g, t.GetNetname(), "track %s on %s" % (t.GetNetname(), ln), oid)

    for f in b.GetFootprints():
        ref = f.GetReference()
        tie_nets = set()
        for p in f.Pads():
            oid = ("p", ref, p.GetNumber(), p.m_Uuid.AsString())
            net = p.GetNetname()
            if p.GetNumber():
                pads.setdefault((ref, p.GetNumber()), []).append((oid, net))
            tie_nets.add(net)
            drilled = p.GetDrillSize().x > 0
            if drilled:
                c = xy(p.GetPosition())
                hole = polyset(p.GetEffectiveHoleShape().ConvertToPolyset(K.ERROR_OUTSIDE, 1000)) if hasattr(p.GetEffectiveHoleShape(), "ConvertToPolyset") else None
                if hole is None or hole.is_empty:
                    hole = Point(c).buffer(mm(min(p.GetDrillSize().x, p.GetDrillSize().y)) / 2, 64)
                plated = p.GetAttribute() == K.PAD_ATTRIB_PTH
                holes.append((c, hole, net if plated else None, "%s.%s hole" % (ref, p.GetNumber()), plated))
            if p.GetAttribute() == K.PAD_ATTRIB_NPTH:
                continue
            for lid, ln in LAYERS.items():
                if not p.IsOnLayer(lid):
                    continue
                if p.FlashLayer(lid):
                    sp = K.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(sp, lid, 0, K.FromMM(0.001), K.ERROR_INSIDE)
                    add(ln, polyset(sp), net, "%s.%s %s on %s" % (ref, p.GetNumber(), net, ln), oid)
                elif drilled and p.GetAttribute() == K.PAD_ATTRIB_PTH:
                    add(ln, hole, net, "%s.%s barrel %s on %s" % (ref, p.GetNumber(), net, ln), oid)
        for g in f.GraphicalItems():
            ln = LAYERS.get(g.GetLayer())
            if ln and g.GetClass() == "PCB_SHAPE":
                sp = K.SHAPE_POLY_SET()
                g.TransformShapeToPolygon(sp, g.GetLayer(), 0, K.FromMM(0.001), K.ERROR_INSIDE)
                oid = ("g", ref, g.m_Uuid.AsString())
                ties[oid] = tie_nets if f.IsNetTie() else set()
                add(ln, polyset(sp), "<tie %s>" % ref if f.IsNetTie() else "<no net>", "%s copper on %s" % (ref, ln), oid)

    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        for lid, ln in LAYERS.items():
            if z.IsOnLayer(lid):
                fill = polyset(z.GetFilledPolysList(lid))
                geoms = list(fill.geoms) if isinstance(fill, MultiPolygon) else [fill]
                for i, g in enumerate(geoms):
                    add(ln, g, z.GetNetname(), "%s fill #%d on %s" % (z.GetNetname(), i, ln), ("z", z.m_Uuid.AsString(), ln, i))

    return items, holes, ties, pads, areas


def main():
    board = sys.argv[1]
    nl = netlist(sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "..", "rev4.net"))
    b = K.LoadBoard(board)
    K.ZONE_FILLER(b).Fill(b.Zones())
    items, holes, ties, pads, areas = shapes(b)

    # ---- join touching same-net shapes; record every other-net contact
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, c):
        parent[find(a)] = find(c)

    for it in items:
        find(it[4])
    shorts, gaps, worst = [], collections.defaultdict(lambda: (9.0, "")), []
    by_layer = collections.defaultdict(list)
    for it in items:
        by_layer[it[0]].append(it)
    zone_of = lambda it: it[4][0] == "z"
    for ln, its in by_layer.items():
        tree = STRtree([it[1] for it in its])
        for i, a in enumerate(its):
            ia = set()
            for j in tree.query(a[1].buffer(ZONE + 0.01)):
                if j <= i:
                    continue
                c = its[j]
                d = a[1].distance(c[1])
                same = a[2] == c[2]
                tie = (a[4] in ties and c[2] in ties[a[4]]) or (c[4] in ties and a[2] in ties[c[4]])
                if d <= TOL:
                    if same or tie:
                        union(a[4], c[4])
                    else:
                        shorts.append((ln, a[3], c[3], d))
                    continue
                if same or tie:
                    continue
                # the escape areas' 0.10 mm holds between any two items that reach
                # into one, a plane's fill included (it reaches into all of them)
                near = [n for n, g in areas.items() if g.intersects(a[1]) and g.intersects(c[1])]
                need = FINE if near else ZONE if zone_of(a) or zone_of(c) else RULE
                key = (ln, ("zone " if zone_of(a) or zone_of(c) else "") + ("fine" if near else "rule"))
                if d < gaps[key][0]:
                    gaps[key] = (d, "%s / %s" % (a[3], c[3]))
                if d < need - 0.0005:
                    worst.append((round(d, 4), need, ln, a[3], c[3]))

    # ---- pieces per net; every pad of a netlist net in one piece
    piece_nets = collections.defaultdict(set)
    piece_pads = collections.defaultdict(set)
    for it in items:
        if not it[2].startswith("<"):
            piece_nets[find(it[4])].add(it[2])
        if it[4][0] == "p":
            piece_pads[find(it[4])].add((it[4][1], it[4][2]))
    split, padnet = [], []
    for net, nodes in sorted(nl.items()):
        roots = set()
        for ref, num in nodes:
            for oid, bnet in pads.get((ref, num), []):
                roots.add(find(oid))
                if bnet.lstrip("/") != net:
                    padnet.append("%s.%s: board %s, netlist %s" % (ref, num, bnet, net))
            if (ref, num) not in pads:
                padnet.append("%s.%s (net %s) is not on the board" % (ref, num, net))
        if len(nodes) > 1 and len(roots) > 1:
            split.append("%s: %d pieces" % (net, len(roots)))
    in_nl = {(r, n) for nodes in nl.values() for r, n in nodes}
    for key, lst in pads.items():
        for oid, bnet in lst:
            if bnet and key not in in_nl:
                padnet.append("%s.%s carries %s on the board, no net in the netlist" % (key[0], key[1], bnet))
    padless = collections.Counter()
    for root, ns in piece_nets.items():
        if not piece_pads.get(root):
            padless[tuple(sorted(ns))] += 1

    # ---- holes
    hole_bad, h2h = [], (9.0, "")
    htree = STRtree([h[1] for h in holes])
    for i, h in enumerate(holes):
        for j in htree.query(h[1].buffer(H2H + 0.01)):
            if j <= i:
                continue
            d = h[1].distance(holes[j][1])
            if d < h2h[0]:
                h2h = (d, "%s / %s" % (h[3], holes[j][3]))
            if d < H2H - 0.0005 and not (h[3].split(" hole")[0] == holes[j][3].split(" hole")[0]):
                hole_bad.append("hole to hole %.3f mm: %s / %s" % (d, h[3], holes[j][3]))
    hcu = (9.0, "")
    for ln, its in by_layer.items():
        tree = STRtree([it[1] for it in its])
        for h in holes:
            for j in tree.query(h[1].buffer(HOLE + 0.01)):
                c = its[j]
                if h[4] and c[2] == h[2]:
                    continue
                if c[4] in ties and h[2] in ties[c[4]]:
                    continue
                d = h[1].distance(c[1])
                if d < hcu[0]:
                    hcu = (d, "%s / %s" % (h[3], c[3]))
                if d < HOLE - 0.0005:
                    hole_bad.append("hole to copper %.3f mm on %s: %s / %s" % (d, ln, h[3], c[3]))

    # ---- outline
    edges = []
    for d in b.GetDrawings():
        if d.GetLayer() == K.Edge_Cuts:
            sp = K.SHAPE_POLY_SET()
            d.TransformShapeToPolygon(sp, K.Edge_Cuts, 0, K.FromMM(0.001), K.ERROR_INSIDE)
            edges.append(polyset(sp))
    outline = unary_union(edges)
    ecu = min((outline.distance(it[1]), it[3]) for it in items)

    print("%s: %d copper shapes on %d layers, %d holes, %d nets in the netlist" % (
        os.path.basename(board), len(items), len(by_layer), len(holes), len(nl)))
    print("1. nets split into pieces: %d %s" % (len(split), split[:10]))
    print("   copper with no pad: %s" % (dict(padless) or "none"))
    print("2. contacts between different nets: %d" % len(shorts))
    for s in shorts[:10]:
        print("     %s: %s / %s" % s[:3])
    print("3. closest copper of different nets, per layer and rule:")
    for key in sorted(gaps):
        print("     %-7s %-10s %.4f mm  %s" % (key[0], key[1], gaps[key][0], gaps[key][1]))
    print("   under the rule: %d" % len(worst))
    for w in sorted(worst)[:10]:
        print("     %.4f < %.2f on %s: %s / %s" % w)
    print("4. holes: %d plated, %d unplated; closest hole to hole %.4f mm (%s); closest hole to other copper %.4f mm (%s); copper to outline %.4f mm (%s)" % (
        sum(1 for h in holes if h[4]), sum(1 for h in holes if not h[4]), h2h[0], h2h[1], hcu[0], hcu[1], ecu[0], ecu[1]))
    for x in hole_bad[:10]:
        print("     " + x)
    print("5. pad nets against the netlist: %d differences" % len(padnet))
    for x in padnet[:10]:
        print("     " + x)
    ok = not split and not shorts and not worst and not hole_bad and not padnet and ecu[0] >= EDGE - 0.0005
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
