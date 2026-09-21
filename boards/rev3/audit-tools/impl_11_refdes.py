"""fix6 -> fix8: reference designators that pass DRC, and fiducials that parity ignores.

0.5 mm text tripped the board's minimum text height (32 violations) and the first
placement only dodged pads, so 22 labels sat on silkscreen. Place at 0.8 mm / 0.12 mm
- the size finalize.py already uses for R1 - and require each label to clear every
pad, every silkscreen graphic, the board edge and every label already placed. A part
that has no such spot keeps its label hidden rather than printing over something.
The fiducials are marked board-only, so the schematic-parity check stops calling them
extra footprints, and left out of the BOM and the placement file."""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew as K
import lr
BASE = lr.SCR + "/work/fix6.kicad_pcb"
OUT = lr.SCR + "/work/fix8.kicad_pcb"
WANT = re.compile(r"^(U|J|JP|L|Y|SW|Q|D)\d+$")
SIZE, THICK = 0.8, 0.12
mm, MM = K.ToMM, K.FromMM

b = K.LoadBoard(BASE)
edge = b.GetBoardEdgesBoundingBox()
E = (mm(edge.GetX()) + 0.2, mm(edge.GetY()) + 0.2, mm(edge.GetRight()) - 0.2, mm(edge.GetBottom()) - 0.2)


def bbox(o):
    r = o.GetBoundingBox()
    return (mm(r.GetX()), mm(r.GetY()), mm(r.GetRight()), mm(r.GetBottom()))


pads, silk = [], []
for f in b.GetFootprints():
    for p in f.Pads():
        pads.append(bbox(p))
    for g in f.GraphicalItems():
        if g.GetLayer() in (K.F_SilkS, K.B_SilkS):
            silk.append(bbox(g))
for d in b.GetDrawings():
    if d.GetLayer() in (K.F_SilkS, K.B_SilkS):
        silk.append(bbox(d))


def hits(r, boxes, gap=0.05):
    return any(not (r[2] + gap < o[0] or r[0] - gap > o[2] or r[3] + gap < o[1] or r[1] - gap > o[3]) for o in boxes)


placed_boxes, placed, hidden, kept = [], [], [], []
for f in b.GetFootprints():
    ref, t = f.GetReference(), f.Reference()
    if ref.startswith("FID"):
        f.SetAttributes(f.GetAttributes() | K.FP_BOARD_ONLY | K.FP_EXCLUDE_FROM_BOM | K.FP_EXCLUDE_FROM_POS_FILES)
        t.SetVisible(False)
        continue
    if not WANT.match(ref):
        if t.IsVisible():
            placed_boxes.append(bbox(t)); kept.append(ref)
        continue
    t.SetVisible(True)
    t.SetTextSize(K.VECTOR2I(MM(SIZE), MM(SIZE)))
    t.SetTextThickness(MM(THICK))
    t.SetKeepUpright(True)
    t.SetLayer(K.F_SilkS)
    fb = f.GetBoundingBox(False, False)
    cx, cy = mm(f.GetPosition().x), mm(f.GetPosition().y)
    hw, hh = (mm(fb.GetRight()) - mm(fb.GetX())) / 2, (mm(fb.GetBottom()) - mm(fb.GetY())) / 2
    best = None
    for d in (0.45, 0.8, 1.2, 1.7, 2.3, 3.0):
        for dx, dy in ((0, -(hh + d)), (0, hh + d), (-(hw + d), 0), (hw + d, 0),
                       (-(hw + d), -(hh + d)), (hw + d, -(hh + d)), (-(hw + d), hh + d), (hw + d, hh + d)):
            t.SetPosition(K.VECTOR2I(MM(cx + dx), MM(cy + dy)))
            r = bbox(t)
            if r[0] < E[0] or r[1] < E[1] or r[2] > E[2] or r[3] > E[3]:
                continue
            if hits(r, pads) or hits(r, silk) or hits(r, placed_boxes):
                continue
            best = r
            break
        if best:
            break
    if best:
        placed_boxes.append(best); placed.append(ref)
    else:
        t.SetVisible(False); hidden.append(ref)

print("left alone (%d): %s" % (len(kept), " ".join(sorted(kept))))
print("labelled (%d): %s" % (len(placed), " ".join(sorted(placed))))
print("no clear spot, still hidden (%d): %s" % (len(hidden), " ".join(sorted(hidden))))
b.Save(OUT)
print("saved", OUT)
