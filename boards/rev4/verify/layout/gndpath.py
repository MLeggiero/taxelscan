"""gndpath.py board ref.pin ref.pin ... - copper path length (and vias) between pairs of pads on their shared net."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr
from decap import graph, paths
m = lr.Model(sys.argv[1])
cache = {}
for a, b in zip(sys.argv[2::2], sys.argv[3::2]):
    pa = next(it for it in m.items if it["kind"] == "pad" and it["ref"] == a)
    pb = next(it for it in m.items if it["kind"] == "pad" and it["ref"] == b)
    net = pa["net"]
    if net not in cache:
        cache[net] = graph(m, net)
    its, adj = cache[net]
    ia = next(i for i, it in enumerate(its) if it is pa)
    ib = next(i for i, it in enumerate(its) if it is pb)
    f = paths(its, adj, ia, "F").get(ib)
    v = paths(its, adj, ia).get(ib)
    print("%-8s -> %-8s %-8s F.Cu only: %s   any: %s" % (a, b, net, "%.2f mm" % f[0] if f else "none",
          "%.2f mm, %d via" % v if v else "none"))
