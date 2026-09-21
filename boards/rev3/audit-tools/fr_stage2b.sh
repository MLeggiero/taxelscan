#!/bin/bash
PY="C:/Program Files/KiCad/10.0/bin/python.exe"
HERE="$(cd "$(dirname "$0")" && pwd)"
RF="$HERE/route_fix.py"
FR="/c/Users/mleggiero/AppData/Local/freerouting/freerouting.exe"
PASSES=${1:-60}
cd /c/Users/mleggiero/Documents/KiCad/taxelscan/boards/rev3
echo "##### keepout-dsn $(date +%T)"; python dsn_keepout.py rev3.dsn rev3.fr.dsn | tail -1
rm -f rev3.ses
echo "##### freerouting $(date +%T) passes=$PASSES"
timeout 3000 "$FR" -de rev3.fr.dsn -do rev3.ses -mp $PASSES -dct 0 --gui.enabled=false 2>&1 | grep -o "pass #[0-9]*.*unrouted)\|session completed.*\|ERROR.*\|WARN   DSN.*" | sed 's/on board.*score of//' | tail -70
ls -la rev3.ses
echo "##### import $(date +%T)"; python import_ses.py --apply --add 2>&1 | tail -12
cp rev3.kicad_pcb backups/audit-fixes-2026-09-10/rev3.kicad_pcb.grow-fr1
echo "##### drc $(date +%T)"; "$PY" "$RF" drc 2>&1 | grep "DRC:"
echo "##### close 3 $(date +%T)"; "$PY" "$RF" close 3 2>&1 | grep "DRC:\|round"
echo "##### maze $(date +%T)"; PYTHONPATH="C:/Users/mleggiero/Documents/KiCad/taxelscan/tmp/usb-power-deps" "$PY" maze_route.py --apply 2>&1 | grep -v "image handler" | tail -12
echo "##### clean $(date +%T)"; "$PY" "$RF" clean 2>&1 | grep "DRC:\|cleaned"
echo "##### prune $(date +%T)"; "$PY" "$RF" prune 2>&1 | grep "DRC:\|pruned"
echo "##### close 2 $(date +%T)"; "$PY" "$RF" close 2 2>&1 | grep "DRC:\|round"
echo "##### prune $(date +%T)"; "$PY" "$RF" prune 2>&1 | grep "DRC:\|pruned" | tail -2
cp rev3.kicad_pcb backups/audit-fixes-2026-09-10/rev3.kicad_pcb.grow-fr1-closed
echo "##### probe $(date +%T)"; "$PY" "$HERE/probe_all.py" 2>&1 | grep "sealed\|both-open\|unconnected$" | cut -c1-110
echo "##### done $(date +%T)"
