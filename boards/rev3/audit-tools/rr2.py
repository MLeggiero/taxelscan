"""rr2.py - costed routing and rip-up-and-reroute on top of lr.Model."""
import sys, os, math, struct, subprocess, collections, time
SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
import lr
import numpy as np
import shapely
from shapely.geometry import box, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree
from scipy.ndimage import distance_transform_edt

GRE2 = os.path.join(lr.SCR, "grid_route2.exe")
LAYERS = lr.LAYERS
LAYER_PEN = {"F": 0, "In2": 16, "B": 3}
PROTECT = set("GND +3.3V +5V +5V_BUS +5V_USB USB_BUS_SW VCORE VREG_LX VREG_AVDD ADC_AVDD SW_NODE ROW_VCC "
              "SENSE_A SENSE_B GAIN_A GAIN_B AMP_A AMP_B ADC_A ADC_B VREF XIN XOUT XOUT_MCU FB RAIL_MON "
              "USB_D_P USB_D_N USBC_D_P USBC_D_N BUS_P BUS_N SYNC_P SYNC_N".split())
FINE_PARTS = ("U9.", "U13.", "U8.")


def geo(it):
    return it["geom"] if it["geom"] is not None else it["hole"]


def anchors(it):
    return [it["a"], it["c"]] if it["kind"] == "track" else [it["xy"]]


def joined(a, ga, b, gb):
    """KiCad-style connectivity: an anchor (track end, via or pad centre) of one
    item inside the other's copper. Two tracks that merely cross do NOT connect;
    two pads connect when their copper overlaps."""
    if a["kind"] == "pad" and b["kind"] == "pad":
        return ga.intersects(gb)
    return (any(gb.distance(Point(p)) < 0.01 for p in anchors(a)) or
            any(ga.distance(Point(p)) < 0.01 for p in anchors(b)))


def comps(m, net):
    its = [it for it in m.items if it["net"] == net]
    if not its:
        return []
    geoms = [geo(it) for it in its]
    tree = STRtree(geoms)
    parent = list(range(len(its)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, g in enumerate(geoms):
        for j in tree.query(g):
            j = int(j)
            if j <= i or not (its[i]["lay"] & its[j]["lay"]):
                continue
            if joined(its[i], g, its[j], geoms[j]):
                parent[find(i)] = find(j)
    groups = collections.defaultdict(list)
    for i, it in enumerate(its):
        groups[find(i)].append(it)
    return sorted(groups.values(), key=lambda c: (-sum(1 for it in c if it["kind"] == "pad"), -len(c)))


def terminals(comp, w):
    out = []
    for it in comp:
        if it["kind"] == "pad":
            if it["geom"] is None:
                continue
            g = it["geom"].buffer(-w / 2 + 0.004)
            if g.is_empty:
                g = Point(it["xy"]).buffer(0.014)
            out += [(l, g, None) for l in it["lay"] if l in LAYERS]
        elif it["kind"] == "via":
            out += [(l, Point(it["xy"]).buffer(0.014), it["xy"]) for l in LAYERS]
        else:
            l = next(iter(it["lay"]))
            if l in LAYERS:
                out += [(l, Point(p).buffer(0.014), p) for p in (it["a"], it["c"])]
    return out


def route2(m, net, src, dst, win, w=0.1, layers=("F", "B"), via=(0.5, 0.3), ignore=frozenset(), g=0.025,
           via_cost=500, hug=4, keepout=None, tlimit=25, extra_pen=None, soft=0):
    """src/dst: terminals [(layer, geom, snap_xy|None)]. Returns (segs, vias, bad) or None.
    soft > 0 with an ignore set: cells that are free only because ignored copper is
    pretended away cost `soft` extra per step, so the path crosses as little of it as it can."""
    ex0, ey0, ex1, ey1 = m.edge
    win = (max(win[0], ex0), max(win[1], ey0), min(win[2], ex1), min(win[3], ey1))
    M = m.masks(net, win, g, w, ignore, via)
    M0 = m.masks(net, win, g, w, frozenset(), via) if (soft and ignore) else None
    ny, nx = M["ny"], M["nx"]
    n = nx * ny
    X, Y = M["X"], M["Y"]
    fb = np.zeros((ny, nx), np.uint8)
    gb = np.zeros((ny, nx), np.uint8)
    pen = np.zeros((len(LAYERS), ny, nx), np.uint8)
    snaps = [(l, sp) for l, _, sp in src + dst if sp is not None]
    for k, l in enumerate(LAYERS):
        if l not in layers:
            continue
        f = M["free"][l].copy()
        for tl, tg, sp in src + dst:
            if tl == l and sp is not None:
                f |= shapely.intersects_xy(tg, X, Y)
        if keepout is not None and l in keepout:
            f &= ~shapely.intersects_xy(keepout[l], X, Y) | M["own"][l]
        M["free"][l] = f
        fb |= f.astype(np.uint8) << k
        p = np.full((ny, nx), LAYER_PEN[l], np.int32)
        if hug:
            p += np.where(distance_transform_edt(f) <= 2, hug, 0)
        if extra_pen is not None and l in extra_pen:
            for poly, val in extra_pen[l]:
                p += np.where(shapely.intersects_xy(poly, X, Y), val, 0)
        if M0 is not None:
            p += np.where(M["free"][l] & ~M0["free"][l], soft, 0)
            p += np.where(M["via_ok"] & ~M0["via_ok"], soft // 2, 0)
        pen[k] = np.clip(p, 0, 255).astype(np.uint8)
    starts = []
    for tl, tg, sp in src:
        if tl in layers:
            k = LAYERS.index(tl)
            ii, jj = np.nonzero(shapely.intersects_xy(tg, X, Y) & M["free"][tl])
            starts += list(k * n + ii * nx + jj)
    for tl, tg, sp in dst:
        if tl in layers:
            k = LAYERS.index(tl)
            gb |= (shapely.intersects_xy(tg, X, Y) & M["free"][tl]).astype(np.uint8) << k
    if not starts or not gb.any():
        return None
    via_ok = M["via_ok"].copy()
    if len([l for l in layers if l in LAYERS]) < 2:
        via_ok[:] = False
    else:
        wb = box(*win)
        its, tree = m.by_layer["F"]
        for k in tree.query(wb):
            it = its[int(k)]
            if it["kind"] == "pad" and it["lay"] == {"F"}:
                bb = it["geom"].buffer(via[0] / 2 + 0.05)
                x0, y0, x1, y1 = bb.bounds
                j0, j1 = max(0, int((x0 - win[0]) / g)), min(nx - 1, int(math.ceil((x1 - win[0]) / g)))
                i0, i1 = max(0, int((y0 - win[1]) / g)), min(ny - 1, int(math.ceil((y1 - win[1]) / g)))
                if j0 <= j1 and i0 <= i1:
                    via_ok[i0:i1 + 1, j0:j1 + 1] &= ~shapely.intersects_xy(bb, X[i0:i1 + 1, j0:j1 + 1], Y[i0:i1 + 1, j0:j1 + 1])
    hs = (distance_transform_edt(gb == 0) * 10.0).astype(np.float32)
    fin, fout = os.path.join(lr.SCR, "g2_in.bin"), os.path.join(lr.SCR, "g2_out.bin")
    seeds = np.array(sorted(set(int(s) for s in starts)), dtype=np.uint32)
    with open(fin, "wb") as fh:
        fh.write(struct.pack("IIIIII", ny, nx, len(LAYERS), len(seeds), via_cost, tlimit))
        seeds.tofile(fh)
        fb.tofile(fh)
        via_ok.astype(np.uint8).tofile(fh)
        gb.tofile(fh)
        hs.tofile(fh)
        pen.tofile(fh)
    subprocess.run([GRE2, fin, fout], check=True, capture_output=True)
    with open(fout, "rb") as fh:
        cnt = struct.unpack("I", fh.read(4))[0]
        ids = np.fromfile(fh, dtype=np.uint32, count=cnt)
    if not cnt:
        return None
    path = [(LAYERS[int(v) // n], win[0] + (int(v) % n % nx) * g, win[1] + (int(v) % n // nx) * g) for v in ids]

    def snap(node):
        l, x, y = node
        for sl, sp in snaps:
            if sl == l and math.hypot(sp[0] - x, sp[1] - y) < 0.03:
                return (l, sp[0], sp[1])
        return node
    path[0], path[-1] = snap(path[0]), snap(path[-1])
    segs, vias = m.simplify(net, path, w, via, ignore)
    vias = list(dict.fromkeys(vias))
    bad = m.verify(net, segs, vias, w, via, ignore)
    return segs, vias, bad


def commit(m, net, segs, vias, w, via=(0.5, 0.3)):
    m.add(net, segs, vias, w, via)
    m.index()


def delete_dead(m, nets):
    dead = []
    for net in nets:
        for c in comps(m, net):
            if not any(it["kind"] == "pad" for it in c):
                dead += [it["uuid"] for it in c if it["kind"] in ("track", "via")]
    if dead:
        m.remove(dead)
        m.index()
    return len(dead)


def default_width(m, net):
    return 0.1 if any(it["kind"] == "pad" and it["ref"].startswith(FINE_PARTS) for it in m.net_items(net, ("pad",))) else 0.15


def conflicts(m, net, segs, vias, w, via=(0.5, 0.3)):
    hit = {}
    for l, a, c in segs:
        for b in m.check_track(net, l, a, c, w):
            hit[b[4]] = b
    for x, y in vias:
        for b in m.check_via(net, x, y, via[0], via[1]):
            hit[b[4]] = b
    return hit


def prune(m, path, tag="prune", rounds=8, log=print):
    """save, DRC, delete what KiCad calls dangling, repeat. Returns the last (drc, counter)."""
    for _ in range(rounds):
        m.save(path)
        d, c = lr.drc(path, tag)
        ids = {i["uuid"] for v in d["violations"] if v["type"] in ("track_dangling", "via_dangling") for i in v["items"]}
        if not ids:
            return d, c
        nets = {m.uuid[u]["net"] for u in ids if u in m.uuid}
        n = m.remove(ids)
        m.index()
        import grp
        n2 = grp.peel(m, sorted(nets))
        log("  pruned %d dangling item(s), peeled %d more" % (n, n2))
    m.save(path)
    return lr.drc(path, tag)


def connect_once(m, net, cs, w, layers, margin, ignore=frozenset(), keepout=None, extra_pen=None):
    main = cs[0]
    mg = unary_union([geo(it) for it in main])
    others = sorted(cs[1:], key=lambda c: mg.distance(unary_union([geo(it) for it in c])))
    other = others[0]
    og = unary_union([geo(it) for it in other])
    d0 = mg.distance(og)
    near = [it for it in main if geo(it).distance(og) <= d0 + 3.0] or main
    bx = unary_union([geo(it) for it in near] + [og]).bounds
    win = (bx[0] - margin, bx[1] - margin, bx[2] + margin, bx[3] + margin)
    r = route2(m, net, terminals(near, w), terminals(other, w), win, w, layers, ignore=ignore, keepout=keepout,
               extra_pen=extra_pen, soft=100 if ignore else 0)
    return r, win


def rrr(m, must, protect=PROTECT, layers_fn=None, width_fn=None, max_iter=60, margin=2.0, log=print,
        locked=(), no_rip=(), keepout=None, extra_pen=None, fallback_layers=("F", "In2", "B")):
    locked = set(locked)
    queue = list(must)
    ripped = collections.Counter()
    failed = []
    touched = set(must)
    width_fn = width_fn or (lambda net: default_width(m, net))
    layers_fn = layers_fn or (lambda net: ("F", "B"))
    it_n = 0
    while queue and it_n < max_iter:
        it_n += 1
        net = queue.pop(0)
        delete_dead(m, [net])
        cs = comps(m, net)
        if len(cs) <= 1:
            if net in must:
                locked.add(net)
            continue
        w, lays = width_fn(net), layers_fn(net)
        r, win = connect_once(m, net, cs, w, lays, margin, keepout=keepout, extra_pen=extra_pen)
        if r and not r[2]:
            commit(m, net, r[0], r[1], w)
            log("  %-14s routed  %5.1f mm %d via  (%d comps left)" % (net, sum(math.dist(a, c) for _, a, c in r[0]), len(r[1]), len(cs) - 1))
            queue.insert(0, net)
            continue
        # blocked: what would it take?
        wb = box(*win)
        ign = frozenset(it["uuid"] for it in m.items if it["kind"] in ("track", "via") and it["net"] != net
                        and it["net"] not in protect and it["net"] not in locked and it["net"] not in no_rip
                        and ripped[it["net"]] < 6 and it["geom"].intersects(wb))
        r, win = connect_once(m, net, cs, w, lays, margin, ignore=ign, keepout=keepout, extra_pen=extra_pen)
        if not r or r[2]:
            r2, win2 = connect_once(m, net, cs, w, fallback_layers, margin + 1.5, ignore=ign, keepout=keepout, extra_pen=extra_pen)
            if r2 and not r2[2]:
                r, win = r2, win2
            else:
                log("  %-14s FAILED (bad=%s)" % (net, (r or r2 or (None, None, "no path"))[2] if (r or r2) else "no path"))
                failed.append(net)
                continue
        segs, vias, _ = r
        hit = conflicts(m, net, segs, vias, w)
        rip = [u for u in hit if u in ign]
        nets_hit = sorted({m.uuid[u]["net"] for u in rip})
        m.remove(rip)
        m.index()
        commit(m, net, segs, vias, w)
        for bn in nets_hit:
            ripped[bn] += 1
            touched.add(bn)
            if bn not in queue:
                queue.append(bn)
        log("  %-14s routed through %d item(s) of %s; %5.1f mm %d via" % (net, len(rip), nets_hit, sum(math.dist(a, c) for _, a, c in segs), len(vias)))
        queue.insert(0, net)
    left = {n: len(comps(m, n)) for n in touched if len(comps(m, n)) > 1}
    return dict(failed=failed, left=left, touched=sorted(touched), ripped=dict(ripped), iters=it_n)
