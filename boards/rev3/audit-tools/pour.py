"""pour.py - pieces of a plane pour, computed from the board's copper (no zone fill).

The pour's free area is its zone outline minus every other net's copper on that layer
(tracks, vias, through pads) grown by the zone clearance, opened by half the zone's
minimum width - which reproduces KiCad's fill closely enough to say which of the pour
net's vias land on the main piece. Works on a live pcbnew BOARD, so a router script
can check the effect of a change without saving and refilling."""
import sys
sys.path.insert(0, "C:/Users/mleggiero/Documents/KiCad/taxelscan/tmp/usb-power-deps")
import pcbnew as K
from shapely.geometry import Polygon, Point, LineString, box
from shapely.ops import unary_union


def pieces(b, net="+3.3V", layer=None, skip=()):
    layer = K.In2_Cu if layer is None else layer
    mm = K.ToMM
    zone = next(z for z in b.Zones() if not z.GetIsRuleArea() and z.GetNetname() == net and z.IsOnLayer(layer))
    clr = max(mm(zone.GetLocalClearance() or 0), mm(b.GetDesignSettings().m_MinClearance), 0.2)
    minw = mm(zone.GetMinThickness())
    ol = zone.Outline().Outline(0)
    outline = Polygon([(mm(ol.CPoint(i).x), mm(ol.CPoint(i).y)) for i in range(ol.PointCount())]).buffer(0)
    obst, anchors = [], []
    for t in b.GetTracks():
        n = t.GetNetname()
        if t.Type() == K.PCB_VIA_T:
            p = (mm(t.GetPosition().x), mm(t.GetPosition().y))
            if n == net:
                anchors.append(p)
            elif n not in skip:
                obst.append(Point(p).buffer(mm(t.GetWidth(layer)) / 2 + clr, 16))
        elif t.GetLayer() == layer and n != net and n not in skip:
            a = (mm(t.GetStart().x), mm(t.GetStart().y))
            c = (mm(t.GetEnd().x), mm(t.GetEnd().y))
            g = LineString([a, c]) if a != c else Point(a)
            obst.append(g.buffer(mm(t.GetWidth()) / 2 + clr, 8))
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.IsOnLayer(layer) and p.GetNetname() != net and p.GetNetname() not in skip:
                bb = p.GetBoundingBox()
                obst.append(box(mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom())).buffer(clr))
            elif p.GetNetname() == net and p.GetDrillSize().x > 0:
                anchors.append((mm(p.GetPosition().x), mm(p.GetPosition().y)))
    free = outline.difference(unary_union(obst)).buffer(-minw / 2 + 1e-4).buffer(minw / 2 - 1e-4)
    polys = [free] if free.geom_type == "Polygon" else list(free.geoms)
    main = max(polys, key=lambda p: p.area)
    grown = main.buffer(0.05)
    on_main = [v for v in anchors if grown.contains(Point(v))]
    return {"main_area": main.area, "on_main": len(on_main), "total": len(anchors),
            "off": [v for v in anchors if v not in on_main], "pieces": len(polys),
            "polys": polys, "main": main}


if __name__ == "__main__":
    r = pieces(K.LoadBoard(sys.argv[1]))
    print("main piece %.0f mm2 holds %d of %d; off: %s" % (r["main_area"], r["on_main"], r["total"], r["off"]))
