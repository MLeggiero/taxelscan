#!/usr/bin/env python3
"""Widen the traces that carry other boards' current.

    ./widen_power.py            says what it would do
    ./widen_power.py --apply    does it, after writing rev3.kicad_pcb.bak

The router laid everything at its default 0.15 mm. For every net on this board
that is fine - a driven row sources 106 uA - with one exception.

`+5V_BUS` passes straight through from J3 to J4, so on the board nearest the
supply it carries the OTHER SEVEN BOARDS' current as well as its own: roughly
8 x 50 mA = 400 mA. At 0.15 mm and 1 oz copper that is about 3.3 mOhm/mm, so a
~45 mm pass-through drops ~59 mV, and the chain as a whole loses ~200 mV before
the last board's regulator sees it. It works - the TLV62569 holds down to
2.5 V - but it is 56% of the trace's thermal rating for no reason, and the
widening is free everywhere the board has room.

Width only. Nothing moves, no topology changes, and any segment that cannot be
widened without breaking clearance is left alone and reported.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

import gen_pcb as P
import gen_schematic as S

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")

# net -> width in mm. Only nets whose current is not their own.
WIDEN = {"+5V_BUS": 0.25, "+5V": 0.25}


def offenders(path):
    """Coordinates of widened segments that DRC now complains about."""
    cli = S.find_cli()
    tmp = tempfile.mkdtemp(prefix="rev3o")
    rpt = os.path.join(tmp, "drc.rpt")
    subprocess.run([cli, "pcb", "drc", "--output", rpt, "--severity-all",
                    "--refill-zones", path], capture_output=True, text=True)
    text = io.open(rpt, encoding="utf-8").read() if os.path.exists(rpt) else ""
    shutil.rmtree(tmp, ignore_errors=True)
    bad = set()
    NL = chr(10)
    pat = ("^" + re.escape("[clearance]") + "[^" + NL + "]*" + NL
           + "[^" + NL + "]*" + NL
           + "((?:\\s*@[^" + NL + "]*" + NL + ")+)")
    for m in re.finditer(pat, text, re.M):
        for x, y, desc in re.findall(r"@\((-?[\d.]+) mm, (-?[\d.]+) mm\): (.*)",
                                     m.group(1)):
            if "Track [+5V" in desc:
                bad.add((round(float(x), 3), round(float(y), 3)))
    return bad


def retrace(text, widths, skip=frozenset()):
    out, i, n = [], 0, 0
    for m in re.finditer(r"\n\t\(segment\b", text):
        st = m.start() + 1
        if st < i:
            continue
        end = P.close_of(text, st)
        blk = text[st:end]
        net = re.search(r'\(net "([^"]*)"\)', blk)
        want = widths.get(net.group(1) if net else "")
        st_m = re.search(r"\(start ([-\d.]+) ([-\d.]+)\)", blk)
        here = ((round(float(st_m.group(1)), 3), round(float(st_m.group(2)), 3))
                if st_m else None)
        if want and here not in skip:
            new, k = re.subn(r"\(width [\d.]+\)", "(width %g)" % want, blk, 1)
            if k and new != blk:
                blk, _ = new, None
                n += 1
        out.append(text[i:st])
        out.append(blk)
        i = end
    out.append(text[i:])
    return "".join(out), n


def violations(path):
    cli = S.find_cli()
    tmp = tempfile.mkdtemp(prefix="rev3w")
    rpt = os.path.join(tmp, "drc.rpt")
    subprocess.run([cli, "pcb", "drc", "--output", rpt, "--severity-all",
                    "--refill-zones", path], capture_output=True, text=True)
    text = io.open(rpt, encoding="utf-8").read() if os.path.exists(rpt) else ""
    shutil.rmtree(tmp, ignore_errors=True)
    return len(re.findall(r"^\[clearance\]", text, re.M)), \
        len(re.findall(r"^\[track_width\]", text, re.M))


def main():
    apply = "--apply" in sys.argv
    text = io.open(BOARD, encoding="utf-8").read()

    before = violations(BOARD)
    print("  before: %d clearance, %d track_width violation(s)" % before)

    wide, n = retrace(text, WIDEN)
    print("  widening %d segment(s) on %s to %.2f mm"
          % (n, ", ".join(sorted(WIDEN)), list(WIDEN.values())[0]))

    # The trial board must be able to find this board's design rules, and
    # KiCad locates the project by BASENAME - so the copy has to be called
    # rev3.kicad_pcb and sit next to a copy of rev3.kicad_pro. Getting either
    # wrong makes KiCad fall back to its own defaults and flag all 199
    # existing 0.15 mm tracks as too narrow, which drowns the real answer.
    tmp = tempfile.mkdtemp(prefix="rev3w")
    shutil.copy(os.path.join(HERE, "rev3.kicad_pro"),
                os.path.join(tmp, "rev3.kicad_pro"))
    trial = os.path.join(tmp, "rev3.kicad_pcb")
    io.open(trial, "w", encoding="utf-8", newline="\n").write(wide)
    after = violations(trial)
    shutil.rmtree(tmp, ignore_errors=True)
    print("  after:  %d clearance, %d track_width violation(s)" % after)

    # A few segments cannot take the extra copper at any width - the count is
    # identical at 0.22 and 0.30 mm, so it is those specific spots and not the
    # width that is the problem. Find them in the DRC report and leave just
    # those alone, rather than abandoning the widening for all 27.
    skip = set()
    for _ in range(6):
        if after[0] <= before[0]:
            break
        bad = offenders(trial)
        if not bad or bad <= skip:
            break
        skip |= bad
        print("  %d segment(s) cannot widen - left at 0.15 mm" % len(skip))
        wide, n = retrace(text, WIDEN, skip=skip)
        io.open(trial, "w", encoding="utf-8", newline="\n").write(wide)
        after = violations(trial)
        print("  after:  %d clearance, %d track_width violation(s)" % after)

    if after[0] > before[0]:
        print("\n  still %d new clearance violation(s). Nothing written."
              % (after[0] - before[0]))
        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    print("  widened %d segment(s), %d left narrow" % (n, len(skip)))
    if not apply:
        print("\ndry run. re-run with --apply to write it.")
        return 0

    shutil.copy(BOARD, BOARD + ".bak")
    io.open(BOARD, "w", encoding="utf-8", newline="\n").write(wide)
    print("\nwrote rev3.kicad_pcb (previous version saved as rev3.kicad_pcb.bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
