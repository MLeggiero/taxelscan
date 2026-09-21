#!/bin/bash
PY="C:/Program Files/KiCad/10.0/bin/python.exe"
HERE="$(cd "$(dirname "$0")" && pwd)"
RF="$HERE/route_fix.py"
cd /c/Users/mleggiero/Documents/KiCad/taxelscan/boards/rev3
echo "##### maze $(date +%T)"; "$PY" "$HERE/maze_wrap.py" --apply 2>&1 | grep -v "image handler" | tail -25
echo "##### clean $(date +%T)"; "$PY" "$RF" clean 2>&1 | grep "DRC:\|cleaned"
echo "##### prune $(date +%T)"; "$PY" "$RF" prune 2>&1 | grep "DRC:\|pruned" | tail -2
echo "##### close 2 $(date +%T)"; "$PY" "$RF" close 2 2>&1 | grep "DRC:\|round"
echo "##### prune $(date +%T)"; "$PY" "$RF" prune 2>&1 | grep "DRC:\|pruned" | tail -2
echo "##### probe $(date +%T)"; "$PY" "$HERE/probe_all.py" 2>&1 | grep "sealed\|both-open\|unconnected$" | cut -c1-110
echo "##### done $(date +%T)"
