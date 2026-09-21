#!/usr/bin/env python3
"""Apply BOM changes to a hand-placed board WITHOUT disturbing the placement.

    ./sync_pcb.py            says what it would do, changes nothing
    ./sync_pcb.py --apply    does it, after writing rev3.kicad_pcb.bak

KiCad's own Tools > Update PCB from Schematic is the usual way to do this, and
on this board it throws the layout away. The reason is that `gen_pcb.py` writes
no `(path ...)` in its footprints, so nothing on the board is tied to a symbol
in the sheet; KiCad falls back to matching by reference, decides it is looking
at a different board, and re-adds everything.

Rather than hand the placement back to a dialog, this does the three things
that actually need doing and nothing else:

    swap    a part whose BOM footprint changed keeps its position, rotation
            and reference, and only its geometry is replaced
    add     a part in the BOM but not on the board is placed in free space
    drop    a part on the board but not in the BOM is removed

Everything it does not name is copied through byte for byte, so a routed track
or a nudged capacitor is untouched. Placement is human work on this board - the
hand-placed layout is 1925 mm2 against the generator's 3300 - and a script that
can only edit is safer than one that regenerates.
"""
import io
import os
import re
import shutil
import sys

import gen_pcb as P
import gen_schematic as S

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")

# One filled plane, written out longhand. Built with chr(10)/chr(9) rather
# than escapes so the file survives being edited through a shell heredoc.
NL, TAB = chr(10), chr(9)
ZONE_TEMPLATE = NL.join([
    TAB + "(zone",
    TAB * 2 + '(net "%s")',
    TAB * 2 + '(net_name "%s")',
    TAB * 2 + '(layer "%s")',
    TAB * 2 + "%s",
    TAB * 2 + "(hatch edge 0.5)",
    TAB * 2 + "(connect_pads",
    TAB * 3 + "(clearance 0.25)",
    TAB * 2 + ")",
    TAB * 2 + "(min_thickness 0.2)",
    TAB * 2 + "(fill yes",
    TAB * 3 + "(thermal_gap 0.5)",
    TAB * 3 + "(thermal_bridge_width 0.5)",
    TAB * 3 + "(island_removal_mode 0)",
    TAB * 2 + ")",
    TAB * 2 + "(polygon",
    TAB * 3 + "(pts",
    TAB * 4 + "%s",
    TAB * 3 + ")",
    TAB * 2 + ")",
    TAB + ")",
])


def board_extent(text):
    """(x1, y1, x2, y2) of the Edge.Cuts outline, in board coordinates."""
    xs, ys = [], []
    for m in re.finditer(r"\(gr_(line|rect|arc|poly)", text):
        blk = text[m.start():P.close_of(text, m.start())]
        if '(layer "Edge.Cuts")' not in blk:
            continue
        for a, b in re.findall(r"\((?:start|end|mid|xy) ([-\d.]+) ([-\d.]+)\)", blk):
            xs.append(float(a))
            ys.append(float(b))
    return (min(xs), min(ys), max(xs), max(ys))


def footprints(text):
    """[(start, end, ref, libname, x, y, rot)] for every footprint on the board."""
    out = []
    for m in re.finditer(r'\n\t\(footprint "([^"]*)"', text):
        st = m.start() + 1
        end = P.close_of(text, st)
        blk = text[st:end]
        ref = re.search(r'\(property "Reference" "([^"]*)"', blk)
        name = ref.group(1) if ref else "?"
        # Tolerant of indentation, and NOT tolerant of absence. This used to
        # demand exactly two tabs and fall back to (0.0, 0.0) when it did not
        # find them - and `gen_pcb.place()` writes three, so every footprint
        # added by sync_pcb.py parsed as sitting at the board origin. Nothing
        # complained: maze_route.py then routed around parts that were not
        # where it thought, dropped vias on top of their real pads, and
        # reported "endpoint cell blocked" for pins it believed were 100 mm
        # away. A position that cannot be read is a broken board file, so say
        # so rather than inventing one.
        at = re.search(r'\n\t{1,3}\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)',
                       blk)
        if not at:
            raise SystemExit("footprint %s has no readable (at x y) - the "
                             "board file is malformed" % name)
        out.append((st, end, name, m.group(1),
                    float(at.group(1)), float(at.group(2)),
                    float(at.group(3)) if at.group(3) else 0.0))
    return out


# Where a newly added part WANTS to be, in board coordinates. The packer
# searches outward from here for the first free spot, so these only have to be
# roughly right - but they have to exist, because a decoupling capacitor that
# lands wherever there happened to be room is not a decoupling capacitor. Each
# one is the pin it serves; see README.md's adjacency table for why.
SEED = {
    # (x, y, rotation), from place_new.py: the nearest position to each part's
    # pin that clears BOTH the courtyards and the copper already routed.
    #
    # These three are bulk and HF on the two 5 V rails that arrived with no
    # capacitor at all. 3.6-6.5 mm from the connector pin is fine here in a
    # way it would not be at a QFN supply pin: bulk holds a rail up over
    # microseconds and does not care where it sits, so long as it is on the
    # right side of the diode.
    "C37": (31.75, 24.25, 90),   # J5 VBUS bulk,           3.60 mm
    "C38": (35.75, 21.50, 0),    # J5 VBUS high frequency, 6.48 mm
    "C39": (10.75, 29.25, 0),    # J4 harness-entry bulk,  6.05 mm
}

def main():
    apply = "--apply" in sys.argv
    text = io.open(BOARD, encoding="utf-8").read()
    bom = S.load_bom()
    nets = S.load_net()
    fps = footprints(text)
    on_board = {f[2]: f for f in fps}

    # A board footprint's name may or may not carry its library: gen_pcb.py
    # writes "Lib:Name" and so does pcbnew on save, but a bare name appears in
    # older files. Compare on the part after the colon either way, or every
    # single component reads as changed.
    bare = lambda s: s.split(":", 1)[-1]

    swaps, adds, drops = [], [], []
    for ref, (value, fpname) in sorted(bom.items()):
        if ref not in on_board:
            adds.append(ref)
        elif bare(on_board[ref][3]) != bare(fpname):
            swaps.append(ref)
    for f in fps:
        if f[2] not in bom:
            drops.append(f[2])

    # Pad nets, for parts that are staying put. A net can change without the
    # footprint changing - grounding the connector shells did exactly that -
    # and rewriting one pad's net moves nothing on the board.
    renets = []
    for st, end, ref, lib, x, y, rot in fps:
        if ref in drops or ref in swaps or ref not in bom:
            continue
        blk = text[st:end]
        for m in re.finditer(r'\(pad "([^"]*)"', blk):
            pend = P.close_of(blk, m.start())
            body = blk[m.start():pend]
            have = re.search(r'\(net "([^"]*)"\)', body)
            have = have.group(1) if have else None
            wants = nets.get(ref, {}).get(m.group(1))
            if wants and have != wants and not (have or "").startswith(
                    "unconnected-"):
                renets.append((ref, m.group(1), have, wants))

    # Plane outlines, against the board outline. KiCad clips a fill to the edge
    # so the copper comes out right either way, but a zone whose polygon is
    # 29 mm taller than the board it is on is a trap for whoever edits next -
    # and that is exactly what shrinking the board by hand left behind.
    ex = board_extent(text)
    zones = []
    for zm in re.finditer(r"\n\t\(zone\b", text):
        st = zm.start() + 1
        blk = text[st:P.close_of(text, st)]
        pm = re.search(r"\(pts\n?(?:\s*\(xy [-\d.]+ [-\d.]+\)\s*)+\)", blk) \
            or re.search(r"\(pts(?:\s*\(xy [-\d.]+ [-\d.]+\))+\s*\)", blk)
        if not pm:
            continue
        pts = [(float(a), float(b))
               for a, b in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", pm.group(0))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        want = (ex[0] + P.EDGE, ex[1] + P.EDGE, ex[2] - P.EDGE, ex[3] - P.EDGE)
        got = (min(xs), min(ys), max(xs), max(ys))
        if max(abs(a - b) for a, b in zip(got, want)) > 0.01:
            net = re.search(r'\(net_name "([^"]*)"\)', blk)
            zones.append((st + pm.start(), st + pm.end(),
                          net.group(1) if net else "?", got, want))

    for _s, _e, net, got, want in zones:
        print("  zone  %-7s %.1f x %.1f -> %.1f x %.1f mm"
              % (net, got[2] - got[0], got[3] - got[1],
                 want[2] - want[0], want[3] - want[1]))

    # A plane that is not there at all. Selecting a zone and deleting it is one
    # keystroke in pcbnew and leaves no trace in the netlist, so nothing else
    # in this toolchain would notice: DRC on a board with no ground plane
    # reports unconnected items, which is exactly what an unrouted board
    # reports anyway.
    have = {n for _s, _e, n, _g, _w in zones}
    for m in re.finditer(r'\(net_name "([^"]*)"\)', text):
        have.add(m.group(1))
    # Parse the zones properly rather than pattern-matching across them. The
    # regex this replaces looked for `(net_name "GND")`, which is the form
    # pcbnew writes and NOT the form this toolchain writes - every zone here
    # says `(net "GND")`. So it reported the +3.3V plane as missing on a board
    # that has it, and `--apply` would have poured a SECOND one on top: two
    # overlapping zones on the same net and layer, which fills without
    # complaint and is invisible in every check downstream.
    present = set()
    for m in re.finditer(r"\n\t\(zone\b", text):
        st = m.start() + 1
        blk = text[st:P.close_of(text, st)]
        nm = re.search(r'\(net(?:_name)? "([^"]*)"\)', blk)
        ly = re.search(r'\(layers? "([^"]+)"\)', blk)
        if nm and ly:
            present.add((ly.group(1), nm.group(1)))
    missing = [(lay, net) for lay, net in (("In1.Cu", "GND"), ("In2.Cu", "+3.3V"))
               if (lay, net) not in present]
    for lay, net in missing:
        print("  zone  %-7s MISSING on %s - will be re-poured" % (net, lay))

    # 3D models KiCad does not ship, pointed at the nearest body it does. This
    # is the picture only - nothing here reaches the netlist, the BOM or any
    # fabrication output - but the two parts with no model were the MCU and the
    # USB port, so the board rendered with a hole where each should be.
    models = sum(text.count(k) for k in P.MODEL_FIX)

    if not (swaps or adds or drops or renets or models or zones or missing):
        print("board already matches BOM.csv - nothing to do")
        return 0
    if models:
        print("  model %d reference(s) -> a body KiCad actually ships" % models)

    for ref, pin, have, wants in renets:
        print("  net   %-5s pad %-3s %s -> %s" % (ref, pin, have or "<none>", wants))

    for ref in swaps:
        print("  swap  %-5s %s -> %s"
              % (ref, on_board[ref][3], bom[ref][1].split(":", 1)[1]))
    for ref in drops:
        print("  drop  %-5s %s" % (ref, on_board[ref][3]))
    for ref in adds:
        print("  add   %-5s %s" % (ref, bom[ref][1].split(":", 1)[1]))

    # Where the new parts can go: anywhere free, preferring the space a dropped
    # part just vacated, since that is where its replacement belongs.
    boxes = {}
    for st, end, ref, lib, x, y, rot in fps:
        if ref in drops:
            continue
        fpfile = P.find_footprint(bom[ref][1]) if ref in bom else None
        if fpfile:
            boxes[ref] = P.courtyard(fpfile, x - P.OX, y - P.OY, rot)
    hints = [(on_board[r][4] - P.OX, on_board[r][5] - P.OY) for r in drops]

    # The board is whatever the outline says it is, NOT gen_pcb's BW/BH - those
    # are the generated size, and this board has been resized by hand since.
    # Using the constants put four of the new parts off the edge.
    bw, bh = ex[2] - ex[0], ex[3] - ex[1]

    def off_board(box, pad=0.15):
        if box is None:
            return False
        if (box[0] < pad or box[1] < pad
                or box[2] > bw - pad or box[3] > bh - pad):
            return True
        return P.hits(box, boxes.values()) and True

    placed, parked = {}, []
    for i, ref in enumerate(adds):
        if ref in SEED:
            # Authoritative, not a hint. These came from place_new.py, which
            # tests the candidate against the COPPER as well as the
            # courtyards; the spiral below knows only about courtyards, and
            # letting it "improve" on a copper-verified position is how the
            # first attempt put 14 fresh pads on top of other nets' tracks.
            x, y, rot = SEED[ref]
            boxes[ref] = P.courtyard(P.find_footprint(bom[ref][1]), x, y, rot)
            placed[ref] = (x, y, rot)
            print("        %s at (%.2f, %.2f) rot %d   [copper-verified]"
                  % (ref, x, y, rot))
            continue
        if hints:
            hx, hy = hints[min(i, len(hints) - 1)]
        else:
            hx, hy = bw / 2, bh / 2
        for dx, dy in [(0, 0)] + [(a * 0.5, b * 0.5)
                                  for r in range(1, 120)
                                  for a, b in ((0, r), (0, -r), (r, 0), (-r, 0),
                                               (r, r), (-r, r), (r, -r), (-r, -r))]:
            x, y = round((hx + dx) * 4) / 4, round((hy + dy) * 4) / 4
            b = P.courtyard(P.find_footprint(bom[ref][1]), x, y, 0)
            if not off_board(b):
                boxes[ref], placed[ref] = b, (x, y, 0)
                break
        else:
            # No free space inside the outline. Park it in a row just off the
            # right edge rather than refuse: KiCad's own update dialog drops new
            # parts at the origin for the same reason, and a part sitting beside
            # the board is obvious to drag in. The packer only models
            # axis-aligned courtyard boxes, so a human will often find room it
            # cannot - between two IC pad rows, say.
            parked.append(ref)
            x, y = bw + 3.0, 2.0 + 2.5 * len(parked)
            boxes[ref] = P.courtyard(P.find_footprint(bom[ref][1]), x, y, 0)
            placed[ref] = (x, y, 0)
        print("        %s at (%.2f, %.2f)%s"
              % (ref, placed[ref][0], placed[ref][1],
                 "   PARKED - no room on the board, place by hand"
                 if ref in parked else ""))

    if not apply:
        print("\ndry run. re-run with --apply to write it.")
        return 0

    # Rebuild the file back to front, so earlier spans stay valid.
    todo = {r for r, _, _, _ in renets}
    edits = []
    for st, end, ref, lib, x, y, rot in fps:
        if ref in todo and ref not in drops and ref not in swaps:
            blk, out, i = text[st:end], [], 0
            for m in re.finditer(r'\(pad "([^"]*)"', blk):
                if m.start() < i:
                    continue
                pend = P.close_of(blk, m.start())
                body = blk[m.start():pend]
                wants = nets.get(ref, {}).get(m.group(1))
                have = re.search(r'\(net "([^"]*)"\)', body)
                if wants and not (have and have.group(1).startswith("unconnected-")):
                    if have:
                        body = body[:have.start()] + '(net "%s")' % wants \
                               + body[have.end():]
                    else:
                        body = body[:-1].rstrip() + '\n\t\t\t(net "%s")\n\t\t)' % wants
                out.append(blk[i:m.start()])
                out.append(body)
                i = pend
            out.append(blk[i:])
            edits.append((st, end, "".join(out)))
        elif ref in drops:
            edits.append((st, end, ""))
        elif ref in swaps:
            chunk, _ = P.place(P.find_footprint(bom[ref][1]), bom[ref][1], ref,
                               bom[ref][0], x - P.OX, y - P.OY, rot,
                               nets.get(ref, {}))
            # NOT chunk.strip("\t"). The span being replaced begins at the
            # leading tab, so stripping it drops the block to column 0. That
            # still parses, but footprints() looks for "\n\t(footprint", so the
            # swapped part became invisible to the next run - which then took
            # it for missing and appended a second copy.
            edits.append((st, end, chunk))
    for st, end, new in sorted(edits, reverse=True):
        text = text[:st] + new + text[end:]

    tail = text.rindex(")")
    new_blocks = []
    for ref in adds:
        x, y, rot = placed[ref]
        chunk, _ = P.place(P.find_footprint(bom[ref][1]), bom[ref][1], ref,
                           bom[ref][0], x, y, rot, nets.get(ref, {}))
        new_blocks.append(chunk)
    if new_blocks:
        text = text[:tail] + "\n".join(new_blocks) + "\n" + text[tail:]

    if missing:
        ins = text.rindex(")")
        blocks = []
        for lay, net in missing:
            pts = " ".join("(xy %g %g)" % (x, y) for x, y in (
                (ex[0] + P.EDGE, ex[1] + P.EDGE), (ex[2] - P.EDGE, ex[1] + P.EDGE),
                (ex[2] - P.EDGE, ex[3] - P.EDGE), (ex[0] + P.EDGE, ex[3] - P.EDGE)))
            blocks.append(ZONE_TEMPLATE % (net, net, lay, P.uid(), pts))
        text = text[:ins] + chr(10).join(blocks) + chr(10) + text[ins:]

    for st, end, _net, _got, want in sorted(zones, reverse=True):
        pts = " ".join("(xy %g %g)" % xy for xy in (
            (want[0], want[1]), (want[2], want[1]),
            (want[2], want[3]), (want[0], want[3])))
        text = text[:st] + "(pts\n\t\t\t\t" + pts + "\n\t\t\t)" + text[end:]

    text = P.fix_models(text)

    shutil.copy(BOARD, BOARD + ".bak")
    io.open(BOARD, "w", encoding="utf-8", newline="\n").write(text)
    print("\nwrote rev3.kicad_pcb (previous version saved as rev3.kicad_pcb.bak)")
    return P.check()


if __name__ == "__main__":
    sys.exit(main())
