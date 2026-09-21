
"""render.py board.kicad_pcb out.png x0 y0 x1 y1 [scale]  - copper on F.Cu (red), B.Cu (blue), In2 (green), vias (black)"""
import sys, time, pcbnew as K
T0 = time.time()
def lap(s): print(s, "%.1f s" % (time.time() - T0), flush=True)
from PIL import Image, ImageDraw
b = K.LoadBoard(sys.argv[1]); out = sys.argv[2]; x0, y0, x1, y1 = map(float, sys.argv[3:7]); S = float(sys.argv[7]) if len(sys.argv) > 7 else 40
mm = K.ToMM
lap("loaded")
tracks = [t for t in b.GetTracks()]
vias = [t for t in tracks if isinstance(t, K.PCB_VIA)]
segs = [(b.GetLayerName(t.GetLayer()), mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y), mm(t.GetWidth()), t.GetNetname()) for t in tracks if not isinstance(t, K.PCB_VIA)]
lap("collected %d segs" % len(segs))
im = Image.new("RGB", (int((x1 - x0) * S), int((y1 - y0) * S)), "white"); d = ImageDraw.Draw(im)
def P(x, y): return ((x - x0) * S, (y - y0) * S)
COL = {"F.Cu": (220, 0, 0), "B.Cu": (0, 60, 220), "In2.Cu": (0, 160, 0), "In1.Cu": (200, 200, 200)}
for f in b.GetFootprints():
    for p in f.Pads():
        bb = p.GetBoundingBox(); fill = (150, 150, 150) if p.GetNetname() != "GND" else (200, 200, 200)
        d.rectangle((P(mm(bb.GetX()), mm(bb.GetY())), P(mm(bb.GetEnd().x), mm(bb.GetEnd().y))), fill=fill, outline=(90, 90, 90))
    px, py = P(mm(f.GetPosition().x), mm(f.GetPosition().y)); d.text((px - 8, py - 5), f.GetReference(), fill=(0, 0, 0))
for order in ("In2.Cu", "B.Cu", "F.Cu"):
    for l, ax, ay, bx, by, w, n in segs:
        if l != order: continue
        d.line((P(ax, ay), P(bx, by)), fill=COL.get(l, (0, 0, 0)), width=max(1, int(w * S)))
for t in vias:
    if True:
        x, y = mm(t.GetPosition().x), mm(t.GetPosition().y); r = mm(t.GetWidth(K.F_Cu)) / 2
        d.ellipse((P(x - r, y - r), P(x + r, y + r)), outline=(0, 0, 0), width=2)
import math
for l, ax, ay, bx, by, w, n in segs:
    if math.hypot(bx - ax, by - ay) > 1.5:
        x, y = (ax + bx) / 2, (ay + by) / 2
        if x0 < x < x1 and y0 < y < y1: d.text(P(x, y), n[:9], fill=(0, 0, 0))
lap("drawn")
im.save(out); print("saved", out)
