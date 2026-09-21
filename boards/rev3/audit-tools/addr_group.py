"""addr_group2.py - ADDR0/1/2 re-routed together (F/B first, In2 only if that fails)."""
import sys, os
SCRP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRP)
import lr, rr2, grp
from shapely.geometry import box
WHOLE = ["ADDR2", "ADDR1", "ADDR0"]
LOCAL = {"USB_PWR_FAULT": box(128.4, 121.2, 131.9, 123.5), "USB_BUS_EN": box(129.4, 121.0, 131.9, 122.1)}

def run(base, out, log=print):
    gb = os.path.splitext(out)[0] + "_addrbase.kicad_pcb"
    m = lr.Model(base)
    rip = [it["uuid"] for it in m.items if it["kind"] in ("track", "via") and
           (it["net"] in WHOLE or (it["net"] in LOCAL and it["geom"].intersects(LOCAL[it["net"]])))]
    m.remove(rip); m.index()
    rr2.delete_dead(m, WHOLE + list(LOCAL))
    m.b.Save(gb)
    rest = [n for n in ["ADDR1", "ADDR0"] + list(LOCAL) if len(rr2.comps(m, n)) > 1]
    log("ripped %d items; reconnect after ADDR2: %s" % (len(rip), rest))
    res, lt = None, None
    for lt in ((("F", "B"),), (("F", "B"), ("F", "In2", "B"))):
        res = grp.try_orders(gb, ["ADDR2"], rest, max_success=4, max_orders=24, log=log, layers_try=lt)
        if res:
            break
    if not res:
        return None
    mm = lr.Model(gb)
    for n in res[0][1]:
        grp.connect_all(mm, n, layers_try=lt)
    log("peeled %d" % grp.peel(mm, WHOLE + list(LOCAL)))
    for n in res[0][1]:
        log("   %-14s %.1f mm %d vias" % ((n,) + grp.net_len(mm, n)))
    mm.b.Save(out)
    return res[0], lt
