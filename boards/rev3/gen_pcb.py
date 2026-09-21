#!/usr/bin/env python3
"""Generate rev-3's PCB: outline, placement, nets and planes. Not the routing.

    ./gen_pcb.py         writes rev3.kicad_pcb, then checks it with KiCad

What this does and does not do is worth being plain about. It places every
footprint, gives every pad its net, draws the outline and pours the two inner
planes. It does NOT route: that is interactive work in pcbnew, and a generator
emitting 400 tracks nobody had looked at would be worse than one emitting none.

The check that matters is `pcb drc --schematic-parity`: KiCad comparing this
board against rev3.kicad_sch directly, so the board and the verified design are
the same circuit rather than two things that resemble each other.

WRITTEN AS TEXT, not through kiutils. kiutils is used for the schematic because
it round-trips KiCad 7 cleanly, but it cannot even READ a KiCad 10 board - a
pad's `(net "GND")` has no number in this format and its parser walks off the
end of the list. Since a .kicad_mod is already a `(footprint ...)` expression
and KiCad applies a footprint's rotation to its own children, placing one is
text surgery rather than geometry: strip the standalone-file header, add
`(at x y rot)`, set two properties, and put a `(net "...")` in each pad.

Standard library only.

PLACEMENT is the actual content of this file, and two rules drive it:

  - Both muxes sit directly inboard of the column connector. The sense node is
    the high-impedance node on this board and its settling time is set by the
    capacitance hanging off it, so short is not cosmetic here.
  - The row drivers sit inboard of the row connector, so 32 row traces leave
    without crossing the analog half.
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

import gen_rev3 as G
import gen_schematic as S

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "rev3.kicad_pcb")
NET = os.path.join(HERE, "rev3.net")

OX, OY = 100.0, 100.0           # where the board sits on KiCad's canvas
BW, BH = 50.0, 66.0             # MEASURED, not chosen - see README.md
EDGE = 0.5                      # keep plane copper this far inside the outline
MARGIN = 1.6                    # keep parts this far inside the outline
GAP = 1.2                       # between parts in a band
BAND_GAP = 4.2                  # between bands: where the passives go

# Anchors are packed band by band, in this order, top to bottom. Within a band
# the order is left to right, so column-side parts stay under the column
# connector and row-side parts under the row connector.
BANDS = [
    ["J1", "J2"],                          # the sensor: sets the width
    ["U1", "U2", "U3", "U4"],              # row drivers, under J1
    ["U5", "U6"],                          # muxes, as close to J2 as a band allows
    ["U7", "U8", "Y1", "U12", "L2"],       # gain, converter, clock, buck
    ["U9", "U10", "U11"],                  # MCU and the two transceivers
    ["J3", "J4"],                          # harness in/out
    ["J5", "J6", "SW1"],                   # USB, debug, BOOTSEL
]

# JLCPCB's standard (cheap) tier: 5 mil trace and space, 0.3 mm minimum hole.
# Without a setup block KiCad applies its own defaults, and those defaults are
# what reported nine drill_out_of_range errors against a footprint whose
# thermal vias are 0.2 mm.
SETUP = """	(setup
		(pad_to_mask_clearance 0)
		(allow_soldermask_bridges_in_footprints no)
		(rules
			(min_clearance 0.127)
			(min_track_width 0.127)
			(min_through_hole 0.3)
			(min_hole_clearance 0.25)
			(min_via_annular_width 0.13)
			(min_copper_edge_clearance 0.3)
			(min_silk_clearance 0.0)
		)
	)"""

FPDIRS = [d.replace("symbols", "footprints") for d in [G.SYMDIR]] + [
    os.path.join(HERE, "..", "..", "libraries")]

LAYERS = """\t(layers
\t\t(0 "F.Cu" signal)
\t\t(4 "In1.Cu" power "GND")
\t\t(6 "In2.Cu" mixed "PWR")
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)"""

# ref: (x, y, rotation) relative to the board origin. All on the front: the
# board is single-sided assembly on purpose, so this table is the whole
# placement.
#
# The two FFC connectors take the entire top edge. They are 22.1 mm of
# courtyard each against a 48 mm board, so they set the width and there is
# nothing to decide about them; everything else is arranged around the two
# blocks they feed.
PLACE = {
    # --- top edge: the sensor -------------------------------------------
    "J1":  (12.0,  4.4,   0),      # 32 rows
    "J2":  (35.5,  4.4,   0),      # 32 columns

    # --- row half: drivers straight below the row connector -------------
    "U1":  (4.0,  11.0,   0),
    "U2":  (10.0, 11.0,   0),
    "U3":  (16.0, 11.0,   0),
    "U4":  (22.0, 11.0,   0),
    "R3":  (3.0,  14.6,   0),
    "R4":  (5.0,  14.6,   0),
    "C1":  (7.0,  14.6,   0),
    "C2":  (9.0,  14.6,   0),
    "C3":  (11.0, 14.6,   0),
    "C4":  (13.0, 14.6,   0),
    "R5":  (15.0, 14.6,   0),
    "C9":  (17.5, 14.6,   0),

    # --- column half: the muxes hug the column connector ----------------
    "U5":  (31.0, 11.0,  90),
    "U6":  (41.0, 11.0,  90),
    "C5":  (36.0,  8.6,   0),
    "C6":  (38.0,  8.6,   0),
    "R1":  (36.0, 13.6,   0),
    "R2":  (38.0, 13.6,   0),
    "U7":  (45.0, 12.5,  90),      # gain stage, beside the muxes
    "R6":  (35.5, 15.4,   0),
    "R7":  (37.5, 15.4,   0),
    "R8":  (39.5, 15.4,   0),
    "R9":  (41.5, 15.4,   0),
    "C7":  (43.5, 15.4,   0),
    "C8":  (45.5, 15.4,   0),

    # --- converter, between the gain stage and the MCU ------------------
    "U8":  (33.0, 17.5,   0),
    "R10": (29.0, 15.4,   0),
    "C10": (31.0, 15.4,   0),
    "C11": (29.0, 17.4,   0),

    # --- MCU ------------------------------------------------------------
    "U9":  (12.0, 20.5,   0),
    "Y1":  (18.5, 17.6,   0),
    "C20": (21.0, 16.6,   0),
    "C21": (23.0, 16.6,   0),
    "L1":  (6.0,  16.6,   0),
    "C19": (4.0,  16.6,   0),
    "C12": (8.0,  16.6,   0),
    "C13": (10.0, 16.6,   0),
    "C14": (12.0, 16.6,   0),
    "C15": (14.0, 16.6,   0),
    "C16": (16.0, 16.6,   0),
    "R20": (18.5, 20.5,   0),
    "SW1": (4.5,  25.0,   0),
    "J6":  (19.0, 25.6,   0),      # SWD, bottom edge

    # --- bus --------------------------------------------------------------
    "U10": (30.0, 22.5,   0),
    "U11": (37.5, 22.5,   0),
    "C17": (33.5, 20.0,   0),
    "C18": (41.0, 20.0,   0),
    "R11": (27.0, 20.0,   0),
    "R12": (25.0, 20.0,   0),
    "J3":  (1.8,  13.0,  90),      # harness in, left short edge
    "J4":  (46.2, 13.0, 270),      # harness out, right short edge
    "JP1": (41.0, 25.0,   0),      # 3-bit address
    "JP2": (43.0, 25.0,   0),
    "JP3": (45.0, 25.0,   0),

    # --- power and USB ----------------------------------------------------
    "J5":  (10.0, 26.6,   0),      # USB-C, bottom edge
    "R13": (15.0, 23.5,   0),
    "R14": (16.8, 23.5,   0),
    "D1":  (45.0, 19.0,   0),
    "D2":  (47.0, 19.0,   0),
    "U12": (45.0,  6.5,   0),
    "L2":  (47.0,  4.5,   0),
    "R18": (41.0,  4.5,   0),
    "R19": (43.0,  4.5,   0),
    "C22": (39.0,  4.5,   0),
    "C23": (37.0,  4.5,   0),
    "R15": (24.0, 17.5,   0),
    "R16": (26.0, 17.5,   0),
    "R17": (22.0, 20.5,   0),
    "D3":  (24.0, 20.5,   0),
}


# Anchors are exactly the parts named in BANDS - derived, not a second list.
# Keeping it a separate set let L1 and D3 be "anchors" that appeared in no
# band, so they were never placed and their four pads went missing from the
# board while every check but the pad comparison stayed green.
ANCHORS = {r for band in BANDS for r in band}


# KiCad ships no 3D model for two of the parts here, and they are the two most
# visible ones - the MCU and the USB port - so the board renders with a hole
# where each should be. Substituted with the nearest shipped body:
#
#   QFN-60 7x7 P0.4  -> QFN-56 7x7 P0.4. Same body, same pitch, four fewer
#                       pins; at render scale they are the same object.
#   HRO TYPE-C-31-M-12 -> GCT USB4105, also 16-pin top-mount horizontal.
#
# These affect the picture only. Nothing here reaches the netlist, the BOM or
# the fabrication outputs, which is why a near-enough body is the right answer
# rather than drawing an exact one.
MODEL_FIX = {
    "Package_DFN_QFN.3dshapes/QFN-60-1EP_7x7mm_P0.4mm_EP3.4x3.4mm.step":
        "Package_DFN_QFN.3dshapes/QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm.step",
    "Connector_USB.3dshapes/USB_C_Receptacle_HRO_TYPE-C-31-M-12.step":
        "Connector_USB.3dshapes/USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal.step",
}


def fix_models(text):
    for old, new in MODEL_FIX.items():
        text = text.replace(old, new)
    return text


# ------------------------------------------------------------ text surgery
FPNAME_RE = re.compile(r'^\(footprint "[^"]*"')


def close_of(text, start):
    """Index just past the ')' matching the '(' at `start`."""
    depth, i, in_str = 0, start, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced s-expression")


def uid():
    return '(uuid "%s")' % _uuid.uuid4()


def courtyard(fpfile, x, y, rot):
    """Board-space bounding box of a footprint's F.CrtYd, as (x1, y1, x2, y2).

    Placement here is a hand-written table, and a table of 69 coordinates
    written by eye WILL have collisions in it - the first version had 88.
    Finding them needs no DRC run and no KiCad: the courtyard is the outline
    the footprint declares as "nothing else may be here", so overlapping two
    of them is a placement error by definition. Checking it in the generator
    means the collision list arrives with names attached instead of as 196
    shorting_items in a report.
    """
    text = io.open(fpfile, encoding="utf-8").read()
    xs, ys = [], []
    for m in re.finditer(r'\((?:fp_line|fp_rect|fp_poly)\b', text):
        body = text[m.start():close_of(text, m.start())]
        if '(layer "F.CrtYd")' not in body:
            continue
        for a, b in re.findall(r'\((?:start|end|mid|xy) ([-\d.]+) ([-\d.]+)\)', body):
            xs.append(float(a))
            ys.append(float(b))
    if not xs:                       # no courtyard drawn: fall back to pads
        for m in re.finditer(r'\(pad "', text):
            body = text[m.start():close_of(text, m.start())]
            at = re.search(r'\(at ([-\d.]+) ([-\d.]+)', body)
            sz = re.search(r'\(size ([-\d.]+) ([-\d.]+)\)', body)
            if at and sz:
                px, py = float(at.group(1)), float(at.group(2))
                sw, sh = float(sz.group(1)) / 2, float(sz.group(2)) / 2
                xs += [px - sw, px + sw]
                ys += [py - sh, py + sh]
    if not xs:
        return None
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    a = math.radians(rot or 0)
    ca, sa = math.cos(a), math.sin(a)
    # KiCad rotates a footprint counter-clockwise about its origin, and its Y
    # axis points down, which is why the sine terms are signed this way.
    rot_pts = [(px * ca + py * sa, -px * sa + py * ca) for px, py in corners]
    rx = [p[0] for p in rot_pts]
    ry = [p[1] for p in rot_pts]
    return (x + min(rx), y + min(ry), x + max(rx), y + max(ry))


def find_footprint(fp):
    lib, name = fp.split(":", 1)
    for d in FPDIRS:
        path = os.path.join(d, lib + ".pretty", name + ".kicad_mod")
        if os.path.exists(path):
            return path
    sys.exit("footprint not in any library: " + fp)


def place(fpfile, fpname, ref, value, x, y, rot, pin_nets):
    """One .kicad_mod turned into a board footprint at (x, y, rot)."""
    text = io.open(fpfile, encoding="utf-8").read().strip()

    # On a board a footprint is named "Library:Name". A standalone .kicad_mod
    # carries only the bare name, and leaving it that way makes KiCad report a
    # footprint_symbol_mismatch against EVERY part - 69 of them here.
    text = re.sub(FPNAME_RE, '(footprint "%s"' % fpname, text, count=1)

    # A standalone .kicad_mod carries a file header a board footprint must not.
    for tag in ("version", "generator_version", "generator"):
        text = re.sub(r'\n\t\(%s [^)]*\)' % tag, "", text, count=1)

    # Position and identity go straight after the name, at whatever
    # indentation the rest of this block uses. Hard-coding two tabs put the
    # `(at ...)` one level deeper than the body once the chunk was nested into
    # a board, and `sync_pcb.footprints()` - which demanded exactly two -
    # silently read every part placed this way as sitting at (0, 0).
    head = text.index("\n")
    ind = re.match(r"\n(\t*)", text[head:])
    ind = ind.group(1) if ind else "\t"
    at = "\n%s(at %g %g%s)\n%s%s" % (
        ind, OX + x, OY + y, (" %g" % rot) if rot else "", ind, uid())
    text = text[:head] + at + text[head:]

    # Reference and Value are properties in this format, not fp_text.
    def setprop(t, key, val):
        m = re.search(r'\(property "%s" "[^"]*"' % key, t)
        if not m:
            return t
        return t[:m.start()] + '(property "%s" "%s"' % (key, val) + t[m.end():]
    text = setprop(text, "Reference", ref)
    text = setprop(text, "Value", value)

    # A pad's own (at x y ANGLE) carries the pad's orientation, and in a board
    # that angle is absolute - the footprint's rotation is NOT added to it.
    # Rotating a footprint therefore moves its pads but leaves each pad's shape
    # pointing the original way, so a rotated SSOP-24's 1.9 mm pads end up
    # broadside to a 0.65 mm pitch. That produced 104 shorting_items, 104
    # solder_mask_bridges and 36 clearance errors, all of them inside single
    # components, until the angle was carried through here.
    if rot:
        def turn(m):
            ang = (float(m.group(3) or 0) + rot) % 360
            return "(at %s %s%s)" % (m.group(1), m.group(2),
                                     " %g" % ang if ang else "")
        parts = []
        i = 0
        for m in re.finditer(r'\(pad "', text):
            if m.start() < i:
                continue
            end = close_of(text, m.start())
            parts.append(text[i:m.start()])
            parts.append(re.sub(r'\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)',
                                turn, text[m.start():end], count=1))
            i = end
        parts.append(text[i:])
        text = "".join(parts)

    # Nets, one pad at a time. Inserted before each pad's closing paren.
    out, i, seen = [], 0, set()
    for m in re.finditer(r'\(pad "([^"]*)"', text):
        if m.start() < i:
            continue
        end = close_of(text, m.start())
        num = m.group(1)
        seen.add(num)
        net = pin_nets.get(num)
        body = text[m.start():end]
        if net:
            body = body[:-1].rstrip() + '\n\t\t\t(net "%s")\n\t\t' % net + ")"
        out.append(text[i:m.start()])
        out.append(body)
        i = end
    out.append(text[i:])
    text = "".join(out)

    unknown = set(pin_nets) - seen
    text = fix_models(text)
    return "\t" + text.replace("\n", "\n\t"), unknown


def hits(box, placed, pad=0.15):
    """Does `box` clash with anything already down, or leave the board?"""
    if box is None:
        return False
    if (box[0] < pad or box[1] < pad
            or box[2] > BW - pad or box[3] > BH - pad):
        return True
    for other in placed:
        if other is None:
            continue
        if (min(box[2], other[2]) - max(box[0], other[0]) > -pad
                and min(box[3], other[3]) - max(box[1], other[1]) > -pad):
            return True
    return False


def resolve(bom):
    """Final (x, y, rot) for every part: anchors packed by band, rest nudged.

    The anchors are NOT placed from hand-typed coordinates. Sixty-nine
    coordinates written by eye had 88 courtyard overlaps in them, and the
    reason was systematic: the area estimates behind them used package BODY
    sizes, where what a neighbour actually has to clear is the COURTYARD -
    about twice the area on small SMD parts. So each band is packed
    left-to-right from the real courtyard widths and the bands are stacked by
    their real heights, which makes anchor collisions impossible by
    construction rather than something to hunt for afterwards.

    Everything else is searched outward from its hint on a 0.25 mm grid.
    Deterministic, so the board comes out identical on every run.
    """
    boxes, out, problems = {}, {}, []

    def box_at(ref, x, y, rot):
        return courtyard(find_footprint(bom[ref][1]), x, y, rot)

    def size(ref, rot):
        b = box_at(ref, 0, 0, rot)
        return (b[2] - b[0], b[3] - b[1]) if b else (2.0, 2.0)

    y = MARGIN
    for band in BANDS:
        band = [r for r in band if r in PLACE]
        rots = {r: PLACE[r][2] for r in band}
        widths = {r: size(r, rots[r])[0] for r in band}
        height = max((size(r, rots[r])[1] for r in band), default=0.0)
        span = sum(widths.values()) + GAP * (len(band) - 1)
        if span > BW - 2 * MARGIN:
            problems.append("band %s needs %.1f mm, the board is %.1f mm wide"
                            % (band, span + 2 * MARGIN, BW))
        x = max(MARGIN, (BW - span) / 2.0)
        for ref in band:
            w, h = size(ref, rots[ref])
            cx, cy = x + w / 2.0, y + height / 2.0
            b0 = box_at(ref, 0, 0, rots[ref])
            # box_at returns the courtyard around the footprint ORIGIN, which
            # is not its centre - place by centre so packing is exact.
            ox, oy = (b0[0] + b0[2]) / 2.0, (b0[1] + b0[3]) / 2.0
            px, py = round((cx - ox) * 4) / 4, round((cy - oy) * 4) / 4
            boxes[ref] = box_at(ref, px, py, rots[ref])
            out[ref] = (px, py, rots[ref])
            x += w + GAP
        y += height + BAND_GAP

    # Deterministic outward search: rings of increasing radius, and within a
    # ring a fixed order, so the same input always gives the same board.
    steps = [(0.0, 0.0)]
    for r in range(1, 61):
        d = r * 0.25
        for dx, dy in ((0, -d), (0, d), (-d, 0), (d, 0), (-d, -d), (d, -d),
                       (-d, d), (d, d), (0, -d * 2), (0, d * 2)):
            steps.append((dx, dy))

    for ref in sorted(set(PLACE) - ANCHORS,
                      key=lambda r: (r[0], int(re.sub(r"\D", "", r) or 0))):
        hx, hy, rot = PLACE[ref]
        for dx, dy in steps:
            x, y = round((hx + dx) * 4) / 4, round((hy + dy) * 4) / 4
            b = box_at(ref, x, y, rot)
            if not hits(b, boxes.values()):
                boxes[ref], out[ref] = b, (x, y, rot)
                break
        else:
            problems.append("%s: nowhere free within 15 mm of its hint" % ref)
            boxes[ref], out[ref] = box_at(ref, hx, hy, rot), (hx, hy, rot)
    moved = sum(1 for r in out if r not in ANCHORS and out[r][:2] != PLACE[r][:2])
    return out, boxes, problems, moved


def build():
    nets_by_ref = S.load_net(NET)
    bom = S.load_bom()

    missing = sorted(set(nets_by_ref) - set(PLACE))
    if missing:
        sys.exit("no position given for: %s" % missing)

    layout, boxes, problems, moved = resolve(bom)

    body = []
    for ref in sorted(layout, key=lambda r: (r[0], int(re.sub(r"\D", "", r) or 0))):
        x, y, rot = layout[ref]
        value, fpname = bom[ref]
        chunk, unknown = place(find_footprint(fpname), fpname, ref, value,
                               x, y, rot, nets_by_ref.get(ref, {}))
        if unknown:
            problems.append("%s: netlist names pad(s) %s the footprint has not"
                            % (ref, sorted(unknown)))
        body.append(chunk)

    edge = []
    for x1, y1, x2, y2 in ((0, 0, BW, 0), (BW, 0, BW, BH),
                           (BW, BH, 0, BH), (0, BH, 0, 0)):
        edge.append('\t(gr_line\n\t\t(start %g %g)\n\t\t(end %g %g)\n'
                    '\t\t(stroke (width 0.1) (type default))\n'
                    '\t\t(layer "Edge.Cuts")\n\t\t%s\n\t)'
                    % (OX + x1, OY + y1, OX + x2, OY + y2, uid()))

    # The two inner planes. ONE unbroken ground: the v2 module split it because
    # 30 mA of press-correlated row current returned through the analog
    # reference, and at 1 MOhm a driven row sources 106 uA.
    zones = []
    for layer, net in (("In1.Cu", "GND"), ("In2.Cu", "+3.3V")):
        pts = " ".join("(xy %g %g)" % (OX + a, OY + b) for a, b in (
            (EDGE, EDGE), (BW - EDGE, EDGE),
            (BW - EDGE, BH - EDGE), (EDGE, BH - EDGE)))
        zones.append(
            '\t(zone\n\t\t(net "%s")\n\t\t(layer "%s")\n\t\t%s\n'
            '\t\t(hatch edge 0.5)\n\t\t(connect_pads\n\t\t\t(clearance 0.25)\n\t\t)\n'
            '\t\t(min_thickness 0.2)\n\t\t(fill yes\n\t\t\t(thermal_gap 0.5)\n'
            '\t\t\t(thermal_bridge_width 0.5)\n\t\t\t(island_removal_mode 0)\n\t\t)\n'
            '\t\t(polygon\n\t\t\t(pts\n\t\t\t\t%s\n\t\t\t)\n\t\t)\n\t)'
            % (net, layer, uid(), pts))

    board = "\n".join([
        "(kicad_pcb",
        '\t(version 20260206)',
        '\t(generator "gen_pcb.py")',
        '\t(generator_version "10.0")',
        "\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)",
        '\t(paper "A4")',
        LAYERS,
        "\t(setup\n\t\t(pad_to_mask_clearance 0)\n"
        "\t\t(allow_soldermask_bridges_in_footprints no)\n\t)",
    ] + body + edge + zones + [")", ""])
    return board, problems, moved


# ------------------------------------------------------------------- checks
def check():
    cli = S.find_cli()
    if not cli:
        print("\n  kicad-cli not found: SKIPPING every check.\n"
              "  The file is written but NOTHING has verified it.")
        return 1
    fails = []
    text = io.open(OUT, encoding="utf-8").read()

    # Every pad rev3.net names must be on the board carrying that net. This is
    # the board's version of the schematic's netlist comparison, and it is what
    # makes the board and the verified design the same circuit.
    want = S.load_net(NET)
    on_board, ref = {}, None
    for m in re.finditer(r'\(property "Reference" "([^"]*)"|\(pad "([^"]*)"', text):
        if m.group(1) is not None:
            ref = m.group(1)
            continue
        end = close_of(text, m.start())
        nm = re.search(r'\(net "([^"]*)"\)', text[m.start():end])
        if nm:
            on_board[(ref, m.group(2))] = nm.group(1)
    bad = 0
    for r, pins in want.items():
        for pin, net in pins.items():
            if on_board.get((r, pin)) != net:
                bad += 1
                if bad <= 6:
                    fails.append("%s.%s is %s on the board, %s in rev3.net"
                                 % (r, pin, on_board.get((r, pin)), net))
    total = sum(len(p) for p in want.values())
    print("  pads   %d of %d carry the net rev3.net gives them" % (total - bad, total))

    tmp = tempfile.mkdtemp(prefix="rev3pcb")
    rpt = os.path.join(tmp, "drc.rpt")
    r = subprocess.run([cli, "pcb", "drc", "--output", rpt, "--severity-all",
                        "--refill-zones", "--schematic-parity", OUT],
                       capture_output=True, text=True)
    if not os.path.exists(rpt):
        print("  DRC    board would not load:\n         %s"
              % (r.stderr or r.stdout).strip()[:300])
        fails.append("KiCad could not load the board, so nothing checked it")
    else:
        rep = io.open(rpt, encoding="utf-8").read()
        counts = {}
        for m in re.finditer(S.VIOLATION_RE, rep, re.M):
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
        unconn = re.search(r"Found (\d+) unconnected item", rep)
        print("  DRC    %d violation(s), %s unconnected item(s)"
              % (sum(counts.values()), unconn.group(1) if unconn else "?"))
        for rule, cnt in sorted(counts.items(), key=lambda kv: -kv[1])[:9]:
            print("           %-30s x%d" % (rule, cnt))
        # Parity is the one that has to be clean: it is KiCad saying the board
        # and the schematic are the same circuit. Overlaps and unrouted nets
        # are expected at this stage; a parity error means they are not.
        # net_conflict on a pad the SCHEMATIC also leaves unconnected is not a
        # disagreement: those are the QSPI bus, the spare GPIO, U4's carry-out
        # and USB-C's sideband pair, all deliberately unused. Genuine parity
        # errors are anything else, and those do fail the build.
        nc_unconnected = len(re.findall(
            r"Pad missing net given by schematic \(unconnected-", rep))
        parity = sum(c for k, c in counts.items()
                     if k in ("footprint_symbol_mismatch", "extra_footprint",
                              "missing_footprint", "footprint_filters_mismatch"))
        parity += counts.get("net_conflict", 0) - nc_unconnected
        print("  parity %d genuine violation(s); %d pad(s) deliberately left "
              "unconnected" % (parity, nc_unconnected))
        if parity > 0:
            fails.append("%d schematic-parity violation(s) - the board and the "
                         "sheet disagree" % parity)
    shutil.rmtree(tmp, ignore_errors=True)
    return S.report_fails(fails)


def hand_edited():
    """Has pcbnew saved over this board since the generator last wrote it?

    Placement past the first pass is human work - the generated layout packs
    bands so nothing collides, but a person moving parts by hand does far
    better, and there is no way to feed that back into PLACE. So once pcbnew
    has saved the file, this generator must not overwrite it. KiCad stamps its
    own name into (generator ...) on save, which is the tell.
    """
    if not os.path.exists(OUT):
        return False
    head = io.open(OUT, encoding="utf-8").read(400)
    m = re.search(r'\(generator "([^"]*)"', head)
    return bool(m) and m.group(1) != "gen_pcb.py"


def main():
    if hand_edited() and "--force" not in sys.argv:
        print("rev3.kicad_pcb was last saved by pcbnew, not by this script.\n"
              "Refusing to overwrite a hand-placed board.\n\n"
              "  To pick up netlist or footprint changes while KEEPING the\n"
              "  placement, open the board in pcbnew and use\n"
              "      Tools > Update PCB from Schematic\n\n"
              "  To throw the placement away and regenerate from scratch:\n"
              "      ./gen_pcb.py --force")
        return check()

    board, problems, moved = build()
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(board)
    print("rev3 pcb: %d footprints, %.0f x %.0f mm (%.0f mm2), 4 layers"
          % (len(PLACE), BW, BH, BW * BH))
    print("  place  %d anchored, %d passives nudged off their hint"
          % (len(ANCHORS & set(PLACE)), moved))
    for p in problems:
        print("  ! " + p)
    return check()


if __name__ == "__main__":
    sys.exit(main())
