"""reach.py - can a net get from one pad to another at all?

Labels the free space of F.Cu, In2.Cu and B.Cu for the net (track width w), joins
the regions that share a via-legal cell (a through via touches every layer), and
reports whether the two pads land in the same joined region, plus the extent of
each side's region per layer. A False here means no router, in any order, can
connect the pair without moving other copper first."""
import numpy as np
import shapely
from scipy.ndimage import label


def conn3d(m, net, pa, pb, win, w=0.1, g=0.025, layers=("F", "In2", "B")):
    M = m.masks(net, win, g, w, frozenset(), (0.5, 0.3))
    X, Y = M["X"], M["Y"]
    labs, offs, total = {}, {}, 0
    for L in layers:
        lab, n = label(M["free"][L], structure=np.ones((3, 3)))
        labs[L], offs[L] = lab, total
        total += n + 1
    parent = list(range(total))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    vo = M["via_ok"]
    for i, L1 in enumerate(layers):
        for L2 in layers[i + 1:]:
            l1, l2 = labs[L1][vo], labs[L2][vo]
            ok = (l1 > 0) & (l2 > 0)
            for a, b in set(zip((l1[ok] + offs[L1]).tolist(), (l2[ok] + offs[L2]).tolist())):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

    def roots(pad):
        s = set()
        for L in ("F", "B"):
            if L in pad["lay"] and L in labs:
                hit = shapely.intersects_xy(pad["geom"].buffer(-w / 2 + 0.004), X, Y) & M["free"][L]
                s |= {find(int(k) + offs[L]) for k in np.unique(labs[L][hit]) if k}
        return s

    def extent(rs):
        out = {}
        for L in layers:
            ks = [k for k in range(1, labs[L].max() + 1) if find(k + offs[L]) in rs]
            mask = np.isin(labs[L], ks)
            if mask.any():
                ii, jj = np.nonzero(mask)
                out[L] = (int(mask.sum()), round(win[0] + jj.min() * g, 2), round(win[1] + ii.min() * g, 2),
                          round(win[0] + jj.max() * g, 2), round(win[1] + ii.max() * g, 2))
        return out

    A, B = roots(pa), roots(pb)
    return bool(A & B), extent(A), extent(B)
