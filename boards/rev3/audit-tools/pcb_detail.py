#!/usr/bin/env python3
import math, sys, collections
sys.argv = [sys.argv[0], sys.argv[1]]
exec(open(sys.path[0] + "/pcb_audit.py").read().split("# ---------------------------------------------------------------- decoupling audit")[0])

def seglen(s): return math.hypot(s["x2"]-s["x1"], s["y2"]-s["y1"])
def pt_seg(px, py, s):
    x1, y1, x2, y2 = s["x1"], s["y1"], s["x2"], s["y2"]
    dx, dy = x2-x1, y2-y1; l2 = dx*dx+dy*dy
    t = 0 if l2 == 0 else max(0, min(1, ((px-x1)*dx+(py-y1)*dy)/l2))
    return math.hypot(px-(x1+t*dx), py-(y1+t*dy))
def seg_seg(a, b):
    # min distance between two segments (sampled)
    best = 1e9
    for s, t in ((a, b), (b, a)):
        n = max(2, int(seglen(s)/0.1)+1)
        for i in range(n):
            u = i/(n-1); px, py = s["x1"]+u*(s["x2"]-s["x1"]), s["y1"]+u*(s["y2"]-s["y1"])
            best = min(best, pt_seg(px, py, t))
    return best

# 1. +5V_USB thin segments
print("=== +5V_USB segments narrower than 0.3 mm ===")
for s in segs:
    if s["net"] == "+5V_USB" and s["w"] < 0.3:
        near = [p["ref"]+"."+p["num"] for p in pads if p["net"] == "+5V_USB" and min(math.hypot(s["x1"]-p["x"], s["y1"]-p["y"]), math.hypot(s["x2"]-p["x"], s["y2"]-p["y"])) < 0.8]
        print("  %s w=%.2f len=%.2f (%.2f,%.2f)-(%.2f,%.2f) near %s" % (s["layer"], s["w"], seglen(s), s["x1"], s["y1"], s["x2"], s["y2"], near))
print("=== +5V segments (all) ===")
for s in segs:
    if s["net"] == "+5V":
        near = [p["ref"]+"."+p["num"] for p in pads if p["net"] == "+5V" and min(math.hypot(s["x1"]-p["x"], s["y1"]-p["y"]), math.hypot(s["x2"]-p["x"], s["y2"]-p["y"])) < 0.8]
        print("  %s w=%.2f len=%.2f near %s" % (s["layer"], s["w"], seglen(s), near))

# 2. how does each U9 supply pin reach copper: F.Cu segments touching pad, vias within 1.2 mm on same net
print("\n=== supply pin escape detail ===")
def pad_by(ref, num):
    for p in fps[ref]["pads"]:
        if p["num"] == num: return p
for ref, num in [("U9","1"),("U9","11"),("U9","20"),("U9","46"),("U9","53"),("U9","54"),("U9","6"),("U9","45"),("U9","50"),("U5","24"),("U6","24"),("U8","9"),("U7","8"),("U10","8"),("U11","8"),("U3","10")]:
    p = pad_by(ref, num)
    touching = [s for s in segs if s["net"] == p["net"] and s["layer"] == "F.Cu" and seg_touches_pad(s, p, "F.Cu")]
    nearvias = [(round(math.hypot(v["x"]-p["x"], v["y"]-p["y"]),2)) for v in vias if v["net"] == p["net"] and math.hypot(v["x"]-p["x"], v["y"]-p["y"]) < 1.5]
    # walk F.Cu copper from the pad: collect connected F.Cu segments (BFS), see which pads/vias it reaches
    seen = set(); frontier = list(range(len(segs)))
    comp = []
    stack = [i for i, s in enumerate(segs) if s in touching]
    seen = set(stack)
    while stack:
        i = stack.pop(); comp.append(i); s = segs[i]
        for j, t in enumerate(segs):
            if j in seen or t["net"] != s["net"] or t["layer"] != "F.Cu": continue
            tol = (s["w"]+t["w"])/2+0.01
            if min(math.hypot(s["x1"]-t["x1"], s["y1"]-t["y1"]), math.hypot(s["x1"]-t["x2"], s["y1"]-t["y2"]), math.hypot(s["x2"]-t["x1"], s["y2"]-t["y1"]), math.hypot(s["x2"]-t["x2"], s["y2"]-t["y2"])) <= tol:
                seen.add(j); stack.append(j)
    reach_pads = set(); reach_vias = 0
    for i in comp:
        s = segs[i]
        for q in pads:
            if q["net"] == s["net"] and (q["ref"], q["num"]) != (ref, num) and seg_touches_pad(s, q, "F.Cu"): reach_pads.add(q["ref"]+"."+q["num"])
        for v in vias:
            if v["net"] == s["net"] and min(math.hypot(s["x1"]-v["x"], s["y1"]-v["y"]), math.hypot(s["x2"]-v["x"], s["y2"]-v["y"])) <= v["size"]/2+0.01: reach_vias += 1
    L = sum(seglen(segs[i]) for i in comp)
    print("%-7s %-9s F.Cu copper from pad: %d segs, %.2f mm, reaches pads %s, vias on it: %d" % (ref+"."+num, p["net"], len(comp), L, sorted(reach_pads), reach_vias))

# 3. neighbours of sensitive nets
SENS = ["SENSE_A", "SENSE_B", "GAIN_A", "GAIN_B", "AMP_A", "AMP_B", "ADC_A", "ADC_B", "VREF", "XIN", "XOUT", "VREG_LX", "SW_NODE", "FB", "RAIL_MON"]
ADJ = {"F.Cu": [], "B.Cu": ["In2.Cu"], "In2.Cu": ["B.Cu"]}
print("\n=== sensitive nets: same-layer neighbours within 0.35 mm edge gap, and adjacent-layer (B.Cu<->In2.Cu) crossings within 0.3 mm ===")
for net in SENS:
    mine = [s for s in segs if s["net"] == net]
    same = collections.defaultdict(float); adj = collections.defaultdict(float)
    for s in mine:
        for t in segs:
            if t["net"] == net or t["net"] == "GND": continue
            if t["layer"] == s["layer"]:
                d = seg_seg(s, t) - (s["w"]+t["w"])/2
                if d < 0.35: same[t["net"]] += min(seglen(s), seglen(t))
            elif t["layer"] in ADJ.get(s["layer"], []):
                d = seg_seg(s, t)
                if d < 0.3: adj[t["net"]] += 1
    print("%-9s same-layer(mm): %s | B.Cu/In2.Cu overlaps(count): %s" % (net, {k: round(v,1) for k, v in sorted(same.items(), key=lambda kv: -kv[1])[:8]}, dict(sorted(adj.items(), key=lambda kv: -kv[1])[:8])))

# 4. USB pair: are DP_C and DM_C routed together? list segments side by side
print("\n=== USB_DP_C / USB_DM_C segments ===")
for net in ("USB_DP_C", "USB_DM_C", "USB_DP", "USB_DM"):
    for s in segs:
        if s["net"] == net and seglen(s) > 0.5:
            print("  %-9s %-6s w=%.2f (%.2f,%.2f)-(%.2f,%.2f) len %.2f" % (net, s["layer"], s["w"], s["x1"], s["y1"], s["x2"], s["y2"], seglen(s)))

# 5. component positions summary (for placement discussion)
print("\n=== placements ===")
for r in ["J1","J2","J3","J4","J5","J6","U1","U2","U3","U4","U5","U6","U7","U8","U9","U10","U11","U12","U13","U14","L1","L2","Y1","R1","R2","R6","R7","R8","R9","R21","R22","C31","C32","C19","C20","C21","C22","C23","C27","D1","D2","D4","Q1","SW1","JP1","JP2","JP3","R15","R16","R3","R4","R5","R10","C10","C26","C39","C37","C38","C9"]:
    v = fps[r]; print("  %-4s (%.2f, %.2f) rot %4.0f  %s" % (r, v["x"], v["y"], v["rot"], v["name"].split(":")[-1][:34]))
