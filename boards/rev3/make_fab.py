#!/usr/bin/env python3
"""Generate the manufacturing package for JLCPCB.

    ./make_fab.py            writes fab/ and checks what it produced

Everything lands in `fab/`:

    gerbers/            copper, mask, silk, paste and edge, plus drill files
    rev3-bom.csv        JLCPCB assembly BOM: Comment, Designator, Footprint, LCSC
    rev3-cpl.csv        JLCPCB pick-and-place: Designator, Mid X/Y, Layer, Rotation
    rev3-gerbers.zip    the whole gerber set, which is what you upload

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
This script emits the CPL straight from KiCad's own numbers and does NOT try
to guess corrections - check every polarised and multi-pin part against
JLCPCB's preview before releasing, which is a human step by design.

**A blank LCSC number is a hard stop for assembly.** JLCPCB will accept an order
with a blank part field and simply not place those designators, so the script
writes `fab/NEEDS-PARTS.txt` and says so - while leaving the bare-board package
alone. Since 17 September every line has a number. The solder jumpers are copper
and want no part placed at all; they are excluded rather than counted missing.

**Two assembly sets.** R11/R12 (bus termination) belong only on the two boards
at the ends of a harness: rev3-bom.csv / rev3-cpl.csv leave them out, and
rev3-bom-end.csv / rev3-cpl-end.csv put them in. ORDER-NOTES.txt says which
JLCPCB options the board needs (epoxy-filled and capped vias, Standard PCBA)
and what to check in the placement preview.
"""
import csv
import io
import os
import re
import shutil
import subprocess
import sys
import zipfile

import gen_schematic as S

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")
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
                    "--layers", LAYERS, "--check-zones", "--no-protel-ext",
                    "--subtract-soldermask", BOARD], "gerbers")
    ok &= run(cli, ["pcb", "export", "drill", "--output", GERBERS,
                    "--format", "excellon", "--excellon-separate-th",
                    "--generate-map", BOARD], "drill files")
    pos = os.path.join(FAB, "rev3-cpl-raw.csv")
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
        with io.open(os.path.join(FAB, "rev3-bom%s.csv" % suffix), "w",
                     encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
            for value, refs, fp, lcsc in sorted(bom, key=lambda x: ",".join(sorted(x[1]))):
                keep = sorted(r for r in refs if r not in skip)
                if keep:
                    w.writerow([value, ",".join(keep), fp, lcsc])
    bom = [(v, ",".join(sorted(r)), fp, l) for v, r, fp, l in bom]
    missing = [r for r in bom if not r[3]]
    print("  %-22s %d line(s), %d without an LCSC number; R11/R12 only in rev3-bom-end.csv"
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

    # ---- CPL, from KiCad's own placement numbers ----------------------------
    if os.path.exists(pos):
        rows = list(csv.DictReader(io.open(pos, encoding="utf-8")))
        counts = []
        for suffix, skip in VARIANTS:
            with io.open(os.path.join(FAB, "rev3-cpl%s.csv" % suffix), "w",
                         encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
                n = 0
                for r in rows:
                    ref = r.get("Ref") or r.get("Designator") or ""
                    if ref in skip:
                        continue
                    w.writerow([ref, r.get("PosX"), r.get("PosY"),
                                "Top" if (r.get("Side") or "").lower() == "top"
                                else "Bottom", r.get("Rot")])
                    n += 1
            counts.append(n)
        os.remove(pos)
        print("  %-22s %d part(s) on middle boards, %d on the two end boards"
              % ("CPL", counts[0], counts[1]))

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
            "  In2.Cu        0.5 oz (17.5 um)   +3.3V plane, and some routing",
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
            "152 via holes lie wholly (19) or partly (133) inside SMD pad",
            "openings, including all 7 ground vias in U9's exposed pad, which",
            "sit in the middle of its paste windows. Tenting cannot cover them.",
            "ORDER THE VIAS EPOXY FILLED AND COPPER CAPPED (JLCPCB 'Via Covering:",
            "Epoxy Filled & Capped'), or solder paste drains into the holes: dry",
            "joints under the QFN and tombstoned 0201/0402s (42 of them have a via",
            "hole on only one pad).",
            "")))
    print("  %-22s fab/STACKUP-NOTES.txt" % "fab notes")

    # ---- what to select and say when ordering ----------------------------
    with io.open(os.path.join(FAB, "ORDER-NOTES.txt"), "w", encoding="utf-8",
                 newline=chr(10)) as fh:
        fh.write(chr(10).join((
            "JLCPCB order settings for rev-3 (checked 17 September 2026, parts added 21 September)",
            "",
            "PCB",
            "  Layers 4, thickness 1.6 mm, stackup JLC04161H-7628 (inner 0.5 oz)",
            "  Outer copper 1 oz; via 0.3 mm / 0.45 mm or larger (no surcharge)",
            "  Via Covering: Epoxy Filled & Capped  <- required, see STACKUP-NOTES",
            "  Min trace/space 0.10 mm (above the 3.5 mil surcharge band)",
            "",
            "Assembly",
            "  Standard PCBA (0201 parts; Economic stops at 0402), top side only",
            "  Middle boards: rev3-bom.csv + rev3-cpl.csv (no R11/R12)",
            "  The two end boards: rev3-bom-end.csv + rev3-cpl-end.csv",
            "  JLCPCB adds rails; J1/J2 overhang the top edge by 1.5 mm, so expect",
            "  an SMT fixture fee. J6 is through-hole (or leave it for hand fit).",
            "",
            "Check in the placement preview before confirming",
            "  L1: the polarity dot must sit on pad 1, the VCORE end (towards U9's",
            "      DVDD side), per the RP2350 datasheet section 6.3.8.",
            "  U9, U12, U14: their pin-1 marks touch neighbouring pads and may be",
            "      clipped from the silkscreen; check pin 1 against the Fab drawing.",
            "  D1-D5, D3 (LED), Q1, U1-U8, U10, U11, U13, Y1: pin 1 / cathode.",
            "  D5 (USBLC6-2P6, SOT-666): pin 1 is the corner nearest J5's D+ pads;",
            "      pins 1/6 carry D+, 3/4 D-, 5 VBUS, 2 GND - a wrong turn shorts",
            "      D+ to VBUS.",
            "",
            "Low stock on 17 September: J1/J2 C597985 (44), U8 C580457 (23),",
            "  R23/R24 C852682 (2800), R21/R22 C852830 (3700), U13 C132554 (902),",
            "  R34 C149916 (550). On 21 September: D5 C15999 (8345), R38 C861215",
            "  (6180); C9/C10/C39/C46 C3039694 shows 0 on LCSC's own site but 98k",
            "  in JLCPCB's assembly stock, which is the one that matters. Re-check",
            "  on the order day.",
            "")))
    print("  %-22s fab/ORDER-NOTES.txt" % "order notes")

    # ---- zip the gerbers ----------------------------------------------------
    zpath = os.path.join(FAB, "rev3-gerbers.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(GERBERS)):
            z.write(os.path.join(GERBERS, f), f)
    print("  %-22s %d file(s), %.0f kB"
          % ("gerber zip", len(os.listdir(GERBERS)),
             os.path.getsize(zpath) / 1024.0))

    # ---- did the planes actually make it into the copper layers? ------------
    #
    # The inner-layer gerber is named after the layer's USER name, not its
    # canonical one: `rev3-GND.gbr` and `rev3-PWR.gbr`, never `rev3-In1_Cu`.
    # Looking for "In1.Cu" therefore matched nothing and printed nothing, and
    # a check that cannot fail is not a check - this repo has now caught four
    # of those. The stackup in the board file says what the names are.
    text = io.open(BOARD, encoding="utf-8").read()
    names = dict(re.findall(r'\((?:4|6) "(In[12]\.Cu)" \w+ "([^"]+)"\)', text))
    bad = False
    for canon, what in (("In1.Cu", "GND plane"), ("In2.Cu", "+3.3V plane")):
        user = names.get(canon, canon)
        hits = [f for f in os.listdir(GERBERS)
                if f.endswith(".gbr") and os.path.splitext(f)[0].endswith(
                    "-" + user.replace(".", "_"))]
        if not hits:
            print("  %-22s %s (%s): NO GERBER FOUND" % ("plane check", what,
                                                        user))
            bad = True
            continue
        for f in hits:
            size = os.path.getsize(os.path.join(GERBERS, f))
            small = size < 20000
            bad = bad or small
            print("  %-22s %-14s %-18s %.0f kB %s"
                  % ("plane check", what, f, size / 1024.0,
                     "<-- SUSPICIOUSLY SMALL" if small else ""))
    return 0 if ok and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
