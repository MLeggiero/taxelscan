"""Dump every pad, track, via and zone of a board to JSON for querying.

  python.exe insp.py board.kicad_pcb out.json
"""
import sys, json
import pcbnew as K

b = K.LoadBoard(sys.argv[1])
mm = K.ToMM
LN = {K.F_Cu: "F", K.In1_Cu: "In1", K.In2_Cu: "In2", K.B_Cu: "B"}


def poly_pts(ps):
    out = []
    for k in range(ps.OutlineCount()):
        ch = ps.COutline(k)
        out.append([(mm(ch.CPoint(i).x), mm(ch.CPoint(i).y)) for i in range(ch.PointCount())])
    return out


fps, pads = [], []
for f in b.GetFootprints():
    bb = f.GetCourtyard(K.F_CrtYd).BBox() if f.GetCourtyard(K.F_CrtYd).OutlineCount() else f.GetBoundingBox(False)
    fps.append(dict(ref=f.GetReference(), x=mm(f.GetPosition().x), y=mm(f.GetPosition().y),
                    rot=f.GetOrientationDegrees(), val=f.GetValue(),
                    crt=[mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom())]))
    for p in f.Pads():
        lay = [LN[l] for l in LN if p.IsOnLayer(l)]
        try:
            poly = poly_pts(p.GetEffectivePolygon(K.F_Cu if p.IsOnLayer(K.F_Cu) else K.B_Cu, K.ERROR_INSIDE))
        except Exception:
            poly = None
        pads.append(dict(ref=f.GetReference(), num=p.GetNumber(), net=p.GetNetname(), uuid=p.m_Uuid.AsString(),
                         x=mm(p.GetPosition().x), y=mm(p.GetPosition().y),
                         sx=mm(p.GetSize().x), sy=mm(p.GetSize().y), ang=p.GetOrientationDegrees(),
                         shape=p.GetShape(), lay=lay, drill=mm(p.GetDrillSize().x),
                         smd=p.GetAttribute() == K.PAD_ATTRIB_SMD, poly=poly))
tracks, vias = [], []
for t in b.GetTracks():
    if isinstance(t, K.PCB_VIA):
        vias.append(dict(uuid=t.m_Uuid.AsString(), net=t.GetNetname(), x=mm(t.GetPosition().x), y=mm(t.GetPosition().y),
                         size=mm(t.GetWidth(K.F_Cu)), drill=mm(t.GetDrillValue())))
    elif isinstance(t, K.PCB_ARC):
        tracks.append(dict(uuid=t.m_Uuid.AsString(), net=t.GetNetname(), lay=LN.get(t.GetLayer(), "?"), arc=True,
                           x1=mm(t.GetStart().x), y1=mm(t.GetStart().y), x2=mm(t.GetEnd().x), y2=mm(t.GetEnd().y), w=mm(t.GetWidth())))
    else:
        tracks.append(dict(uuid=t.m_Uuid.AsString(), net=t.GetNetname(), lay=LN.get(t.GetLayer(), "?"),
                           x1=mm(t.GetStart().x), y1=mm(t.GetStart().y), x2=mm(t.GetEnd().x), y2=mm(t.GetEnd().y), w=mm(t.GetWidth())))
zones = []
for z in b.Zones():
    zones.append(dict(name=z.GetZoneName(), net=z.GetNetname(), rule=z.GetIsRuleArea(),
                      layers=[LN.get(l, str(l)) for l in z.GetLayerSet().Seq()],
                      outline=poly_pts(z.Outline()),
                      filled={LN.get(l, str(l)): round(sum(abs(a) for a in [z.GetFilledPolysList(l).Area()]) / 1e12, 1)
                              for l in z.GetLayerSet().Seq() if l in LN} if not z.GetIsRuleArea() else None))
e = b.GetBoardEdgesBoundingBox()
json.dump(dict(edge=[mm(e.GetX()), mm(e.GetY()), mm(e.GetRight()), mm(e.GetBottom())],
               fps=fps, pads=pads, tracks=tracks, vias=vias, zones=zones), open(sys.argv[2], "w"), indent=0)
print("fps", len(fps), "pads", len(pads), "tracks", len(tracks), "vias", len(vias), "zones", len(zones))
