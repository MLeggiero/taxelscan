"""corner_angles.py BOARD [BOARD2] - sharp corners in the routing: where exactly two track
segments of a net meet, the angle between them (180 = straight on). Also the total
track length and the worst pair skew, to compare a compacted board with the original."""
import sys, math, collections
import pcbnew as K

mm = K.ToMM


def report(path):
    b = K.LoadBoard(path)
    ends = collections.defaultdict(list)
    length = 0.0
    per_net = collections.defaultdict(float)
    for t in b.GetTracks():
        if t.Type() == K.PCB_VIA_T:
            continue
        a = (round(mm(t.GetStart().x), 3), round(mm(t.GetStart().y), 3))
        c = (round(mm(t.GetEnd().x), 3), round(mm(t.GetEnd().y), 3))
        if a == c:
            continue
        L = math.dist(a, c)
        length += L
        per_net[t.GetNetname()] += L
        ends[(t.GetLayer(), t.GetNetname(), a)].append(c)
        ends[(t.GetLayer(), t.GetNetname(), c)].append(a)
    hist = collections.Counter()
    worst = []
    for (lay, net, p), others in ends.items():
        if len(others) != 2:
            continue
        v1 = (others[0][0] - p[0], others[0][1] - p[1])
        v2 = (others[1][0] - p[0], others[1][1] - p[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        cosang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        ang = math.degrees(math.acos(cosang))
        hist[int(ang // 15) * 15] += 1
        if ang < 60:
            worst.append((round(ang, 1), net, p))
    pairs = [("USBC_D_P", "USBC_D_N"), ("USB_D_P", "USB_D_N"), ("BUS_P", "BUS_N"), ("SYNC_P", "SYNC_N")]
    skew = {"%s/%s" % pr: round(per_net[pr[0]] - per_net[pr[1]], 2) for pr in pairs if pr[0] in per_net}
    print("%s: track length %.0f mm; corner angles %s; skew %s" % (path, length, dict(sorted(hist.items())), skew))
    for w in sorted(worst)[:12]:
        print("   sharp corner %.1f deg  %s @(%.2f,%.2f)" % (w[0], w[1], *w[2]))


for p in sys.argv[1:]:
    report(p)
