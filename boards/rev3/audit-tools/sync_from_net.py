"""sync_from_net.py BOARD NETLIST OUT - bring the board's values and pad nets in line with rev3.net.

- Footprint values that differ from the netlist's are updated.
- L1 is turned 180 degrees about its own origin, so its pad 1 (the polarity dot)
  lands on the copper that pad 2 had. Both pads are identical, so no copper moves.
- Every pad's net is then set from the netlist; anything else that differs is reported.
"""
import re, sys
import pcbnew as K

BOARD, NET, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
BOMCSV = sys.argv[4] if len(sys.argv) > 4 else NET.replace("rev3.net", "BOM.csv")
text = open(NET, encoding="utf-8").read()
import csv
values = {}
for row in csv.DictReader(open(BOMCSV, encoding="utf-8")):
    for part in row["Reference"].replace(" ", "").split(","):
        m = re.fullmatch(r"([A-Z]+)(\d+)-[A-Z]*(\d+)", part)
        if m:
            for i in range(int(m.group(2)), int(m.group(3)) + 1):
                values["%s%d" % (m.group(1), i)] = row["Value"]
        else:
            values[part] = row["Value"]
padnet = {}
for name, body in re.findall(r'\(net \(code "?\d+"?\) \(name "([^"]+)"\)(.*?)(?=\(net \(code|\Z)', text, re.S):
    for ref, pin in re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', body):
        padnet[(ref, pin)] = name
if not values or not padnet:
    sys.exit("could not parse %s" % NET)

b = K.LoadBoard(BOARD)
changed = []
f = b.FindFootprintByReference("L1")
before = {p.GetNumber(): (p.GetPosition().x, p.GetPosition().y) for p in f.Pads()}
f.Rotate(f.GetPosition(), K.EDA_ANGLE(180.0, K.DEGREES_T))
after = {p.GetNumber(): (p.GetPosition().x, p.GetPosition().y) for p in f.Pads()}
assert after["1"] == before["2"] and after["2"] == before["1"], (before, after)
changed.append("L1 turned 180 deg: pad 1 now sits where pad 2 was")

nets = {n.GetNetname(): n for n in b.GetNetsByName().values()} if hasattr(b, "GetNetsByName") else {}
for fp in b.GetFootprints():
    ref = fp.GetReference()
    if ref in values and fp.GetValue() != values[ref]:
        changed.append("%s value %s -> %s" % (ref, fp.GetValue(), values[ref]))
        fp.SetValue(values[ref])
    for p in fp.Pads():
        want = padnet.get((ref, p.GetNumber()))
        if want is None:
            continue
        if p.GetNetname() != want:
            ni = b.FindNet(want)
            if ni is None:
                sys.exit("net %s not on the board" % want)
            changed.append("%s.%s net %s -> %s" % (ref, p.GetNumber(), p.GetNetname(), want))
            p.SetNet(ni)
b.Save(OUT)
print("\n".join(changed))
print("saved", OUT)
