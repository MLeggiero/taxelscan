"""rr3.py - octilinear routing on lr.Model: 0, 45 and 90 degrees only, bend-costed.

rr2.route2 finds a cell path and then simplifies it by line of sight, which is
what leaves segments at arbitrary angles - "jagged" routing. route3 instead
searches (cell, direction) with grid_route3, which charges for every bend and
refuses acute ones, and simplifies only by merging runs of one direction. Every
segment it emits is exactly horizontal, vertical or diagonal.

Ends are not snapped to pad or via centres: the search starts and stops on grid
points inside the terminal's copper, which KiCad counts as connected (a track
end inside a pad, a via or another track), so no off-angle stub is ever added
to reach an exact centre.
"""
import collections
import math
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import shapely
from shapely.geometry import Point, box
from scipy.ndimage import distance_transform_edt
import lr
import rr2

GRE3 = os.path.join(lr.SCR, "grid_route3" + lr.EXE)
LAYERS = lr.LAYERS
LAYER_PEN = {"F": 0, "In2": 30, "B": 3}


def terminals(items, w):
    """Like rr2.terminals, but a pad is entered near its centre only, so traces
    arrive at the pad the way they would be drawn by hand."""
    out = []
    for it in items:
        if it["kind"] == "pad":
            if it["geom"] is None:
                continue
            core = it["geom"].buffer(-w / 2 + 0.004)
            c = Point(it["xy"])
            g = c.buffer(0.04) if (not core.is_empty and core.contains(c)) else (core if not core.is_empty else c.buffer(0.014))
            out += [(l, g, None) for l in it["lay"] if l in LAYERS]
        elif it["kind"] == "via":
            out += [(l, Point(it["xy"]).buffer(0.04), None) for l in LAYERS]
        else:
            l = next(iter(it["lay"]))
            if l in LAYERS:
                out += [(l, it["geom"].buffer(-it["w"] / 2 + 0.004) if it["w"] > 0.02 else Point(it["a"]).buffer(0.014), None)]
    return out


def route3(m, net, src, dst, win, w=0.15, layers=("F", "B"), via=(0.5, 0.3), ignore=frozenset(), g=0.025,
           via_cost=700, bend45=40, bend90=120, tlimit=30, keepout=None, extra_pen=None, align=None, soft=0):
    """src/dst: [(layer, geom, _)]. Returns (segs, vias, bad) or None.
    align: an (x, y) the grid should pass through exactly (the start pad's centre).
    soft > 0 with an ignore set: as in rr2.route2, a cell that is free only because
    ignored copper is pretended away costs `soft` extra, so a rip-up search
    crosses as little of other nets' copper as it can."""
    ex0, ey0, ex1, ey1 = m.edge
    win = [max(win[0], ex0), max(win[1], ey0), min(win[2], ex1), min(win[3], ey1)]
    if align is not None:
        win[0] = align[0] - math.floor((align[0] - win[0]) / g) * g
        win[1] = align[1] - math.floor((align[1] - win[1]) / g) * g
    win = tuple(win)
    M = m.masks(net, win, g, w, ignore, via)
    M0 = m.masks(net, win, g, w, frozenset(), via) if (soft and ignore) else None
    ny, nx = M["ny"], M["nx"]
    n = nx * ny
    X, Y = M["X"], M["Y"]
    fb = np.zeros((ny, nx), np.uint8)
    gb = np.zeros((ny, nx), np.uint8)
    pen = np.zeros((len(LAYERS), ny, nx), np.uint8)
    for k, l in enumerate(LAYERS):
        if l not in layers:
            continue
        f = M["free"][l].copy()
        for tl, tg, _ in src + dst:
            if tl == l:
                f |= shapely.intersects_xy(tg, X, Y) & M["own"][l]
        if keepout is not None and l in keepout:
            f &= ~shapely.intersects_xy(keepout[l], X, Y) | M["own"][l]
        M["free"][l] = f
        fb |= f.astype(np.uint8) << k
        p = np.full((ny, nx), LAYER_PEN[l], np.int32)
        if extra_pen is not None and l in extra_pen:
            for poly, val in extra_pen[l]:
                p += np.where(shapely.intersects_xy(poly, X, Y), val, 0)
        if M0 is not None:
            p += np.where(M["free"][l] & ~M0["free"][l], soft, 0)
        pen[k] = np.clip(p, 0, 255).astype(np.uint8)
    starts = []
    for tl, tg, _ in src:
        if tl in layers:
            k = LAYERS.index(tl)
            ii, jj = np.nonzero(shapely.intersects_xy(tg, X, Y) & M["free"][tl])
            starts += list(k * n + ii * nx + jj)
    for tl, tg, _ in dst:
        if tl in layers:
            k = LAYERS.index(tl)
            gb |= (shapely.intersects_xy(tg, X, Y) & M["free"][tl]).astype(np.uint8) << k
    if not starts or not gb.any():
        return None
    via_ok = M["via_ok"].copy()
    if len([l for l in layers if l in LAYERS]) < 2:
        via_ok[:] = False
    else:                                   # never a via in an F.Cu-only pad
        its, tree = m.by_layer["F"]
        for kk in tree.query(box(*win)):
            it = its[int(kk)]
            if it["kind"] == "pad" and it["lay"] == {"F"}:
                bb = it["geom"].buffer(via[0] / 2 + 0.05)
                via_ok &= ~shapely.intersects_xy(bb, X, Y)
    # admissible: octile distance in cost units is >= 10 x euclidean cells
    hs = (distance_transform_edt(gb == 0) * 10.0).astype(np.float32)
    fin = os.path.join(lr.SCR, "g3_in.%d.bin" % os.getpid())
    fout = os.path.join(lr.SCR, "g3_out.%d.bin" % os.getpid())
    seeds = np.array(sorted(set(int(s) for s in starts)), dtype=np.uint32)
    with open(fin, "wb") as fh:
        fh.write(struct.pack("IIIIIIII", ny, nx, len(LAYERS), len(seeds), via_cost, tlimit, bend45, bend90))
        seeds.tofile(fh)
        fb.tofile(fh)
        via_ok.astype(np.uint8).tofile(fh)
        gb.tofile(fh)
        hs.tofile(fh)
        pen.tofile(fh)
    subprocess.run([GRE3, fin, fout], check=True, capture_output=True)
    with open(fout, "rb") as fh:
        cnt = struct.unpack("I", fh.read(4))[0]
        ids = np.fromfile(fh, dtype=np.uint32, count=cnt)
    if not cnt:
        return None
    path = [(LAYERS[int(v) // n], round(win[0] + (int(v) % n % nx) * g, 5), round(win[1] + (int(v) % n // nx) * g, 5))
            for v in ids]
    segs, vias = runs(path)
    bad = m.verify(net, segs, vias, w, via, ignore)
    return segs, vias, bad


def runs(path):
    """Cell path -> (segments, vias): one segment per run of constant direction."""
    segs, vias = [], []
    i = 0
    while i < len(path) - 1:
        a, b = path[i], path[i + 1]
        if a[0] != b[0]:                       # layer change: a via at this cell
            vias.append((b[1], b[2]))
            i += 1
            continue
        d = (round((b[1] - a[1]) * 1e4), round((b[2] - a[2]) * 1e4))
        j = i + 1
        while j < len(path) - 1:
            c = path[j + 1]
            if c[0] != a[0]:
                break
            e = (round((c[1] - path[j][1]) * 1e4), round((c[2] - path[j][2]) * 1e4))
            if e != d:
                break
            j += 1
        segs.append((a[0], (a[1], a[2]), (path[j][1], path[j][2])))
        i = j
    return segs, list(dict.fromkeys(vias))


def length(segs):
    return sum(math.dist(a, c) for _l, a, c in segs)


def connect(m, net, w, layers, margin=2.0, ignore=frozenset(), keepout=None, extra_pen=None, **kw):
    """Join the net's components one at a time, nearest first, octilinear.
    Returns (ok, added uuids)."""
    added = set()
    for _ in range(40):
        cs = rr2.comps(m, net)
        if len(cs) <= 1:
            return True, added
        main = cs[0]
        mg = shapely.union_all([rr2.geo(it) for it in main])
        others = sorted(cs[1:], key=lambda c: mg.distance(shapely.union_all([rr2.geo(it) for it in c])))
        done = False
        for other in others[:3]:
            og = shapely.union_all([rr2.geo(it) for it in other])
            d0 = mg.distance(og)
            near = [it for it in main if rr2.geo(it).distance(og) <= d0 + 3.0] or main
            bx = shapely.union_all([rr2.geo(it) for it in near] + [og]).bounds
            for mg_ in (margin, margin + 2.0):
                win = (bx[0] - mg_, bx[1] - mg_, bx[2] + mg_, bx[3] + mg_)
                start_pad = next((it for it in other if it["kind"] == "pad"), None)
                area = (win[2] - win[0]) * (win[3] - win[1])
                # a coarse grid for long connections keeps the search small;
                # the exact check afterwards is the same either way
                r = None
                for g in ((0.05, 0.025) if area > 250 else (0.025,)):
                    r = route3(m, net, terminals(other, w), terminals(near, w), win, w, layers, ignore=ignore,
                               keepout=keepout, extra_pen=extra_pen, g=g,
                               align=start_pad["xy"] if start_pad else None, **kw)
                    if r and not r[2]:
                        break
                if r and not r[2]:
                    before = {t.m_Uuid.AsString() for t in m.b.GetTracks()}
                    m.add(net, r[0], r[1], w)
                    m.index()
                    added |= {t.m_Uuid.AsString() for t in m.b.GetTracks()} - before
                    done = True
                    break
            if done:
                break
        if not done:
            return False, added
    return len(rr2.comps(m, net)) <= 1, added


def connect_once(m, net, cs, w, layers, margin, ignore=frozenset(), keepout=None, extra_pen=None, soft=0, **kw):
    """One octilinear connection from the nearest other component to the main one."""
    main = cs[0]
    mg = shapely.union_all([rr2.geo(it) for it in main])
    other = min(cs[1:], key=lambda c: mg.distance(shapely.union_all([rr2.geo(it) for it in c])))
    og = shapely.union_all([rr2.geo(it) for it in other])
    d0 = mg.distance(og)
    near = [it for it in main if rr2.geo(it).distance(og) <= d0 + 3.0] or main
    bx = shapely.union_all([rr2.geo(it) for it in near] + [og]).bounds
    win = (bx[0] - margin, bx[1] - margin, bx[2] + margin, bx[3] + margin)
    start_pad = next((it for it in other if it["kind"] == "pad"), None)
    area = (win[2] - win[0]) * (win[3] - win[1])
    r = None
    for g in ((0.05, 0.025) if area > 250 else (0.025,)):
        r = route3(m, net, terminals(other, w), terminals(near, w), win, w, layers, ignore=ignore, keepout=keepout,
                   extra_pen=extra_pen, g=g, align=start_pad["xy"] if start_pad else None, soft=soft, **kw)
        if r and not r[2]:
            break
    return r, win


def rrr3(m, must, protect=rr2.PROTECT, layers_fn=None, width_fn=None, max_iter=80, margin=2.0, log=print,
         locked=(), no_rip=(), keepout=None, extra_pen=None, fallback_layers=None, max_rip=6):
    """rr2.rrr with octilinear connections: route each net's components together;
    when blocked, route through other nets' copper (soft-costed), rip what was
    crossed and queue those nets again. fallback_layers(net) may widen a net's
    layers on a second try (None: no fallback)."""
    locked = set(locked)
    queue = list(must)
    ripped = collections.Counter()
    failed = []
    touched = set(must)
    width_fn = width_fn or (lambda net: rr2.default_width(m, net))
    layers_fn = layers_fn or (lambda net: ("F", "B"))
    it_n = 0
    while queue and it_n < max_iter:
        it_n += 1
        net = queue.pop(0)
        rr2.delete_dead(m, [net])
        cs = rr2.comps(m, net)
        if len(cs) <= 1:
            continue
        w, lays = width_fn(net), layers_fn(net)
        r, win = connect_once(m, net, cs, w, lays, margin, keepout=keepout, extra_pen=extra_pen)
        if not (r and not r[2]):
            r, win = connect_once(m, net, cs, w, lays, margin + 2.0, keepout=keepout, extra_pen=extra_pen)
        if r and not r[2]:
            rr2.commit(m, net, r[0], r[1], w)
            log("  %-14s routed  %5.1f mm %d via  (%d comps left)" % (net, length(r[0]), len(r[1]), len(cs) - 2))
            queue.insert(0, net)
            continue
        wb = box(*win)
        ign = frozenset(it["uuid"] for it in m.items if it["kind"] in ("track", "via") and it["net"] != net
                        and it["net"] not in protect and it["net"] not in locked and it["net"] not in no_rip
                        and ripped[it["net"]] < max_rip and it["geom"].intersects(wb))
        r, win = connect_once(m, net, cs, w, lays, margin, ignore=ign, keepout=keepout, extra_pen=extra_pen, soft=60)
        if (not r or r[2]) and fallback_layers is not None and tuple(fallback_layers(net)) != tuple(lays):
            r2, win2 = connect_once(m, net, cs, w, fallback_layers(net), margin + 1.5, ignore=ign, keepout=keepout,
                                    extra_pen=extra_pen, soft=60)
            if r2 and not r2[2]:
                r, win = r2, win2
        if not r or r[2]:
            log("  %-14s FAILED (%s)" % (net, r[2] if r else "no path"))
            failed.append(net)
            continue
        segs, vias, _ = r
        hit = rr2.conflicts(m, net, segs, vias, w)
        rip = [u for u in hit if u in ign]
        nets_hit = sorted({m.uuid[u]["net"] for u in rip})
        m.remove(rip)
        m.index()
        rr2.commit(m, net, segs, vias, w)
        for bn in nets_hit:
            ripped[bn] += 1
            touched.add(bn)
            if bn not in queue:
                queue.append(bn)
        log("  %-14s routed through %d item(s) of %s; %5.1f mm %d via" % (net, len(rip), nets_hit, length(segs), len(vias)))
        queue.insert(0, net)
    left = {n: len(rr2.comps(m, n)) for n in touched if len(rr2.comps(m, n)) > 1}
    return dict(failed=failed, left=left, touched=sorted(touched), ripped=dict(ripped), iters=it_n)
