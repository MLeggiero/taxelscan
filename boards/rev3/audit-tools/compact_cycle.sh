#!/usr/bin/env bash
# compact_cycle.sh START ROUNDS PREFIX - compaction passes toward the right, left, bottom and
# top edges in turn (compact_lp.py), until a round gains under 0.05 mm; then DRC the last board.
# J1/J2 stay on their board-edge marks: held in place when the bottom edge moves, and moved
# with the top edge when it moves. Boards are written next to START.
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="/c/Program Files/KiCad/10.0/bin/python.exe"
cd "$(dirname "$1")"
cur="$(basename "$1")"; rounds="$2"; pre="$3"
for r in $(seq 1 "$rounds"); do
  tot=0
  for dir in "x" "x --from-high" "y --hold J1,J2" "y --from-high --pin-moving J1,J2"; do
    tag=$(echo "$dir" | awk '{t=$1; if ($0 ~ /from-high/) t=t "h"; print t}')
    out="${pre}_${r}${tag}.kicad_pcb"
    "$PY" "$HERE/compact_lp.py" "$cur" $dir 8 --out "$out" --report 0 > "${out%.kicad_pcb}.log" 2>&1
    g=$(grep -o "largest gain [0-9.]*" "${out%.kicad_pcb}.log" | awk '{print $3}')
    if [ -z "$g" ] || [ ! -f "$out" ]; then echo "round $r $tag: FAILED (kept $cur)"; tail -3 "${out%.kicad_pcb}.log"; continue; fi
    echo "round $r $tag: gain $g -> $out"
    cur="$out"
    tot=$(awk -v a="$tot" -v b="$g" 'BEGIN{print a+b}')
  done
  echo "round $r total $tot"
  if awk -v t="$tot" 'BEGIN{exit !(t < 0.05)}'; then echo "converged"; break; fi
done
echo "last: $cur"
"$PY" "$HERE/compact_verify.py" "$cur" "v_${pre}" 2>&1 | grep -v "image handler"
