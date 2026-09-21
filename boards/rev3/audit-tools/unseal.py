"""Free the pads DRC still calls unconnected: rip every track of an unrelated
net within RADIUS of the sealed pad on F.Cu, route the sealed connection first
at the fine rule, then let the close rounds put the ripped nets back.

  python.exe unseal.py [radius_mm]
"""
import sys, os, json, re, math, collections
radius = float(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else 0.9
sys.argv = ["x", "noop"]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "route_fix.py")).read().split('if __name__ == "__main__":')[0])

KEEP = set(ANALOG) | {"GND", "VCORE", "VREG_LX", "SW_NODE",
                      "USB_D_P", "USB_D_N", "USBC_D_P", "USBC_D_N", "BUS_P", "BUS_N", "SYNC_P", "SYNC_N"}
d = fill_and_drc("unseal")
b = K.LoadBoard(PCB)
pads = {}
for f in b.GetFootprints():
    for p in f.Pads():
        pads[p.m_Uuid.AsString()] = p
todo = []
for v in d["unconnected_items"]:
    items = [pads.get(i["uuid"]) for i in v["items"][:2]]
    net = re.search(r"\[([^\]]+)\]", v["items"][0]["description"]).group(1)
    todo.append((net, [i for i in items if i is not None]))
ripped = collections.Counter(); ripped_nets = set()
for net, ps in todo:
    for p in ps:
        if p.GetAttribute() != K.PAD_ATTRIB_SMD: continue
        cx, cy = K.ToMM(p.GetPosition().x), K.ToMM(p.GetPosition().y)
        for t in list(b.GetTracks()):
            if t.GetNetname() in KEEP or t.GetNetname() == net: continue
            if isinstance(t, K.PCB_VIA):
                if math.hypot(K.ToMM(t.GetPosition().x) - cx, K.ToMM(t.GetPosition().y) - cy) <= radius:
                    ripped[t.GetNetname() + " via"] += 1; ripped_nets.add(t.GetNetname()); b.Delete(t)
                continue
            if b.GetLayerName(t.GetLayer()) != "F.Cu": continue
            for q in (t.GetStart(), t.GetEnd()):
                if math.hypot(K.ToMM(q.x) - cx, K.ToMM(q.y) - cy) <= radius:
                    ripped[t.GetNetname()] += 1; ripped_nets.add(t.GetNetname()); b.Delete(t); break
b.Save(PCB)
print("ripped", sum(ripped.values()), "segments on", dict(ripped))
# route the sealed connections first, fine rule where a fine part is involved
R = Router()
for net, ps in todo:
    if len(ps) < 2:
        continue
    refs = []
    for p in ps:
        f = p.GetParent()
        key = R.padkey.get(p.m_Uuid.AsString())
        if key: refs.append(key)
    if len(refs) < 2: continue
    fine = any(r in FINE for r, n in refs)
    w, rule = (0.1, 0.1) if fine else (0.15, 0.15)
    lay = TOP if net in ANALOG else ALL
    if net == "GND":
        R.route(net, [refs[0]], [], TOP, via_goal=True, label="stitch %s.%s" % refs[0]) or R.route(net, [refs[0]], [refs[1]], ALL, label="bridge")
    else:
        R.route(net, [refs[0]], [refs[1]], lay, w, rule=rule, label="%s %s.%s>%s.%s" % ((net,) + refs[0] + refs[1]))
R.flush()
# re-drop the +3.3V plane vias that were ripped: every +3.3V cap pad 1 without one
if "+3.3V" in ripped_nets:
    for (ref, num), (x, y, on, n) in sorted(R.padpos.items()):
        if n == "+3.3V" and num == "1" and ref.startswith("C") and len(on) == 1:
            R.route("+3.3V", [(ref, num)], [], TOP, via_goal=True, label="%s.%s via" % (ref, num))
    R.flush()
print("ripped nets to restore:", sorted(ripped_nets))
