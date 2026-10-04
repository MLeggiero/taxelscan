"""route_rev4.py - finish rev-4's routing after place_rev4.py.

    python3 route_rev4.py <in.kicad_pcb> <out.kicad_pcb>

What is left once the ADC-input cells are down:

    AMP_A, AMP_B          U7's outputs to R6/R8 and on to R21/R22 beside the MCU
    +5V                   R15's feed, which moved with the rail monitor
    USB_CC_OUT1/2         U9.29 / U9.31 (rev-3's SPI pins) to U13 and R32/R33
    USB_ILIM, USB_ILIM_LOW, the +5V B.Cu run beside them
                          legal at 0.10 mm only inside rev-3's U8_escape area,
                          which went with U8; they are ripped where they come
                          within 0.15 mm of something and re-routed at 0.15

Last, the fiducials: their pads carry a 0.6 mm local clearance that the router
model does not know about (it holds every pad to the board's 0.15 mm), so a
fiducial that new copper came too close to is moved to the nearest spot that
keeps 0.6 mm to all copper and its courtyard clear. A fiducial is only an
optical mark for the assembler; a fraction of a millimetre does not matter.

    python3 route_rev4.py --fiducials-only <in.kicad_pcb> <out.kicad_pcb>

runs just that step on an already-routed board.

Routing is ../../rev3/audit-tools' rip-up-and-reroute loop (rr2.rrr) on the
exact-geometry model (lr.py): a net that cannot get through may rip unprotected
signal copper, which then queues to be re-routed itself. Power, plane, analog
and differential-pair nets are never ripped (rr2.PROTECT). The AMP nets are
low-impedance op-amp outputs, so unlike SENSE/GAIN they may take a B.Cu hop -
they have to cross SENSE_B, which may not leave F.Cu. Then the prune loop
deletes whatever KiCad's own DRC calls dangling, and the result is checked
with zones refilled.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr
import rr2
import pcbnew as K

FIX_NETS = ("USB_ILIM", "USB_ILIM_LOW")


def clearance_offenders(m):
    """Signal copper closer than the board rules allow to something else."""
    bad = {}
    for it in m.items:
        if it["kind"] != "track" or it["net"] not in FIX_NETS:
            continue
        l = next(iter(it["lay"]))
        if m.check_track(it["net"], l, it["a"], it["c"], it["w"], ignore=frozenset({it["uuid"]})):
            bad[it["uuid"]] = it
    return bad


FID_CLEAR = 0.6


def fiducial_gap(m, f):
    """Smallest copper edge-to-edge distance from fiducial f's pad, and the
    worst offender. The fiducial's own 0.6 mm is the rule."""
    p = [q for q in f.Pads()][0]
    g = lr.polyset(p.GetEffectivePolygon(K.F_Cu, K.ERROR_OUTSIDE))
    worst = (99.0, None)
    for l in ("F",):
        its, tree = m.by_layer[l]
        for k in tree.query(g.buffer(FID_CLEAR + 0.3)):
            it = its[int(k)]
            if it["ref"].startswith(f.GetReference() + "."):
                continue
            d = g.distance(it["geom"])
            if d < worst[0]:
                worst = (d, it["net"] + " " + it["ref"])
    return worst


def fix_fiducials(m):
    b = m.b
    moved = []
    crts = {g.GetReference(): lr.polyset(g.GetCourtyard(K.F_CrtYd)) for g in b.GetFootprints()
            if g.GetCourtyard(K.F_CrtYd).OutlineCount()}
    for f in b.GetFootprints():
        ref = f.GetReference()
        if not ref.startswith("FID"):
            continue
        gap, who = fiducial_gap(m, f)
        if gap >= FID_CLEAR - 1e-4:
            continue
        x0, y0 = lr.mm(f.GetPosition().x), lr.mm(f.GetPosition().y)
        best = None
        for i in range(-30, 31):
            for j in range(-30, 31):
                x, y = x0 + i * 0.05, y0 + j * 0.05
                d = math.hypot(x - x0, y - y0)
                if d > 1.5 or (best and d >= best[0]):
                    continue
                f.SetPosition(lr.V(x, y))
                c = lr.polyset(f.GetCourtyard(K.F_CrtYd))
                if any(c.intersects(o) and c.intersection(o).area > 1e-6 for r, o in crts.items() if r != ref):
                    continue
                if fiducial_gap(m, f)[0] >= FID_CLEAR + 0.005:
                    best = (d, x, y)
        if best is None:
            f.SetPosition(lr.V(x0, y0))
            print("  %s: no spot within 1.5 mm keeps %.1f mm (closest %s at %.3f mm)" % (ref, FID_CLEAR, who, gap))
            continue
        f.SetPosition(lr.V(best[1], best[2]))
        moved.append(ref)
        print("  %s moved %.2f mm, (%.2f, %.2f) -> (%.2f, %.2f): %s had come to %.3f mm"
              % (ref, best[0], x0, y0, best[1], best[2], who, gap))
    m.index()
    return moved


def main():
    if sys.argv[1] == "--fiducials-only":
        src, dst = sys.argv[2], sys.argv[3]
        lr.PRO_REF = os.path.join(lr.ROOT, "tmp", "rev4_ref.kicad_pro")
        m = lr.Model(src)
        fix_fiducials(m)
        d, c = rr2.prune(m, dst, "route_rev4")
        lr.summary(d, c)
        return 0 if not d["unconnected_items"] else 1
    src, dst = sys.argv[1], sys.argv[2]
    lr.PRO_REF = os.path.join(lr.ROOT, "tmp", "rev4_ref.kicad_pro")
    m = lr.Model(src)

    # the ILIM pair: rip every segment that breaks 0.15 mm, so rrr re-lays it
    off = clearance_offenders(m)
    if off:
        m.remove(set(off))
        m.index()
        rr2.delete_dead(m, list(FIX_NETS))
        print("ripped %d over-close segment(s) of %s" % (len(off), ", ".join(sorted({i["net"] for i in off.values()}))))

    def layers(net):
        if net in ("AMP_A", "AMP_B"):
            return ("F", "B")
        return ("F", "B")

    def width(net):
        if net == "+5V":
            return 0.2
        if net in ("AMP_A", "AMP_B"):
            return 0.15
        return rr2.default_width(m, net)

    must = ["AMP_A", "AMP_B", "+5V", "USB_CC_OUT1", "USB_CC_OUT2"] + list(FIX_NETS)
    res = rr2.rrr(m, must, layers_fn=layers, width_fn=width, margin=2.5, max_iter=120,
                  fallback_layers=("F", "In2", "B"))
    print("rrr:", {k: v for k, v in res.items() if k != "touched"})
    fix_fiducials(m)
    d, c = rr2.prune(m, dst, "route_rev4")
    lr.summary(d, c)
    return 0 if not d["unconnected_items"] else 1


if __name__ == "__main__":
    sys.exit(main())
