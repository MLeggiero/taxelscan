import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairs_apply
W = lr.SCR + "/work/rev3.kicad_pcb"
C = lr.SCR + "/work/cand_rowdata.kicad_pcb"
PRO = lr.SCR + "/work/rev3.kicad_pro"
pro0 = grp.md5(PRO)
m = lr.Model(C)
for n in ("ROW_CLK", "ROW_LATCH"):
    print(n, grp.connect_all(m, n, layers_try=(("F", "B"),)), grp.net_len(m, n))
d, c = pairs_apply.close_loop(m, W, rounds=5)
d, c = rr2.prune(m, W, "t18")
lr.summary(d, c)
print("pro unchanged:", grp.md5(PRO) == pro0)
m.b.Save(lr.SCR + "/work/stage3_rowdata.kicad_pcb")
