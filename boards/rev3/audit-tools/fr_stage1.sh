#!/bin/bash
PY="C:/Program Files/KiCad/10.0/bin/python.exe"
HERE="$(cd "$(dirname "$0")" && pwd)"
RF="$HERE/route_fix.py"
cd /c/Users/mleggiero/Documents/KiCad/taxelscan/boards/rev3
for st in fanout stubs analog gnd pairs power; do
  echo "##### $st $(date +%T)"; "$PY" "$RF" $st 2>&1 | grep -v "image handler"
done
cp rev3.kicad_pcb backups/audit-fixes-2026-09-10/rev3.kicad_pcb.grow-fixedcopper
echo "##### drc $(date +%T)"; "$PY" "$RF" drc 2>&1 | grep "DRC:"
echo "##### done $(date +%T)"
