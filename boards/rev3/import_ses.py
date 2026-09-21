#!/usr/bin/env python3
"""Apply a freerouting Specctra session (.ses) to the board.

    ./import_ses.py            says what it would do, changes nothing
    ./import_ses.py --apply    does it, after writing rev3.kicad_pcb.bak

pcbnew does this from File > Import > Specctra Session, and kicad-cli cannot -
there is no `pcb import` verb in KiCad 10, the same gap that makes DSN export
GUI-only. Doing it here means the routing can be applied, measured and checked
without a round trip through the GUI.

WHAT IT KEEPS. Existing tracks and vias are REPLACED, not added to: a session
is the router's complete answer for the nets it touched, so merging it with the
previous attempt would double up every wire. Footprints, zones, the outline and
everything else are untouched.

COORDINATES. Specctra counts in `(resolution um 10)` units - tenths of a
micron, so 1 unit is 0.0001 mm - and its Y axis points the opposite way to
KiCad's, which is why every y is negated rather than merely scaled. Getting
that backwards mirrors the whole board about the x axis, which is obvious on a
render and silent in a netlist.

LAYERS. The session names layers as the DSN did, so `GND` and `PWR` are the
board's In1.Cu and In2.Cu.
"""
import io
import os
import re
import shutil
import sys
import uuid as _uuid

import gen_pcb as P

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")
SES = os.path.join(HERE, "rev3.ses")

LAYER = {"F.Cu": "F.Cu", "GND": "In1.Cu", "PWR": "In2.Cu", "B.Cu": "B.Cu"}
NL, TAB = chr(10), chr(9)


def parse(text):
    """[(net, layer, width_mm, [(x,y)...])], [(net, x, y)] from a session."""
    unit = 10.0
    m = re.search(r"\(resolution\s+um\s+([\d.]+)\)", text)
    if m:
        unit = float(m.group(1))
    scale = 0.001 / unit                       # -> mm

    body = text[text.index("(network_out"):]
    wires, vias = [], []
    for nm in re.finditer(r'\(net\s+"?([^"\s)]+)"?', body):
        start = nm.end()
        nxt = body.find("(net ", start)
        chunk = body[start:nxt if nxt > 0 else len(body)]
        net = nm.group(1)
        for pm in re.finditer(r"\(path\s+(\S+)\s+(\d+)([^)]*)\)", chunk):
            nums = [int(v) for v in pm.group(3).split()]
            pts = [(nums[i] * scale, -nums[i + 1] * scale)
                   for i in range(0, len(nums) - 1, 2)]
            wires.append((net, pm.group(1), int(pm.group(2)) * scale, pts))
        for vm in re.finditer(r'\(via\s+"[^"]*"\s+(-?\d+)\s+(-?\d+)', chunk):
            vias.append((net, int(vm.group(1)) * scale,
                         -int(vm.group(2)) * scale))
    return wires, vias


def emit(wires, vias):
    out = []
    for net, lay, w, pts in wires:
        layer = LAYER.get(lay, lay)
        for a, b in zip(pts, pts[1:]):
            # Compare at the precision actually WRITTEN, not full float
            # precision. Two session points a few nanometres apart survive an
            # `a == b` test and then round to identical "%g" coordinates - a
            # zero-length track. freerouting's combine_at_end() then finds that
            # trace as its own neighbour to merge with and recurses until the
            # JVM stack dies, which is exactly how a StackOverflowError comes
            # back out of a board this router itself produced.
            if ("%g %g" % a) == ("%g %g" % b):
                continue
            out.append(NL.join([
                TAB + "(segment",
                TAB * 2 + "(start %g %g)" % a,
                TAB * 2 + "(end %g %g)" % b,
                TAB * 2 + "(width %g)" % w,
                TAB * 2 + '(layer "%s")' % layer,
                TAB * 2 + '(net "%s")' % net,
                TAB * 2 + '(uuid "%s")' % _uuid.uuid4(),
                TAB + ")"]))
    for net, x, y in vias:
        out.append(NL.join([
            TAB + "(via",
            TAB * 2 + "(at %g %g)" % (x, y),
            TAB * 2 + "(size 0.6)",
            TAB * 2 + "(drill 0.3)",
            TAB * 2 + '(layers "F.Cu" "B.Cu")',
            TAB * 2 + '(net "%s")' % net,
            TAB * 2 + '(uuid "%s")' % _uuid.uuid4(),
            TAB + ")"]))
    return out


def strip(text, nets=None):
    """Remove existing tracks and vias, back to front.

    `nets` limits the removal to copper on those nets; None means all of it.
    """
    spans = []
    for pat in (r"\n\t\(segment\b", r"\n\t\(via\b"):
        for m in re.finditer(pat, text):
            st = m.start() + 1
            end = P.close_of(text, st)
            if nets is not None:
                nm = re.search(r'\(net "([^"]*)"', text[st:end])
                if not nm or nm.group(1) not in nets:
                    continue
            spans.append((st, end))
    for st, end in sorted(spans, reverse=True):
        text = text[:st] + text[end:]
    return text, len(spans)


def supersede_set(wires, vias):
    """Nets whose existing copper the session is entitled to replace.

    The module docstring's "a session is the router's complete answer" is true
    only when freerouting was free to move everything. It was not: protect_dsn
    marks the supply and analog copper `(type fix)`, and freerouting OMITS
    fixed items from its session output entirely - this session carries 47
    nets, 93 wires and 10 vias, with GND, +3.3V, VCORE, VREF and XIN absent
    altogether. Replacing wholesale would therefore delete 97 wires and all 73
    plane-stitching vias and put back nothing, silently un-decoupling the board
    and re-floating both planes.

    So supersede exactly the nets freerouting was allowed to rip up - the ones
    in the session that protect_dsn did not fix - and keep the rest. There is
    no double-up risk: anything fixed is by definition not in the session.
    """
    import protect_dsn as PD
    seen = {w[0] for w in wires} | {v[0] for v in vias}
    return {n for n in seen if not PD.fixed(n)}


def main():
    apply = "--apply" in sys.argv
    if not os.path.exists(SES):
        sys.exit("no rev3.ses next to the board")
    if os.path.getmtime(SES) < os.path.getmtime(BOARD):
        print("  NOTE: rev3.ses is OLDER than rev3.kicad_pcb - is it stale?")

    wires, vias = parse(io.open(SES, encoding="utf-8").read())
    text = io.open(BOARD, encoding="utf-8").read()

    from collections import Counter
    bylay = Counter(LAYER.get(l, l) for _n, l, _w, _p in wires)
    segs = sum(max(0, len(p) - 1) for _n, _l, _w, p in wires)
    print("  session: %d path(s) -> %d segment(s), %d via(s), %d net(s)"
          % (len(wires), segs, len(vias), len({w[0] for w in wires})))
    for lay, n in sorted(bylay.items()):
        print("     %-7s %d path(s)" % (lay, n))
    on_gnd = bylay.get("In1.Cu", 0)
    print("  ground plane: %s"
          % ("CLEAN - nothing routed on it" if not on_gnd
             else "*** %d path(s) ON THE GROUND PLANE ***" % on_gnd))

    merge = "--merge" in sys.argv
    add = "--add" in sys.argv          # keep every existing track/via, only append the session's
    only = set() if add else supersede_set(wires, vias) if merge else None
    if merge:
        print("  MERGE: keeping every fixed net's copper; superseding only")
        print("         %s" % (", ".join(sorted(only)) or "(nothing)"))
    _stripped, n_old = strip(text, only)
    print("  removing %d existing track/via object(s)%s"
          % (n_old, " on those nets" if merge else " (full replace)"))

    if not apply:
        print(NL + "dry run. re-run with --apply to write it.")
        return 0

    text, _ = strip(text, only)
    blocks = emit(wires, vias)
    tail = text.rindex(")")
    text = text[:tail] + NL.join(blocks) + NL + text[tail:]

    shutil.copy(BOARD, BOARD + ".bak")
    io.open(BOARD, "w", encoding="utf-8", newline=NL).write(text)
    print(NL + "wrote rev3.kicad_pcb (previous version saved as .bak)")
    return P.check()


if __name__ == "__main__":
    sys.exit(main())
