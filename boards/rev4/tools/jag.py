"""jag.py board.kicad_pcb [net ...] - how jagged is the routing?

Per net: copper length, the share of it in segments whose direction is not a
multiple of 45 degrees (more than 1 degree off), and the number of segments
shorter than 0.1 mm (the staircase steps a grid router or a compaction pass
leaves behind). With no nets named, the board totals by net group, and the
corners: every point where exactly two segments of a net meet on one layer and
change direction, and how many of those turn by more than 90 degrees (an
acute inside angle - what hand routing never does)."""
import collections, math, sys
import pcbnew as K

mm = K.ToMM


def segs(b):
    for t in b.GetTracks():
        if t.Type() in (K.PCB_VIA_T, K.PCB_ARC_T):
            continue
        a, c = t.GetStart(), t.GetEnd()
        x0, y0, x1, y1 = mm(a.x), mm(a.y), mm(c.x), mm(c.y)
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-6:
            continue
        ang = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 45.0
        off = min(ang, 45.0 - ang) > 1.0
        yield t.GetNetname(), L, off, L < 0.1


def table(b, nets=None):
    tot = collections.defaultdict(lambda: [0.0, 0.0, 0, 0])
    for n, L, off, tiny in segs(b):
        if nets and n not in nets:
            continue
        r = tot[n]
        r[0] += L
        r[1] += L if off else 0.0
        r[2] += tiny
        r[3] += 1
    return tot


def corners(b):
    """(corners, acute) over the board: two-segment joints that bend, and those turning > 90 degrees."""
    ends = collections.defaultdict(list)
    for t in b.GetTracks():
        if t.Type() != K.PCB_TRACE_T:
            continue
        a, c = t.GetStart(), t.GetEnd()
        if a.x == c.x and a.y == c.y:
            continue
        for p, q in ((a, c), (c, a)):
            ends[(t.GetNetCode(), t.GetLayer(), p.x // 1000, p.y // 1000)].append((q.x - p.x, q.y - p.y))
    n = acute = 0
    for v in ends.values():
        if len(v) != 2:
            continue
        (ax, ay), (bx, by) = v
        # the outgoing directions from the joint: straight on means they are opposite
        cos = (ax * bx + ay * by) / (math.hypot(ax, ay) * math.hypot(bx, by))
        turn = 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        if turn > 1.0:
            n += 1
            acute += turn > 90.5
    return n, acute


if __name__ == "__main__":
    b = K.LoadBoard(sys.argv[1])
    tot = table(b, set(sys.argv[2:]) or None)
    L = sum(r[0] for r in tot.values()); off = sum(r[1] for r in tot.values())
    tiny = sum(r[2] for r in tot.values()); n = sum(r[3] for r in tot.values())
    print("%s: %d segments, %.0f mm; off-45 %.0f mm (%.0f%%); %d segments < 0.1 mm"
          % (sys.argv[1].split("/")[-1], n, L, off, 100 * off / L, tiny))
    nc, na = corners(b)
    print("corners %d, of them turning more than 90 degrees %d" % (nc, na))
    worst = sorted(tot.items(), key=lambda kv: -kv[1][1])[:25]
    for k, r in worst:
        print("   %-14s %6.1f mm  off-45 %5.1f mm (%3.0f%%)  tiny %3d  segs %4d" % (k, r[0], r[1], 100 * r[1] / r[0], r[2], r[3]))
