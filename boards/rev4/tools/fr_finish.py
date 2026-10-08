"""fr_finish.py in.kicad_pcb out.kicad_pcb [--rounds N] [--fixed NET,...] [--tidy-only] - finish a freerouting result.

--tidy-only: the board is finished already; only the tidy, detour and
staircase passes, the prune and the widening below.

ses_import.py brings freerouting's session back onto the board; what it leaves
is finished here, with the same exact-geometry model and the octilinear router
(rr3) used everywhere else, so the repairs are as straight as the rest:

  1. KiCad's DRC (zones refilled, the board's real rules). Every signal track
     or via in a copper violation - clearance, hole clearance, edge clearance,
     width, a short, a solder-mask bridge - is taken off. Freerouting knows
     nothing of the rule areas, the fiducials' 0.6 mm clearance or the 0.25 mm
     copper-to-hole rule beyond what dsn_prep.py could express, so this is
     where those are enforced.
  2. Every net in more than one piece - left open by freerouting, or opened
     by step 1 - is joined by rr3.rrr3: octilinear connections nearest-first;
     when blocked, through other nets' copper at a cost, which is ripped and
     queued again. Any signal freerouting routed may be ripped; the planes'
     ties, the --fixed nets and SENSITIVE (below) may not. Widths are the
     classes' (dsn_prep.CLASSES); SENSE / GAIN / ADC / RAIL_MON stay on F.Cu;
     In2 is a last resort, tried only in the last round.
  3. rr2.prune: what KiCad calls dangling goes, the DRC runs again.

Steps 1-3 repeat until the DRC has no copper violation and nothing open.

Nets named --fixed (preroute_rev4.FIXED) are never taken off or ripped; nor are
the planes' ties, nor the layout-critical power and clock nets in SENSITIVE.

Then the crystal: XOUT_MCU (pin 22 to the series resistor R36) and XIN
(pin 21 to C20 and Y1) leave side by side and belong on F.Cu without a via.
Freerouting ran XIN between R36's pads and sent XOUT_MCU over it through two
vias; if either has a via, both are ripped and drawn again on F.Cu, XOUT_MCU
first, and if that leaves either open every net it touched goes back as it
was. (preroute_rev4.py now lays the crystal itself, so this finds no via.)

And the op-amp outputs: AMP_A and AMP_B (U7 to the ADC cells' 51-ohm
resistors) may leave F.Cu - they have to cross SENSE_B - but freerouting put
32 mm of them on B.Cu, over the +3.3 V plane instead of ground. Both are
redrawn with B.Cu costed everywhere, so they use it only to cross.

Then a tidy pass, twice over: every signal net that has a via, the pre-routed
ones, the op-amp outputs, +5V_USB (with its pre-laid VBUS ties) and +5V (with
the buck's input loop) aside, is
taken off alone and routed again by rr3 with everything else in place. The new
routing stays if it saves a via and is at most a quarter longer, or, with as
many vias, is 1 mm shorter; otherwise the net goes back exactly as it was. The
rip-up loop leaves detours behind - nets routed round copper that was later
ripped - and this takes them out. A short net (pads' spanning tree under 15 mm)
still more than twice that long plus 3 mm is ripped with one or two of the
nets whose copper lies between its pads, and all are routed again, it first;
kept if the lot is 2 mm shorter with no more vias.

Then the staircases: where rr3's 0.025 mm grid stepped round a curve (a
keep-out's rounded corner), it left short horizontal and vertical segments in
turn. Each such run, on any net (the pre-routed ones too), is drawn as the
diagonal between its ends (and the straight rest, when they are not at 45
degrees) where the exact check allows and no corner turns acute; the segments
either side are merged with it where they line up.

Last, the widths: freerouting routed every net that touches the RP2354A or the
TUSB320 at 0.10 mm, end to end (one width per net class), where only the
0.4 mm-pitch pins need it. Each such track is widened back to 0.15 mm (VCORE,
the core supply, to 0.25 / 0.20 / 0.15 when it was not pre-routed at 0.20),
and the 5 V rails' 0.30 mm to 0.60 / 0.50 / 0.40 (+5V_BUS, the harness
pass-through) or 0.50 / 0.40 (+5V, +5V_USB), wherever the exact check allows
it; a widening the DRC then objects to is undone.

Unless the corner nets are --fixed (pre-routed), one corner is drawn first
rather than searched: the RP2354A's ADC pins.
ADC_B, RAIL_MON and ADC_A leave pins 43, 42 and 41 side by side on F.Cu, and
RAIL_MON - in the middle - has to pass between C32 and C31, 0.38 mm apart
(inside U9_escape, so 0.10 mm clearance each side of a 0.10 mm track), and
then under R22. Freerouting left it open in every run, and a search finds the
way only when the other two have stepped aside first, so all three are laid
down here, octilinear, and whatever they cross is re-routed around them.
"""
import argparse
import collections
import itertools
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shapely
import lr
import rr2
import rr3
import dsn_prep
import straighten_rev4 as st

PLANE = ("GND", "+3.3V")
SENSITIVE = {"VREG_LX", "VREG_AVDD", "SW_NODE", "XIN", "XOUT", "XOUT_MCU", "USB_BUS_SW",
             "+5V_USB"}     # +5V_USB carries D5.5's and J5's pre-laid VBUS ties (preroute_rev4.vbus_d5 / vbus_j5)
WIDEN = {"VCORE": (0.25, 0.2, 0.15),
         "+5V_BUS": (0.6, 0.5, 0.4),      # the harness pass-through: every board carries the boards after it
         "+5V": (0.5, 0.4), "+5V_USB": (0.5, 0.4)}
COPPER = {"clearance", "hole_clearance", "copper_edge_clearance", "track_width", "shorting_items",
          "tracks_crossing", "solder_mask_bridge", "hole_to_hole", "via_diameter", "annular_width",
          "drill_out_of_range", "connection_width", "items_not_allowed"}
WIDTH = {}
F_ONLY = set()
for _name, _w, _c, _via, _layers, _nets in dsn_prep.CLASSES:
    for _n in _nets:
        WIDTH[_n] = _w / 1000.0
        if _layers == ["F.Cu"]:
            F_ONLY.add(_n)


# pad centres the drawing was made for, and the drawing (mm, F.Cu, 0.10 mm)
CORNER_PADS = {"U9.43": (129.956, 118.651), "U9.42": (129.956, 119.051), "U9.41": (129.956, 119.451),
               "C32.1": (134.500, 118.980), "C31.1": (134.500, 119.920), "R22.2": (135.490, 119.280),
               "R21.2": (135.490, 120.920), "C45.1": (137.420, 119.800)}
CORNER = {
    # pin 43 out, step down 0.13 to clear C36, into C32; C32 on to R22
    "ADC_B": [[(129.956, 118.651), (130.600, 118.651), (130.729, 118.780), (134.400, 118.780)],
              [(134.500, 118.980), (135.190, 118.980), (135.490, 119.280)]],
    # pin 42 out, down to y 119.45 - the middle of the C32/C31 gap - and through
    # it, down beside C31 and east under R22 into C45
    "RAIL_MON": [[(129.956, 119.051), (131.000, 119.051), (131.399, 119.450), (134.970, 119.450),
                  (134.970, 119.820), (137.420, 119.820)]],
    # pin 41 out, down 0.22 at once (under RAIL_MON, over VCORE's via), into C31; C31 on to R21
    "ADC_A": [[(129.956, 119.451), (130.600, 119.451), (130.819, 119.670), (133.900, 119.670),
               (134.150, 119.920), (134.500, 119.920)],
              [(134.500, 119.920), (135.490, 120.910)]],
}
CORNER_RIP = ("ADC_A", "ADC_B", "RAIL_MON", "ADC_AVDD")   # ADC_AVDD's vias sat in ADC_B's way


def draw_corner(m, log):
    """Lay the ADC corner down; take off whatever of other signal nets it crosses."""
    for ref, xy in CORNER_PADS.items():
        r, n = ref.split(".")
        p = m.pad(r, n)["xy"]
        if math.dist(p, xy) > 0.002:
            log("ADC corner: %s is at (%.3f, %.3f), not where the drawing expects; not drawn" % (ref, p[0], p[1]))
            return False
    m.remove([it["uuid"] for it in m.items if it["net"] in CORNER_RIP and it["kind"] in ("track", "via")])
    m.index()
    segs = {net: [("F", a, c) for path in paths for a, c in zip(path, path[1:])] for net, paths in CORNER.items()}
    rip = set()
    for net, ss in segs.items():
        for b in m.verify(net, ss, [], 0.1):
            it = m.uuid.get(b[-1])
            if it is None or it["kind"] == "pad" or it["net"] in PLANE:     # the edge, a pad, a plane tie
                log("ADC corner: %s would hit %s; not drawn" % (net, b))
                return False
            rip.add(b[-1])
    nets_hit = collections.Counter(m.uuid[u]["net"] for u in rip)
    m.remove(rip)
    m.index()
    for net, ss in segs.items():
        m.add(net, ss, [], 0.1)
    m.index()
    for net, ss in segs.items():                 # and the three clear of each other
        bad = m.verify(net, ss, [], 0.1)
        if bad:
            raise SystemExit("ADC corner: the drawing itself fails: %s %s" % (net, bad[:3]))
    log("ADC corner drawn: ADC_B / RAIL_MON / ADC_A %.1f / %.1f / %.1f mm; took off %d item(s) of %s"
        % tuple([sum(math.dist(a, c) for _l, a, c in segs[n]) for n in ("ADC_B", "RAIL_MON", "ADC_A")] +
                [len(rip), dict(nets_hit)]))
    return True


CRYSTAL = ("XOUT_MCU", "XIN")


def crystal(m, log, keep):
    """XOUT_MCU and XIN on F.Cu without vias, XOUT_MCU first; if that leaves anything
    open, every net it touched goes back as it was."""
    if not any(it["kind"] == "via" for it in m.items if it["net"] in CRYSTAL):
        return True
    snaps = collections.defaultdict(list)
    for it in m.items:
        if it["kind"] in ("track", "via"):
            snaps[it["net"]] += st.snapshot([it])
    m.remove([it["uuid"] for it in m.items if it["net"] in CRYSTAL and it["kind"] in ("track", "via")])
    m.index()
    res = rr3.rrr3(m, list(CRYSTAL), protect=(keep | SENSITIVE) - set(CRYSTAL),
                   layers_fn=lambda n: ("F",) if n in CRYSTAL else layers_of(n),
                   width_fn=lambda n: 0.15 if n in CRYSTAL else width_of(m, n), log=log, max_iter=60)
    if res["left"]:
        back = set(CRYSTAL) | set(res["touched"])
        m.remove([it["uuid"] for it in m.items if it["net"] in back and it["kind"] in ("track", "via")])
        m.index()
        for n in sorted(back):
            st.restore(m, n, snaps[n])
    log("crystal: %s" % ("XOUT_MCU and XIN on F.Cu, no via" if not res["left"] else
                         "no F.Cu way (%s open); %s put back as they were" % (sorted(res["left"]), ", ".join(sorted(back)))))
    return not res["left"]


AMP = ("AMP_A", "AMP_B")
ANALOG_F = {"SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B"}


def amp(m, log, keep):
    """AMP_A / AMP_B on F.Cu over the ground plane, B.Cu only where they must cross."""
    from shapely.geometry import box
    b0 = sum(math.dist(it["a"], it["c"]) for it in m.items if it["net"] in AMP and it["kind"] == "track" and "B" in it["lay"])
    snap = [(it["net"], next(iter(it["lay"])), it["a"], it["c"], it["w"]) for it in m.items if it["net"] in AMP and it["kind"] == "track"]
    vsnap = [(it["net"], it["xy"], it["size"], it["drill"]) for it in m.items if it["net"] in AMP and it["kind"] == "via"]
    m.remove([it["uuid"] for it in m.items if it["net"] in AMP and it["kind"] in ("track", "via")])
    m.index()
    res = rr3.rrr3(m, list(AMP), protect=keep | SENSITIVE | ANALOG_F, layers_fn=lambda n: ("F", "B") if n in AMP else layers_of(n),
                   width_fn=lambda n: width_of(m, n), log=log, max_iter=80,
                   extra_pen={"B": [(box(m.edge[0], m.edge[1], m.edge[2], m.edge[3]), 40)]})
    b1 = sum(math.dist(it["a"], it["c"]) for it in m.items if it["net"] in AMP and it["kind"] == "track" and "B" in it["lay"])
    if res["left"] or res["ripped"]:
        # only take the redraw if it is clean and touched nothing else; otherwise put them back
        m.remove([it["uuid"] for it in m.items if it["net"] in AMP and it["kind"] in ("track", "via")])
        m.index()
        for n, l, a, c, w in snap:
            m.add(n, [(l, a, c)], [], w)
        for n, xy, size, drill in vsnap:
            m.add(n, [], [xy], 0.15, (size, drill))
        m.index()
        log("op-amp outputs: kept as freerouting left them (%.1f mm on B.Cu)" % b0)
        return False
    log("op-amp outputs: B.Cu %.1f -> %.1f mm" % (b0, b1))
    return True


def width_of(m, net):
    return WIDTH.get(net) or rr2.default_width(m, net)


def layers_of(net):
    return ("F",) if net in F_ONLY else ("F", "B")


def fallback_of(net):
    return ("F",) if net in F_ONLY else ("F", "In2", "B")


def offenders(m, d, keep):
    """uuids of the signal tracks and vias in copper violations (never those of the nets in keep)."""
    out = set()
    for v in d["violations"]:
        if v["type"] not in COPPER:
            continue
        for i in v["items"]:
            it = m.uuid.get(i["uuid"])
            if it and it["kind"] in ("track", "via") and it["net"] not in keep:
                out.add(i["uuid"])
    return out


def tidy(m, log, skip, passes=2):
    """Each signal net with a via, ripped alone and routed again; kept only if better."""
    for p in range(passes):
        saved = redrawn = 0
        for net in sorted({it["net"] for it in m.items if it["kind"] == "via"} - set(skip)):
            items = st.net_copper(m, net)
            v0, L0 = sum(1 for it in items if it["kind"] == "via"), st.jag_of(items)[0]
            snap = st.snapshot(items)
            m.remove([it["uuid"] for it in items])
            m.index()
            ok, _ = rr3.connect(m, net, width_of(m, net), layers_of(net), margin=1.5)
            new = st.net_copper(m, net)
            v1, L1 = sum(1 for it in new if it["kind"] == "via"), st.jag_of(new)[0]
            if ok and ((v1 < v0 and L1 <= 1.25 * L0 + 1.0) or (v1 == v0 and L1 < L0 - 1.0)):
                log("  tidy %-14s %5.1f -> %5.1f mm, vias %d -> %d" % (net, L0, L1, v0, v1))
                saved += v0 - v1
                redrawn += 1
                continue
            m.remove([it["uuid"] for it in new])
            m.index()
            st.restore(m, net, snap)
        log("tidy, pass %d: %d net(s) redrawn, %d via(s) fewer" % (p + 1, redrawn, saved))
        if not redrawn:
            break


def mst(pts):
    """Length of the minimum spanning tree over the points."""
    if len(pts) < 2:
        return 0.0
    left, tot = set(range(1, len(pts))), 0.0
    d = {i: math.dist(pts[0], pts[i]) for i in left}
    while left:
        j = min(left, key=d.get)
        tot += d[j]
        left.remove(j)
        for i in left:
            d[i] = min(d[i], math.dist(pts[j], pts[i]))
    return tot


def detours(m, log, skip, short=15.0):
    """A short net routed far round, ripped with one or two of the nets between its pads; kept only if better."""
    def stat(nets):
        its = [it for n in nets for it in st.net_copper(m, n)]
        return sum(1 for it in its if it["kind"] == "via"), st.jag_of(its)[0]
    for net in sorted({it["net"] for it in m.items if it["kind"] == "track"} - set(skip)):
        pads = [it for it in m.items if it["net"] == net and it["kind"] == "pad"]
        t = mst([p["xy"] for p in pads])
        if t > short or st.jag_of(st.net_copper(m, net))[0] <= 2.0 * t + 3.0:
            continue
        bx = shapely.box(*shapely.union_all([rr2.geo(p) for p in pads]).bounds).buffer(0.5)
        inside = collections.Counter()
        for it in m.items:
            if it["kind"] in ("track", "via") and it["net"] not in skip and it["net"] != net:
                g = rr2.geo(it)
                if g.intersects(bx):
                    inside[it["net"]] += g.intersection(bx).area
        top = [n for n, _ in inside.most_common(4)]
        for group in [[net] + list(c) for k in (1, 2) for c in itertools.combinations(top, k)]:
            (v0, L0), snaps = stat(group), {n: st.snapshot(st.net_copper(m, n)) for n in group}
            m.remove([it["uuid"] for n in group for it in st.net_copper(m, n)])
            m.index()
            ok = all([rr3.connect(m, n, width_of(m, n), layers_of(n), margin=3.0)[0] for n in group])
            v1, L1 = stat(group)
            if ok and v1 <= v0 and L1 < L0 - 2.0:
                log("  detour %-12s with %s: %.1f mm %d via -> %.1f mm %d via" % (net, ", ".join(group[1:]), L0, v0, L1, v1))
                break
            m.remove([it["uuid"] for n in group for it in st.net_copper(m, n)])
            m.index()
            for n in group:
                st.restore(m, n, snaps[n])


def destair(m, log, step=0.13):
    """Staircases - rr3's 0.025 mm grid stepping round a curve - drawn as their diagonal."""
    key = lambda p: (round(p[0], 4), round(p[1], 4))

    def joints():
        ends = collections.defaultdict(list)
        for it in m.items:
            if it["kind"] == "track":
                for p in (it["a"], it["c"]):
                    ends[(it["net"], next(iter(it["lay"])), key(p))].append(it)
        return ends

    def bare(net, l, p):                  # no via or pad of the net there
        its, tree = m.by_layer[l]
        pt = shapely.Point(p)
        return not any(its[k]["kind"] in ("via", "pad") and its[k]["net"] == net and its[k]["geom"].intersects(pt)
                       for k in tree.query(pt))

    def far(it, p):
        return it["c"] if key(it["a"]) == p else it["a"]

    def axis(it):
        dx, dy = abs(it["c"][0] - it["a"][0]), abs(it["c"][1] - it["a"][1])
        return "H" if dy < 1e-6 < dx else "V" if dx < 1e-6 < dy else None

    def away(p, q):
        d = math.dist(p, q)
        return ((q[0] - p[0]) / d, (q[1] - p[1]) / d) if d > 1e-9 else (0.0, 0.0)

    ends = joints()

    def stair(net, l, p):
        e = ends[(net, l, p)]
        return (len(e) == 2 and {axis(e[0]), axis(e[1])} == {"H", "V"} and
                max(math.dist(it["a"], it["c"]) for it in e) <= step and bare(net, l, p))

    seen, runs, segs = set(), 0, 0
    for net, l, p in list(ends):
        if (net, l, p) in seen or not stair(net, l, p):
            continue
        seen.add((net, l, p))
        chain, tips = list(ends[(net, l, p)]), []
        for cur in list(chain):
            q = key(far(cur, p))
            while (net, l, q) not in seen and stair(net, l, q):
                seen.add((net, l, q))
                cur = next(s for s in ends[(net, l, q)] if s is not cur)
                chain.append(cur)
                q = key(far(cur, q))
            tips.append(q)
        e0, e1 = tips
        dx, dy = e1[0] - e0[0], e1[1] - e0[1]
        if (sum(abs(it["c"][0] - it["a"][0]) for it in chain) > abs(dx) + 1e-4 or
                sum(abs(it["c"][1] - it["a"][1]) for it in chain) > abs(dy) + 1e-4):
            continue                        # not monotone: a jog, not a staircase
        d = min(abs(dx), abs(dy))
        mx, my = math.copysign(d, dx), math.copysign(d, dy)
        if d < 0.01 or abs(abs(dx) - abs(dy)) < 1e-4:
            options = [[e0, e1]]
        else:
            options = [[e0, (e0[0] + mx, e0[1] + my), e1], [e0, (e1[0] - mx, e1[1] - my), e1]]
        w = min(it["w"] for it in chain)
        ids = {it["uuid"] for it in chain}
        for pl in options:
            # no acute corner with what meets the ends
            acute = False
            for tip, nxt in ((pl[0], pl[1]), (pl[-1], pl[-2])):
                u = away(tip, nxt)
                for it in ends[(net, l, key(tip))]:
                    if it["uuid"] not in ids:
                        v = away(tip, far(it, key(tip)))
                        acute |= u[0] * v[0] + u[1] * v[1] > 1e-6
            if not acute and not any(m.check_track(net, l, a, c, w) for a, c in zip(pl, pl[1:])):
                m.remove(list(ids))
                m.add(net, [(l, a, c) for a, c in zip(pl, pl[1:])], [], w)
                runs += 1
                segs += len(chain)
                break
    m.index()
    # the segments either side of a new diagonal, in line with it: one segment
    merged, again = 0, True
    while again:
        again = False
        ends = joints()
        for (net, l, p), e in ends.items():
            if len(e) != 2 or abs(e[0]["w"] - e[1]["w"]) > 1e-6 or not bare(net, l, p):
                continue
            q0, q1 = far(e[0], p), far(e[1], p)
            u, v = away(p, q0), away(p, q1)
            if abs(u[0] * v[1] - u[1] * v[0]) < 1e-4 and u[0] * v[0] + u[1] * v[1] < 0:
                m.remove([e[0]["uuid"], e[1]["uuid"]])
                m.add(net, [(l, q0, q1)], [], e[0]["w"])
                m.index()
                merged += 1
                again = True
                break
    log("staircases: %d drawn as diagonals (%d segments), %d joint(s) merged" % (runs, segs, merged))


def widen(m, log, skip):
    """Widen 0.10 mm tracks where the exact check allows; returns {uuid: old width}."""
    targets = {}
    for net in {it["net"] for it in m.items if it["kind"] == "track"} - set(skip):
        if net in WIDEN:
            targets[net] = WIDEN[net]
        elif width_of(m, net) <= 0.1:
            targets[net] = (0.15,)
    done = []                      # (geom, net, in_area) of tracks widened so far
    old = {}
    tracks = {t.m_Uuid.AsString(): t for t in m.b.GetTracks()}
    for it in [it for it in m.items if it["kind"] == "track" and it["net"] in targets]:
        l = next(iter(it["lay"]))
        for w in targets[it["net"]]:
            if w <= it["w"] + 1e-6:
                break
            g = lr.seg_geom(it["a"], it["c"], w)
            if m.check_track(it["net"], l, it["a"], it["c"], w):
                continue
            ina = g.intersects(m.area)
            if any(dn != it["net"] and dl == l and g.distance(dg) < (lr.FINE if (ina and din) else lr.RULE) - 1e-4
                   for dg, dn, din, dl in done if dg.bounds[0] < g.bounds[2] + 0.5 and dg.bounds[2] > g.bounds[0] - 0.5):
                continue
            tracks[it["uuid"]].SetWidth(lr.K.FromMM(w))
            old[it["uuid"]] = it["w"]
            done.append((g, it["net"], ina, l))
            break
    m.index()
    L = sum(math.dist(m.uuid[u]["a"], m.uuid[u]["c"]) for u in old if u in m.uuid)
    log("widened %d track(s), %.0f mm, of %d net(s)" % (len(old), L, len(targets)))
    return old


def open_nets(m):
    nets = sorted({it["net"] for it in m.items if it["kind"] == "pad" and it["net"]} - set(PLANE))
    return [n for n in nets if len(rr2.comps(m, n)) > 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--fixed", default="")
    ap.add_argument("--tidy-only", action="store_true")
    a = ap.parse_args()
    fixed = {n for n in a.fixed.split(",") if n}
    keep = set(PLANE) | fixed
    lr.PRO_REF = os.path.join(lr.ROOT, "tmp", "rev4_ref.kicad_pro")
    log = lambda s: print(s, flush=True)
    m = lr.Model(a.src)
    if not set(CORNER) <= fixed and not a.tidy_only:
        draw_corner(m, log)
    m.save(a.dst)
    d, c = lr.drc(a.dst, "frfin")
    for r in range(0 if a.tidy_only else a.rounds):
        bad = offenders(m, d, keep)
        nets_bad = collections.Counter(m.uuid[u]["net"] for u in bad)
        log("round %d: DRC unconnected %d, copper %d; taking off %d item(s) of %s"
            % (r + 1, len(d["unconnected_items"]), sum(n for k, n in c.items() if k in COPPER), len(bad),
               dict(nets_bad.most_common(30))))
        if bad:
            m.remove(bad)
            m.index()
        must = [n for n in open_nets(m) if n not in fixed]
        log("  open: %d net(s) %s" % (len(must), must[:40]))
        if not must and not bad:
            break
        res = rr3.rrr3(m, must, protect=keep | SENSITIVE, layers_fn=layers_of, width_fn=lambda n: width_of(m, n), log=log,
                       fallback_layers=fallback_of if r == a.rounds - 1 else None,   # In2 only in the last round
                       max_iter=40 + 12 * len(must))
        # what failed at the class width gets one more try at 0.10 / 0.15 mm
        for n in sorted(res["left"]):
            w = width_of(m, n)
            narrow = 0.15 if w > 0.15 else 0.1
            if narrow < w:
                ok, _ = rr3.connect(m, n, narrow, layers_of(n))
                log("  %-14s at %.2f mm: %s" % (n, narrow, "joined" if ok else "still open"))
        d, c = rr2.prune(m, a.dst, "frfin", log=log)
        if not d["unconnected_items"] and not any(k in COPPER for k in c):
            break
    if not d["unconnected_items"] and not any(k in COPPER for k in c):
        if not a.tidy_only:
            if not crystal(m, log, keep):
                log("crystal could not be redrawn")
            amp(m, log, keep)
        # +5V carries the buck's input loop at U12's pins (preroute_rev4.buck_in): the
        # rip-up may move the rest of the net, but the tidy pass, which takes a whole
        # net off and routes it again, leaves it alone
        tidy(m, log, keep | set(AMP) | {"+5V_USB", "+5V"})
        detours(m, log, keep | set(AMP) | SENSITIVE)
        destair(m, log)
    d, c = rr2.prune(m, a.dst, "frfin", log=log)
    if not d["unconnected_items"] and not any(k in COPPER for k in c):
        old = widen(m, log, fixed | set(PLANE))
        m.save(a.dst)
        d, c = lr.drc(a.dst, "frfin")
        back = [u for v in d["violations"] if v["type"] in COPPER for u in (i["uuid"] for i in v["items"]) if u in old]
        if back:
            tracks = {t.m_Uuid.AsString(): t for t in m.b.GetTracks()}
            for u in set(back):
                tracks[u].SetWidth(lr.K.FromMM(old[u]))
            m.index()
            m.save(a.dst)
            d, c = lr.drc(a.dst, "frfin")
            log("  %d widening(s) undone after the DRC" % len(set(back)))
    lr.summary(d, c)
    m.save(a.dst)
    return 0 if not d["unconnected_items"] and not any(k in COPPER for k in c) else 1


if __name__ == "__main__":
    sys.exit(main())
