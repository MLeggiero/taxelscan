"""dsn_prep.py in.dsn out.dsn [--plane-keepouts] [--fid-keepouts] [--fixed NET,NET,...]

Make KiCad's Specctra export fit for freerouting.

KiCad's exporter gets four things wrong for this board, and freerouting knows
nothing of the board's custom rules, so the DSN is rewritten before routing:

  1. Rule areas become keepouts. U9_escape and U13_escape are *clearance*
     rule areas (0.10 mm inside them, rev4.kicad_dru), not no-route zones, but
     the exporter writes each as a keepout on every layer - which would wall off
     the whole MCU corner. They are dropped. VREG_LX_cutout is a plane cut-out
     on In1 (GND), a power layer freerouting never routes; it stays.
  2. In2 is exported as a signal layer because the board calls it "mixed". It
     is the +3.3 V plane: every signal routed on it cuts the pour (rev-3 had
     221 mm there and seven pour pieces). It becomes a power layer, so
     freerouting routes on F.Cu and B.Cu only.
  3. The copper kept on the board - GND and +3.3 V, the pad stubs and vias that
     tie every SMD pad to its plane - is exported as ordinary routing, which
     freerouting's optimiser is free to rip up. It becomes protected.
  4. Every net is in one 0.15 / 0.15 class. The board's widths are not: the
     5 V rails are 0.3 mm, USB_BUS_SW 0.4, the buck switch node 0.3, VREG_LX
     0.2, and the nets on the RP2354A's and TUSB320's 0.4 mm-pitch pins 0.1 mm.
     The analog chain stays on F.Cu, over the unbroken ground: SENSE, GAIN,
     ADC and RAIL_MON may not leave it. AMP_A/AMP_B, the op-amp outputs, may
     (they must cross SENSE_B). New classes carry all of that.

Freerouting has no area rules. A first run gave the fine-pitch classes 0.10 mm
clearance everywhere and came back with 46 clearance and 92 hole-clearance
violations outside the escape areas, so every class now uses the board's
0.15 mm, which with a 0.5 mm via also keeps the 0.25 mm copper-to-hole rule.

Two more things the first run taught, both options here:

  --plane-keepouts  freerouting does not see the GND / +3.3 V pad stubs and vias
                    as connecting through the planes, counted 114 of their
                    connections "unrouted" and routed copper between them. So
                    the two nets leave the network altogether: their copper
                    becomes keepouts (each segment as its outline, each via as
                    a circle on both outer layers), and ses_import.py puts the
                    original copper back after the session is imported.
  --fid-keepouts    the fiducials' 0.6 mm pad clearance is not in the DSN; a
                    keepout circle of the pad plus 0.6 mm stands in for it.
  --fixed NETS      nets already routed before freerouting runs (the ADC
                    corner, the RS-485 pairs, USB_D): with --plane-keepouts
                    they leave the network the same way as the planes, so
                    freerouting routes around their copper and cannot move it.
"""
import math
import re
import sys

FINE_PARTS = ("U9", "U13")
CLASSES = [
    # name, width um, clearance um, via, layers (None = F.Cu and B.Cu), nets
    ("P5V", 300, 150, "Via[0-3]_600:300_um", None, ["+5V", "+5V_BUS", "+5V_USB"]),
    ("P04", 400, 150, "Via[0-3]_600:300_um", None, ["USB_BUS_SW"]),
    ("P03", 300, 150, "Via[0-3]_600:300_um", None, ["SW_NODE"]),
    ("LX", 200, 150, "Via[0-3]_500:300_um", None, ["VREG_LX"]),
    ("ANAF", 150, 150, "Via[0-3]_500:300_um", ["F.Cu"], ["SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B"]),
    ("ANAFINE", 100, 150, "Via[0-3]_500:300_um", ["F.Cu"], ["ADC_A", "ADC_B", "RAIL_MON"]),
    ("AMP", 150, 150, "Via[0-3]_500:300_um", None, ["AMP_A", "AMP_B"]),
    ("PAIRFINE", 100, 150, "Via[0-3]_500:300_um", None, ["USB_D_P", "USB_D_N"]),
    ("PAIR", 150, 150, "Via[0-3]_500:300_um", None, ["USBC_D_P", "USBC_D_N", "BUS_P", "BUS_N", "SYNC_P", "SYNC_N"]),
    ("PLANE", 150, 150, "Via[0-3]_500:300_um", None, ["GND", "+3.3V"]),
]
PROTECT_NETS = {"GND", "+3.3V"}


def block_end(s, i):
    """Index just past the parenthesised block that starts at s[i] == '('."""
    depth = 0
    q = False
    for j in range(i, len(s)):
        c = s[j]
        if c == '"':
            q = not q
        elif not q:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return j + 1
    raise ValueError("unbalanced")


def nets_of(s):
    """{net: [pins]} from the network section."""
    out = {}
    for m in re.finditer(r'\(net ("[^"]*"|\S+)\s*\(pins ([^)]*)\)', s):
        out[m.group(1).strip('"')] = m.group(2).split()
    return out


def seg_polygon(x0, y0, x1, y1, w):
    """A segment of width w as an octagon-ended outline (DSN units)."""
    from shapely.geometry import LineString, Point
    g = (LineString([(x0, y0), (x1, y1)]) if (x0, y0) != (x1, y1) else Point(x0, y0)).buffer(w / 2, 2)
    pts = list(g.exterior.coords)
    return " ".join("%.0f %.0f" % p for p in pts)


def planes_to_keepouts(s, report, fixed=()):
    """Take GND, +3.3V and the fixed nets out of the network; their copper becomes keepouts."""
    out_nets = set(PROTECT_NETS) | set(fixed)
    ws = s.index("(wiring")
    we = block_end(s, ws)
    wiring = s[ws:we]
    keep = []
    out = []
    i = 0
    n_w = n_v = 0
    while True:
        k1 = wiring.find("(wire ", i)
        k2 = wiring.find("(via ", i)
        ks = [k for k in (k1, k2) if k >= 0]
        if not ks:
            out.append(wiring[i:])
            break
        k = min(ks)
        e = block_end(wiring, k)
        blk = wiring[k:e]
        net = re.search(r"\(net ([^)]*)\)", blk)
        net = net.group(1).strip('"') if net else ""
        out.append(wiring[i:k])
        if net in out_nets:
            if blk.startswith("(wire"):
                m = re.search(r"\(path (\S+) ([\d.]+)\s+([-\d.\s]+)\)", blk)
                lay, w = m.group(1), float(m.group(2))
                xy = [float(v) for v in m.group(3).split()]
                for j in range(0, len(xy) - 2, 2):
                    keep.append('    (keepout "" (polygon %s 0 %s))\n' % (lay, seg_polygon(xy[j], xy[j + 1], xy[j + 2], xy[j + 3], w)))
                n_w += 1
            else:
                m = re.search(r'\(via "Via\[0-3\]_(\d+):\d+_um"\s+([-\d.]+) ([-\d.]+)', blk)
                dia, x, y = m.group(1), m.group(2), m.group(3)
                for lay in ("F.Cu", "B.Cu"):
                    keep.append('    (keepout "" (circle %s %s %s %s))\n' % (lay, dia, x, y))
                n_v += 1
        else:
            out.append(blk)
        i = e
    s = s[:ws] + "".join(out) + s[we:]
    # the nets leave the network, and the planes that referred to them go
    for net in sorted(out_nets):
        q = '"%s"' % net if re.search(r'[\s()]', net) else net
        s, n = re.subn(r"\(net %s\s*\(pins [^)]*\)\s*\)\s*" % re.escape(q), "", s)
        if not n:
            raise SystemExit("dsn_prep: net %s is not in the network" % net)
    s = re.sub(r"\(plane (?:GND|\+3\.3V) \(polygon [^)]*\)\)\s*", "", s)
    st = s.index("(structure")
    ins = s.index("    (via ", st)
    s = s[:ins] + "".join(keep) + s[ins:]
    report.append("plane%s nets out of the network: %d wire(s), %d via(s) -> %d keepout shape(s)"
                  % (" and %d fixed" % len(fixed) if fixed else "", n_w, n_v, len(keep)))
    return s


def fid_keepouts(s, report, clear=600):
    keep = []
    for m in re.finditer(r"\(place (FID\d+) ([-\d.]+) ([-\d.]+) front", s):
        keep.append('    (keepout "" (circle F.Cu %d %s %s))\n' % (1000 + 2 * clear, m.group(2), m.group(3)))
    st = s.index("(structure")
    ins = s.index("    (via ", st)
    s = s[:ins] + "".join(keep) + s[ins:]
    report.append("fiducial keepouts: %d" % len(keep))
    return s


def main(src, dst, plane_keepouts=False, fid=False, fixed=()):
    s = open(src, encoding="utf-8").read()
    report = []

    # 1. the clearance rule areas are not keepouts
    st = s.index("(structure")
    se = block_end(s, st)
    struct = s[st:se]
    kept, dropped = [], 0
    i = 0
    out = []
    while True:
        k = struct.find("(keepout", i)
        if k < 0:
            out.append(struct[i:])
            break
        e = block_end(struct, k)
        blk = struct[k:e]
        out.append(struct[i:k])
        if "(polygon GND" in blk and "127464" in blk:          # VREG_LX_cutout, on the GND plane
            out.append(blk)
        else:
            dropped += 1
        i = e
    struct = "".join(out)
    report.append("dropped %d rule-area keepouts (U9_escape, U13_escape on every layer)" % dropped)

    # 2. In2 is the +3.3 V plane
    struct, n = re.subn(r"\(layer PWR\s*\(type signal\)", "(layer PWR\n      (type power)", struct)
    report.append("In2 (PWR) typed power: %d" % n)
    s = s[:st] + struct + s[se:]

    if fixed and not plane_keepouts:
        raise SystemExit("dsn_prep: --fixed needs --plane-keepouts")
    if plane_keepouts:
        s = planes_to_keepouts(s, report, fixed)
    if fid:
        s = fid_keepouts(s, report)

    # 3. the copper already on the board is fixed: the plane nets' ties, and with
    # --plane-keepouts whatever is left in the wiring after the planes and the
    # fixed nets went - copper laid on purpose for a net freerouting still
    # routes (preroute_rev4's VBUS ties on +5V_USB, at D5.5 and across J5)
    def protect(m):
        blk = m.group(0)
        net = re.search(r"\(net ([^)]*)\)", blk).group(1).strip('"')
        return blk.replace("(type route)", "(type protect)") if (net in PROTECT_NETS or plane_keepouts) else blk
    s, nw = re.subn(r"\(wire (?:\([^()]*\)|[^()])*?\(net [^)]*\)\s*\(type route\)\)", protect, s)
    s, nv = re.subn(r"\(via \"[^\"]*\" [-\d. ]+\s*\(net [^)]*\)\s*\(type route\)\)", protect, s)
    np_ = s.count("(type protect)")
    report.append("protected %d pre-placed wire and via item(s)" % np_)

    # 4. classes
    nets = nets_of(s)
    fine = sorted(n for n, pins in nets.items()
                  if any(p.split("-")[0] in FINE_PARTS for p in pins))
    claimed = {n for c in CLASSES for n in c[5]}
    fine_only = [n for n in fine if n not in claimed]
    classes = list(CLASSES) + [("FINE", 100, 150, "Via[0-3]_500:300_um", None, fine_only)]
    claimed |= set(fine_only)
    rest = sorted(n for n in nets if n not in claimed)
    classes.append(("kicad_default", 150, 150, "Via[0-3]_600:300_um", None, rest))
    txt = []
    for name, w, c, via, layers, members in classes:
        members = [n for n in members if n in nets]
        if not members:
            continue
        circuit = '        (use_via "%s")\n' % via
        if layers:
            circuit += "        (use_layer %s)\n" % " ".join(layers)
        q = lambda n: '"%s"' % n if re.search(r'[\s()]', n) else n
        txt.append("    (class %s %s\n      (circuit\n%s      )\n      (rule\n        (width %d)\n"
                   "        (clearance %d)\n      )\n    )\n" % (name, " ".join(q(n) for n in members),
                                                             circuit, w, c))
        report.append("class %-13s %3d um / %3d um %-6s %3d net(s)" % (name, w, c, "F only" if layers else "", len(members)))
    a = s.index("(class ")
    b = s.index("  )\n  (wiring")
    s = s[:a] + "".join(txt).lstrip() + s[b:]
    open(dst, "w", encoding="utf-8").write(s)
    for r in report:
        print(r)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--plane-keepouts", action="store_true")
    ap.add_argument("--fid-keepouts", action="store_true")
    ap.add_argument("--fixed", default="")
    a = ap.parse_args()
    main(a.src, a.dst, a.plane_keepouts, a.fid_keepouts, [n for n in a.fixed.split(",") if n])
