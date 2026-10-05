"""preroute_rev4.py in.kicad_pcb out.kicad_pcb - the nets routed before freerouting.

Freerouting has one clearance per net class and no rule areas, so it cannot use
U9_escape's 0.10 mm, and it knows nothing of pairs. The nets that need either
are routed here first, on the stripped board (every signal off, the planes'
pad-to-via ties kept), by the same exact-geometry model and octilinear router
as everything else; dsn_prep.py --fixed then hands them to freerouting as
copper it must route around. In this order:

  - VREG_LX, the RP2354A regulator's switching node: pin 48 straight to the
    inductor L1 on F.Cu, 0.2 mm, no via (freerouting left it open, and the
    rip-up loop then took it through two vias); and SW_NODE, the buck
    converter's: U12's switch pin to L2 on F.Cu, 0.3 mm, no via (one
    freerouting run sent it through two)
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
  - D5.5, the ESD array's VBUS pin, sits between D+ (D5.6) and D- (D5.4) with
    both lines coming up from J5 on its west, and the package's 0.54 mm pitch
    leaves no room for a via in the pad. It gets one in the middle of D5's
    body, between the two rows, and from there a B.Cu tie to C38, the VBUS
    capacitor beside D5 - before the USB-C pair, which then routes around it
    (dsn_prep.py keeps this +5V_USB copper as protected wiring)
  - USBC_D_N / USBC_D_P, the receptacle side of USB: J5's interleaved data pins
    (B7, A6, A7, B6 at 0.5 mm) bridged in pairs - D- over the pin row, D+
    under it - then through the ESD array D5 pin to pin, and on to the series
    resistors R23 / R24. D5 has D+ on its bottom row and D- on its top, and a
    ground via and C38 close its east side, so D+ cannot get past D-'s row
    on F.Cu: the long run goes on B.Cu, the two side by side (F.Cu costed
    there), which takes 2 vias a line where an F.Cu run needed 2 more to
    pass under the ADC corner. N first, P beside it
  - J5's two VBUS pin pairs, A4/B9 and A9/B4, tied on F.Cu under the pin row
    and round D+'s bridge, 0.3 mm, and on to R34 (the TUSB320's VBUS_DET
    resistor) south of the receptacle's west shield pin. A4/B9 has pins
    0.25 mm away on both sides, a locating peg's hole south of it and U13 1 mm
    north; the way under the row passes B8 at U9_escape's 0.10 mm, which
    freerouting does not have: with the pair in, one run left the pad open and
    two others took +5V_USB to R34 on B.Cu through the crowded corner south of
    U9. Kept off the strip between U13 and the pin row, which the CC and
    VBUS_DET lines need. Protected wiring, like D5.5's
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
from shapely.geometry import LineString, box
from shapely.ops import unary_union
import straighten_rev4 as st
import pairs_rev4
import fr_finish

FIXED = (list(pairs_rev4.DRAWING) + list(fr_finish.CORNER) +
         ["USB_D_P", "USB_D_N", "VREG_LX", "SW_NODE", "VCORE", "USBC_D_N", "USBC_D_P"])
NORTH = box(125.8, 114.2, 129.6, 116.75)      # F.Cu over U9's top pin row, west of R24
USBC_RUN = box(128.6, 113.9, 139.4, 125.0)    # D5 to R23 / R24: F.Cu costed, the pair runs on B.Cu
D5_VBUS_VIA = (137.40, 126.00)                # D5.5's via: under D5's body, between its D+ and D- rows
J5_SOUTH = (132.6, 128.0, 138.6, 131.0)       # J5's pin row and the receptacle south of it
R34_WIN = (127.6, 126.8, 134.6, 131.9)        # J5's VBUS tie to R34, round the receptacle's west shield pin
J5_NORTH = box(129.3, 125.0, 135.0, 128.5)    # between U13 and J5's pin row: the CC / VBUS_DET nets' way
USBC_ENDS = box(133.8, 124.8, 139.2, 130.6)   # J5's data pins and D5: no pair cost, the bridges need room


def switch_node(m, log, net, widths):
    """A switching node: F.Cu only, no via, as wide as it will go."""
    for w in widths:
        ok, _ = rr3.connect(m, net, w, ("F",), margin=1.5)
        its = st.net_copper(m, net)
        if ok:
            log("%s at %.2f mm: %.1f mm, F.Cu, no via" % (net, w, st.jag_of(its)[0]))
            return True
        m.remove([it["uuid"] for it in its])
        m.index()
    return False


def vreg_lx(m, log):
    return switch_node(m, log, "VREG_LX", (0.2, 0.15))


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


def vbus_d5(m, log):
    pad = m.pad("D5", "5")
    x, y = D5_VBUS_VIA
    seg = [("F", pad["xy"], (x, y))]
    bad = m.verify("+5V_USB", seg, [(x, y)], 0.15, (0.5, 0.3))
    if bad:
        log("D5.5: no room for its via: %s" % bad[:2])
        return False
    m.add("+5V_USB", seg, [(x, y)], 0.15, (0.5, 0.3))
    m.index()
    c38 = m.pad("C38", "1")
    cx, cy = c38["xy"]
    win = (min(x, cx) - 1.5, min(y, cy) - 1.5, max(x, cx) + 1.5, max(y, cy) + 1.5)
    r = rr3.route3(m, "+5V_USB", [("B", shapely.Point(x, y).buffer(0.04), None)], rr3.terminals([c38], 0.15), win,
                   0.15, ("F", "B"), align=(x, y))
    if not r or r[2]:
        log("D5.5: no tie from its via to C38")
        return False
    m.add("+5V_USB", r[0], r[1], 0.15)
    m.index()
    log("D5.5: via under D5's body, %.1f mm to C38, %d more via" % (rr3.length(r[0]), len(r[1])))
    return True


def vbus_j5(m, log):
    west, east = m.pad("J5", "B9"), m.pad("J5", "A9")
    r = rr3.route3(m, "+5V_USB", rr3.terminals([west], 0.3), rr3.terminals([east], 0.3), J5_SOUTH, 0.3, ("F",),
                   via=(0.6, 0.3), align=west["xy"])
    if not r or r[2]:
        log("J5 VBUS: no F.Cu tie under the pin row")
        return False
    m.add("+5V_USB", r[0], r[1], 0.3, (0.6, 0.3))
    m.index()
    log("J5 VBUS: A4/B9 to A9/B4 under the pin row, %.1f mm, F.Cu" % rr3.length(r[0]))
    tie = [(l, LineString([a, c]).buffer(0.04), None) for l, a, c in r[0]]
    r = rr3.route3(m, "+5V_USB", tie, rr3.terminals([m.pad("R34", "1")], 0.3), R34_WIN, 0.3, ("F",),
                   via=(0.6, 0.3), keepout={"F": J5_NORTH})
    if not r or r[2]:
        log("J5 VBUS: no F.Cu way round to R34")
        return False
    m.add("+5V_USB", r[0], r[1], 0.3, (0.6, 0.3))
    m.index()
    log("J5 VBUS: on to R34 south of the receptacle's shield pin, %.1f mm, F.Cu" % rr3.length(r[0]))
    return True


def usbc_d(m, log):
    fpen = {"F": [(USBC_RUN, 40)]}
    for w in (0.15, 0.1):
        okn, _ = rr3.connect(m, "USBC_D_N", w, ("F", "B"), margin=1.5, extra_pen=fpen)
        pads = [it for it in m.items if it["net"] in ("USBC_D_P", "USBC_D_N") and it["kind"] == "pad"]
        bx = unary_union([rr2.geo(it) for it in pads]).bounds
        follow = (st.pair_pen(m, "USBC_D_N", (bx[0] - 4, bx[1] - 4, bx[2] + 4, bx[3] + 4)) if okn else None) or {}
        pen = {l: [(poly.difference(USBC_ENDS), v) for poly, v in follow.get(l, [])] + fpen.get(l, [])
               for l in ("F", "B", "In2")}
        okp, _ = rr3.connect(m, "USBC_D_P", w, ("F", "B"), margin=1.5, extra_pen=pen)
        if okn and okp:
            its = {n: st.net_copper(m, n) for n in ("USBC_D_P", "USBC_D_N")}
            log("USBC_D_P / USBC_D_N at %.2f mm: %.1f / %.1f mm, %d / %d via" % (
                w, st.jag_of(its["USBC_D_P"])[0], st.jag_of(its["USBC_D_N"])[0],
                sum(1 for it in its["USBC_D_P"] if it["kind"] == "via"), sum(1 for it in its["USBC_D_N"] if it["kind"] == "via")))
            return True
        m.remove([it["uuid"] for it in m.items if it["net"] in ("USBC_D_P", "USBC_D_N") and it["kind"] in ("track", "via")])
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
    if not switch_node(m, log, "SW_NODE", (0.3, 0.2)):
        sys.exit("preroute: SW_NODE could not be routed on F.Cu")
    if not usb_d(m, log):
        sys.exit("preroute: USB_D_P / USB_D_N could not be routed")
    if not fr_finish.draw_corner(m, log):
        sys.exit("preroute: the ADC corner could not be drawn")
    ok, _ = rr3.connect(m, "RAIL_MON", 0.1, ("F",), margin=1.5)
    log("RAIL_MON C45 - R15 / R16: %s" % ("joined" if ok else "OPEN"))
    if not vcore(m, log):
        sys.exit("preroute: VCORE could not be routed")
    if not vbus_d5(m, log):
        sys.exit("preroute: D5.5's VBUS tie could not be laid")
    if not usbc_d(m, log):
        sys.exit("preroute: USBC_D_P / USBC_D_N could not be routed")
    if not vbus_j5(m, log):
        sys.exit("preroute: J5's VBUS pins could not be tied")
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
