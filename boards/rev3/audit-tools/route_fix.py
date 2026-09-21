"""Phase 2 of the audit fixes: rip and re-route rev3.kicad_pcb with intent.

  python.exe route_fix.py rip        strip every net except the rev-1 fanout and the kept trunks
  python.exe route_fix.py stubs      pin -> capacitor runs on F.Cu, no vias
  python.exe route_fix.py analog     SENSE/GAIN/AMP/ADC/VREF/crystal/regulator nets, F.Cu only
  python.exe route_fix.py gnd        a via at every SMD GND pad
  python.exe route_fix.py pairs      the four differential pairs, N hugging P
  python.exe route_fix.py power      5 V feeds, buck output, +3.3V cap vias
  python.exe route_fix.py digital    everything else, F.Cu/B.Cu first, In2.Cu as fallback
  python.exe route_fix.py close      fill zones, DRC, route what KiCad still calls unconnected
  python.exe route_fix.py prune      delete dangling tracks/vias per DRC
  python.exe route_fix.py drc        fill + DRC summary
"""
import sys, os, io, re, json, math, struct, subprocess, uuid, time, collections
ROOT = r"C:/Users/mleggiero/Documents/KiCad/taxelscan"
sys.path.insert(0, ROOT + "/tmp/usb-power-deps")
sys.path.insert(0, ROOT + "/boards/rev3")
os.chdir(ROOT)
import numpy as np
from scipy.ndimage import label as cc_label, distance_transform_edt
import pcbnew as K
import maze_route as M

PCB = ROOT + "/boards/rev3/rev3.kicad_pcb"
KICLI = r"C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
M.GRID = 0.05; M.VIA_SIZE = 0.5; M.VIA_DRILL = 0.3
M.EDGE_VIA = M.VIA_SIZE / 2 + M.EDGE_RULE + M.SAFETY
M.LAYERS = ["F.Cu", "In2.Cu", "B.Cu"]
ALL = ["F.Cu", "In2.Cu", "B.Cu"]; TOP = ["F.Cu"]; OUTER = ["F.Cu", "B.Cu"]
SEG_FMT = chr(9) + "(segment" + chr(10) + chr(9)*2 + "(start %.4f %.4f)" + chr(10) + chr(9)*2 + "(end %.4f %.4f)" + chr(10) + chr(9)*2 + "(width 0.15)" + chr(10) + chr(9)*2 + "(layer " + chr(34) + "F.Cu" + chr(34) + ")" + chr(10) + chr(9)*2 + "(net " + chr(34) + "%s" + chr(34) + ")" + chr(10) + chr(9)*2 + "(uuid " + chr(34) + "%s" + chr(34) + ")" + chr(10) + chr(9) + ")"

ANALOG = ("SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B", "AMP_A", "AMP_B", "ADC_A", "ADC_B", "VREF",
          "XIN", "XOUT", "XOUT_MCU", "VREG_LX", "VREG_AVDD", "ADC_AVDD", "FB", "RAIL_MON")
NOISY = ("SW_NODE", "VREG_LX", "BUS_P", "BUS_N", "SYNC_P", "SYNC_N", "USB_D_P", "USB_D_N", "USBC_D_P", "USBC_D_N",
         "ROW_CLK", "ROW_CLK_MCU", "ADC_SCK", "BOOTSEL")

def keep_net(n):
    return False
    return bool(re.fullmatch(r"(ROW|COL)_\d+|SR_CHAIN_\d|ROW_CLK|ROW_LATCH|ROW_CLK_MCU|ROW_LATCH_MCU|MUX_S[0-3]|"
                             r"\+5V_BUS|USB_BUS_SW|USB_ILIM", n))

def set_width(w, rule=0.15):
    M.TRACK_W = w
    M.RULE = rule
    M.EDGE_TRACK = M.TRACK_W / 2.0 + M.EDGE_RULE + M.SAFETY
FINE = ("U9", "U13", "U8")   # parts whose escapes use the 0.1/0.1 rule areas
FINE_AREAS = [(119.5, 111.0, 135.0, 132.5), (130.3, 124.0, 135.0, 128.6), (148.0, 117.0, 154.0, 128.6)]

# ------------------------------------------------------------------ C++ A*
def astar(free, via_ok, starts, goals, via_goal=False):
    layers = M.LAYERS; h, w = free[layers[0]].shape; n = h * w
    fb = np.zeros((h, w), np.uint8); gb = np.zeros_like(fb)
    for l, k in enumerate(layers): fb |= free[k].astype(np.uint8) << l
    for k, i, j in goals: gb[i, j] |= 1 << layers.index(k)
    if via_goal: gb[via_ok] = 7
    if not np.any(gb): return None
    hs = (distance_transform_edt(gb == 0) * 9.899).astype(np.float32)
    seeds = np.array(sorted({layers.index(k) * n + i * w + j for k, i, j in starts}), dtype=np.uint32)
    if not len(seeds): return None
    fin, fout = ROOT + "/tmp/grid-route-in.bin", ROOT + "/tmp/grid-route-out.bin"
    with open(fin, "wb") as f:
        f.write(struct.pack("III", h, w, len(seeds))); seeds.tofile(f); fb.tofile(f)
        via_ok.astype(np.uint8).tofile(f); gb.tofile(f); hs.tofile(f)
    subprocess.run([ROOT + "/tmp/grid_route.exe", fin, fout], check=True, capture_output=True)
    with open(fout, "rb") as f:
        count = struct.unpack("I", f.read(4))[0]; ids = np.fromfile(f, dtype=np.uint32, count=count)
    return [(layers[int(v) // n], int(v) % n // w, int(v) % w) for v in ids] or None

# ------------------------------------------------------------------ board wrapper
class Router:
    def __init__(self):
        self.text = io.open(PCB, encoding="utf-8").read()
        self.b = M.Board(self.text)
        self.blocks = []
        self.native = K.LoadBoard(PCB)
        self.padpos = {}; self.padsize = {}; self.padkey = {}
        for f in self.native.GetFootprints():
            for p in f.Pads():
                on = [l for l in ALL if p.IsOnLayer(self.native.GetLayerID(l))]
                key = p.GetNumber(); k = 2
                while (f.GetReference(), key) in self.padpos:      # duplicate numbers (FFC mounting pads)
                    key = "%s@%d" % (p.GetNumber(), k); k += 1
                self.padkey[p.m_Uuid.AsString()] = (f.GetReference(), key)
                self.padpos[(f.GetReference(), key)] = (K.ToMM(p.GetPosition().x), K.ToMM(p.GetPosition().y), on, p.GetNetname())
                bb = p.GetBoundingBox()
                self.padsize[(f.GetReference(), key)] = (K.ToMM(bb.GetWidth()), K.ToMM(bb.GetHeight()))
        self._dist_cache = {}
        # fanout stub tips whose net has nothing but stubs yet: keep other copper away
        self.tips = []
        tp = ROOT + "/tmp/fanout_tips.json"
        if os.path.exists(tp):
            tips = json.load(open(tp))
            per = collections.Counter(t[2] for t in tips)
            have = collections.Counter(n_ for n_, l, *_ in self.b.tracks)
            self.tips = [t for t in tips if have.get(t[2], 0) <= per[t[2]] and t[2] not in ("GND", "+3.3V", "VCORE")]

    def tip_mask(self, net, r=0.225, length=1.0):
        """a band straight ahead of every pending stub tip of another net."""
        b = self.b
        m = np.zeros((b.h, b.w), bool)
        rr = int(r / M.GRID) + 1
        disc = [(di, dj) for di in range(-rr, rr + 1) for dj in range(-rr, rr + 1) if di * di + dj * dj <= rr * rr]
        for x, y, n_, dx, dy in self.tips:
            if n_ == net: continue
            for k in range(0, int(length / M.GRID) + 1):
                i, j = b.cell(x + dx * k * M.GRID, y + dy * k * M.GRID)
                for di, dj in disc:
                    if 0 <= i + di < b.h and 0 <= j + dj < b.w: m[i + di, j + dj] = True
        return m

    def flush(self):
        if self.blocks:
            self.text = self.text.rstrip()[:-1] + "\n" + "\n".join(self.blocks) + "\n)\n"
            io.open(PCB, "w", encoding="utf-8", newline="\n").write(self.text)
            self.blocks = []
        self.b = M.Board(self.text); self._dist_cache = {}

    def cells(self, ref, num, layers):
        x, y, on, net = self.padpos[(ref, num)]
        i, j = self.b.cell(x, y)
        return [(l, i, j) for l in on if l in layers]

    def island(self, seed, own, free, net, layers):
        """every free cell of this net's copper connected to the seed cells (through vias too)."""
        b = self.b
        labels = {l: cc_label(own[l], np.ones((3, 3)))[0] for l in M.LAYERS}
        ids = {(l, int(labels[l][y, x])) for l, y, x in seed if labels[l][y, x]}
        through = [(x, y) for n_, x, y, *_ in b.vias if n_ == net]
        through += [(x, y) for n_, on, x, y, *_ in b.pads if n_ == net and len(on) == 3]
        links = []
        for x, y in through:
            i, j = b.cell(x, y)
            links.append({(l, int(labels[l][i, j])) for l in M.LAYERS if labels[l][i, j]})
        changed = True
        while changed:
            changed = False
            for link in links:
                if ids & link and not link <= ids: ids |= link; changed = True
        out = []
        for l in M.LAYERS:
            if l not in layers: continue
            keys = [i for ll, i in ids if ll == l]
            if not keys: continue
            mask = np.isin(labels[l], keys) & free[l]
            out.extend((l, int(i), int(j)) for i, j in zip(*np.where(mask)))
        # a pad centre may be a non-free cell (edge band); keep the seeds regardless
        return out or [c for c in seed if own[c[0]][c[1], c[2]]]

    def dist_to_nets(self, nets, reach=0.6):
        key = tuple(sorted(nets))
        if key in self._dist_cache: return self._dist_cache[key]
        b = self.b
        INF = np.float32(1e6)
        dist = {l: np.full((b.h, b.w), INF, np.float32) for l in M.LAYERS}
        for n_, on, x, y, w, h_, a in b.pads:
            if n_ in nets:
                for l in on: b._dist_rect(dist[l], x, y, w, h_, a, reach)
        for n_, l, x1, y1, x2, y2, w, _s in b.tracks:
            if n_ in nets and l in M.LAYERS: b._dist_seg(dist[l], x1, y1, x2, y2, w, reach)
        for n_, x, y, s, _s in b.vias:
            if n_ in nets:
                for l in M.LAYERS: b._dist_seg(dist[l], x, y, x, y, s, reach)
        self._dist_cache[key] = dist
        return dist

    def area_mask(self):
        b = self.b
        if not hasattr(self, "_area"):
            m = np.zeros((b.h, b.w), bool)
            for x0, y0, x1, y1 in FINE_AREAS:
                i0, j0 = b.cell(x0, y0); i1, j1 = b.cell(x1, y1)
                m[max(0, i0):i1 + 1, max(0, j0):j1 + 1] = True
            self._area = m
        return self._area

    def route(self, net, src, dst, layers=ALL, width=0.15, via_goal=False, corridor=None, avoid=None, avoid_d=0.4, label="", rule=0.15, keepout=None):
        """src/dst: lists of (ref, pin). Returns path or None.

        A route touching a FINE part is built with two rule sets: 0.1/0.1 inside
        the rule area, the normal 0.15 clearance outside, at 0.1 width throughout."""
        b = self.b
        touches = any(r in FINE for r, p in list(src) + list(dst))
        if touches and rule != 0.1:
            width = min(width, 0.1)
            saved = b.vias; b.vias = [(n_, x, y, max(s, 0.6), sp) for n_, x, y, s, sp in saved]
            set_width(width, 0.1); M.VIA_SIZE = 0.6; free_f, via_f, own = b.build(net); M.VIA_SIZE = 0.5
            b.vias = saved
            set_width(width, 0.15); free_n, via_n, _ = b.build(net)
            A = self.area_mask()
            free = {l: np.where(A, free_f[l], free_n[l]) for l in M.LAYERS}
            via_ok = np.where(A, via_f, via_n)
            set_width(width, 0.1)
        else:
            saved = b.vias
            if rule < 0.15: b.vias = [(n_, x, y, max(s, 0.6), sp) for n_, x, y, s, sp in saved]; M.VIA_SIZE = 0.6
            set_width(width, rule)
            free, via_ok, own = b.build(net)
            b.vias = saved; M.VIA_SIZE = 0.5
        for l in M.LAYERS:
            if l not in layers: free[l][:] = False
        if len(layers) == 1 and not via_goal: via_ok[:] = False
        if avoid:
            dd = self.dist_to_nets(avoid)
            for l in layers: free[l] &= (dd[l] >= avoid_d) | own[l]
        if corridor is not None:
            for l in layers: free[l] &= corridor[l] | own[l]
        if keepout is not None:
            x0, y0, x1, y1 = keepout; i0, j0 = b.cell(x0, y0); i1, j1 = b.cell(x1, y1)
            for l in layers: free[l][max(0, i0):i1 + 1, max(0, j0):j1 + 1] &= own[l][max(0, i0):i1 + 1, max(0, j0):j1 + 1]
        if self.tips:
            tm = self.tip_mask(net)
            for l in layers: free[l] &= ~tm | own[l]
            via_ok &= ~self.tip_mask(net, 0.5)
        starts = [c for r, p in src for c in self.cells(r, p, layers)]
        starts = self.island(starts, own, free, net, layers)
        if via_goal:
            goals = []
        else:
            goals = [c for r, p in dst for c in self.cells(r, p, layers)]
            goals = self.island(goals, own, free, net, layers)
        if not starts or (not goals and not via_goal):
            print("  BLOCKED %-12s %s (no start/goal cells)" % (net, label)); return None
        path = astar(free, via_ok, starts, goals, via_goal)
        if not path:
            print("  NO ROUTE %-12s %s" % (net, label)); return None
        blocks = M.emit(b, net, path)
        # tie the path ends to the pad centres they start/end on: a path may begin
        # at a cell inside a roundrect pad's bounding box but outside its copper
        for end, pads in ((path[0], src), (path[-1], dst)):
            ex, ey = b.world(end[1], end[2])
            for r, p in pads:
                if r == "@": continue
                x, y, on, n_ = self.padpos[(r, p)]
                pw, ph = self.padsize.get((r, p), (0.0, 0.0))
                if end[0] in on and abs(ex - x) <= pw / 2 + 0.01 and abs(ey - y) <= ph / 2 + 0.01 and (abs(ex - x) > 0.03 or abs(ey - y) > 0.03):
                    blocks.append((SEG_FMT % (x, y, ex, ey, net, uuid.uuid4())).replace("(width 0.15)", "(width %g)" % M.TRACK_W).replace('"F.Cu"', '"%s"' % end[0]))
                    b.tracks.append((net, end[0], x, y, ex, ey, M.TRACK_W, None))
                    break
        via_xy = None
        if via_goal:
            _, yy, xx = path[-1]; x, y = b.world(yy, xx); via_xy = (x, y)
            blocks.append('\t(via\n\t\t(at %.4f %.4f)\n\t\t(size %g)\n\t\t(drill %g)\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "%s")\n\t\t(uuid "%s")\n\t)'
                          % (x, y, M.VIA_SIZE, M.VIA_DRILL, net, uuid.uuid4()))
        self.blocks.extend(blocks)
        M.register(b, net, path, via_xy)
        L = sum(math.hypot(*(np.subtract(b.world(*p[1:]), b.world(*q[1:])))) for p, q in zip(path, path[1:]) if p[0] == q[0])
        nv = sum(1 for p, q in zip(path, path[1:]) if p[0] != q[0]) + (1 if via_goal else 0)
        print("  ok %-12s %-28s %5.1f mm  vias %d  %s" % (net, label, L, nv, "/".join(sorted({p[0] for p in path}))))
        return path

    def chain(self, net, seq, **kw):
        """route pad seq[0]->seq[1], then the growing copper -> seq[2], ..."""
        for k in range(1, len(seq)):
            self.route(net, seq[:k], [seq[k]], label="%s.%s>%s.%s" % (seq[k-1] + seq[k]), **kw)

    def corridor_from(self, path, halfwidth=0.5, pads=(), near=1.5):
        b = self.b
        out = {}
        for l in M.LAYERS:
            m = np.zeros((b.h, b.w), bool)
            for ll, i, j in path:
                if ll == l: m[i, j] = True
            for r, p in pads:
                for ll, i, j in self.cells(r, p, [l]): m[i, j] = True
            if not m.any(): out[l] = m; continue
            d = distance_transform_edt(~m) * M.GRID
            out[l] = d <= halfwidth
            for r, p in pads:
                for ll, i, j in self.cells(r, p, [l]):
                    dd = distance_transform_edt(~np.isin(np.arange(b.h * b.w).reshape(b.h, b.w), [i * b.w + j])) * M.GRID
                    out[l] |= dd <= near
        return out

# ------------------------------------------------------------------ stages
def stage_rip():
    b = K.LoadBoard(PCB)
    ep = b.FindFootprintByReference("U9").FindPadByNumber("61")
    epb = ep.GetBoundingBox()
    n = collections.Counter()
    for t in list(b.GetTracks()):
        net = t.GetNetname()
        if keep_net(net): continue
        if net == "GND" and isinstance(t, K.PCB_VIA) and epb.Contains(t.GetPosition()): continue
        n[net] += 1; b.Delete(t)
    K.SaveBoard(PCB, b)
    print("ripped", sum(n.values()), "items on", len(n), "nets; kept",
          sum(1 for t in b.GetTracks()), "items")

PAIRS_33 = [("U9", "1", "C33"), ("U9", "11", "C34"), ("U9", "20", "C11"), ("U9", "30", "C13"), ("U9", "38", "C14"),
            ("U9", "45", "C15"), ("U9", "49", "C17"), ("U9", "53", "C18"), ("U9", "54", "C28"),
            ("U5", "24", "C5"), ("U6", "24", "C6"), ("U7", "8", "C7"), ("U8", "9", "C8"), ("U10", "8", "C29"),
            ("U11", "8", "C12"), ("U13", "12", "C40")]

def stage_stubs(R):
    # core rail
    R.route("VREG_LX", [("U9", "48")], [("L1", "1")], TOP, 0.2, rule=0.1, label="LX")
    R.route("VCORE", [("L1", "2")], [("C19", "1")], TOP, 0.25, rule=0.1, label="L1>C19")
    for pin, cap in (("6", "C24"), ("23", "C25"), ("39", "C35")):
        R.route("VCORE", [("U9", pin)], [(cap, "1")], TOP, 0.1, rule=0.1, label="U9.%s>%s" % (pin, cap))
    if not R.route("VCORE", [("C19", "1")], [("U9", "50")], TOP, 0.1, rule=0.1, label="C19>VREG_FB"):
        R.route("VCORE", [("C19", "1")], [("U9", "50")], ALL, 0.1, rule=0.1, label="C19>VREG_FB (via)")
    # regulator analogue supply and the ADC supply
    R.chain("VREG_AVDD", [("U9", "46"), ("C16", "1"), ("C43", "1"), ("R35", "2")], layers=TOP, width=0.1, rule=0.1)
    R.chain("ADC_AVDD", [("U9", "44"), ("C36", "1"), ("R26", "2")], layers=TOP, width=0.1, rule=0.1)
    # +3.3V pins through their capacitors, F.Cu only (after the core rail, so no
    # stub can cut through the gap between L1 and C19 first)
    for ref, pin, cap in PAIRS_33:
        f = ref in FINE or ref == "U8"
        R.route("+3.3V", [(ref, pin)], [(cap, "1")], TOP, 0.1 if f else 0.15, rule=0.1 if f else 0.15, label="%s.%s>%s" % (ref, pin, cap))
    # row driver rail
    for ref, pin, cap in (("U1", "16", "C1"), ("U2", "16", "C2"), ("U3", "16", "C3"), ("U4", "16", "C4")):
        R.route("ROW_VCC", [(ref, pin)], [(cap, "1")], TOP, label="%s.%s>%s" % (ref, pin, cap))
    # buck input and switch node
    R.route("+5V", [("U12", "4")], [("C27", "1")], TOP, 0.3, label="VIN>C27")
    R.route("+5V", [("C27", "1")], [("C22", "1")], TOP, 0.3, label="C27>C22")
    R.route("SW_NODE", [("U12", "3")], [("L2", "1")], TOP, 0.3, label="SW>L2")
    R.route("+3.3V", [("L2", "2")], [("C23", "1")], TOP, 0.3, label="L2>C23")
    R.route("+3.3V", [("C23", "1")], [("R18", "1")], TOP, label="C23>R18 FB sense")
    R.route("+5V_USB", [("U14", "1")], [("C42", "1")], TOP, 0.3, label="U14>C42")
    R.flush()

def stage_analog(R):
    R.chain("ADC_A", [("U8", "2"), ("C31", "1"), ("R21", "2")], layers=TOP, width=0.1, rule=0.1)
    R.chain("ADC_B", [("U8", "3"), ("C32", "1"), ("R22", "2")], layers=TOP, width=0.1, rule=0.1)
    # op-amp outputs first (they exit the top/bottom rows outward), then the
    # gain node, then the sense line, which takes the lane nearest the muxes
    R.route("AMP_A", [("U7", "1")], [("R6", "2")], TOP, label="U7.1>R6.2")
    R.route("AMP_A", [("U7", "1"), ("R6", "2")], [("R21", "1")], OUTER, avoid=("SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B"), avoid_d=0.3, label="AMP_A>R21 (via ok)")
    R.chain("GAIN_A", [("U7", "2"), ("R6", "1"), ("R7", "1")], layers=TOP)
    R.chain("SENSE_A", [("U5", "1"), ("U7", "3"), ("R1", "1")], layers=TOP)
    R.route("AMP_B", [("U7", "7")], [("R8", "2")], TOP, label="U7.7>R8.2")
    R.route("AMP_B", [("U7", "7"), ("R8", "2")], [("R22", "1")], OUTER, avoid=("SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B"), avoid_d=0.3, label="AMP_B>R22 (via ok)")
    R.chain("GAIN_B", [("U7", "6"), ("R8", "1"), ("R9", "1")], layers=TOP)
    R.chain("SENSE_B", [("U6", "1"), ("U7", "5"), ("R2", "1")], layers=TOP)
    R.chain("VREF", [("U8", "10"), ("C26", "1"), ("C10", "1"), ("R10", "2")], layers=TOP, width=0.1, rule=0.1)
    # XOUT first: R36 sits straight below pins 21/22 between C11 and C25, and
    # XIN routed first walls it off from that corridor (XIN then passes left of R36)
    R.route("XOUT_MCU", [("U9", "22")], [("R36", "1")], TOP, 0.1, rule=0.1, label="XOUT_MCU")
    R.chain("XOUT", [("R36", "2"), ("Y1", "3"), ("C21", "1")], layers=TOP, width=0.1, rule=0.1)
    R.chain("XIN", [("U9", "21"), ("Y1", "1"), ("C20", "1")], layers=TOP, width=0.1, rule=0.1)
    R.chain("FB", [("U12", "5"), ("R19", "1"), ("R18", "2")], layers=TOP)
    R.chain("RAIL_MON", [("U9", "42"), ("C45", "1"), ("R16", "1"), ("R15", "2")], layers=TOP, width=0.1, rule=0.1)
    for net, a, c in (("USB_CC1", ("J5", "A5"), ("U13", "1")), ("USB_CC2", ("J5", "B5"), ("U13", "2"))):
        if not R.route(net, [a], [c], TOP, label=net):
            R.route(net, [a], [c], ALL, label=net + " (via)")
    R.flush()

def gnd_pads(R):
    out = []
    for (ref, num), (x, y, on, net) in R.padpos.items():
        if net == "GND" and len(on) == 1: out.append((ref, num))
    return sorted(out)

def stage_gnd(R, only=None):
    pads = gnd_pads(R) if only is None else only
    R.route("GND", [("U9", "47")], [("U9", "61")], TOP, 0.1, rule=0.1, label="VREG_PGND>EP")
    for ref, num in pads:
        if (ref, num) == ("U9", "47"): continue
        R.route("GND", [(ref, num)], [], TOP, via_goal=True, label="%s.%s stitch" % (ref, num))
    R.flush()

def pair(R, P, N, segments, layers=ALL, width=0.15):
    """segments: list of (srcpads_P, dstpads_P, srcpads_N, dstpads_N)."""
    for sp, dp, sn, dn in segments:
        path = R.route(P, sp, dp, layers, width, label="P %s>%s" % (sp[-1], dp[0]))
        if path:
            cor = R.corridor_from(path, 1.2, pads=sn + dn)
            if not R.route(N, sn, dn, layers, width, corridor=cor, label="N %s>%s hug" % (sn[-1], dn[0])):
                R.route(N, sn, dn, layers, width, label="N %s>%s free" % (sn[-1], dn[0]))
        else:
            R.route(N, sn, dn, layers, width, label="N %s>%s free" % (sn[-1], dn[0]))

def stage_pairs(R):
    pair(R, "USB_D_P", "USB_D_N", [([("U9", "52")], [("R23", "1")], [("U9", "51")], [("R24", "1")])], OUTER)
    pair(R, "USBC_D_P", "USBC_D_N", [([("R23", "2")], [("J5", "A6"), ("J5", "B6")], [("R24", "2")], [("J5", "A7"), ("J5", "B7")])], OUTER)
    R.route("USBC_D_N", [("J5", "A7")], [("J5", "B7")], TOP, label="A7-B7 below", keepout=(130.0, 122.0, 141.5, 130.1))
    R.route("USBC_D_P", [("J5", "A6")], [("J5", "B6")], TOP, label="A6-B6 above", keepout=(130.0, 128.9, 141.5, 137.9))
    pair(R, "BUS_P", "BUS_N", [([("J4", "3")], [("U10", "6")], [("J4", "4")], [("U10", "7")]),
                               ([("J4", "3"), ("U10", "6")], [("J3", "3")], [("J4", "4"), ("U10", "7")], [("J3", "4")])])
    R.route("BUS_P", [("U10", "6")], [("R11", "1")], ALL, label="R11")
    R.route("BUS_N", [("U10", "7")], [("R11", "2")], ALL, label="R11")
    pair(R, "SYNC_P", "SYNC_N", [([("J4", "5")], [("U11", "6")], [("J4", "6")], [("U11", "7")]),
                                 ([("J4", "5"), ("U11", "6")], [("J3", "5")], [("J4", "6"), ("U11", "7")], [("J3", "6")])])
    R.route("SYNC_P", [("U11", "6")], [("R12", "1")], ALL, label="R12")
    R.route("SYNC_N", [("U11", "7")], [("R12", "2")], ALL, label="R12")
    R.flush()

def stage_power(R):
    A = ANALOG
    R.chain("+5V", [("C22", "1"), ("U12", "1"), ("D1", "1"), ("R15", "1"), ("D2", "1")], layers=ALL, width=0.3, avoid=A)
    R.chain("+5V_USB", [("J5", "A4"), ("J5", "B4"), ("C38", "1"), ("C37", "1"), ("U14", "1"), ("R34", "1"), ("D2", "2")], layers=ALL, width=0.3, avoid=A)
    R.route("+5V_USB", [("J5", "A9")], [("J5", "A4"), ("J5", "B4")], ALL, 0.3, avoid=A, label="A9")
    R.route("+5V_USB", [("J5", "B9")], [("J5", "A4"), ("J5", "B4"), ("J5", "A9")], ALL, 0.3, avoid=A, label="B9")
    R.route("+5V_BUS", [("D1", "2")], [("J3", "1"), ("J4", "1"), ("D4", "1")], ALL, 0.3, avoid=A, label="D1 anode")
    R.route("+5V_BUS", [("C39", "1")], [("J4", "1"), ("D4", "1")], ALL, 0.3, avoid=A, label="C39")
    # +3.3V: every capacitor's pad 1 and every unpaired +3.3V pad gets a via into the pour
    for (ref, num), (x, y, on, net) in sorted(R.padpos.items()):
        if net != "+3.3V" or len(on) != 1: continue
        if ref.startswith("C") and num == "1" or ref in ("R18", "R20", "R26", "R31", "R32", "R33", "R35", "R5") or (ref, num) in (("U9", "1"),):
            R.route("+3.3V", [(ref, num)], [], TOP, via_goal=True, label="%s.%s via" % (ref, num))
    R.route("ROW_VCC", [("R5", "2")], [("C1", "1"), ("C2", "1"), ("C3", "1"), ("C4", "1")], ALL, label="R5 trunk")
    for ref, pin in (("U1", "10"), ("U2", "10"), ("U3", "10"), ("U4", "10"), ("R10", "1"), ("C1", "1"), ("C2", "1"), ("C3", "1"), ("C4", "1")):
        R.route("ROW_VCC", [(ref, pin)], [(r, p) for r, p in (("R5", "2"), ("C1", "1"), ("C2", "1"), ("C3", "1"), ("C4", "1"), ("U1", "16"), ("U2", "16"), ("U3", "16"), ("U4", "16")) if (r, p) != (ref, pin)], ALL, label="%s.%s" % (ref, pin))
    R.route("VCORE", [("C24", "1")], [("C19", "1")], ALL, 0.2, avoid=A, label="C24 trunk")
    R.route("VCORE", [("C25", "1")], [("C19", "1"), ("C24", "1")], ALL, 0.2, avoid=A, label="C25 trunk")
    R.route("VCORE", [("C35", "1")], [("C19", "1"), ("C24", "1"), ("C25", "1")], ALL, 0.2, avoid=A, label="C35 trunk")
    R.flush()

DIGITAL = [
    ("ADC_SDI", [("U8", "6"), ("U9", "31")]), ("ADC_SDO", [("U8", "7"), ("U9", "27")]),
    ("ADC_SCK", [("U8", "8"), ("U9", "29")]), ("ADC_CONV", [("U8", "1"), ("U9", "28")]),
    ("ROW_DATA", [("U1", "14"), ("U9", "5")]),
    ("BUS_DI", [("U10", "4"), ("U9", "12")]), ("BUS_RO", [("U10", "1"), ("U9", "13")]),
    ("BUS_DE", [("U10", "2"), ("U10", "3"), ("U9", "14")]), ("SYNC_OUT", [("U11", "1"), ("U9", "15")]),
    ("USB_BUS_EN", [("U14", "3"), ("R30", "1"), ("U9", "36")]), ("USB_ILIM_HI", [("Q1", "1"), ("R29", "1"), ("U9", "2")]),
    ("USB_CC_OUT1", [("U13", "7"), ("R32", "1"), ("U9", "41")]), ("USB_CC_OUT2", [("U13", "8"), ("R33", "1"), ("U9", "43")]),
    ("USB_PWR_FAULT", [("U14", "4"), ("R31", "1"), ("U9", "35")]),
    ("USB_CC2", [("J5", "B5"), ("U13", "2")]), ("USB_VBUS_DET", [("U13", "4"), ("R34", "2")]),
    ("USB_ILIM_LOW", [("R28", "2"), ("Q1", "3")]),
    ("ADDR0", [("U9", "32"), ("JP1", "1")]), ("ADDR1", [("U9", "33"), ("JP2", "1")]), ("ADDR2", [("U9", "34"), ("JP3", "1")]),
    ("BOOTSEL_SW", [("R25", "2"), ("SW1", "1")]), ("SWCLK", [("U9", "24"), ("J6", "2")]),
    ("SWDIO", [("U9", "25"), ("J6", "1")]), ("RUN", [("U9", "26"), ("R20", "1"), ("J6", "4")]),
    ("STATUS", [("U9", "37"), ("R17", "1")]), ("LED_A", [("R17", "2"), ("D3", "2")]),
    ("BOOTSEL", [("U9", "60"), ("R25", "1")]),
]

FANOUT_PARTS = ("U9", "U8", "U13")   # only the fine-pitch escapes; everything else is freerouting's job

def stage_fanout(R, length=0.55):
    """A short straight stub outward from every connected fine-pitch pin, so
    later routes cannot hug a pin row and seal the neighbours in."""
    b = R.b; n = 0; tips = []
    for f in R.native.GetFootprints():
        ref = f.GetReference()
        if ref not in FANOUT_PARTS: continue
        cx, cy = K.ToMM(f.GetPosition().x), K.ToMM(f.GetPosition().y)
        for p in f.Pads():
            net = p.GetNetname()
            if not net or net == "GND" or keep_net(net) or p.GetAttribute() != K.PAD_ATTRIB_SMD: continue
            px, py = K.ToMM(p.GetPosition().x), K.ToMM(p.GetPosition().y)
            sx, sy = K.ToMM(p.GetSize().x), K.ToMM(p.GetSize().y)
            if sx > 2.5 and sy > 2.5: continue          # exposed pad
            a = math.radians(p.GetOrientationDegrees())
            ax, ay = math.cos(a), -math.sin(a)          # pad local x axis in board coords
            if sy > sx: ax, ay = -ay, ax; half = sy / 2  # long axis is local y
            else: half = sx / 2
            if (px - cx) * ax + (py - cy) * ay < 0: ax, ay = -ax, -ay
            fine = ref in FINE
            w, L = (0.1, 1.5 if ref == "U9" else 0.8) if fine else (0.15, length)
            set_width(w, 0.1 if fine else 0.15); free, via_ok, own = b.build(net)
            reach = 0.0
            for k in range(0, int((half + L) / M.GRID) + 1):
                x, y = px + ax * k * M.GRID, py + ay * k * M.GRID
                i, j = b.cell(x, y)
                if not (0 <= i < b.h and 0 <= j < b.w) or not free["F.Cu"][i, j]: break
                reach = k * M.GRID
            L = reach - half - 0.05
            if L < 0.3: continue
            ex, ey = px + ax * (half + L), py + ay * (half + L)
            if min(ex - b.ex[0], b.ex[2] - ex, ey - b.ex[1], b.ex[3] - ey) < 1.0: continue
            R.blocks.append((SEG_FMT % (px, py, ex, ey, net, uuid.uuid4())).replace("(width 0.15)", "(width %g)" % w))
            b.tracks.append((net, "F.Cu", px, py, ex, ey, w, None)); n += 1
            pass   # no tip bands: the local links routed here are themselves the escapes, and freerouting does the rest
    json.dump(tips, open(ROOT + "/tmp/fanout_tips.json", "w"))
    R.flush(); print("fanout stubs:", n)


def stage_rowcol(R):
    """rev-1's matrix fanout, re-routed: connector pin first, then the driver/mux pin."""
    nets = collections.defaultdict(list)
    for (ref, num), (x, y, on, net) in R.padpos.items():
        if re.fullmatch(r"(ROW|COL)_\d+|SR_CHAIN_\d|ROW_CLK|ROW_LATCH|ROW_CLK_MCU|ROW_LATCH_MCU|MUX_S[0-3]", net):
            nets[net].append((ref, num))
    def key(n):
        m = re.fullmatch(r"(ROW|COL)_(\d+)", n)
        return (1 if n.startswith("COL") else 2 if n.startswith("ROW_") and m else 0, int(m.group(2)) if m else 0, n)
    for net in sorted(nets, key=key):
        pads = sorted(nets[net], key=lambda rp: (0 if rp[0].startswith("J") else 1 if rp[0].startswith("R") else 2, rp))
        seq = [pads[0]]; rest = pads[1:]
        while rest:
            lx, ly = R.padpos[seq[-1]][:2]
            rest.sort(key=lambda rp: math.hypot(R.padpos[rp][0]-lx, R.padpos[rp][1]-ly)); seq.append(rest.pop(0))
        for k in range(1, len(seq)):
            ok = R.route(net, seq[:k], [seq[k]], OUTER, avoid=ANALOG, avoid_d=0.3, label="%s.%s>%s.%s" % (seq[k-1] + seq[k]))
            if not ok: R.route(net, seq[:k], [seq[k]], ALL, avoid=ANALOG, avoid_d=0.3, label="%s.%s>%s.%s (In2)" % (seq[k-1] + seq[k]))
    R.flush()

def stage_digital(R):
    for net, seq in DIGITAL:
        for k in range(1, len(seq)):
            ok = R.route(net, seq[:k], [seq[k]], OUTER, avoid=ANALOG, label="%s.%s>%s.%s" % (seq[k-1] + seq[k]))
            if not ok:
                R.route(net, seq[:k], [seq[k]], ALL, avoid=ANALOG, label="%s.%s>%s.%s (In2)" % (seq[k-1] + seq[k]))
    R.flush()

def fill_and_drc(tag="drc"):
    b = K.LoadBoard(PCB)
    K.ZONE_FILLER(b).Fill(b.Zones()); b.Save(PCB)
    out = ROOT + "/tmp/%s.json" % tag
    subprocess.run([KICLI, "pcb", "drc", "--format", "json", "--all-track-errors", "--severity-all", "-o", out, PCB], capture_output=True)
    d = json.load(open(out, encoding="utf-8"))
    c = collections.Counter(v["type"] for v in d["violations"])
    print("DRC: unconnected %d | %s" % (len(d["unconnected_items"]), dict(c)))
    return d

def stage_close(R=None, rounds=3):
    for rnd in range(rounds):
        d = fill_and_drc("close%d" % rnd)
        items = d["unconnected_items"]
        if not items: return
        R = Router(); R.tips = []
        objs = {u: ("pad",) + key for u, key in R.padkey.items()}
        for t in R.native.GetTracks(): objs[t.m_Uuid.AsString()] = ("track", t)
        done = 0
        for v in items:
            a, c = v["items"][:2]
            oa, oc = objs.get(a["uuid"]), objs.get(c["uuid"])
            if not oa or not oc: continue
            net = re.search(r"\[([^\]]+)\]", a["description"]).group(1)
            def nodes(o):
                if o[0] == "pad": return [(o[1], o[2])]
                t = o[1]; pts = [t.GetPosition()] if isinstance(t, K.PCB_VIA) else [t.GetStart(), t.GetEnd()]
                lay = ALL if isinstance(t, K.PCB_VIA) else [{K.F_Cu: "F.Cu", K.In2_Cu: "In2.Cu", K.B_Cu: "B.Cu"}.get(t.GetLayer(), "")]
                return [("@", (K.ToMM(p.x), K.ToMM(p.y), lay)) for p in pts]
            # temporary pseudo-pads for track endpoints
            for k, o in enumerate((oa, oc)):
                for r, p in nodes(o):
                    if r == "@": R.padpos[("@%d" % k, str(p))] = (p[0], p[1], [l for l in p[2] if l], net)
            src = [(r, p if r != "@" else str(p)) for r, p in nodes(oa)]
            src = [(("@0" if r == "@" else r), (str(p) if r == "@" else p)) for r, p in nodes(oa)]
            dst = [(("@1" if r == "@" else r), (str(p) if r == "@" else p)) for r, p in nodes(oc)]
            w = 0.3 if net in ("+5V", "+5V_USB", "+5V_BUS", "USB_BUS_SW", "SW_NODE") else 0.15
            lay = TOP if net in ANALOG else ALL
            if net == "GND":
                ok = R.route(net, src, [], TOP, via_goal=True, label="stitch %s" % a["description"][:30])
                if not ok: ok = R.route(net, src, dst, ALL, label="bridge %s" % a["description"][:30])
            else:
                ok = R.route(net, src, dst, lay, w, avoid=(ANALOG if net not in ANALOG else None), label=a["description"][:36])
                if not ok and net == "+3.3V": ok = R.route(net, src, [], TOP, via_goal=True, label="via %s" % a["description"][:30])
            done += bool(ok)
        R.flush()
        print("round %d: routed %d of %d" % (rnd, done, len(items)))

def stage_clean():
    """delete every track or via that takes part in a clearance, short or hole-clearance violation."""
    d = fill_and_drc("clean")
    ids = {i["uuid"] for v in d["violations"] if v["type"] in ("clearance", "shorting_items", "hole_clearance", "tracks_crossing") for i in v["items"]}
    b = K.LoadBoard(PCB); n = 0
    for t in list(b.GetTracks()):
        if t.m_Uuid.AsString() in ids: b.Delete(t); n += 1
    b.Save(PCB); print("cleaned", n, "items")

def stage_prune():
    for _ in range(6):
        d = fill_and_drc("prune")
        ids = {i["uuid"] for v in d["violations"] if v["type"] in ("track_dangling", "via_dangling") for i in v["items"]}
        if not ids: return
        b = K.LoadBoard(PCB); n = 0
        for t in list(b.GetTracks()):
            if t.m_Uuid.AsString() in ids: b.Delete(t); n += 1
        b.Save(PCB); print("pruned", n)

if __name__ == "__main__":
    cmd = sys.argv[1]
    t0 = time.time()
    if cmd == "rip": stage_rip()
    elif cmd == "drc": fill_and_drc()
    elif cmd == "prune": stage_prune()
    elif cmd == "clean": stage_clean()
    elif cmd == "close": stage_close(rounds=int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    else:
        R = Router()
        {"fanout": stage_fanout, "stubs": stage_stubs, "analog": stage_analog, "gnd": stage_gnd, "pairs": stage_pairs,
         "power": stage_power, "digital": stage_digital, "rowcol": stage_rowcol}[cmd](R)
    print("done %s in %.0f s" % (cmd, time.time() - t0))
