"""stage6m -> stage6n: lift MUX_S1's In2 hops off the MCU's +3.3V island.

On stage6m the +3.3V pour's main piece holds 20 of 35 vias; the MCU's eight plane drops
sit on one island that VCORE's In2 trunk and MUX_S1's In2 hops ring between them (pour.py:
without MUX_S1's In2 copper the main piece gains 8). MUX_S1 as a whole net will not route
on F.Cu/B.Cu (t33), so lift only its In2 copper, keep its F/B parts, and reconnect the
pieces on F/B, or on In2 kept 0.8 mm off the island. Keep the change only if the main
piece gains vias; otherwise put the net back exactly. Then close loop and prune."""
import sys, os, functools, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply, pour
from shapely.geometry import Point
lr.PRO_REF = lr.SCR + "/start-backup/rev3.kicad_pro"
W = lr.SCR + "/work/t34w.kicad_pcb"
BASE = lr.SCR + "/work/stage6m.kicad_pcb"
OUT = lr.SCR + "/work/stage6n.kicad_pcb"
FB = (("F", "B"),)
LT = (("F", "B"), ("F", "In2", "B"))
NETS = ["MUX_S1"]
rr2.route2 = functools.partial(rr2.route2, tlimit=90)


def snapshot(m, net):
    t, v = collections.defaultdict(list), collections.defaultdict(list)
    for it in m.net_items(net, ("track", "via")):
        if it["kind"] == "track":
            t[round(it["w"], 4)].append((next(iter(it["lay"])), it["a"], it["c"]))
        else:
            v[(round(it["geom"].bounds[2] - it["geom"].bounds[0], 3), round(it["hole"].bounds[2] - it["hole"].bounds[0], 3))].append(it["xy"])
    return t, v


def restore(m, net, snap):
    m.remove([it["uuid"] for it in m.net_items(net, ("track", "via"))]); m.index()
    t, v = snap
    for w, s in t.items():
        m.add(net, s, [], w)
    for size, xy in v.items():
        m.add(net, [], xy, 0.1, size)
    m.index()


m = lr.Model(BASE)
cur = pour.pieces(m.b)
others = [p for p in cur["polys"] if p is not cur["main"]]
island = max(others, key=lambda p: sum(1 for v in cur["off"] if p.buffer(0.05).contains(Point(v))))
print("start: main piece holds %d of %d; MCU island %.1f mm2 at %s" % (
    cur["on_main"], cur["total"], island.area, [round(x, 1) for x in island.bounds]), flush=True)
ko = {"In2": island.buffer(0.8)}
moved = []
for net in NETS:
    snap = snapshot(m, net)
    old = grp.net_len(m, net)
    in2 = [it["uuid"] for it in m.net_items(net, ("track",)) if it["lay"] == {"In2"}]
    m.remove(in2); m.index()
    rr2.delete_dead(m, [net])
    print("%s: lifted %d In2 segment(s); %d component(s) to join" % (net, len(in2), len(rr2.comps(m, net))), flush=True)
    ok = None
    for what, lt, keep in (("F/B", FB, None), ("F/In2/B, In2 off the island", LT, ko)):
        for mg in (2.0, 5.0, 8.0):
            if grp.connect_all(m, net, margin=mg, layers_try=lt, keepout=keep):
                ok = (what, mg)
                break
        if ok:
            break
    new = pour.pieces(m.b) if ok else None
    if ok and new["on_main"] > cur["on_main"]:
        print("%s rejoined on %s at margin %.0f: %s -> %s; main piece holds %d (was %d)" % (
            net, ok[0], ok[1], tuple(round(x, 1) for x in old), tuple(round(x, 1) for x in grp.net_len(m, net)), new["on_main"], cur["on_main"]), flush=True)
        cur = new
        moved.append(net)
    else:
        restore(m, net, snap)
        print("%s %s - put back as it was (%d component(s))" % (
            net, "did not rejoin" if not ok else "rejoined but the island stayed (%d)" % new["on_main"], len(rr2.comps(m, net))), flush=True)

print("peeled", grp.peel(m, moved))
d, c = pairs_apply.close_loop(m, W, rounds=3, use_rrr=False)
d, c = rr2.prune(m, W, "t34", rounds=16)
lr.summary(d, c)
m.save(OUT)
end = pour.pieces(lr.Model(OUT).b)
print("end: main piece %.0f mm2 holds %d of %d; off the main piece: %s" % (end["main_area"], end["on_main"], end["total"], end["off"]))
