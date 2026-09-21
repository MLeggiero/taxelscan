"""finalize_compact.py board.kicad_pcb reference.kicad_pro

Zone fill, save, project and rules put back, DRC with schematic parity (the board's
folder must hold the sheet). finalize.py without its fixed R1 label position, which
would land outside a compacted outline."""
import sys, os, json, shutil, subprocess, collections
import pcbnew as K

PCB, PRO_REF = sys.argv[1], sys.argv[2]
KICLI = "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
b = K.LoadBoard(PCB)
K.ZONE_FILLER(b).Fill(b.Zones())
b.Save(PCB)
base = os.path.splitext(os.path.abspath(PCB))[0]
for ext in (".kicad_pro", ".kicad_dru"):
    ref = os.path.splitext(PRO_REF)[0] + ext
    if os.path.exists(ref) and (not os.path.exists(base + ext) or open(base + ext, "rb").read() != open(ref, "rb").read()):
        shutil.copyfile(ref, base + ext)
        print("%s restored from the reference" % os.path.basename(base + ext))
if not os.path.exists(base + ".kicad_sch"):
    print("WARNING: no %s beside the board - the parity check will be meaningless" % os.path.basename(base + ".kicad_sch"))
out = base + "-final-drc.json"
subprocess.run([KICLI, "pcb", "drc", "--format", "json", "--severity-all", "--all-track-errors", "--refill-zones",
                "--schematic-parity", "-o", out, PCB], capture_output=True)
d = json.load(open(out, encoding="utf-8"))
print("unconnected:", len(d["unconnected_items"]))
print("violations:", dict(collections.Counter(v["type"] for v in d["violations"])))
print("parity:", dict(collections.Counter(v["type"] for v in d.get("schematic_parity", []))))
for v in d["unconnected_items"]:
    print("   open:", [i["description"][:50] for i in v["items"]])
for v in d["violations"]:
    if v["type"] not in ("silk_over_copper", "lib_footprint_issues"):
        print("   %s: %s" % (v["type"], [(i["description"][:40], i["pos"]["x"], i["pos"]["y"]) for i in v["items"]]))
e = b.GetBoardEdgesBoundingBox()
print("outline (edge-line centres) %.2f x %.2f mm" % (K.ToMM(e.GetWidth()) - 0.1, K.ToMM(e.GetHeight()) - 0.1))
tracks = list(b.GetTracks())
print("tracks", sum(1 for t in tracks if t.Type() != K.PCB_VIA_T), "vias", sum(1 for t in tracks if t.Type() == K.PCB_VIA_T))
