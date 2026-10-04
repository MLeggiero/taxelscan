"""place_rev4.py - place the ADC-input networks beside the RP2354A and route them.

    python3 place_rev4.py <in.kicad_pcb> <out.kicad_pcb> [--rail-pin 42|43]

rev-3 sat R21/C31 and R22/C32 hard against U8's CH0/CH1. In rev-4 the converter
is the RP2354A, whose ADC pins (GPIO26-29, pins 40-43) all face east toward the
analog section, and the 1 nF reservoir belongs at those pins: it supplies the
sample-and-hold's charge, and the trace between it and the pin is what that
charge has to cross. The same corner already holds the 5 V rail monitor
(R15/R16/C45), a DC divider with a 100 nF reservoir that does not mind a few
millimetres. So the sensor networks get first claim on the space beside the
pins and the rail monitor takes what is left.

Parts are placed and their connections routed before the next is considered,
in priority order:

    C32 + R22  bank B's input cell: reservoir routed to its ADC pin, the 51R
               routed to the reservoir, the 51R's far pad open to the east
    C31 + R21  bank A's, the same
    C45        rail-monitor reservoir, routed to its ADC pin
    R16, R15   the divider, routed to C45 (R15's +5V is left to route_rev4.py)

A cell is searched as a pair. Placing the capacitor first and the resistor
afterwards can box the capacitor's pad in so that no resistor reaches it (the
first version of this script did exactly that to C32), so each capacitor
candidate is tried with its own best few resistor positions, and the pair is
kept only if the resistor's amplifier-side pad can still be reached from the
analog side - the AMP_A/AMP_B routes come later, from U7, 20 mm east.

For a part, every spot on a 0.1 mm grid in four rotations is checked for
legality: courtyard clear of every other courtyard, every pad 0.15 mm (0.10 mm
where both are in U9_escape) from other nets' copper and 0.25 mm from any hole,
nothing in front of U9's pins where the escapes run, and each GND pad with a
via site within 1.2 mm (never in the pad). The 30 best by straight-line
distance are then actually routed - F.Cu only, the analog rule - and the
shortest routed result wins. Its GND via, stub and route go on the board, so
the next part sees them as obstacles.

--rail-pin says which ADC pin RAIL_MON uses: 42 (GPIO28, rev-3's) or 43
(GPIO29). ADC_B takes the other. The board's U9.42/U9.43 pads are set to
match, so a run with --rail-pin 43 is only consistent with a rev4.net that
says so.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr
import rr2
import pcbnew as K
from shapely.geometry import Point, box
from shapely.affinity import rotate, translate

RIP_NETS = ("ADC_A", "ADC_B", "AMP_A", "AMP_B", "RAIL_MON")
MOVERS = ("C32", "C31", "C45", "R22", "R21", "R16", "R15")
WIN = (131.6, 116.4, 141.6, 124.2)            # placement window, board mm
STEP = 0.1
ROTS = (0, 90, 180, 270)
TOP_K = 30
# Keep the QFN's fan-out clear: the 0201 ring ends at x 131.9, and the band
# between C36 and C35 is the only way the ADC-pin escapes get out.
KEEPOUT = [box(129.0, 116.6, 132.0, 124.6), box(132.0, 118.55, 133.95, 120.08)]
W_ADC = 0.1


def pad_rect(cx, cy, sx, sy, ang):
    r = box(-sx / 2, -sy / 2, sx / 2, sy / 2)
    return translate(rotate(r, -ang, origin=(0, 0)), cx, cy)


class Part:
    """A movable two-pad part, described in its own frame from the board."""

    def __init__(self, f):
        self.ref = f.GetReference()
        x0, y0 = lr.mm(f.GetPosition().x), lr.mm(f.GetPosition().y)
        a0 = math.radians(f.GetOrientationDegrees())
        self.pads = []
        for p in f.Pads():
            if not p.GetNumber():
                continue
            px, py = lr.mm(p.GetPosition().x) - x0, lr.mm(p.GetPosition().y) - y0
            lx = px * math.cos(-a0) + py * math.sin(-a0)
            ly = -px * math.sin(-a0) + py * math.cos(-a0)
            self.pads.append(dict(num=p.GetNumber(), net=p.GetNetname(), lx=lx, ly=ly,
                                  sx=lr.mm(p.GetSize().x), sy=lr.mm(p.GetSize().y),
                                  lang=p.GetOrientationDegrees() - f.GetOrientationDegrees()))
        bb = f.GetCourtyard(K.F_CrtYd).BBox()
        w, h = lr.mm(bb.GetWidth()), lr.mm(bb.GetHeight())
        if round(f.GetOrientationDegrees()) % 180:
            w, h = h, w
        self.cw, self.ch = w, h

    def at(self, x, y, rot):
        a = math.radians(rot)
        pads = []
        for q in self.pads:
            cx = x + q["lx"] * math.cos(a) + q["ly"] * math.sin(a)
            cy = y - q["lx"] * math.sin(a) + q["ly"] * math.cos(a)
            pads.append(dict(num=q["num"], net=q["net"], xy=(cx, cy),
                             geom=pad_rect(cx, cy, q["sx"], q["sy"], q["lang"] + rot)))
        cw, ch = (self.cw, self.ch) if rot % 180 == 0 else (self.ch, self.cw)
        return pads, box(x - cw / 2, y - ch / 2, x + cw / 2, y + ch / 2)


class Placer:
    def __init__(self, path, rail_pin):
        self.m = lr.Model(path)
        self.b = self.m.b
        self.rail_pin = rail_pin
        self.adc_b_pin = "43" if rail_pin == "42" else "42"
        self.f = {r: self.b.FindFootprintByReference(r) for r in MOVERS}
        self.parts = {r: Part(f) for r, f in self.f.items()}
        self.placed = {}
        self.log = []

    # ---------------------------------------------------------------- set-up
    def prepare(self):
        b = self.b
        u9 = b.FindFootprintByReference("U9")
        for p in u9.Pads():                              # match --rail-pin
            if p.GetNumber() == self.rail_pin:
                p.SetNet(b.FindNet("RAIL_MON"))
            elif p.GetNumber() == self.adc_b_pin:
                p.SetNet(b.FindNet("ADC_B"))
        gone = {}
        for t in list(b.GetTracks()):
            if t.GetNetname() in RIP_NETS:
                gone[t.m_Uuid.AsString()] = t
            elif t.Type() != K.PCB_VIA_T:
                for f in self.f.values():
                    for p in f.Pads():
                        if p.GetNetname() == t.GetNetname() and p.GetNetname() in ("GND", "+5V") and (
                                p.HitTest(t.GetStart()) or p.HitTest(t.GetEnd())):
                            gone[t.m_Uuid.AsString()] = t
        ends = [(t.GetNetname(), lr.mm(t.GetStart().x), lr.mm(t.GetStart().y), lr.mm(t.GetEnd().x), lr.mm(t.GetEnd().y))
                for t in gone.values() if t.Type() != K.PCB_VIA_T and t.GetNetname() in ("GND", "+5V")]
        for t in gone.values():
            b.Delete(t)
        for f in self.f.values():                          # off the board while searching
            f.SetPosition(lr.V(300, 300))
        self.m.index()
        # the vias those stubs led to now hang off nothing but a plane; DRC
        # would call them dangling, so they go before the search sees them.
        # Only vias a deleted stub ended on: the plane-stitching vias touch no
        # track either, and they stay.
        dead = []
        for it in self.m.items:
            if it["kind"] != "via" or it["net"] not in ("GND", "+5V"):
                continue
            if not any(n == it["net"] and it["geom"].distance(Point(x, y)) < 0.01
                       for n, xa, ya, xb, yb in ends for x, y in ((xa, ya), (xb, yb))):
                continue
            touching = [o for o in self.m.near(it["geom"], 0.0, ("F", "B", "In2"), ("track", "pad"))
                        if o["net"] == it["net"] and o["uuid"] != it["uuid"]]
            if not touching:
                dead.append(it["uuid"])
        n = self.m.remove(dead)
        self.m.index()
        msg = "ripped %d item(s) of %s and the movers' GND/+5V stubs; %d orphaned via(s)" % (
            len(gone), ", ".join(RIP_NETS), n)
        print(msg)
        self.log.append(msg)

    # ---------------------------------------------------------------- checks
    def legal(self, pads, crt):
        m = self.m
        if any(crt.intersects(k) for k in KEEPOUT):
            return False
        x0, y0, x1, y1 = m.edge
        bx = crt.bounds
        if bx[0] < x0 + lr.EDGE or bx[2] > x1 - lr.EDGE or bx[1] < y0 + lr.EDGE or bx[3] > y1 - lr.EDGE:
            return False
        for c in self.crts:
            if crt.intersects(c) and crt.intersection(c).area > 1e-6:
                return False
        for p in pads:
            g = p["geom"]
            ina = g.intersects(m.area)
            its, tree = m.by_layer["F"]
            for k in tree.query(g.buffer(lr.RULE + 0.02)):
                it = its[int(k)]
                if it["net"] == p["net"] and it["kind"] != "pad":
                    continue                                  # own net's copper may touch
                need = lr.FINE if (ina and it["in_area"]) else lr.RULE
                if g.distance(it["geom"]) < need - 1e-4:
                    return False
            hs, ht = m.holes
            for k in ht.query(g.buffer(lr.HOLE + 0.02)):
                it = hs[int(k)]
                if it["net"] != p["net"] and g.distance(it["hole"]) < lr.HOLE - 1e-4:
                    return False
        return True

    def gnd_via(self, pads):
        gp = [p for p in pads if p["net"] == "GND"]
        if not gp:
            return 0.0, None
        gx, gy = gp[0]["xy"]
        best = None
        for i in range(-12, 13):
            for j in range(-12, 13):
                x, y = gx + i * 0.1, gy + j * 0.1
                dd = math.hypot(x - gx, y - gy)
                if dd > 1.2 or (best and dd >= best[0]):
                    continue
                vg = Point(x, y).buffer(0.25)
                if vg.distance(gp[0]["geom"]) < 0.05:          # no new via-in-pad
                    continue
                if any(p["net"] != "GND" and vg.distance(p["geom"]) < lr.RULE for p in pads):
                    continue
                if self.m.check_via("GND", x, y):
                    continue
                stub = lr.seg_geom((gx, gy), (x, y), 0.15)
                if any(p["net"] != "GND" and stub.distance(p["geom"]) < lr.RULE for p in pads):
                    continue
                if self.m.check_track("GND", "F", (gx, gy), (x, y), 0.15):
                    continue
                best = (dd, (round(x, 3), round(y, 3)))
        return best if best else (None, None)

    # ---------------------------------------------------------------- helpers
    def candidates(self, ref, est, k, win=WIN):
        """The k best legal spots for `ref` by est(pads), each with its GND via."""
        part = self.parts[ref]
        self.crts = [lr.polyset(g.GetCourtyard(K.F_CrtYd)) for g in self.b.GetFootprints()
                     if g.GetReference() != ref and g.GetCourtyard(K.F_CrtYd).OutlineCount()]
        cands = []
        nx = int(round((win[2] - win[0]) / STEP)) + 1
        ny = int(round((win[3] - win[1]) / STEP)) + 1
        for i in range(nx):
            x = round(win[0] + i * STEP, 3)
            for j in range(ny):
                y = round(win[1] + j * STEP, 3)
                for rot in ROTS:
                    pads, crt = part.at(x, y, rot)
                    cands.append((est(pads), x, y, rot, pads, crt))
        cands.sort(key=lambda c: c[0])
        out = []
        for e, x, y, rot, pads, crt in cands:
            if len(out) >= k:
                break
            if any(abs(x - o[1]) < 0.25 and abs(y - o[2]) < 0.25 and rot == o[3] for o in out):
                continue
            if not self.legal(pads, crt):
                continue
            dv, via = self.gnd_via(pads)
            if dv is None:
                continue
            out.append((e, x, y, rot, pads, dv, via))
        return out

    def put(self, ref, x, y, rot, pads=None):
        f = self.f[ref]
        f.SetPosition(lr.V(x, y))
        f.SetOrientationDegrees(rot)
        if pads:                                   # the frame model must agree with pcbnew
            for p in f.Pads():
                if p.GetNumber():
                    q = [z for z in pads if z["num"] == p.GetNumber()][0]
                    got = (lr.mm(p.GetPosition().x), lr.mm(p.GetPosition().y))
                    assert math.dist(got, q["xy"]) < 0.002, (ref, p.GetNumber(), got, q["xy"])
        self.m.index()

    def park(self, ref):
        self.f[ref].SetPosition(lr.V(300, 300))
        self.m.index()

    def route_f(self, net, a, c, w=W_ADC):
        m = self.m
        pa, pc = m.pad(*a)["xy"], m.pad(*c)["xy"]
        win = (min(pa[0], pc[0]) - 1.5, min(pa[1], pc[1]) - 1.5,
               max(pa[0], pc[0]) + 1.5, max(pa[1], pc[1]) + 1.5)
        r = rr2.route2(m, net, rr2.terminals([m.pad(*a)], w), rr2.terminals([m.pad(*c)], w),
                       win, w, layers=("F",))
        if not r or r[2]:
            return None
        return r[0], sum(math.dist(sg[1], sg[2]) for sg in r[0])

    def add(self, net, segs, vias, w):
        """Put copper on the board; returns the new items' uuids."""
        before = {t.m_Uuid.AsString() for t in self.b.GetTracks()}
        self.m.add(net, segs, vias, w)
        self.m.index()
        return {t.m_Uuid.AsString() for t in self.b.GetTracks()} - before

    def gnd_stub(self, pads, via):
        if not via:
            return set()
        gp = [p for p in pads if p["net"] == "GND"][0]
        return self.add("GND", [("F", gp["xy"], via)], [via], 0.15)

    def reach_east(self, net, term, x_east=142.0):
        """Can the AMP route still get from this pad to the analog side at all?"""
        m = self.m
        px, py = m.pad(*term)["xy"]
        dst = [(l, box(x_east - 0.2, 116.9, x_east + 0.2, 123.3), None) for l in ("F", "B")]
        win = (px - 2.0, 116.0, x_east + 0.6, 124.2)
        r = rr2.route2(m, net, rr2.terminals([m.pad(*term)], 0.15), dst, win, 0.15, layers=("F", "B"))
        return bool(r and not r[2])

    def report(self, msg):
        print(msg)
        self.log.append(msg)

    # ---------------------------------------------------------------- a cell
    def place_cell(self, cref, rref, net, pin, amp):
        m = self.m
        pin_xy = m.pad("U9", pin)["xy"]
        amp_xy = m.pad(*amp)["xy"]
        ccands = self.candidates(cref, lambda pads: math.dist(pads[0]["xy"], pin_xy), 24)
        best = None
        for e, x, y, rot, cpads, dv, via in ccands:
            self.put(cref, x, y, rot, cpads)
            r1 = self.route_f(net, ("U9", pin), (cref, "1"))
            if not r1:
                continue
            tmp = self.gnd_stub(cpads, via) | self.add(net, r1[0], [], W_ADC)
            c1 = m.pad(cref, "1")["xy"]

            def rest(pads):
                p1 = [q for q in pads if q["num"] == "1"][0]["xy"]
                p2 = [q for q in pads if q["num"] == "2"][0]["xy"]
                return math.dist(p2, c1) + 0.15 * math.dist(p1, amp_xy)
            win = (c1[0] - 2.6, c1[1] - 2.6, c1[0] + 2.6, c1[1] + 2.6)
            for e2, x2, y2, rot2, rpads, _dv, _v in self.candidates(rref, rest, 6, win):
                self.put(rref, x2, y2, rot2, rpads)
                r2 = self.route_f(net, (rref, "2"), (cref, "1"))
                if not r2:
                    continue
                cost = r1[1] + r2[1] + 2.0 * dv + 0.2 * math.dist(m.pad(rref, "1")["xy"], amp_xy)
                if best and cost >= best[0]:
                    continue
                if not self.reach_east("AMP_" + net[-1], (rref, "1")):
                    continue
                best = (cost, (x, y, rot, cpads, via, r1), (x2, y2, rot2, rpads, r2))
            self.park(rref)
            m.remove(tmp)
            m.index()
        if best is None:
            self.park(cref)
            self.report("  %s+%s  no routable cell among %d capacitor spots" % (cref, rref, len(ccands)))
            return False
        cost, (x, y, rot, cpads, via, r1), (x2, y2, rot2, rpads, r2) = best
        self.put(cref, x, y, rot, cpads)
        self.gnd_stub(cpads, via)
        self.add(net, r1[0], [], W_ADC)
        self.put(rref, x2, y2, rot2, rpads)
        self.add(net, r2[0], [], W_ADC)
        self.placed[cref] = (x, y, rot)
        self.placed[rref] = (x2, y2, rot2)
        self.report("  %-4s (%.2f, %.2f) rot %3d   %s pin->C %.2f mm   GND via %s" % (cref, x, y, rot, net, r1[1], via))
        self.report("  %-4s (%.2f, %.2f) rot %3d   %s R->C   %.2f mm" % (rref, x2, y2, rot2, net, r2[1]))
        return True

    # ---------------------------------------------------------------- one part
    def place(self, ref, conns):
        """conns: [(net, (ref, pad), (ref, pad))] to route once `ref` is down."""
        m = self.m

        def xy_of(term, pads):
            r, num = term
            if r == ref:
                return [p for p in pads if p["num"] == num][0]["xy"]
            return m.pad(r, num)["xy"]
        short = self.candidates(ref, lambda pads: sum(math.dist(xy_of(a, pads), xy_of(c, pads))
                                                      for _n, a, c in conns), TOP_K)
        best = None
        for e, x, y, rot, pads, dv, via in short:
            self.put(ref, x, y, rot, pads)
            routes, length = [], 2.0 * dv
            for net, a, c in conns:
                r = self.route_f(net, a, c)
                if not r:
                    break
                routes.append((net, r[0]))
                length += r[1]
            else:
                if best is None or length < best[0]:
                    best = (length, x, y, rot, pads, via, routes)
        if best is None:
            self.park(ref)
            self.report("  %-4s no routable spot among %d legal candidates" % (ref, len(short)))
            return False
        length, x, y, rot, pads, via, routes = best
        self.put(ref, x, y, rot, pads)
        self.gnd_stub(pads, via)
        for net, segs in routes:
            self.add(net, segs, [], W_ADC)
        self.placed[ref] = (x, y, rot)
        self.report("  %-4s (%.2f, %.2f) rot %3d   routed %s %.2f mm   GND via %s" % (
            ref, x, y, rot, "+".join(n for n, _ in routes) or "-", length, via))
        return True

    def run(self):
        self.prepare()
        ok = self.place_cell("C32", "R22", "ADC_B", self.adc_b_pin, ("U7", "7"))
        ok &= self.place_cell("C31", "R21", "ADC_A", "41", ("U7", "1"))
        ok &= self.place("C45", [("RAIL_MON", ("U9", self.rail_pin), ("C45", "1"))])
        ok &= self.place("R16", [("RAIL_MON", ("R16", "1"), ("C45", "1"))])
        ok &= self.place("R15", [("RAIL_MON", ("R15", "2"), ("C45", "1"))])
        return ok


def main():
    src, dst = sys.argv[1], sys.argv[2]
    rail_pin = "42"
    if "--rail-pin" in sys.argv:
        rail_pin = sys.argv[sys.argv.index("--rail-pin") + 1]
    assert rail_pin in ("42", "43")
    pl = Placer(src, rail_pin)
    ok = pl.run()
    pl.m.save(dst)
    print("saved %s%s" % (dst, "" if ok else "  (INCOMPLETE)"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
