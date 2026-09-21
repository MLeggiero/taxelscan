"""finalize.py board.kicad_pcb [reference.kicad_pro]

Silk tidy, zone fill, DRC with schematic parity (the board's folder must hold the
project, rules and sheet). pcbnew's BOARD.Save() can rewrite the project next to the
board with default rules, so the reference project is put back before DRC runs."""
import sys, os, json, shutil, subprocess, collections
import pcbnew as K

PCB = sys.argv[1]
PRO_REF = sys.argv[2] if len(sys.argv) > 2 else None
KICLI = "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
mm = K.ToMM
b = K.LoadBoard(PCB)

# R1's reference sat over U5 pins 1-2: put it beside R1, inside the edge, clear of pads
f = b.FindFootprintByReference("R1")
ref = f.Reference()
ref.SetTextSize(K.VECTOR2I(K.FromMM(0.8), K.FromMM(0.8)))
ref.SetTextThickness(K.FromMM(0.12))
ref.SetPosition(K.VECTOR2I(K.FromMM(162.2), K.FromMM(117.8)))
bb = ref.GetBoundingBox()
print("R1 ref bbox", [round(mm(v), 3) for v in (bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom())])

K.ZONE_FILLER(b).Fill(b.Zones())
b.Save(PCB)
# kicad-cli reads the project, custom rules and (for parity) the sheet named after the BOARD
base = os.path.splitext(os.path.abspath(PCB))[0]
if PRO_REF:
    for ext in (".kicad_pro", ".kicad_dru"):
        ref = os.path.splitext(PRO_REF)[0] + ext
        if os.path.exists(ref) and (not os.path.exists(base + ext) or open(base + ext, "rb").read() != open(ref, "rb").read()):
            shutil.copyfile(ref, base + ext)
            print("%s missing or rewritten by the save - restored from the reference" % os.path.basename(base + ext))
if not os.path.exists(base + ".kicad_sch"):
    print("WARNING: no %s beside the board - the parity check will be meaningless" % os.path.basename(base + ".kicad_sch"))
out = os.path.splitext(PCB)[0] + "-final-drc.json"
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
tracks = [t for t in b.GetTracks()]
print("tracks", sum(1 for t in tracks if t.Type() != K.PCB_VIA_T), "vias", sum(1 for t in tracks if t.Type() == K.PCB_VIA_T))
