"""syncde_prep.py IN OUT R37_UUID - SYNC hardware fix, board side, step 1 (no routing yet):
new net SYNC_DE on U9.17 (GPIO13) and U11.3 (DE); U11.3's GND stub and via removed;
R37 added (a copy of R31: 10k 0402) linked to its schematic symbol, parked off-board."""
import sys, math
import pcbnew as K

IN, OUT, UUID = sys.argv[1], sys.argv[2], sys.argv[3]
mm, MM = K.ToMM, K.FromMM
b = K.LoadBoard(IN)
ni = K.NETINFO_ITEM(b, "SYNC_DE")
b.Add(ni)
ni.thisown = False
net = b.FindNet("SYNC_DE")

gone = 0
for t in list(b.GetTracks()):
    if t.GetNetname() != "GND":
        continue
    if t.Type() == K.PCB_VIA_T:
        p = t.GetPosition()
        if math.hypot(mm(p.x) - 113.75, mm(p.y) - 124.264) < 0.01:
            b.Remove(t); gone += 1
    else:
        for q in (t.GetStart(), t.GetEnd()):
            if math.hypot(mm(q.x) - 114.015, mm(q.y) - 125.189) < 0.01:
                b.Remove(t); gone += 1
                break
print("removed U11.3 GND stub items:", gone)

u11 = b.FindFootprintByReference("U11")
u9 = b.FindFootprintByReference("U9")
for fp, num in ((u11, "3"), (u9, "17")):
    pad = next(p for p in fp.Pads() if p.GetNumber() == num)
    print("%s.%s: %r -> SYNC_DE" % (fp.GetReference(), num, pad.GetNetname()))
    pad.SetNet(net)

src = b.FindFootprintByReference("R31")
r37 = K.Cast_to_FOOTPRINT(src.Duplicate(False)) if hasattr(K, "Cast_to_FOOTPRINT") else src.Duplicate(False)
r37.SetReference("R37")
r37.SetValue("10k")
root = src.GetPath().AsString().split("/")[1]
r37.SetPath(K.KIID_PATH("/%s/%s" % (root, UUID)))
b.Add(r37)
r37.thisown = False
r37.SetPosition(K.VECTOR2I(MM(95.0), MM(95.0)))       # parked outside the outline until placed
for p in r37.Pads():
    p.SetNet(net if p.GetNumber() == "1" else b.FindNet("GND"))
b.Save(OUT)
print("saved", OUT)
