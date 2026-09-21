import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp
import numpy as np
import shapely
from scipy.ndimage import label
from shapely.geometry import box, MultiPoint
GB = lr.SCR + "/work/group_base_r34.kicad_pcb"
m = lr.Model(GB)
for n in ("ROW_LATCH_MCU", "ROW_CLK_MCU"):
    ok = grp.connect_all(m, n)
    its = m.net_items(n, ("track", "via"))
    print(n, ok, [(it["kind"], "".join(sorted(it["lay"]))[:3], it.get("a"), it.get("c"), it.get("xy")) for it in its])
m.b.Save(lr.SCR + "/work/diag_r34.kicad_pcb")
w, g = 0.1, 0.025
win = (116.8, 113.6, 124.8, 121.4)
M = m.masks("ROW_DATA", win, g, w, frozenset(), (0.5, 0.3))
X, Y = M["X"], M["Y"]
def cells(mask):
    ii, jj = np.nonzero(mask)
    return [(round(win[0] + j * g, 3), round(win[1] + i * g, 3)) for i, j in zip(ii, jj)]
def bbox(mask):
    c = cells(mask)
    if not c: return None
    xs = [p[0] for p in c]; ys = [p[1] for p in c]
    return (min(xs), min(ys), max(xs), max(ys))
def pocket(layer, pad):
    t = pad["geom"].buffer(-w / 2 + 0.004)
    seed = shapely.intersects_xy(t, X, Y) & M["free"][layer]
    lab, _ = label(M["free"][layer], structure=np.ones((3, 3)))
    ids = [k for k in np.unique(lab[seed]) if k]
    return np.isin(lab, ids)
A = pocket("F", m.pad("U9", "5")); B = pocket("F", m.pad("U1", "14"))
va, vb = A & M["via_ok"], B & M["via_ok"]
print("U9.5 pocket: %d cells bbox %s, via-legal %d" % (A.sum(), bbox(A), va.sum()))
print("U1.14 pocket: %d cells bbox %s, via-legal %d" % (B.sum(), bbox(B), vb.sum()))
if va.sum(): print("   via sites near U9.5 (sample):", cells(va)[::max(1, int(va.sum() // 12))][:12])
if vb.sum(): print("   via sites near U1.14 (sample):", cells(vb)[::max(1, int(vb.sum() // 12))][:12])
for inner in ("In2", "B"):
    lab, _ = label(M["free"][inner], structure=np.ones((3, 3)))
    la = {k for k in np.unique(lab[va]) if k}; lb = {k for k in np.unique(lab[vb]) if k}
    print(inner, "via-to-via connected:", bool(la & lb))
if A.sum() < 4000:
    poly = MultiPoint(cells(A)).buffer(g * 0.75).buffer(0)
    around = m.near(poly, 0.45, layers=["F"])
    print("F items bounding the U9.5 pocket:")
    for it in sorted(around, key=lambda it: it["net"]):
        print("   %-14s %-5s %s" % (it["net"], it["kind"], it.get("ref") if it["kind"] == "pad" else (it.get("xy") or (it.get("a"), it.get("c")))))
