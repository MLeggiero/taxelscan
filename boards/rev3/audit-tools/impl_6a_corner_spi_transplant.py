"""stage5d -> stage6e: transplant the corner escapes that worked.

stage3_rowdata had ADC_SDO, ADC_SCK, ADC_SDI and ADC_CONV routed out of U9's
bottom-right corner alongside ADDR0/ADDR1 and the USB-power signals (only ADDR2
open). Checked against the current board, its SDI and CONV copper fits as it is and
its SDO/SCK copper collides only with STATUS, USB_CC_OUT1 and USB_CC_OUT2, which
have been re-routed since. So: rip the nine corner nets and those three, put the
stage-3 SPI/CONV copper back, check that the straps can still get out, then route
the eight remaining nets in a handful of orders from clean copies and keep the best."""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply, reach
lr.PRO_REF = lr.SCR + "/start-backup/rev3.kicad_pro"
W = lr.SCR + "/work/t26w.kicad_pcb"
BASE0 = lr.SCR + "/work/stage5h_base.kicad_pcb"     # stage5d with the nine corner nets ripped
S3 = lr.SCR + "/work/stage3_rowdata.kicad_pcb"
BASE = lr.SCR + "/work/stage5t_base.kicad_pcb"
OUT = lr.SCR + "/work/stage6e.kicad_pcb"
KEEP = ["ADC_SDO", "ADC_SCK", "ADC_SDI", "ADC_CONV"]
EXTRA = ["STATUS", "USB_CC_OUT1", "USB_CC_OUT2"]
LT = (("F", "B"), ("F", "In2", "B"))
ORDERS = [
    ("ADDR2", "USB_PWR_FAULT", "USB_BUS_EN", "ADDR1", "ADDR0", "STATUS", "USB_CC_OUT1", "USB_CC_OUT2"),
    ("ADDR2", "ADDR1", "ADDR0", "USB_PWR_FAULT", "USB_BUS_EN", "STATUS", "USB_CC_OUT1", "USB_CC_OUT2"),
    ("ADDR0", "ADDR1", "ADDR2", "USB_PWR_FAULT", "USB_BUS_EN", "STATUS", "USB_CC_OUT1", "USB_CC_OUT2"),
    ("STATUS", "USB_CC_OUT1", "USB_CC_OUT2", "ADDR2", "USB_PWR_FAULT", "USB_BUS_EN", "ADDR1", "ADDR0"),
    ("ADDR2", "ADDR0", "USB_BUS_EN", "USB_PWR_FAULT", "ADDR1", "STATUS", "USB_CC_OUT1", "USB_CC_OUT2"),
    ("USB_CC_OUT1", "USB_CC_OUT2", "STATUS", "ADDR2", "ADDR1", "ADDR0", "USB_PWR_FAULT", "USB_BUS_EN"),
]
MAX_OK = int(sys.argv[1]) if len(sys.argv) > 1 else 2

m = lr.Model(BASE0)
old = {n: grp.net_len(lr.Model(lr.SCR + "/work/stage5d.kicad_pcb"), n) for n in EXTRA}
m.remove([it["uuid"] for it in m.items if it["kind"] in ("track", "via") and it["net"] in EXTRA]); m.index()
s3 = lr.Model(S3)
for net in KEEP:
    segs, vias = collections.defaultdict(list), collections.defaultdict(list)
    for it in s3.net_items(net, ("track", "via")):
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
    print("transplanted %-9s %s comps %d" % (net, tuple(round(x, 1) for x in grp.net_len(m, net)), len(rr2.comps(m, net))), flush=True)
m.save(BASE)

WIN = (116.0, 114.0, 140.0, 137.9)
for net, a, b in (("ADDR2", ("U9", "34"), ("JP3", "1")), ("ADDR0", ("U9", "32"), ("JP1", "1")), ("ADDR1", ("U9", "33"), ("JP2", "1"))):
    ok, ea, eb = reach.conn3d(m, net, m.pad(*a), m.pad(*b), WIN)
    print("reach %s: %s   from %s.%s %s" % (net, ok, a[0], a[1], ea if not ok else ""), flush=True)
    if not ok:
        sys.exit("%s cannot get out with the stage-3 escapes in place" % net)

results = []
for order in ORDERS:
    mm = lr.Model(BASE)
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
    sys.exit("no order completes the corner")

m = lr.Model(BASE)
for n in results[0][1]:
    grp.connect_all(m, n, layers_try=LT)
group = KEEP + list(results[0][1])
print("peeled", grp.peel(m, group))
d, c = pairs_apply.close_loop(m, W, rounds=3, use_rrr=False)
d, c = rr2.prune(m, W, "t26", rounds=16)
lr.summary(d, c)
m.save(OUT)
for n in group:
    print("   %-14s %s" % (n, tuple(round(v, 1) for v in grp.net_len(m, n))))
