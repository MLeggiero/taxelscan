"""stage5t -> stage6g: finish the corner transplant.

On top of t26's base (corner ripped, stage-3 SPI/CONV copper back) the stage-3 copper
of STATUS, USB_CC_OUT1, USB_CC_OUT2 and USB_BUS_EN also fits unchanged. USB_PWR_FAULT
is NOT transplanted: its stage-3 escape via on pin 35 is what sealed ADDR2 (U9.34) in
the first place. Transplant the four, check the three straps and USB_PWR_FAULT can
reach their far ends, route them in a few orders with ADDR2 early, keep the best,
close loop and prune."""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply, reach
lr.PRO_REF = lr.SCR + "/start-backup/rev3.kicad_pro"
W = lr.SCR + "/work/t28w.kicad_pcb"
BASE = lr.SCR + "/work/stage5t_base.kicad_pcb"
S3 = lr.SCR + "/work/stage3_rowdata.kicad_pcb"
VBASE = lr.SCR + "/work/stage5v_base.kicad_pcb"
OUT = lr.SCR + "/work/stage6g.kicad_pcb"
SPI = ["ADC_SDO", "ADC_SCK", "ADC_SDI", "ADC_CONV"]
TRANS = ["STATUS", "USB_CC_OUT1", "USB_CC_OUT2", "USB_BUS_EN"]
LT = (("F", "B"), ("F", "In2", "B"))
ORDERS = [("ADDR2", "ADDR0", "ADDR1", "USB_PWR_FAULT"),
          ("ADDR0", "ADDR1", "ADDR2", "USB_PWR_FAULT"),
          ("ADDR2", "ADDR1", "ADDR0", "USB_PWR_FAULT"),
          ("ADDR2", "USB_PWR_FAULT", "ADDR0", "ADDR1"),
          ("ADDR0", "ADDR2", "ADDR1", "USB_PWR_FAULT"),
          ("ADDR1", "ADDR0", "ADDR2", "USB_PWR_FAULT")]
MAX_OK = int(sys.argv[1]) if len(sys.argv) > 1 else 2


def transplant(m, src, nets):
    for net in nets:
        segs, vias = collections.defaultdict(list), collections.defaultdict(list)
        for it in src.net_items(net, ("track", "via")):
            if it["kind"] == "track":
                segs[round(it["w"], 4)].append((next(iter(it["lay"])), it["a"], it["c"]))
            else:
                size = round(it["geom"].bounds[2] - it["geom"].bounds[0], 3)
                drill = round(it["hole"].bounds[2] - it["hole"].bounds[0], 3)
                vias[(size, drill)].append(it["xy"])
        bad = [b for w, s in segs.items() for b in m.verify(net, s, [], w)]
        bad += [b for v, xy in vias.items() for b in m.verify(net, [], xy, 0.1, v)]
        if bad:
            sys.exit("%s stage-3 copper collides: %s" % (net, bad[:6]))
        for w, s in segs.items():
            m.add(net, s, [], w)
        for v, xy in vias.items():
            m.add(net, [], xy, 0.1, v)
        m.index()
        print("transplanted %-12s %s comps %d" % (net, tuple(round(x, 1) for x in grp.net_len(m, net)), len(rr2.comps(m, net))), flush=True)


m = lr.Model(BASE)
transplant(m, lr.Model(S3), TRANS)
m.save(VBASE)
WIN = (116.0, 114.0, 150.0, 137.9)
for net, a, b in (("ADDR2", ("U9", "34"), ("JP3", "1")), ("ADDR0", ("U9", "32"), ("JP1", "1")),
                  ("ADDR1", ("U9", "33"), ("JP2", "1")), ("USB_PWR_FAULT", ("U9", "35"), ("U14", "4"))):
    ok, ea, eb = reach.conn3d(m, net, m.pad(*a), m.pad(*b), WIN)
    print("reach %-13s %s %s" % (net, ok, "" if ok else "from %s.%s: %s" % (a[0], a[1], ea)), flush=True)
    if not ok:
        sys.exit("%s is shut in with the stage-3 escapes in place" % net)

results = []
for order in ORDERS:
    mm = lr.Model(VBASE)
    fail = next((n for n in order if not grp.connect_all(mm, n, layers_try=LT)), None)
    if fail:
        print("   order %s: %s failed" % (",".join(order), fail), flush=True)
        continue
    L = {n: grp.net_len(mm, n) for n in order}
    score = sum(a + 2 * b for a, b in L.values())
    print("   order %s: OK %.1f %s" % (",".join(order), score, {n: (round(a, 1), b) for n, (a, b) in L.items()}), flush=True)
    results.append((score, order))
    if len(results) >= MAX_OK:
        break
results.sort()
print("results:", results, flush=True)
if not results:
    sys.exit("no order completes the straps")

m = lr.Model(VBASE)
for n in results[0][1]:
    grp.connect_all(m, n, layers_try=LT)
group = SPI + TRANS + list(results[0][1])
print("peeled", grp.peel(m, group))
d, c = pairs_apply.close_loop(m, W, rounds=3, use_rrr=False)
d, c = rr2.prune(m, W, "t28", rounds=16)
lr.summary(d, c)
m.save(OUT)
for n in group:
    print("   %-14s %s" % (n, tuple(round(v, 1) for v in grp.net_len(m, n))))
