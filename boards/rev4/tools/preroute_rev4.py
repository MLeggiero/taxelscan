"""preroute_rev4.py in.kicad_pcb out.kicad_pcb - the nets routed before freerouting.

Freerouting has one clearance per net class and no rule areas, so it cannot use
U9_escape's 0.10 mm, and it knows nothing of pairs or of how a regulator has to
be laid out. The nets that need any of that are laid here first, on the
stripped board (every signal off, the planes' ties kept - fix_rev4.py drew the
regulator's and the buck's GND / +3.3 V copper), by the same exact-geometry
model and octilinear router as everything else; dsn_prep.py --fixed then hands
them to freerouting as copper it must route around. In this order:

  - the RP2354A's core regulator, drawn as the RP2350 datasheet's Figure 26
    and Raspberry Pi's RP2350A minimal design (vreg): VREG_LX from pin 48
    straight up between CIN's (C44) and COUT's (C19) pads into L1, on F.Cu
    with no via, 0.2 mm off the pin and 0.3 mm on; VCORE's regulator end - L1's dot pad to COUT, and down
    through two vias west of COUT - and VREG_FB from COUT's pad back to pin 50,
    clear of LX; VREG_AVDD from pin 46 to CFILT (C43), the 33 R (R35) and
    C16. Then SW_NODE, the buck converter's: the TPS62162's switch pin to L2
    on F.Cu, 0.3 mm, no via; and the buck's +5 V input at the pins - C27
    across VIN / PGND, EN tied to VIN, on to C22 - which freerouting joins to
    D1 and D2 (fr_finish.py keeps it as laid)
  - USB_D_P / USB_D_N, the RP2354A's USB pins to the 27-ohm series resistors
    R23 / R24, north-west on F.Cu beside VREG_FB as the minimal design runs
    them; pins 51 and 52 are 0.4 mm apart in U9_escape, which freerouting's
    0.15 mm clearance leaves no way out of
  - the ADC corner: ADC_B, RAIL_MON and ADC_A out of the RP2354A's pins 43, 42
    and 41, drawn (fr_finish.CORNER); then RAIL_MON's other end, C45 to the
    divider R15 / R16, by rr3
  - the rest of VCORE, the 1.1 V core rail: from the two vias to pins 6, 23
    and 39 and their capacitors, 0.20 mm where it fits, kept out from under the
    QFN body on F.Cu (the ring between the exposed pad and the pins) and from
    under L1 and the LX track on B.Cu
  - D5.5, the ESD array's VBUS pin, sits between D+ (D5.6) and D- (D5.4) with
    both lines coming up from J5 on its west, and the package's 0.54 mm pitch
    leaves no room for a via in the pad. It gets one in the middle of D5's
    body, between the two rows, and from there a B.Cu tie to C38, the VBUS
    capacitor beside D5 - before the USB-C pair, which then routes around it
    (dsn_prep.py keeps this +5V_USB copper as protected wiring)
  - USBC_D_N / USBC_D_P, the receptacle side of USB: J5's interleaved data pins
    (B7, A6, A7, B6 at 0.5 mm) bridged in pairs - D- over the pin row, D+
    under it - then through the ESD array D5 pin to pin, and on to the series
    resistors R23 / R24 north-west of U9. D5 has D+ on its bottom row and D- on
    its top, and a ground via and C38 close its east side, so D+ cannot get
    past D-'s row on F.Cu: the long run goes on B.Cu, the two side by side
    (F.Cu costed there), clear of L1 and the LX track. N first, P beside it
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
  - the crystal, XOUT_MCU, XIN and XOUT, on F.Cu without a via: U9's pins 22 /
    21 to R36, C20 and Y1, and R36 on to Y1 and C21. Laid after the debug
    lines below, which leave U9 beside them, they had no F.Cu way left
  - the corner south-east of U9, which freerouting and the rip-up loop could
    not finish (7 October): ADDR0-2 (U9's pins 32-34 to the address jumpers)
    and USB_CC_OUT1 (pin 29 to R32 and the TUSB320) drawn (SOUTH_EAST, after
    the board of 7ca63a4, whose parts there have not moved); then the TUSB320's
    other nets by rr3 - USB_CC_OUT2, USB_VBUS_DET, USB_CC2, USB_CC1, in that
    order (CC2 passes under the USB-C pair; with CC1 or CC2 first, one of the
    others has no way)
  - USB_ILIM, U14's current-limit pin to R28 and R27: the pairs run between
    the two resistors, and once freerouting has filled the board neither it
    nor the rip-up loop finds a way round (7 October: no path); laid here, on
    the nearly empty board, it crosses the lane on two vias (the board before
    took four)
  - SWCLK, SWDIO and RUN, U9's pins 24 / 25 / 26 to J6 and R20: with J6 in
    Raspberry Pi's debug order the corner south of U9 was left to the rip-up
    loop, which churned there round after round (7 October). In that order
    (with RUN or SWDIO first, past the crystal, one of the others had no way)

Every net named in FIXED must end up in one piece and pass the exact check, or
this stops.
"""
import math
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
         ["USB_D_P", "USB_D_N", "VREG_LX", "SW_NODE", "VCORE", "VREG_AVDD", "USBC_D_N", "USBC_D_P", "USB_ILIM",
          "XOUT_MCU", "XIN", "XOUT", "ADDR0", "ADDR1", "ADDR2", "USB_CC_OUT1", "USB_CC_OUT2", "USB_VBUS_DET",
          "USB_CC2", "USB_CC1", "SWCLK", "SWDIO", "RUN"])
USBC_RUN = box(127.0, 110.9, 139.4, 125.0)    # D5 to R23 / R24: F.Cu costed, the pair runs on B.Cu
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


def corner(m):
    """Offsets from U9.48 (VREG_LX), the origin fix_rev4.py placed the regulator by."""
    x, y = m.pad("U9", "48")["xy"]
    return lambda dx, dy: (round(x + dx, 3), round(y + dy, 3))


# pads the drawings below are made for, as offsets from U9.48 (fix_rev4.PLACE)
CORNER_PADS = {("C44", "1"): (-0.515, -1.16), ("C44", "2"): (0.515, -1.16), ("C19", "1"): (-0.515, -2.10),
               ("C19", "2"): (0.515, -2.10), ("L1", "1"): (-0.8, -3.75), ("L1", "2"): (0.8, -3.75),
               ("C43", "1"): (2.2, -2.101), ("R35", "2"): (2.2, -2.971), ("C16", "1"): (3.094, -1.941),
               ("R24", "1"): (-2.1, -3.441), ("R23", "1"): (-3.1, -3.441)}


def lay(m, log, net, polylines, vias=(), via=(0.6, 0.3)):
    """Polylines [(width, [points])] on F.Cu, or (width, [points], layer), and vias,
    checked exactly before any goes on."""
    segs = lambda pl: [((pl[2] if len(pl) > 2 else "F"), a, c) for a, c in zip(pl[1], pl[1][1:])]
    bad = []
    for pl in polylines:
        bad += m.verify(net, segs(pl), [], pl[0])
    bad += m.verify(net, [], list(vias), 0.2, via)
    if bad:
        for b in bad[:6]:
            log("  %s: %s" % (net, b))
        return False
    for pl in polylines:
        m.add(net, segs(pl), [], pl[0])
    if vias:
        m.add(net, [], list(vias), 0.2, via)
    m.index()
    return True


def vreg(m, log):
    """The core regulator, as the minimal design: LX, VCORE's regulator end, VREG_FB, VREG_AVDD."""
    at = corner(m)
    for (ref, num), (dx, dy) in CORNER_PADS.items():
        got = m.pad(ref, num)["xy"]
        if math.dist(got, at(dx, dy)) > 0.01:
            log("  %s.%s is at %s, the drawing wants %s - fix_rev4.py's placement changed" % (ref, num, got, at(dx, dy)))
            return False
    # pin 48 straight up between CIN's and COUT's pads, 0.2 mm in the pin row, then 0.3 mm, into L1.2
    ok = lay(m, log, "VREG_LX", [(0.2, [at(0, 0), at(0, -0.651)]),
                                 (0.3, [at(0, -0.651), at(0, -2.501), at(0.544, -3.045)])])
    # L1's dot pad to COUT, and the two vias west of COUT, joined on both layers so
    # both carry the rail down; VREG_FB from COUT's pad
    ok = ok and lay(m, log, "VCORE", [(0.4, [at(-0.556, -3.30), at(-0.556, -2.10)]),
                                      (0.4, [at(-1.5, -2.35), at(-0.556, -2.35)]),
                                      (0.4, [at(-1.5, -2.35), at(-1.5, -1.75)]),
                                      (0.4, [at(-1.5, -2.35), at(-1.5, -1.75)], "B"),
                                      (0.15, [at(-0.8, 0), at(-0.8, -0.651), at(-1.05, -0.901), at(-1.05, -1.951),
                                              at(-0.656, -1.951)])],
                    vias=[at(-1.5, -2.35), at(-1.5, -1.75)])
    # pin 46 out between its neighbours' corners to CFILT, on to R35 and C16
    ok = ok and lay(m, log, "VREG_AVDD", [(0.2, [at(0.8, 0), at(0.8, -0.15)]),
                                          (0.15, [at(0.8, -0.15), at(1.64, -0.99), at(1.64, -1.54), at(2.2, -2.10)]),
                                          (0.2, [at(2.2, -2.10), at(2.2, -2.971)]),
                                          (0.2, [at(2.2, -2.001), at(3.094, -2.001)])])
    if ok:
        log("core regulator drawn as the RP2350 minimal design: VREG_LX %.1f mm F.Cu, VREG_FB %.1f mm from COUT, "
            "VCORE down two vias west of COUT" % (st.jag_of(st.net_copper(m, "VREG_LX"))[0],
                                                  sum(math.dist(it["a"], it["c"]) for it in st.net_copper(m, "VCORE")
                                                      if it["kind"] == "track" and it["w"] < 0.2)))
    return ok


def buck_in(m, log):
    """The TPS62162's +5 V input at its pins: VIN to C27, EN to VIN, C27 on to C22."""
    vin, en = m.pad("U12", "2")["xy"], m.pad("U12", "3")["xy"]
    c27, c22 = m.pad("C27", "1")["xy"], m.pad("C22", "1")["xy"]
    ok = lay(m, log, "+5V", [(0.2, [(vin[0], vin[1] + 0.1), (c27[0], vin[1] + 0.1)]),
                             (0.2, [(vin[0] - 0.2, vin[1]), (en[0] - 0.2, en[1])]),
                             (0.3, [(c27[0] + 0.18, c27[1] + 0.02), (c27[0] + 0.18, c22[1] - 0.25)])])
    if ok:
        log("+5V at U12: VIN and EN to C27 and C22 on F.Cu")
    return ok


def usb_d(m, log):
    """Pins 51 / 52 north-west to R24 / R23 on F.Cu, the minimal design's way out."""
    at = corner(m)
    ok = lay(m, log, "USB_D_N", [(0.15, [at(-1.2, 0), at(-1.2, -0.651), at(-2.1, -1.551), at(-2.1, -3.441)])])
    ok = ok and lay(m, log, "USB_D_P", [(0.15, [at(-1.6, 0), at(-1.6, -0.551), at(-3.1, -2.051), at(-3.1, -3.441)])])
    if ok:
        log("USB_D_P / USB_D_N drawn: %.1f / %.1f mm F.Cu, no via" % (
            st.jag_of(st.net_copper(m, "USB_D_P"))[0], st.jag_of(st.net_copper(m, "USB_D_N"))[0]))
    return ok


def lx_zone(m, grow=0.0):
    """The In1 cut-out under L1 and the LX track (rule area VREG_LX_cutout)."""
    for z in m.b.Zones():
        if z.GetIsRuleArea() and z.GetZoneName() == "VREG_LX_cutout":
            return lr.polyset(z.Outline()).buffer(grow)
    return shapely.Polygon()


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
    keep = {l: lx_zone(m, 0.3) for l in ("F", "B")}
    for w in (0.15, 0.1):
        okn, _ = rr3.connect(m, "USBC_D_N", w, ("F", "B"), margin=1.5, extra_pen=fpen, keepout=keep)
        pads = [it for it in m.items if it["net"] in ("USBC_D_P", "USBC_D_N") and it["kind"] == "pad"]
        bx = unary_union([rr2.geo(it) for it in pads]).bounds
        follow = (st.pair_pen(m, "USBC_D_N", (bx[0] - 4, bx[1] - 4, bx[2] + 4, bx[3] + 4)) if okn else None) or {}
        pen = {l: [(poly.difference(USBC_ENDS), v) for poly, v in follow.get(l, [])] + fpen.get(l, [])
               for l in ("F", "B", "In2")}
        okp, _ = rr3.connect(m, "USBC_D_P", w, ("F", "B"), margin=1.5, extra_pen=pen, keepout=keep)
        if okn and okp:
            its = {n: st.net_copper(m, n) for n in ("USBC_D_P", "USBC_D_N")}
            log("USBC_D_P / USBC_D_N at %.2f mm: %.1f / %.1f mm, %d / %d via" % (
                w, st.jag_of(its["USBC_D_P"])[0], st.jag_of(its["USBC_D_N"])[0],
                sum(1 for it in its["USBC_D_P"] if it["kind"] == "via"), sum(1 for it in its["USBC_D_N"] if it["kind"] == "via")))
            return True
        m.remove([it["uuid"] for it in m.items if it["net"] in ("USBC_D_P", "USBC_D_N") and it["kind"] in ("track", "via")])
        m.index()
    return False


def usb_ilim(m, log):
    """U14's current-limit pin to R28 and R27, which sit either side of the RS-485 pairs' lane."""
    for w in (0.15, 0.1):
        ok, _ = rr3.connect(m, "USB_ILIM", w, ("F", "B"), margin=2.0)
        its = st.net_copper(m, "USB_ILIM")
        if ok:
            log("USB_ILIM at %.2f mm: %.1f mm, %d via" % (w, st.jag_of(its)[0],
                                                       sum(1 for it in its if it["kind"] == "via")))
            return True
        m.remove([it["uuid"] for it in its])
        m.index()
    return False


def crystal(m, log):
    """XOUT_MCU, XIN and XOUT on F.Cu without a via: U9's pins 22 / 21 to R36, C20 and
    Y1, and R36 on to Y1 and C21, XOUT_MCU first (fr_finish.crystal's order). Laid after
    the debug lines, which leave U9 beside them, they had no F.Cu way left (7 October)."""
    for net in fr_finish.CRYSTAL + ("XOUT",):
        ok, _ = rr3.connect(m, net, 0.15, ("F",), margin=2.0)
        its = st.net_copper(m, net)
        if not ok:
            log("%s: no F.Cu way" % net)
            return False
        log("%s: %.1f mm, F.Cu" % (net, st.jag_of(its)[0]))
    return True


# The corner south-east of U9, drawn: its parts have not moved since the board of
# 7ca63a4, and these follow that board's routing (freerouting's), straightened.
# ADDR0 and ADDR1 drop to B.Cu in U9's pin ring and ADDR2 just east of pin 34, and
# the three run down to the address jumpers side by side on B.Cu; USB_CC_OUT1
# drops under pin 29 and crosses under the east pin row to R32 and the TUSB320.
# Searched one net at a time instead, in any order, one of these, the TUSB320's
# other nets or the debug lines was left with no way (7 October).
SOUTH_EAST = {
    "ADDR0": ([(0.15, [(129.956, 123.051), (129.406, 123.051), (129.156, 123.301)], "F"),
               (0.15, [(129.156, 123.301), (127.821, 124.636), (127.821, 128.186), (122.706, 133.301)], "B"),
               (0.15, [(122.706, 133.301), (121.406, 134.601), (121.406, 135.701)], "F")],
              [(129.156, 123.301), (122.706, 133.301)]),
    "ADDR1": ([(0.15, [(129.956, 122.651), (129.266, 122.651), (129.216, 122.601)], "F"),
               (0.10, [(129.216, 122.601), (129.616, 123.001), (129.616, 123.526), (129.391, 123.751),
                       (129.241, 123.751), (128.096, 124.896), (128.096, 129.920)], "B"),
               (0.15, [(128.096, 129.920), (124.716, 133.300)], "B"),
               (0.15, [(124.716, 133.300), (124.716, 135.675)], "F")],
              [(129.216, 122.601), (124.716, 133.300)]),
    "ADDR2": ([(0.15, [(129.956, 122.251), (130.306, 122.251), (130.756, 122.701), (130.756, 123.151)], "F"),
               (0.15, [(130.756, 123.151), (130.756, 125.951), (130.206, 126.501), (130.206, 131.101),
                       (128.006, 133.301)], "B"),
               (0.15, [(128.006, 133.301), (128.006, 135.701)], "F")],
              [(130.756, 123.151), (128.006, 133.301)]),
    "USB_CC_OUT1": ([(0.15, [(128.906, 124.101), (128.906, 124.134), (128.806, 124.234), (128.806, 124.840)], "F"),
                     (0.15, [(128.806, 124.840), (129.831, 123.815)], "B"),
                     (0.10, [(129.831, 123.815), (129.831, 123.415), (130.556, 122.690), (132.106, 122.690)], "B"),
                     (0.15, [(132.106, 122.690), (134.106, 124.690), (134.106, 125.615)], "B"),
                     (0.15, [(134.106, 125.615), (133.681, 126.040), (133.250, 126.040)], "F"),
                     (0.15, [(134.106, 125.615), (134.456, 125.265), (134.876, 125.265)], "F")],
                    [(128.806, 124.840), (134.106, 125.615)]),
}


def south_east(m, log):
    """ADDR0-2 and USB_CC_OUT1, drawn (SOUTH_EAST)."""
    for net, (polylines, vias) in SOUTH_EAST.items():
        if not lay(m, log, net, polylines, vias, (0.5, 0.3)) or len(rr2.comps(m, net)) != 1:
            log("%s: the drawing does not fit" % net)
            return False
        its = st.net_copper(m, net)
        log("%s drawn: %.1f mm, %d via" % (net, st.jag_of(its)[0], sum(1 for it in its if it["kind"] == "via")))
    return True


def usb_cc(m, log):
    """The TUSB320's (U13's) other nets: USB_CC_OUT2, pin 8 to U9's pin 31 and R33; then
    USB_VBUS_DET, USB_CC2 and USB_CC1 from R34 and J5's CC pins. U13's CC pins face west,
    J5's are south-east of them, and CC2's has the USB-C pair between - it passes under
    the pair south of D5. Left to freerouting, CC1 and CC2, and then CC_OUT1 and
    CC_OUT2, ripped each other round after round in the rip-up loop (7 October). In this
    order; with CC1 or CC2 first, one of the others has no way."""
    for net in ("USB_CC_OUT2", "USB_VBUS_DET", "USB_CC2", "USB_CC1"):
        for w in (0.15, 0.1):
            ok, _ = rr3.connect(m, net, w, ("F", "B"), margin=3.0)
            its = st.net_copper(m, net)
            if ok:
                log("%s at %.2f mm: %.1f mm, %d via" % (net, w, st.jag_of(its)[0],
                                                       sum(1 for it in its if it["kind"] == "via")))
                break
            m.remove([it["uuid"] for it in its])
            m.index()
        else:
            log("%s: open" % net)
            return False
    return True


def debug_lines(m, log):
    """SWCLK, SWDIO and RUN: U9's pins 24 / 25 / 26 to J6 (and RUN's pull-up R20), the long
    nets of the corner south of U9. SWCLK first: with RUN or SWDIO first, past the
    crystal, one of the others had no way."""
    for net in ("SWCLK", "SWDIO", "RUN"):
        ok, _ = rr3.connect(m, net, 0.15, ("F", "B"), margin=3.0)
        its = st.net_copper(m, net)
        if not ok:
            log("%s: open" % net)
            return False
        log("%s: %.1f mm, %d via" % (net, st.jag_of(its)[0], sum(1 for it in its if it["kind"] == "via")))
    return True


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
    """VCORE from the regulator's two vias (vreg) to the DVDD pins and their capacitors."""
    body = qfn_interior(m)
    keep = {"F": body, "B": lx_zone(m, 0.2)}
    drawn = {it["uuid"] for it in st.net_copper(m, "VCORE")}
    for w in (0.2, 0.15, 0.1):
        ok, _ = rr3.connect(m, "VCORE", w, ("F", "B"), margin=1.5, keepout=keep)
        its = st.net_copper(m, "VCORE")
        if ok:
            log("VCORE at %.2f mm: %.1f mm, %d via, nothing on In2" % (w, st.jag_of(its)[0],
                                                                    sum(1 for it in its if it["kind"] == "via")))
            return True
        m.remove([it["uuid"] for it in its if it["uuid"] not in drawn])
        m.index()
    return False


def main(src, dst):
    lr.PRO_REF = os.path.join(lr.ROOT, "tmp", "rev4_ref.kicad_pro")
    log = lambda s: print(s, flush=True)
    m = lr.Model(src)
    if not vreg(m, log):
        sys.exit("preroute: the core regulator could not be drawn")
    if not switch_node(m, log, "SW_NODE", (0.3, 0.2)):
        sys.exit("preroute: SW_NODE could not be routed on F.Cu")
    if not buck_in(m, log):
        sys.exit("preroute: the buck's +5 V input could not be drawn")
    if not usb_d(m, log):
        sys.exit("preroute: USB_D_P / USB_D_N could not be drawn")
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
    if not crystal(m, log):
        sys.exit("preroute: the crystal could not be laid on F.Cu")
    if not south_east(m, log):
        sys.exit("preroute: ADDR0-2 / USB_CC_OUT1 could not be drawn")
    if not usb_cc(m, log):
        sys.exit("preroute: the TUSB320's nets could not be routed")
    if not usb_ilim(m, log):
        sys.exit("preroute: USB_ILIM could not be routed")
    if not debug_lines(m, log):
        sys.exit("preroute: SWCLK / SWDIO / RUN could not be routed")
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
