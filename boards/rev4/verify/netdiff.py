"""netdiff.py - what rev-4's netlist changes against rev-3's.

  python3 netdiff.py            (from this directory; reads ../rev4.net and ../../rev3/rev3.net)

Parts removed / added, nets removed / added, every pin of a kept part whose net
changed, and the nets whose pin membership differs."""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    s = open(path).read()
    nets = {}
    for m in re.finditer(r'\(net \(code "?\d+"?\) \(name "([^"]*)"\)(.*?)(?=\n    \(net |\n  \)\n)', s, re.S):
        nets[m.group(1)] = frozenset(re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', m.group(2)))
    return nets


r3 = load(os.path.join(HERE, "..", "..", "rev3", "rev3.net"))
r4 = load(os.path.join(HERE, "..", "rev4.net"))
pin3 = {n: k for k, v in r3.items() for n in v}
pin4 = {n: k for k, v in r4.items() for n in v}
refs3, refs4 = {r for r, _ in pin3}, {r for r, _ in pin4}
print("parts removed:", sorted(refs3 - refs4), " added:", sorted(refs4 - refs3))
print("nets removed:", sorted(set(r3) - set(r4)), " added:", sorted(set(r4) - set(r3)))
kept = refs3 & refs4
moved = [(p, pin3.get(p), pin4.get(p)) for p in sorted(set(pin3) | set(pin4)) if p[0] in kept and pin3.get(p) != pin4.get(p)]
print("pins of kept parts whose net changed: %d" % len(moved))
for p, a, b in moved:
    print("   %s.%s: %s -> %s" % (p[0], p[1], a, b))
common = set(r3) & set(r4)
print("nets with identical pins: %d of %d in both" % (sum(1 for k in common if r3[k] == r4[k]), len(common)))
for k in sorted(common):
    if r3[k] != r4[k]:
        print("   %s: -%s +%s" % (k, sorted("%s.%s" % x for x in r3[k] - r4[k]), sorted("%s.%s" % x for x in r4[k] - r3[k])))
