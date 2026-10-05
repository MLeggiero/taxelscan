"""aggr.py board - other nets' copper beside the sensitive nets, same layer, within GAP mm (edge to edge).

For each sensitive net: each neighbour net, the length of the sensitive net's tracks that
runs within GAP of it, and the closest edge distance. Inner layers carry only the
planes on this board, so same-layer neighbours are the coupling that matters."""
import os, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from shapely.strtree import STRtree
from shapely.geometry import LineString
import lr, rr2

GAP = float(sys.argv[2]) if len(sys.argv) > 2 else 0.30
SENS = ["SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B", "AMP_A", "AMP_B", "ADC_A", "ADC_B", "RAIL_MON", "ADC_AVDD",
        "VREG_AVDD", "XIN", "XOUT", "XOUT_MCU", "USB_D_P", "USB_D_N", "USBC_D_P", "USBC_D_N"]
QUIET = {"GND", "+3.3V"}
SWITCHING = {"SW_NODE", "VREG_LX"}
m = lr.Model(sys.argv[1])
its = [it for it in m.items if it["kind"] in ("track", "via", "pad") and rr2.geo(it) is not None]
geoms = [rr2.geo(it) for it in its]
tree = STRtree(geoms)
def cls(n):
    if n in QUIET: return "quiet"
    if n in SWITCHING: return "SWITCHING"
    if n.startswith("COL_"): return "column"
    if n.startswith("ROW_") and n[4:].isdigit(): return "row"
    if n in SENS: return "sensitive"
    return "digital/other"
for net in SENS:
    mine = [(i, it) for i, it in enumerate(its) if it["net"] == net and it["kind"] == "track"]
    agg = collections.defaultdict(lambda: [0.0, 9.0])
    for i, it in mine:
        l = next(iter(it["lay"]))
        seg = LineString([it["a"], it["c"]])
        g = geoms[i]
        for j in tree.query(g.buffer(GAP)):
            o = its[int(j)]
            if o["net"] == net or not (o["lay"] & it["lay"]) or not o["net"]:
                continue
            d = g.distance(geoms[int(j)])
            if d > GAP:
                continue
            near = seg.intersection(geoms[int(j)].buffer(GAP + it["w"] / 2))
            a = agg[(o["net"], l)]
            a[0] += near.length
            a[1] = min(a[1], d)
    L = sum(rr2.geo(it).length if False else ((it["a"][0]-it["c"][0])**2+(it["a"][1]-it["c"][1])**2)**.5 for _, it in mine)
    lays = collections.Counter(next(iter(it["lay"])) for _, it in mine)
    items = sorted(agg.items(), key=lambda kv: -kv[1][0])
    loud = [(n, l, round(v[0], 2), round(v[1], 3)) for (n, l), v in items if cls(n) not in ("quiet",) and n != net]
    print("%-10s %5.1f mm %-14s neighbours within %.2f mm: %s" % (net, L, dict(lays), GAP,
          "; ".join("%s[%s] %s %.1f mm @%.3f" % (n, cls(n), l, ln, d) for n, l, ln, d in loud) or "none but GND / +3.3V"))
