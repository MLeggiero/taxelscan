"""Grown-board re-placement of rev3: 70 x 38 mm, connectors keep their edge offsets,
every other part re-placed by function block for routability.
Run with KiCad python:  python.exe place_grow.py [--dry]
"""
import sys, os, io, math, shutil
sys.path.insert(0, r"C:/Users/mleggiero/Documents/KiCad/taxelscan/tmp/usb-power-deps")
import pcbnew as K
ROOT = r"C:/Users/mleggiero/Documents/KiCad/taxelscan"; REV3 = ROOT + "/boards/rev3"
SRC = os.path.dirname(os.path.abspath(__file__)) + "/placed3.kicad_pcb"
PCB = REV3 + "/rev3.kicad_pcb"
DRY = "--dry" in sys.argv
X0, Y0, X1, Y1 = 93.0, 100.0, 163.0, 138.0
def mm(v): return K.ToMM(v)
def V(x, y): return K.VECTOR2I(K.FromMM(x), K.FromMM(y))

b = K.LoadBoard(SRC)
for t in list(b.GetTracks()): b.Delete(t)
for d in list(b.GetDrawings()):
    if d.GetLayerName() == "Edge.Cuts": b.Delete(d)
for (ax, ay, bx, by) in ((X0, Y0, X1, Y0), (X1, Y0, X1, Y1), (X1, Y1, X0, Y1), (X0, Y1, X0, Y0)):
    s = K.PCB_SHAPE(b); s.SetShape(K.SHAPE_T_SEGMENT); s.SetLayer(K.Edge_Cuts)
    s.SetStart(V(ax, ay)); s.SetEnd(V(bx, by)); s.SetWidth(K.FromMM(0.1)); b.Add(s)

existing = {f.GetReference(): f for f in b.GetFootprints()}
PLACE = {
 # connectors: same offset from their edge as on the 54 x 36 board
 "J1": (111.75, 103.40, 180), "J2": (139.75, 103.40, 180),
 "J3": (158.65, 131.97, 90),  "J4": (97.35, 131.97, 270),
 "J5": (135.75, 133.54, 0),   "J6": (109.88, 135.50, 90),
 # row drivers in one row under J1, outputs facing the connector (U4..U1 = J1 left..right)
 "U4": (100.5, 112.5, 270), "U3": (106.3, 112.5, 270), "U2": (112.1, 112.5, 270), "U1": (117.9, 112.5, 270),
 "C4": (102.77, 117.4, 90), "C3": (108.57, 117.4, 90), "C2": (114.37, 117.4, 90), "C1": (120.17, 117.4, 90),
 "C28": (121.3, 115.6, 90), "R3": (118.9, 119.0, 0), "R4": (118.9, 120.2, 0), "R5": (118.9, 121.4, 0),
 "R20": (120.85, 123.4, 0), "R30": (120.85, 124.5, 0), "R31": (120.85, 125.6, 0),
 # buck in the left block, far from the analog side
 "U12": (104.0, 120.2, 180), "L2": (109.4, 120.7, 270), "C23": (112.1, 121.4, 90), "C41": (113.7, 121.4, 90), "C9": (115.4, 121.4, 90),
 "C27": (106.3, 122.4, 0), "C22": (106.9, 120.3, 90), "R19": (102.1, 122.5, 0), "R18": (102.1, 123.6, 0),
 "D1": (98.7, 118.7, 180), "C39": (96.5, 122.0, 90),
 # RS-485 transceivers, bus pins facing the bottom lane
 "U10": (103.5, 127.8, 270), "U11": (111.3, 127.8, 270), "C29": (107.2, 130.7, 0), "C12": (115.2, 130.7, 0),
 "R11": (103.5, 132.4, 0), "R12": (111.3, 132.4, 0),
 # MCU cluster: identical geometry to the audited placement, shifted +3.95 mm in x
 "U9": (126.5, 120.72, 0),
 "C33": (121.5, 117.92, 0), "C24": (121.5, 119.92, 0), "C34": (121.5, 121.92, 0),
 "C11": (125.3, 125.95, 90), "C25": (126.5, 125.95, 90), "C13": (129.3, 125.95, 90),
 "C16": (129.45, 115.6, 90), "C17": (128.1, 115.6, 90), "C18": (126.5, 115.6, 90),
 "C15": (131.55, 117.92, 0), "C36": (133.1, 118.32, 0), "C35": (133.1, 120.32, 0), "C14": (131.55, 120.72, 0),
 "Y1": (125.9, 130.3, 0), "C20": (122.45, 130.3, 90), "C21": (129.0, 129.45, 0), "R36": (126.15, 127.9, 180),
 "L1": (126.05, 113.6, 180), "C19": (124.0, 115.5, 180), "C44": (123.0, 113.4, 0), "C43": (130.85, 113.95, 90), "R35": (130.55, 111.45, 90),
 "C45": (134.4, 121.0, 90), "R23": (128.94, 113.5, 0), "R24": (130.95, 116.0, 0),
 "R15": (135.7, 120.5, 90), "R16": (135.7, 118.0, 90), "R26": (135.0, 115.9, 0),
 # USB-C support below the MCU, the D+/D- corridor kept free at x 136.3-137.7
 "U13": (132.6, 126.3, 0), "C40": (130.4, 126.0, 90), "R32": (134.9, 125.0, 90), "R33": (134.9, 127.2, 90),
 "R34": (129.4, 127.7, 0), "C38": (139.6, 127.0, 90), "C37": (142.4, 127.5, 90),
 # USB power switch, its diodes and the status LED in the bottom-right
 "U14": (146.0, 128.5, 0), "C42": (144.1, 125.7, 90), "D4": (149.3, 129.0, 90), "R28": (147.0, 131.7, 0),
 "R27": (150.0, 132.0, 0), "Q1": (147.5, 134.0, 180), "R29": (145.0, 134.0, 90), "D2": (152.8, 134.0, 90),
 "D3": (144.0, 136.6, 0), "R17": (147.5, 136.6, 0),
 # muxes under J2 (U6 = COL_16..31 left, U5 = COL_0..15 right), amp and ADC below them
 "U6": (141.5, 112.2, 180), "U5": (156.0, 112.2, 180), "U7": (155.7, 120.2, 270), "U8": (150.5, 122.6, 270),
 "C31": (151.25, 118.5, 90), "C32": (150.25, 118.5, 90), "R21": (152.25, 118.5, 270), "R22": (149.25, 118.5, 270),
 "R1": (160.8, 117.8, 90), "R6": (159.2, 118.7, 0), "R7": (159.2, 119.9, 0), "R8": (159.2, 121.1, 0), "R9": (159.2, 122.3, 0), "C7": (159.2, 123.5, 0),
 "R2": (155.7, 124.0, 0), "C5": (149.3, 115.7, 0), "C6": (138.6, 117.6, 90),
 "C26": (153.0, 126.4, 0), "C10": (152.3, 128.75, 0), "R10": (151.9, 130.5, 0), "C8": (151.0, 126.7, 90),
 # BOOTSEL switch in the gap between the FFC connectors, address jumpers along the bottom edge
 "SW1": (125.75, 102.4, 0), "R25": (125.75, 105.6, 0),
 "JP1": (121.0, 136.5, 0), "JP2": (124.6, 136.5, 0), "JP3": (128.2, 136.5, 0),
}
missing = sorted(set(existing) - set(PLACE)); extra = sorted(set(PLACE) - set(existing))
assert not missing and not extra, (missing, extra)
for ref, (x, y, rot) in PLACE.items():
    f = existing[ref]; f.SetPosition(V(x, y)); f.SetOrientationDegrees(rot)

# two-terminal parts: the named pad must be the one nearer its partner
FACE = {"C33": ("1", "U9", "1"), "C24": ("1", "U9", "6"), "C34": ("1", "U9", "11"), "C11": ("1", "U9", "20"),
        "C25": ("1", "U9", "23"), "C13": ("1", "U9", "30"), "C16": ("1", "U9", "46"), "C17": ("1", "U9", "49"),
        "C18": ("1", "U9", "53"), "C15": ("1", "U9", "45"), "C36": ("1", "U9", "44"), "C35": ("1", "U9", "39"),
        "C14": ("1", "U9", "38"), "C28": ("1", "U9", "54"), "C5": ("1", "U5", "24"), "C6": ("1", "U6", "24"), "C7": ("1", "U7", "8"),
        "C8": ("1", "U8", "9"), "C12": ("1", "U11", "8"), "C29": ("1", "U10", "8"), "C40": ("1", "U13", "12"),
        "C42": ("1", "U14", "1"), "C27": ("1", "U12", "1"), "C22": ("1", "U12", "1"), "C31": ("1", "U8", "2"), "C32": ("1", "U8", "3"),
        "C26": ("1", "U8", "10"), "C10": ("1", "C26", "1"), "R10": ("2", "C10", "1"), "C20": ("1", "Y1", "1"), "C21": ("1", "Y1", "3"),
        "C19": ("1", "L1", "2"), "C43": ("1", "C16", "1"), "L2": ("1", "U12", "3"), "C23": ("1", "L2", "2"), "C9": ("1", "C23", "1"),
        "C41": ("1", "L2", "2"), "C45": ("1", "U9", "42"), "R21": ("2", "C31", "1"), "R22": ("2", "C32", "1"), "R36": ("1", "U9", "22"),
        "L1": ("1", "U9", "48"), "R26": ("2", "C36", "1"), "R35": ("2", "C43", "1"), "C44": ("1", "U9", "54"),
        "C1": ("1", "U1", "16"), "C2": ("1", "U2", "16"), "C3": ("1", "U3", "16"), "C4": ("1", "U4", "16"),
        "R3": ("1", "U9", "4"), "R4": ("1", "U9", "3"), "R5": ("1", "C9", "1"), "R20": ("1", "U9", "26"), "R30": ("1", "U9", "36"), "R31": ("1", "U9", "35"),
        "R15": ("2", "C45", "1"), "R16": ("1", "C45", "1"), "R23": ("1", "U9", "52"), "R24": ("1", "U9", "51"),
        "R18": ("2", "U12", "5"), "R19": ("1", "U12", "5"), "D1": ("1", "U12", "1"), "C39": ("1", "D1", "2"),
        "R11": ("1", "U10", "6"), "R12": ("1", "U11", "6"),
        "R32": ("1", "U13", "7"), "R33": ("1", "U13", "8"), "R34": ("2", "U13", "4"), "C38": ("1", "J5", "A9"), "C37": ("1", "J5", "A9"),
        "D4": ("2", "U14", "6"), "R28": ("1", "U14", "5"), "R27": ("1", "U14", "5"), "R29": ("1", "Q1", "1"), "D2": ("2", "C37", "1"),
        "R17": ("2", "D3", "2"), "R25": ("2", "SW1", "1"),
        "R1": ("1", "U5", "1"), "R2": ("1", "U7", "5"), "R6": ("2", "U7", "1"), "R8": ("2", "U7", "7"), "R7": ("1", "U7", "2"), "R9": ("1", "U7", "6")}
def padxy(ref, num):
    for p in existing[ref].Pads():
        if p.GetNumber() == num: return mm(p.GetPosition().x), mm(p.GetPosition().y)
for ref, (own, pref, ppad) in FACE.items():
    f = existing[ref]; pads = {p.GetNumber(): p for p in f.Pads() if p.GetNumber()}   # 0201 footprints carry two extra paste-only pads
    if len(pads) != 2: continue
    other = [n for n in pads if n != own][0]
    tx, ty = padxy(pref, ppad); ox, oy = padxy(ref, own); ax, ay = padxy(ref, other)
    if math.hypot(ax - tx, ay - ty) < math.hypot(ox - tx, oy - ty):
        f.SetOrientationDegrees((f.GetOrientationDegrees() + 180) % 360)
        print("flipped", ref, "so pad", own, "faces", pref + "." + ppad)

# zones: fills follow the new outline, rule areas follow the fine-pitch parts
ls = K.LSET()
for l in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"): ls.addLayer(b.GetLayerID(l))
for z in list(b.Zones()):
    if z.GetIsRuleArea(): b.Delete(z); continue
    poly = z.Outline(); poly.RemoveAllContours(); poly.NewOutline()
    for x, y in ((X0 + 0.5, Y0 + 0.5), (X1 - 0.5, Y0 + 0.5), (X1 - 0.5, Y1 - 0.5), (X0 + 0.5, Y1 - 0.5)): poly.Append(K.FromMM(x), K.FromMM(y))
    print("zone", z.GetNetname(), "resized")
AREAS = {"U9_escape": (119.5, 111.0, 135.0, 132.5), "U13_escape": (130.3, 124.0, 135.0, 128.6), "U8_escape": (148.0, 117.0, 154.0, 128.6)}
for name, (x0, y0, x1, y1) in AREAS.items():
    z = K.ZONE(b); z.SetZoneName(name); z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(False); z.SetDoNotAllowVias(False); z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False); z.SetDoNotAllowZoneFills(False)
    z.SetLayerSet(ls); poly = z.Outline(); poly.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)): poly.Append(K.FromMM(x), K.FromMM(y))
    b.Add(z)

# checks
def box(f):
    f.BuildCourtyardCaches(); bb = f.GetCourtyard(K.F_Cu).BBox()
    if not bb.GetWidth(): bb = f.GetBoundingBox(False, False)
    return (mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetEnd().x), mm(bb.GetEnd().y))
boxes = {r: box(f) for r, f in existing.items()}; refs = sorted(boxes)
print("=== courtyard bbox overlaps (>0.02 mm) ===")
for i, a in enumerate(refs):
    for c in refs[i+1:]:
        A, B = boxes[a], boxes[c]
        ox = min(A[2], B[2]) - max(A[0], B[0]); oy = min(A[3], B[3]) - max(A[1], B[1])
        if ox > 0.02 and oy > 0.02: print("  %-4s %-4s overlap %.2f x %.2f" % (a, c, ox, oy))
print("=== pad-to-pad conflicts between different parts (<0.15 mm, different nets) ===")
pads = [(f.GetReference(), p) for f in existing.values() for p in f.Pads()]
for i, (ra, pa) in enumerate(pads):
    ba = pa.GetBoundingBox()
    for rb, pb in pads[i+1:]:
        if ra == rb: continue
        bb = pb.GetBoundingBox()
        gx = max(mm(ba.GetX()), mm(bb.GetX())) - min(mm(ba.GetEnd().x), mm(bb.GetEnd().x))
        gy = max(mm(ba.GetY()), mm(bb.GetY())) - min(mm(ba.GetEnd().y), mm(bb.GetEnd().y))
        if max(gx, gy) < 0.15 and pa.GetNetname() != pb.GetNetname(): print("  %s.%s %s.%s gap %.2f/%.2f" % (ra, pa.GetNumber(), rb, pb.GetNumber(), gx, gy))
print("=== decoupling: pad-1 distance to its pin (should be < 1.2 mm) ===")
for ref, (own, pref, ppad) in sorted(FACE.items()):
    if not ref.startswith("C"): continue
    tx, ty = padxy(pref, ppad); ox, oy = padxy(ref, own)
    dd = math.hypot(ox - tx, oy - ty)
    if dd > 1.2: print("  %-4s pad %s -> %s.%s  %.2f mm" % (ref, own, pref, ppad, dd))
print("=== outline ===")
for r, (x1, y1, x2, y2) in boxes.items():
    if r.startswith("J"): continue
    if x1 < X0 + 0.3 or y1 < Y0 + 0.3 or x2 > X1 - 0.3 or y2 > Y1 - 0.3: print("  near/over edge:", r, boxes[r])
if not DRY:
    pro = io.open(PCB.replace(".kicad_pcb", ".kicad_pro"), encoding="utf-8").read()
    K.SaveBoard(PCB, b)
    io.open(PCB.replace(".kicad_pcb", ".kicad_pro"), "w", encoding="utf-8", newline=chr(10)).write(pro)   # SaveBoard rewrites the project with defaults
    shutil.copy(PCB, REV3 + "/backups/audit-fixes-2026-09-10/rev3.kicad_pcb.grow-placed")
    print("saved", PCB)
from PIL import Image, ImageDraw
S = 24
im = Image.new("RGB", (int((X1 - X0 + 2) * S), int((Y1 - Y0 + 2) * S)), "white"); d = ImageDraw.Draw(im)
def pt(x, y): return ((x - X0 + 1) * S, (y - Y0 + 1) * S)
d.rectangle((pt(X0, Y0), pt(X1, Y1)), outline="black")
for f in existing.values():
    x1, y1, x2, y2 = boxes[f.GetReference()]
    d.rectangle((pt(x1, y1), pt(x2, y2)), outline="blue")
    for p in f.Pads():
        bb = p.GetBoundingBox()
        d.rectangle((pt(mm(bb.GetX()), mm(bb.GetY())), pt(mm(bb.GetEnd().x), mm(bb.GetEnd().y))), fill="#c00" if p.GetNumber() == "1" else "#888")
    px, py = pt(mm(f.GetPosition().x), mm(f.GetPosition().y))
    d.text((px - 8, py - 5), f.GetReference(), fill="black")
for x in range(int(X0), int(X1) + 1, 5): d.line((pt(x, Y0), pt(x, Y1)), fill="#ddd"); d.text(pt(x, Y0 - 0.8), str(x), fill="#888")
for y in range(int(Y0), int(Y1) + 1, 5): d.line((pt(X0, y), pt(X1, y)), fill="#ddd"); d.text(pt(X0 - 1, y), str(y), fill="#888")
im.save(ROOT + "/tmp/placement-grow.png"); print("rendered tmp/placement-grow.png")
