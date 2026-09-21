"""fix5 -> fix6: give +5V_BUS the outer layers, and put the fiducials somewhere sane.

In fix5 ADDR2 took F/B first and +5V_BUS was left on In2, where 0.5 oz at 0.30 mm
is ~0.30 A against up to 0.70 A of chain current. The rail has the stronger claim,
so route it first and let the address strap take In2 if it must. Fiducials are
re-placed clear of copper AND of every courtyard."""
import sys, os, functools, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply, pour
import pcbnew as K
from shapely.geometry import Point, box
from shapely.ops import unary_union
lr.PRO_REF = lr.SCR + "/start-backup/rev3.kicad_pro"
W = lr.SCR + "/work/t49w.kicad_pcb"
BASE = lr.SCR + "/work/fix5.kicad_pcb"
OUT = lr.SCR + "/work/fix6.kicad_pcb"
SITES = [(x, y) for y in (119.59, 120.72, 121.85) for x in (125.37, 126.50, 127.63)]
EPKO = {l: unary_union([Point(*s).buffer(0.45) for s in SITES]) for l in ("In2", "B")}
FB, LT = (("F", "B"),), (("F", "B"), ("F", "In2", "B"))
rr2.route2 = functools.partial(rr2.route2, tlimit=120)

m = lr.Model(BASE)
cur = pour.pieces(m.b)
print("start: pour %d of %d" % (cur["on_main"], cur["total"]), flush=True)

fids = [m.b.FindFootprintByReference(r) for r in ("FID1", "FID2", "FID3")]
fids = [f for f in fids if f]
print("existing fiducials to move:", len(fids), flush=True)

old5, oldA = grp.net_len(m, "+5V_BUS"), grp.net_len(m, "ADDR2")
m.remove([it["uuid"] for it in m.items if it["kind"] in ("track", "via") and it["net"] in ("+5V_BUS", "ADDR2")]); m.index()
ok5 = pairs_apply.connect_net(m, "+5V_BUS", 0.30, layers_try=FB)
in2 = sum(i["geom"].length for i in m.net_items("+5V_BUS", ("track",)) if i["lay"] == {"In2"})
print("+5V_BUS on F/B: %s  %s -> %s, In2 %.1f mm" % (ok5, tuple(round(v,1) for v in old5), tuple(round(v,1) for v in grp.net_len(m, "+5V_BUS")), in2), flush=True)
if not ok5:
    pairs_apply.connect_net(m, "+5V_BUS", 0.30, layers_try=LT)
    print("   fell back to F/In2/B", flush=True)
got = False
for tag, lt in (("F/B", FB), ("F/In2/B", LT)):
    for mg in (2.0, 5.0, 8.0):
        if grp.connect_all(m, "ADDR2", margin=mg, layers_try=lt, keepout=EPKO):
            got = tag
            break
    if got:
        break
print("ADDR2 on %s: %s -> %s" % (got, tuple(round(v,1) for v in oldA), tuple(round(v,1) for v in grp.net_len(m, "ADDR2"))), flush=True)

# fiducials clear of copper and of every courtyard
e = m.edge
yards = []
for f in m.b.GetFootprints():
    if f.GetReference().startswith("FID"):
        continue
    bb = f.GetBoundingBox(False, False)
    yards.append(box(K.ToMM(bb.GetX()), K.ToMM(bb.GetY()), K.ToMM(bb.GetRight()), K.ToMM(bb.GetBottom())))
yards = unary_union(yards)
its, tree = m.by_layer["F"]
holes, htree = m.holes
clear = []
for xi in range(int(e[0] * 4) + 10, int(e[2] * 4) - 10):
    for yi in range(int(e[1] * 4) + 10, int(e[3] * 4) - 10):
        p = Point(xi / 4.0, yi / 4.0)
        if yards.distance(p) < 0.6:
            continue
        if any(its[int(k)]["geom"].distance(p) < 1.7 for k in tree.query(p.buffer(1.7))):
            continue
        if any(holes[int(k)]["hole"].distance(p) < 1.7 for k in htree.query(p.buffer(1.7))):
            continue
        clear.append((p.x, p.y))
print("clear-of-everything fiducial sites:", len(clear), flush=True)
chosen = []
for want in ((e[0], e[3]), (e[2], e[3]), (e[2], e[1]), (e[0], e[1])):
    cands = [c for c in clear if all((c[0]-o[0])**2 + (c[1]-o[1])**2 > 225 for o in chosen)]
    if cands:
        chosen.append(min(cands, key=lambda c: (c[0]-want[0])**2 + (c[1]-want[1])**2))
    if len(chosen) == 3:
        break
for i, (x, y) in enumerate(chosen, 1):
    if i <= len(fids):
        fp = fids[i - 1]
    else:
        fp = K.FootprintLoad("C:/Program Files/KiCad/10.0/share/kicad/footprints/Fiducial.pretty", "Fiducial_1mm_Mask2mm")
        fp.SetReference("FID%d" % i); fp.Reference().SetVisible(False)
        fp.SetValue("Fiducial"); fp.Value().SetVisible(False)
        m.b.Add(fp)
    fp.SetPosition(K.VECTOR2I(K.FromMM(x), K.FromMM(y)))
m.index()
print("fiducials at", chosen, flush=True)

d, c = pairs_apply.close_loop(m, W, rounds=3, use_rrr=False)
d, c = rr2.prune(m, W, "t49", rounds=16)
lr.summary(d, c)
m.save(OUT)
mm = lr.Model(OUT)
r = pour.pieces(mm.b)
ep = mm.pad("U9", "61")
print("end: pour %d of %d; %d vias in U9's pad; +5V_BUS In2 %.1f mm" % (
    r["on_main"], r["total"],
    sum(1 for it in mm.net_items("GND", ("via",)) if ep["geom"].contains(it["geom"].centroid)),
    sum(i["geom"].length for i in mm.net_items("+5V_BUS", ("track",)) if i["lay"] == {"In2"})))
