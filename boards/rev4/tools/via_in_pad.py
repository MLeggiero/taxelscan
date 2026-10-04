"""via_in_pad.py board.kicad_pcb - the via-in-pad numbers fab/STACKUP-NOTES.txt quotes.

A via hole counts when it meets an SMD pad's solder-mask opening (the pad grown
by its mask expansion): wholly inside, or partly. "One-pad passives" are the
two-pad R/C/L/D parts with a via hole in exactly one pad's opening - the ones
that tombstone if the hole drains solder from that side."""
import collections
import sys
import pcbnew as K
from shapely.geometry import Point
from shapely.ops import unary_union

mm = K.ToMM


def poly(ps):
    from shapely.geometry import Polygon
    out = []
    for k in range(ps.OutlineCount()):
        ch = ps.COutline(k)
        out.append(Polygon([(mm(ch.CPoint(i).x), mm(ch.CPoint(i).y)) for i in range(ch.PointCount())]))
    return unary_union(out)


def measure(path):
    b = K.LoadBoard(path)
    openings = []
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.GetAttribute() != K.PAD_ATTRIB_SMD:
                continue
            lay = K.F_Cu if p.IsOnLayer(K.F_Cu) else K.B_Cu
            exp = mm(p.GetSolderMaskExpansion(K.F_Mask if lay == K.F_Cu else K.B_Mask))
            g = poly(p.GetEffectivePolygon(lay, K.ERROR_OUTSIDE)).buffer(exp)
            openings.append((f.GetReference(), p.GetNumber(), g))
    wholly = partly = 0
    ep = 0
    per_part = collections.defaultdict(set)
    for v in b.GetTracks():
        if v.Type() != K.PCB_VIA_T:
            continue
        h = Point(mm(v.GetPosition().x), mm(v.GetPosition().y)).buffer(mm(v.GetDrillValue()) / 2, 32)
        hit = [(r, n, g) for r, n, g in openings if g.intersects(h)]
        if not hit:
            continue
        if any(g.contains(h) for _r, _n, g in hit):
            wholly += 1
        else:
            partly += 1
        for r, n, g in hit:
            per_part[r].add(n)
        if any(r == "U9" and n in ("61", "") for r, n, _g in hit):
            ep += 1
    one_pad = sorted(r for r, pads in per_part.items()
                     if r[0] in "RCLD" and len(pads) == 1 and not r.startswith("D5"))
    return wholly + partly, wholly, partly, ep, one_pad


if __name__ == "__main__":
    for path in sys.argv[1:]:
        t, w, p, ep, one = measure(path)
        print("%s: %d via holes meet SMD pad openings: %d wholly, %d partly; %d in U9's exposed pad; "
              "%d two-pad passives with a hole on one pad only" % (path.split("/")[-1], t, w, p, ep, len(one)))
