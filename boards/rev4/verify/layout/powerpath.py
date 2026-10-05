"""powerpath.py board - DC resistance and narrowest copper between power terminals (tracks and vias; no zones).

Copper: outer layers 35 um (1 oz finished), inner 17.5 um. Vias: 0.3 mm drill,
20 um barrel plating, 1.6 mm board. Resistances are solved as a network, so
parallel paths count."""
import math, os, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spl
from shapely.geometry import Point
from shapely.strtree import STRtree
import lr, rr2

RHO = 1.72e-8
T = {"F": 35e-6, "B": 35e-6, "In1": 17.5e-6, "In2": 17.5e-6}
R_VIA = RHO * 1.6e-3 / (math.pi * 0.3e-3 * 20e-6)
PATHS = [
    ("+5V_BUS", "J4.1", "J3.1", "harness pass-through, J4 to J3"),
    ("+5V_BUS", "J3.1", "D1.2", "harness to this board's OR diode"),
    ("+5V_BUS", "D4.1", "J3.1", "USB feed into the harness"),
    ("+5V_USB", "J5.A9", "U14.1", "USB VBUS (east pins) to the harness switch"),
    ("+5V_USB", "J5.B9", "U14.1", "USB VBUS (west pins) to the harness switch"),
    ("+5V_USB", "J5.A9", "D2.2", "USB VBUS to this board's OR diode"),
    ("USB_BUS_SW", "U14.6", "D4.2", "switch output to the blocking diode"),
    ("+5V", "D1.1", "U12.4", "OR output to the buck input"),
    ("+5V", "D2.1", "U12.4", "OR output to the buck input"),
    ("ROW_VCC", "R5.2", "U1.16", "row rail to U1"),
    ("ROW_VCC", "R5.2", "U4.16", "row rail to U4"),
    ("VCORE", "L1.1", "U9.6", "core rail to DVDD pin 6"),
    ("VCORE", "L1.1", "U9.39", "core rail to DVDD pin 39"),
    ("SW_NODE", "U12.3", "L2.1", "buck switch node"),
    ("VREG_LX", "U9.48", "L1.2", "core regulator switch node"),
]


def network(m, net):
    its = [it for it in m.items if it["net"] == net]
    geoms = [rr2.geo(it) for it in its]
    tree = STRtree(geoms)
    node = {}                      # (item index, end) -> node id
    def nid(k):
        if k not in node:
            node[k] = len(node)
        return node[k]
    edges = []
    for i, it in enumerate(its):
        if it["kind"] == "track":
            l = next(iter(it["lay"]))
            L = math.dist(it["a"], it["c"]) * 1e-3
            r = RHO * L / (it["w"] * 1e-3 * T[l]) if L > 0 else 1e-9
            edges.append((nid((i, 0)), nid((i, 1)), r))
        else:
            nid((i, 0))
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            j = int(j)
            if j <= i or not (its[i]["lay"] & its[j]["lay"]) or not rr2.joined(its[i], g, its[j], geoms[j]):
                continue
            def ends(a, ga, b, gb):
                """node(s) of a that touch b."""
                if a["kind"] != "track":
                    return [(0, 0.0)]
                out = []
                for e, p in ((0, a["a"]), (1, a["c"])):
                    if gb.distance(Point(p)) < 0.01:
                        out.append((e, 0.0))
                if not out:                     # b's anchor touches a mid-track: nearest end, part of its length
                    q = rr2.anchors(b)[0]
                    da, dc = math.dist(q, a["a"]), math.dist(q, a["c"])
                    e, dd = (0, da) if da <= dc else (1, dc)
                    l = next(iter(a["lay"]))
                    out.append((e, RHO * dd * 1e-3 / (a["w"] * 1e-3 * T[l])))
                return out
            for ei, ri in ends(its[i], g, its[j], geoms[j]):
                for ej, rj in ends(its[j], geoms[j], its[i], g):
                    r = ri + rj + 1e-7
                    if its[i]["kind"] == "via" or its[j]["kind"] == "via":
                        r += R_VIA / 2
                    edges.append((nid((i, ei)), nid((j, ej)), r))
    return its, node, edges


def resistance(its, node, edges, a_ref, b_ref):
    n = len(node)
    ia = next(i for i, it in enumerate(its) if it["kind"] == "pad" and it["ref"] == a_ref)
    ib = next(i for i, it in enumerate(its) if it["kind"] == "pad" and it["ref"] == b_ref)
    A, B = node[(ia, 0)], node[(ib, 0)]
    rows, cols, vals = [], [], []
    for u, v, r in edges:
        g = 1.0 / r
        rows += [u, v, u, v]; cols += [u, v, v, u]; vals += [g, g, -g, -g]
    G = sp.csr_matrix((vals, (rows, cols)), shape=(n, n))
    keep = [k for k in range(n) if k != B]
    Gr = G[keep][:, keep]
    I = np.zeros(n - 1)
    I[keep.index(A)] = 1.0
    try:
        V = spl.spsolve(Gr.tocsc(), I)
    except Exception:
        return None
    r = V[keep.index(A)]
    return r if np.isfinite(r) and r < 1e3 else None


m = lr.Model(sys.argv[1])
cache = {}
print("%-11s %-6s -> %-6s %10s  %9s  %s" % ("net", "from", "to", "R (mOhm)", "min w (mm)", "what"))
for net, a, b, what in PATHS:
    if net not in cache:
        cache[net] = network(m, net)
    its, node, edges = cache[net]
    r = resistance(its, node, edges, a, b)
    ws = [it["w"] for it in its if it["kind"] == "track"]
    nv = sum(1 for it in its if it["kind"] == "via")
    print("%-11s %-6s -> %-6s %10s  %9s  %s (net: %d vias)" % (net, a, b, "%.1f" % (r * 1e3) if r is not None else "open",
          "%.2f" % min(ws) if ws else "-", what, nv))
