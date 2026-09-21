"""zones_check.py board - each plane-zone fill polygon: area, and the same-net vias / through pads inside it"""
import sys
import pcbnew as K
b = K.LoadBoard(sys.argv[1])
K.ZONE_FILLER(b).Fill(b.Zones())
LAYER = {K.F_Cu: "F.Cu", K.In1_Cu: "In1.Cu", K.In2_Cu: "In2.Cu", K.B_Cu: "B.Cu"}
for z in b.Zones():
    if z.GetIsRuleArea():
        continue
    net = z.GetNetname()
    for lid in z.GetLayerSet().CuStack():
        polys = z.GetFilledPolysList(lid)
        print("zone %s on %s: island removal mode %s, %d polygon(s)" % (net, LAYER.get(lid, lid), z.GetIslandRemovalMode(), polys.OutlineCount()))
        for i in range(polys.OutlineCount()):
            ol = polys.Outline(i)
            area = ol.Area() / 1e12
            bb = ol.BBox()
            conns = []
            for t in b.GetTracks():
                if t.Type() == K.PCB_VIA_T and t.GetNetname() == net and polys.Contains(t.GetPosition(), i):
                    conns.append(("via", round(K.ToMM(t.GetPosition().x), 2), round(K.ToMM(t.GetPosition().y), 2)))
            for fp in b.GetFootprints():
                for p in fp.Pads():
                    if p.GetNetname() == net and p.IsOnLayer(lid) and polys.Contains(p.GetPosition(), i):
                        conns.append((fp.GetReference() + "." + p.GetNumber(),))
            print("   #%d %8.1f mm2  bbox x %.1f-%.1f y %.1f-%.1f  connections %d %s" % (
                i, area, K.ToMM(bb.GetX()), K.ToMM(bb.GetRight()), K.ToMM(bb.GetY()), K.ToMM(bb.GetBottom()),
                len(conns), conns[:4] if area < 100 else ""))
