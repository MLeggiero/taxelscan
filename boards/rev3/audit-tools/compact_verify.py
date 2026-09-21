"""compact_verify.py BOARD [TAG] - DRC (reference project and rules), outline size and the +3.3V pour."""
import sys, os, collections
SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
import lr, pour
import pcbnew as K

BOARD = sys.argv[1]
TAG = sys.argv[2] if len(sys.argv) > 2 else "verify"
lr.PRO_REF = lr.ROOT + "/boards/rev3/rev3.kicad_pro"
d, c = lr.drc(BOARD, TAG)
b = K.LoadBoard(BOARD)
xs = [K.ToMM(q.x) for d in b.GetDrawings() if d.GetLayer() == K.Edge_Cuts for q in (d.GetStart(), d.GetEnd())]
ys = [K.ToMM(q.y) for d in b.GetDrawings() if d.GetLayer() == K.Edge_Cuts for q in (d.GetStart(), d.GetEnd())]
w, h = max(xs) - min(xs), max(ys) - min(ys)
print("outline %.2f x %.2f mm = %.0f mm2 (edge-line centres)" % (w, h, w * h))
print("unconnected %d | %s" % (len(d["unconnected_items"]), dict(sorted(c.items()))))
for v in d["unconnected_items"][:20]:
    print("   open:", " <-> ".join("%s @(%.2f,%.2f)" % (i["description"][:45], i["pos"]["x"], i["pos"]["y"]) for i in v["items"]))
shown = collections.Counter()
for v in d["violations"]:
    if v["type"] in ("silk_over_copper", "silk_overlap", "silk_edge_clearance", "text_height", "lib_footprint_issues"):
        continue
    shown[v["type"]] += 1
    if shown[v["type"]] <= 12:
        print("   %s:" % v["type"], " | ".join("%s @(%.3f,%.3f)" % (i["description"][:48], i["pos"]["x"], i["pos"]["y"]) for i in v["items"]))
r = pour.pieces(b)
print("+3.3V pour: %d pieces, main %.0f mm2 holds %d of %d vias/pads" % (r["pieces"], r["main_area"], r["on_main"], r["total"]))
