"""Phase 1 of the audit fixes on rev3.kicad_pcb: netlist sync + re-placement.

Run with KiCad's python:  python.exe place_fix.py [--dry]
"""
import sys, os, re, io, csv, math
sys.path.insert(0, r"C:/Users/mleggiero/Documents/KiCad/taxelscan/tmp/usb-power-deps")
import pcbnew as K

ROOT = r"C:/Users/mleggiero/Documents/KiCad/taxelscan"
REV3 = ROOT + "/boards/rev3"
PCB = REV3 + "/rev3.kicad_pcb"
FPROOT = r"C:/Program Files/KiCad/10.0/share/kicad/footprints"
DRY = "--dry" in sys.argv

def mm(v): return K.ToMM(v)
def V(x, y): return K.VECTOR2I(K.FromMM(x), K.FromMM(y))

# ----------------------------------------------------------------- netlist
netmap = {}   # ref -> {pin: net}
txt = io.open(REV3 + "/rev3.net", encoding="utf-8").read()
for name, body in re.findall(r'\(net \(code "\d+"\) \(name "([^"]+)"\)(.*?)\n    \)', txt, re.S):
    for ref, pin in re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)\)', body):
        netmap.setdefault(ref, {})[pin] = name
bom = {}
for row in csv.DictReader(io.open(REV3 + "/BOM.csv", encoding="utf-8")):
    for r in row["Reference"].replace(" ", "").split(","):
        m = re.fullmatch(r"([A-Z]+)(\d+)-[A-Z]*(\d+)", r)
        refs = ["%s%d" % (m.group(1), i) for i in range(int(m.group(2)), int(m.group(3)) + 1)] if m else [r]
        for x in refs: bom[x] = (row["Value"], row["Footprint"])

b = K.LoadBoard(PCB)

# ----------------------------------------------------------------- net rename
RENAMES = {"USB_DP_C": "USBC_D_P", "USB_DM_C": "USBC_D_N", "USB_DP": "USB_D_P", "USB_DM": "USB_D_N",
           "BUS_A": "BUS_P", "BUS_B": "BUS_N", "SYNC_A": "SYNC_P", "SYNC_B": "SYNC_N"}
for ni in list(b.GetNetInfo().NetsByNetcode().values()):
    n = ni.GetNetname().lstrip("/")
    if n in RENAMES: ni.SetNetname(RENAMES[n])
    elif ni.GetNetname().startswith("/"): ni.SetNetname(n)

def net(name):
    if not name: return None
    n = b.FindNet(name)
    if n is None:
        n = K.NETINFO_ITEM(b, name); b.Add(n)
    return n

# ----------------------------------------------------------------- footprints
def fp_path(fpname):
    lib, name = fpname.split(":", 1)
    if lib == "FlexiTac":
        return ROOT + "/libraries/FlexiTac.pretty", name
    return FPROOT + "/" + lib + ".pretty", name

def load_fp(ref):
    value, fpname = bom[ref]
    libdir, name = fp_path(fpname)
    f = K.FootprintLoad(libdir, name)
    assert f is not None, (ref, fpname)
    f.SetReference(ref); f.SetValue(value)
    f.SetFPID(K.LIB_ID(fpname.split(":")[0], name))
    f.Reference().SetVisible(False); f.Value().SetVisible(False)
    return f

existing = {f.GetReference(): f for f in b.GetFootprints()}
# swap every footprint whose library name differs from the BOM, in place
def fpname_of(f):
    return "%s:%s" % (f.GetFPID().GetLibNickname(), f.GetFPID().GetLibItemName())
for ref in [r for r, f in existing.items() if r in bom and fpname_of(f).split(":")[-1] != bom[r][1].split(":")[-1]]:
    print("swap", ref, fpname_of(existing[ref]), "->", bom[ref][1])
    old = existing[ref]
    new = load_fp(ref)
    new.SetPosition(old.GetPosition()); new.SetOrientationDegrees(old.GetOrientationDegrees())
    b.Delete(old); b.Add(new); existing[ref] = new
# add new parts
for ref in ("R35", "C43", "C44", "R36", "C45"):
    if ref in existing: continue
    f = load_fp(ref); f.SetPosition(V(60, 60)); b.Add(f); existing[ref] = f
# values from BOM
for ref, f in existing.items():
    if ref in bom: f.SetValue(bom[ref][0])
# pad nets from netlist
for ref, f in existing.items():
    for p in f.Pads():
        want = netmap.get(ref, {}).get(p.GetNumber())
        if want: p.SetNet(net(want))
        else: p.SetNetCode(0)

# ----------------------------------------------------------------- placement
PLACE = {
 # analog chain, right band under the muxes
 "U7":  (146.70, 119.60, 270), "U8":  (141.50, 122.00, 270),
 "C31": (142.25, 117.90, 90),  "C32": (141.25, 117.90, 90),
 "R21": (143.25, 117.90, 270), "R22": (140.25, 117.90, 270),
 "R1":  (151.80, 116.90, 90),  "R6":  (150.20, 118.10, 0),  "R7": (150.20, 119.30, 0),
 "R8":  (150.20, 120.50, 0),   "R9":  (150.20, 121.70, 0),  "C7": (150.20, 122.90, 0),
 "R2":  (146.70, 123.40, 0),   "C5":  (140.30, 115.10, 0),
 "C26": (144.00, 125.80, 0),   "C10": (143.30, 128.15, 0),  "R10": (142.90, 129.90, 0),
 "C8":  (142.00, 126.10, 90),
 # buck beside the MCU, diodes at the bottom centre / left
 "U12": (133.90, 118.90, 180), "L2":  (137.70, 119.10, 90), "C23": (137.90, 122.50, 0),
 "C27": (131.90, 116.50, 0),   "C22": (134.40, 116.35, 0),
 "R18": (132.00, 121.95, 0),   "R19": (132.00, 120.90, 0),  "C41": (134.60, 121.30, 0), "C40": (129.70, 123.00, 90),
 "D1":  (109.00, 124.70, 0),   "D2":  (129.40, 131.60, 90),
 "C6":  (130.40, 116.50, 0),   "C28": (112.20, 120.30, 0),  "C45": (129.50, 121.00, 90),
 "R32": (127.00, 126.40, 0),   "R33": (129.00, 126.40, 0),
 "D3":  (143.20, 131.40, 0),   "R17": (143.20, 132.90, 0),  "R12": (143.20, 134.50, 0),
 # MCU bypass ring (0201), crystal group below the MCU
 "C33": (117.55, 117.92, 0),   "C24": (117.55, 119.92, 0),  "C34": (117.55, 121.92, 0),
 "C11": (121.35, 125.95, 90),  "C25": (122.55, 125.95, 90), "C13": (125.35, 125.95, 90),
 "C16": (125.50, 115.60, 90),  "C17": (124.15, 115.60, 90), "C18": (122.55, 115.60, 90),
 "C15": (127.60, 117.92, 0),   "C36": (127.60, 118.32, 0),  "C35": (127.60, 120.32, 0),  "C14": (127.60, 120.72, 0),
 "Y1":  (121.95, 130.30, 0),   "C20": (118.50, 130.30, 90), "C21": (124.70, 130.90, 90),
 "R36": (122.20, 128.15, 180), "C42": (126.20, 131.50, 0),  "R27": (129.45, 134.80, 0),
 "SW1": (121.80, 134.30, 180),
 # regulator support above the MCU
 "L1":  (122.10, 113.60, 180), "C19": (120.05, 115.50, 180),
 "C44": (119.05, 113.40, 0),   "C43": (126.90, 113.95, 90), "R35": (126.60, 111.45, 90),
 # left-bottom block: data transceiver, sync transceiver, pulls, links
 "U10": (112.30, 116.60, 180), "C4":  (108.05, 118.50, 90), "C29": (110.00, 120.30, 0),
 "R3":  (104.00, 120.30, 0),   "R4":  (106.20, 120.30, 0),  "C39": (104.90, 122.60, 0),
 "R11": (108.60, 122.30, 0),   "Q1":  (114.40, 121.90, 180), "R28": (114.40, 123.35, 0),
 "R29": (116.90, 123.40, 0),   "R5":  (116.90, 124.50, 0),  "R20": (116.90, 125.60, 0),
 "R25": (116.90, 126.70, 0),   "R30": (116.90, 127.80, 0),  "R31": (116.90, 128.90, 0),
 "C9":  (113.00, 124.90, 0),   "U11": (111.30, 129.00, 180), "C12": (107.10, 130.00, 90),
 # address jumpers between the two FFC connectors
 "JP1": (124.30, 101.80, 90),  "JP2": (127.00, 101.80, 90), "JP3": (125.60, 105.00, 0),
}
for ref, (x, y, rot) in PLACE.items():
    f = existing[ref]
    f.SetPosition(V(x, y)); f.SetOrientationDegrees(rot)

# ----------------------------------------------------------------- orientation: pad 1 (or the named pad) must face its partner
# ref: (own pad, partner ref, partner pad). A two-terminal part is flipped 180 deg
# when its other pad is nearer the partner - the stub then never has to cross a lane.
FACE = {"C33": ("1", "U9", "1"), "C24": ("1", "U9", "6"), "C34": ("1", "U9", "11"), "C11": ("1", "U9", "20"),
        "C25": ("1", "U9", "23"), "C13": ("1", "U9", "30"), "C16": ("1", "U9", "46"), "C17": ("1", "U9", "49"),
        "C18": ("1", "U9", "53"), "C15": ("1", "U9", "45"), "C36": ("1", "U9", "44"), "C35": ("1", "U9", "39"),
        "C14": ("1", "U9", "38"), "C5": ("1", "U5", "24"), "C6": ("1", "U6", "24"), "C7": ("1", "U7", "8"),
        "C8": ("1", "U8", "9"), "C12": ("1", "U11", "8"), "C29": ("1", "U10", "8"), "C40": ("1", "U13", "12"),
        "C42": ("1", "U14", "1"), "C27": ("1", "U12", "4"), "C31": ("1", "U8", "2"), "C32": ("1", "U8", "3"),
        "C26": ("1", "U8", "10"), "C20": ("1", "Y1", "1"), "C21": ("1", "Y1", "3"), "C19": ("1", "L1", "2"),
        "C43": ("1", "C16", "1"), "L2": ("1", "U12", "3"), "C23": ("1", "L2", "2"), "C45": ("1", "U9", "42"),
        "R21": ("2", "C31", "1"), "R22": ("2", "C32", "1"), "R36": ("1", "U9", "22"), "C36": ("1", "U9", "44"),
        "L1": ("1", "U9", "48"), "R26": ("2", "C36", "1"), "R35": ("2", "C43", "1"), "C22": ("1", "C27", "1")}
def padxy(ref, num):
    for p in existing[ref].Pads():
        if p.GetNumber() == num: return mm(p.GetPosition().x), mm(p.GetPosition().y)
for ref, (own, pref, ppad) in FACE.items():
    f = existing[ref]
    pads = {p.GetNumber(): p for p in f.Pads()}
    if len(pads) != 2: continue
    other = [n for n in pads if n != own][0]
    tx, ty = padxy(pref, ppad)
    ox, oy = padxy(ref, own); ax, ay = padxy(ref, other)
    if math.hypot(ax - tx, ay - ty) < math.hypot(ox - tx, oy - ty):
        f.SetOrientationDegrees((f.GetOrientationDegrees() + 180) % 360)
        print("flipped", ref, "so pad", own, "faces", pref + "." + ppad)

# ----------------------------------------------------------------- rule area for the fine-pitch escape rules (see rev3.kicad_dru)
for z in list(b.Zones()):
    if z.GetZoneName() == "U9_escape": b.Delete(z)
z = K.ZONE(b)
z.SetZoneName("U9_escape"); z.SetIsRuleArea(True)
z.SetDoNotAllowTracks(False); z.SetDoNotAllowVias(False); z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False); z.SetDoNotAllowZoneFills(False)
ls = K.LSET()
for l in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"): ls.addLayer(b.GetLayerID(l))
z.SetLayerSet(ls)
poly = z.Outline(); poly.NewOutline()
for x, y in ((116.0, 112.0), (129.5, 112.0), (129.5, 132.0), (116.0, 132.0)): poly.Append(K.FromMM(x), K.FromMM(y))
b.Add(z)
z2 = K.ZONE(b)
z2.SetZoneName("U13_escape"); z2.SetIsRuleArea(True)
z2.SetDoNotAllowTracks(False); z2.SetDoNotAllowVias(False); z2.SetDoNotAllowPads(False); z2.SetDoNotAllowFootprints(False); z2.SetDoNotAllowZoneFills(False)
z2.SetLayerSet(ls)
poly2 = z2.Outline(); poly2.NewOutline()
for x, y in ((130.0, 122.6), (134.6, 122.6), (134.6, 127.4), (130.0, 127.4)): poly2.Append(K.FromMM(x), K.FromMM(y))
b.Add(z2)
z3 = K.ZONE(b)
z3.SetZoneName("U8_escape"); z3.SetIsRuleArea(True)
z3.SetDoNotAllowTracks(False); z3.SetDoNotAllowVias(False); z3.SetDoNotAllowPads(False); z3.SetDoNotAllowFootprints(False); z3.SetDoNotAllowZoneFills(False)
z3.SetLayerSet(ls)
poly3 = z3.Outline(); poly3.NewOutline()
for x, y in ((139.0, 116.5), (145.0, 116.5), (145.0, 128.0), (139.0, 128.0)): poly3.Append(K.FromMM(x), K.FromMM(y))
b.Add(z3)

# ----------------------------------------------------------------- checks
def box(f):
    f.BuildCourtyardCaches(); bb = f.GetCourtyard(K.F_Cu).BBox()
    if not bb.GetWidth(): bb = f.GetBoundingBox(False, False)
    return (mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetEnd().x), mm(bb.GetEnd().y))
boxes = {r: box(f) for r, f in existing.items()}
refs = sorted(boxes)
print("=== courtyard bbox overlaps (>0.02 mm) ===")
for i, a in enumerate(refs):
    for c in refs[i+1:]:
        A, B = boxes[a], boxes[c]
        ox = min(A[2], B[2]) - max(A[0], B[0]); oy = min(A[3], B[3]) - max(A[1], B[1])
        if ox > 0.02 and oy > 0.02:
            print("  %-4s %-4s overlap %.2f x %.2f" % (a, c, ox, oy))
print("=== outline ===")
for r, (x1, y1, x2, y2) in boxes.items():
    if x1 < 99.3 or y1 < 100.3 or x2 > 152.7 or y2 > 135.7: print("  near/over edge:", r, boxes[r])
print("=== key pads ===")
for ref, pins in {"U7": "1 2 3 4 5 6 7 8", "U8": "1 2 3 6 7 8 9 10", "U12": "1 2 3 4 5", "L2": "1 2", "Y1": "1 3",
                  "R36": "1 2", "C31": "1 2", "C32": "1 2", "R21": "1 2", "R22": "1 2", "U11": "6 7 8",
                  "L1": "1 2", "C19": "1", "U11": "5 6 7 8", "C12": "1", "C5": "1 2", "C43": "1", "C44": "1", "R35": "1 2", "R2": "1", "R1": "1", "C5": "1", "C26": "1", "C8": "1"}.items():
    f = existing[ref]
    print(" ", ref, " ".join("%s:%s(%.2f,%.2f)" % (p.GetNumber(), p.GetNetname(), mm(p.GetPosition().x), mm(p.GetPosition().y))
                            for p in f.Pads() if p.GetNumber() in pins.split()))
if not DRY:
    K.SaveBoard(PCB, b)
    print("saved", PCB)

# ----------------------------------------------------------------- render
from PIL import Image, ImageDraw
S = 30
im = Image.new("RGB", (int(55 * S), int(37 * S)), "white"); d = ImageDraw.Draw(im)
def pt(x, y): return ((x - 98.5) * S, (y - 99.5) * S)
for f in existing.values():
    x1, y1, x2, y2 = boxes[f.GetReference()]
    d.rectangle((pt(x1, y1), pt(x2, y2)), outline="blue")
    for p in f.Pads():
        bb = p.GetBoundingBox()
        d.rectangle((pt(mm(bb.GetX()), mm(bb.GetY())), pt(mm(bb.GetEnd().x), mm(bb.GetEnd().y))), fill="#888")
    px, py = pt(mm(f.GetPosition().x), mm(f.GetPosition().y))
    d.text((px - 8, py - 5), f.GetReference(), fill="red")
for x in range(99, 154):
    d.line((pt(x, 100), pt(x, 136)), fill="#eee"); d.text(pt(x, 99.6), str(x), fill="#aaa")
for y in range(100, 137):
    d.line((pt(99, y), pt(153, y)), fill="#eee"); d.text(pt(98.6, y), str(y), fill="#aaa")
im.save(ROOT + "/tmp/placement-fix.png")
print("rendered tmp/placement-fix.png")
