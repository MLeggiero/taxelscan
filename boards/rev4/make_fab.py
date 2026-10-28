#!/usr/bin/env python3
"""Generate the manufacturing package for JLCPCB.

    ./make_fab.py            writes fab/ and checks what it produced

Everything lands in `fab/`:

    gerbers/            copper, mask, silk, paste and edge, plus drill files
    rev4-bom.csv        JLCPCB assembly BOM: Comment, Designator, Footprint, LCSC
    rev4-cpl.csv        JLCPCB pick-and-place: Designator, Mid X/Y, Layer, Rotation
    rev4-gerbers.zip    the whole gerber set, which is what you upload

TWO THINGS WORTH KNOWING.

**Zones are refilled at export time.** The board file itself carries zone
OUTLINES but no `filled_polygon` geometry - nothing in this toolchain ever
writes a computed fill back, and `kicad-cli pcb drc --refill-zones` computes
one in memory and discards it. `--check-zones` on the gerber export does the
same thing at plot time, so the planes ARE in the output even though the
`.kicad_pcb` looks empty of them. Open the board in pcbnew and press B before
judging a plane by eye.

**Rotation is the classic JLCPCB failure.** Their library's idea of 0 degrees
frequently disagrees with KiCad's, and a rotated 4067 is a scrapped board.
JLCPCB places each LCSC part's own footprint, so `tools/jlc_placement.py`
fits, part by part, the rotation and origin offset that carry KiCad's
footprint onto that one (matched pad by pad, not guessed from package names)
and writes `jlc_placement.csv`; this script applies it to the CPLs and stops
if a placed part has no entry. Still check every polarised and multi-pin part
against JLCPCB's preview before releasing: a human step by design.

**A blank LCSC number is a hard stop for assembly.** JLCPCB will accept an order
with a blank part field and simply not place those designators, so the script
writes `fab/NEEDS-PARTS.txt` and says so - while leaving the bare-board package
alone. Since 17 September every line has a number. The solder jumpers are copper
and want no part placed at all; they are excluded rather than counted missing.

**Protel file extensions.** JLCPCB's KiCad guides ask for them ("JLCPCB
prefers Protel filename extensions"), and they say which layer a file is
whatever the layer is called: the inner layers are `rev4-GND.g1` (layer 2,
the ground plane) and `rev4-PWR.g2` (layer 3, the 3.3 V plane), G1 and G2
being Protel's first and second inner layers. Each file also carries its
layer in its X2 attributes, which the checks at the end read.

**Two assembly sets.** R11/R12 (bus termination) belong only on the two boards
at the ends of a harness: rev4-bom.csv / rev4-cpl.csv leave them out, and
rev4-bom-end.csv / rev4-cpl-end.csv put them in. ORDER-NOTES.txt says which
JLCPCB options the board needs (Standard PCBA; the vias are tented)
and what to check in the placement preview.
"""
import csv
import io
import os
import re
import shutil
import subprocess
import sys
import math
import zipfile

import gen_schematic as S

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev4.kicad_pcb")
FAB = os.path.join(HERE, "fab")
GERBERS = os.path.join(FAB, "gerbers")

# What JLCPCB wants plotted, in their naming order.
LAYERS = ("F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,"
          "F.Mask,B.Mask,Edge.Cuts")

# Copper-only, no part is placed on these.
NO_PLACE = {"JP1", "JP2", "JP3"}

# Fitted only on the two boards at the ends of a harness. Eight boards all
# carrying 120 R would load each pair with 15 R; the SN65HVD75 is specified
# for 54 R. The default files leave them out, the -end files put them in.
END_ONLY = {"R11", "R12"}
VARIANTS = (("", NO_PLACE | END_ONLY), ("-end", NO_PLACE))


def run(cli, args, what):
    r = subprocess.run([cli] + args, capture_output=True, text=True)
    ok = r.returncode == 0
    print("  %-22s %s" % (what, "ok" if ok else "FAILED"))
    if not ok:
        print("      " + (r.stderr or r.stdout).strip()[:300])
    return ok


def main():
    cli = S.find_cli()
    if not cli:
        sys.exit("kicad-cli not found")
    if os.path.isdir(FAB):
        shutil.rmtree(FAB)
    os.makedirs(GERBERS)

    ok = True
    ok &= run(cli, ["pcb", "export", "gerbers", "--output", GERBERS,
                    "--layers", LAYERS, "--check-zones",
                    "--subtract-soldermask", BOARD], "gerbers")
    ok &= run(cli, ["pcb", "export", "drill", "--output", GERBERS,
                    "--format", "excellon", "--excellon-separate-th",
                    "--generate-map", BOARD], "drill files")
    # Drawings for the assembler and for checking the placement preview by eye:
    # top copper, silkscreen, fab outlines (pin-1 marks, L1's dot) and courtyards.
    ok &= run(cli, ["pcb", "export", "pdf", "--output", os.path.join(FAB, "rev4-assembly-top.pdf"),
                    "--layers", "F.Fab,F.SilkS,F.CrtYd,Edge.Cuts,F.Cu", "--include-border-title",
                    "--mode-single", "--black-and-white", BOARD], "assembly drawing")
    ok &= run(cli, ["pcb", "export", "pdf", "--output", os.path.join(FAB, "rev4-fab-drawing.pdf"),
                    "--layers", "F.Fab,Edge.Cuts,User.Drawings", "--include-border-title",
                    "--mode-single", "--black-and-white", BOARD], "fab drawing")
    ok &= run(cli, ["sch", "export", "pdf", "--output", os.path.join(FAB, "rev4-schematic.pdf"),
                    os.path.join(HERE, "rev4.kicad_sch")], "schematic pdf")
    pos = os.path.join(FAB, "rev4-cpl-raw.csv")
    ok &= run(cli, ["pcb", "export", "pos", "--output", pos, "--format", "csv",
                    "--units", "mm", "--side", "both", "--exclude-dnp", BOARD],
              "placement file")

    # ---- BOM, expanded from the reference ranges in BOM.csv -----------------
    bom = []
    for row in csv.DictReader(io.open(os.path.join(HERE, "BOM.csv"),
                                      encoding="utf-8")):
        refs = []
        for ref in row["Reference"].replace(" ", "").split(","):
            m = re.fullmatch(r"([A-Z]+)(\d+)-[A-Z]*(\d+)", ref)
            if m:
                refs += ["%s%d" % (m.group(1), i)
                         for i in range(int(m.group(2)), int(m.group(3)) + 1)]
            else:
                refs.append(ref)
        refs = [r for r in refs if r not in NO_PLACE]
        if refs:
            bom.append((row["Value"], refs, row["Footprint"], row.get("LCSC", "")))
    for suffix, skip in VARIANTS:
        with io.open(os.path.join(FAB, "rev4-bom%s.csv" % suffix), "w",
                     encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
            for value, refs, fp, lcsc in sorted(bom, key=lambda x: ",".join(sorted(x[1]))):
                keep = sorted(r for r in refs if r not in skip)
                if keep:
                    w.writerow([value, ",".join(keep), fp, lcsc])
    lcsc_of = {ref: l.strip() for _v, refs, _fp, l in bom for ref in refs if l.strip()}
    bom = [(v, ",".join(sorted(r)), fp, l) for v, r, fp, l in bom]
    missing = [r for r in bom if not r[3]]
    print("  %-22s %d line(s), %d without an LCSC number; R11/R12 only in rev4-bom-end.csv"
          % ("BOM", len(bom), len(missing)))
    if missing:
        # A blank LCSC field is not a warning, it is a part nobody has chosen.
        # JLCPCB will happily take the order and quietly not place those
        # designators, and the first sign of it is a board that does not work.
        # The BARE board is a different matter - the gerbers below are
        # complete and orderable today, which is the order the plan calls for
        # first anyway ("five bare PCBs, seat a real mat tail on each
        # connector before releasing any assembly job").
        need = os.path.join(FAB, "NEEDS-PARTS.txt")
        with io.open(need, "w", encoding="utf-8", newline=chr(10)) as fh:
            fh.write("Assembly is BLOCKED until these have an LCSC number in "
                     "BOM.csv." + chr(10) + chr(10))
            for value, refs, fp, _ in sorted(missing, key=lambda r: r[1]):
                fh.write("  %-12s %-22s %s%s" % (refs, value, fp, chr(10)))
            fh.write(chr(10) + "The gerbers and drill files in this directory "
                     "are complete; bare boards can be ordered now." + chr(10))
        print("  %-22s %s" % ("assembly BLOCKED",
                              ", ".join(r[1] for r in missing)))
        print("  %-22s %s" % ("", "-> fab/NEEDS-PARTS.txt"))

    # ---- CPL, from KiCad's placement, turned and moved onto each LCSC part's
    # own footprint (tools/jlc_placement.py fits jlc_placement.csv) -----------
    corr = {}
    corr_path = os.path.join(HERE, "jlc_placement.csv")
    if os.path.exists(corr_path):
        for c in csv.DictReader(io.open(corr_path, encoding="utf-8")):
            corr[(c["LCSC"], c["Footprint"])] = (float(c["Rotation"]), float(c["Offset X"]),
                                                 float(c["Offset Y"]))
    if os.path.exists(pos):
        rows = list(csv.DictReader(io.open(pos, encoding="utf-8")))
        counts, turned, unchecked = [], set(), set()
        for suffix, skip in VARIANTS:
            with io.open(os.path.join(FAB, "rev4-cpl%s.csv" % suffix), "w",
                         encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
                n = 0
                for r in rows:
                    ref = r.get("Ref") or r.get("Designator") or ""
                    if ref in skip:
                        continue
                    x, y, rot = r.get("PosX"), r.get("PosY"), r.get("Rot")
                    c = corr.get((lcsc_of.get(ref, ""), r.get("Package", "")))
                    if c is None:
                        unchecked.add(ref)
                    elif c[0] or math.hypot(c[1], c[2]) >= 0.0005:
                        # rotation theta - delta; position P + R(theta) . offset
                        th = math.radians(float(rot))
                        x = "%.6f" % (float(x) + c[1] * math.cos(th) - c[2] * math.sin(th))
                        y = "%.6f" % (float(y) + c[1] * math.sin(th) + c[2] * math.cos(th))
                        a = (float(rot) - c[0]) % 360.0
                        rot = "%.6f" % (a - 360.0 if a > 180.0 else a)
                        turned.add(ref)
                    w.writerow([ref, x, y, "Top" if (r.get("Side") or "").lower() == "top"
                                else "Bottom", rot])
                    n += 1
            counts.append(n)
        os.remove(pos)
        print("  %-22s %d part(s) on middle boards, %d on the two end boards; %d turned or moved "
              "onto their LCSC footprints (jlc_placement.csv)" % ("CPL", counts[0], counts[1], len(turned)))
        if unchecked:
            # a part JLCPCB would place from KiCad's numbers alone, unchecked against
            # its own footprint: rotated parts are exactly how a board is scrapped
            print("  %-22s %s: no jlc_placement.csv entry - run tools/jlc_placement.py"
                  % ("CPL NOT CHECKED", ", ".join(sorted(unchecked))))
            ok = False

    # ---- what the board assumes of the fab ---------------------------------
    # The board file carries no stackup block, so a fab builds it to their
    # default. This design is drawn for the standard 4-layer one, with the 5 V
    # rails kept on the 1 oz outer layers - say so with the gerbers rather than
    # leaving it to be inferred.
    with io.open(os.path.join(FAB, "STACKUP-NOTES.txt"), "w", encoding="utf-8",
                 newline=chr(10)) as fh:
        fh.write(chr(10).join((
            "Stackup the design assumes",
            "",
            "  4 layer, 1.6 mm finished thickness",
            "  F.Cu / B.Cu   1 oz (35 um)",
            "  In1.Cu        0.5 oz (17.5 um)   GND plane",
            "  In2.Cu        0.5 oz (17.5 um)   +3.3V plane (no signal routing)",
            "",
            "The 5 V rails (+5V_BUS, +5V_USB) run only on the 1 oz outer layers,",
            "0.30 mm wide (USB_BUS_SW 0.40 mm): about 1 A at a 10 C rise against",
            "the 0.7 A the chain can draw at the switch's high current limit.",
            "Nothing on In2 carries 5 V, so 0.5 oz inner copper is enough; 1 oz",
            "inner layers are margin, not a problem.",
            "",
            "Vias are 0.3 mm drill / 0.5 or 0.6 mm pad: a 0.10 mm annular ring,",
            "inside JLCPCB's via rule (pad >= hole + 0.10 mm) at no extra cost.",
            "",
            "The vias are tented: solder mask covers each one on both sides, except",
            "where it sits in a pad's opening. No via hole is in an SMD pad's",
            "solder-mask opening, or within 0.10 mm of one (JLCPCB's minimum mask",
            "bridge), except the 9 ground vias in the exposed pads of U9 (7) and",
            "U12 (2), where the RP2350 and TPS62162 layouts put them, under the",
            "paste windows. U5 pin 15's ground via (that mux's enable, tied low) is",
            "boxed in by the column lines at the toe of its pad, so that pad's toe",
            "is cut back 0.30 mm (1.60 mm long, the other pins 1.90): the hole is",
            "0.15 mm clear of it, under the mask, and the lead still lands on the",
            "pad at the package's largest lead span. tools/via_in_pad.py counts",
            "them, and the board rule 'via hole clear of SMD pads' holds the rest",
            "in KiCad's DRC. Until the 21 October design review 98 vias besides the",
            "exposed pads' had their holes in pads, 32 of them on one pad of a",
            "two-pad passive (the tombstoning case), and the board had to be",
            "ordered with epoxy-filled, capped vias (../REVIEW.md).",
            "",
            "Epoxy filled and capped vias are now optional (a paid option on a",
            "4-layer board). They keep paste from draining into the exposed-pad",
            "vias, which otherwise leave some voiding under U9 and U12, as any open",
            "thermal via does. Without them, check the exposed pads at inspection.",
            "",
            "Copper is at least 0.40 mm from the outline everywhere: JLCPCB's figure",
            "for a V-scored edge (a routed edge needs 0.2 mm).",
            "")))
    print("  %-22s fab/STACKUP-NOTES.txt" % "fab notes")

    # ---- what to select and say when ordering ----------------------------
    with io.open(os.path.join(FAB, "ORDER-NOTES.txt"), "w", encoding="utf-8",
                 newline=chr(10)) as fh:
        fh.write(chr(10).join((
            "JLCPCB order settings for rev-4 (rev-3's, checked 17 September 2026, less U8/C8/R10/C10/C26)",
            "",
            "PCB",
            "  Layers 4, thickness 1.6 mm, stackup JLC04161H-7628 (inner 0.5 oz)",
            "  Outer copper 1 oz; via 0.3 mm / 0.45 mm or larger (no surcharge)",
            "  Via Covering: Tented (Epoxy Filled & Capped optional: see STACKUP-NOTES)",
            "  Min trace/space 0.10 mm (above the 3.5 mil surcharge band)",
            "",
            "Files",
            "  PCB: rev4-gerbers.zip (Gerbers with Protel extensions, Excellon drills).",
            "  JLCPCB's viewer should show the four copper layers in this order:",
            "    L1 top      rev4-F_Cu.gtl",
            "    L2 ground   rev4-GND.g1   (In1, the GND plane)",
            "    L3 3.3 V    rev4-PWR.g2   (In2, the +3.3V plane)",
            "    L4 bottom   rev4-B_Cu.gbl",
            "  G1 / G2 are Protel's first and second inner layers, and each file",
            "  names its layer in its X2 attributes too. If the viewer has the",
            "  inner layers the other way round, put them right before ordering.",
            "",
            "Assembly",
            "  Standard PCBA (0201 parts; Economic stops at 0402), top side only",
            "  Middle boards: rev4-bom.csv + rev4-cpl.csv (no R11/R12)",
            "  The two end boards: rev4-bom-end.csv + rev4-cpl-end.csv",
            "  JLCPCB adds rails; J1/J2 overhang the top edge by 1.5 mm, so expect",
            "  an SMT fixture fee. J6 is through-hole (or leave it for hand fit).",
            "",
            "Rotations and positions in the CPLs are already corrected for JLCPCB's",
            "own footprints (jlc_placement.csv, fitted pad by pad by",
            "tools/jlc_placement.py from each LCSC part's EasyEDA footprint):",
            "  turned -90 deg  U1-U4 (TSSOP-16), U5/U6 (SSOP-24), U7 (MSOP-8),",
            "                  U10/U11 (SOIC-8), J6 (pin header, also moved 3.81 mm",
            "                  from pin 1 to its middle)",
            "  turned 180 deg  U14 (SOT-23-6), D5 (SOT-666), Q1 (SOT-323)",
            "  moved           J5 1.57 mm, J1/J2 0.23 mm, J3/J4 0.10 mm (origin offsets)",
            "  unchanged       everything else, U9 / U12 / U13 / Y1 and the diodes and",
            "                  LED included (pad 1 is the cathode in both libraries)",
            "In the preview every part should now sit on its pads with pin 1 on pin 1.",
            "If one does not, fix that part in JLCPCB's editor and tell us which:",
            "its LCSC footprint has changed, and jlc_placement.py must be run again.",
            "",
            "Check in the placement preview before confirming",
            "  L1: the polarity dot must sit on pad 1, the VCORE end - the west end,",
            "      over COUT (C19) - per the RP2350 datasheet section 6.3.8.",
            "  C44 / C19 (CIN / COUT, 0402 4.7 uF): centred on their wide-gap pads,",
            "      VREG_LX runs between the pads; a part off-centre bridges it.",
            "  U12 (TPS62162, WSON-8 2 x 2 mm): pin 1 (PGND) top left, beside C27;",
            "      the exposed pad must be soldered (two vias in it).",
            "  U9, U12, U14: their pin-1 marks touch neighbouring pads and may be",
            "      clipped from the silkscreen; check pin 1 against the Fab drawing.",
            "  D1-D5, D3 (LED), Q1, U1-U7, U10, U11, U13, Y1: pin 1 / cathode.",
            "  D5 (USBLC6-2P6, SOT-666): pin 1 is the corner nearest J5's D+ pads;",
            "      pins 1/6 carry D+, 3/4 D-, 5 VBUS, 2 GND - a wrong turn shorts",
            "      D+ to VBUS.",
            "",
            "JLCPCB assembly stock on 28 October 2026, every BOM line, parts per board",
            "in brackets:",
            "  U9 C41378174 RP2354A: 0 (1); JLCPCB offers 92 for pre-order (8 minimum).",
            "      <- BLOCKS EVERY BOARD. Pre-order it into your JLCPCB parts inventory",
            "      before ordering, or take it through Global Sourcing: Digi-Key had the",
            "      A4 stepping (Raspberry Pi SC1511-A4) on 28 October, 2,740 on 7-inch",
            "      reels and 9,197 on 13-inch. A pre-order does not say its stepping;",
            "      A4 is the current one (bring-up item 7 reads it off the chip).",
            "  L1 C42411119 AOTA-B201610S3R3-101-T: 0 (1), none for pre-order.",
            "      <- BLOCKS EVERY BOARD. It is the RP2350 datasheet's polarity-marked",
            "      inductor (gen_rev4.py asserts it): take it through Global Sourcing",
            "      (Digi-Key had 5,266 on 28 October; 9-15 days), or buy it and consign",
            "      it, or leave L1 off and fit it by hand, dot on pad 1, the VCORE end.",
            "      No L1, no core rail: the RP2354A will not run. If it cannot be had,",
            "      FTC201610S3R3MBCA (C5832345) fits L1's pads and meets the datasheet's",
            "      numbers but has no polarity mark, so about half the boards would get",
            "      it the wrong way round: scope VCORE under load on each and turn L1",
            "      round on those that misbehave.",
            "  J1/J2 C597985 FH12-32S-0.5SH(55): 15 (2) = 7 boards.",
            "  J6 C5184785 2.54 mm header: 14 (1) = 14 boards.",
            "  Every other line has stock for at least 260 boards. Re-check on the",
            "  order day.",
            "",
            "Harness cables (not part of the PCBA order)",
            "  JST GH: GHR-06V-S housing at each end, SSHL-002T-P0.2 contacts, 26 AWG.",
            "  Wired PIN n TO PIN n: J3 of one board to J4 of the next. J3 and J4 are",
            "  the same part turned 180 degrees, so the wires cross between adjacent",
            "  boards; a cable laid straight across joins pin 1 to pin 6 and puts",
            "  +5V_BUS on SYNC_N. Twist 3 with 4 (BUS_P / BUS_N) and 5 with 6",
            "  (SYNC_P / SYNC_N). Pin 1 (+5V_BUS) is marked '1' on the silkscreen at",
            "  both connectors; ring J3.1 to J4.1 before connecting power.",
            "")))
    print("  %-22s fab/ORDER-NOTES.txt" % "order notes")

    # ---- zip the gerbers ----------------------------------------------------
    zpath = os.path.join(FAB, "rev4-gerbers.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(GERBERS)):
            z.write(os.path.join(GERBERS, f), f)
    print("  %-22s %d file(s), %.0f kB"
          % ("gerber zip", len(os.listdir(GERBERS)),
             os.path.getsize(zpath) / 1024.0))

    # ---- every layer once, and did the planes make it into theirs? ----------
    #
    # Each Gerber says which layer it is in its X2 attributes
    # (%TF.FileFunction,Copper,L2,Inr*%), whatever its name. The inner files
    # are named after the layers' USER names, `rev4-GND.g1` and `rev4-PWR.g2`,
    # never `rev4-In1_Cu`: a check that once looked for "In1.Cu" matched
    # nothing and printed nothing, and a check that cannot fail is not a check
    # (this repo has caught four of those). So this one reads the attribute,
    # and the stackup in the board file says what each inner layer is called.
    text = io.open(BOARD, encoding="utf-8").read()
    names = dict(re.findall(r'\((?:4|6) "(In[12]\.Cu)" \w+ "([^"]+)"\)', text))
    inner = {"In1.Cu": names.get("In1.Cu", "In1_Cu"), "In2.Cu": names.get("In2.Cu", "In2_Cu")}
    want = {
        "Copper,L1,Top": "rev4-F_Cu.gtl",
        "Copper,L2,Inr": "rev4-%s.g1" % inner["In1.Cu"].replace(".", "_"),
        "Copper,L3,Inr": "rev4-%s.g2" % inner["In2.Cu"].replace(".", "_"),
        "Copper,L4,Bot": "rev4-B_Cu.gbl",
        "Soldermask,Top": "rev4-F_Mask.gts", "Soldermask,Bot": "rev4-B_Mask.gbs",
        "Legend,Top": "rev4-F_Silkscreen.gto", "Legend,Bot": "rev4-B_Silkscreen.gbo",
        "Paste,Top": "rev4-F_Paste.gtp", "Paste,Bot": "rev4-B_Paste.gbp",
        "Profile,NP": "rev4-Edge_Cuts.gm1",
    }
    found = {}
    for f in sorted(os.listdir(GERBERS)):
        head = io.open(os.path.join(GERBERS, f), encoding="utf-8", errors="replace").read(4000)
        m = re.search(r"%TF\.FileFunction,([^*]*)\*%", head)
        if m:
            found.setdefault(m.group(1), []).append(f)
    bad = False
    for func, name in want.items():
        if found.get(func) != [name]:
            print("  %-22s %s: want %s, found %s" % ("layer check", func, name, found.get(func) or "nothing"))
            bad = True
    extra = sorted(set(found) - set(want))
    if extra:
        print("  %-22s unexpected layers: %s" % ("layer check", extra))
        bad = True
    if not bad:
        print("  %-22s %d layers, one file each, inner L2 %s / L3 %s" % (
            "layer check", len(want), want["Copper,L2,Inr"], want["Copper,L3,Inr"]))
    for func, what in (("Copper,L2,Inr", "GND plane"), ("Copper,L3,Inr", "+3.3V plane")):
        for f in found.get(func, []):
            size = os.path.getsize(os.path.join(GERBERS, f))
            small = size < 20000
            bad = bad or small
            print("  %-22s %-14s %-18s %.0f kB %s"
                  % ("plane check", what, f, size / 1024.0,
                     "<-- SUSPICIOUSLY SMALL" if small else ""))
    return 0 if ok and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
