"""stage5v -> stage6j: lay strapgeo's escapes for U9 pins 32-35, then route outward.

  t31.py --verify   place and check the geometry only (prints every collision)
  t31.py            place, check, route ADDR0/ADDR2/ADDR1/USB_PWR_FAULT from their
                    escape vias to the jumpers / R31 / U14 with growing windows, join
                    USB_CC_OUT1, close loop, prune, save stage6j
"""
import sys, os, math, functools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply, reach, strapgeo
lr.PRO_REF = lr.SCR + "/start-backup/rev3.kicad_pro"
W = lr.SCR + "/work/t31w.kicad_pcb"
VBASE = lr.SCR + "/work/stage5v_base.kicad_pcb"
GBASE = lr.SCR + "/work/stage5y_base.kicad_pcb"
OUT = lr.SCR + "/work/stage6j.kicad_pcb"
LT = (("F", "B"), ("F", "In2", "B"))
GROUP = ["ADC_SDO", "ADC_SCK", "ADC_SDI", "ADC_CONV", "STATUS", "USB_CC_OUT1", "USB_CC_OUT2", "USB_BUS_EN",
         "ADDR0", "ADDR1", "ADDR2", "USB_PWR_FAULT"]
ENDS = {"ADDR0": ("JP1", "1"), "ADDR2": ("JP3", "1"), "ADDR1": ("JP2", "1"), "USB_PWR_FAULT": ("U14", "4")}
rr2.route2 = functools.partial(rr2.route2, tlimit=90)

m = lr.Model(VBASE)
gone = []
for it in m.net_items("ADC_SDO", ("track", "via")):
    if it["kind"] == "via" and any(math.dist(it["xy"], p) < 0.01 for p in strapgeo.SDO_REMOVE["via"]):
        gone.append(it["uuid"])
    if it["kind"] == "track":
        for l, a, c in strapgeo.SDO_REMOVE["track"]:
            if l in it["lay"] and {tuple(round(v, 3) for v in it["a"]), tuple(round(v, 3) for v in it["c"])} == {a, c}:
                gone.append(it["uuid"])
if len(gone) != 3:
    sys.exit("expected ADC_SDO's ring via and its two tracks, found %d item(s)" % len(gone))
m.remove(gone); m.index()

segs, vias = strapgeo.geometry()
bad_all = []
for net in ["ADC_SDO", "ADDR2", "ADDR0", "ADDR1", "USB_PWR_FAULT"]:
    s = [(l, a, c) for n, l, a, c in segs if n == net]
    v = [p for n, p in vias if n == net]
    bad = m.verify(net, s, v, strapgeo.W, strapgeo.VIA)
    for b in bad:
        print("  COLLISION %-13s %s" % (net, b), flush=True)
    bad_all += bad
    m.add(net, s, v, strapgeo.W, strapgeo.VIA)
    m.index()
print("ADC_SDO components after the move:", len(rr2.comps(m, "ADC_SDO")))
if bad_all:
    sys.exit("%d collision(s) - adjust strapgeo" % len(bad_all))
print("geometry clean", flush=True)
m.save(GBASE)
if "--verify" in sys.argv:
    sys.exit(0)

WIN = (116.0, 114.0, 150.0, 137.9)
for net, far in ENDS.items():
    ok, ea, eb = reach.conn3d(m, net, [it for it in m.net_items(net, ("via",))][0], m.pad(*far), WIN)
    print("reach %-13s from its escape via: %s %s" % (net, ok, "" if ok else ea), flush=True)


def widen(net, margins=(2.0, 5.0, 8.0)):
    for mg in margins:
        if grp.connect_all(m, net, margin=mg, layers_try=LT):
            print("%-13s routed at margin %.0f: %s" % (net, mg, tuple(round(x, 1) for x in grp.net_len(m, net))), flush=True)
            return True
    print("%-13s NOT routed (%d components)" % (net, len(rr2.comps(m, net))), flush=True)
    return False


ok = all([widen(n) for n in ("ADDR0", "ADDR2", "ADDR1", "USB_PWR_FAULT")])
if len(rr2.comps(m, "USB_CC_OUT1")) > 1:
    widen("USB_CC_OUT1")
print("peeled", grp.peel(m, GROUP))
d, c = pairs_apply.close_loop(m, W, rounds=3, use_rrr=False)
d, c = rr2.prune(m, W, "t31", rounds=16)
lr.summary(d, c)
m.save(OUT)
for n in GROUP:
    print("   %-14s %s" % (n, tuple(round(v, 1) for v in grp.net_len(m, n))))
