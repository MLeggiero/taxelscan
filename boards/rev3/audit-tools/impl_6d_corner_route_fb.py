"""stage5y -> stage6k: route the corner nets outward without cutting the +3.3V pour.

t31 routed ADDR0/ADDR2/ADDR1/USB_PWR_FAULT out from strapgeo's escapes and closed
everything except one +3.3V open: ADDR1's and ADDR2's In2 hops, plus a short In2 piece in
USB_CC_OUT1's join, fenced a patch of the In2 pour into two islands, and U9.30's feed
vias (131.9, 125.1) / (132.0, 124.35) ended up in one of them. stage5y_base (escapes laid,
nothing routed outward) still has those vias in the main pour. So route from there on
F.Cu/B.Cu only; if a net will not go, allow In2 but keep it out of the corner where the
pour is narrow; only then In2 anywhere (reported)."""
import sys, os, functools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply
from shapely.geometry import box
lr.PRO_REF = lr.SCR + "/start-backup/rev3.kicad_pro"
W = lr.SCR + "/work/t32w.kicad_pcb"
YBASE = lr.SCR + "/work/stage5y_base.kicad_pcb"
OUT = lr.SCR + "/work/stage6k.kicad_pcb"
FB = (("F", "B"),)
LT = (("F", "B"), ("F", "In2", "B"))
KO = {"In2": box(126.0, 121.0, 138.0, 128.5)}
GROUP = ["ADC_SDO", "ADC_SCK", "ADC_SDI", "ADC_CONV", "STATUS", "USB_CC_OUT1", "USB_CC_OUT2", "USB_BUS_EN",
         "ADDR0", "ADDR1", "ADDR2", "USB_PWR_FAULT"]
rr2.route2 = functools.partial(rr2.route2, tlimit=90)     # connect_once looks route2 up as a module global

m = lr.Model(YBASE)


def route(net):
    for what, lt, ko in (("F/B", FB, None), ("F/In2/B, In2 kept out of the corner", LT, KO), ("F/In2/B anywhere", LT, None)):
        for mg in (2.0, 5.0, 8.0):
            if grp.connect_all(m, net, margin=mg, layers_try=lt, keepout=ko):
                in2 = sum(1 for it in m.net_items(net, ("track",)) if it["lay"] == {"In2"})
                print("%-13s routed on %s at margin %.0f: %s, %d In2 segment(s)" % (net, what, mg, tuple(round(x, 1) for x in grp.net_len(m, net)), in2), flush=True)
                return True
    print("%-13s NOT routed (%d components)" % (net, len(rr2.comps(m, net))), flush=True)
    return False


for n in ("ADDR0", "ADDR2", "ADDR1", "USB_PWR_FAULT"):
    route(n)
if len(rr2.comps(m, "USB_CC_OUT1")) > 1:
    route("USB_CC_OUT1")
print("peeled", grp.peel(m, GROUP))
d, c = pairs_apply.close_loop(m, W, rounds=3, use_rrr=False)
d, c = rr2.prune(m, W, "t32", rounds=16)
lr.summary(d, c)
m.save(OUT)
for n in GROUP:
    print("   %-14s %s" % (n, tuple(round(v, 1) for v in grp.net_len(m, n))))
