import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp
import pcbnew as K
from shapely.geometry import box
S2 = lr.SCR + "/work/stage2_busdi.kicad_pcb"
GB = lr.SCR + "/work/group_base_r34.kicad_pcb"
m = lr.Model(S2)
r3, r4 = m.b.FindFootprintByReference("R3"), m.b.FindFootprintByReference("R4")
p3, p4 = r3.GetPosition(), r4.GetPosition()
print("R3 at", K.ToMM(p3.x), K.ToMM(p3.y), r3.GetOrientationDegrees(), " R4 at", K.ToMM(p4.x), K.ToMM(p4.y), r4.GetOrientationDegrees())
near = box(117.7, 118.2, 120.1, 121.0)
rip = [it["uuid"] for it in m.items if it["kind"] in ("track", "via") and
       (it["net"] in ("ROW_CLK_MCU", "ROW_LATCH_MCU") or (it["net"] in ("ROW_CLK", "ROW_LATCH") and it["geom"].intersects(near)))]
m.remove(rip)
r3.SetPosition(K.VECTOR2I(p4.x, p4.y))
r4.SetPosition(K.VECTOR2I(p3.x, p3.y))
m.index()
rr2.delete_dead(m, ["ROW_CLK", "ROW_LATCH", "ROW_CLK_MCU", "ROW_LATCH_MCU"])
print("R3.1", m.pad("R3", "1")["xy"], "R4.1", m.pad("R4", "1")["xy"], "R3.2", m.pad("R3", "2")["xy"], "R4.2", m.pad("R4", "2")["xy"])
print("components:", {n: len(rr2.comps(m, n)) for n in ("ROW_DATA", "ROW_CLK_MCU", "ROW_LATCH_MCU", "ROW_CLK", "ROW_LATCH")})
m.b.Save(GB)
res = grp.try_orders(GB, ["ROW_DATA"], ["ROW_LATCH_MCU", "ROW_CLK_MCU", "ROW_CLK", "ROW_LATCH"], max_success=3, max_orders=24)
print("results:", res)
if not res:
    res = grp.try_orders(GB, ["ROW_LATCH_MCU", "ROW_CLK_MCU"], ["ROW_DATA", "ROW_CLK", "ROW_LATCH"], max_success=3, max_orders=6)
    print("results (fan-out first):", res)
