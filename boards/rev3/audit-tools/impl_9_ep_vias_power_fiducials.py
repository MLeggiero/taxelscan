"""fix0 (the installed board) -> fix5, in one pass.

  1. ADDR1, ADDR2 and MUX_S0 are re-routed clear of the nine via sites in U9's
     exposed pad, on F/B if they will take it so the +3.3V pour keeps its copper.
  2. Ground vias go into every site that is then free.
  3. +5V_BUS and +5V_USB come off In2 (0.5 oz inner at 0.30 mm is ~0.30 A for a
     10 C rise; the chain draws up to 0.70 A) - on F/B, 1 oz, that is ~1 A.
  4. USB_BUS_SW is widened; the whole chain current crosses it.
  5. Three fiducials go where the copper is genuinely clear.
Every step is kept only if it does not cost +3.3V vias on the main pour piece."""
import sys, os, functools, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply, pour
import pcbnew as K
from shapely.geometry import Point, box
from shapely.ops import unary_union
lr.PRO_REF = lr.SCR + "/start-backup/rev3.kicad_pro"
W = lr.SCR + "/work/t48w.kicad_pcb"
BASE = lr.SCR + "/work/fix0.kicad_pcb"
OUT = lr.SCR + "/work/fix5.kicad_pcb"
SITES = [(x, y) for y in (119.59, 120.72, 121.85) for x in (125.37, 126.50, 127.63)]
EPKO = {l: unary_union([Point(*s).buffer(0.45) for s in SITES]) for l in ("In2", "B")}
FB, LT = (("F", "B"),), (("F", "B"), ("F", "In2", "B"))
rr2.route2 = functools.partial(rr2.route2, tlimit=90)


def snap(m, net):
    t, v = collections.defaultdict(list), collections.defaultdict(list)
    for it in m.net_items(net, ("track", "via")):
        if it["kind"] == "track":
            t[round(it["w"], 4)].append((next(iter(it["lay"])), it["a"], it["c"]))
        else:
            v[(round(it["geom"].bounds[2]-it["geom"].bounds[0], 3), round(it["hole"].bounds[2]-it["hole"].bounds[0], 3))].append(it["xy"])
    return t, v


def restore(m, net, s):
    m.remove([it["uuid"] for it in m.net_items(net, ("track", "via"))]); m.index()
    t, v = s
    for w, segs in t.items():
        m.add(net, segs, [], w)
    for size, xy in v.items():
        m.add(net, [], xy, 0.1, size)
    m.index()


m = lr.Model(BASE)
cur = pour.pieces(m.b)
print("start: %d of %d +3.3V vias on the main piece, %d pieces" % (cur["on_main"], cur["total"], cur["pieces"]), flush=True)

# 1. clear the pad's via sites
for net in ("ADDR1", "ADDR2", "MUX_S0"):
    s, old = snap(m, net), grp.net_len(m, net)
    m.remove([it["uuid"] for it in m.net_items(net, ("track", "via"))]); m.index()
    got = None
    for tag, lt in (("F/B", FB), ("F/In2/B", LT)):
        for mg in (2.0, 5.0, 8.0):
            if grp.connect_all(m, net, margin=mg, layers_try=lt, keepout=EPKO):
                got = tag
                break
        if got:
            break
    new = pour.pieces(m.b) if got else None
    if got and new["on_main"] >= cur["on_main"] - 1:
        print("%-7s clear of the pad on %-8s %s -> %s; pour %d" % (net, got, tuple(round(v,1) for v in old), tuple(round(v,1) for v in grp.net_len(m, net)), new["on_main"]), flush=True)
        cur = new
    else:
        restore(m, net, s)
        print("%-7s left as it was (%s)" % (net, "no route" if not got else "pour would drop to %d" % new["on_main"]), flush=True)

free = [s for s in SITES if not m.check_via("GND", s[0], s[1], 0.5, 0.3)]
m.add("GND", [], free, 0.15, (0.5, 0.3)); m.index()
print("ground vias into U9's pad: %d %s" % (len(free), free), flush=True)

# 2. the 5 V rails off In2
for net in ("+5V_BUS", "+5V_USB"):
    s, old = snap(m, net), grp.net_len(m, net)
    in2 = sum(i["geom"].length for i in m.net_items(net, ("track",)) if i["lay"] == {"In2"})
    m.remove([it["uuid"] for it in m.net_items(net, ("track", "via"))]); m.index()
    if pairs_apply.connect_net(m, net, 0.30, layers_try=FB):
        print("%-8s off In2 (%.1f mm was inner): %s -> %s" % (net, in2, tuple(round(v,1) for v in old), tuple(round(v,1) for v in grp.net_len(m, net))), flush=True)
    else:
        restore(m, net, s)
        print("%-8s would not route on F/B - left as it was" % net, flush=True)

# 3. the switch output carries the whole chain
for it in [x for x in m.net_items("USB_BUS_SW", ("track",)) if x["lay"] == {"F"}]:
    for w in (0.40, 0.30):
        if w > it["w"] and not m.check_track("USB_BUS_SW", "F", it["a"], it["c"], w, ignore=frozenset([it["uuid"]])):
            m.remove([it["uuid"]]); m.add("USB_BUS_SW", [("F", it["a"], it["c"])], [], w); m.index()
            break

# 4. fiducials, only where nothing is near
e = m.edge
grid = [(x / 4.0, y / 4.0) for x in range(int(e[0] * 4) + 8, int(e[2] * 4) - 8)
        for y in range(int(e[1] * 4) + 8, int(e[3] * 4) - 8)]
its, tree = m.by_layer["F"]
holes, htree = m.holes
clear = []
for x, y in grid:
    p = Point(x, y)
    if any(its[int(k)]["geom"].distance(p) < 1.7 for k in tree.query(p.buffer(1.7))):
        continue
    if any(holes[int(k)]["hole"].distance(p) < 1.7 for k in htree.query(p.buffer(1.7))):
        continue
    clear.append((x, y))
print("clear fiducial sites found:", len(clear), flush=True)
chosen = []
for want in ((e[0], e[3]), (e[2], e[3]), (e[0], e[1]), (e[2], e[1])):
    cands = [c for c in clear if all((c[0]-o[0])**2 + (c[1]-o[1])**2 > 100 for o in chosen)]
    if not cands:
        continue
    chosen.append(min(cands, key=lambda c: (c[0]-want[0])**2 + (c[1]-want[1])**2))
    if len(chosen) == 3:
        break
for i, (x, y) in enumerate(chosen, 1):
    fp = K.FootprintLoad("C:/Program Files/KiCad/10.0/share/kicad/footprints/Fiducial.pretty", "Fiducial_1mm_Mask2mm")
    fp.SetPosition(K.VECTOR2I(K.FromMM(x), K.FromMM(y)))
    fp.SetReference("FID%d" % i); fp.Reference().SetVisible(False)
    fp.SetValue("Fiducial"); fp.Value().SetVisible(False)
    m.b.Add(fp)
m.index()
print("fiducials at", chosen, flush=True)

d, c = pairs_apply.close_loop(m, W, rounds=3, use_rrr=False)
d, c = rr2.prune(m, W, "t48", rounds=16)
lr.summary(d, c)
m.save(OUT)
mm = lr.Model(OUT)
r = pour.pieces(mm.b)
ep = mm.pad("U9", "61")
print("end: pour %d of %d on the main piece (%d pieces); %d vias in U9's pad" % (
    r["on_main"], r["total"], r["pieces"], sum(1 for it in mm.net_items("GND", ("via",)) if ep["geom"].contains(it["geom"].centroid))))
