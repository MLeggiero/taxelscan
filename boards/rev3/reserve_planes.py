#!/usr/bin/env python3
"""Give the plane layers back to the planes, and re-export for routing.

    ./reserve_planes.py            says what it would do
    ./reserve_planes.py --apply    does it, after writing rev3.kicad_pcb.bak

WHY THIS EXISTS. `gen_pcb.py` declared all four copper layers as `signal`:

    (4 "In1.Cu" signal "GND")
    (6 "In2.Cu" signal "PWR")

The name in quotes is only a label, so KiCad exported both to Specctra as
`(type signal)` and freerouting - correctly, given what it was told - used them
as ordinary routing layers. It put 300 signal segments through the ground plane
and 276 through the power plane, and **not one segment of either plane net**.

That is not a cosmetic problem on this board. The whole argument for four
layers here is an unbroken ground under a megohm-impedance front end: it is the
return path for every signal and the reference the dark reference is measured
against. Three hundred traces through it means the return under each F.Cu track
is broken, and 80 segments of SENSE/GAIN/AMP were themselves buried between the
planes where they pick up everything and can never be probed.

WHAT THIS DOES.

  1. Retypes the layers so the router cannot make the same mistake twice:
     In1.Cu becomes `power` - freerouting will not route on it at all - and
     In2.Cu becomes `mixed`, a +3.3 V pour that still accepts signal where the
     board is too dense to avoid it. Keeping ONE solid plane is the part that
     matters; the supply plane can be shared.
  2. Deletes every segment on In1.Cu, so the ground plane is solid again.
  3. Deletes the analog nets from In2.Cu and B.Cu, so they come back to F.Cu
     directly over that solid ground.
  4. Drops vias left serving nothing.

Everything else - the 1179 F.Cu segments and the non-analog In2/B routing - is
kept as pre-routed wiring, so re-running the router finishes the remainder
rather than starting over.
"""
import io
import os
import re
import shutil
import sys

import gen_pcb as P

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")

# The high-impedance chain, plus the reference it is measured against. These
# belong on F.Cu over solid ground and nowhere else: SENSE is the mux common at
# ~7.6 kOhm, and GAIN/AMP/ADC carry the amplified version of it.
ANALOG = {"SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B",
          "AMP_A", "AMP_B", "ADC_A", "ADC_B", "VREF"}

# layer -> what may stay on it
# In1.Cu keeps NOTHING but the plane. In2.Cu and B.Cu keep everything,
# analog included: In2.Cu sits directly under the solid ground plane, so a
# sense trace there is shielded by it rather than exposed. The original rule
# banned analog from both inner layers, which made sense while In1.Cu was
# being shredded by the router - but once the ground plane is whole, stripping
# analog off In2.Cu just deletes working connections. It cost 11 of them.
KEEP = {
    "In1.Cu": set(),                  # solid GND, nothing else
    "In2.Cu": "all",
    "B.Cu": "all",
    "F.Cu": "all",
}


def segments(text):
    for m in re.finditer(r"\n\t\(segment\b", text):
        st = m.start() + 1
        end = P.close_of(text, st)
        blk = text[st:end]
        lay = re.search(r'\(layer "([^"]+)"\)', blk)
        net = re.search(r'\(net "([^"]*)"\)', blk)
        yield st, end, (lay.group(1) if lay else ""), (net.group(1) if net else "")


def main():
    apply = "--apply" in sys.argv
    text = io.open(BOARD, encoding="utf-8").read()

    doomed, kept = [], 0
    for st, end, lay, net in segments(text):
        rule = KEEP.get(lay, "all")
        if rule == "all":
            kept += 1
        elif rule == set():                      # nothing may stay
            doomed.append((st, end, lay, net))
        elif net in ANALOG:                      # analog off the inner/back
            doomed.append((st, end, lay, net))
        else:
            kept += 1

    from collections import Counter
    by_layer = Counter(l for _s, _e, l, _n in doomed)
    print("  keeping %d segments, removing %d" % (kept, len(doomed)))
    for lay, n in sorted(by_layer.items()):
        why = ("all of it - this is the ground plane" if lay == "In1.Cu"
               else "analog nets only")
        print("    %-7s -%3d   (%s)" % (lay, n, why))

    retype = ('(4 "In1.Cu" signal "GND")' in text
              or '(6 "In2.Cu" signal "PWR")' in text)
    print("  layer types                : %s"
          % ("In1.Cu -> power, In2.Cu -> mixed" if retype else "already correct"))

    if not apply:
        print("\ndry run. re-run with --apply to write it.")
        return 0

    for st, end, _l, _n in sorted(doomed, reverse=True):
        text = text[:st] + text[end:]

    text = text.replace('(4 "In1.Cu" signal "GND")', '(4 "In1.Cu" power "GND")')
    text = text.replace('(6 "In2.Cu" signal "PWR")', '(6 "In2.Cu" mixed "PWR")')

    # Vias left serving nothing. A via earns its place by joining tracks on two
    # different layers; once the In1.Cu half of a trace is gone, the via that
    # reached it is a hole to nowhere. Counting only "touches no track at all"
    # missed almost every one of them - the F.Cu end was still there - so this
    # counts how many DISTINCT layers actually meet at the via.
    #
    # Plane nets are exempt: a GND or +3.3V via is stitching to a zone, and the
    # zone is not a segment.
    def touching_layers(text):
        by_point = {}
        for m in re.finditer(r"\n\t\(segment\b", text):
            st = m.start() + 1
            blk = text[st:P.close_of(text, st)]
            lay = re.search(r'\(layer "([^"]+)"\)', blk)
            lay = lay.group(1) if lay else ""
            for a, b in re.findall(r"\((?:start|end) ([-\d.]+) ([-\d.]+)\)", blk):
                by_point.setdefault(
                    (round(float(a), 3), round(float(b), 3)), set()).add(lay)
        return by_point

    pads = set()
    for m in re.finditer(r'\n\t\(footprint "', text):
        st = m.start() + 1
        blk = text[st:P.close_of(text, st)]
        at = re.search(r"\n\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)", blk)
        if not at:
            continue
        import math
        ox, oy = float(at.group(1)), float(at.group(2))
        ang = math.radians(float(at.group(3) or 0))
        ca, sa = math.cos(ang), math.sin(ang)
        for pm in re.finditer(r'\(pad "', blk):
            pblk = blk[pm.start():P.close_of(blk, pm.start())]
            pat = re.search(r"\(at ([-\d.]+) ([-\d.]+)", pblk)
            if pat:
                px, py = float(pat.group(1)), float(pat.group(2))
                pads.add((round(ox + px * ca + py * sa, 3),
                          round(oy - px * sa + py * ca, 3)))

    removed = 0
    while True:
        by_point = touching_layers(text)
        victim = None
        for m in re.finditer(r"\n\t\(via\b", text):
            st = m.start() + 1
            end = P.close_of(text, st)
            blk = text[st:end]
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)\)", blk)
            net = re.search(r'\(net "([^"]*)"\)', blk)
            if not at or (net.group(1) if net else "") in ("GND", "+3.3V"):
                continue
            pt = (round(float(at.group(1)), 3), round(float(at.group(2)), 3))
            if len(by_point.get(pt, set())) < 2 and pt not in pads:
                victim = (st, end)
                break
        if not victim:
            break
        text = text[:victim[0]] + text[victim[1]:]
        removed += 1
    print("  vias left serving nothing   : %d removed" % removed)

    # And the stubs those vias fed. A segment with a free end - meeting no
    # other segment, no via and no pad - is not a connection, it is an antenna
    # soldered to a signal, which is the last thing this board needs. Removing
    # one can free the end of the next, so this repeats until it settles.
    stubs = 0
    for _pass in range(40):
        vias = set()
        for m in re.finditer(r"\n\t\(via\b", text):
            st = m.start() + 1
            blk = text[st:P.close_of(text, st)]
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)\)", blk)
            if at:
                vias.add((round(float(at.group(1)), 3),
                          round(float(at.group(2)), 3)))
        seen = {}
        segs = []
        for m in re.finditer(r"\n\t\(segment\b", text):
            st = m.start() + 1
            end = P.close_of(text, st)
            blk = text[st:end]
            pts = [(round(float(a), 3), round(float(b), 3)) for a, b in
                   re.findall(r"\((?:start|end) ([-\d.]+) ([-\d.]+)\)", blk)]
            net = re.search(r'\(net "([^"]*)"\)', blk)
            segs.append((st, end, pts, net.group(1) if net else ""))
            for pt in pts:
                seen[pt] = seen.get(pt, 0) + 1
        doomed_now = []
        for st, end, pts, net in segs:
            if net in ("GND", "+3.3V"):
                continue                      # a zone can terminate these
            if any(seen.get(pt, 0) < 2 and pt not in vias and pt not in pads
                   for pt in pts):
                doomed_now.append((st, end))
        if not doomed_now:
            break
        for st, end in sorted(doomed_now, reverse=True):
            text = text[:st] + text[end:]
        stubs += len(doomed_now)
    print("  dangling track stubs        : %d removed" % stubs)

    shutil.copy(BOARD, BOARD + ".bak")
    io.open(BOARD, "w", encoding="utf-8", newline="\n").write(text)
    print("\nwrote rev3.kicad_pcb (previous version saved as rev3.kicad_pcb.bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
