#!/usr/bin/env python3
"""Add the stitching vias the router never placed.

    ./stitch_gnd.py            says what it would do
    ./stitch_gnd.py --apply    does it, after writing rev3.kicad_pcb.bak

WHY THIS IS NEEDED. This board is single-sided assembly: every GND pad is SMD,
on F.Cu only. A pad like that can only reach the buried In1.Cu ground plane
through a via - there is no other physical path. freerouting was told In1.Cu
was off limits to route ON (correctly, since it is the plane), but it placed
ZERO vias down to it for the GND net anyway: it wired GND pads to each other
with ordinary F.Cu/B.Cu tracks, and the result is up to 55 separate copper
islands, most of them a single pad, none of them touching the plane.

The fix is not "one via per pad" - many pads are already tied to each other by
a track, so the fix is one via per ISLAND: a connected group of GND copper that
currently touches nothing else. Tie any ONE point in an island to the plane and
the whole island joins it, because the plane is one contiguous pour.

An island is skipped if it already contains a through-hole pad (J1/J2/J3's
mounting pin, or J5's shield tab, or J6.3) - those pads carry copper on every
layer already, so they touch the plane on their own.

PLACEMENT. For each island, every member pad is tried as an anchor and a ring
of candidate points around it is tested - clear of every footprint courtyard,
every existing via, and every track on any layer, with the board's own
clearance rule (0.127 mm) added as margin. The first legal point wins. A short
F.Cu stub (or the anchor pad's own layer) joins the pad to the via when they
are not coincident.

Vias are 0.6 mm / 0.3 mm drill, matching the vias freerouting already placed
for +3.3V - this board's rules require at least 0.56 mm OD, so it is also the
minimum standard size.

Nothing already on the board is touched. Anything this script cannot place
safely is reported and left for a person, not guessed at.
"""
import io
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid as _uuid

import gen_pcb as P
import gen_schematic as S
import sync_pcb as SY

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")

# Stays 0.6/0.3. Shrinking to 0.45/0.25 to buy 0.075 mm of keepout radius was
# tried and is wrong twice over: the board's own setup asks for min hole 0.3
# and min via diameter 0.5, so every one of the 131 vias failed DRC, and the
# DSN padstack freerouting places its own vias from is Via[0-3]_600:300_um,
# so the board would carry two via sizes for no reason. 0.5/0.3 is legal but
# lands exactly on the min_via_annular_width of 0.1 mm with no margin.
#
# Tightness comes from the search in candidates() instead, which costs
# nothing and cannot violate a rule.
VIA_SIZE = 0.6
VIA_DRILL = 0.3
# The board's own rule block asks for 0.127 mm, but real DRC still flagged one
# via 0.131 mm from a track - 0.019 mm short of an effective 0.15 mm minimum
# that applies somewhere between board rules and net-class defaults. Rather
# than chase which one wins, this margin sits comfortably above either.
CLEARANCE = 0.2
TRACK_W = 0.15


def key(x, y):
    return (round(x, 3), round(y, 3))


def load():
    text = io.open(BOARD, encoding="utf-8").read()
    fps = SY.footprints(text)
    netmap = S.load_net()
    bom = S.load_bom()
    return text, fps, netmap, bom


def pad_positions(text, fps, net):
    """{(ref, pin): (x, y, all_layers)} for every pad on `net`."""
    netmap = S.load_net()
    out = {}
    for st, end, ref, lib, x, y, rot in fps:
        blk = text[st:end]
        ang = math.radians(rot)
        ca, sa = math.cos(ang), math.sin(ang)
        for pm in re.finditer(r'\(pad "([^"]*)"', blk):
            pin = pm.group(1)
            if netmap.get(ref, {}).get(pin) != net:
                continue
            pblk = blk[pm.start():P.close_of(blk, pm.start())]
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", pblk)
            lay = re.search(r'\(layers?\s+((?:"[^"]+"\s*)+)\)', pblk)
            if not at:
                continue
            px, py = float(at.group(1)), float(at.group(2))
            out[(ref, pin)] = (x + px * ca + py * sa, y - px * sa + py * ca,
                               bool(lay and "*.Cu" in lay.group(1)))
    return out


def net_islands(text, fps, net):
    """Groups of `net` copper not yet touching the plane.

    A group already touches the plane if it contains EITHER a through-hole
    pad (padstack spans every copper layer) OR an existing via on `net` -
    including one this script placed on an earlier run. Missing the via case
    was a real bug: re-running against an already-partly-stitched board
    reported all the same islands as still needing a via, because a via's own
    position was a graph node (segments could terminate there) but nothing
    ever marked that node as plane-connected. The result was this script
    trying to stitch pads that were already fixed, and simply failing more
    often because its own earlier vias were now in the way.
    """
    pads = pad_positions(text, fps, net)
    parent, touches = {}, set()

    def find(a):
        while parent.get(a, a) != a:
            parent[a] = parent.get(parent[a], parent[a])
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for m in re.finditer(r"\n\t\(segment\b", text):
        st = m.start() + 1
        blk = text[st:P.close_of(text, st)]
        segnet = re.search(r'\(net "([^"]*)"\)', blk)
        if not segnet or segnet.group(1) != net:
            continue
        s = re.search(r"\(start ([-\d.]+) ([-\d.]+)\)", blk)
        e = re.search(r"\(end ([-\d.]+) ([-\d.]+)\)", blk)
        a, b = key(float(s.group(1)), float(s.group(2))), \
            key(float(e.group(1)), float(e.group(2)))
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        union(a, b)
    for m in re.finditer(r"\n\t\(via\b", text):
        st = m.start() + 1
        blk = text[st:P.close_of(text, st)]
        vnet = re.search(r'\(net "([^"]*)"\)', blk)
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+)\)", blk)
        if vnet and vnet.group(1) == net and at:
            k = key(float(at.group(1)), float(at.group(2)))
            parent.setdefault(k, k)
            touches.add(k)
    for (ref, pin), (x, y, _plane) in pads.items():
        parent.setdefault(key(x, y), key(x, y))

    groups = {}
    for (ref, pin), (x, y, plane) in pads.items():
        groups.setdefault(find(key(x, y)), []).append((ref, pin, x, y, plane))

    plane_roots = {find(k) for k in touches}
    return [members for root, members in groups.items()
            if root not in plane_roots and not any(m[4] for m in members)]


_DRC_CACHE = {}


def drc_text(path):
    """kicad-cli's DRC report for `path`, re-run whenever the board changes."""
    stamp = (os.path.abspath(path), os.path.getmtime(path))
    if stamp in _DRC_CACHE:
        return _DRC_CACHE[stamp]
    cli = S.find_cli()
    tmp = tempfile.mkdtemp(prefix="rev3s")
    rpt = os.path.join(tmp, "drc.rpt")
    subprocess.run([cli, "pcb", "drc", "--output", rpt, "--severity-all",
                    "--refill-zones", path], capture_output=True, text=True)
    text = io.open(rpt, encoding="utf-8").read() if os.path.exists(rpt) else ""
    shutil.rmtree(tmp, ignore_errors=True)
    _DRC_CACHE[stamp] = text
    return text


def drc_pairs(path):
    """[(net, x1, y1, ref1, pin1, x2, y2, ref2, pin2)] - every gap KiCad names.

    THIS IS THE ONLY SOURCE OF TRUTH for what is still unconnected. Both of
    the alternatives that came before it were wrong in the same direction.

    `net_islands()` decides an island by unioning segment ENDPOINTS, so a pad
    joins the graph only when some segment ends exactly on the pad's CENTRE.
    KiCad's connectivity is shape-based - a track landing anywhere on the pad
    counts - and the two disagreed badly here: 28 islands claimed against 16
    pairs KiCad actually reports, every one of the extra 12 a pad already
    connected by a track that stopped short of its centre. Stitching those
    wastes vias and fills the tight spots the genuinely-unconnected pads need.

    `bridge_nets.PAIRS` was the same list transcribed by hand from one DRC run
    and then left behind by every edit since. Reading it live costs one DRC
    pass, which is cached on the board's mtime, and cannot go stale.

    A pad item names its ref/pin; a track or via item is reported at its own
    coordinates and anchored there, which is equally valid - any point on an
    island will do.
    """
    NL = chr(10)
    pat = ("^" + re.escape("[unconnected_items]") + "[^" + NL + "]*" + NL
           + "[^" + NL + "]*" + NL
           + "((?:\s*@[^" + NL + "]*" + NL + ")+)")
    out = []
    for m in re.finditer(pat, drc_text(path), re.M):
        items = []
        for x, y, desc in re.findall(
                r"@\((-?[\d.]+) mm, (-?[\d.]+) mm\): (.*)", m.group(1)):
            nm = re.search(r"\[([^]]*)\]", desc)
            pm = re.search(r"Pad (\S+) \[[^]]*\] of (\S+)", desc)
            items.append((nm.group(1) if nm else "", float(x), float(y),
                          pm.group(2) if pm else None,
                          pm.group(1) if pm else None))
        if len(items) == 2:
            out.append((items[0][0],) + items[0][1:] + items[1][1:])
    return out


def drc_unconnected(path, net):
    """[(x, y, ref, pin)] - distinct unconnected anchors on `net`."""
    out, seen = [], set()
    for n, x1, y1, r1, p1, x2, y2, r2, p2 in drc_pairs(path):
        if n != net:
            continue
        for x, y, r, p in ((x1, y1, r1, p1), (x2, y2, r2, p2)):
            k = (round(x, 3), round(y, 3))
            if k not in seen:
                seen.add(k)
                out.append((x, y, r, p))
    return out


def group_anchors(text, anchors):
    """One anchor per island, so two pads already tied together share a via."""
    parent = {}

    def find(a):
        while parent.get(a, a) != a:
            parent[a] = parent.get(parent[a], parent[a])
            a = parent[a]
        return a

    for m in re.finditer(r"\n\t\(segment\b", text):
        st = m.start() + 1
        blk = text[st:P.close_of(text, st)]
        s = re.search(r"\(start ([-\d.]+) ([-\d.]+)\)", blk)
        e = re.search(r"\(end ([-\d.]+) ([-\d.]+)\)", blk)
        a, b = key(float(s.group(1)), float(s.group(2))),             key(float(e.group(1)), float(e.group(2)))
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    groups = {}
    for x, y, ref, pin in anchors:
        k = key(x, y)
        parent.setdefault(k, k)
        groups.setdefault(find(k), []).append((ref or "track", pin or "-",
                                               x, y, False))
    return list(groups.values())


def all_pads(text, fps, netmap):
    """[(ref, pin, x, y, w, h, ang, net)] - every pad, absolute position and
    absolute rotation, as the rectangle it actually is.

    This used to hand back a circumscribed-circle radius instead, on the
    argument that a circle is conservative and avoids rotated-rect maths. It
    is conservative to the point of uselessness on this board: a TSSOP pad is
    1.5 x 0.4 mm, whose circumscribed circle is 1.55 mm across - four times
    the pad's real width - so on a 0.65 mm pitch part every candidate beside
    a pin reads as "on top of the neighbouring pin". That single approximation
    is what rejected 243 of 624 candidates for U2.8 and left 23 GND islands
    unstitchable. Real geometry costs twenty lines and unblocks them.

    The net comes along because `legal()` has to tell a neighbour apart from a
    relative: a via or stub that brushes a pad on the SAME net shorts nothing,
    it just joins two bits of the same island.
    """
    out = []
    for st, end, ref, lib, x, y, rot in fps:
        blk = text[st:end]
        ang = math.radians(rot)
        ca, sa = math.cos(ang), math.sin(ang)
        for pm in re.finditer(r'\(pad "([^"]*)"', blk):
            pblk = blk[pm.start():P.close_of(blk, pm.start())]
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?", pblk)
            sz = re.search(r"\(size ([\d.]+) ([\d.]+)\)", pblk)
            if not at or not sz:
                continue
            px, py = float(at.group(1)), float(at.group(2))
            w, h = float(sz.group(1)), float(sz.group(2))
            # A pad's own (at ... rot) is absolute in KiCad, not relative to
            # the footprint - the same trap that once left 104 pad shapes
            # unrotated on this board. When the pad states no angle it takes
            # the footprint's.
            pang = float(at.group(3)) if at.group(3) is not None else rot
            out.append((ref, pm.group(1), x + px * ca + py * sa,
                        y - px * sa + py * ca, w, h, pang,
                        netmap.get(ref, {}).get(pm.group(1), "")))
    return out


def point_rect_dist(px, py, cx, cy, w, h, ang):
    """Distance from a point to a rotated rectangle; 0 when inside."""
    a = math.radians(ang)
    ca, sa = math.cos(a), math.sin(a)
    dx, dy = px - cx, py - cy
    lx = dx * ca - dy * sa
    ly = dx * sa + dy * ca
    ox = max(abs(lx) - w / 2.0, 0.0)
    oy = max(abs(ly) - h / 2.0, 0.0)
    return math.hypot(ox, oy)


def seg_rect_dist(x1, y1, x2, y2, cx, cy, w, h, ang):
    """Distance from a segment to a rotated rectangle; 0 when they touch.

    Exact for two convex shapes: either an endpoint is nearest, or a corner
    is - so the minimum over both families is the answer, and it is 0 when a
    corner lies on the segment or an endpoint lies inside the rectangle.
    """
    d = min(point_rect_dist(x1, y1, cx, cy, w, h, ang),
            point_rect_dist(x2, y2, cx, cy, w, h, ang))
    if d == 0.0:
        return 0.0
    a = math.radians(ang)
    ca, sa = math.cos(a), math.sin(a)
    for sx in (-0.5, 0.5):
        for sy in (-0.5, 0.5):
            ux, uy = sx * w, sy * h
            qx = cx + ux * ca + uy * sa
            qy = cy - ux * sa + uy * ca
            d = min(d, point_seg_dist(qx, qy, x1, y1, x2, y2))
    return d


def obstacles(text, fps, bom, ex, stitch_net):
    """Pad copper, via centres, and track segments - for clearance tests.

    Courtyard boxes are NOT used here. A courtyard is a mechanical keep-out for
    silkscreen and neighbouring parts, not an electrical rule, and using it as
    the exclusion zone rejected every candidate near a component's own body -
    including right beside its own pad, which is exactly where a stitching via
    belongs. Real pad geometry is the thing a via actually has to clear.
    """
    # GND tracks are deliberately excluded from `segs`. They are what the
    # stub is JOINING - the whole reason a candidate near the anchor pad
    # exists is that it is already GND copper - so treating them as something
    # to avoid crossing means a stub can never leave the pad it starts from
    # in the very first sample. Same-net copper touching same-net copper is
    # never a short; it is the plane doing its job. Vias keep every net,
    # because two holes cannot overlap regardless of net.
    vias = []
    for m in re.finditer(r"\n\t\(via\b", text):
        st = m.start() + 1
        blk = text[st:P.close_of(text, st)]
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+)\)", blk)
        sz = re.search(r"\(size ([\d.]+)\)", blk)
        if at:
            vias.append((float(at.group(1)), float(at.group(2)),
                        float(sz.group(1)) if sz else VIA_SIZE))
    segs = []
    for m in re.finditer(r"\n\t\(segment\b", text):
        st = m.start() + 1
        blk = text[st:P.close_of(text, st)]
        segnet = re.search(r'\(net "([^"]*)"\)', blk)
        if segnet and segnet.group(1) == stitch_net:
            continue
        s = re.search(r"\(start ([-\d.]+) ([-\d.]+)\)", blk)
        e = re.search(r"\(end ([-\d.]+) ([-\d.]+)\)", blk)
        w = re.search(r"\(width ([\d.]+)\)", blk)
        if s and e:
            segs.append((float(s.group(1)), float(s.group(2)),
                        float(e.group(1)), float(e.group(2)),
                        float(w.group(1)) if w else TRACK_W))
    return vias, segs


def point_seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def legal(x, y, ax, ay, ex, pads, vias, segs, placed_vias, skip_pad, net):
    """Is a via at (x,y), AND the straight stub from the anchor pad (ax,ay) to
    it, clear of everything on another net?

    Checking only the via's own endpoint is not enough: a stub of a few mm can
    cross an unrelated net's track partway along its length while both of its
    own endpoints are individually clear. The first version of this script
    proved that the hard way - it applied cleanly by its own point-only check,
    and real DRC then found 15 shorting_items, every one of them a GND stub
    3-10 mm long crossing someone else's copper.

    `segs`/`pads`/`vias` already exclude GND (obstacles() strips GND tracks;
    GND pads and vias are excluded per-call via skip_pad and are otherwise
    fine to be near, since same-net contact is never a short) - what is left
    here is exactly "everything that is NOT this stub's own net", which is
    the right thing to avoid touching.
    """
    r = VIA_SIZE / 2.0 + CLEARANCE
    if not (ex[0] + r <= x <= ex[2] - r and ex[1] + r <= y <= ex[3] - r):
        return False
    tw = TRACK_W / 2.0 + CLEARANCE
    for ref, pin, px, py, pw, ph, pang, pnet in pads:
        if (ref, pin) == skip_pad:
            continue
        if pnet == net:
            # Same net. The via must still not land ON the pad - via-in-pad is
            # an assembly problem, not an electrical one, and JLCPCB charges
            # for filled-and-capped - but it needs no clearance beyond that,
            # and the stub is free to run straight across the pad, because
            # arriving there is the whole point. Counting these as obstacles
            # is what made every one of the 28 remaining GND islands report
            # "no legal spot": in a plane-stitched design a GND pad's nearest
            # neighbour is nearly always another GND pad.
            if point_rect_dist(x, y, px, py, pw, ph, pang) < VIA_SIZE / 2.0:
                return False
            continue
        if point_rect_dist(x, y, px, py, pw, ph, pang) < r:
            return False
        # Walk the stub rather than calling seg_rect_dist, which is wrong:
        # for the stub U9.47 -> (125.95, 117.27) against U9.46 (0.2 x 0.8 at
        # 125.35, 117.27) it returns 0.30 at pad angle 0 - the distance from
        # the stub's START to the pad edge - while the stub actually runs
        # straight THROUGH the pad and on to x=125.95. It only reports the
        # overlap at angle 90/270, so whether a real short is caught depends
        # on the pad's rotation, which is the absolute-pad-angle trap this
        # repo keeps hitting. It shorted GND to +3.3V on U9.
        #
        # point_rect_dist is correct (it gives 0.5 for the via against that
        # same pad, which is right), so sample along the stub with it. Same
        # approach the segment loop below already uses, and obviously correct
        # at the cost of a few more distance evaluations.
        length = math.hypot(x - ax, y - ay)
        steps = max(1, int(length / 0.1))
        for i in range(steps + 1):
            t = i / steps
            sx, sy = ax + t * (x - ax), ay + t * (y - ay)
            if point_rect_dist(sx, sy, px, py, pw, ph, pang) < tw:
                return False
    for vx, vy, vs in list(vias) + placed_vias:
        if math.hypot(x - vx, y - vy) < r + vs / 2.0:
            return False
        if point_seg_dist(vx, vy, ax, ay, x, y) < tw + vs / 2.0:
            return False
    for x1, y1, x2, y2, w in segs:
        if point_seg_dist(x, y, x1, y1, x2, y2) < r + w / 2.0:
            return False
        length = math.hypot(x - ax, y - ay)
        steps = max(1, int(length / 0.2))
        for i in range(steps + 1):
            t = i / steps
            sx, sy = ax + t * (x - ax), ay + t * (y - ay)
            if point_seg_dist(sx, sy, x1, y1, x2, y2) < tw + w / 2.0:
                return False
    return True


def candidates(ax, ay):
    # GND carries no signal integrity requirement here - a few mm of stub
    # costs nothing - so the ring keeps growing rather than giving up. Finer
    # angular steps close in, coarser further out where a hit is less likely
    # to matter which exact direction it lands.
    # Tight first. The old ring started at 0.5 mm and stepped 30 degrees, so
    # the nearest legal spot it could even TRY was often further out than one
    # that existed - with a 0.45 mm via the limit beside a 0402 pad is about
    # 0.47 mm from pad centre, and a 30 degree step at that radius samples
    # only every 0.26 mm around it. Fine steps close in cost a few hundred
    # extra distance tests and buy materially shorter stubs; the coarse tail
    # is unchanged because out there the exact direction stops mattering.
    ring = [0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80, 0.90, 1.0, 1.15, 1.3,
            1.6, 2.0, 2.5, 3.0, 3.6, 4.2, 5.0, 6.0, 7.0, 8.0]
    ring += [d for d in range(9, 21)]           # last resort: out to 20 mm
    for dist in ring:
        step = (10 if dist <= 1.0 else
                20 if dist <= 5.0 else 12)
        for deg in range(0, 360, step):
            rad = math.radians(deg)
            yield (round(ax + dist * math.cos(rad), 3),
                   round(ay + dist * math.sin(rad), 3))


def emit_via_and_stub(anchor, vx, vy, net, via_layers):
    ax, ay, alayer = anchor
    NL, TAB = chr(10), chr(9)
    parts = []
    if (round(ax, 3), round(ay, 3)) != (round(vx, 3), round(vy, 3)):
        parts.append(NL.join([
            TAB + "(segment",
            TAB * 2 + "(start %g %g)" % (ax, ay),
            TAB * 2 + "(end %g %g)" % (vx, vy),
            TAB * 2 + "(width %g)" % TRACK_W,
            TAB * 2 + '(layer "F.Cu")',
            TAB * 2 + '(net "%s")' % net,
            TAB * 2 + '(uuid "%s")' % _uuid.uuid4(),
            TAB + ")"]))
    parts.append(NL.join([
        TAB + "(via",
        TAB * 2 + "(at %g %g)" % (vx, vy),
        TAB * 2 + "(size %g)" % VIA_SIZE,
        TAB * 2 + "(drill %g)" % VIA_DRILL,
        TAB * 2 + "(layers %s)" % via_layers,
        TAB * 2 + '(net "%s")' % net,
        TAB * 2 + '(uuid "%s")' % _uuid.uuid4(),
        TAB + ")"]))
    return NL.join(parts)


def main():
    apply = "--apply" in sys.argv
    net = next((a for a in sys.argv[1:] if a != "--apply"), "GND")
    text, fps, netmap, bom = load()
    ex = SY.board_extent(text)

    anchors = drc_unconnected(BOARD, net)
    islands = group_anchors(text, anchors)
    print("  %s items KiCad calls unconnected: %d, in %d island(s)"
          % (net, len(anchors), len(islands)))
    if not islands:
        print("  nothing to do")
        return 0

    vias, segs = obstacles(text, fps, bom, ex, net)
    pads = all_pads(text, fps, netmap)
    # Sort islands smallest-search-radius-first is not tractable to know in
    # advance, but placing the SHORT, easy stitches before the hard ones means
    # the hard ones' wide search sees more of the board already occupied by
    # thin stubs rather than by nothing - which is the honest state of the
    # board once the easy ones are placed, and searching in some fixed order
    # is required either way for the "placed" obstacle list to be consistent.
    placed, failed, new_blocks = [], [], []
    for members in islands:
        found = None
        for ref, pin, ax, ay, _plane in sorted(members):
            for vx, vy in candidates(ax, ay):
                if legal(vx, vy, ax, ay, ex, pads, vias, segs, placed,
                         (ref, pin), net):
                    found = ((ax, ay), vx, vy)
                    break
            if found:
                break
        label = ", ".join("%s.%s" % (r, p) for r, p, *_r2 in sorted(members))
        if found:
            (ax, ay), vx, vy = found
            placed.append((vx, vy, VIA_SIZE))
            # The stub itself is NOT added to segs: it is on `net`, and
            # obstacles() already excludes that net's own tracks from the
            # crossing test for exactly this reason - a later stitch touching
            # an earlier one on the SAME net is fine, it merges two islands
            # into one, which only helps.
            new_blocks.append(emit_via_and_stub((ax, ay, None), vx, vy, net,
                                                '"F.Cu" "B.Cu"'))
            print("  via   (%.2f, %.2f)  <- %s  (%.1f mm stub)"
                  % (vx, vy, label, math.hypot(vx - ax, vy - ay)))
        else:
            failed.append(label)
            print("  FAIL  no legal spot found for: %s" % label)

    print()
    print("  %d via(s) placed, %d island(s) could not be placed automatically"
          % (len(placed), len(failed)))
    if failed:
        print("  unresolved - place these by hand in pcbnew:")
        for f in failed:
            print("    " + f)

    if not apply:
        print(chr(10) + "dry run. re-run with --apply to write it.")
        return 1 if failed else 0

    tail = text.rindex(")")
    text = text[:tail] + chr(10).join(new_blocks) + chr(10) + text[tail:]
    shutil.copy(BOARD, BOARD + ".bak")
    io.open(BOARD, "w", encoding="utf-8", newline=chr(10)).write(text)
    print(chr(10) + "wrote rev3.kicad_pcb (previous version saved as .bak)")
    return P.check() or (1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
