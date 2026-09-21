"""refdes_replace.py IN OUT - put the visible reference designators back where they clear everything.

Compaction moves each label with its part, so some now sit on a neighbour's pads or
silkscreen, or past the new edge. Every visible label keeps its place if that place
clears every pad, every silkscreen graphic, the board edge (0.2 mm) and the labels
already settled; otherwise it takes the nearest clear spot around its part (0.8 mm
text, 0.12 mm stroke, as the board already uses). A label with no clear spot is hidden
rather than printed over something."""
import sys
import pcbnew as K

IN, OUT = sys.argv[1], sys.argv[2]
SIZE, THICK = 0.8, 0.12
mm, MM = K.ToMM, K.FromMM
b = K.LoadBoard(IN)

xs, ys = [], []
for d in b.GetDrawings():
    if d.GetLayer() == K.Edge_Cuts:
        for p in (d.GetStart(), d.GetEnd()):
            xs.append(mm(p.x)); ys.append(mm(p.y))
E = (min(xs) + 0.25, min(ys) + 0.25, max(xs) - 0.25, max(ys) - 0.25)


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


def clear(r, placed):
    return (r[0] >= E[0] and r[1] >= E[1] and r[2] <= E[2] and r[3] <= E[3]
            and not hits(r, pads) and not hits(r, silk) and not hits(r, placed))


labels = [(f, f.Reference()) for f in b.GetFootprints() if f.Reference().IsVisible() and f.Reference().GetLayer() == K.F_SilkS]
placed, kept, moved, hidden = [], [], [], []
# first pass: labels that are fine where they are stay put
todo = []
for f, t in labels:
    t.SetTextSize(K.VECTOR2I(MM(SIZE), MM(SIZE)))
    t.SetTextThickness(MM(THICK))
    r = bbox(t)
    if clear(r, placed):
        placed.append(r)
        kept.append(f.GetReference())
    else:
        todo.append((f, t))
# second pass: search around the part, nearest first
for f, t in todo:
    fb = f.GetBoundingBox(False, False)
    cx, cy = mm(f.GetPosition().x), mm(f.GetPosition().y)
    hw, hh = (mm(fb.GetRight()) - mm(fb.GetX())) / 2, (mm(fb.GetBottom()) - mm(fb.GetY())) / 2
    old = t.GetPosition()
    best = None
    for d in (0.45, 0.8, 1.2, 1.7, 2.3, 3.0, 3.8):
        for dx, dy in ((0, -(hh + d)), (0, hh + d), (-(hw + d), 0), (hw + d, 0),
                       (-(hw + d), -(hh + d)), (hw + d, -(hh + d)), (-(hw + d), hh + d), (hw + d, hh + d)):
            t.SetPosition(K.VECTOR2I(MM(cx + dx), MM(cy + dy)))
            r = bbox(t)
            if clear(r, placed):
                best = r
                break
        if best:
            break
    if best:
        placed.append(best)
        moved.append(f.GetReference())
    else:
        t.SetPosition(old)
        t.SetVisible(False)
        hidden.append(f.GetReference())
print("kept %d: %s" % (len(kept), " ".join(sorted(kept))))
print("moved %d: %s" % (len(moved), " ".join(sorted(moved))))
print("hidden (no clear spot) %d: %s" % (len(hidden), " ".join(sorted(hidden))))
b.Save(OUT)
