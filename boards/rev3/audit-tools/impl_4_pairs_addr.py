import sys, os, shutil
SCRP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRP)
import lr, rr2, grp, pairs_apply, addr_group2
W = lr.SCR + "/work/rev3.kicad_pcb"
S3 = lr.SCR + "/work/stage3_rowdata.kicad_pcb"
PRO = lr.SCR + "/work/rev3.kicad_pro"
pro0 = grp.md5(PRO)
print("===== pairs")
r = pairs_apply.apply(S3, W)
if r is None:
    sys.exit("pair geometry hits pads")
lr.summary(*r)
shutil.copy(W, lr.SCR + "/work/stage4_pairs.kicad_pcb")
print("===== address straps")
cand = lr.SCR + "/work/cand_addr.kicad_pcb"
res = addr_group2.run(W, cand)
print("addr result:", res)
if res:
    m = lr.Model(cand)
    m.save(W)
    d, c = pairs_apply.close_loop(m, W)
    d, c = rr2.prune(m, W, "addr_prune")
    lr.summary(d, c)
    shutil.copy(W, lr.SCR + "/work/stage5_addr.kicad_pcb")
    for n in ("BUS_P", "BUS_N", "SYNC_P", "SYNC_N", "ADDR0", "ADDR1", "ADDR2", "+5V", "+5V_BUS", "RUN", "SWCLK", "SWDIO",
              "USB_ILIM", "USB_ILIM_HI", "USB_ILIM_LOW", "USB_PWR_FAULT", "USB_BUS_EN", "ROW_DATA", "BUS_DI", "USB_VBUS_DET"):
        print("   %-14s %.1f mm %d vias" % ((n,) + grp.net_len(m, n)))
print("pro unchanged:", grp.md5(PRO) == pro0)
