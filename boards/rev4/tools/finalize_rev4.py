"""finalize_rev4.py - install a routed stage board as rev4.kicad_pcb and check it.

    python3 finalize_rev4.py <stage.kicad_pcb>

pcbnew's BOARD.Save() of a stage board writes a default project beside it, and
kicad-cli reads the project and custom rules named after the board, so the
stage is copied in as ../rev4.kicad_pcb with rev4.kicad_pro / rev4.kicad_dru
left exactly as they are in the repository. Then KiCad's own DRC runs with zones
refilled and schematic parity against rev4.kicad_sch, and the result is judged
the way ../ROUTING_STATUS.md reports it:

    unconnected   must be 0
    copper        must be 0 (clearance, shorts, holes, widths, dangling, ...)
    silkscreen    reported; rev-3 carried the same five warnings
    parity        every item must be the sheet's leading-'/' local-label name,
                  or one of the two USB-C sideband pins the sheet leaves open

Exit status 0 only if all of that holds.
"""
import collections
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REV4 = os.path.normpath(os.path.join(HERE, ".."))
BOARD = os.path.join(REV4, "rev4.kicad_pcb")
SCR = os.path.normpath(os.path.join(REV4, "..", "..", "tmp"))
WARN_ONLY = {"silk_overlap", "silk_over_copper", "lib_footprint_issues", "text_height",
             "silk_edge_clearance", "text_thickness"}
SIDEBAND = re.compile(r"Pad missing net given by schematic \(unconnected-\(J5-SBU[12]-Pad[AB]8\)\)")


def main():
    src = os.path.abspath(sys.argv[1])
    keep = {p: open(p, "rb").read() for p in (os.path.join(REV4, "rev4.kicad_pro"),
                                                os.path.join(REV4, "rev4.kicad_dru"))}
    if src != BOARD:
        shutil.copyfile(src, BOARD)
    for p, data in keep.items():
        open(p, "wb").write(data)
    os.makedirs(SCR, exist_ok=True)
    out = os.path.join(SCR, "rev4_final_drc.json")
    cli = shutil.which("kicad-cli") or "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
    subprocess.run([cli, "pcb", "drc", "--refill-zones", "--schematic-parity", "--severity-all",
                    "--all-track-errors", "--format", "json", "-o", out, BOARD], capture_output=True)
    d = json.load(open(out, encoding="utf-8"))
    kinds = collections.Counter(v["type"] for v in d["violations"])
    copper = {k: n for k, n in kinds.items() if k not in WARN_ONLY}
    silk = {k: n for k, n in kinds.items() if k in WARN_ONLY and k != "lib_footprint_issues"}
    parity_bad = []
    for v in d.get("schematic_parity", []):
        m = re.search(r"Pad net \((.*)\) doesn't match net given by schematic \((.*)\)", v["description"])
        if m and m.group(2).lstrip("/") == m.group(1):
            continue
        if SIDEBAND.search(v["description"]):
            continue
        parity_bad.append(v["description"])
    print("rev4.kicad_pcb, KiCad DRC with zones refilled and schematic parity:")
    print("  unconnected   %d" % len(d["unconnected_items"]))
    print("  copper        %d  %s" % (sum(copper.values()), copper or ""))
    print("  silkscreen    %d  %s" % (sum(silk.values()), silk))
    print("  parity        %d item(s), %d not a '/'-prefix or sideband item" % (
        len(d.get("schematic_parity", [])), len(parity_bad)))
    for x in parity_bad[:10]:
        print("      " + x)
    ok = not d["unconnected_items"] and not copper and not parity_bad
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
