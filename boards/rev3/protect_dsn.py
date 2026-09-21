#!/usr/bin/env python3
"""Mark the copper freerouting must not touch, before handing it the DSN.

    ./protect_dsn.py rev3.dsn

KiCad exports every existing wire as `(type route)`, which tells freerouting it
may rip up and re-lay all of it. On this board that is 865 wires including the
decoupling runs, the 29 ground vias sitting on capacitor pads, the nine thermal
vias under U9's exposed pad, and the analog chain - all of it placed
deliberately, and all of it the thing the last several hours were spent
getting right.

So the wiring is split in two:

  fixed   the supply and analog nets. Decoupling is a geometry problem, not a
          connectivity one: a capacitor re-routed "better" by a length-and-via
          cost function is a capacitor that has stopped decoupling. The same
          argument the README makes for keeping SENSE_*/GAIN_*/AMP_*/ADC_* away
          from a grid search applies to any autorouter, freerouting included.

  route   the row, column and bus signals. This is what freerouting is for and
          where it beats the maze router outright, because it can push an
          existing trace aside instead of only ripping it out - which is
          exactly what the 36 unrouted pins on U9 need.

`fix` rather than `protect` is deliberate: it is the Specctra keyword for "do
not move", and it is the one freerouting's own SES writer uses for items it
considers immovable.
"""
import io
import os
import re
import shutil
import sys

# Nets whose copper is placed for a physical reason, not a topological one.
# ADC_A and ADC_B are the two analog inputs and are named explicitly; an
# "ADC_" prefix would also catch ADC_SCK/SDI/SDO/CONV, which are ordinary
# digital signals freerouting is welcome to improve.
FIXED_EXACT = {"GND", "+3.3V", "VCORE", "ROW_VCC", "ADC_AVDD", "VREF",
               "+5V", "+5V_BUS", "+5V_USB", "VREG_LX", "SW_NODE", "FB",
               "VREG_LX"}
FIXED_PREFIX = ()

# Length is not the spec for these, the LAYER is - see below.
ANALOG = ["SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B", "AMP_A",
          "AMP_B", "ADC_A", "ADC_B", "VREF", "XIN", "XOUT"]


FIX_ALL = "--all" in sys.argv   # every wire already on the board is deliberate: fix all of it

def fixed(net):
    return FIX_ALL or net in FIXED_EXACT or net.startswith(FIXED_PREFIX)


def plane_layers(text):
    """Declare every layer that carries a pour `power`, not `signal`.

    This is the thing that stopped freerouting dead, and it is worth writing
    down because nothing about the symptom pointed at it.

    KiCad writes In2.Cu - typed `mixed` in Board Setup - out as
    `(type signal)`, and exports its +3.3V pour as a single board-sized
    rectangle with no antipad windows at all. So freerouting sees a routable
    signal layer that is 100% covered by another net's conduction area.

    The only via in the file is Via[0-3]: F.Cu straight through to B.Cu. Every
    via, for every net except +3.3V, therefore lands inside that obstacle.
    Freerouting could not place a via anywhere on the board, could not change
    layer, and was left grinding on F.Cu alone - which is exactly the "83
    unrouted, runs for hours, completes nothing" this was diagnosed from. B.Cu
    was empty and perfectly routable the whole time; it was simply unreachable.

    In1.Cu was already `power` and was never part of this.

    Calling In2.Cu unroutable contradicts maze_route.py, which does route on
    it - but maze_route gets there by cutting the pour, and freerouting cannot
    cut a plane. For freerouting the layer genuinely is unroutable, so saying
    so costs a routing layer that was never on offer and buys back the via,
    and with it B.Cu. F.Cu + B.Cu over two solid reference planes is also the
    better stackup: every signal keeps an adjacent return path.
    """
    on = set(re.findall(r"\(plane \S+ \(polygon (\S+) ", text))
    hit = []

    def swap(m):
        if m.group(1) in on and m.group(3) != "power":
            hit.append("%s: %s -> power" % (m.group(1), m.group(3)))
            return "(layer %s%s(type power)" % (m.group(1), m.group(2))
        return m.group(0)

    return re.sub(r"\(layer (\S+)(\s*)\(type (\w+)\)", swap, text), hit


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "rev3.dsn"
    text = io.open(path, encoding="utf-8").read()

    # --planes narrows the fixed set to the two plane nets.
    #
    # For a CONTINUATION run this is the right set, and the default is not.
    # FIXED_EXACT also names VCORE, ROW_VCC, VREF, +5V, ADC_AVDD, VREG_LX and
    # friends, which made sense while their only copper was hand-laid. They
    # now carry freerouting's own routing, and VCORE and VREG_LX are among the
    # connections still outstanding - freezing a half-finished net is a good
    # way to guarantee it stays half-finished. Only the plane stitching has to
    # survive, because freerouting believes the pours already connect every
    # same-net pad and would never re-place a stub it removed.
    global FIXED_EXACT
    if "--planes" in sys.argv:
        FIXED_EXACT = {"GND", "+3.3V"}

    text, planes = plane_layers(text)
    for h in planes:
        print("  plane layer %s" % h)
    if not planes:
        print("  plane layers already declared power")

    # How much to freeze. Freerouting's fixed states run
    # UNFIXED < SHOVE_FIXED < USER_FIXED < SYSTEM_FIXED, and anything at
    # SHOVE_FIXED or above can be neither ripped up NOR shoved aside. There is
    # no soft setting to fall back to, so the only lever is HOW MUCH is fixed -
    # and freezing 170 of 176 items, nearly all of them clustered around the
    # ICs where the escape paths are, walls pads in and the router retries them
    # forever. That is the "stuck on the routes you told it not to touch"
    # failure, and it is the second time over-protection has caused it.
    #
    #   (default)   fix every wire and via on the supply/analog nets
    #   --minimal   fix only the VIAS. This is the one to reach for.
    #   --none      fix nothing; re-lay decoupling afterwards
    #
    # --minimal is the useful middle. `(type route)` does not mean "delete" -
    # it means the router may rip up and re-lay provided the net still ends up
    # connected - so freeing the 103 wires costs nothing that cannot be
    # rebuilt, while the 73 vias stay as plane anchors. Keeping those matters
    # for a reason that is not obvious: they are what makes GND and +3.3V read
    # as already connected. Free them and freerouting sees two enormous
    # unrouted nets and starts wiring ground pads to each other with long F.Cu
    # tracks instead of dropping vias to the plane - which is exactly what
    # stitch_gnd.py was written to clean up after last time.
    mode = ("none" if "--none" in sys.argv else
            "minimal" if "--minimal" in sys.argv else "full")

    counts = {"fix": 0, "route": 0}
    nets_fixed = set()

    # Match the (net X)(type route) pair, not the whole wire. A wire's path
    # wraps across lines when it has many points, so a line-bounded pattern
    # silently missed 185 of the 480 - and a half-protected board is worse
    # than an unprotected one, because the gaps are invisible.
    kinds = {"wire": 0, "via": 0}

    def swap(m):
        net = m.group(1)
        # Which element are we inside? Whichever opener is nearer behind us.
        s = m.string
        is_via = s.rfind("(via ", 0, m.start()) > s.rfind("(wire ", 0, m.start())
        keep = (mode == "full" or (mode == "minimal" and is_via))
        if keep and fixed(net):
            counts["fix"] += 1
            kinds["via" if is_via else "wire"] += 1
            nets_fixed.add(net)
            return "(net %s)(type fix)" % net
        counts["route"] += 1
        return m.group(0)

    text, n = re.subn(r"\(net ([^)]+)\)\(type route\)", swap, text)
    print("  %d wiring item(s) seen, mode=%s" % (n, mode))

    # The analog chain needs a LAYER constraint, not a fixed path. SENSE_A
    # alone is 26.6 mm from U5's common to the amplifier - far too long to
    # hand-lay - but what actually matters is that it stays on F.Cu over the
    # unbroken ground plane rather than diving to B.Cu, which is a constraint
    # freerouting can honour if it is told. So they get their own class with
    # one usable layer.
    analog = [n for n in ANALOG if ("(net " + n) in text]
    cls = ("    (class analog " + " ".join(analog) + chr(10)
           + "      (circuit" + chr(10)
           + "        (use_layer F.Cu)" + chr(10)
           + "      )" + chr(10)
           + "      (rule" + chr(10)
           + "        (width 150)" + chr(10)
           + "      )" + chr(10)
           + "    )" + chr(10))
    # --no-class skips the layer restriction entirely.
    #
    # `use_layer` is valid Specctra but freerouting does not necessarily
    # implement it, and a parser that meets an unknown token inside (network)
    # can abandon the section - no nets, no ratsnest, no routes, which is
    # exactly the symptom it produced. The class also lacked the
    # `(clearance 150)` that kicad_default carries. Constraining the analog
    # layer is worth doing, but not at the cost of the router working at all:
    # route first, then audit which analog nets went to B.Cu and fix those.
    m = re.search(r"^[ 	]*\(class kicad_default", text, re.M)
    if "--no-class" in sys.argv:
        m = None
        print("  analog layer class SKIPPED (--no-class)")
    if m and analog:
        # Take the names out of kicad_default: a net in two classes is
        # ambiguous, freerouting picks one, and which one is not something to
        # leave to chance when the layer restriction is the whole point. Done
        # by rewriting the token list, not by regex surgery on it - the names
        # contain "+" and "." and a pattern over them is a trap.
        head = text.index("(class kicad_default", m.start())
        body = head + len("(class kicad_default")
        stop = text.index("(circuit", body)
        names = text[body:stop].split()
        kept = [x for x in names if x not in set(analog)]
        wrapped, line = [], "     "
        for x in kept:
            if len(line) + len(x) + 1 > 76:
                wrapped.append(line)
                line = "     "
            line += " " + x
        wrapped.append(line)
        text = text[:body] + chr(10) + chr(10).join(wrapped) + chr(10) + "     " + text[stop:]
        m = re.search(r"^[ 	]*\(class kicad_default", text, re.M)
        print("  removed %d name(s) from kicad_default, %d left"
              % (len(names) - len(kept), len(kept)))
        text = text[:m.start()] + cls + text[m.start():]
        print("  analog class on F.Cu only: %s" % ", ".join(analog))

    print("  fixed  %4d item(s) on %d net(s)  (%d wire, %d via)"
          % (counts["fix"], len(nets_fixed), kinds["wire"], kinds["via"]))
    if nets_fixed:
        print("         %s" % ", ".join(sorted(nets_fixed)))
    print("  free   %4d for freerouting to rip up and improve"
          % counts["route"])
    if not counts["fix"] and mode != "none":
        print("  nothing matched - check the DSN's wire syntax")
        return 1
    shutil.copy(path, path + ".bak")
    io.open(path, "w", encoding="utf-8", newline=chr(10)).write(text)
    print(chr(10) + "wrote %s (original saved as %s.bak)" % (path, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
