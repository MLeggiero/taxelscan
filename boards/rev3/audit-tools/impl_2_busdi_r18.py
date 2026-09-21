import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp
from shapely.geometry import box, Point
W = lr.SCR + "/work/rev3.kicad_pcb"
PRO = lr.SCR + "/work/rev3.kicad_pro"
S1B, S1C, GB = [lr.SCR + "/work/" + n for n in ("stage1b_prefb.kicad_pcb", "stage1c_fb.kicad_pcb", "group_base.kicad_pcb")]
pro0 = grp.md5(PRO)

m = lr.Model(S1B)
fb = m.net_items("FB", ("track",))
main = {((102.862, 121.15), (102.85, 121.45)), ((102.85, 121.45), (102.85, 122.2)), ((102.61, 122.5), (102.85, 122.2))}
def key(it): return (tuple(round(v, 3) for v in it["a"]), tuple(round(v, 3) for v in it["c"]))
loop = [it["uuid"] for it in fb if key(it) not in main and (key(it)[1], key(it)[0]) not in main]
tail = [it["uuid"] for it in m.net_items("+3.3V", ("track",)) if "F" in it["lay"] and it["geom"].intersects(box(101.2, 122.85, 102.95, 123.95))]
m.remove(loop + tail)
r18 = m.b.FindFootprintByReference("R18")
r18.SetOrientationDegrees(r18.GetOrientationDegrees() + 180)
m.index()
r = rr2.route2(m, "FB", rr2.terminals([m.pad("R19", "1")], 0.15), rr2.terminals([m.pad("R18", "2")], 0.15), (100.8, 121.8, 103.4, 124.3), 0.15, ("F",))
rr2.commit(m, "FB", r[0], r[1], 0.15)
end = (103.05, 123.05)
r = rr2.route2(m, "+3.3V", [("F", Point(end).buffer(0.014), end)], rr2.terminals([m.pad("R18", "1")], 0.15), (101.8, 122.3, 104.8, 124.2), 0.15, ("F",))
rr2.commit(m, "+3.3V", r[0], r[1], 0.15)
m.b.Save(S1C)
print("FB fixed; saved stage1c")

def base_with(nets):
    mm = lr.Model(S1C)
    mm.remove([it["uuid"] for it in mm.items if it["kind"] in ("track", "via") and it["net"] in nets])
    mm.index()
    mm.b.Save(GB)

for group in (["BUS_DI", "BUS_RO", "BUS_DE"], ["BUS_DI", "BUS_RO", "BUS_DE", "SYNC_OUT"]):
    base_with(group)
    print("== group", group)
    res = grp.try_orders(GB, [group[0]], group[1:])
    if res:
        break
print("best:", res[:2])
if res:
    m = lr.Model(GB)
    for net in res[0][1]:
        grp.connect_all(m, net)
    print("peeled", grp.peel(m, group))
    m.save(W)
    print("pro unchanged:", grp.md5(PRO) == pro0)
    d, c = rr2.prune(m, W, "t8")
    lr.summary(d, c)
    m.b.Save(lr.SCR + "/work/stage2_busdi.kicad_pcb")
