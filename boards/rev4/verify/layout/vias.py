"""vias.py [board] [gerbers] - every via: what it joins, its plane, its mask and paste, what sits over it, its current.

    python3 vias.py ../../rev4.kicad_pcb ../../fab/gerbers

From the board's copper shapes (copper.py's, zones filled):

  joins     the layers on which copper of the via's own net touches it; a via
            that joins fewer than two layers does nothing
  plane     every GND via must reach the In1 plane's fill and every +3.3V via
            the In2 plane's; how much of the ring round its land the fill
            covers says solid (about 100 %) or spokes
  barrel    the copper of other nets on the inner layers: the hole's distance
            to it, and the land's

From the Gerbers, rendered by gerbv as etest.py does:

  mask      share of each hole inside a solder-mask opening, per side: a
            tented via shows none
  paste     share of each hole under a paste aperture: paste printed over a
            hole drains into it at reflow

From the courtyards: vias under a part, and which nets; under J5's metal shell
only GND belongs.

From the DC network of each power net (powerpath.py's, tracks and vias): the
current each via carries at the net's worst design current, against the rating
of a 0.3 mm via (IPC-2221's conductor formula on the barrel's section).
"""
import collections
import math
import os
import sys
import tempfile

import numpy as np
import pcbnew as K
from shapely.geometry import Point
from shapely.strtree import STRtree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import copper            # noqa: E402
import etest             # noqa: E402

mm = K.ToMM
REV4 = os.path.normpath(os.path.join(HERE, "..", ".."))
PLANE = {"GND": "In1.Cu", "+3.3V": "In2.Cu"}
PLATING = 0.020          # mm, the barrel plating powerpath.py assumes
# worst design currents: the harness feed limit (TPS2553, 697 mA max) through the
# pass-through and from USB; the board's own draw through its OR diodes and buck
CURRENTS = [
    ("+5V_BUS", "J4.1", "J3.1", 0.70, "harness pass-through at the feed limit"),
    ("+5V_BUS", "D4.1", "J3.1", 0.70, "USB feed into the harness at the limit"),
    ("+5V_USB", "J5.A9", "U14.1", 0.76, "USB VBUS to the harness switch, limit + this board"),
    ("+5V_USB", "J5.A9", "D2.2", 0.10, "USB VBUS to this board's OR diode"),
    ("+5V", "D1.1", "U12.2", 0.10, "harness OR output to the buck"),
    ("+5V", "D2.1", "U12.2", 0.10, "USB OR output to the buck"),
    ("VCORE", "L1.1", "U9.6", 0.10, "core rail"),
]


def via_rating(dt):
    """IPC-2221 conductor current (A) for the barrel's copper section at a rise of dt C: outer, inner constant"""
    a = math.pi * (0.3 + PLATING) * PLATING * (1 / 0.0254) ** 2          # mm2 -> mil2
    return 0.048 * dt ** 0.44 * a ** 0.725, 0.024 * dt ** 0.44 * a ** 0.725, a


def currents(net, a_ref, b_ref, amps):
    """current through each via of net when amps flow from pad a to pad b: [(xy, A)]"""
    import lr
    import powerpath
    import scipy.sparse as sp
    import scipy.sparse.linalg as spl
    m = currents.model = getattr(currents, "model", None) or lr.Model(BOARD)
    its, node, edges = powerpath.network(m, net)
    n = len(node)
    ia = next(i for i, it in enumerate(its) if it["kind"] == "pad" and it["ref"] == a_ref)
    ib = next(i for i, it in enumerate(its) if it["kind"] == "pad" and it["ref"] == b_ref)
    A, B = node[(ia, 0)], node[(ib, 0)]
    rows, cols, vals = [], [], []
    for u, v, r in edges:
        g = 1.0 / r
        rows += [u, v, u, v]
        cols += [u, v, v, u]
        vals += [g, g, -g, -g]
    G = sp.csr_matrix((vals, (rows, cols)), shape=(n, n))
    keep = [k for k in range(n) if k != B]
    I = np.zeros(n - 1)
    I[keep.index(A)] = amps
    V = np.zeros(n)
    V[keep] = spl.spsolve(G[keep][:, keep].tocsc(), I)
    through = collections.defaultdict(float)
    for u, v, r in edges:
        i = abs(V[u] - V[v]) / r
        for k in (u, v):
            through[k] += i / 2.0
    out = []
    for i, it in enumerate(its):
        if it["kind"] == "via":
            out.append((it["xy"], through[node[(i, 0)]]))
    return sorted(out, key=lambda x: -x[1])


def main():
    global BOARD
    BOARD = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REV4, "rev4.kicad_pcb")
    gerbers = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REV4, "fab", "gerbers")
    sys.path.insert(0, os.path.join(REV4, "tools"))
    b = K.LoadBoard(BOARD)
    K.ZONE_FILLER(b).Fill(b.Zones())
    items, holes, ties, pads, areas = copper.shapes(b)
    by_layer = collections.defaultdict(list)
    for it in items:
        by_layer[it[0]].append(it)
    trees = {ln: STRtree([it[1] for it in its]) for ln, its in by_layer.items()}

    vias = [t for t in b.GetTracks() if t.Type() == K.PCB_VIA_T]
    sizes = collections.Counter((round(mm(v.GetWidth(K.F_Cu)), 3), round(mm(v.GetDrillValue()), 3)) for v in vias)
    joins = collections.Counter()
    idle, off_plane, spokes = [], [], []
    barrel = {"hole": (9.0, ""), "land": (9.0, "")}
    for v in vias:
        c = copper.xy(v.GetPosition())
        oid = ("t", v.m_Uuid.AsString())
        net = v.GetNetname()
        hole = Point(c).buffer(mm(v.GetDrillValue()) / 2, 64)
        used = []
        for ln in copper.LAYERS.values():
            land = Point(c).buffer(mm(v.GetWidth(K.F_Cu)) / 2, 64)
            hits = [by_layer[ln][j] for j in trees[ln].query(land.buffer(copper.TOL))]
            if any(h[2] == net and h[4] != oid and h[1].distance(land) <= copper.TOL for h in hits):
                used.append(ln)
            for h in hits:
                if h[2] != net and ln in ("In1.Cu", "In2.Cu"):
                    for key, g in (("hole", hole), ("land", land)):
                        d = g.distance(h[1])
                        if d < barrel[key][0]:
                            barrel[key] = (d, "%s via at (%.3f, %.3f) / %s" % (net, c[0], c[1], h[3]))
        joins[tuple(used)] += 1
        if len(used) < 2:
            idle.append("%s at (%.3f, %.3f): %s" % (net, c[0], c[1], used))
        if net in PLANE:
            ln = PLANE[net]
            fill = [it for it in by_layer[ln] if it[2] == net and it[4][0] == "z"]
            land = Point(c).buffer(mm(v.GetWidth(K.F_Cu)) / 2, 64)
            if not any(f[1].distance(land) <= copper.TOL for f in fill):
                off_plane.append("%s at (%.3f, %.3f)" % (net, c[0], c[1]))
            else:
                ring = land.buffer(0.1, 64).difference(land)
                cover = sum(f[1].intersection(ring).area for f in fill) / ring.area
                spokes.append(cover)

    # mask and paste over each hole, from the Gerbers
    files = etest.layer_files(gerbers)
    edge = etest.coords(files["Edge_Cuts"])
    x0, y0 = min(p[0] for p in edge) - 1.0, min(p[1] for p in edge) - 1.0
    w = round(max(p[0] for p in edge) + 1.0 - x0)
    h = round(max(p[1] for p in edge) + 1.0 - y0)
    tmp = tempfile.mkdtemp()
    img = {f: etest.render(files[f], x0, y0, w, h, os.path.join(tmp, f + ".png"))
           for f in ("F_Mask", "B_Mask", "F_Paste", "B_Paste")}
    exposed = collections.defaultdict(list)
    for v in vias:
        cx, cy = copper.xy(v.GetPosition())
        r = mm(v.GetDrillValue()) / 2
        rr, cc = np.mgrid[-20:21, -20:21]
        disk = (rr ** 2 + cc ** 2) <= (r * etest.PX) ** 2
        R, C = int(round((y0 + h + cy) * etest.PX)), int(round((cx - x0) * etest.PX))      # Gerber y = -board y
        for f, im in img.items():
            share = im[R - 20:R + 21, C - 20:C + 21][disk].mean()
            if share > 0:
                exposed[f].append((round(100 * share), v.GetNetname(), round(cx, 3), round(cy, 3)))

    # vias under parts
    under = collections.defaultdict(collections.Counter)
    for f in b.GetFootprints():
        for side in (K.F_CrtYd, K.B_CrtYd):
            cy_ = copper.polyset(f.GetCourtyard(side))
            if cy_.is_empty:
                continue
            for v in vias:
                if cy_.contains(Point(copper.xy(v.GetPosition()))):
                    under[f.GetReference()][v.GetNetname()] += 1

    print("%s: %d vias, %s" % (os.path.basename(BOARD), len(vias), ", ".join(
        "%d at %.2f / %.2f mm (ring %.2f)" % (n, s, d, (s - d) / 2) for (s, d), n in sorted(sizes.items()))))
    print("  joins (layers with copper of its own net on the land): %s" % ", ".join(
        "%s %d" % ("+".join(l.split(".")[0] for l in k) or "nothing", n) for k, n in joins.most_common()))
    print("  joining fewer than two layers: %d %s" % (len(idle), idle[:10]))
    print("  plane vias off their plane's fill: %d %s; ring round the land covered by the fill: %.0f-%.0f %% (%d vias)" % (
        len(off_plane), off_plane[:10], 100 * min(spokes), 100 * max(spokes), len(spokes)))
    print("  inner layers, other nets: closest to a hole %.3f mm (%s); to a land %.3f mm (%s)" % (
        barrel["hole"][0], barrel["hole"][1], barrel["land"][0], barrel["land"][1]))
    for f in ("F_Mask", "B_Mask", "F_Paste", "B_Paste"):
        print("  %-7s over a hole: %d %s" % (f.replace("_", "."), len(exposed[f]), sorted(exposed[f], reverse=True)))
    print("  under a part's courtyard: %s" % "; ".join(
        "%s %s" % (ref, dict(c)) for ref, c in sorted(under.items())))
    hot, cold, sect = via_rating(10.0)
    print("  current: a 0.3 mm via with %.0f um plating has %.1f mil2 of barrel; IPC-2221 gives %.2f A (outer constant) "
          "or %.2f A (inner) for a 10 C rise" % (PLATING * 1e3, sect, hot, cold))
    for net, a, bb, amps, what in CURRENTS:
        vs = currents(net, a, bb, amps)
        top = ", ".join("%.2f A at (%.2f, %.2f)" % (i, p[0], p[1]) for p, i in vs[:4] if i > 0.005)
        print("    %-8s %-6s -> %-6s %.2f A (%s): %s" % (net, a, bb, amps, what, top or "no via carries it"))
    ok = not idle and not off_plane
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
