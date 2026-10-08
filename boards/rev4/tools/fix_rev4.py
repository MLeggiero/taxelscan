"""fix_rev4.py [in.kicad_pcb] [out.kicad_pcb] - the placement for VERIFICATION.md findings 1-3 and 10.

    python3 fix_rev4.py                      (KiCad's Python; in place on ../rev4.kicad_pcb)

Run after gen_rev4.py has written rev4.net; freeroute_rev4.py then routes the
result. Like eco_rev4.py it changes the board's parts and plane ties only, and
takes every signal track off (the router lays them again):

 1. The RP2354A's core regulator, as the RP2350 datasheet's Figure 26 and
    Raspberry Pi's RP2350A minimal design (RPI-RP2350A-MINIMAL R4) lay it out,
    at that design's offsets from pins 46-50: C44 (CIN) straddling VREG_VIN
    (49) and VREG_PGND (47); C19 (COUT) beside it; L1 above, dot on VCORE;
    VREG_LX running straight up between their pads (preroute_rev4.vreg); CIN's
    and COUT's grounds joined to pin 47 and taken to the plane at ONE point,
    two adjacent vias; the VREG_AVDD filter (R35, C43, and C16) east of the
    stack with CFILT's own ground via; VCORE down through two vias west of
    COUT, VREG_FB from COUT's pad. In1 is cut away under L1 and the LX track
    (Figure 27). R23 / R24, the USB series resistors, go north-west of pins
    51 / 52, where the minimal design has them, so USB_D leaves on F.Cu. Pins
    53 and 54 are tied in the ring and share C18; C17 decouples the filter's
    +3.3 V input.
 2. U12 becomes the TPS62162 (WSON-8, 20 V absolute maximum) in TI's layout:
    C27 across VIN / PGND at the pins, C22 under them, L2 beside SW, VOS from
    L2's output pad, C23 at the output; AGND, PGND, FB and C22's ground meet
    at the exposed pad, which has two vias. R18 / R19 are gone.
 3. J6 re-pinned to Raspberry Pi's debug order (SC, GND, SD, RUN) with a silk
    legend; a "1" beside pin 1 of J3 and J4 for the harness cable.
 4. For the re-route: R30 / R31, U14's EN pull-down and FAULT pull-up, under
    its pins 3 and 4. West of U9, where rev-3 left them, USB_BUS_EN and
    USB_PWR_FAULT crossed under the MCU to reach them (38-52 mm, 4-6 vias),
    and the rip-up loop could not fit both past U9's east side.

GND and +3.3 V copper is drawn here, because freeroute_rev4.py's prep keeps
the planes' ties; VREG_LX, VCORE, VREG_AVDD, USB_D and the buck's +5 V input
are drawn by preroute_rev4.py, which runs after the strip.
"""
import collections
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lr
import pcbnew as K
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
REV4 = os.path.join(HERE, "..")
NET = os.path.join(REV4, "rev4.net")
PLANE = ("GND", "+3.3V")
FPDIRS = [os.environ.get("KICAD10_FOOTPRINT_DIR", ""), "/usr/share/kicad/footprints",
          "C:/Program Files/KiCad/10.0/share/kicad/footprints", os.path.join(lr.ROOT, "libraries")]

SWAP = {"C19": "FlexiTac:C_0402_1005Metric_WideGap", "C44": "FlexiTac:C_0402_1005Metric_WideGap",
        "C43": "Capacitor_SMD:C_0402_1005Metric",
        "U12": "Package_SON:Texas_DSG0008A_WSON-8-1EP_2x2mm_P0.5mm_EP0.9x1.6mm"}
GONE = ("R18", "R19")

# Pin 48 (VREG_LX) is the origin of the regulator corner: the minimal design's
# parts sit at these offsets from its U1.48 (102.0, 96.55).
P48 = (128.506, 117.201)


def at(dx, dy):
    return (round(P48[0] + dx, 3), round(P48[1] + dy, 3))


#            ref    position          rotation
PLACE = {
    "C44": (at(0.0, -1.16), 0),       # CIN, the minimal design's C6: +3.3V pad on 49, GND pad on 47
    "C19": (at(0.0, -2.10), 0),       # COUT, its C7
    "L1": (at(0.0, -3.75), 0),        # its L1, pad 1 (the dot) on VCORE to the west
    "C43": (at(2.2, -1.621), 270),    # CFILT, its C9: VREG_AVDD north, GND south
    "R35": (at(2.2, -3.481), 270),    # its R3: +3.3V north
    "C16": (at(3.094, -1.621), 270),  # 100 nF on VREG_AVDD beside C43
    "C17": (at(3.444, -3.991), 0),    # +3.3V side of the filter
    "R24": (at(-2.1, -3.951), 90),    # USB_D_N series, its R8; MCU side south
    "R23": (at(-3.1, -3.951), 90),    # USB_D_P series, its R7
    "C18": (at(-3.406, -1.101), 180),  # pins 53 / 54
    # the buck: TPS62162 with TI's layout (datasheet Figure 42) turned to fit
    "U12": ((106.4, 120.75), 0),
    "C27": ((104.42, 120.30), 90),     # 100 nF across VIN / PGND at the pins
    "C22": ((105.6, 122.85), 0),       # 4.7 uF under them, ground into the exposed pad
    "L2": ((109.6, 121.30), -90),      # SW pad north, beside pin 7
    "C23": ((113.0, 122.55), 0),       # output, +3.3V pad beside L2's output pad
    # U14's EN pull-down and FAULT pull-up, under its pins 3 and 4: west of U9,
    # where rev-3 left them, both nets crossed under the MCU to reach them
    "R30": ((143.0, 129.95), 180),     # USB_BUS_EN pad east, under pin 3
    "R31": ((145.7, 130.15), 180),     # USB_PWR_FAULT pad east, under pin 4
}
REF_AT = {"U14": (144.1, 131.25)}      # its reference was where R30 now is

# GND / +3.3V copper: (net, width, [points]) polylines, and vias (net, x, y, size, drill)
COPPER = [
    # pin 47 to CIN's ground, straight up; on diagonally to the second ground via
    ("GND", 0.2, [at(0.4, 0.0), at(0.4, -1.001)]),
    ("GND", 0.3, [at(0.4, -0.749), at(1.1, -1.449)]),
    # CIN's and COUT's grounds joined, and to the two adjacent vias: the ONE point
    ("GND", 0.4, [at(0.565, -1.16), at(0.565, -2.10)]),
    ("GND", 0.4, [at(0.565, -2.05), at(1.1, -2.05)]),
    ("GND", 0.4, [at(0.565, -1.45), at(1.1, -1.45)]),
    # CFILT's own ground via, as the minimal design's C9
    ("GND", 0.4, [at(2.2, -1.141), at(2.2, -0.75), at(1.85, -0.40)]),
    ("GND", 0.2, [at(3.094, -1.281), at(2.2, -1.281)]),       # C16 beside it
    # pin 49 to CIN's +3.3V pad, and its +3.3V via inward in the ring
    ("+3.3V", 0.2, [at(-0.4, 0.0), at(-0.4, -1.001)]),
    ("+3.3V", 0.2, [at(-0.4, 0.0), at(-0.4, 1.075)]),
    # pins 53 and 54 tied in the ring, a via between them; 54 on to C18
    ("+3.3V", 0.2, [at(-2.0, 0.0), at(-2.0, 0.749), at(-2.4, 0.749), at(-2.4, 0.0)]),
    ("+3.3V", 0.2, [at(-2.2, 0.749), at(-2.2, 1.249)]),
    ("+3.3V", 0.2, [at(-2.4, 0.0), at(-2.4, -0.601), at(-2.9, -1.101)]),
    ("GND", 0.2, [at(-3.726, -1.101), at(-3.726, -1.851)]),     # C18's ground via
    # R35's +3.3V pad to C17 and a via; C17's ground via
    ("+3.3V", 0.2, [at(2.2, -3.991), at(3.124, -3.991)]),
    ("+3.3V", 0.2, [at(2.944, -3.991), at(2.944, -3.251)]),
    ("GND", 0.2, [at(3.764, -3.991), at(3.764, -3.251)]),
    # the buck: PGND, AGND and FB into the exposed pad, C27 and C22 to it
    ("GND", 0.25, [(105.45, 120.00), (106.2, 120.00)]),
    ("GND", 0.25, [(105.45, 121.50), (106.2, 121.50)]),
    ("GND", 0.25, [(107.35, 121.50), (106.6, 121.50)]),
    ("GND", 0.2, [(105.45, 120.00), (104.42, 120.00)]),
    ("GND", 0.3, [(106.375, 122.85), (106.375, 121.30)]),
    # +3.3V: L2's output pad to C23, and VOS from it
    ("+3.3V", 0.6, [(109.6, 122.55), (112.05, 122.55)]),
    ("+3.3V", 0.15, [(107.35, 121.00), (107.80, 121.00), (109.0, 122.20)]),
    ("+3.3V", 0.4, [(111.24, 122.55), (111.24, 123.25)]),        # the output's two plane vias
    ("GND", 0.5, [(113.95, 122.55), (114.75, 122.55), (114.75, 123.65)]),   # C23's ground, two vias
    # R30's ground and R31's +3.3 V, each straight down to a via
    ("GND", 0.2, [(142.49, 129.95), (142.49, 130.65)]),
    ("+3.3V", 0.2, [(145.19, 130.15), (145.19, 130.85)]),
]
VIAS = [
    ("GND",) + at(1.1, -2.05) + (0.6, 0.3), ("GND",) + at(1.1, -1.45) + (0.6, 0.3),
    ("GND",) + at(1.85, -0.40) + (0.6, 0.3),
    ("+3.3V",) + at(-0.4, 1.075) + (0.5, 0.3),
    ("+3.3V",) + at(-2.2, 1.249) + (0.5, 0.3),
    ("GND",) + at(-3.726, -1.851) + (0.5, 0.3),
    ("+3.3V",) + at(2.944, -3.251) + (0.5, 0.3),
    ("GND",) + at(3.764, -3.251) + (0.5, 0.3),
    ("GND", 106.4, 120.35, 0.5, 0.3), ("GND", 106.4, 121.15, 0.5, 0.3),    # in U12's exposed pad
    ("+3.3V", 111.24, 122.55, 0.5, 0.3), ("+3.3V", 111.24, 123.25, 0.5, 0.3),
    ("GND", 114.75, 122.95, 0.5, 0.3), ("GND", 114.75, 123.65, 0.5, 0.3),
    ("GND", 142.49, 130.65, 0.5, 0.3), ("+3.3V", 145.19, 130.85, 0.5, 0.3),
]


def load_net(path=NET):
    text = open(path, encoding="utf-8").read()
    out = {}
    for name, body in re.findall(r'\(net \(code "\d+"\) \(name "([^"]*)"\)(.*?)\n    \)', text, re.S):
        for ref, pin in re.findall(r'\(ref "([^"]*)"\) \(pin "([^"]*)"\)', body):
            out[(ref, pin)] = name
    return out


def fp_load(fpid):
    lib, name = fpid.split(":", 1)
    for d in FPDIRS:
        p = os.path.join(d, lib + ".pretty")
        if d and os.path.exists(os.path.join(p, name + ".kicad_mod")):
            return K.FootprintLoad(p, name)
    sys.exit("footprint %s not found" % fpid)


def swap(b, ref, fpid):
    old = b.FindFootprintByReference(ref)
    new = fp_load(fpid)
    new.SetFPID(K.LIB_ID(*fpid.split(":", 1)))
    new.SetReference(ref)
    new.SetValue(old.GetValue())
    new.SetPath(old.GetPath())
    new.SetAttributes(old.GetAttributes())
    new.SetPosition(old.GetPosition())
    new.SetOrientationDegrees(old.GetOrientationDegrees())
    of, nf = old.Reference(), new.Reference()
    nf.SetVisible(of.IsVisible())
    nf.SetLayer(of.GetLayer())
    nf.SetTextSize(of.GetTextSize())
    nf.SetTextThickness(of.GetTextThickness())
    b.Delete(old)
    b.Add(new)
    return new


def track_touches(t, geoms):
    """Does track / via t touch any of the shapely pad shapes in geoms (same copper)?"""
    if t.Type() == K.PCB_VIA_T:
        g = Point(lr.mm(t.GetPosition().x), lr.mm(t.GetPosition().y)).buffer(lr.mm(t.GetWidth(K.F_Cu)) / 2)
    else:
        a, c = t.GetStart(), t.GetEnd()
        g = lr.seg_geom((lr.mm(a.x), lr.mm(a.y)), (lr.mm(c.x), lr.mm(c.y)), lr.mm(t.GetWidth()))
    return any(g.intersects(p) for p in geoms)


def pad_geoms(b, refs, pins=None):
    out = []
    for f in b.GetFootprints():
        if f.GetReference() not in refs:
            continue
        for p in f.Pads():
            if pins and f.GetReference() in pins and p.GetNumber() not in pins[f.GetReference()]:
                continue
            if p.GetNetname() in PLANE and p.IsOnLayer(K.F_Cu):
                out.append(lr.polyset(p.GetEffectivePolygon(K.F_Cu, K.ERROR_OUTSIDE)))
    return out


def groups(items):
    """Plane copper joined by contact: tracks on a shared layer, vias with anything on F / B."""
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            la = a["lay"] if a["kind"] != "via" else {"F", "B"}
            lb = b["lay"] if b["kind"] != "via" else {"F", "B"}
            if (la & lb) and a["geom"].intersects(b["geom"]):
                parent[find(i)] = find(j)
    out = collections.defaultdict(list)
    for i, it in enumerate(items):
        out[find(i)].append(it)
    return list(out.values())


def prune_orphans(m, log):
    """Plane copper in a group that reaches no pad goes; dangling track ends go."""
    total = 0
    while True:
        m.index()
        gone = []
        for net in PLANE:
            items = [it for it in m.items if it["net"] == net and it["kind"] in ("track", "via")]
            pads = [it for it in m.items if it["net"] == net and it["kind"] == "pad" and it["geom"] is not None]
            for g in groups(items + pads):
                if not any(it["kind"] == "pad" for it in g):
                    gone += [it["uuid"] for it in g]
            for it in items:
                lay = it["lay"]
                g = it["geom"]
                if it["kind"] == "track":
                    # each end must touch another item of the net on this layer
                    for end in (it["a"], it["c"]):
                        e = Point(end).buffer(it["w"] / 2 * 0.9)
                        if not any(o is not it and (o["lay"] & lay) and o["geom"] is not None and e.intersects(o["geom"])
                                   for o in items + pads):
                            gone.append(it["uuid"])
                            break
                else:
                    touch = [o for o in items + pads if o is not it and o["geom"] is not None and
                             (o["lay"] & {"F", "B"}) and g.intersects(o["geom"])]
                    if not touch:
                        gone.append(it["uuid"])
        if not gone:
            break
        total += m.remove(gone)
    log("  pruned %d orphaned / dangling plane item(s)" % total)


def draw(m, net, w, pts, layer="F"):
    segs = [(layer, a, c) for a, c in zip(pts, pts[1:])]
    m.add(net, segs, [], w)


def main(src, dst):
    lr.PRO_REF = os.path.join(lr.ROOT, "tmp", "rev4_ref.kicad_pro")
    log = lambda s: print(s, flush=True)
    want = load_net()
    m = lr.Model(src)
    b = m.b

    # 1. every signal track off; the plane ties of everything that moves off
    n = collections.Counter()
    for t in list(b.GetTracks()):
        if t.GetNetname() not in PLANE:
            b.Delete(t)
            n["signal"] += 1
    movers = set(PLACE) | set(SWAP) | set(GONE)
    geoms = pad_geoms(b, movers | {"U9"}, pins={"U9": {"47", "49", "53", "54"}})
    for t in list(b.GetTracks()):
        if track_touches(t, geoms):
            b.Delete(t)
            n["plane tie"] += 1
    log("took off %d signal item(s), %d plane tie item(s) of the parts that move" % (n["signal"], n["plane tie"]))
    m.index()
    prune_orphans(m, log)

    # 2. parts: swap footprints, delete the divider, re-pin to rev4.net, place
    for ref, fpid in SWAP.items():
        swap(b, ref, fpid)
        log("  %s -> %s" % (ref, fpid))
    for ref in GONE:
        b.Delete(b.FindFootprintByReference(ref))
        log("  %s deleted" % ref)
    for ref, val in (("U12", "TPS62162DSGR"), ("R38", "0.68R"), ("C46", "22uF")):
        b.FindFootprintByReference(ref).SetValue(val)
    changed = 0
    for f in b.GetFootprints():
        ref = f.GetReference()
        if ref.startswith(("FID", "H", "#")):
            continue
        for p in f.Pads():
            if not p.GetNumber():
                continue
            new = want.get((ref, p.GetNumber()), "")
            if p.GetNetname() != new:
                if new:
                    ni = b.FindNet(new)
                    if ni is None:
                        ni = K.NETINFO_ITEM(b, new)
                        b.Add(ni)
                    p.SetNet(ni)
                else:
                    p.SetNetCode(0)
                changed += 1
    log("  %d pad net(s) changed to rev4.net" % changed)
    for ref, ((x, y), rot) in PLACE.items():
        f = b.FindFootprintByReference(ref)
        f.SetPosition(lr.V(x, y))
        f.SetOrientationDegrees(rot)
    for ref, (x, y) in REF_AT.items():
        b.FindFootprintByReference(ref).Reference().SetPosition(lr.V(x, y))
    m.index()

    # 3. the drawn copper; then whatever old plane copper it or the new
    #    placement collides with goes
    before = {t.m_Uuid.AsString() for t in b.GetTracks()}
    for net, w, pts in COPPER:
        draw(m, net, w, pts)
    for net, x, y, s, d in VIAS:
        m.add(net, [], [(x, y)], 0.2, (s, d))
    m.index()
    gone = []
    for it in m.items:
        if it["uuid"] not in before:
            continue
        if it["kind"] == "track" and it["net"] in PLANE:
            if m.check_track(it["net"], next(iter(it["lay"])), it["a"], it["c"], it["w"]):
                gone.append(it["uuid"])
        elif it["kind"] == "via" and it["net"] in PLANE:
            if m.check_via(it["net"], it["xy"][0], it["xy"][1], it["size"], it["drill"]):
                gone.append(it["uuid"])
    log("took off %d plane item(s) the new placement collides with" % m.remove(gone))
    prune_orphans(m, log)

    m.index()

    # 5. the In1 cut-out under L1 and the LX track, as Figure 27
    l1 = b.FindFootprintByReference("L1")
    cb = l1.GetCourtyard(K.F_CrtYd).BBox()
    body = box(lr.mm(cb.GetLeft()), lr.mm(cb.GetTop()), lr.mm(cb.GetRight()), lr.mm(cb.GetBottom()))
    lx = LineString([at(0.0, 0.55), at(0.0, -2.501), at(0.544, -3.045)]).buffer(0.30, join_style=2, cap_style=2)
    cut = unary_union([body, lx]).simplify(0.01)
    for z in b.Zones():
        if z.GetIsRuleArea() and z.GetZoneName() == "VREG_LX_cutout":
            ol = z.Outline()
            ol.RemoveAllContours()
            ol.NewOutline()
            for x, y in list(cut.exterior.coords)[:-1]:
                ol.Append(K.FromMM(round(x, 3)), K.FromMM(round(y, 3)))
            log("VREG_LX_cutout: %d corners, %.1f mm2" % (len(cut.exterior.coords) - 1, cut.area))

    # 6. silk: pin 1 of the harness connectors, and the debug header's legend
    def text(s, x, y, size=0.8, thick=0.12, angle=0):
        t = K.PCB_TEXT(b)
        t.SetText(s)
        t.SetPosition(lr.V(x, y))
        t.SetLayer(K.F_SilkS)
        t.SetTextSize(K.VECTOR2I(K.FromMM(size), K.FromMM(size)))
        t.SetTextThickness(K.FromMM(thick))
        t.SetTextAngleDegrees(angle)
        b.Add(t)
    # beside the footprints' own pin-1 tick, on the side away from pin 2
    for ref, dx, dy in (("J4", 0.56, -1.3), ("J3", -0.54, 1.3)):
        p1 = [p for p in b.FindFootprintByReference(ref).Pads() if p.GetNumber() == "1"][0].GetPosition()
        text("1", lr.mm(p1.x) + dx, lr.mm(p1.y) + dy)
    # between R12 and the header's outline: the 1 mm strip there is all there is
    j6 = {p.GetNumber(): p.GetPosition() for p in b.FindFootprintByReference("J6").Pads()}
    for num, s in (("1", "SC"), ("2", "GND"), ("3", "SD"), ("4", "RUN")):
        text(s, lr.mm(j6[num].x), lr.mm(j6[num].y) - 1.98)

    # 7. check: every pad matches rev4.net, the drawn copper is clear
    bad = []
    for f in b.GetFootprints():
        ref = f.GetReference()
        if ref.startswith(("FID", "H")):
            continue
        for p in f.Pads():
            if p.GetNumber() and p.GetNetname() != want.get((ref, p.GetNumber()), ""):
                bad.append("%s.%s is %r, rev4.net %r" % (ref, p.GetNumber(), p.GetNetname(), want.get((ref, p.GetNumber()), "")))
    for it in m.items:
        if it["kind"] == "track":
            for x in m.check_track(it["net"], next(iter(it["lay"])), it["a"], it["c"], it["w"]):
                bad.append("%s track %s-%s: %s" % (it["net"], it["a"], it["c"], x[:4]))
        elif it["kind"] == "via":
            for x in m.check_via(it["net"], it["xy"][0], it["xy"][1], it["size"], it["drill"]):
                bad.append("%s via %s: %s" % (it["net"], it["xy"], x[:4]))
    keep = {}
    for ext in (".kicad_pro", ".kicad_dru"):
        p = os.path.splitext(src)[0] + ext
        if os.path.exists(p):
            keep[os.path.splitext(dst)[0] + ext] = open(p, "rb").read()
    m.save(dst)
    for p, data in keep.items():
        open(p, "wb").write(data)
    for x in bad[:40]:
        log("  " + x)
    log("%s: %d problem(s)" % (os.path.basename(dst), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    a = sys.argv[1:] or [lr.PCB]
    sys.exit(main(a[0], a[1] if len(a) > 1 else a[0]))
