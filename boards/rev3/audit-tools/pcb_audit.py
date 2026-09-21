#!/usr/bin/env python3
"""Geometry audit of rev3.kicad_pcb: decoupling distances, net routing stats,
diff pairs, thermal vias, planes, power widths."""
import math, re, sys, collections

PCB = sys.argv[1] if len(sys.argv) > 1 else "rev3.kicad_pcb"
txt = open(PCB, encoding="utf-8").read()

# ---------------------------------------------------------------- s-expr
def parse(s):
    tok = re.finditer(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+', s)
    stack = [[]]
    for m in tok:
        t = m.group(0)
        if t == '(':
            stack.append([])
        elif t == ')':
            l = stack.pop(); stack[-1].append(l)
        else:
            if t.startswith('"'): t = t[1:-1]
            stack[-1].append(t)
    return stack[0][0]

root = parse(txt)
def kids(node, key):
    return [k for k in node[1:] if isinstance(k, list) and k and k[0] == key]
def kid(node, key):
    r = kids(node, key); return r[0] if r else None
def f(x): return float(x)

netnum = {}
for n in kids(root, "net"):
    netnum[n[1]] = n[2] if len(n) > 2 else ""
def netname(node):
    n = kid(node, "net")
    if not n: return ""
    v = n[1]
    return netnum.get(v, v) if v.isdigit() else v

def rot(x, y, deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c

# ---------------------------------------------------------------- footprints/pads
pads = []   # dict: ref, num, x, y, w, h, rot, layers, net, kind
fps = {}
for fp in kids(root, "footprint"):
    ref = None
    for pr in kids(fp, "property"):
        if pr[1] == "Reference": ref = pr[2]
    at = kid(fp, "at"); fx, fy = f(at[1]), f(at[2]); frot = f(at[3]) if len(at) > 3 else 0.0
    layer = kid(fp, "layer")[1]
    fps[ref] = dict(x=fx, y=fy, rot=frot, layer=layer, name=fp[1], pads=[])
    for pd in kids(fp, "pad"):
        pat = kid(pd, "at"); px, py = f(pat[1]), f(pat[2]); prot = f(pat[3]) if len(pat) > 3 else frot
        dx, dy = rot(px, py, frot)
        sz = kid(pd, "size"); w, h = f(sz[1]), f(sz[2])
        lay = kid(pd, "layers")[1:]
        p = dict(ref=ref, num=pd[1], x=fx + dx, y=fy + dy, w=w, h=h, rot=prot,
                 layers=lay, net=netname(pd), kind=pd[2], shape=pd[3])
        pads.append(p); fps[ref]["pads"].append(p)

segs = []
for s in kids(root, "segment"):
    a, b = kid(s, "start"), kid(s, "end")
    segs.append(dict(x1=f(a[1]), y1=f(a[2]), x2=f(b[1]), y2=f(b[2]),
                     w=f(kid(s, "width")[1]), layer=kid(s, "layer")[1], net=netname(s)))
vias = []
for v in kids(root, "via"):
    a = kid(v, "at")
    vias.append(dict(x=f(a[1]), y=f(a[2]), size=f(kid(v, "size")[1]),
                     drill=f(kid(v, "drill")[1]), layers=kid(v, "layers")[1:], net=netname(v)))
zones = []
for z in kids(root, "zone"):
    lay = kid(z, "layer") or kid(z, "layers")
    polys = kids(z, "filled_polygon")
    areas = []
    for fpoly in polys:
        pts = [(f(p[1]), f(p[2])) for p in kids(kid(fpoly, "pts"), "xy")]
        a = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
            a += x1 * y2 - x2 * y1
        areas.append(abs(a) / 2)
    zones.append(dict(net=netname(z), layers=lay[1:], nfill=len(polys), areas=areas))

# board outline
ex = []
for g in kids(root, "gr_line") + kids(root, "gr_rect") + kids(root, "gr_arc") + kids(root, "gr_poly"):
    if kid(g, "layer") and kid(g, "layer")[1] == "Edge.Cuts":
        for key in ("start", "end", "mid"):
            k = kid(g, key)
            if k: ex.append((f(k[1]), f(k[2])))
        pts = kid(g, "pts")
        if pts: ex += [(f(p[1]), f(p[2])) for p in kids(pts, "xy")]
if ex:
    xs, ys = [p[0] for p in ex], [p[1] for p in ex]
    print("OUTLINE %.2f x %.2f mm  (x %.2f..%.2f, y %.2f..%.2f)" % (max(xs)-min(xs), max(ys)-min(ys), min(xs), max(xs), min(ys), max(ys)))
print("footprints %d  pads %d  segments %d  vias %d  zones %d" % (len(fps), len(pads), len(segs), len(vias), len(zones)))
print("footprints not on F.Cu:", [r for r, v in fps.items() if v["layer"] != "F.Cu"])
for z in zones:
    print("ZONE %-6s %-8s polygons=%d area=%.1f mm2 (largest %.1f)" % (z["net"], z["layers"], z["nfill"], sum(z["areas"]), max(z["areas"]) if z["areas"] else 0))
print("In1.Cu segments:", sum(1 for s in segs if s["layer"] == "In1.Cu"),
      " nets:", sorted({s["net"] for s in segs if s["layer"] == "In1.Cu"}))

def padpos(ref, num):
    for p in fps[ref]["pads"]:
        if p["num"] == num: return p
    raise KeyError((ref, num))
def dist(a, b): return math.hypot(a["x"] - b["x"], a["y"] - b["y"])

# ---------------------------------------------------------------- connectivity per net per layer (F.Cu only path check)
def seg_touches_pad(s, p, layer):
    if layer not in p["layers"] and "*.Cu" not in p["layers"]: return False
    # distance from pad centre to segment endpoints, within pad half-diagonal + w/2
    r = math.hypot(p["w"], p["h"]) / 2 + s["w"] / 2
    return (math.hypot(s["x1"]-p["x"], s["y1"]-p["y"]) <= r or math.hypot(s["x2"]-p["x"], s["y2"]-p["y"]) <= r)

def fcu_components(net):
    """union-find over F.Cu segments and F.Cu pads of a net; vias NOT included."""
    items = [("s", i) for i, s in enumerate(segs) if s["net"] == net and s["layer"] == "F.Cu"]
    plist = [p for p in pads if p["net"] == net and ("F.Cu" in p["layers"] or "*.Cu" in p["layers"])]
    items += [("p", i) for i in range(len(plist))]
    parent = {it: it for it in items}
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    def union(a, b): parent[find(a)] = find(b)
    S = [segs[i] for _, i in items if _ == "s"]
    sidx = [i for _, i in items if _ == "s"]
    for a in range(len(S)):
        for b in range(a + 1, len(S)):
            sa, sb = S[a], S[b]
            tol = (sa["w"] + sb["w"]) / 2 + 0.01
            if (math.hypot(sa["x1"]-sb["x1"], sa["y1"]-sb["y1"]) <= tol or math.hypot(sa["x1"]-sb["x2"], sa["y1"]-sb["y2"]) <= tol
                or math.hypot(sa["x2"]-sb["x1"], sa["y2"]-sb["y1"]) <= tol or math.hypot(sa["x2"]-sb["x2"], sa["y2"]-sb["y2"]) <= tol):
                union(("s", sidx[a]), ("s", sidx[b]))
        for j, p in enumerate(plist):
            if seg_touches_pad(S[a], p, "F.Cu"): union(("s", sidx[a]), ("p", j))
    comp = {}
    for j, p in enumerate(plist):
        comp[(p["ref"], p["num"])] = find(("p", j))
    return comp

# ---------------------------------------------------------------- decoupling audit
SUPPLY = {"+3.3V", "VCORE", "ADC_AVDD", "ROW_VCC", "+5V", "+5V_USB", "VREF", "+5V_BUS"}
cap_val = {}
for fp in kids(root, "footprint"):
    ref = val = None
    for pr in kids(fp, "property"):
        if pr[1] == "Reference": ref = pr[2]
        if pr[1] == "Value": val = pr[2]
    cap_val[ref] = val
print("\n=== IC supply pins: nearest same-net capacitor pad, F.Cu-only path, GND via at cap ===")
print("%-8s %-9s %-6s %-8s %-6s %-8s %-8s" % ("pin", "net", "cap", "value", "dist", "FCuPath", "gndvia"))
comps_cache = {}
rows = []
for p in pads:
    if not p["ref"].startswith("U") or p["net"] not in SUPPLY: continue
    net = p["net"]
    caps = [q for q in pads if q["ref"].startswith("C") and q["net"] == net]
    if not caps:
        rows.append((p["ref"]+"."+p["num"], net, "-", "-", 99, "-", "-")); continue
    best = min(caps, key=lambda q: dist(p, q))
    if net not in comps_cache: comps_cache[net] = fcu_components(net)
    cc = comps_cache[net]
    same = cc.get((p["ref"], p["num"])) == cc.get((best["ref"], best["num"]))
    # GND pad of that cap and nearest GND via
    gpad = [q for q in fps[best["ref"]]["pads"] if q["net"] == "GND"]
    gv = min((math.hypot(v["x"]-gpad[0]["x"], v["y"]-gpad[0]["y"]) for v in vias if v["net"] == "GND"), default=99) if gpad else 99
    rows.append((p["ref"]+"."+p["num"], net, best["ref"], cap_val[best["ref"]], dist(p, best), "yes" if same else "NO", gv))
for r in sorted(rows, key=lambda r: (r[1], -r[4])):
    print("%-8s %-9s %-6s %-8s %6.2f %-8s %s" % (r[0], r[1], r[2], r[3], r[4], r[5], ("%.2f" % r[6]) if isinstance(r[6], float) else r[6]))

# ---------------------------------------------------------------- net routing stats
def netstats(net):
    L = collections.defaultdict(float); W = collections.Counter()
    for s in segs:
        if s["net"] == net:
            L[s["layer"]] += math.hypot(s["x2"]-s["x1"], s["y2"]-s["y1"]); W[(s["layer"], s["w"])] += 1
    nv = sum(1 for v in vias if v["net"] == net)
    return dict(L), nv, W
print("\n=== routing stats (mm per layer, vias, widths) ===")
for net in ["SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B", "AMP_A", "AMP_B", "ADC_A", "ADC_B", "VREF", "ROW_VCC",
            "XIN", "XOUT", "VREG_LX", "VCORE", "SW_NODE", "FB", "+5V", "+5V_USB", "+5V_BUS", "USB_BUS_SW", "+3.3V",
            "USB_DP", "USB_DM", "USB_DP_C", "USB_DM_C", "USB_CC1", "USB_CC2", "BUS_A", "BUS_B", "SYNC_A", "SYNC_B",
            "ADC_SCK", "ADC_SDO", "ADC_SDI", "ADC_CONV", "ROW_CLK", "ROW_CLK_MCU", "ROW_LATCH", "ROW_DATA", "BOOTSEL", "RAIL_MON", "ADC_AVDD", "USB_ILIM", "USB_VBUS_DET"]:
    L, nv, W = netstats(net)
    widths = sorted({w for (_, w) in W})
    print("%-12s total %6.2f  %s  vias=%d  widths=%s" % (net, sum(L.values()), {k: round(v, 1) for k, v in sorted(L.items())}, nv, widths))

# ---------------------------------------------------------------- diff pair geometry
def pair_gap(n1, n2):
    S1 = [s for s in segs if s["net"] == n1]; S2 = [s for s in segs if s["net"] == n2]
    def pt_seg(px, py, s):
        x1, y1, x2, y2 = s["x1"], s["y1"], s["x2"], s["y2"]
        dx, dy = x2-x1, y2-y1; l2 = dx*dx+dy*dy
        t = 0 if l2 == 0 else max(0, min(1, ((px-x1)*dx+(py-y1)*dy)/l2))
        return math.hypot(px-(x1+t*dx), py-(y1+t*dy))
    gaps = []
    for s in S1:
        n = max(2, int(math.hypot(s["x2"]-s["x1"], s["y2"]-s["y1"]) / 0.2) + 1)
        for i in range(n):
            t = i/(n-1); px, py = s["x1"]+t*(s["x2"]-s["x1"]), s["y1"]+t*(s["y2"]-s["y1"])
            d = min((pt_seg(px, py, q) for q in S2 if q["layer"] == s["layer"]), default=None)
            if d is not None: gaps.append(d - (s["w"]))  # edge-to-edge approx (assumes equal widths)
    if not gaps: return None
    gaps.sort()
    return gaps[0], gaps[len(gaps)//2], gaps[-1]
print("\n=== differential pairs: edge gap min/median/max (mm) ===")
for a, b in [("USB_DP", "USB_DM"), ("USB_DP_C", "USB_DM_C"), ("BUS_A", "BUS_B"), ("SYNC_A", "SYNC_B")]:
    print(a, b, pair_gap(a, b))

# ---------------------------------------------------------------- key distances
def pd(ref, num): return padpos(ref, num)
def show(label, a, b):
    print("%-44s %6.2f mm" % (label, dist(a, b)))
print("\n=== key placements ===")
show("L1.1 -> U9.48 VREG_LX", pd("L1", "1"), pd("U9", "48"))
show("C19.1 -> L1.2 (VCORE bulk at inductor out)", pd("C19", "1"), pd("L1", "2"))
show("U9.50 VREG_FB -> C19.1", pd("U9", "50"), pd("C19", "1"))
show("Y1.1 -> U9.21 XIN", pd("Y1", "1"), pd("U9", "21"))
show("Y1.3 -> U9.22 XOUT", pd("Y1", "3"), pd("U9", "22"))
show("C20.1 -> Y1.1", pd("C20", "1"), pd("Y1", "1"))
show("C21.1 -> Y1.3", pd("C21", "1"), pd("Y1", "3"))
show("C31.1 -> U8.2 CH0", pd("C31", "1"), pd("U8", "2"))
show("C32.1 -> U8.3 CH1", pd("C32", "1"), pd("U8", "3"))
show("R21.2 -> C31.1", pd("R21", "2"), pd("C31", "1"))
show("R22.2 -> C32.1", pd("R22", "2"), pd("C32", "1"))
show("C26.1 -> U8.10 VREF", pd("C26", "1"), pd("U8", "10"))
show("C10.1 -> U8.10 VREF", pd("C10", "1"), pd("U8", "10"))
show("R1.1 -> U5.1 (sense A node)", pd("R1", "1"), pd("U5", "1"))
show("R1.1 -> U7.3", pd("R1", "1"), pd("U7", "3"))
show("U5.1 -> U7.3 (SENSE_A span)", pd("U5", "1"), pd("U7", "3"))
show("R2.1 -> U6.1 (sense B node)", pd("R2", "1"), pd("U6", "1"))
show("U6.1 -> U7.5 (SENSE_B span)", pd("U6", "1"), pd("U7", "5"))
show("R6/R7 junction: R6.1 -> U7.2", pd("R6", "1"), pd("U7", "2"))
show("R8/R9 junction: R8.1 -> U7.6", pd("R8", "1"), pd("U7", "6"))
show("C27.1 -> U12.4 VIN", pd("C27", "1"), pd("U12", "4"))
show("C27.2 -> U12.2 GND", pd("C27", "2"), pd("U12", "2"))
show("C22.1 -> U12.4 VIN", pd("C22", "1"), pd("U12", "4"))
show("L2.1 -> U12.3 SW", pd("L2", "1"), pd("U12", "3"))
show("C23.1 -> L2.2", pd("C23", "1"), pd("L2", "2"))
show("R19.1 -> U12.5 FB", pd("R19", "1"), pd("U12", "5"))
show("R18.1 (FB top) -> C23.1 (Vout)", pd("R18", "1"), pd("C23", "1"))
show("C40.1 -> U13.12 VDD", pd("C40", "1"), pd("U13", "12"))
show("C41.1 -> U13.12 VDD", pd("C41", "1"), pd("U13", "12"))
show("C42.1 -> U14.1 IN", pd("C42", "1"), pd("U14", "1"))
show("U13 -> J5 (CC1 pad A5)", pd("U13", "1"), pd("J5", "A5"))
show("R23.1 -> U9.52 USB_DP", pd("R23", "1"), pd("U9", "52"))
show("R24.1 -> U9.51 USB_DM", pd("R24", "1"), pd("U9", "51"))
show("C38.1 -> J5.A4 VBUS", pd("C38", "1"), pd("J5", "A4"))
show("C39.1 -> D4.1 (K)", pd("C39", "1"), pd("D4", "1"))
show("C36.1 -> U9.44 ADC_AVDD", pd("C36", "1"), pd("U9", "44"))
show("R26.2 -> C36.1", pd("R26", "2"), pd("C36", "1"))
show("R16.1 -> U9.40 RAIL_MON", pd("R16", "1"), pd("U9", "40"))
show("R3.1 -> U9.4", pd("R3", "1"), pd("U9", "4"))
show("R11.1 -> J3.3", pd("R11", "1"), pd("J3", "3"))
show("R11.1 -> U10.6", pd("R11", "1"), pd("U10", "6"))
show("U10.6 -> J3.3", pd("U10", "6"), pd("J3", "3"))
show("U10.6 -> J4.3", pd("U10", "6"), pd("J4", "3"))
show("U11.6 -> J3.5", pd("U11", "6"), pd("J3", "5"))
show("R25.1 -> U9.60", pd("R25", "1"), pd("U9", "60"))
show("R20.1 -> U9.26 RUN", pd("R20", "1"), pd("U9", "26"))

# thermal vias under U9 EP
ep = pd("U9", "61")
tv = [v for v in vias if v["net"] == "GND" and abs(v["x"]-ep["x"]) <= ep["w"]/2 and abs(v["y"]-ep["y"]) <= ep["h"]/2]
print("\nU9 EP at (%.2f, %.2f) %.1fx%.1f: GND vias inside = %d  %s" % (ep["x"], ep["y"], ep["w"], ep["h"], len(tv), [(round(v["x"],2), round(v["y"],2), v["drill"]) for v in tv]))
print("EP pad layers:", ep["layers"])
# vias stats
vc = collections.Counter((v["size"], v["drill"]) for v in vias)
print("via sizes:", dict(vc))
# GND vias count, per-IC GND pad nearest via
print("\n=== IC GND pins: nearest GND via (mm) ===")
for p in pads:
    if p["ref"].startswith("U") and p["net"] == "GND":
        d = min(math.hypot(v["x"]-p["x"], v["y"]-p["y"]) for v in vias if v["net"] == "GND")
        print("%-8s %.2f" % (p["ref"]+"."+p["num"], d))

# copper-to-copper: analog nets on In2/B.Cu
print("\n=== analog nets with copper off F.Cu ===")
for net in ["SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B", "AMP_A", "AMP_B", "ADC_A", "ADC_B", "VREF", "XIN", "XOUT"]:
    lays = sorted({s["layer"] for s in segs if s["net"] == net})
    print(net, lays)

# +5V_BUS J3->J4 path width
print("\n=== power nets: min width ===")
for net in ["+5V_BUS", "+5V", "+5V_USB", "USB_BUS_SW", "+3.3V", "VCORE", "VREG_LX", "SW_NODE", "ROW_VCC", "GND"]:
    ws = [s["w"] for s in segs if s["net"] == net]
    if ws: print("%-12s n=%4d min=%.3f max=%.3f  len=%.1f" % (net, len(ws), min(ws), max(ws), sum(math.hypot(s["x2"]-s["x1"], s["y2"]-s["y1"]) for s in segs if s["net"] == net)))

# pads with no net
print("\nunassigned pads:", [(p["ref"], p["num"]) for p in pads if p["net"] == ""])
