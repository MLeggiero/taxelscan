import sys, os, math, random, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp
from shapely.geometry import box
D = lr.SCR + "/work/diag_r34.kicad_pcb"
GB = lr.SCR + "/work/group_base_row3.kicad_pcb"
m = lr.Model(D)
hub = [it for it in m.net_items("VCORE", ("track", "via")) if it["geom"].intersects(box(123.3, 118.75, 124.3, 119.95))
       and not (it["kind"] == "track" and ("In2" in it["lay"] or min(it["a"][0], it["c"][0]) < 123.35))]
print("VCORE hub items removed:", [(it["kind"], it.get("xy") or (it["a"], it["c"])) for it in hub])
m.remove([it["uuid"] for it in hub]); m.index()
LOCAL = box(118.4, 114.8, 124.75, 121.2)
keep = {"ROW_CLK_MCU", "ROW_LATCH_MCU"}
rip = [it for it in m.items if it["kind"] in ("track", "via") and it["net"] and it["net"] not in rr2.PROTECT
       and it["net"] not in keep and it["geom"].intersects(LOCAL)]
nets = sorted({it["net"] for it in rip})
print("local rip %d items on %s" % (len(rip), nets))
m.remove([it["uuid"] for it in rip]); m.index()
rr2.delete_dead(m, nets)
m.b.Save(GB)
need = [n for n in nets if len(rr2.comps(m, n)) > 1 and n != "ROW_DATA"]
print("to reconnect after ROW_DATA and VCORE:", need)
KO = {"F": box(119.3, 117.3, 122.62, 121.0)}
def trial(order):
    mm = lr.Model(GB)
    cs = rr2.comps(mm, "ROW_DATA")
    r, win = rr2.connect_once(mm, "ROW_DATA", cs, 0.1, ("F", "In2", "B"), 2.5, keepout=KO)
    if not r or r[2]:
        return None, "ROW_DATA"
    rr2.commit(mm, "ROW_DATA", r[0], r[1], 0.1)
    if not grp.connect_all(mm, "VCORE", layers_try=(("F", "In2", "B"),)):
        return None, "VCORE"
    for n in order:
        if not grp.connect_all(mm, n):
            return None, n
    return mm, None
span = lambda n: sum(it["geom"].length if it["geom"] is not None else 0 for it in lr.Model(GB).net_items(n))
orders = [tuple(need), tuple(reversed(need))]
rnd = random.Random(3)
for _ in range(10):
    o = list(need); rnd.shuffle(o); orders.append(tuple(o))
best = None
for o in orders:
    mm, fail = trial(o)
    if fail:
        print("   fail at %-14s %s" % (fail, ",".join(o)))
        if fail in ("ROW_DATA", "VCORE"):
            break
        continue
    L = {n: grp.net_len(mm, n) for n in ("ROW_DATA", "VCORE") + o}
    score = sum(a + 2 * b for a, b in L.values())
    print("   OK %.1f %s" % (score, {n: (round(a, 1), b) for n, (a, b) in L.items()}))
    if best is None or score < best[0]:
        best = (score, o)
print("best:", best)
if best:
    mm, _ = trial(best[1])
    print("peeled", grp.peel(mm, nets + ["ROW_DATA", "VCORE"]))
    mm.b.Save(lr.SCR + "/work/cand_rowdata.kicad_pcb")
