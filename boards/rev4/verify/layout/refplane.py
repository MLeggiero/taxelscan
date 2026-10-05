"""refplane.py board - how much of each critical net runs over a solid reference plane.

Zones are refilled first. F.Cu tracks are checked against the In1 GND fill and B.Cu
tracks against the In2 +3.3V fill: the share of each track's copper (its width plus
0.1 mm each side) that is over filled plane."""
import os, sys, collections
import pcbnew as K
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

mm = K.ToMM
b = K.LoadBoard(sys.argv[1])
K.ZONE_FILLER(b).Fill(b.Zones())
LID = {"F": K.F_Cu, "B": K.B_Cu, "In1": K.In1_Cu, "In2": K.In2_Cu}


def fill(net, layer):
    out = []
    for z in b.Zones():
        if z.GetIsRuleArea() or z.GetNetname() != net:
            continue
        ps = z.GetFilledPolysList(LID[layer])
        for k in range(ps.OutlineCount()):
            ol = ps.Outline(k)
            pts = [(mm(ol.CPoint(i).x), mm(ol.CPoint(i).y)) for i in range(ol.PointCount())]
            holes = []
            for h in range(ps.HoleCount(k)):
                hl = ps.Hole(k, h)
                holes.append([(mm(hl.CPoint(i).x), mm(hl.CPoint(i).y)) for i in range(hl.PointCount())])
            if len(pts) >= 3:
                out.append(Polygon(pts, [h for h in holes if len(h) >= 3]).buffer(0))
    return unary_union(out)


ref = {"F": fill("GND", "In1"), "B": fill("+3.3V", "In2")}
NETS = sys.argv[2].split(",") if len(sys.argv) > 2 else [
    "SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B", "AMP_A", "AMP_B", "ADC_A", "ADC_B", "RAIL_MON", "XIN", "XOUT",
    "XOUT_MCU", "USB_D_P", "USB_D_N", "USBC_D_P", "USBC_D_N", "BUS_P", "BUS_N", "SYNC_P", "SYNC_N",
    "ROW_CLK", "ROW_CLK_MCU", "SWCLK", "VREG_LX", "SW_NODE"]
LN = {K.F_Cu: "F", K.B_Cu: "B", K.In1_Cu: "In1", K.In2_Cu: "In2"}
acc = collections.defaultdict(lambda: [0.0, 0.0, []])
for t in b.GetTracks():
    if t.Type() == K.PCB_VIA_T or t.GetNetname() not in NETS:
        continue
    l = LN.get(t.GetLayer())
    if l not in ref:
        continue
    a, c = (mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y))
    if a == c:
        continue
    g = LineString([a, c]).buffer(mm(t.GetWidth()) / 2 + 0.1, cap_style=2)
    over = g.intersection(ref[l]).area / g.area
    r = acc[(t.GetNetname(), l)]
    L = LineString([a, c]).length
    r[0] += L
    r[1] += L * over
    if over < 0.98:
        r[2].append((round(a[0], 2), round(a[1], 2), round(c[0], 2), round(c[1], 2), round(over, 2)))
for net in NETS:
    for l in ("F", "B"):
        if (net, l) in acc:
            L, Lo, bad = acc[(net, l)]
            print("%-11s %s %6.1f mm  %5.1f%% over %s%s" % (net, l, L, 100 * Lo / L, "In1 GND" if l == "F" else "In2 +3.3V",
                  ("  gaps: %s" % bad[:4]) if bad else ""))
