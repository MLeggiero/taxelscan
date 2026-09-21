"""stage5_addr -> stage5d: USB_CC_OUT2 shortened, SYNC B lanes jogged south under R27
so R27.2 (GND) gets a via east of itself, then a DRC to see what is left."""
import sys, os, math, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply, pairgeo
lr.PRO_REF = lr.SCR + "/start-backup/rev3.kicad_pro"
W = lr.SCR + "/work/rev3.kicad_pcb"
S5 = lr.SCR + "/work/stage5_addr.kicad_pcb"
OUT = lr.SCR + "/work/stage5d.kicad_pcb"

m = lr.Model(S5)
# 1. USB_CC_OUT2 alone (30 mm / 6 vias after the churn)
old = grp.net_len(m, "USB_CC_OUT2")
m.remove([it["uuid"] for it in m.net_items("USB_CC_OUT2", ("track", "via"))]); m.index()
ok = grp.connect_all(m, "USB_CC_OUT2", layers_try=(("F", "B"),))
new = grp.net_len(m, "USB_CC_OUT2")
print("USB_CC_OUT2 old %s new %s ok %s" % (old, new, ok))
if not ok:
    sys.exit("USB_CC_OUT2 did not reconnect")

# 2. SYNC_N/SYNC_P B lanes: a 0.5 mm jog south between x 149.8 and 152.55 (45 deg,
#    pitch kept at 0.3 mm, both conductors gain the same length)
K = 0.3 * (math.sqrt(2) - 1)            # outer-lane lead on a 45 deg bend at 0.3 mm pitch
XP, D, J = 149.8, 152.05, 0.5
y_n, y_p = 131.65, 131.95
lanes = {
    "SYNC_N": ((141.25, y_n), (154.05, y_n)),
    "SYNC_P": ((142.05, y_p), (154.7, y_p)),
}
jog = {
    "SYNC_N": [(141.25, y_n), (XP + K, y_n), (XP + K + J, y_n + J), (D - K, y_n + J), (D - K + J, y_n), (154.05, y_n)],
    "SYNC_P": [(142.05, y_p), (XP, y_p), (XP + J, y_p + J), (D, y_p + J), (D + J, y_p), (154.7, y_p)],
}
for net, (a, c) in lanes.items():
    hit = [it for it in m.net_items(net, ("track",)) if it["lay"] == {"B"}
           and math.dist(it["a"], a) < 0.01 and math.dist(it["c"], c) < 0.01]
    if len(hit) != 1:
        sys.exit("%s B lane not found as one track: %s" % (net, hit))
    m.remove([hit[0]["uuid"]])
m.index()
for net, pts in jog.items():
    segs = [("B", (round(p[0], 6), round(p[1], 6)), (round(q[0], 6), round(q[1], 6))) for p, q in zip(pts, pts[1:])]
    bad = m.verify(net, segs, [], pairgeo.W, pairgeo.VIA)
    if bad:
        sys.exit("%s jog collides: %s" % (net, bad[:6]))
    m.add(net, segs, [], pairgeo.W, pairgeo.VIA)
m.index()
for net in jog:
    print(net, "lane length", grp.net_len(m, net))

# 3. R27.2
p = m.pad("R27", "2")
print("R27.2 stitched:", pairs_apply.stitch(m, p), "grounded:", pairs_apply.grounded(m, p))
vias = [it for it in m.net_items("GND", ("via",)) if math.dist(it["xy"], p["xy"]) < 2.0]
print("GND vias within 2 mm of R27.2:", [it["xy"] for it in vias])

m.save(OUT)
shutil.copyfile(lr.PRO_REF, lr.SCR + "/work/rev3.kicad_pro")
d, c = lr.drc(OUT, "t21")
lr.summary(d, c)
