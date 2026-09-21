"""grp.py - group re-routing: rip a small set of nets, try routing orders from a clean copy, keep the best."""
import sys, os, math, itertools, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2
from shapely.geometry import box, Point

ZONE = {"GND", "+3.3V"}


def _attached(it, p, its, tol):
    """Is track end `p` joined to something? A pad or via counts when p lies on its
    copper; another track counts at its ends or on its body between the ends. A free
    end that merely sits inside the width of the previous segment of a staircase is
    NOT attached - that is exactly the stub KiCad reports as dangling."""
    l = next(iter(it["lay"]))
    pt = Point(p)
    q = it["c"] if p == it["a"] else it["a"]          # this track's other end
    for o in its:
        if o is it or l not in o["lay"] or o["geom"] is None:
            continue
        if o["geom"].distance(pt) >= (tol if o["kind"] == "track" else 0.05):
            continue
        if o["kind"] != "track":
            return True                                   # on (or at the rounded edge of) a pad or via
        # p lies on o's copper. That is a real join unless this track's other end
        # is ALSO on o - then p may just sit inside o's width at a staircase end,
        # and only o's own ends or o's body between its ends count.
        if o["geom"].distance(Point(q)) >= tol:
            return True
        if math.dist(p, o["a"]) < tol or math.dist(p, o["c"]) < tol:
            return True
        (ax, ay), (cx, cy) = o["a"], o["c"]
        dx, dy = cx - ax, cy - ay
        L2 = dx * dx + dy * dy
        if L2 > 0 and 0.0 < ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2 < 1.0:
            return True
    return False


def peel(m, nets, tol=0.02):
    """Remove dead-end copper: tracks with a free end, vias touched by fewer than
    two things. Anything this misses KiCad's dangling report catches next round."""
    total = 0
    for _ in range(80):
        rm = []
        for net in nets:
            if net in ZONE or not net:
                continue
            its = [it for it in m.items if it["net"] == net]
            for it in its:
                if it["kind"] == "track":
                    if not all(_attached(it, p, its, tol) for p in (it["a"], it["c"])):
                        rm.append(it["uuid"])
                elif it["kind"] == "via":
                    n = sum(1 for o in its if o is not it and o["geom"] is not None and o["geom"].distance(it["geom"]) < tol)
                    if n <= 1:
                        rm.append(it["uuid"])
        if not rm:
            break
        m.remove(rm)
        m.index()
        total += len(rm)
    return total


def connect_all(m, net, margin=2.0, layers_try=(("F", "B"), ("F", "In2", "B")), max_steps=12, keepout=None, extra_pen=None):
    w = rr2.default_width(m, net)
    for _ in range(max_steps):
        cs = rr2.comps(m, net)
        if len(cs) <= 1:
            return True
        done = False
        for lays in layers_try:
            for mg in (margin, margin + 2.5):
                r, win = rr2.connect_once(m, net, cs, w, lays, mg, keepout=keepout, extra_pen=extra_pen)
                if r and not r[2]:
                    rr2.commit(m, net, r[0], r[1], w)
                    done = True
                    break
            if done:
                break
        if not done:
            return False
    return len(rr2.comps(m, net)) <= 1


def net_len(m, net):
    its = m.net_items(net, ("track", "via"))
    return (sum(math.dist(it["a"], it["c"]) for it in its if it["kind"] == "track"),
            sum(1 for it in its if it["kind"] == "via"))


def try_orders(base_path, must, rest, max_success=3, max_orders=30, log=print, **kw):
    results = []
    orders = [tuple(must) + p for p in itertools.permutations(rest)]
    for order in orders[:max_orders]:
        m = lr.Model(base_path)
        ok = True
        for net in order:
            if not connect_all(m, net, **kw):
                log("   order %s: %s failed" % (",".join(order), net))
                ok = False
                break
        if ok:
            score = sum(net_len(m, n)[0] + 2.0 * net_len(m, n)[1] for n in order)
            log("   order %s: OK score %.1f  %s" % (",".join(order), score, {n: tuple(round(v, 1) for v in net_len(m, n)) for n in order}))
            results.append((score, order))
            if len(results) >= max_success:
                break
    return sorted(results)


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()
