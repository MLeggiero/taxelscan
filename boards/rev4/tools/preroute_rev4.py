"""preroute_rev4.py in.kicad_pcb out.kicad_pcb - the nets routed before freerouting.

Freerouting has one clearance per net class and no rule areas, so it cannot use
U9_escape's 0.10 mm, and it knows nothing of pairs. The nets that need either
are routed here first, on the stripped board (every signal off, the planes'
pad-to-via ties kept), by the same exact-geometry model and octilinear router
as everything else; dsn_prep.py --fixed then hands them to freerouting as
copper it must route around. In this order:

  - VREG_LX, the RP2354A regulator's switching node: pin 48 straight to the
    inductor L1 on F.Cu, 0.2 mm, no via (freerouting left it open, and the
    rip-up loop then took it through two vias)
  - USB_D_P / USB_D_N, the RP2354A's USB pins to the 27-ohm series resistors:
    pins 51 and 52 are 0.4 mm apart in U9_escape, which freerouting's 0.15 mm
    clearance leaves no way out of; rr3, P first and N beside it, down
    through vias between the pin row and the exposed pad and across on B.Cu,
    F.Cu north of the pin row costed - that is where VREG_LX and VCORE's pin 50
    leave. Both before the plane ties are redrawn, because that way out is where
    a redrawn +3.3 V tie would otherwise put its via
  - the ADC corner: ADC_B, RAIL_MON and ADC_A out of the RP2354A's pins 43, 42
    and 41, drawn (fr_finish.CORNER); then RAIL_MON's other end, C45 to the
    divider R15 / R16, by rr3
  - VCORE, the 1.1 V core rail: L1 and C19 to pins 6, 23, 39 and 50 (VREG_FB,
    the regulator's sense) and their capacitors, 0.20 mm where it fits, kept
    out from under the QFN body (the ring between the exposed pad and the
    pins). Freerouting twice left pin 50 open - boxed in by USB_D and a
    redrawn +3.3 V tie - so the whole rail goes in before the ties
  - the GND / +3.3 V pad-to-via ties, redrawn octilinear
    (straighten_rev4.redo_plane_stubs) around all of that
  - the RS-485 pairs, BUS_P/N and SYNC_P/N: drawn (pairs_rev4.py)

Every net named here must end up in one piece and pass the exact check, or this
stops.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr
import rr2
import rr3
import shapely
from shapely.geometry import box
from shapely.ops import unary_union
import straighten_rev4 as st
import pairs_rev4
import fr_finish

FIXED = list(pairs_rev4.DRAWING) + list(fr_finish.CORNER) + ["USB_D_P", "USB_D_N", "VREG_LX", "VCORE"]
NORTH = box(125.8, 114.2, 129.6, 116.75)      # F.Cu over U9's top pin row, west of R24


def vreg_lx(m, log):
    for w in (0.2, 0.15):
        ok, _ = rr3.connect(m, "VREG_LX", w, ("F",), margin=1.5)
        its = st.net_copper(m, "VREG_LX")
        if ok:
            log("VREG_LX at %.2f mm: %.1f mm, F.Cu, no via" % (w, st.jag_of(its)[0]))
            return True
        m.remove([it["uuid"] for it in its])
        m.index()
    return False


def usb_d(m, log):
    north = {"F": [(NORTH, 60)]}
    for w in (0.15, 0.1):
        okp, _ = rr3.connect(m, "USB_D_P", w, ("F", "B"), margin=1.5, extra_pen=north)
        pads = [it for it in m.items if it["net"] in ("USB_D_P", "USB_D_N") and it["kind"] == "pad"]
        bx = unary_union([rr2.geo(it) for it in pads]).bounds
        follow = st.pair_pen(m, "USB_D_P", (bx[0] - 4, bx[1] - 4, bx[2] + 4, bx[3] + 4)) if okp else None
        pen = {l: (follow or {}).get(l, []) + north.get(l, []) for l in ("F", "B", "In2")}
        okn, _ = rr3.connect(m, "USB_D_N", w, ("F", "B"), margin=1.5, extra_pen=pen)
        if okp and okn:
            log("USB_D_P / USB_D_N at %.2f mm: %.1f / %.1f mm, %d / %d via" % (
                w, st.jag_of(st.net_copper(m, "USB_D_P"))[0], st.jag_of(st.net_copper(m, "USB_D_N"))[0],
                sum(1 for it in st.net_copper(m, "USB_D_P") if it["kind"] == "via"),
                sum(1 for it in st.net_copper(m, "USB_D_N") if it["kind"] == "via")))
            return True
        m.remove([it["uuid"] for it in m.items if it["net"] in ("USB_D_P", "USB_D_N") and it["kind"] in ("track", "via")])
        m.index()
    return False


def qfn_interior(m):
    """U9's body between its exposed pad and its pin rows."""
    pads = [it for it in m.items if it["kind"] == "pad" and it["ref"].startswith("U9.")]
    ep = max(pads, key=lambda it: it["geom"].area)
    pins = [it for it in pads if it is not ep]
    cx, cy = ep["xy"]
    return box(max(it["geom"].bounds[2] for it in pins if it["xy"][0] < cx - 2),
               max(it["geom"].bounds[3] for it in pins if it["xy"][1] < cy - 2),
               min(it["geom"].bounds[0] for it in pins if it["xy"][0] > cx + 2),
               min(it["geom"].bounds[1] for it in pins if it["xy"][1] > cy + 2))


def vcore(m, log):
    body = qfn_interior(m)
    for w in (0.2, 0.15, 0.1):
        ok, _ = rr3.connect(m, "VCORE", w, ("F", "B"), margin=1.5, keepout={"F": body})
        its = st.net_copper(m, "VCORE")
        if ok:
            log("VCORE at %.2f mm: %.1f mm, %d via, nothing on In2" % (w, st.jag_of(its)[0],
                                                                    sum(1 for it in its if it["kind"] == "via")))
            return True
        m.remove([it["uuid"] for it in its])
        m.index()
    return False


def main(src, dst):
    lr.PRO_REF = os.path.join(lr.ROOT, "tmp", "rev4_ref.kicad_pro")
    log = lambda s: print(s, flush=True)
    m = lr.Model(src)
    if not vreg_lx(m, log):
        sys.exit("preroute: VREG_LX could not be routed on F.Cu")
    if not usb_d(m, log):
        sys.exit("preroute: USB_D_P / USB_D_N could not be routed")
    if not fr_finish.draw_corner(m, log):
        sys.exit("preroute: the ADC corner could not be drawn")
    ok, _ = rr3.connect(m, "RAIL_MON", 0.1, ("F",), margin=1.5)
    log("RAIL_MON C45 - R15 / R16: %s" % ("joined" if ok else "OPEN"))
    if not vcore(m, log):
        sys.exit("preroute: VCORE could not be routed")
    for net in st.PLANE:
        st.redo_plane_stubs(m, net, log)
    if pairs_rev4.draw(m, log):
        sys.exit("preroute: the pair drawing does not verify")
    bad = [n for n in FIXED if len(rr2.comps(m, n)) != 1]
    nbad = 0
    for net in FIXED:
        its = st.net_copper(m, net)
        for it in its:
            if it["kind"] == "track":
                b = m.check_track(net, next(iter(it["lay"])), it["a"], it["c"], it["w"])
            else:
                b = m.check_via(net, it["xy"][0], it["xy"][1], it["size"], it["drill"])
            for x in b:
                log("  %s: %s %s" % (net, it["kind"], x))
                nbad += 1
    m.save(dst)
    if bad or nbad:
        sys.exit("preroute: open %s, %d clearance problem(s)" % (bad, nbad))
    log("pre-routed and fixed: %s" % ",".join(FIXED))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
