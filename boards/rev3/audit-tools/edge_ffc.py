"""edge_ffc.py IN OUT - put the top board edge back on the FFC connectors' PCB-edge marks.

The FH12 footprints carry two short F.Fab marks where the board edge belongs; the
original placement had them exactly on the edge. Only the Edge.Cuts line and the plane
outlines move, so no copper gets closer to anything."""
import sys
import pcbnew as K
mm, MM = K.ToMM, K.FromMM
b = K.LoadBoard(sys.argv[1])


def marks(ref):
    f = b.FindFootprintByReference(ref)
    ys = []
    for g in f.GraphicalItems():
        if g.GetLayer() == K.F_Fab and g.GetClass() == "PCB_SHAPE":
            bb = g.GetBoundingBox()
            if abs(mm(bb.GetWidth()) - 0.7) < 0.05 and abs(mm(bb.GetHeight()) - 0.1) < 0.02 and mm(bb.GetY()) < mm(f.GetPosition().y) - 2.5:
                ys.append(round((mm(bb.GetY()) + mm(bb.GetBottom())) / 2, 4))
    return ys


y1, y2 = marks("J1"), marks("J2")
print("J1 edge marks", y1, "J2 edge marks", y2)
target = min(y1 + y2)
assert max(y1 + y2) - target < 0.01, "J1 and J2 disagree about the edge"
top = min(mm(p.y) for d in b.GetDrawings() if d.GetLayer() == K.Edge_Cuts for p in (d.GetStart(), d.GetEnd()))
print("top edge %.4f -> %.4f" % (top, target))
for d in b.GetDrawings():
    if d.GetLayer() == K.Edge_Cuts:
        for get, set_ in ((d.GetStart, d.SetStart), (d.GetEnd, d.SetEnd)):
            q = get()
            if abs(mm(q.y) - top) < 1e-4:
                set_(K.VECTOR2I(q.x, MM(target)))
for z in b.Zones():
    if z.GetIsRuleArea():
        continue
    o = z.Outline()
    for vi in range(o.TotalVertices()):
        q = o.CVertex(vi)
        if mm(q.y) < top + 1.0:
            o.SetVertex(vi, K.VECTOR2I(q.x, q.y - MM(top - target)))
b.Save(sys.argv[2])
print("saved", sys.argv[2])
