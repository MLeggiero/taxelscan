"""eco_rev4.py - turn the routed rev-3 board into rev-4's starting board.

    python3 eco_rev4.py              (KiCad's Python: needs pcbnew)

rev4.kicad_pcb starts as a byte copy of ../rev3/rev3.kicad_pcb. This applies
the netlist change and nothing else, so that what the router does afterwards
is a separate, reviewable step:

  1. rip the copper of every net the change touches:
       gone        VREF ADC_CONV ADC_SCK ADC_SDI ADC_SDO
       re-routed   ADC_A ADC_B AMP_A AMP_B USB_CC_OUT1 USB_CC_OUT2
  2. delete U8, C8, R10, C10, C26 and the U8_escape rule area
  3. re-pin the RP2354A to gen_rev4.py's GPIO map, read from rev4.net:
       U9.41 GPIO27/ADC1 ADC_A        U9.29 GPIO18 USB_CC_OUT1
       U9.43 GPIO29/ADC3 ADC_B        U9.31 GPIO19 USB_CC_OUT2
       U9.27 GPIO16 and U9.28 GPIO17 left unconnected
  4. check every pad on the board against rev4.net, then save

The stubs that served only the deleted pads (their GND, +3.3V and ROW_VCC
fan-outs) are left for route_rev4.py's prune step, which removes what KiCad's
own DRC calls dangling, so nothing is deleted on this script's say-so alone.
"""
import os
import re
import sys

import pcbnew as K

HERE = os.path.dirname(os.path.abspath(__file__))
REV4 = os.path.join(HERE, "..")
BOARD = os.path.join(REV4, "rev4.kicad_pcb")
NET = os.path.join(REV4, "rev4.net")

GONE_PARTS = ("U8", "C8", "R10", "C10", "C26")
GONE_NETS = ("VREF", "ADC_CONV", "ADC_SCK", "ADC_SDI", "ADC_SDO")
REROUTE = ("ADC_A", "ADC_B", "AMP_A", "AMP_B", "USB_CC_OUT1", "USB_CC_OUT2")
GONE_AREAS = ("U8_escape",)


def load_net(path=NET):
    """{(ref, pin): net} from rev4.net."""
    text = open(path, encoding="utf-8").read()
    out = {}
    for name, body in re.findall(r'\(net \(code "\d+"\) \(name "([^"]*)"\)(.*?)\n    \)', text, re.S):
        for ref, pin in re.findall(r'\(ref "([^"]*)"\) \(pin "([^"]*)"\)', body):
            out[(ref, pin)] = name
    return out


def main():
    want = load_net()
    b = K.LoadBoard(BOARD)

    # 1. rip
    ripped = {}
    for t in list(b.GetTracks()):
        n = t.GetNetname()
        if n in GONE_NETS or n in REROUTE:
            ripped[n] = ripped.get(n, 0) + 1
            b.Delete(t)
    for n in sorted(ripped):
        print("  ripped %-12s %3d track/via item(s)" % (n, ripped[n]))

    # 2. delete the parts and the converter's escape area
    for f in list(b.GetFootprints()):
        if f.GetReference() in GONE_PARTS:
            print("  deleted %-4s %s" % (f.GetReference(), f.GetValue()))
            b.Delete(f)
    for z in list(b.Zones()):
        if z.GetIsRuleArea() and z.GetZoneName() in GONE_AREAS:
            print("  deleted rule area %s" % z.GetZoneName())
            b.Delete(z)

    # 3. re-pin to rev4.net, creating the nets the board does not have yet
    changed = 0
    for f in b.GetFootprints():
        ref = f.GetReference()
        for p in f.Pads():
            key = (ref, p.GetNumber())
            if not p.GetNumber() or ref.startswith(("FID", "H")):
                continue
            new = want.get(key, "")
            old = p.GetNetname()
            if old == new:
                continue
            if new:
                ni = b.FindNet(new)
                if ni is None:
                    ni = K.NETINFO_ITEM(b, new)
                    b.Add(ni)
                p.SetNet(ni)
            else:
                p.SetNetCode(0)
            print("  re-pinned %s.%-3s %-12s -> %s" % (ref, p.GetNumber(), old or "(none)", new or "(none)"))
            changed += 1

    # 4. every pad must now agree with rev4.net
    bad = []
    seen = set()
    for f in b.GetFootprints():
        ref = f.GetReference()
        if ref.startswith(("FID", "H")):
            continue
        for p in f.Pads():
            if not p.GetNumber():
                continue
            key = (ref, p.GetNumber())
            seen.add(key)
            if p.GetNetname() != want.get(key, ""):
                bad.append("%s.%s is %r, rev4.net says %r" % (ref, p.GetNumber(), p.GetNetname(), want.get(key, "")))
    missing = sorted(k for k in want if k not in seen)
    if missing:
        bad.append("in rev4.net but not on the board: %s" % missing[:10])
    if bad:
        print("\n%d pad(s) disagree with rev4.net:" % len(bad))
        for x in bad[:20]:
            print("  " + x)
        return 1
    # BOARD.Save() of a board loaded without its project rewrites the .kicad_pro
    # beside it with default rules (see ../../rev3/audit-tools/README.md), so
    # the project and the custom rules are put back byte for byte.
    keep = {}
    for ext in (".kicad_pro", ".kicad_dru"):
        path = os.path.splitext(BOARD)[0] + ext
        keep[path] = open(path, "rb").read()
    b.Save(BOARD)
    for path, data in keep.items():
        open(path, "wb").write(data)
    print("\n%d pad net(s) changed; every pad matches rev4.net; saved %s" % (changed, os.path.basename(BOARD)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
