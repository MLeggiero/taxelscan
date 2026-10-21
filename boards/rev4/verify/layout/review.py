"""review.py board [board ...] - the six comments of the 21 October design review, measured.

    1  vias in pads     via holes in, or within 0.10 mm (JLCPCB's minimum solder-mask
                        bridge) of, an SMD pad's mask opening; the exposed pads' own
                        ground vias counted apart; two-pad passives with a hole at
                        one pad only (the tombstoning case)
    2  width changes    signal copper at 0.10 mm outside the fine-pitch escape areas,
                        and on the board; nets with more than one width; width steps
                        (two widths meeting) that are not at a pad
    3  +3.3 V tracks    narrowest and longest +3.3 V track, and what 0.15 mm carries
                        (IPC-2221, external 1 oz, 10 C rise) against the rail's draw
    4  right angles     bare corners (two segments of a net meeting off any pad or
                        via) by angle: 45, 90, sharper; on the bus pairs; T-joints
    5  reference plane  signal copper on B.Cu, which runs over In2 (+3.3 V) rather
                        than In1 (GND): the fast nets, the pairs, the rest
    6  edge             copper to the outline's centre line: every footprint under
                        0.5 mm (JLCPCB: 0.2 routed, 0.4 V-cut), tracks and vias under
                        0.4 mm
"""
import collections
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import lr
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

K = lr.K
mm = K.ToMM
POWER = {"GND", "+3.3V", "+5V", "+5V_BUS", "+5V_USB", "VCORE", "ADC_AVDD", "ROW_VCC", "VREG_AVDD", "VREG_LX",
         "SW_NODE", "USB_BUS_SW", "USB_SNUB"}
PAIRS = {"BUS_P", "BUS_N", "SYNC_P", "SYNC_N", "USB_D_P", "USB_D_N", "USBC_D_P", "USBC_D_N"}
FAST = {"ROW_CLK", "ROW_DATA", "ROW_LATCH", "ROW_CLK_MCU", "ROW_LATCH_MCU", "BUS_DI", "BUS_RO", "BUS_DE",
        "SYNC_OUT", "SYNC_DE", "SR_CHAIN_1", "SR_CHAIN_2", "SR_CHAIN_3"}
EP = {("U9", "61"), ("U12", "9")}
WEB = 0.10


def openings(b):
    out = []
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.GetAttribute() == K.PAD_ATTRIB_SMD and p.IsOnLayer(K.F_Mask) and p.IsOnLayer(K.F_Cu):
                g = lr.polyset(p.GetEffectivePolygon(K.F_Cu, K.ERROR_OUTSIDE))
                e = mm(p.GetSolderMaskExpansion(K.F_Mask))
                out.append((f.GetReference(), p.GetNumber(), g.buffer(e) if e > 0 else g))
    return out


def vias_in_pads(m):
    ops = openings(m.b)
    inside = near = ep = 0
    per = collections.defaultdict(set)
    for v in [it for it in m.items if it["kind"] == "via"]:
        h = Point(v["xy"]).buffer(v["drill"] / 2, 32)
        hits = [(r, n) for r, n, g in ops if g.intersects(h)]
        if any(x in EP for x in hits):
            ep += 1
            continue
        d = min(g.distance(h) for r, n, g in ops if (r, n) not in EP)
        if hits:
            inside += 1
            for r, n in hits:
                per[r].add(n)
        elif d < WEB - 1e-4:
            near += 1
    one = sorted(r for r, ps in per.items() if r[0] in "RCLD" and len(ps) == 1)
    return inside, near, ep, one


def widths(m):
    esc = unary_union([lr.polyset(z.Outline()) for z in m.b.Zones() if z.GetIsRuleArea() and
                       z.GetZoneName() in ("U9_escape", "U13_escape")])
    out_esc = thin = 0.0
    per = collections.defaultdict(lambda: collections.defaultdict(float))
    ends = collections.defaultdict(set)
    for it in m.items:
        if it["kind"] != "track" or it["net"] in POWER:
            continue
        L = math.dist(it["a"], it["c"])
        per[it["net"]][round(it["w"], 3)] += L
        if it["w"] < 0.149 and L > 0:
            thin += L
            out_esc += LineString([it["a"], it["c"]]).difference(esc).length
        for p in (it["a"], it["c"]):
            ends[(it["net"], next(iter(it["lay"])), round(p[0], 3), round(p[1], 3))].add(round(it["w"], 3))
    pads = [it for it in m.items if it["kind"] == "pad" and it["geom"] is not None]
    steps = 0
    for (net, l, x, y), ws in ends.items():
        if len(ws) > 1 and not any(p["net"] == net and p["geom"].distance(Point(x, y)) < 0.01 for p in pads):
            steps += 1
    mixed = sorted(n for n, ws in per.items() if len(ws) > 1)
    return thin, out_esc, mixed, steps


def ipc2221(w_mm, oz=1.0, dt=10.0):
    """IPC-2221 external-layer current for a 10 C rise."""
    area = (w_mm / 0.0254) * (1.378 * oz)
    return 0.048 * dt ** 0.44 * area ** 0.725


def rail(m):
    t = [it for it in m.items if it["kind"] == "track" and it["net"] == "+3.3V"]
    return min(it["w"] for it in t), max(math.dist(it["a"], it["c"]) for it in t), sum(math.dist(it["a"], it["c"]) for it in t)


def corners(m):
    ends = collections.defaultdict(list)
    for it in m.items:
        if it["kind"] == "track" and math.dist(it["a"], it["c"]) > 1e-6:
            for p, q in ((it["a"], it["c"]), (it["c"], it["a"])):
                ends[(it["net"], next(iter(it["lay"])), round(p[0], 4), round(p[1], 4))].append((q[0] - p[0], q[1] - p[1]))
    padvia = [it for it in m.items if it["kind"] in ("pad", "via") and it["geom"] is not None]
    by = collections.defaultdict(collections.Counter)
    for (net, l, x, y), v in ends.items():
        grp = "pairs" if net in PAIRS else "power" if net in POWER else "signal"
        if len(v) >= 3:
            by[grp]["T"] += 1
            continue
        if len(v) != 2:
            continue
        (ax, ay), (bx, by_) = v
        cos = (ax * bx + ay * by_) / (math.hypot(ax, ay) * math.hypot(bx, by_))
        turn = 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        if turn < 1.0:
            continue
        if any(it["net"] == net and it["geom"].distance(Point(x, y)) < 0.01 for it in padvia):
            continue                        # a bend inside a pad or via: no corner on the copper
        k = "45" if abs(turn - 45) < 1 else "90" if abs(turn - 90) < 1 else "sharper" if turn > 91 else "other"
        by[grp][k] += 1
    return by


def bcu(m):
    out = collections.Counter()
    for it in m.items:
        if it["kind"] == "track" and it["net"] not in POWER:
            L = math.dist(it["a"], it["c"])
            grp = "fast" if it["net"] in FAST else "pairs" if it["net"] in PAIRS else "other"
            out[(grp, next(iter(it["lay"])))] += L
    return out


def edge(m):
    x0, y0, x1, y1 = m.edge

    def d(g):
        bx = g.bounds
        return min(bx[0] - x0, bx[1] - y0, x1 - bx[2], y1 - bx[3])
    parts = collections.defaultdict(lambda: 9.0)
    for it in m.items:
        if it["kind"] == "pad" and it["geom"] is not None:
            r = it["ref"].split(".")[0]
            parts[r] = min(parts[r], d(it["geom"]))
    close = sorted((round(v, 2), r) for r, v in parts.items() if v < 0.5)
    tv = sorted((round(d(it["geom"]), 2), it["kind"], it["net"]) for it in m.items if it["kind"] in ("track", "via") and d(it["geom"]) < 0.4)
    return close, tv


def main():
    for path in sys.argv[1:]:
        m = lr.Model(path)
        print("== %s" % path)
        inside, near, ep, one = vias_in_pads(m)
        print("1 vias: %d holes in an SMD pad opening, %d more within %.2f mm of one; %d in the exposed pads; "
              "%d two-pad passives with a hole at one pad %s" % (inside, near, WEB, ep, len(one), one))
        thin, out_esc, mixed, steps = widths(m)
        print("2 widths: signal copper at 0.10 mm %.1f mm, %.1f mm of it outside the escape areas; %d nets with "
              "more than one width; %d width steps off the pads" % (thin, out_esc, len(mixed), steps))
        w, L, tot = rail(m)
        print("3 +3.3 V: %.1f mm of track, narrowest %.2f mm (%.2f A by IPC-2221), longest segment %.1f mm; "
              "0.15 mm carries %.2f A" % (tot, w, ipc2221(w), L, ipc2221(0.15)))
        c = corners(m)
        print("4 corners: %s" % "; ".join("%s %s" % (g, dict(v)) for g, v in sorted(c.items())))
        b = bcu(m)
        print("5 signal copper F / B.Cu: %s" % "; ".join("%s %.0f / %.0f mm" % (g, b[(g, "F")], b[(g, "B")])
                                                       for g in ("fast", "pairs", "other")))
        close, tv = edge(m)
        print("6 edge: parts with copper under 0.5 mm %s; tracks / vias under 0.4 mm %s" % (close, tv[:6]))


if __name__ == "__main__":
    main()
