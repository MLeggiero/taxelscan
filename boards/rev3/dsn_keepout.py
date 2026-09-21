#!/usr/bin/env python3
"""Turn the hand-routed copper in a Specctra DSN into keepouts, so freerouting
routes only what is left without ever touching (or crashing on) it.

    python dsn_keepout.py rev3.dsn rev3.fr.dsn

WHY. Freerouting 2.2.4 throws `NullPointerException: "to_trace_entries" is
null` and aborts its routing passes as soon as the DSN carries pre-routed
wires - fixed or not, on any layer, of any width (bisected on this board: one
wire is fine, a few hundred are not). The hand-laid copper here - the MCU's
bypass stubs, the analog chain, the crystal, the buck loop, the plane
stitching, the differential pairs - is exactly the part that must not move,
so it cannot be handed over as `(type route)` either.

So it is handed over as geometry instead of wiring:

  * every wire on a signal layer becomes a `(keepout)` rectangle of the
    wire's own width on that layer;
  * every via becomes a circle keepout on each signal layer plus a
    `(via_keepout)` so nothing else drills into it;
  * every net that the hand routing owns is dropped from `(network)`, which
    unassigns its pins - freerouting then neither routes it nor treats its
    pads as anything but obstacles. The wires themselves are deleted.

Freerouting keeps the class clearance between its traces and keepout areas,
so the result imports back with `import_ses.py --apply --add` (session copper
added to the existing board, nothing removed) and only the fine-pitch escapes
still need the maze router afterwards.
"""
import io
import re
import sys

# Nets whose copper is placed for a physical reason. Anything freerouting
# would do to them is a regression, so they are removed from its universe.
OWNED = {"GND", "+3.3V", "VCORE", "VREG_LX", "VREG_AVDD", "ADC_AVDD", "VREF", "ROW_VCC",
         "XIN", "XOUT", "XOUT_MCU", "SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B", "AMP_A", "AMP_B",
         "ADC_A", "ADC_B", "FB", "SW_NODE", "+5V", "+5V_USB", "+5V_BUS", "RAIL_MON",
         "USB_CC1", "USB_CC2", "USB_D_P", "USB_D_N", "USBC_D_P", "USBC_D_N",
         "BUS_P", "BUS_N", "SYNC_P", "SYNC_N"}
SIGNAL = ("F.Cu", "B.Cu")
U = 1000.0   # DSN coordinates are in um: (unit um)


def square(x, y, r):
    """An octagon of circumradius r about (x, y): freerouting reads `(circle)`
    keepouts with the wrong scale (one via circle walled off the whole
    board), polygons it reads correctly."""
    import math
    pts = [(x + r * math.cos(-math.pi / 4 * k + math.pi / 8), y + r * math.sin(-math.pi / 4 * k + math.pi / 8)) for k in range(8)]   # clockwise, like the wire rectangles
    return " ".join("%d %d" % (int(px), int(py)) for px, py in pts)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    t = io.open(src, encoding="utf-8").read()
    wires = re.findall(r'\(wire\s*\(path (\S+) ([\d.]+)((?:\s+[-\d.]+)+)\s*\)\s*\(net ([^)]+)\)\(type \w+\)\s*\)\n?', t)
    vias = re.findall(r'\(via\s+"([^"]+)"\s+([-\d.]+)\s+([-\d.]+)\s*\(net ([^)]+)\)\(type \w+\)\s*\)\n?', t)
    keep = []
    n_seg = 0
    for lay, w, pts, net, in wires:
        # a wire on a plane layer is out of freerouting's reach, but its VIAS
        # still drill through that layer: fence it with a via keepout instead
        kind = "keepout" if lay in SIGNAL else "via_keepout"
        xs = list(map(float, pts.split()))
        hw = float(w) / 2.0 + (0 if lay in SIGNAL else 200)
        if lay not in SIGNAL: lay = "F.Cu"
        for k in range(len(xs) // 2 - 1):
            x1, y1, x2, y2 = xs[2*k], xs[2*k+1], xs[2*k+2], xs[2*k+3]
            dx, dy = x2 - x1, y2 - y1
            L = (dx*dx + dy*dy) ** 0.5
            if L < 1:
                keep.append('(%s "" (polygon %s 0 %s))' % (kind, lay, square(x1, y1, hw)))
                continue
            nx, ny = -dy / L * hw, dx / L * hw
            ex, ey = dx / L * hw, dy / L * hw          # square the ends off past the endpoints
            poly = [(x1 - ex + nx, y1 - ey + ny), (x2 + ex + nx, y2 + ey + ny),
                    (x2 + ex - nx, y2 + ey - ny), (x1 - ex - nx, y1 - ey - ny)]
            keep.append('(%s "" (polygon %s 0 %s))' % (kind, lay, " ".join("%d %d" % (int(x), int(y)) for x, y in poly)))
            n_seg += 1
    # vias sitting on a pad of their own net add nothing the pad does not already block
    import json, os
    fv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../tmp/free_vias.json")
    free = None   # every via keeps its keepout; the on-pad filter is not needed at the right scale
    for pad, x, y, net in vias:
        if free is not None and (round(float(x)), round(float(y))) not in free:
            continue
        m = re.search(r"_(\d+):(\d+)_um", pad)
        dia = int(m.group(1)) if m else 500   # um
        for lay in SIGNAL:
            keep.append('(keepout "" (polygon %s 0 %s))' % (lay, square(float(x), float(y), (dia / 2.0 + 30) / 0.9239)))   # inscribed radius = pad radius
        keep.append('(via_keepout "" (polygon F.Cu 0 %s))' % square(float(x), float(y), (dia / 2.0 + 200) / 0.9239))
    # KiCad exports rule areas (the 0.1 mm escape zones) as keepouts although
    # they forbid nothing - drop every keepout the exporter wrote.
    def drop_block(s, opener):
        while True:
            i = s.find(opener)
            if i < 0: return s
            depth = 0; j = i
            while True:
                if s[j] == "(": depth += 1
                elif s[j] == ")":
                    depth -= 1
                    if depth == 0: break
                j += 1
            s = s[:i] + s[j+1:]
    n0 = t.count("(keepout"); t = drop_block(t, "(keepout"); print("  removed %d exporter keepouts (rule areas)" % n0)
    # strip the wiring section
    m = re.search(r"\n  \(wiring\n", t)
    if m:
        end = t.index("\n  )\n", m.start()) + len("\n  )\n")
        t = t[:m.start()] + "\n  (wiring\n  )\n" + t[end:]
    # drop the owned nets from the network and the classes
    dropped = []
    for net in sorted(OWNED):
        pat = r'\n\s*\(net %s\s*\n\s*\(pins[^)]*\)\s*\n\s*\)' % re.escape(net)
        t, n = re.subn(pat, "", t)
        if n:
            dropped.append(net)
    def fix_class(m):
        toks = m.group(2).split()
        toks = [x for x in toks if x not in OWNED]
        return "(class %s %s" % (m.group(1), " ".join(toks))
    t = re.sub(r"\(class (\S+)((?:\s+[^\s()]+)+)", fix_class, t)
    # insert the keepouts at the end of (structure)
    s = t.index("(structure")
    e = t.index("\n  )\n", s)
    t = t[:e] + "\n    " + "\n    ".join(keep) + t[e:]
    io.open(dst, "w", encoding="utf-8", newline="\n").write(t)
    print("  %d wire segments -> keepouts, %d vias -> keepouts, %d nets removed: %s"
          % (n_seg, len(vias), len(dropped), " ".join(dropped)))
    left = re.findall(r"\(net (\S+)\s*\n\s*\(pins", t)
    print("  %d nets left for freerouting" % len(left))


if __name__ == "__main__":
    main()
