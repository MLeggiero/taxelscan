"""syncde_route.py IN OUT - place R37 and route SYNC_DE + R37's ground, trying candidate
spots in order until everything routes and the +3.3V pour keeps its connections.

SYNC_DE joins U9.17 (GPIO13), U11.3 (DE) and R37.1; F/B first, In2 only as a fallback.
R37.2 joins the nearest ground copper on F/B."""
import sys, os, math, functools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pour
import numpy as np
import pcbnew as K
from shapely.geometry import box
from shapely import affinity
from shapely.ops import unary_union
from shapely.strtree import STRtree

IN, OUT = sys.argv[1], sys.argv[2]
mm, MM = K.ToMM, K.FromMM
rr2.route2 = functools.partial(rr2.route2, tlimit=90)
POUR_REQUIRED = 27

# ---- legal spots (same test as syncde_place.py), ordered by pad 1's distance to U11.3
m0 = lr.Model(IN)
b0 = m0.b
r = b0.FindFootprintByReference("R37")
loc = [(p.GetNumber(), mm(p.GetFPRelativePosition().x), mm(p.GetFPRelativePosition().y),
        mm((p.GetSize(K.F_Cu) if hasattr(p, "GetSize") else p.GetSize()).x),
        mm((p.GetSize(K.F_Cu) if hasattr(p, "GetSize") else p.GetSize()).y)) for p in r.Pads()]
cyb = r.GetCourtyard(K.F_CrtYd).BBox()
cx0, cy0 = mm(cyb.GetX()) - mm(r.GetPosition().x), mm(cyb.GetY()) - mm(r.GetPosition().y)
cw, chh = mm(cyb.GetWidth()), mm(cyb.GetHeight())
courts = [lr.polyset(f.GetCourtyard(K.F_CrtYd)) for f in b0.GetFootprints()
          if f.GetReference() != "R37" and f.GetCourtyard(K.F_CrtYd).OutlineCount()]
ctree = STRtree(courts)
its, tree = m0.by_layer["F"]
hs, htree = m0.holes
x0, y0, x1, y1 = m0.edge
u11_3 = m0.pad("U11", "3")


def shapes(x, y, rot):
    out = {}
    for num, ox, oy, sx, sy in loc:
        g = affinity.rotate(box(ox - sx / 2, oy - sy / 2, ox + sx / 2, oy + sy / 2), -rot, origin=(0, 0))
        out[num] = affinity.translate(g, x, y)
    c = affinity.translate(affinity.rotate(box(cx0, cy0, cx0 + cw, cy0 + chh), -rot, origin=(0, 0)), x, y)
    return out, c


cands = []
for x in np.arange(109.0, 121.01, 0.05):
    for y in np.arange(121.5, 127.51, 0.05):
        for rot in (0, 90):
            pads, court = shapes(x, y, rot)
            bx = court.bounds
            if bx[0] < x0 + 0.3 or bx[1] < y0 + 0.3 or bx[2] > x1 - 0.3 or bx[3] > y1 - 0.3:
                continue
            if any(courts[int(k)].intersection(court).area > 1e-6 for k in ctree.query(court)):
                continue
            ok = True
            for num, g in pads.items():
                net = "SYNC_DE" if num == "1" else "GND"
                for k in tree.query(g.buffer(0.16)):
                    it = its[int(k)]
                    if (it["kind"] == "via" or it["net"] != net) and it["geom"].distance(g) < 0.15:
                        ok = False
                        break
                if ok:
                    for k in htree.query(g.buffer(0.26)):
                        if hs[int(k)]["hole"].distance(g) < 0.25:
                            ok = False
                            break
                if not ok:
                    break
            if ok:
                cands.append((pads["1"].distance(u11_3["geom"]), float(x), float(y), rot))
cands.sort()
picked = []
for c in cands:
    if all(math.hypot(c[1] - q[1], c[2] - q[2]) > 0.6 or c[3] != q[3] for q in picked):
        picked.append(c)
print("%d legal spots, %d distinct candidates to try" % (len(cands), len(picked)), flush=True)


def gnd_connect(m):
    cs = rr2.comps(m, "GND")
    mine = [c for c in cs if any(it["kind"] == "pad" and it["ref"] == "R37.2" for it in c)]
    if not mine:
        return False
    if len(mine[0]) > 1:
        return True          # already touching ground copper
    others = [c for c in cs if c is not mine[0]]
    for mg in (1.0, 2.0, 3.0):
        res, win = rr2.connect_once(m, "GND", [mine[0]] + others, 0.15, ("F", "B"), mg)
        if res and not res[2]:
            rr2.commit(m, "GND", res[0], res[1], 0.15)
            return True
    return False


for n_try, (d1, x, y, rot) in enumerate(picked[:25]):
    m = lr.Model(IN)
    f = m.b.FindFootprintByReference("R37")
    f.SetOrientationDegrees(float(rot))
    f.SetPosition(K.VECTOR2I(MM(x), MM(y)))
    m.index()
    ok_sig = grp.connect_all(m, "SYNC_DE", margin=2.0, layers_try=(("F", "B"),))
    layers_used = "F/B"
    if not ok_sig:
        m = lr.Model(IN)
        f = m.b.FindFootprintByReference("R37")
        f.SetOrientationDegrees(float(rot))
        f.SetPosition(K.VECTOR2I(MM(x), MM(y)))
        m.index()
        ok_sig = grp.connect_all(m, "SYNC_DE", margin=2.0, layers_try=(("F", "B"), ("F", "In2", "B")))
        layers_used = "F/In2/B"
    if not ok_sig:
        print("  try %d (%.2f, %.2f) rot %d: SYNC_DE does not route" % (n_try, x, y, rot), flush=True)
        continue
    if not gnd_connect(m):
        print("  try %d (%.2f, %.2f) rot %d: R37.2 cannot reach ground" % (n_try, x, y, rot), flush=True)
        continue
    L, nv = grp.net_len(m, "SYNC_DE")
    in2 = sum(it["geom"].length / 2 for it in m.net_items("SYNC_DE", ("track",)) if it["lay"] == {"In2"})
    pr = pour.pieces(m.b)
    print("  try %d (%.2f, %.2f) rot %d: routed on %s, SYNC_DE %.1f mm %d vias; +3.3V pour main %.0f mm2 holds %d of %d" % (
        n_try, x, y, rot, layers_used, L, nv, pr["main_area"], pr["on_main"], pr["total"]), flush=True)
    if pr["on_main"] < POUR_REQUIRED:
        print("     pour lost connections - rejected", flush=True)
        continue
    m.save(OUT)
    print("saved", OUT)
    break
else:
    raise SystemExit("no candidate worked")
