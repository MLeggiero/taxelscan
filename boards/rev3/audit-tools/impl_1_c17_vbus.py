import sys, os, time, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2
W = lr.SCR + "/work/rev3.kicad_pcb"
m = lr.Model(W)
# 1. C17.2 GND -> C18.2 (grounded) on F.Cu
c17, c18 = m.pad("C17", "2"), m.pad("C18", "2")
r = rr2.route2(m, "GND", rr2.terminals([c17], 0.1), rr2.terminals([c18], 0.1), (125.8, 114.4, 129.0, 116.4), 0.1, ("F",))
print("C17:", r)
if r and not r[2]:
    rr2.commit(m, "GND", r[0], r[1], 0.1)
# 2. USB_VBUS_DET with rip-up
t0 = time.time()
res = rr2.rrr(m, ["USB_VBUS_DET"], max_iter=30)
print(res, "%.1fs" % (time.time() - t0))
for net in ("USB_VBUS_DET", "USB_CC1", "USB_CC2"):
    its = m.net_items(net, ("track", "via"))
    L = sum(math.dist(it["a"], it["c"]) for it in its if it["kind"] == "track")
    print("%-13s %d tracks %.1f mm, vias %s" % (net, sum(1 for it in its if it["kind"] == "track"), L, [it["xy"] for it in its if it["kind"] == "via"]))
m.save(W)
d, c = lr.drc(W, "drc_t4")
lr.summary(d, c)
