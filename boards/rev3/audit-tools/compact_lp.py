"""compact_lp.py BOARD AXIS GMAX [--out OUT] [--gain G] [--freeze FILE] - compaction that keeps every connection.

A linear programme over the shifts p (toward the fixed low edge, along AXIS) of parts,
vias and track corners. The shapes:
  disc      a via, a hole, a track's round end: centre and radius, moves with one node
  band      a track's straight part: a rectangle along the track, its two ends moving
            with the track's two end nodes (the rows it covers never change)
  polygon   a pad, a courtyard: convex, moves with its part
Gaps are measured along the axis, row by row. Between a disc and anything the centre is
tested against the other shape grown by radius + clearance at the centre's row, which
is exact. Between bands and polygons the gap is linear between corner rows, so those
rows are enough. A band's side keeps its distance from the track's centre line when
the track turns only if its offset is the half width / cos(turn); a track may turn by
ANGLE degrees (TIGHT_ANGLE when something is already within 0.02 mm of it) and its band
is drawn with that offset. Maximise the gain (at most GMAX), then minimise the total
shift. With --out the result (or --gain, if smaller) is applied.
"""
import sys, math, collections, time, json, argparse
sys.path.insert(0, "C:/Users/mleggiero/Documents/KiCad/taxelscan/tmp/usb-power-deps")
import numpy as np
import pcbnew as K
from shapely.geometry import Polygon, Point, LineString, box
from shapely.ops import unary_union, triangulate
from shapely.strtree import STRtree
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

ap = argparse.ArgumentParser()
ap.add_argument("board")
ap.add_argument("axis")
ap.add_argument("gmax", type=float)
ap.add_argument("--out")
ap.add_argument("--gain", type=float)
ap.add_argument("--freeze")
ap.add_argument("--angle", type=float, default=15.0)
ap.add_argument("--tight-angle", type=float, default=3.0)
ap.add_argument("--report", type=int, default=30)
ap.add_argument("--drop-nets", default="", help="what-if: leave these nets' tracks and vias out")
ap.add_argument("--drop-parts", default="", help="what-if: leave these parts out")
ap.add_argument("--bounds", action="store_true", help="also report binding variable bounds")
ap.add_argument("--from-high", action="store_true", help="keep the high edge and move the low one")
ap.add_argument("--pin-moving", default="", help="parts that move exactly with the moving edge")
ap.add_argument("--hold", default="", help="parts that do not move at all")
args = ap.parse_args()

mm, MM = K.ToMM, K.FromMM
LID = {"F": K.F_Cu, "In2": K.In2_Cu, "B": K.B_Cu}
CU, FINE, HOLE, EDGE, SAFE = 0.15, 0.10, 0.25, 0.22, 0.001
t0 = time.time()
b = K.LoadBoard(args.board)
swap = args.axis == "y"
SGN = -1.0 if args.from_high else 1.0
GMAX = args.gmax
frozen = set(json.load(open(args.freeze))) if args.freeze else set()
DROP_NETS = set(n for n in args.drop_nets.split(",") if n)
DROP_PARTS = set(n for n in args.drop_parts.split(",") if n)


def UV(x, y):
    return (SGN * y, x) if swap else (SGN * x, y)


def uvp(v):
    return UV(mm(v.x), mm(v.y))


def polyset(ps):
    out = []
    for k in range(ps.OutlineCount()):
        ch = ps.COutline(k)
        pts = [UV(mm(ch.CPoint(i).x), mm(ch.CPoint(i).y)) for i in range(ch.PointCount())]
        if len(pts) >= 3:
            out.append(Polygon(pts).buffer(0))
    return unary_union(out) if out else Polygon()


def convex_parts(g):
    out = []
    for poly in getattr(g, "geoms", [g]):
        if poly.is_empty:
            continue
        h = poly.convex_hull
        if h.area - poly.area <= 1e-4 * max(h.area, 1e-9):
            out.append(h)
            continue
        for tri in triangulate(poly):
            if poly.contains(tri.representative_point()):
                out.append(tri)
    return out


# board edge: the Edge.Cuts centre lines; clearance is taken from the line's inner side
eu, ew = [], 0.0
for dr in b.GetDrawings():
    if dr.GetLayer() == K.Edge_Cuts:
        eu += [uvp(dr.GetStart())[0], uvp(dr.GetEnd())[0]]
        ew = max(ew, mm(dr.GetWidth()))
U_LO, U_HI = min(eu), max(eu)
EDGE_EFF = EDGE + ew / 2

# ------------------------------------------------------------------ nodes
parent = {}


def find(x):
    parent.setdefault(x, x)
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def union(a, c):
    ra, rc = find(a), find(c)
    if ra != rc:
        parent[ra] = rc


# items: dict(t: 'P'|'D'|'S', grp: layer name | 'H' | 'CRT', kind: 'cu'|'hole'|'crt', net, ...)
#   P: poly (shapely, convex), k
#   D: c (u, v), r, k
#   S: a, c, w (half width = w/2), ka, kc, uuid (track) or None
items = []
pads_ln = collections.defaultdict(list)
vias_n = collections.defaultdict(list)
for f in b.GetFootprints():
    k = "fp:" + f.GetReference()
    find(k)
    if f.GetReference() in DROP_PARTS:
        continue
    for p in f.Pads():
        net = p.GetNetname() or ("nc:%s.%s" % (f.GetReference(), p.GetNumber()))
        if p.GetAttribute() != K.PAD_ATTRIB_NPTH:
            for L, lid in LID.items():
                if p.IsOnLayer(lid):
                    g = polyset(p.GetEffectivePolygon(lid, K.ERROR_OUTSIDE))
                    if g.is_empty:
                        continue
                    lc = p.GetLocalClearance()
                    lc = mm(lc) if lc else 0.0
                    for part in convex_parts(g):
                        items.append(dict(t="P", grp=L, kind="cu", net=net, poly=part, k=k, lc=lc))
                    pads_ln[(L, net)].append((k, g, uvp(p.GetPosition())))
        ds = p.GetDrillSize()
        if ds.x > 0:
            hnet = net if p.GetAttribute() != K.PAD_ATTRIB_NPTH else "npth:%s.%s" % (f.GetReference(), p.GetNumber())
            hx, hy = mm(p.GetPosition().x), mm(p.GetPosition().y)
            dx, dy = mm(ds.x), mm(ds.y)
            if abs(dx - dy) > 1e-6:
                a = math.radians(p.GetOrientationDegrees())
                L_, r = abs(dx - dy) / 2, min(dx, dy) / 2
                ux, uy = (math.cos(a), -math.sin(a)) if dx > dy else (math.sin(a), math.cos(a))
                c1, c2 = UV(hx - ux * L_, hy - uy * L_), UV(hx + ux * L_, hy + uy * L_)
                items.append(dict(t="S", grp="H", kind="hole", net=hnet, a=c1, c=c2, w=2 * r, ka=k, kc=k, uuid=None))
                items.append(dict(t="D", grp="H", kind="hole", net=hnet, c=c1, r=r, k=k))
                items.append(dict(t="D", grp="H", kind="hole", net=hnet, c=c2, r=r, k=k))
            else:
                items.append(dict(t="D", grp="H", kind="hole", net=hnet, c=UV(hx, hy), r=dx / 2, k=k))
    cy = f.GetCourtyard(K.F_CrtYd)
    if cy.OutlineCount():
        for part in convex_parts(polyset(cy)):
            items.append(dict(t="P", grp="CRT", kind="crt", net="crt:" + f.GetReference(), poly=part, k=k))

tracks = []
for t in b.GetTracks():
    u = t.m_Uuid.AsString()
    net = t.GetNetname()
    if net in DROP_NETS:
        continue
    if t.Type() == K.PCB_VIA_T:
        k = "via:" + u
        find(k)
        c = uvp(t.GetPosition())
        s, dr = mm(t.GetWidth(K.F_Cu)), mm(t.GetDrillValue())
        for L in LID:
            items.append(dict(t="D", grp=L, kind="cu", net=net, c=c, r=s / 2, k=k))
        items.append(dict(t="D", grp="H", kind="hole", net=net, c=c, r=dr / 2, k=k))
        vias_n[net].append((k, c, s / 2))
        continue
    if t.Type() == K.PCB_ARC_T:
        raise SystemExit("arcs are not handled")
    L = {K.F_Cu: "F", K.In2_Cu: "In2", K.B_Cu: "B"}.get(t.GetLayer())
    if L is None:
        continue
    tracks.append(dict(uuid=u, net=net, lay=L, a=uvp(t.GetStart()), c=uvp(t.GetEnd()), w=mm(t.GetWidth())))

# ---- joins: track ends to pads, vias and each other (with KiCad's tolerance: inside the copper)
ends_ln = collections.defaultdict(list)          # (layer, net) -> [(track idx, end idx, point, half width)]
for ti, tr in enumerate(tracks):
    for ei, e in enumerate((tr["a"], tr["c"])):
        ends_ln[(tr["lay"], tr["net"])].append((ti, ei, e, tr["w"] / 2))
        tr.setdefault("keys", [None, None])
        tr["keys"][ei] = "end:%d:%d" % (ti, ei)
        find(tr["keys"][ei])
for (L, net), lst in ends_ln.items():
    pts = [Point(e) for _, _, e, _ in lst]
    for (ti, ei, e, hw), pe in zip(lst, pts):
        key = tracks[ti]["keys"][ei]
        for kp, g, _ in pads_ln[(L, net)]:
            if g.distance(pe) <= hw + 1e-3:
                union(key, kp)
        for kv, c, r in vias_n[net]:
            if math.dist(c, e) <= r + hw + 1e-3:
                union(key, kv)
    for i in range(len(lst)):
        ti, ei, e, hw = lst[i]
        for j in range(i + 1, len(lst)):
            tj, ej, e2, hw2 = lst[j]
            if math.dist(e, e2) <= max(hw, hw2) + 1e-3:
                union(tracks[ti]["keys"][ei], tracks[tj]["keys"][ej])
for net, vs in vias_n.items():
    for i, (k1, c1, r1) in enumerate(vs):
        for k2, c2, r2 in vs[i + 1:]:
            if math.dist(c1, c2) < r1 + r2:
                union(k1, k2)
        for L in LID:
            for kp, g, _ in pads_ln[(L, net)]:
                if g.distance(Point(c1)) < r1:
                    union(k1, kp)
for tr in tracks:
    if tr["uuid"] in frozen:
        union(tr["keys"][0], tr["keys"][1])

# anchors on a track's interior (another track's end, a via or pad centre inside its copper)
eqs = []                     # ({key: coef}, rhs) ==
anchors = collections.defaultdict(list)
for ti, tr in enumerate(tracks):
    anchors[(tr["lay"], tr["net"])] += [(tr["keys"][0], tr["a"]), (tr["keys"][1], tr["c"])]
for net, vs in vias_n.items():
    for kv, c, r in vs:
        for L in LID:
            anchors[(L, net)].append((kv, c))
for (L, net), lst in pads_ln.items():
    for kp, g, c in lst:
        anchors[(L, net)].append((kp, c))
for tr in tracks:
    a, c, hw = tr["a"], tr["c"], tr["w"] / 2
    du, dv = c[0] - a[0], c[1] - a[1]
    ln2 = du * du + dv * dv
    if ln2 < 1e-10:
        continue
    ka, kc = tr["keys"]
    for key, q in anchors[(tr["lay"], tr["net"])]:
        if find(key) in (find(ka), find(kc)):
            continue
        lam = ((q[0] - a[0]) * du + (q[1] - a[1]) * dv) / ln2
        if lam <= 0 or lam >= 1:
            continue
        if math.hypot(q[0] - (a[0] + lam * du), q[1] - (a[1] + lam * dv)) > hw + 1e-3:
            continue
        eqs.append(({key: 1.0, ka: -(1 - lam), kc: -lam}, 0.0))

# ---- tracks: bands and round ends
cap_seen = {}
for tr in tracks:
    ka, kc = tr["keys"]
    for e, ke in ((tr["a"], ka), (tr["c"], kc)):
        sig = (tr["lay"], tr["net"], find(ke), round(e[0], 4), round(e[1], 4), round(tr["w"], 4))
        if sig not in cap_seen:
            cap_seen[sig] = dict(t="D", grp=tr["lay"], kind="cu", net=tr["net"], c=e, r=tr["w"] / 2, k=ke, ends=[])
            items.append(cap_seen[sig])
        cap_seen[sig]["ends"].append(tr["uuid"])
    if math.dist(tr["a"], tr["c"]) > 1e-4:
        items.append(dict(t="S", grp=tr["lay"], kind="cu", net=tr["net"], a=tr["a"], c=tr["c"], w=tr["w"], ka=ka, kc=kc, uuid=tr["uuid"]))

# escape areas
areas = []
for z in b.Zones():
    if z.GetIsRuleArea():
        areas.append(dict(name=z.GetZoneName(), poly=polyset(z.Outline()), key="fp:" + z.GetZoneName().split("_")[0], zone=z))


def exact_geom(it):
    if it["t"] == "P":
        return it["poly"]
    if it["t"] == "D":
        return Point(it["c"]).buffer(it["r"], 32)
    return LineString([it["a"], it["c"]]).buffer(it["w"] / 2, 32)


def centre_geom(it):
    if it["t"] == "P":
        return it["poly"]
    if it["t"] == "D":
        return Point(it["c"])
    return LineString([it["a"], it["c"]])


def half(it):
    return 0.0 if it["t"] == "P" else (it["r"] if it["t"] == "D" else it["w"] / 2)


for it in items:
    it["geom"] = exact_geom(it)
    it["areas"] = frozenset(a["name"] for a in areas if it["kind"] == "cu" and it["geom"].intersects(a["poly"]))
    if it["t"] == "S" and find(it["ka"]) == find(it["kc"]):
        it["rigid"] = True

# ---- tightness: how close each track already is to anything it must clear
groups = {L: [i for i, it in enumerate(items) if it["grp"] in (L, "H")] for L in LID}
groups["CRT"] = [i for i, it in enumerate(items) if it["grp"] == "CRT"]


def clearance(A, B):
    ka, kb = A["kind"], B["kind"]
    if (ka == "crt") != (kb == "crt"):
        return None
    if ka == "crt":
        return 0.0
    if ka == "hole" and kb == "hole":
        return HOLE
    if A["net"] == B["net"]:
        return None
    if ka == "hole" or kb == "hole":
        return HOLE
    base = FINE if (A["areas"] & B["areas"]) else CU
    return max(base, A.get("lc", 0.0), B.get("lc", 0.0))


def nodes_of(it):
    if it["t"] == "S":
        return {find(it["ka"]), find(it["kc"])}
    return {find(it["k"])}


tight_min = collections.defaultdict(lambda: 9.0)
trees = {}
for gname, idx in groups.items():
    trees[gname] = STRtree([items[i]["geom"] for i in idx])
    if gname == "CRT":
        continue
    for i in idx:
        A = items[i]
        if A["t"] != "S" or A.get("rigid"):
            continue
        for jj in trees[gname].query(A["geom"].buffer(0.3)):
            j = idx[int(jj)]
            if j == i:
                continue
            B = items[j]
            c = clearance(A, B)
            if c is None or nodes_of(A) & nodes_of(B) == nodes_of(A) == nodes_of(B):
                continue
            d = centre_geom(A).distance(centre_geom(B)) - A["w"] / 2 - half(B) - c
            need = (A["w"] / 2 + half(B) + c) * (1 / math.cos(math.radians(args.angle)) - 1) + 0.006
            tight_min[i] = min(tight_min[i], d - need)
n_tight = 0
TIGHT = math.tan(math.radians(0))  # placeholder, replaced below
for i, it in enumerate(items):
    if it["t"] == "S":
        if it.get("rigid") or abs(it["a"][1] - it["c"][1]) < 1e-6:
            it["turn"] = 0.0
        elif tight_min[i] < 0.0:
            it["turn"] = args.tight_angle
            n_tight += 1
        else:
            it["turn"] = args.angle
# a track's turn allowance is the smaller one if any of its bands (on the board there is one) is tight
turn_of = {it["uuid"]: it["turn"] for it in items if it["t"] == "S" and it["uuid"]}
for it in items:
    if it["t"] == "D":
        it["turn"] = max([turn_of.get(u, 0.0) for u in it.get("ends", [])] or [0.0])
    elif it["t"] == "P":
        it["turn"] = 0.0

# ------------------------------------------------------------------ constraint rows
keys = sorted({find(k) for k in parent})
KID = {k: i for i, k in enumerate(keys)}
NV = len(keys)
G = NV


def kid(k):
    return KID[find(k)]


rows_simple = {}              # (ky, kx) -> (rhs, meta)
rows_gen = []                 # (coef dict, rhs, meta)
stats = collections.Counter()


def add(coef, g, meta, lerp=False):
    c2 = collections.defaultdict(float)
    for k, v in coef:
        c2[k] += v
    c2 = {k: v for k, v in c2.items() if abs(v) > 1e-9}
    rhs = g - SAFE
    if not c2:
        if rhs < -1e-3:
            stats["violated-now"] += 1
        return
    if rhs < -0.02:
        stats["clamped"] += 1
        rhs = 0.0
    elif rhs < 0:
        stats["tight"] += 1
    if len(c2) == 2 and sorted(round(v, 9) for v in c2.values()) == [-1.0, 1.0]:
        ky = next(k for k, v in c2.items() if v > 0)
        kx = next(k for k, v in c2.items() if v < 0)
        if (ky, kx) not in rows_simple or rows_simple[(ky, kx)][0] > rhs:
            rows_simple[(ky, kx)] = (rhs, meta)
    else:
        rows_gen.append((c2, rhs, meta))


def band_poly(it, extra, turn=None):
    """corners (4,2) and labels for a band grown by `extra`, offset for its turn allowance"""
    a, c = np.array(it["a"]), np.array(it["c"])
    d = c - a
    d /= np.linalg.norm(d)
    n = np.array([-d[1], d[0]])
    R = (it["w"] / 2 + extra) / math.cos(math.radians(it.get("turn", 0.0) if turn is None else turn))
    cs = np.array([a + R * n, c + R * n, c - R * n, a - R * n])
    ka, kc = kid(it["ka"]), kid(it["kc"])
    return cs, np.array([ka, kc, kc, ka])


def rigid_poly(it, extra, turn=0.0):
    extra = extra / math.cos(math.radians(turn))
    g = it["poly"] if extra == 0 else it["poly"].buffer(extra, 16)
    cs = np.asarray(g.exterior.coords)[:-1]
    return cs, np.full(len(cs), kid(it["k"]))


def chains(cs, ls, vs, side):
    u1, v1 = cs[:, 0], cs[:, 1]
    u2, v2 = np.roll(u1, -1), np.roll(v1, -1)
    k1, k2 = ls, np.roll(ls, -1)
    V = vs[:, None]
    lo, hi = np.minimum(v1, v2), np.maximum(v1, v2)
    ins = (V >= lo - 1e-9) & (V <= hi + 1e-9)
    dv = v2 - v1
    flat = np.abs(dv) < 1e-12
    t = np.clip(np.where(flat, 0.0, (V - v1) / np.where(flat, 1.0, dv)), 0.0, 1.0)
    uu = u1 + t * (u2 - u1)
    if side == 0:
        tf = np.broadcast_to(np.where(u1 <= u2, 0.0, 1.0), t.shape)
        m = np.where(ins, np.where(flat, np.minimum(u1, u2), uu), np.inf)
        e = m.argmin(axis=1)
    else:
        tf = np.broadcast_to(np.where(u1 >= u2, 0.0, 1.0), t.shape)
        m = np.where(ins, np.where(flat, np.maximum(u1, u2), uu), -np.inf)
        e = m.argmax(axis=1)
    rows = np.arange(len(vs))
    tt = np.where(flat, tf, t)[rows, e]
    return m[rows, e], k1[e], k2[e], tt


def shape(it, extra, turn=None):
    if it["t"] == "S":
        return band_poly(it, extra, None if turn is None else max(turn, it.get("turn", 0.0)))
    return rigid_poly(it, extra, turn or 0.0)


def poly_vs_poly(A, B, c, meta):
    """A grown by c against B; rows at every corner of either"""
    ca, la = shape(A, c)
    cb, lb = shape(B, 0.0)
    lo, hi = max(ca[:, 1].min(), cb[:, 1].min()), min(ca[:, 1].max(), cb[:, 1].max())
    if hi - lo < 1e-4:
        return
    vs = np.concatenate([ca[:, 1], cb[:, 1], [lo, hi]])
    vs = np.unique(vs[(vs >= lo - 1e-9) & (vs <= hi + 1e-9)])
    aR, a1, a2, at = chains(ca, la, vs, 1)
    bL, b1, b2, bt = chains(cb, lb, vs, 0)
    aL, a1l, a2l, atl = chains(ca, la, vs, 0)
    bR, b1r, b2r, btr = chains(cb, lb, vs, 1)
    ok = np.isfinite(aR) & np.isfinite(bL)
    if not ok.any():
        return
    right = (bL - aR)[ok].min()
    left = (aL - bR)[ok].min()
    if right >= -0.02 and right >= left:
        for r in np.nonzero(ok)[0]:
            g = bL[r] - aR[r]
            if g <= GMAX + 0.5:
                add([(b1[r], 1 - bt[r]), (b2[r], bt[r]), (a1[r], -(1 - at[r])), (a2[r], -at[r])], g, meta)
    elif left >= -0.02:
        for r in np.nonzero(ok)[0]:
            g = aL[r] - bR[r]
            if g <= GMAX + 0.5:
                add([(a1l[r], 1 - atl[r]), (a2l[r], atl[r]), (b1r[r], -(1 - btr[r])), (b2r[r], -btr[r])], g, meta)
    else:
        stats["overlap"] += 1
        stats_ov.append(meta)


stats_ov = []


def disc_vs(D, B, c, meta):
    """disc D against item B: D's centre against B grown by r + c, at the centre's row"""
    uc, vc = D["c"]
    kd = kid(D["k"])
    if B["t"] == "D":
        R = (D["r"] + B["r"] + c) / math.cos(math.radians(max(D.get("turn", 0.0), B.get("turn", 0.0))))
        dv = B["c"][1] - vc
        if abs(dv) >= R - 1e-4:
            return
        h = math.sqrt(R * R - dv * dv)
        du = B["c"][0] - uc
        kb = kid(B["k"])
        if du >= 0:
            add([(kb, 1.0), (kd, -1.0)], du - h, meta)
        else:
            add([(kd, 1.0), (kb, -1.0)], -du - h, meta)
        return
    cs, ls = shape(B, D["r"] + c, D.get("turn", 0.0))
    if not (cs[:, 1].min() + 1e-4 < vc < cs[:, 1].max() - 1e-4):
        return
    vs = np.array([vc])
    L, l1, l2, lt = chains(cs, ls, vs, 0)
    R, r1, r2, rt = chains(cs, ls, vs, 1)
    if not (np.isfinite(L[0]) and np.isfinite(R[0])):
        return
    if uc >= R[0] - 0.02 and (uc - R[0]) >= (L[0] - uc):
        add([(kd, 1.0), (r1[0], -(1 - rt[0])), (r2[0], -rt[0])], uc - R[0], meta)
    elif uc <= L[0] + 0.02:
        add([(l1[0], 1 - lt[0]), (l2[0], lt[0]), (kd, -1.0)], L[0] - uc, meta)
    else:
        stats["overlap"] += 1
        stats_ov.append(meta)


def describe(A):
    if A["t"] == "D":
        return "%s disc %s r%.3f @(%.2f,%.2f)" % (A["grp"], A["net"], A["r"], *A["c"])
    if A["t"] == "S":
        return "%s band %s w%.2f (%.2f,%.2f)-(%.2f,%.2f)" % (A["grp"], A["net"], A["w"], *A["a"], *A["c"])
    bx = A["poly"].bounds
    return "%s poly %s %s [%.2f..%.2f, %.2f..%.2f]" % (A["grp"], A["net"], A["k"], bx[0], bx[2], bx[1], bx[3])


W = GMAX + 0.5
seen = set()
for gname, idx in groups.items():
    tree = trees[gname]
    for i in idx:
        A = items[i]
        u0, v0, u1, v1 = A["geom"].bounds
        for jj in tree.query(box(u0 - W, v0 - 0.8, u1 + W, v1 + 0.8)):
            j = idx[int(jj)]
            if j <= i or (i, j) in seen:
                continue
            B = items[j]
            c = clearance(A, B)
            if c is None:
                continue
            na, nb = nodes_of(A), nodes_of(B)
            if len(na) == 1 and na == nb:
                continue
            seen.add((i, j))
            stats["pairs"] += 1
            meta = (i, j, c)
            if A["t"] == "D":
                disc_vs(A, B, c, meta)
            elif B["t"] == "D":
                disc_vs(B, A, c, meta)
            else:
                poly_vs_poly(A, B, c, meta)
print("items %d, tracks %d (%d tight), pairs %d, overlaps %d, clamped %d, rows needing separation %d, simple rows %d, general rows %d, equalities %d (%.0fs)" % (
    len(items), len(tracks), n_tight, stats["pairs"], stats["overlap"], stats["clamped"], stats["tight"], len(rows_simple), len(rows_gen), len(eqs), time.time() - t0), flush=True)
for m in stats_ov[:8]:
    print("   overlap in the model:", describe(items[m[0]]), "|", describe(items[m[1]]), "c=%.2f" % m[2])

# ------------------------------------------------------------------ assemble
Rr, Cc, Vv, Bu, META = [], [], [], [], []


def put(coef, rhs, meta):
    r = len(Bu)
    for k, v in coef.items():
        Rr.append(r); Cc.append(k); Vv.append(v)
    Bu.append(rhs)
    META.append(meta)


for (ky, kx), (rhs, meta) in rows_simple.items():
    put({ky: 1.0, kx: -1.0}, rhs, meta)
for coef, rhs, meta in rows_gen:
    put(coef, rhs, meta)
# track turn and order
for tr in tracks:
    ka, kc = kid(tr["keys"][0]), kid(tr["keys"][1])
    if ka == kc:
        continue
    a, c = tr["a"], tr["c"]
    du, dv = c[0] - a[0], c[1] - a[1]
    if abs(dv) < 1e-6:
        lo, hi = (ka, kc) if du > 0 else (kc, ka)
        put({hi: 1.0, lo: -1.0}, max(abs(du) - 0.005, 0.0), ("order", tr["uuid"]))
        continue
    if dv < 0:
        ka, kc, du, dv = kc, ka, -du, -dv
    phi = math.degrees(math.atan2(du, dv))
    turn = turn_of.get(tr["uuid"], args.angle)
    lo_phi = min(max(phi - turn, -89.9), phi)
    hi_phi = max(min(phi + turn, 89.9), phi)
    put({kc: 1.0, ka: -1.0}, du - dv * math.tan(math.radians(lo_phi)), ("turn", tr["uuid"]))
    put({ka: 1.0, kc: -1.0}, dv * math.tan(math.radians(hi_phi)) - du, ("turn", tr["uuid"]))
# board edges
lo_lim, hi_lim = {}, {}
for i, it in enumerate(items):
    if it["kind"] == "hole":
        continue
    m = EDGE_EFF if it["kind"] == "cu" else 0.0
    if it["t"] == "D":
        pts = [(it["c"][0] - it["r"], kid(it["k"])), (it["c"][0] + it["r"], kid(it["k"]))]
    else:
        cs, ls = shape(it, 0.0)
        pts = list(zip(cs[:, 0], ls))
    if it["kind"] == "crt" and (min(p[0] for p in pts) < U_LO or max(p[0] for p in pts) > U_HI):
        continue
    for uu, k in pts:
        k = int(k)
        lo_lim[k] = min(lo_lim.get(k, 1e9), uu - U_LO - m)
        hi_lim[k] = min(hi_lim.get(k, 1e9), U_HI - m - uu)
for f in b.GetFootprints():
    k = kid("fp:" + f.GetReference())
    us = []
    for g in f.GraphicalItems():
        if g.GetLayer() in (K.F_SilkS, K.B_SilkS) and g.IsVisible() if hasattr(g, "IsVisible") else g.GetLayer() in (K.F_SilkS, K.B_SilkS):
            bb = g.GetBoundingBox()
            us += [uvp(K.VECTOR2I(bb.GetX(), bb.GetY()))[0], uvp(K.VECTOR2I(bb.GetRight(), bb.GetBottom()))[0]]
    if us and min(us) >= U_LO and max(us) <= U_HI:
        lo_lim[k] = min(lo_lim.get(k, 1e9), min(us) - U_LO - ew / 2)
        hi_lim[k] = min(hi_lim.get(k, 1e9), U_HI - ew / 2 - max(us))
for k, v in lo_lim.items():
    put({k: 1.0}, max(v - SAFE, 0.0), ("edge-lo", k))
for k, v in hi_lim.items():
    put({G: 1.0, k: -1.0}, max(v - SAFE, 0.0), ("edge-hi", k))
put({k: -1.0 for k in []} or {G: -1.0}, 0.0, ("gain>=0",))
# items in an escape area stay in it
for i, it in enumerate(items):
    for a in areas:
        if a["name"] not in it["areas"]:
            continue
        inter = it["geom"].intersection(a["poly"])
        if inter.is_empty:
            continue
        v = inter.representative_point().coords[0][1]
        au0, av0, au1, av1 = a["poly"].bounds
        kq = kid(a["key"])
        if it["t"] == "D":
            half_w = math.sqrt(max(it["r"] ** 2 - (v - it["c"][1]) ** 2, 0.0))
            Lb, Rb = it["c"][0] - half_w, it["c"][0] + half_w
            kk = kid(it["k"])
            put({kq: 1.0, kk: -1.0}, max(au1 - 0.01 - Lb, 0.0), ("area", i))
            put({kk: 1.0, kq: -1.0}, max(Rb - au0 - 0.01, 0.0), ("area", i))
        else:
            cs, ls = shape(it, 0.0)
            vs = np.array([v])
            Lb, l1, l2, lt = chains(cs, ls, vs, 0)
            Rb, r1, r2, rt = chains(cs, ls, vs, 1)
            if not (np.isfinite(Lb[0]) and np.isfinite(Rb[0])):
                continue
            put({kq: 1.0, int(l1[0]): -(1 - lt[0]), int(l2[0]): -lt[0]}, max(au1 - 0.01 - Lb[0], 0.0), ("area", i))
            put({int(r1[0]): 1 - rt[0], int(r2[0]): rt[0], kq: -1.0}, max(Rb[0] - au0 - 0.01, 0.0), ("area", i))
for ref in [r for r in args.pin_moving.split(",") if r]:
    eqs.append(({"fp:" + ref: 1.0, "__G__": -1.0}, 0.0))
for ref in [r for r in args.hold.split(",") if r]:
    eqs.append(({"fp:" + ref: 1.0}, 0.0))
Re, Ce, Ve, Be = [], [], [], []
for coef, rhs in eqs:
    r = len(Be)
    tot = collections.defaultdict(float)
    for k, v in coef.items():
        tot[G if k == "__G__" else kid(k)] += v
    for k, v in tot.items():
        if abs(v) > 1e-12:
            Re.append(r); Ce.append(k); Ve.append(v)
    Be.append(rhs)
# rows that ask for separation get a heavily penalised slack, so an unreachable one
# degrades to "no closer than now" instead of making the programme infeasible
NEG = [r for r, v in enumerate(Bu) if v < 0]
NS = len(NEG)
for j, r in enumerate(NEG):
    Rr.append(r); Cc.append(NV + 1 + j); Vv.append(-1.0)
nvar = NV + 1 + NS
A_ub = coo_matrix((Vv, (Rr, Cc)), shape=(len(Bu), nvar)).tocsr()
b_ub = np.array(Bu)
SL_B = [(0.0, -Bu[r]) for r in NEG]
PEN = 200.0
A_eq = coo_matrix((Ve, (Re, Ce)), shape=(len(Be), nvar)).tocsr() if Be else None
b_eq = np.array(Be) if Be else None
print("LP: %d variables, %d rows, %d equalities (%.0fs)" % (nvar, len(Bu), len(Be), time.time() - t0), flush=True)
cvec = np.zeros(nvar)
cvec[G] = -1.0
cvec[NV + 1:] = PEN
res = linprog(cvec, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=[(-0.5, GMAX)] * NV + [(0.0, GMAX)] + SL_B, method="highs")
print("stage 1:", res.status, res.message)
if res.status != 0:
    raise SystemExit(1)
best = float(res.x[G])
used = res.x[NV + 1:]
print("largest gain %.3f mm (%.0fs); separation rows left unmet: %d (max %.4f mm)" % (
    best, time.time() - t0, int((used > 1e-7).sum()), float(used.max()) if NS else 0.0), flush=True)
if args.bounds:
    lo_m, up_m = res.lower.marginals, res.upper.marginals
    for i in np.argsort(lo_m)[::-1][:15]:
        if abs(lo_m[i]) > 1e-6:
            print("   lower bound binds: %s  %.3f  p=%.3f" % (keys[i] if i < NV else "G", lo_m[i], res.x[i]))
    for i in np.argsort(up_m)[:15]:
        if abs(up_m[i]) > 1e-6:
            print("   upper bound binds: %s  %.3f  p=%.3f" % (keys[i] if i < NV else "G", up_m[i], res.x[i]))


# what holds it back: rows with the largest duals
duals = res.ineqlin.marginals
order = np.argsort(duals)[: args.report]
print("binding constraints (dual, row):")
for r in order:
    if duals[r] > -1e-6:
        break
    m = META[r]
    if isinstance(m[0], (int, np.integer)) and len(m) == 3:
        desc = "%s  |  %s  c=%.2f" % (describe(items[m[0]]), describe(items[m[1]]), m[2])
    elif m[0] in ("edge-lo", "edge-hi"):
        desc = "%s %s" % (m[0], keys[m[1]])
    elif m[0] in ("turn", "order"):
        tr = next(t for t in tracks if t["uuid"] == m[1])
        desc = "%s track %s %s (%.2f,%.2f)-(%.2f,%.2f)" % (m[0], tr["net"], tr["lay"], *tr["a"], *tr["c"])
    else:
        desc = str(m)
    print("  %8.3f  %s" % (duals[r], desc))

if args.out is None:
    raise SystemExit(0)
g_use = best if args.gain is None else min(best, args.gain)
g_use = max(0.0, g_use - 0.001)
from scipy.sparse import hstack, identity, vstack, csr_matrix
# |p| via p = q - r with q, r >= 0: minimise sum(q + r)
A2 = hstack([A_ub[:, :NV], -A_ub[:, :NV], A_ub[:, NV:]]).tocsr()
Aeq2 = hstack([A_eq[:, :NV], -A_eq[:, :NV], A_eq[:, NV:]]).tocsr() if A_eq is not None else None
c2 = np.concatenate([np.ones(NV), np.ones(NV), [0.0], np.full(NS, PEN)])
bounds2 = [(0.0, GMAX)] * NV + [(0.0, 0.5)] * NV + [(g_use, g_use)] + SL_B
res2 = linprog(c2, A_ub=A2, b_ub=b_ub, A_eq=Aeq2, b_eq=b_eq, bounds=bounds2, method="highs")
if res2.status == 0:
    sl2 = res2.x[2 * NV + 1:]
    print("stage 2 separation rows left unmet: %d (max %.4f mm)" % (int((sl2 > 1e-7).sum()), float(sl2.max()) if NS else 0.0))
    res2.x = np.concatenate([res2.x[:NV] - res2.x[NV:2 * NV], res2.x[2 * NV:]])
print("stage 2:", res2.status, res2.message)
if res2.status != 0:
    raise SystemExit(1)
p = res2.x
print("total shift %.1f mm over %d nodes" % (p[:NV].sum(), int((p[:NV] > 1e-6).sum())))

if args.out:
    def sh(k):
        return float(p[kid(k)])

    def dvec(d):
        d = float(d) * SGN
        return K.VECTOR2I(0, MM(-d)) if swap else K.VECTOR2I(MM(-d), 0)

    for f in b.GetFootprints():
        d = sh("fp:" + f.GetReference())
        if abs(d) > 1e-7:
            f.Move(dvec(d))
    trk = {tr["uuid"]: tr for tr in tracks}
    for t in b.GetTracks():
        u = t.m_Uuid.AsString()
        if t.Type() == K.PCB_VIA_T:
            d = sh("via:" + u)
            if abs(d) > 1e-7:
                t.Move(dvec(d))
        elif u in trk:
            tr = trk[u]
            s0, e0 = t.GetStart(), t.GetEnd()
            t.SetStart(s0 + dvec(sh(tr["keys"][0])))
            t.SetEnd(e0 + dvec(sh(tr["keys"][1])))
    for a in areas:
        d = sh(a["key"])
        if abs(d) > 1e-7:
            a["zone"].Move(dvec(d))
    new_hi = float(U_HI - g_use)
    for dr in b.GetDrawings():
        if dr.GetLayer() == K.Edge_Cuts:
            for get, set_ in ((dr.GetStart, dr.SetStart), (dr.GetEnd, dr.SetEnd)):
                q = get()
                if abs(uvp(q)[0] - U_HI) < 1e-4:
                    real = SGN * new_hi
                    set_(K.VECTOR2I(q.x, MM(real)) if swap else K.VECTOR2I(MM(real), q.y))
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        o = z.Outline()
        for vi in range(o.TotalVertices()):
            q = o.CVertex(vi)
            if uvp(q)[0] > (U_LO + U_HI) / 2:
                o.SetVertex(vi, q + dvec(g_use))
    b.Save(args.out)
    json.dump(dict(axis=args.axis, gain=g_use, best=best), open(args.out + ".json", "w"))
    print("applied %.3f mm: edge %.3f -> %.3f; saved %s (%.0fs)" % (g_use, U_HI, new_hi, args.out, time.time() - t0))
