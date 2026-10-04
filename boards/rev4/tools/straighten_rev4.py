"""straighten_rev4.py in.kicad_pcb out.kicad_pcb [--passes N] [--nets A,B,...] [--planes-only]

Re-route the board net by net with rr3's octilinear router, so that every
segment is horizontal, vertical or at 45 degrees.

rev-4 inherited rev-3's routing, and 63 % of rev-3's copper ran at some other
angle: grid-router staircases simplified by line of sight, and the 17 September
compaction, which let every track corner move toward an edge and turn up to 15
degrees per pass. This was the first way tried to straighten it - keep the
proven topology and redraw each net in it - before the full re-route
(freeroute_rev4.py) replaced it: one net at a time it was too slow for the
whole board. --planes-only is what the re-route still uses it for. Per net:

  - one net at a time: rip its tracks and vias, reconnect its pads nearest-first
    with route3 (0/45/90 only, a cost per bend, no acute corners), in a window
    around the net, on the layers it used before;
  - keep the result only if every pad is connected, every segment and via
    passes the exact clearance check (0.15 mm, 0.10 mm inside U9_escape /
    U13_escape, 0.25 mm to holes, 0.22 mm to the edge), it adds at most one via
    and it is no more than 35 % + 1 mm longer; otherwise put the old copper back
    exactly as it was;
  - the analog rules hold: SENSE / GAIN / ADC / RAIL_MON on F.Cu only; In2 (the
    +3.3 V plane layer) only as a last resort, at a high cost;
  - the differential pairs: N is routed after P with every cell outside a band
    at the pair's 0.30 mm pitch from P costed, so it runs beside P;
  - GND and +3.3 V reach their planes through pad-to-via stubs; each stub group
    is redrawn between the pads and vias it joined;
  - a second pass retries what failed, now that its neighbours take less room.

Then KiCad's DRC decides (prune by its dangling report, zones refilled).

--planes-only redraws the GND / +3.3 V stub groups and nothing else: the step
that follows a freerouting re-route (fr_finish.py), which leaves the planes'
copper exactly as the base board had it.
"""
import argparse
import collections
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr
import rr2
import rr3
import grp_route as grp
import shapely
from shapely.geometry import box, Point
from shapely.ops import unary_union

PLANE = ("GND", "+3.3V")
F_ONLY = {"SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B", "ADC_A", "ADC_B", "RAIL_MON"}
PAIRS = [("BUS_P", "BUS_N"), ("SYNC_P", "SYNC_N"), ("USB_D_P", "USB_D_N"), ("USBC_D_P", "USBC_D_N")]
PAIR_OF = {n: p for p in PAIRS for n in p}


def off45(a, c):
    ang = math.degrees(math.atan2(c[1] - a[1], c[0] - a[0])) % 45.0
    return min(ang, 45.0 - ang) > 1.0


def net_copper(m, net):
    return [it for it in m.items if it["net"] == net and it["kind"] in ("track", "via")]


def jag_of(items):
    L = sum(math.dist(it["a"], it["c"]) for it in items if it["kind"] == "track")
    off = sum(math.dist(it["a"], it["c"]) for it in items if it["kind"] == "track" and off45(it["a"], it["c"]))
    tiny = sum(1 for it in items if it["kind"] == "track" and math.dist(it["a"], it["c"]) < 0.1)
    return L, off, tiny


def snapshot(items):
    return [(it["kind"], next(iter(it["lay"])) if it["kind"] == "track" else None,
             it.get("a"), it.get("c"), it.get("w"), it.get("xy"), it.get("size"), it.get("drill")) for it in items]


def restore(m, net, snap):
    for kind, l, a, c, w, xy, size, drill in snap:
        if kind == "track":
            m.add(net, [(l, a, c)], [], w)
        else:
            m.add(net, [], [xy], 0.15, (size, drill))
    m.index()


def widths(items):
    ws = collections.Counter()
    for it in items:
        if it["kind"] == "track":
            ws[round(it["w"], 3)] += math.dist(it["a"], it["c"])
    if not ws:
        return [0.15]
    big, small = max(ws), min(ws)
    return [big] if big == small else [big, small]


def layers_for(net, items):
    used = {next(iter(it["lay"])) for it in items if it["kind"] == "track"}
    if net in F_ONLY:
        return [("F",)]
    first = ("F", "B")
    out = [first]
    if "In2" in used:
        out.append(("F", "In2", "B"))
    return out


def pair_pen(m, other, win, pitch=0.30, tol=0.06, cost=25):
    """extra_pen that makes a net run at `pitch` beside its partner's copper, on
    the partner's layer: a band on F.Cu beside a B.Cu run is no pair at all (the
    two planes are between them), so every layer is costed except the band on
    the layer the partner is on."""
    trk = [it for it in net_copper(m, other) if it["kind"] == "track"]
    if not trk:
        return None
    out = {}
    for l in ("F", "In2", "B"):
        lines = [shapely.LineString([it["a"], it["c"]]) for it in trk if l in it["lay"]]
        if lines:
            u = unary_union(lines)
            band = u.buffer(pitch + tol).difference(u.buffer(max(pitch - tol, 0.01)))
            out[l] = [(box(*win).difference(band), cost)]
        else:
            out[l] = [(box(*win), cost)]
    return out


def redo_net(m, net, log, pair_partner=None):
    items = net_copper(m, net)
    pads = [it for it in m.items if it["net"] == net and it["kind"] == "pad"]
    if len(pads) < 2:
        return "skip"
    L0, off0, tiny0 = jag_of(items)
    v0 = sum(1 for it in items if it["kind"] == "via")
    if L0 > 0 and off0 < 0.02 * L0 and tiny0 == 0:
        return "straight"
    snap = snapshot(items)
    for ws in widths(items):
        for lays in layers_for(net, items):
            m.remove([it["uuid"] for it in net_copper(m, net)])
            m.index()
            extra = None
            if pair_partner:
                allc = unary_union([rr2.geo(it) for it in pads])
                bx = allc.bounds
                extra = pair_pen(m, pair_partner, (bx[0] - 6, bx[1] - 6, bx[2] + 6, bx[3] + 6))
            t = time.time()
            ok, _ = rr3.connect(m, net, ws, lays, margin=1.5, extra_pen=extra)
            new = net_copper(m, net)
            L1, off1, tiny1 = jag_of(new)
            v1 = sum(1 for it in new if it["kind"] == "via")
            good = ok and L1 <= 1.35 * L0 + 1.0 and v1 <= v0 + 1
            if good:
                log("  %-14s %5.1f -> %5.1f mm  vias %d -> %d  off-45 %4.1f -> %4.1f mm  w %.2f %s  (%.0fs)" % (
                    net, L0, L1, v0, v1, off0, off1, ws, "/".join(lays), time.time() - t))
                return "done"
            m.remove([it["uuid"] for it in new])
            m.index()
            restore(m, net, snap)
    log("  %-14s kept as it was (%5.1f mm, off-45 %4.1f mm)" % (net, L0, off0))
    return "kept"


def redo_pair(m, pn, nn, log):
    """Both lines of a differential pair, or neither: P first, then N held at
    the pair's pitch beside it. Kept only if both route, neither grows more
    than the single-net limit and the skew does not get worse by over 0.5 mm."""
    ip, inn = net_copper(m, pn), net_copper(m, nn)
    Lp0, offp0, tp0 = jag_of(ip)
    Ln0, offn0, tn0 = jag_of(inn)
    if Lp0 + Ln0 > 0 and offp0 + offn0 < 0.02 * (Lp0 + Ln0) and tp0 + tn0 == 0:
        return "straight"
    vp0 = sum(1 for it in ip if it["kind"] == "via")
    vn0 = sum(1 for it in inn if it["kind"] == "via")
    snap_p, snap_n = snapshot(ip), snapshot(inn)
    ws = widths(ip + inn)[0]
    used_in2 = any("In2" in it["lay"] for it in ip + inn if it["kind"] == "track")
    for lays in [("F", "B")] + ([("F", "In2", "B")] if used_in2 else []):
        m.remove([it["uuid"] for it in net_copper(m, pn) + net_copper(m, nn)])
        m.index()
        t = time.time()
        okp, _ = rr3.connect(m, pn, ws, lays, margin=1.5)
        okn = False
        if okp:
            pads = [it for it in m.items if it["net"] in (pn, nn) and it["kind"] == "pad"]
            bx = unary_union([rr2.geo(it) for it in pads]).bounds
            extra = pair_pen(m, pn, (bx[0] - 6, bx[1] - 6, bx[2] + 6, bx[3] + 6))
            okn, _ = rr3.connect(m, nn, ws, lays, margin=1.5, extra_pen=extra)
        newp, newn = net_copper(m, pn), net_copper(m, nn)
        Lp1, offp1, _ = jag_of(newp)
        Ln1, offn1, _ = jag_of(newn)
        vp1 = sum(1 for it in newp if it["kind"] == "via")
        vn1 = sum(1 for it in newn if it["kind"] == "via")
        good = (okp and okn and Lp1 <= 1.35 * Lp0 + 1.0 and Ln1 <= 1.35 * Ln0 + 1.0
                and vp1 <= vp0 + 1 and vn1 <= vn0 + 1 and abs(Lp1 - Ln1) <= abs(Lp0 - Ln0) + 0.5)
        if good:
            log("  %-14s %5.1f/%5.1f -> %5.1f/%5.1f mm  skew %.2f -> %.2f  off-45 %4.1f -> %4.1f mm  %s  (%.0fs)" % (
                pn[:-2] + " pair", Lp0, Ln0, Lp1, Ln1, abs(Lp0 - Ln0), abs(Lp1 - Ln1), offp0 + offn0, offp1 + offn1,
                "/".join(lays), time.time() - t))
            return "done"
        m.remove([it["uuid"] for it in newp + newn])
        m.index()
        restore(m, pn, snap_p)
        restore(m, nn, snap_n)
    log("  %-14s kept as it was (%5.1f/%5.1f mm, off-45 %4.1f mm)" % (pn[:-2] + " pair", Lp0, Ln0, offp0 + offn0))
    return "kept"


def stub_groups(m, net):
    """The net's tracks grouped by connectivity, each with the pads and vias it touches."""
    trk = [it for it in m.items if it["net"] == net and it["kind"] == "track"]
    ends = [it for it in m.items if it["net"] == net and it["kind"] in ("pad", "via")]
    parent = list(range(len(trk)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    geoms = [it["geom"] for it in trk]
    tree = shapely.STRtree(geoms)
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            j = int(j)
            if j > i and trk[i]["lay"] == trk[j]["lay"] and rr2.joined(trk[i], g, trk[j], geoms[j]):
                parent[find(i)] = find(j)
    groups = collections.defaultdict(list)
    for i, it in enumerate(trk):
        groups[find(i)].append(it)
    out = []
    for g in groups.values():
        gu = unary_union([it["geom"] for it in g])
        touch = [e for e in ends if (e["lay"] & {next(iter(t["lay"])) for t in g}) and
                 any(rr2.joined(t, t["geom"], e, rr2.geo(e)) for t in g)]
        out.append((g, touch))
    return out


def redo_plane_stubs(m, net, log):
    done = kept = 0
    for g, touch in stub_groups(m, net):
        L0, off0, tiny0 = jag_of(g)
        if len(touch) < 2 or (off0 < 0.02 * max(L0, 1e-9) and tiny0 == 0):
            continue
        snap = snapshot(g)
        ws = widths(g)[0]
        lays = tuple(sorted({next(iter(t["lay"])) for t in g}, key=["F", "In2", "B"].index))
        m.remove([it["uuid"] for it in g])
        m.index()
        added = []
        ok = True
        anchor = [touch[0]]
        for e in touch[1:]:
            pa = unary_union([rr2.geo(x) for x in anchor + [e]]).bounds
            win = (pa[0] - 1.2, pa[1] - 1.2, pa[2] + 1.2, pa[3] + 1.2)
            r = rr3.route3(m, net, rr3.terminals([e], ws), rr3.terminals(anchor, ws), win, ws, lays,
                           align=e["xy"] if e["kind"] == "pad" else None)
            if not r or r[2]:
                ok = False
                break
            before = {t.m_Uuid.AsString() for t in m.b.GetTracks()}
            m.add(net, r[0], r[1], ws)
            m.index()
            added += list({t.m_Uuid.AsString() for t in m.b.GetTracks()} - before)
            anchor.append(e)
        new = [it for it in m.items if it["uuid"] in set(added)]
        L1 = sum(math.dist(it["a"], it["c"]) for it in new if it["kind"] == "track")
        if ok and L1 <= 1.5 * L0 + 0.5 and not any(it["kind"] == "via" for it in new):
            done += 1
        else:
            m.remove(added)
            m.index()
            restore(m, net, snap)
            kept += 1
    log("  %-14s plane stubs: %d redrawn, %d kept" % (net, done, kept))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--nets", default="")
    ap.add_argument("--no-planes", action="store_true")
    ap.add_argument("--planes-only", action="store_true")
    a = ap.parse_args()
    lr.PRO_REF = os.path.join(lr.ROOT, "tmp", "rev4_ref.kicad_pro")
    m = lr.Model(a.src)
    log = lambda s: print(s, flush=True)
    nets = sorted({it["net"] for it in m.items if it["kind"] in ("track", "via") and it["net"]} - set(PLANE))
    if a.nets:
        nets = [n for n in a.nets.split(",") if n]
    # most jagged first: they free the most room for the rest
    nets.sort(key=lambda n: -jag_of(net_copper(m, n))[1])
    status = {}
    for p in range(0 if a.planes_only else a.passes):
        log("pass %d: %d net(s)" % (p + 1, len([n for n in nets if status.get(n) not in ("done", "straight", "skip")])))
        for n in nets:
            if status.get(n) in ("done", "straight", "skip"):
                continue
            if n in PAIR_OF:
                pp, nn = PAIR_OF[n]
                if pp in nets and nn in nets:
                    status[pp] = status[nn] = redo_pair(m, pp, nn, log)
                    continue
            status[n] = redo_net(m, n, log)
        m.save(a.dst)
    if not a.no_planes and not a.nets:
        for n in PLANE:
            redo_plane_stubs(m, n, log)
    c = collections.Counter(status.values())
    log("nets: %s" % dict(c))
    d, cc = rr2.prune(m, a.dst, "straighten")
    lr.summary(d, cc)
    return 0 if not d["unconnected_items"] else 1


if __name__ == "__main__":
    sys.exit(main())
