"""stage6k -> stage6m: take signal nets off In2 where they cut the +3.3V pour.

stage6k closes every connection, but its +3.3V pour on In2 fills in 9 pieces and only
19 of the 35 +3.3V vias land on the main one - the MCU's plane drops sit on a 21 mm2
island. pour.py says moving the In2 copper of seven signal nets (MUX_S1, ADDR1,
USB_PWR_FAULT, ADC_CONV, USB_BUS_EN, USB_ILIM_LOW, ADC_SDO) brings that to 33 (the start
board had 30). One net at a time: lift its copper except the hand-laid escapes, route it
again on F.Cu/B.Cu only; keep the result if it routes and the main piece does not lose
vias, otherwise put the original copper back exactly. Then close loop and prune."""
import sys, os, functools, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply, strapgeo, pour
lr.PRO_REF = lr.SCR + "/start-backup/rev3.kicad_pro"
W = lr.SCR + "/work/t33w.kicad_pcb"
BASE = lr.SCR + "/work/stage6k.kicad_pcb"
OUT = lr.SCR + "/work/stage6m.kicad_pcb"
FB = (("F", "B"),)
ORDER = ["MUX_S1", "ADDR1", "USB_PWR_FAULT", "ADC_CONV", "USB_BUS_EN", "USB_ILIM_LOW", "ADC_SDO"]
rr2.route2 = functools.partial(rr2.route2, tlimit=90)     # connect_once looks route2 up as a module global

segs, vias = strapgeo.geometry()
ESC_T = {(n, l, frozenset({tuple(round(v, 3) for v in a), tuple(round(v, 3) for v in c)})) for n, l, a, c in segs if l != "In2"}
ESC_V = {(n, tuple(round(v, 3) for v in p)) for n, p in vias}


def escape(it):
    if it["kind"] == "via":
        return (it["net"], tuple(round(v, 3) for v in it["xy"])) in ESC_V
    key = frozenset({tuple(round(v, 3) for v in it["a"]), tuple(round(v, 3) for v in it["c"])})
    return (it["net"], next(iter(it["lay"])), key) in ESC_T


def lift(m, net):
    """remove the net's copper except its escapes; return what was removed, for putting back"""
    its = [it for it in m.net_items(net, ("track", "via")) if not escape(it)]
    t = collections.defaultdict(list)
    v = collections.defaultdict(list)
    for it in its:
        if it["kind"] == "track":
            t[round(it["w"], 4)].append((next(iter(it["lay"])), it["a"], it["c"]))
        else:
            v[(round(it["geom"].bounds[2] - it["geom"].bounds[0], 3), round(it["hole"].bounds[2] - it["hole"].bounds[0], 3))].append(it["xy"])
    m.remove([it["uuid"] for it in its]); m.index()
    return t, v


def put_back(m, net, snap):
    m.remove([it["uuid"] for it in m.net_items(net, ("track", "via")) if not escape(it)]); m.index()
    t, v = snap
    for w, s in t.items():
        m.add(net, s, [], w)
    for size, xy in v.items():
        m.add(net, [], xy, 0.1, size)
    m.index()


m = lr.Model(BASE)
cur = pour.pieces(m.b)
print("start: main piece %.0f mm2 holds %d of %d +3.3V vias" % (cur["main_area"], cur["on_main"], cur["total"]), flush=True)
moved = []
for net in ORDER:
    if not any(it["lay"] == {"In2"} for it in m.net_items(net, ("track",))):
        print("%-13s has no In2 copper now - skipped" % net, flush=True)
        continue
    old = grp.net_len(m, net)
    snap = lift(m, net)
    ok = False
    for mg in (2.0, 5.0, 8.0):
        if grp.connect_all(m, net, margin=mg, layers_try=FB):
            ok = True
            break
    new = pour.pieces(m.b) if ok else None
    if ok and new["on_main"] >= cur["on_main"]:
        print("%-13s moved to F/B at margin %.0f: %s -> %s; main piece holds %d (was %d)" % (
            net, mg, tuple(round(x, 1) for x in old), tuple(round(x, 1) for x in grp.net_len(m, net)), new["on_main"], cur["on_main"]), flush=True)
        cur = new
        moved.append(net)
    else:
        put_back(m, net, snap)
        print("%-13s %s - original copper put back (%d component(s))" % (
            net, "did not route on F/B" if not ok else "routed but the pour got worse", len(rr2.comps(m, net))), flush=True)

print("moved:", moved, flush=True)
print("peeled", grp.peel(m, moved))
d, c = pairs_apply.close_loop(m, W, rounds=3, use_rrr=False)
d, c = rr2.prune(m, W, "t33", rounds=16)
lr.summary(d, c)
m.save(OUT)
end = pour.pieces(lr.Model(OUT).b)
print("end: main piece %.0f mm2 holds %d of %d; off the main piece: %s" % (end["main_area"], end["on_main"], end["total"], end["off"]))
