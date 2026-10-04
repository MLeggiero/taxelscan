"""pairs_rev4.py in.kicad_pcb out.kicad_pcb - the RS-485 pairs, coupled and octilinear.

BUS_P/N and SYNC_P/N (20 Mbaud) run from J4 at the left edge, through their
transceivers (U10, U11) and terminations (R11, R12), across the board to J3 at
the right edge. J3 and J4 are mirror images, so the four conductors arrive at
J3 in exactly the reverse of their order at J4: each pair swaps P and N once
and the two pairs cross once. rev-3 laid them by hand for that reason
(../../rev3/audit-tools/pairgeo.py), and its later compaction bent them off
the 45-degree grid. A search was tried here - each conductor costed into a
lane of its own, N made to follow P - and kept making a tangle of the crowded
J4 end, so the pairs are drawn again, for this placement:

  the lanes   four straight F.Cu lanes from the transceivers to the USB-C
              receptacle, in J3's order from the top: SYNC_N 131.85, SYNC_P
              132.15, BUS_N 132.50, BUS_P 132.80 (0.30 mm pitch in a pair,
              0.35 between them). The corridor is 1.0 mm wide for track centres
              between the crystal Y1's pads and the receptacle's front shield
              pins, each with 0.15 mm clearance.
  J4 end      SYNC's two legs drop to B.Cu beside J4 and run there coupled,
              under BUS's legs, up beside R12 (the pair crossing). U11.6 reaches
              R12.1 through the 0.5 mm between U11's pins and R12; SYNC_P leaves
              under R12.2, SYNC_N over it (SYNC's swap). BUS_P threads the
              0.26 mm between U10's pins and R11 to U10.6 and R11.1; BUS_N takes
              a B.Cu hop past it (BUS's swap). Under R12 the three lower
              conductors run 0.3 mm apart and rise into their lanes in turn.
  J3 end      past the receptacle the R27 / R28 / Q1 / D2 group leaves no room
              for four tracks on F.Cu: SYNC goes on in B.Cu, coupled, and comes
              up beside J3.5 / J3.6; BUS stays on F.Cu, steps up under R28 and
              R27, and BUS_N goes up between them and over R27 to J3.4. R27.2's
              ground via stood in BUS_N's way; it moves into the pad (the board
              has filled, capped vias throughout).

Every segment is at 0, 45 or 90 degrees, and lr's exact check (0.15 mm, 0.25
mm to holes) passes on all of it before the board is saved. Run on the
stripped board before freerouting; dsn_prep.py --fixed then keeps it.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr
import rr2
import shapely
from shapely.ops import unary_union
import straighten_rev4 as st

W = 0.15
VIA = (0.5, 0.3)


def pl(layer, *pts):
    return [(layer, a, c) for a, c in zip(pts, pts[1:])]


# pad centres the drawing was made for
PADS = {"J4.3": (103.89, 130.35), "J4.4": (103.89, 131.6), "J4.5": (103.89, 132.85), "J4.6": (103.89, 134.1),
        "U10.6": (107.29, 129.9), "U10.7": (108.56, 129.9), "R11.1": (106.52, 132.055), "R11.2": (108.17, 132.055),
        "U11.6": (114.01, 130.135), "U11.7": (115.28, 130.135), "R12.1": (112.17, 132.085), "R12.2": (113.82, 132.085),
        "J3.3": (154.04, 131.6), "J3.4": (154.04, 130.35), "J3.5": (154.04, 129.1), "J3.6": (154.04, 127.85),
        "R27.2": (150.13, 131.24)}

DRAWING = {
    # J4.3 under U10.5 into the 0.26 mm corridor between U10's pins and R11; up into
    # U10.6, down through R11.1; the lane, bottom of the four; past J5 up 0.5 mm
    # under R28 and R27, up into J3.3 over D2
    "BUS_P": (pl("F", (104.4, 130.35), (104.8, 130.35), (105.675, 131.225), (107.29, 131.225), (107.29, 130.7)) +
              pl("F", (106.52, 131.225), (106.52, 133.45), (114.75, 133.45), (115.4, 132.8), (142.925, 132.8),
                 (143.425, 132.3), (150.45, 132.3), (151.15, 131.6), (153.6, 131.6)), []),
    # J4.4 on B.Cu past BUS_P's corridor, up beside U10.8 into R11.2 and U10.7; under
    # R12 above BUS_P, the lane; past J5 up with BUS_P, then up between R28 and R27,
    # over R27 under D4 into J3.4
    "BUS_N": (pl("F", (104.4, 131.6), (105.2, 131.6)) + pl("B", (105.2, 131.6), (109.2, 131.6)) +
              pl("F", (109.2, 131.6), (108.3, 131.6)) + pl("F", (108.56, 130.75), (108.56, 131.6)) +
              pl("F", (108.17, 132.3), (108.17, 133.15), (114.6, 133.15), (115.25, 132.5), (142.8, 132.5), (143.3, 132.0),
                 (147.6, 132.0), (148.05, 131.55), (148.05, 130.85), (148.3, 130.6), (151.0, 130.6), (151.25, 130.35),
                 (153.6, 130.35)),
              [(105.2, 131.6), (109.2, 131.6)]),
    # J4.5 down to B.Cu, coupled with SYNC_N under the BUS lane legs, up west of R12.1;
    # U11.6 to R12.1 through the 0.5 mm between U11's pins and R12; under R12.2 into the
    # lane; past J5 to B.Cu, coupled on, up beside J3.5
    "SYNC_P": (pl("F", (104.4, 132.85), (105.3, 132.85)) + pl("B", (105.3, 132.85), (110.65, 132.85), (111.2, 132.3)) +
               pl("F", (111.2, 132.3), (112.0, 132.3)) +
               pl("F", (114.01, 130.9), (114.01, 131.36), (112.17, 131.36), (112.17, 132.85), (114.3, 132.85),
                  (115.0, 132.15), (141.3, 132.15), (141.6, 131.85), (142.0, 131.85)) +
               pl("B", (142.0, 131.85), (142.2, 131.65), (148.975, 131.65), (151.525, 129.1), (152.3, 129.1)) +
               pl("F", (152.3, 129.1), (153.6, 129.1)),
               [(105.3, 132.85), (111.2, 132.3), (142.0, 131.85), (152.3, 129.1)]),
    # J4.6 down to B.Cu, up between R12's pads; R12.2 and U11.7 join at the lane, top of
    # the four; past J5 to B.Cu, up beside J3.6
    "SYNC_N": (pl("F", (104.4, 134.1), (105.3, 134.1)) + pl("B", (105.3, 134.1), (106.25, 133.15), (112.15, 133.15), (113.0, 132.3)) +
               pl("F", (113.0, 132.3), (113.6, 132.3)) + pl("F", (115.28, 130.9), (115.28, 131.85)) +
               pl("F", (113.82, 131.85), (140.7, 131.85), (141.2, 131.35)) +
               pl("B", (141.2, 131.35), (148.85, 131.35), (152.35, 127.85)) + pl("F", (152.35, 127.85), (153.6, 127.85)),
               [(105.3, 134.1), (113.0, 132.3), (141.2, 131.35), (152.35, 127.85)]),
}


def coupling(m, a, b, pitch=0.30, tol=0.08):
    """share of b's length at the pair's pitch beside a, on a's layer"""
    tot = inb = 0.0
    for l in ("F", "In2", "B"):
        la = [shapely.LineString([it["a"], it["c"]]) for it in st.net_copper(m, a) if it["kind"] == "track" and l in it["lay"]]
        lb = [shapely.LineString([it["a"], it["c"]]) for it in st.net_copper(m, b) if it["kind"] == "track" and l in it["lay"]]
        tot += sum(x.length for x in lb)
        if la and lb:
            ua = unary_union(la)
            band = ua.buffer(pitch + tol).difference(ua.buffer(max(pitch - tol, 0.01)))
            inb += sum(x.intersection(band).length for x in lb)
    return inb / max(tot, 1e-9)


def per_layer(m, n):
    d = {}
    for it in st.net_copper(m, n):
        if it["kind"] == "track":
            l = next(iter(it["lay"]))
            d[l] = d.get(l, 0.0) + math.dist(it["a"], it["c"])
    return " ".join("%s %.1f" % kv for kv in sorted(d.items()))


def move_r27_tie(m, log):
    """R27.2's ground via sat where BUS_N passes over R27: it goes into the pad."""
    groups = st.stub_groups(m, "GND")
    mine = [(g, touch) for g, touch in groups if any(e["kind"] == "pad" and e["ref"] == "R27.2" for e in touch)]
    if len(mine) != 1:
        sys.exit("pairs_rev4: R27.2's ground stub is not one group (%d)" % len(mine))
    g, touch = mine[0]
    others = {e["uuid"] for gg, tt in groups if gg is not g for e in tt}
    vias = [e for e in touch if e["kind"] == "via" and e["uuid"] not in others]
    rip = [it["uuid"] for it in g] + [v["uuid"] for v in vias]
    m.remove(rip)
    m.index()
    pad = m.pad("R27", "2")
    m.add("GND", [], [pad["xy"]], 0.15, VIA)
    m.index()
    log("R27.2: ground tie moved into the pad (took off %d track(s), %d via(s))" % (len(g), len(vias)))


def draw(m, log):
    """Lay the drawing down on the model; returns the number of problems the exact check finds."""
    for ref, xy in PADS.items():
        r, n = ref.split(".")
        p = m.pad(r, n)["xy"]
        if math.dist(p, xy) > 0.01:
            log("pairs_rev4: %s is at (%.3f, %.3f), not where the drawing expects" % (ref, p[0], p[1]))
            return 1
    move_r27_tie(m, log)
    nets = list(DRAWING)
    m.remove([it["uuid"] for it in m.items if it["net"] in nets and it["kind"] in ("track", "via")])
    m.index()
    for net, (segs, vias) in DRAWING.items():
        for l, a, c in segs:
            ang = math.degrees(math.atan2(c[1] - a[1], c[0] - a[0])) % 45.0
            if min(ang, 45.0 - ang) > 0.01:
                log("pairs_rev4: %s segment %s-%s is not octilinear" % (net, a, c))
                return 1
        m.add(net, segs, vias, W, VIA)
    m.index()
    bad = 0
    for net, (segs, vias) in DRAWING.items():
        for b in m.verify(net, segs, vias, W, VIA):
            log("  %s: %s" % (net, b))
            bad += 1
        n = len(rr2.comps(m, net))
        if n != 1:
            log("  %s: %d pieces" % (net, n))
            bad += 1
    for pn, nn in (("SYNC_P", "SYNC_N"), ("BUS_P", "BUS_N")):
        Lp, Ln = st.jag_of(st.net_copper(m, pn))[0], st.jag_of(st.net_copper(m, nn))[0]
        vp = sum(1 for it in st.net_copper(m, pn) if it["kind"] == "via")
        vn = sum(1 for it in st.net_copper(m, nn) if it["kind"] == "via")
        log("%-4s P %.1f mm (%s) %d via  N %.1f mm (%s) %d via  skew %.2f mm  coupled %.0f%%" % (
            pn[:-2], Lp, per_layer(m, pn), vp, Ln, per_layer(m, nn), vn, abs(Lp - Ln), 100 * coupling(m, pn, nn)))
    log("pairs: %d problem(s)" % bad)
    return bad


def main(src, dst):
    lr.PRO_REF = os.path.join(lr.ROOT, "tmp", "rev4_ref.kicad_pro")
    m = lr.Model(src)
    bad = draw(m, lambda s: print(s, flush=True))
    m.save(dst)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
