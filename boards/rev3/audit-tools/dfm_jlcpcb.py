"""dfm_jlcpcb.py BOARD - JLCPCB-specific checks the first pass did not cover.

1. plated through-hole (component) holes to other-net copper: >=0.28 mm outer
   (0.35 recommended), >=0.30 mm on inner layers
2. vias to PTH/NPTH holes: filled-and-capped vias want >=0.45 mm to other holes
3. via to other mask openings (plugged vias want >=0.35 mm)
4. component-to-component spacing against JLCPCB's class table (outer pad or body edges)
5. silkscreen within 0.15 mm of exposed pads (removed at the fab) - polarity marks at risk
6. part bodies within 2.5 mm of the board edge, fiducials within 3.35 mm
"""
import sys, math, collections, re
sys.path.insert(0, "C:/Users/mleggiero/Documents/KiCad/taxelscan/tmp/usb-power-deps")
import pcbnew as K
from shapely.geometry import Point, Polygon, LineString, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

mm = K.ToMM
b = K.LoadBoard(sys.argv[1])


def polyset(ps):
    out = []
    for k in range(ps.OutlineCount()):
        ch = ps.COutline(k)
        pts = [(mm(ch.CPoint(i).x), mm(ch.CPoint(i).y)) for i in range(ch.PointCount())]
        if len(pts) >= 3:
            out.append(Polygon(pts).buffer(0))
    return unary_union(out) if out else Polygon()


LAY = {"F": K.F_Cu, "In1": K.In1_Cu, "In2": K.In2_Cu, "B": K.B_Cu}
# copper per layer, with net
cu = {l: [] for l in LAY}
for f in b.GetFootprints():
    for p in f.Pads():
        for l, lid in LAY.items():
            if p.IsOnLayer(lid) and p.GetAttribute() != K.PAD_ATTRIB_NPTH:
                g = polyset(p.GetEffectivePolygon(lid, K.ERROR_OUTSIDE))
                if not g.is_empty:
                    cu[l].append((g, p.GetNetname(), "%s.%s" % (f.GetReference(), p.GetNumber())))
vias = []
for t in b.GetTracks():
    if t.Type() == K.PCB_VIA_T:
        c = (mm(t.GetPosition().x), mm(t.GetPosition().y))
        g = Point(c).buffer(mm(t.GetWidth(K.F_Cu)) / 2, 32)
        vias.append((c, mm(t.GetDrillValue()) / 2, mm(t.GetWidth(K.F_Cu)) / 2, t.GetNetname()))
        for l in LAY:
            cu[l].append((g, t.GetNetname(), "via"))
    else:
        l = {K.F_Cu: "F", K.In2_Cu: "In2", K.B_Cu: "B"}.get(t.GetLayer())
        if l:
            a = (mm(t.GetStart().x), mm(t.GetStart().y)); e = (mm(t.GetEnd().x), mm(t.GetEnd().y))
            g = (LineString([a, e]) if a != e else Point(a)).buffer(mm(t.GetWidth()) / 2, 16)
            cu[l].append((g, t.GetNetname(), "track"))
# zone fills count as copper too
filler = K.ZONE_FILLER(b)
filler.Fill(b.Zones())
for z in b.Zones():
    if z.GetIsRuleArea():
        continue
    for l, lid in LAY.items():
        if z.IsOnLayer(lid):
            fp = z.GetFilledPolysList(lid)
            g = polyset(fp)
            if not g.is_empty:
                cu[l].append((g, z.GetNetname(), "zone"))
trees = {l: STRtree([g for g, _, _ in cu[l]]) for l in LAY}

# ---- 1 & 2: component holes
holes = []
for f in b.GetFootprints():
    for p in f.Pads():
        ds = p.GetDrillSize()
        if ds.x <= 0:
            continue
        hx, hy = mm(p.GetPosition().x), mm(p.GetPosition().y)
        dx, dy = mm(ds.x), mm(ds.y)
        if abs(dx - dy) > 1e-6:
            a = math.radians(p.GetOrientationDegrees())
            L, r = abs(dx - dy) / 2, min(dx, dy) / 2
            ux, uy = (math.cos(a), -math.sin(a)) if dx > dy else (math.sin(a), math.cos(a))
            g = LineString([(hx - ux * L, hy - uy * L), (hx + ux * L, hy + uy * L)]).buffer(r, 32)
        else:
            g = Point(hx, hy).buffer(dx / 2, 32)
        holes.append((g, p.GetNetname(), "%s.%s" % (f.GetReference(), p.GetNumber()), p.GetAttribute() == K.PAD_ATTRIB_NPTH))
print("== 1. component holes to other-net copper (JLCPCB: PTH >=0.28 mm outer, 0.35 recommended, >=0.30 inner; NPTH >=0.20)")
for g, net, ref, npth in holes:
    worst = {}
    for l in LAY:
        for k in trees[l].query(g.buffer(0.6)):
            cg, cnet, cref = cu[l][int(k)]
            if cnet == net and not npth:
                continue
            if cref.startswith(ref.split(".")[0] + ".") and cnet == net:
                continue
            d = g.distance(cg)
            if d < worst.get(l, (9, ""))[0]:
                worst[l] = (round(d, 3), "%s %s" % (cnet or "(no net)", cref))
    print("   %-8s %s %s" % (ref, "NPTH" if npth else "PTH ", {l: v for l, v in worst.items() if v[0] < 0.45}))

print("== 2. vias to component holes (filled-and-capped vias want >=0.45 mm to other PTH/NPTH)")
close = []
for c, rh, rp, net in vias:
    vh = Point(c).buffer(rh, 32)
    for g, hnet, ref, npth in holes:
        d = vh.distance(g)
        if d < 0.45:
            close.append((round(d, 3), ref, net, (round(c[0], 2), round(c[1], 2))))
print("   vias within 0.45 mm (hole edge to hole edge) of a component hole: %d %s" % (len(close), sorted(close)[:10]))

# ---- 3: via to mask openings of pads (plugged vias want >=0.35 mm)
mask_open = []
for f in b.GetFootprints():
    for p in f.Pads():
        if p.IsOnLayer(K.F_Mask):
            g = polyset(p.GetEffectivePolygon(K.F_Cu, K.ERROR_OUTSIDE)) if p.IsOnLayer(K.F_Cu) else None
            if g is not None and not g.is_empty:
                m = p.GetLocalSolderMaskMargin()
                exp = mm(m) if m else mm(b.GetDesignSettings().m_SolderMaskExpansion)
                mask_open.append((g.buffer(exp) if exp else g, "%s.%s" % (f.GetReference(), p.GetNumber())))
mt = STRtree([g for g, _ in mask_open])
cnt = collections.Counter()
for c, rh, rp, net in vias:
    vg = Point(c).buffer(rp, 32)
    ds_ = [vg.distance(mask_open[int(k)][0]) for k in mt.query(vg.buffer(0.35))]
    d = min(ds_) if ds_ else 9
    cnt["inside an opening" if d == 0 else ("<0.35 mm" if d < 0.35 else ">=0.35 mm")] += 1
print("== 3. vias vs pad mask openings (plugging wants >=0.35 mm):", dict(cnt))

# ---- 4: component spacing by JLCPCB class
def klass(f):
    n = str(f.GetFPID().GetLibItemName())
    if re.search(r"(C|R|L|LED)_(0201|0402|0603|0805|1206|1210)|_0806_|SOD-323|SOD-123", n):
        return "0201" if "0201" in n else "chip"
    if "SOT-23" in n or "SOT-323" in n or "SC-70" in n:
        return "SOT"
    if re.search(r"QFN|DFN", n):
        return "QFN"
    if re.search(r"SOIC|SOP|SSOP|TSSOP|MSOP", n):
        return "SOP"
    if n.startswith("Fiducial"):
        return None
    return "other"


RULE = {frozenset(["0201"]): 0.15, frozenset(["0201", "chip"]): 0.15, frozenset(["chip"]): 0.15,
        frozenset(["chip", "SOT"]): 0.2, frozenset(["0201", "SOT"]): 0.2,
        frozenset(["chip", "SOP"]): 0.4, frozenset(["0201", "SOP"]): 0.4,
        frozenset(["chip", "QFN"]): 1.0, frozenset(["0201", "QFN"]): 1.0,
        frozenset(["QFN", "SOP"]): 1.0, frozenset(["SOP"]): 0.5, frozenset(["SOT", "SOP"]): 0.4,
        frozenset(["SOT", "QFN"]): 1.0, frozenset(["SOT"]): 0.3}
parts = []
for f in b.GetFootprints():
    k = klass(f)
    if k is None:
        continue
    pads = unary_union([polyset(p.GetEffectivePolygon(K.F_Cu, K.ERROR_OUTSIDE)) for p in f.Pads() if p.IsOnLayer(K.F_Cu)])
    fab = [g for g in f.GraphicalItems() if g.GetLayer() == K.F_Fab and g.GetClass() == "PCB_SHAPE"]
    body = None
    if fab:
        bb = [g.GetBoundingBox() for g in fab]
        body = box(min(mm(r.GetX()) for r in bb), min(mm(r.GetY()) for r in bb), max(mm(r.GetRight()) for r in bb), max(mm(r.GetBottom()) for r in bb))
    outline = unary_union([pads] + ([body] if body is not None else []))
    parts.append((f.GetReference(), k, outline))
pt = STRtree([o for _, _, o in parts])
viol = collections.Counter()
examples = collections.defaultdict(list)
for i, (ra, ka, oa) in enumerate(parts):
    for j in pt.query(oa.buffer(1.0)):
        j = int(j)
        if j <= i:
            continue
        rb, kb, ob = parts[j]
        rule = RULE.get(frozenset([ka, kb]))
        if rule is None:
            continue
        d = oa.distance(ob)
        if d < rule - 1e-3:
            key = "%s-%s < %.2f" % tuple(sorted([ka, kb]) + [rule])
            viol[key] += 1
            if len(examples[key]) < 6:
                examples[key].append((ra, rb, round(d, 2)))
print("== 4. part pairs closer than JLCPCB's recommended spacing (pad/body edges):")
for k, v in sorted(viol.items()):
    print("   %-24s %3d  e.g. %s" % (k, v, examples[k]))

# ---- 5: silkscreen near exposed pads
pad_open = unary_union([g for g, _ in mask_open])
risk = collections.defaultdict(list)
for f in b.GetFootprints():
    for g in f.GraphicalItems():
        if g.GetLayer() != K.F_SilkS or g.GetClass() != "PCB_SHAPE":
            continue
        try:
            poly = polyset(g.GetEffectiveShape().ConvertToPolygon() if False else None)
        except Exception:
            poly = None
        bb = g.GetBoundingBox()
        w = mm(g.GetWidth())
        shp = g.GetShape()
        if shp in (K.SHAPE_T_SEGMENT,):
            geom = LineString([(mm(g.GetStart().x), mm(g.GetStart().y)), (mm(g.GetEnd().x), mm(g.GetEnd().y))]).buffer(max(w, 0.01) / 2)
        else:
            geom = box(mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom()))
        d = geom.distance(pad_open)
        if d < 0.15:
            kind = "polygon/marker" if shp == K.SHAPE_T_POLY else ("segment" if shp == K.SHAPE_T_SEGMENT else "other shape %d" % shp)
            risk[f.GetReference()].append((kind, round(d, 3)))
markers = {r: v for r, v in risk.items() if any(k == "polygon/marker" for k, _ in v)}
print("== 5. silkscreen within 0.15 mm of an exposed pad (the fab removes it): %d footprints; polarity-marker polygons at risk: %s" % (
    len(risk), {r: [x for x in v if x[0] == "polygon/marker"] for r, v in markers.items()}))
print("   footprints with outline segments at risk:", sorted(r for r, v in risk.items() if any(k == "segment" for k, _ in v)))

# ---- 6: edges
xs, ys = [], []
for d in b.GetDrawings():
    if d.GetLayer() == K.Edge_Cuts:
        for q in (d.GetStart(), d.GetEnd()):
            xs.append(mm(q.x)); ys.append(mm(q.y))
X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)
near = []
for ref, k, o in parts + [(f.GetReference(), "fid", unary_union([polyset(p.GetEffectivePolygon(K.F_Cu, K.ERROR_OUTSIDE)) for p in f.Pads()])) for f in b.GetFootprints() if f.GetReference().startswith("FID")]:
    x0, y0, x1, y1 = o.bounds
    d = min(x0 - X0, y0 - Y0, X1 - x1, Y1 - y1)
    lim = 3.35 if k == "fid" else 2.5
    if d < lim:
        near.append((round(d, 2), ref))
print("== 6. part pads/bodies within 2.5 mm of the edge (fiducials: 3.35 mm): %d %s" % (len(near), sorted(near)))
