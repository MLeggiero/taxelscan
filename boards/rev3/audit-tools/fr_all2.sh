#!/bin/bash
bash /c/Users/mleggiero/Documents/KiCad/taxelscan/tmp/fr_stage1.sh
cd /c/Users/mleggiero/Documents/KiCad/taxelscan/boards/rev3
"C:/Program Files/KiCad/10.0/bin/python.exe" - <<'PYEOF' 2>&1 | grep -v handler
import pcbnew as K, sys
sys.path.insert(0, ".")
from dsn_keepout import OWNED
b = K.LoadBoard("rev3.kicad_pcb"); n = 0
for t in list(b.GetTracks()):
    if t.GetNetname() not in OWNED: b.Delete(t); n += 1
b.Save("rev3.kicad_pcb"); print("removed", n, "fanout stubs")
b = K.LoadBoard("rev3.kicad_pcb"); print("export", K.ExportSpecctraDSN(b, "rev3.dsn"))
PYEOF
python protect_dsn.py rev3.dsn --none --no-class 2>&1 | grep "plane layer"
cp rev3.kicad_pcb backups/audit-fixes-2026-09-10/rev3.kicad_pcb.grow-fixedcopper
bash /c/Users/mleggiero/Documents/KiCad/taxelscan/tmp/fr_stage2b.sh 60
