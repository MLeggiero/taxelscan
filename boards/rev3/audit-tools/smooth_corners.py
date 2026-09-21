"""smooth_corners.py IN OUT [MAX_DEG] - straighten the sharp corners compaction left behind.

Where exactly two segments of a net meet at a free corner (no pad or via there) at less
than MAX_DEG degrees (default 60), both are replaced by one segment between their far
ends - if that segment clears everything by the board's rules (lr.Model's exact check)
and its ends still land where the old ones did. Anything that does not clear is left
alone and listed."""
import sys, os, math, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr
import pcbnew as K

IN, OUT = sys.argv[1], sys.argv[2]
MAX_DEG = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
m = lr.Model(IN)
key = lambda p: (round(p[0], 4), round(p[1], 4))
done_total = 0
for rnd in range(5):
    ends = collections.defaultdict(list)
    for it in m.items:
        if it["kind"] == "track":
            l = next(iter(it["lay"]))
            ends[(l, it["net"], key(it["a"]))].append((it, "a"))
            ends[(l, it["net"], key(it["c"]))].append((it, "c"))
    anchors = [it for it in m.items if it["kind"] in ("pad", "via")]
    fixed, left = 0, []
    used = set()
    for (l, net, p), lst in ends.items():
        if len(lst) != 2:
            continue
        (t1, e1), (t2, e2) = lst
        if t1["uuid"] in used or t2["uuid"] in used or abs(t1["w"] - t2["w"]) > 1e-6:
            continue
        q1 = t1["c"] if e1 == "a" else t1["a"]
        q2 = t2["c"] if e2 == "a" else t2["a"]
        v1, v2 = (q1[0] - p[0], q1[1] - p[1]), (q2[0] - p[0], q2[1] - p[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        ang = math.degrees(math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))))
        if ang >= MAX_DEG:
            continue
        # a corner sitting on a pad or via of the net is a real connection point: leave it
        from shapely.geometry import Point
        if any(a["net"] == net and l in a["lay"] and a["geom"] is not None and a["geom"].distance(Point(p)) < 1e-3 for a in anchors):
            continue
        ign = frozenset((t1["uuid"], t2["uuid"]))
        bad = m.check_track(net, l, q1, q2, t1["w"], ign)
        if bad:
            left.append((round(ang, 1), net, p, bad[0][:4]))
            continue
        m.remove([t1["uuid"], t2["uuid"]])
        m.add(net, [(l, q1, q2)], [], t1["w"])
        used |= set(ign)
        fixed += 1
        print("  %-12s %s corner %.1f deg at (%.3f,%.3f) straightened" % (net, l, ang, *p))
    m.index()
    done_total += fixed
    if not fixed:
        break
for x in left:
    print("  left as is: %.1f deg %s at (%.3f,%.3f): %s" % (x[0], x[1], x[2][0], x[2][1], x[3]))
m.save(OUT)
print("straightened %d corner(s); saved %s" % (done_total, OUT))
