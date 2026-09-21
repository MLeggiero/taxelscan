"""install_board.py final.kicad_pcb [BACKUP_DIR_NAME]

Back up the live rev-3 board files, install the finished scratch board, and verify it
where it lives: KiCad DRC with zones refilled and schematic parity against rev3.kicad_sch.
"""
import sys, os, shutil, json, subprocess, collections, hashlib

ROOT = "C:/Users/mleggiero/Documents/KiCad/taxelscan"
REV3 = ROOT + "/boards/rev3"
KICLI = "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
BK = REV3 + "/backups/" + (sys.argv[2] if len(sys.argv) > 2 else "implement-2026-09-15")


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


src = sys.argv[1]
os.makedirs(BK, exist_ok=True)
for f in ("rev3.kicad_pcb", "rev3.kicad_pro", "rev3.kicad_prl", "rev3.kicad_dru"):
    dst = BK + "/" + f + ".before"
    if not os.path.exists(dst):
        shutil.copy2(REV3 + "/" + f, dst)
        print("backed up", f, "->", dst)
pro0, dru0 = md5(REV3 + "/rev3.kicad_pro"), md5(REV3 + "/rev3.kicad_dru")
shutil.copy2(src, REV3 + "/rev3.kicad_pcb")
print("installed", src)
out = ROOT + "/tmp/install-final-drc.json"
subprocess.run([KICLI, "pcb", "drc", "--format", "json", "--severity-all", "--all-track-errors", "--refill-zones",
                "--schematic-parity", "-o", out, REV3 + "/rev3.kicad_pcb"], capture_output=True)
d = json.load(open(out, encoding="utf-8"))
print("unconnected:", len(d["unconnected_items"]))
print("violations:", dict(collections.Counter(v["type"] for v in d["violations"])))
print("parity:", dict(collections.Counter(v["type"] for v in d.get("schematic_parity", []))))
for v in d["unconnected_items"]:
    print("   open:", [i["description"][:50] for i in v["items"]])
for v in d["violations"]:
    print("   %s: %s" % (v["type"], [(i["description"][:44], i["pos"]["x"], i["pos"]["y"]) for i in v["items"]]))
gen = [v for v in d.get("schematic_parity", []) if v["type"] != "net_conflict"]
print("non net-name parity issues:", [(v["type"], v["description"], [i["description"] for i in v["items"]]) for v in gen])
print("project file unchanged:", md5(REV3 + "/rev3.kicad_pro") == pro0, " rules unchanged:", md5(REV3 + "/rev3.kicad_dru") == dru0)
