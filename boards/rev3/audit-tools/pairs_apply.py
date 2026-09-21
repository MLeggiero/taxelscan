"""pairs_apply.py base.kicad_pcb out.kicad_pcb

Rip BUS_P/N and SYNC_P/N, lay the hand-designed pairs from pairgeo.py, move
whatever the new copper collides with, and put the displaced nets back with the
router (power at 0.3 mm, F.Cu/B.Cu only so the +3.3V pour on In2 is not cut).
GND pads that lose their stitching via get a new one BEFORE anything else is
re-routed, so they are not crowded out. The address straps are left for the
address group that runs next. A DRC-driven close loop finishes whatever KiCad
still calls unconnected. `out` must sit next to a copy of rev3.kicad_pro/.kicad_dru.
"""
import sys, os, math, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr, rr2, grp, pairgeo
import numpy as np
import shapely
from scipy.ndimage import label
from shapely.geometry import box, Point
from shapely.ops import unary_union

PAIR = ("BUS_P", "BUS_N", "SYNC_P", "SYNC_N")
POWER_W = {"+5V": 0.3, "+5V_BUS": 0.3, "+5V_USB": 0.3, "USB_BUS_SW": 0.3}
FB = (("F", "B"),)
LATER = ("ADDR0", "ADDR1", "ADDR2")


def width_of(m, net):
    return POWER_W.get(net) or rr2.default_width(m, net)


def restitch(m, pad, radius=2.2, w=0.15):
    """a short F.Cu track from a GND pad to a fresh via onto the plane"""
    cx, cy = pad["xy"]
    win = (cx - radius, cy - radius, cx + radius, cy + radius)
    M = m.masks("GND", win, 0.025, w, frozenset(), (0.5, 0.3))
    X, Y = M["X"], M["Y"]
    seed = shapely.intersects_xy(pad["geom"].buffer(-w / 2 + 0.004), X, Y) & M["free"]["F"]
    lab, _ = label(M["free"]["F"], structure=np.ones((3, 3)))
    pocket = np.isin(lab, [k for k in np.unique(lab[seed]) if k])
    cand = pocket & M["via_ok"]
    its, tree = m.by_layer["F"]
    for k in tree.query(box(*win)):
        it = its[int(k)]
        if it["kind"] == "pad" and it["lay"] == {"F"}:
            cand &= ~shapely.intersects_xy(it["geom"].buffer(0.30), X, Y)
    ii, jj = np.nonzero(cand)
    if not len(ii):
        return False
    order = np.argsort((X[ii, jj] - cx) ** 2 + (Y[ii, jj] - cy) ** 2)
    for k in order[:60]:
        vx, vy = round(float(X[ii[k], jj[k]]), 4), round(float(Y[ii[k], jj[k]]), 4)
        if m.check_via("GND", vx, vy):
            continue
        r = rr2.route2(m, "GND", rr2.terminals([pad], w), [("F", Point(vx, vy).buffer(0.014), (vx, vy))], win, w, ("F",))
        if r and not r[2]:
            rr2.commit(m, "GND", r[0], [(vx, vy)], w)
            return True
    return False


def gnd_join(m, pad, radius=3.0, w=0.15):
    """fallback: an F.Cu track from a GND pad to the nearest existing GND via or through-hole GND pad"""
    cx, cy = pad["xy"]
    win = (cx - radius, cy - radius, cx + radius, cy + radius)
    targets = [it for it in m.items if it["net"] == "GND" and it["uuid"] != pad["uuid"]
               and (it["kind"] == "via" or (it["kind"] == "pad" and len(it["lay"]) > 1))
               and math.dist(it["xy"], (cx, cy)) < radius]
    if not targets:
        return False
    r = rr2.route2(m, "GND", rr2.terminals([pad], w), rr2.terminals(targets, w), win, w, ("F",))
    if r and not r[2]:
        rr2.commit(m, "GND", r[0], r[1], w)
        return True
    return False


def stitch(m, pad, log=print):
    ok = restitch(m, pad) or gnd_join(m, pad)
    log("    stitch %-7s %s" % (pad["ref"], "ok" if ok else "FAILED"))
    return ok


def grounded(m, pad):
    for c in rr2.comps(m, "GND"):
        if any(i["uuid"] == pad["uuid"] for i in c):
            return any(i["kind"] == "via" or (i["kind"] == "pad" and len(i["lay"]) > 1) for i in c)
    return False


def connect_net(m, net, w, layers_try=FB):
    for _ in range(14):
        cs = rr2.comps(m, net)
        if len(cs) <= 1:
            return True
        done = False
        for lays in layers_try:
            for mg in (2.0, 4.5):
                r, win = rr2.connect_once(m, net, cs, w, lays, mg)
                if r and not r[2]:
                    rr2.commit(m, net, r[0], r[1], w)
                    done = True
                    break
            if done:
                break
        if not done:
            return False
    return True


def connect_pair(m, net, ca, cb, w, lays, margin):
    ga = unary_union([rr2.geo(it) for it in ca])
    gb = unary_union([rr2.geo(it) for it in cb])
    d0 = ga.distance(gb)
    na = [it for it in ca if rr2.geo(it).distance(gb) <= d0 + 3.0] or ca
    nb = [it for it in cb if rr2.geo(it).distance(ga) <= d0 + 3.0] or cb
    bx = unary_union([rr2.geo(it) for it in na + nb]).bounds
    win = (bx[0] - margin, bx[1] - margin, bx[2] + margin, bx[3] + margin)
    return rr2.route2(m, net, rr2.terminals(na, w), rr2.terminals(nb, w), win, w, lays)


def close_loop(m, out, rounds=4, skip=(), use_rrr=False, log=print):
    """reconnect what KiCad calls unconnected, between the components holding the
    two items KiCad names; GND pads get a stitch. No rip-up unless use_rrr."""
    d = c = None
    for rnd in range(rounds):
        m.save(out)
        d, c = lr.drc(out, "close_loop")
        opens = d["unconnected_items"]
        log("  close round %d: %d open" % (rnd, len(opens)))
        if not opens:
            break
        progress = False
        for v in opens:
            its = [m.uuid.get(i["uuid"]) for i in v["items"]]
            its = [it for it in its if it]
            if len(its) < 2:
                continue
            net = its[0]["net"]
            if net in skip:
                continue
            if net == "GND":
                # KiCad names one item on each side; stitch the pads of whichever side is
                # an island, never a pad that already reaches the plane
                cs = rr2.comps(m, "GND")
                for it in its:
                    comp = next((x for x in cs if any(i["uuid"] == it["uuid"] for i in x)), [it])
                    if any(i["kind"] == "via" or (i["kind"] == "pad" and len(i["lay"]) > 1) for i in comp):
                        continue
                    for p in [i for i in comp if i["kind"] == "pad" and i["lay"] == {"F"}]:
                        progress |= stitch(m, p, log)
                continue
            if net in grp.ZONE:
                log("    %s open on a pour net, left for inspection: %s" % (net, [it.get("ref") for it in its]))
                continue
            w = width_of(m, net)
            cs = rr2.comps(m, net)
            ua, ub = its[0]["uuid"], its[1]["uuid"]
            ca = next((x for x in cs if any(i["uuid"] == ua for i in x)), [its[0]])
            cb = next((x for x in cs if any(i["uuid"] == ub for i in x)), [its[1]])
            if ca is cb:
                ca, cb = [its[0]], [its[1]]
            ok = False
            for lays in ((("B", "F"),) if net in POWER_W else (("F", "B"), ("F", "In2", "B"))):
                for mg in (2.0, 4.5):
                    r = connect_pair(m, net, ca, cb, w, lays, mg)
                    if r and not r[2]:
                        rr2.commit(m, net, r[0], r[1], w)
                        ok = True
                        break
                if ok:
                    break
            if ok:
                log("    %-14s reconnected" % net)
                progress = True
            elif use_rrr:
                res = rr2.rrr(m, [net], protect=set(rr2.PROTECT) | set(PAIR), max_iter=10, layers_fn=lambda n: ("F", "B"),
                              width_fn=lambda n: width_of(m, n), fallback_layers=("F", "B"), log=log)
                log("    %-14s rrr: %s" % (net, {k: res[k] for k in ("failed", "left", "ripped")}))
                progress |= not res["failed"]
            else:
                log("    %-14s could not reconnect" % net)
        if not progress:
            break
    return d, c


def apply(base, out, log=print):
    m = lr.Model(base)
    m.remove([it["uuid"] for it in m.items if it["kind"] in ("track", "via") and it["net"] in PAIR])
    m.index()
    segs, vias = pairgeo.geometry()
    hits = {}
    for net, l, a, b in segs:
        for x in m.check_track(net, l, a, b, pairgeo.W):
            hits[x[4]] = x
    for net, (x, y) in vias:
        for bb in m.check_via(net, x, y, *pairgeo.VIA):
            hits[bb[4]] = bb
    hit_items = [m.uuid[u] for u in hits if u in m.uuid]
    bad_pads = [it for it in hit_items if it["kind"] == "pad"]
    if bad_pads:
        log("PAIR GEOMETRY HITS PADS: %s" % [(it["ref"], it["net"]) for it in bad_pads])
        return None
    near_gnd = set()
    for it in hit_items:
        if it["net"] == "GND" and it["kind"] == "via":
            for p in m.items:
                if p["kind"] == "pad" and p["net"] == "GND" and p["lay"] == {"F"} and p["geom"].distance(it["geom"]) < 1.2:
                    near_gnd.add(p["uuid"])
    rip = [it["uuid"] for it in hit_items if it["kind"] in ("track", "via")]
    nets = sorted({m.uuid[u]["net"] for u in rip})
    log("ripping %d item(s) on %s" % (len(rip), dict(collections.Counter(m.uuid[u]["net"] for u in rip))))
    m.remove(rip)
    m.index()
    sig = [n for n in nets if n not in grp.ZONE]
    rr2.delete_dead(m, sig)
    log("pre-peel %d" % grp.peel(m, sig))
    for net in PAIR:
        m.add(net, [(l, a, b) for n, l, a, b in segs if n == net], [p for n, p in vias if n == net], pairgeo.W, pairgeo.VIA)
    m.index()
    for net in PAIR:
        bad = m.verify(net, [(l, a, b) for n, l, a, b in segs if n == net], [p for n, p in vias if n == net], pairgeo.W, pairgeo.VIA)
        if bad:
            log("VERIFY %s: %s" % (net, bad[:4]))
    # ground first
    for u in sorted(near_gnd):
        p = m.uuid.get(u)
        if p and not grounded(m, p):
            stitch(m, p, log)
    for n in sorted(sig, key=lambda n: (n not in POWER_W, n)):
        if n in LATER:
            continue
        ok = connect_net(m, n, width_of(m, n))
        log("  reconnect %-14s w%.2f %s  %s" % (n, width_of(m, n), "ok" if ok else "deferred", grp.net_len(m, n)))
    log("peel %d" % grp.peel(m, sig))
    d, c = close_loop(m, out, skip=LATER, use_rrr=False, log=log)
    d, c = rr2.prune(m, out, "pairs_prune", rounds=16, log=log)
    return d, c


if __name__ == "__main__":
    d, c = apply(sys.argv[1], sys.argv[2])
    lr.summary(d, c)
