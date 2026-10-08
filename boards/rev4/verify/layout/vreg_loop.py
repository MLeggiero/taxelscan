"""vreg_loop.py board - the area the RP2354A core regulator's switching loop encloses.

The loop: VREG_LX from pin 48 to L1, across L1, VCORE from L1 to COUT (C19),
across C19, GND from C19 back to VREG_PGND (pin 47), and across the pin row to
pin 48. Each leg follows its net's F.Cu copper, the shortest way between the
pads' centres. Where the centre line crosses itself it splits into lobes, and
each lobe counts once. Measured the same way, Raspberry Pi's RP2350A minimal
design (R4) encloses 2.28 mm2.

A leg with no F.Cu path (the ground returning through vias and the plane, as on
the boards before 7 October) is reported, and the loop is not closed.
"""
import collections
import heapq
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import lr
from shapely.geometry import LineString, Point
from shapely.ops import polygonize, unary_union

LEGS = [("VREG_LX", "U9.48", "L1.2"), (None, "L1.2", "L1.1"), ("VCORE", "L1.1", "C19.1"),
        (None, "C19.1", "C19.2"), ("GND", "C19.2", "U9.47"), (None, "U9.47", "U9.48")]


def key(p):
    return (round(p[0], 3), round(p[1], 3))


def leg(m, net, a, b):
    """Shortest F.Cu centre-line path from pad a's centre to pad b's, along net's tracks."""
    pads = {it["ref"]: it for it in m.items if it["kind"] == "pad"}
    adj = collections.defaultdict(list)
    tracks = [it for it in m.items if it["kind"] == "track" and it["net"] == net and "F" in it["lay"]]
    for t in tracks:
        p, q = key(t["a"]), key(t["c"])
        adj[p].append((q, math.dist(p, q)))
        adj[q].append((p, math.dist(p, q)))
    for ref, pad in pads.items():               # every F.Cu pad of the net joins the track ends on it
        if pad["net"] != net or "F" not in pad["lay"]:
            continue
        c, g = key(pad["xy"]), pad["geom"]
        for t in tracks:
            for p in (key(t["a"]), key(t["c"])):
                if g.buffer(1e-3).contains(Point(p)):
                    adj[c].append((p, math.dist(c, p)))
                    adj[p].append((c, math.dist(c, p)))
    s, e = key(pads[a]["xy"]), key(pads[b]["xy"])
    best, prev, pq = {s: 0.0}, {}, [(0.0, s)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == e:
            break
        if d > best[u]:
            continue
        for v, w in adj[u]:
            if d + w < best.get(v, 1e9):
                best[v], prev[v] = d + w, u
                heapq.heappush(pq, (d + w, v))
    if e not in best:
        return None
    path = [e]
    while path[-1] != s:
        path.append(prev[path[-1]])
    return path[::-1]


def main(path):
    m = lr.Model(path)
    pads = {it["ref"]: it for it in m.items if it["kind"] == "pad"}
    pts = []
    for net, a, b in LEGS:
        seg = [key(pads[a]["xy"]), key(pads[b]["xy"])] if net is None else leg(m, net, a, b)
        if seg is None:
            print("%s %s -> %s: no F.Cu path; the loop closes through vias and a plane" % (net, a, b))
            return 1
        L = sum(math.dist(p, q) for p, q in zip(seg, seg[1:]))
        print("%-8s %-6s -> %-6s %5.2f mm%s" % (net or "(part)", a, b, L, "" if net else " straight"))
        pts += seg if not pts else seg[1:]
    lobes = sorted((f.area for f in polygonize(unary_union(LineString(pts)))), reverse=True)
    print("loop: %.2f mm2 (lobes %s)" % (sum(lobes), ", ".join("%.2f" % a for a in lobes)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
