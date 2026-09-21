"""For every connection DRC still calls unconnected: is the start sealed, the goal sealed, or neither?"""
import sys, os, json, subprocess, re, collections
sys.argv = ["x", "noop"]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "route_fix.py")).read().split('if __name__ == "__main__":')[0])
from collections import deque
d = fill_and_drc("probe")
R = Router()
objs = {u: ("pad",) + key for u, key in R.padkey.items()}
for t in R.native.GetTracks(): objs[t.m_Uuid.AsString()] = ("track", t)

def bfs(free, via_ok, seeds, limit=6000):
    seen = set(seeds); dq = deque(seeds); h, w = free["F.Cu"].shape
    while dq and len(seen) < limit:
        l, i, j = dq.popleft(); f = free[l]
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ii, jj = i + di, j + dj
            if 0 <= ii < h and 0 <= jj < w and f[ii, jj] and (l, ii, jj) not in seen: seen.add((l, ii, jj)); dq.append((l, ii, jj))
        if via_ok[i, j]:
            for l2 in M.LAYERS:
                if l2 != l and free[l2][i, j] and (l2, i, j) not in seen: seen.add((l2, i, j)); dq.append((l2, i, j))
    return seen

def nodes(o):
    if o[0] == "pad": return [(o[1], o[2])]
    t = o[1]; pts = [t.GetPosition()] if isinstance(t, K.PCB_VIA) else [t.GetStart(), t.GetEnd()]
    lay = ALL if isinstance(t, K.PCB_VIA) else [{K.F_Cu: "F.Cu", K.In2_Cu: "In2.Cu", K.B_Cu: "B.Cu"}.get(t.GetLayer(), "")]
    out = []
    for k, p in enumerate(pts):
        key = ("@", "%d_%d" % (id(t), k)); R.padpos[key] = (K.ToMM(p.x), K.ToMM(p.y), [l for l in lay if l], t.GetNetname())
        out.append(key)
    return out

rows = []
for v in d["unconnected_items"]:
    a, c = v["items"][:2]
    oa, oc = objs.get(a["uuid"]), objs.get(c["uuid"])
    if not oa or not oc: continue
    net = re.search(r"\[([^\]]+)\]", a["description"]).group(1)
    src, dst = nodes(oa), nodes(oc)
    touches = any(r in FINE for r, p in src + dst if r != "@")
    lay = TOP if net in ANALOG else ALL
    set_width(0.1 if touches else 0.15, 0.1 if touches else 0.15)
    free, via_ok, own = R.b.build(net)
    for l in M.LAYERS:
        if l not in lay: free[l][:] = False
    if R.tips:
        tm = R.tip_mask(net)
        for l in lay: free[l] &= ~tm | own[l]
    res = []
    for pads in (src, dst):
        s = R.island([cc for r, p in pads for cc in R.cells(r, p, lay)], own, free, net, lay)
        rs = bfs(free, via_ok, s)
        xs = [R.b.world(i, j) for l, i, j in rs] or [(0, 0)]
        res.append((len(s), len(rs), min(x for x, y in xs), max(x for x, y in xs), min(y for x, y in xs), max(y for x, y in xs)))
    verdict = "start-sealed" if res[0][1] < 600 else "goal-sealed" if res[1][1] < 600 else "both-open"
    rows.append((net, a["description"][:34], c["description"][:34], verdict, res))
for net, a, c, verdict, res in sorted(rows):
    print("%-13s %-12s %-34s | %-34s  S %5d->%5d (x%.1f-%.1f y%.1f-%.1f)  G %5d->%5d (x%.1f-%.1f y%.1f-%.1f)" % (net, verdict, a, c, *res[0], *res[1]))
print(len(rows), "unconnected")
