"""dfm_measure.py BOARD - measure what a fab and an assembler care about, from the board's exact geometry.

Tracks, gaps, vias, holes, rings, edge distances, silkscreen, solder-mask dams,
via-in-pad, part spacing, parts near or over the edge, through-hole parts."""
import sys, os, math, collections, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr
import pcbnew as K
from shapely.geometry import Point, LineString, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

mm = K.ToMM
BOARD = sys.argv[1]
m = lr.Model(BOARD)
b = m.b
out = {}

# ---- outline
xs, ys, ew = [], [], 0
for d in b.GetDrawings():
    if d.GetLayer() == K.Edge_Cuts:
        for q in (d.GetStart(), d.GetEnd()):
            xs.append(mm(q.x)); ys.append(mm(q.y))
        ew = max(ew, mm(d.GetWidth()))
X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)
ds = b.GetDesignSettings()
print("outline %.2f x %.2f mm (edge-line centres), edge line %.2f mm; board thickness %.2f mm; copper layers %d" % (
    X1 - X0, Y1 - Y0, ew, mm(ds.GetBoardThickness()), b.GetCopperLayerCount()))
inner = box(X0, Y0, X1, Y1)


def edge_dist(g):
    """distance from geometry to the outline (centre line)"""
    return inner.exterior.distance(g) if inner.contains(g) else -inner.exterior.distance(g)


# ---- tracks
wid = collections.Counter()
for it in m.items:
    if it["kind"] == "track":
        wid[(next(iter(it["lay"])), round(it["w"], 3))] += 1
print("track widths (layer, mm): count ->", dict(sorted(wid.items())))

# ---- copper gaps between different nets, per layer
gaps = {}
low = collections.Counter()
worst = []
for l in ("F", "In2", "B"):
    its, tree = m.by_layer[l]
    best = 9
    for i, a in enumerate(its):
        for j in tree.query(a["geom"].buffer(0.2)):
            j = int(j)
            if j <= i:
                continue
            c = its[j]
            if a["net"] == c["net"] or not a["net"] and not c["net"] and a.get("ref", "").split(".")[0] == c.get("ref", "").split(".")[0]:
                continue
            d = a["geom"].distance(c["geom"])
            if d < best:
                best = d
            for th in (0.10, 0.127, 0.15, 0.2):
                if d < th - 1e-4:
                    low[(l, th)] += 1
            if d < 0.1499:
                worst.append((round(d, 4), l, a["net"], a["ref"], c["net"], c["ref"]))
    gaps[l] = round(best, 4)
print("min copper gap between different nets per layer (mm):", gaps)
print("pairs closer than threshold (layer, mm): ", dict(sorted(low.items())))
kinds = collections.Counter((w[1], "pad-pad" if (".") in w[3] and "." in w[5] else "other") for w in worst)
print("pairs under 0.15 mm by layer/kind:", dict(kinds))
for w in sorted(worst)[:8]:
    print("   %.4f %s %s %s | %s %s" % w)

# ---- vias
vias = [it for it in m.items if it["kind"] == "via"]
vs = collections.Counter((round(v["size"], 3), round(v["drill"], 3)) for v in vias)
print("vias (pad, drill) -> count:", dict(vs), " annular rings:", sorted({round((s - d) / 2, 3) for s, d in vs}))
vt = STRtree([Point(v["xy"]) for v in vias])
hh_same, hh_diff = 9, 9
for i, v in enumerate(vias):
    for j in vt.query(Point(v["xy"]).buffer(1.0)):
        j = int(j)
        if j <= i:
            continue
        w = vias[j]
        d = math.dist(v["xy"], w["xy"]) - v["drill"] / 2 - w["drill"] / 2
        if v["net"] == w["net"]:
            hh_same = min(hh_same, d)
        else:
            hh_diff = min(hh_diff, d)
print("via hole-to-hole edge gap: same net %.3f mm, different nets %.3f mm" % (hh_same, hh_diff))

# ---- pads with holes, slots, NPTH
tht = collections.defaultdict(list)
for f in b.GetFootprints():
    for p in f.Pads():
        dsz = p.GetDrillSize()
        if dsz.x > 0:
            sz = p.GetSize(K.F_Cu) if hasattr(p, "GetSize") else p.GetSize()
            ring = (min(mm(sz.x), mm(sz.y)) - min(mm(dsz.x), mm(dsz.y))) / 2 if p.GetAttribute() != K.PAD_ATTRIB_NPTH else None
            tht[f.GetReference()].append(dict(num=p.GetNumber(), drill=(round(mm(dsz.x), 3), round(mm(dsz.y), 3)),
                                              pad=(round(mm(sz.x), 3), round(mm(sz.y), 3)),
                                              npth=p.GetAttribute() == K.PAD_ATTRIB_NPTH,
                                              ring=None if ring is None else round(ring, 3)))
for ref, lst in tht.items():
    print("holes %s: %s" % (ref, [(h["num"], "NPTH" if h["npth"] else "PTH", "drill %sx%s" % h["drill"], "pad %sx%s" % h["pad"], "ring %s" % h["ring"]) for h in lst]))
drills = sorted({min(h["drill"]) for lst in tht.values() for h in lst} | {round(v["drill"], 3) for v in vias})
print("distinct drill sizes (mm):", drills)

# ---- copper to edge
ed = collections.defaultdict(lambda: 9.0)
near = []
for it in m.items:
    if it["geom"] is None:
        continue
    d = edge_dist(it["geom"])
    for l in it["lay"]:
        ed[l] = min(ed[l], d)
    if d < 0.3:
        near.append((round(d, 3), it["kind"], it["net"], it.get("ref")))
print("min copper to outline centre line per layer (mm):", {k: round(v, 3) for k, v in ed.items()},
      "(subtract %.2f for the line's inner side)" % (ew / 2))
print("copper items within 0.30 mm of the outline centre line: %d" % len(near), sorted(near)[:10])

# ---- zones inset
for z in b.Zones():
    if not z.GetIsRuleArea():
        bb = z.GetBoundingBox()
        print("zone %s outline inset from edge: L %.2f T %.2f R %.2f B %.2f; min width %.2f, clearance %.2f" % (
            z.GetNetname(), mm(bb.GetX()) - X0, mm(bb.GetY()) - Y0, X1 - mm(bb.GetRight()), Y1 - mm(bb.GetBottom()),
            mm(z.GetMinThickness()), mm(z.GetLocalClearance() or 0)))

# ---- silkscreen
texts, lines = collections.Counter(), collections.Counter()
for f in b.GetFootprints():
    for t in [f.Reference(), f.Value()] + [g for g in f.GraphicalItems() if g.GetClass() in ("PCB_TEXT", "PCB_FIELD")]:
        if t.GetLayer() in (K.F_SilkS, K.B_SilkS) and t.IsVisible():
            texts[(round(mm(t.GetTextHeight()), 2), round(mm(t.GetTextThickness()), 3))] += 1
    for g in f.GraphicalItems():
        if g.GetLayer() in (K.F_SilkS, K.B_SilkS) and g.GetClass() == "PCB_SHAPE":
            lines[round(mm(g.GetWidth()), 3)] += 1
for d in b.GetDrawings():
    if d.GetLayer() in (K.F_SilkS, K.B_SilkS):
        if d.GetClass() == "PCB_SHAPE":
            lines[round(mm(d.GetWidth()), 3)] += 1
        else:
            texts[(round(mm(d.GetTextHeight()), 2), round(mm(d.GetTextThickness()), 3))] += 1
print("silk text (height, stroke) -> count:", dict(texts))
print("silk line widths -> count:", dict(sorted(lines.items())))

# ---- solder mask dams between pads of different nets (mask expansion from board + pad)
pads = [it for it in m.items if it["kind"] == "pad" and it["geom"] is not None and ("F" in it["lay"])]
pexp = {}
for f in b.GetFootprints():
    for p in f.Pads():
        exp = ds.m_SolderMaskExpansion
        lm = p.GetLocalSolderMaskMargin()
        if lm is not None:
            exp = lm
        pexp["%s.%s" % (f.GetReference(), p.GetNumber())] = mm(exp)
pt = STRtree([p["geom"] for p in pads])
dam_same_fp, dam_other_fp = 9, 9
dams = collections.Counter()
dworst = []
for i, a in enumerate(pads):
    for j in pt.query(a["geom"].buffer(0.4)):
        j = int(j)
        if j <= i:
            continue
        c = pads[j]
        if a["net"] and a["net"] == c["net"]:
            continue
        g = a["geom"].distance(c["geom"]) - pexp.get(a["ref"], 0) - pexp.get(c["ref"], 0)
        same = a["ref"].split(".")[0] == c["ref"].split(".")[0]
        if same:
            dam_same_fp = min(dam_same_fp, g)
        else:
            dam_other_fp = min(dam_other_fp, g)
            dworst.append((round(g, 3), a["ref"], c["ref"]))
        for th in (0.08, 0.1, 0.13, 0.2):
            if g < th - 1e-4:
                dams[(("same part" if same else "two parts"), th)] += 1
print("solder mask dam (pad-to-pad web, different nets): within a part min %.3f mm, between parts min %.3f mm" % (dam_same_fp, dam_other_fp))
print("dams below threshold:", dict(sorted(dams.items())))
print("closest pads of two different parts:", sorted(dworst)[:10])

# ---- via in pad
vip = []
smd = [(p, f) for f in b.GetFootprints() for p in f.Pads() if p.GetAttribute() == K.PAD_ATTRIB_SMD]
for v in b.GetTracks():
    if v.Type() != K.PCB_VIA_T:
        continue
    for p, f in smd:
        if p.HitTest(v.GetPosition()):
            vip.append((f.GetReference(), p.GetNumber(), v.GetNetname(), f.GetFPID().GetLibItemName().wx_str() if hasattr(f.GetFPID().GetLibItemName(), "wx_str") else str(f.GetFPID().GetLibItemName())))
            break
byfp = collections.Counter(x[3] for x in vip)
print("vias inside SMD pads: %d, by footprint: %s" % (len(vip), dict(byfp)))
# vias touching (not centred in) an SMD pad's copper
touch = 0
for it in vias:
    for p in pads:
        if p["net"] == it["net"] and p["geom"].intersects(it["geom"]) and not p["geom"].contains(Point(it["xy"])):
            touch += 1
            break
print("vias overlapping a same-net SMD pad edge (partly in pad): %d" % touch)

# ---- parts: courtyard spacing, edge distance, overhang
cy = {}
for f in b.GetFootprints():
    c = f.GetCourtyard(K.F_CrtYd)
    if c.OutlineCount():
        g = lr.polyset(c)
        cy[f.GetReference()] = g
refs = sorted(cy)
ct = STRtree([cy[r] for r in refs])
touching, close = [], []
for i, r in enumerate(refs):
    for j in ct.query(cy[r].buffer(0.3)):
        j = int(j)
        if j <= i:
            continue
        d = cy[r].distance(cy[refs[j]])
        if d < 1e-3:
            touching.append((r, refs[j]))
        elif d < 0.25:
            close.append((round(d, 3), r, refs[j]))
print("courtyard pairs touching (0 gap): %d; within 0.25 mm: %d" % (len(touching), len(close)))
print("   touching:", touching[:40])
edge_parts = []
for r in refs:
    g = cy[r]
    d = edge_dist(g)
    bx = g.bounds
    over = max(X0 - bx[0], Y0 - bx[1], bx[2] - X1, bx[3] - Y1, 0)
    if d < 5.0 or over > 0:
        edge_parts.append((round(d, 2), r, round(over, 2)))
print("parts whose courtyard is within 5 mm of the outline (distance, ref, overhang past edge):")
print("  ", sorted(edge_parts))
fps = [f for f in b.GetFootprints()]
thtparts = sorted({f.GetReference() for f in fps for p in f.Pads() if p.GetAttribute() in (K.PAD_ATTRIB_PTH,)})
print("parts with plated through-hole pads:", thtparts)
print("parts on the bottom side:", [f.GetReference() for f in fps if f.IsFlipped()])
sizes = collections.Counter()
for f in fps:
    n = str(f.GetFPID().GetLibItemName())
    for key in ("0201", "0402", "0603", "0805", "1206"):
        if key in n:
            sizes[key] += 1
print("passive package counts:", dict(sizes))
fids = [(f.GetReference(), round(mm(f.GetPosition().x), 2), round(mm(f.GetPosition().y), 2)) for f in fps if f.GetReference().startswith("FID")]
print("fiducials:", fids)
