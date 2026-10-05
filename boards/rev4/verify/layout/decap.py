"""decap.py board [board ...] - every IC supply pin and its nearest decoupling capacitor, measured along copper.

For each supply pin: the nearest same-net capacitor (whose other pad is GND) that the
pin reaches on its own layer without a via, and the length of that copper path; if
none, the shortest path through vias. For the capacitor: its GND pad to the nearest
GND via, along copper."""
import heapq, math, os, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import lr, rr2
from shapely.strtree import STRtree

SUPPLY = {"+3.3V", "VCORE", "ADC_AVDD", "VREG_AVDD", "ROW_VCC", "+5V", "+5V_USB", "USB_BUS_SW"}


def graph(m, net):
    its = [it for it in m.items if it["net"] == net]
    geoms = [rr2.geo(it) for it in its]
    tree = STRtree(geoms)
    adj = collections.defaultdict(list)
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            j = int(j)
            if j <= i or not (its[i]["lay"] & its[j]["lay"]):
                continue
            if rr2.joined(its[i], g, its[j], geoms[j]):
                adj[i].append(j)
                adj[j].append(i)
    return its, adj


def cost(it):
    return math.dist(it["a"], it["c"]) if it["kind"] == "track" else 0.0


def paths(its, adj, start, layer=None):
    """Dijkstra from item index start; layer: stay on that layer, no vias."""
    best = {start: (0.0, 0)}
    pq = [(0.0, 0, start)]
    while pq:
        d, v, i = heapq.heappop(pq)
        if best.get(i, (1e9, 0)) < (d, v):
            continue
        for j in adj[i]:
            it = its[j]
            if layer is not None and (it["kind"] == "via" or layer not in it["lay"]):
                continue
            nd, nv = d + cost(it), v + (1 if it["kind"] == "via" else 0)
            if (nd, nv) < best.get(j, (1e9, 0)):
                best[j] = (nd, nv)
                heapq.heappush(pq, (nd, nv, j))
    return best


def analyse(path):
    m = lr.Model(path)
    pads = [it for it in m.items if it["kind"] == "pad"]
    caps = collections.defaultdict(list)       # net -> cap pads on it (other pad GND)
    by_ref = collections.defaultdict(list)
    for p in pads:
        by_ref[p["ref"].split(".")[0]].append(p)
    for ref, ps in by_ref.items():
        if ref.startswith("C") and len(ps) == 2 and {p["net"] for p in ps} & {"GND"}:
            for p in ps:
                if p["net"] in SUPPLY:
                    caps[p["net"]].append((ref, p, next(q for q in ps if q is not p)))
    gcache = {}
    out = []
    for p in pads:
        ref = p["ref"].split(".")[0]
        if not (ref.startswith("U") or ref in ("D5",)) or p["net"] not in SUPPLY:
            continue
        net = p["net"]
        if net not in gcache:
            gcache[net] = graph(m, net)
        its, adj = gcache[net]
        si = next(i for i, it in enumerate(its) if it is p)
        lay = "F" if "F" in p["lay"] else next(iter(p["lay"]))
        same = paths(its, adj, si, lay)
        anyp = paths(its, adj, si)
        near = sorted(caps[net], key=lambda c: math.dist(c[1]["xy"], p["xy"]))[:4]
        best = None
        for cref, cp, cg in near:
            ci = next(i for i, it in enumerate(its) if it is cp)
            if ci in same:
                cand = ("F", same[ci][0], 0, cref, math.dist(cp["xy"], p["xy"]), cg)
            elif ci in anyp:
                cand = ("via", anyp[ci][0], anyp[ci][1], cref, math.dist(cp["xy"], p["xy"]), cg)
            else:
                continue
            key = (cand[0] != "F", cand[1])
            if best is None or key < (best[0] != "F", best[1]):
                best = cand
        out.append((p["ref"], net, best))
    # GND side of each cap: GND pad to the nearest GND via along copper
    gits, gadj = graph(m, "GND")
    gvia = {}
    for net, cl in caps.items():
        for cref, cp, cg in cl:
            gi = next(i for i, it in enumerate(gits) if it is cg)
            d = paths(gits, gadj, gi)
            vs = [d[j] for j in d if gits[j]["kind"] == "via" or (gits[j]["kind"] == "pad" and len(gits[j]["lay"]) > 2)]
            gvia[cref] = min(vs)[0] if vs else None
    return out, gvia


if __name__ == "__main__":
    res = {}
    for path in sys.argv[1:]:
        res[path] = analyse(path)
    names = [os.path.basename(p) for p in sys.argv[1:]]
    print("pin            net        " + "  |  ".join("%-34s" % n for n in names))
    rows = collections.OrderedDict()
    for path in sys.argv[1:]:
        for ref, net, best in res[path][0]:
            rows.setdefault((ref, net), {})[path] = best
    key = lambda r: [int(t) if t.isdigit() else t for t in __import__("re").split(r"(\d+)", r[0][0])]
    for (ref, net), per in sorted(rows.items(), key=key):
        cells = []
        for path in sys.argv[1:]:
            b = per.get(path)
            if b is None:
                cells.append("%-34s" % "no cap reached")
            else:
                kind, L, v, cref, dc, cg = b
                gv = res[path][1].get(cref)
                cells.append("%-34s" % ("%s %s %.2f mm%s; GND via %s" % (cref, "F.Cu" if kind == "F" else "VIA", L,
                                        "" if kind == "F" else " (%d via)" % v, "%.2f" % gv if gv is not None else "-")))
        print("%-14s %-10s %s" % (ref, net, "  |  ".join(cells)))
