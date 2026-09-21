"""stats.py board.kicad_pcb [net ...]

The numbers ROUTING_STATUS.md quotes: segment and via counts, vias per plane net,
copper per layer, and length / vias / layers for the nets the status tracks."""
import sys, collections
import pcbnew as K

NETS = ["BUS_P", "BUS_N", "SYNC_P", "SYNC_N", "USBC_D_P", "USBC_D_N", "USB_D_P", "USB_D_N",
        "VCORE", "+5V", "+5V_BUS", "+5V_USB", "SENSE_A", "SENSE_B", "ADC_A", "ADC_B", "AMP_B",
        "XIN", "XOUT", "XOUT_MCU", "VREG_LX", "ROW_DATA", "BUS_DI", "USB_VBUS_DET",
        "ADDR0", "ADDR1", "ADDR2", "ADC_SDO", "ADC_SCK", "ADC_SDI", "USB_ILIM", "USB_CC_OUT2",
        "ROW_CLK", "ROW_LATCH", "ROW_CLK_MCU", "ROW_LATCH_MCU", "FB"]
LAYER = {K.F_Cu: "F.Cu", K.In1_Cu: "In1.Cu", K.In2_Cu: "In2.Cu", K.B_Cu: "B.Cu"}

b = K.LoadBoard(sys.argv[1])
per_layer = collections.Counter()
length = collections.defaultdict(collections.Counter)
vias = collections.Counter()
n_tracks = n_vias = 0
for t in b.GetTracks():
    net = t.GetNetname()
    if t.Type() == K.PCB_VIA_T:
        n_vias += 1
        vias[net] += 1
        continue
    n_tracks += 1
    lay = LAYER.get(t.GetLayer(), str(t.GetLayer()))
    L = K.ToMM(t.GetLength())
    per_layer[lay] += L
    length[net][lay] += L

print("tracks %d  vias %d  (GND %d, +3.3V %d)" % (n_tracks, n_vias, vias["GND"], vias["+3.3V"]))
print("copper per layer  " + "   ".join("%s %.0f mm" % (l, per_layer[l]) for l in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")))
for n in sys.argv[2:] or NETS:
    lays = length[n]
    print("%-14s %6.1f mm %2d vias   %s" % (n, sum(lays.values()), vias[n],
                                          "  ".join("%s %.1f" % (l, v) for l, v in sorted(lays.items()))))
for p, q in (("BUS_P", "BUS_N"), ("SYNC_P", "SYNC_N"), ("USBC_D_P", "USBC_D_N"), ("USB_D_P", "USB_D_N")):
    a, c = sum(length[p].values()), sum(length[q].values())
    print("skew %-8s %.2f mm" % (p[:-2], abs(a - c)))

# zone fills: how many separate polygons each plane zone fills into, per layer
K.ZONE_FILLER(b).Fill(b.Zones())
for z in b.Zones():
    if z.GetIsRuleArea():
        continue
    for lid in z.GetLayerSet().CuStack():
        polys = z.GetFilledPolysList(lid)
        n = polys.OutlineCount()
        areas = sorted((polys.Outline(i).Area() / 1e12 for i in range(n)), reverse=True)
        print("zone %-6s on %-6s %d fill polygon(s)  areas mm2 %s" % (z.GetNetname(), LAYER.get(lid, lid), n,
                                                                    [round(a, 1) for a in areas[:6]]))
