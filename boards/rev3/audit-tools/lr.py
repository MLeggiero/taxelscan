"""lr.py - exact-geometry local router and editor for rev3.kicad_pcb.

Obstacles come from pcbnew with real pad polygons. Clearance follows the board:
0.15 mm, 0.10 mm when both items intersect a rule area (rev3.kicad_dru),
0.25 mm copper-to-hole, 0.25 mm hole-to-hole, 0.22 mm copper-to-edge. Paths are
searched on a fine grid by tmp/grid_route.exe, simplified by line of sight, and
every emitted segment and via is re-checked exactly with shapely before it goes
on the board.
"""
import sys, os, math, struct, subprocess, json, collections
ROOT = "C:/Users/mleggiero/Documents/KiCad/taxelscan"
sys.path.insert(0, ROOT + "/tmp/usb-power-deps")
import numpy as np
import shapely
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import unary_union
from shapely.strtree import STRtree
from scipy.ndimage import distance_transform_edt
import pcbnew as K

PCB = ROOT + "/boards/rev3/rev3.kicad_pcb"
KICLI = "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
GRE = ROOT + "/tmp/grid_route.exe"
SCR = os.path.join(ROOT, "tmp")          # scratch files, DRC reports, grid_route*.exe
LAYERS = ["F", "In2", "B"]
ALLCU = ["F", "In1", "In2", "B"]
LID = {"F": K.F_Cu, "In1": K.In1_Cu, "In2": K.In2_Cu, "B": K.B_Cu}
LNAME = {v: k for k, v in LID.items()}
RULE, FINE, HOLE, H2H, EDGE, SAFE = 0.15, 0.10, 0.25, 0.25, 0.22, 0.003
mm = K.ToMM


def V(x, y):
    return K.VECTOR2I(K.FromMM(x), K.FromMM(y))


def seg_geom(a, c, w):
    return (LineString([a, c]) if (abs(a[0] - c[0]) > 1e-9 or abs(a[1] - c[1]) > 1e-9) else Point(a)).buffer(w / 2, 16)


def polyset(ps):
    out = []
    for k in range(ps.OutlineCount()):
        ch = ps.COutline(k)
        out.append(Polygon([(mm(ch.CPoint(i).x), mm(ch.CPoint(i).y)) for i in range(ch.PointCount())]))
    return unary_union(out)


class Model:
    def __init__(self, path=PCB):
        self.path = path
        self.b = K.LoadBoard(path)
        self.index()

    # ------------------------------------------------------------ model
    def index(self):
        b = self.b
        areas = []
        for z in b.Zones():
            if z.GetIsRuleArea():
                areas.append(polyset(z.Outline()))
        self.area = unary_union(areas) if areas else Polygon()
        e = b.GetBoardEdgesBoundingBox()
        self.edge = (mm(e.GetX()), mm(e.GetY()), mm(e.GetRight()), mm(e.GetBottom()))
        self.items = []
        for f in b.GetFootprints():
            for p in f.Pads():
                lays = {l for l in ALLCU if p.IsOnLayer(LID[l])}
                hx, hy = mm(p.GetPosition().x), mm(p.GetPosition().y)
                ds = p.GetDrillSize()
                hole = None
                if ds.x > 0:
                    a = math.radians(p.GetOrientationDegrees())
                    dx, dy = mm(ds.x), mm(ds.y)
                    if abs(dx - dy) > 1e-6:
                        L, r = abs(dx - dy) / 2, min(dx, dy) / 2
                        ux, uy = (math.cos(a), -math.sin(a)) if dx > dy else (math.sin(a), math.cos(a))
                        hole = LineString([(hx - ux * L, hy - uy * L), (hx + ux * L, hy + uy * L)]).buffer(r, 16)
                    else:
                        hole = Point(hx, hy).buffer(dx / 2, 32)
                geom = None
                if lays and p.GetAttribute() != K.PAD_ATTRIB_NPTH:
                    lay0 = LID["F"] if "F" in lays else LID["B"] if "B" in lays else LID[sorted(lays)[0]]
                    geom = polyset(p.GetEffectivePolygon(lay0, K.ERROR_OUTSIDE))   # never smaller than the real copper
                if geom is None and hole is None:
                    continue
                self.items.append(dict(kind="pad", net=p.GetNetname(), lay=lays, geom=geom, hole=hole,
                                       uuid=p.m_Uuid.AsString(), ref="%s.%s" % (f.GetReference(), p.GetNumber()),
                                       xy=(hx, hy)))
        for t in b.GetTracks():
            n, u = t.GetNetname(), t.m_Uuid.AsString()
            if t.Type() == K.PCB_VIA_T:
                x, y = mm(t.GetPosition().x), mm(t.GetPosition().y)
                s, dr = mm(t.GetWidth(K.F_Cu)), mm(t.GetDrillValue())
                self.items.append(dict(kind="via", net=n, lay=set(ALLCU), geom=Point(x, y).buffer(s / 2, 32),
                                       hole=Point(x, y).buffer(dr / 2, 32), uuid=u, ref="via", xy=(x, y), size=s, drill=dr))
            else:
                l = LNAME.get(t.GetLayer())
                a = (mm(t.GetStart().x), mm(t.GetStart().y))
                c = (mm(t.GetEnd().x), mm(t.GetEnd().y))
                w = mm(t.GetWidth())
                self.items.append(dict(kind="track", net=n, lay={l}, geom=seg_geom(a, c, w), hole=None, uuid=u,
                                       ref="trk", a=a, c=c, w=w))
        for it in self.items:
            it["in_area"] = bool((it["geom"] is not None and it["geom"].intersects(self.area)) or
                                 (it["hole"] is not None and it["hole"].intersects(self.area)))
        self.by_layer = {}
        for l in ALLCU:
            its = [it for it in self.items if l in it["lay"] and it["geom"] is not None]
            self.by_layer[l] = (its, STRtree([it["geom"] for it in its]))
        hs = [it for it in self.items if it["hole"] is not None]
        self.holes = (hs, STRtree([it["hole"] for it in hs]))
        self.uuid = {it["uuid"]: it for it in self.items}

    def pad(self, ref, num):
        for it in self.items:
            if it["kind"] == "pad" and it["ref"] == "%s.%s" % (ref, num):
                return it
        raise KeyError((ref, num))

    def net_items(self, net, kinds=("track", "via", "pad")):
        return [it for it in self.items if it["net"] == net and it["kind"] in kinds]

    def near(self, geom, dist, layers=ALLCU, kinds=("track", "via", "pad")):
        out = {}
        for l in layers:
            its, tree = self.by_layer[l]
            for k in tree.query(geom.buffer(dist)):
                it = its[k]
                if it["kind"] in kinds and geom.distance(it["geom"]) <= dist:
                    out[it["uuid"]] = it
        return list(out.values())

    # ------------------------------------------------------------ exact checks
    def check_track(self, net, l, a, c, w, ignore=frozenset()):
        g = seg_geom(a, c, w)
        ina = g.intersects(self.area)
        bad = []
        its, tree = self.by_layer[l]
        for k in tree.query(g.buffer(RULE + 0.02)):
            it = its[k]
            if it["uuid"] in ignore or (net and it["net"] == net):
                continue
            need = FINE if (ina and it["in_area"]) else RULE
            d = g.distance(it["geom"])
            if d < need - 1e-4:
                bad.append((round(d, 4), need, it["net"], it["ref"], it["uuid"]))
        hs, ht = self.holes
        for k in ht.query(g.buffer(HOLE + 0.02)):
            it = hs[k]
            if it["uuid"] in ignore or (net and it["net"] == net):
                continue
            d = g.distance(it["hole"])
            if d < HOLE - 1e-4:
                bad.append((round(d, 4), HOLE, it["net"], it["ref"] + "(hole)", it["uuid"]))
        x0, y0, x1, y1 = self.edge
        bx = g.bounds
        de = min(bx[0] - x0, bx[1] - y0, x1 - bx[2], y1 - bx[3])
        if de < EDGE - 1e-4:
            bad.append((round(de, 4), EDGE, "", "edge", ""))
        return bad

    def check_via(self, net, x, y, size=0.5, drill=0.3, ignore=frozenset()):
        g = Point(x, y).buffer(size / 2, 32)
        h = Point(x, y).buffer(drill / 2, 32)
        ina = g.intersects(self.area)
        bad = []
        seen = set()
        for l in ALLCU:
            its, tree = self.by_layer[l]
            for k in tree.query(g.buffer(0.5)):
                it = its[k]
                if it["uuid"] in ignore or it["uuid"] in seen or (net and it["net"] == net):
                    continue
                seen.add(it["uuid"])
                need = FINE if (ina and it["in_area"]) else RULE
                d = g.distance(it["geom"])
                if d < need - 1e-4:
                    bad.append((round(d, 4), need, it["net"], it["ref"], it["uuid"]))
                dh = h.distance(it["geom"])
                if dh < HOLE - 1e-4:
                    bad.append((round(dh, 4), HOLE, it["net"], it["ref"] + "(to hole)", it["uuid"]))
        hs, ht = self.holes
        for k in ht.query(g.buffer(0.6)):
            it = hs[k]
            if it["uuid"] in ignore:
                continue
            if it["kind"] == "via" and it["net"] == net and math.hypot(it["xy"][0] - x, it["xy"][1] - y) < 1e-6:
                continue                                  # the via itself, already committed
            d = h.distance(it["hole"])
            if d < H2H - 1e-4:
                bad.append((round(d, 4), H2H, it["net"], it["ref"] + "(hole-hole)", it["uuid"]))
            if not (net and it["net"] == net):
                d2 = g.distance(it["hole"])
                if d2 < HOLE - 1e-4:
                    bad.append((round(d2, 4), HOLE, it["net"], it["ref"] + "(its hole)", it["uuid"]))
        x0, y0, x1, y1 = self.edge
        de = min(x - x0, y - y0, x1 - x, y1 - y) - size / 2
        if de < EDGE - 1e-4:
            bad.append((round(de, 4), EDGE, "", "edge", ""))
        return bad

    # ------------------------------------------------------------ grid masks
    def masks(self, net, win, g, w, ignore=frozenset(), via=(0.5, 0.3)):
        x0, y0, x1, y1 = win
        nx = int(math.floor((x1 - x0) / g + 1e-9)) + 1
        ny = int(math.floor((y1 - y0) / g + 1e-9)) + 1
        xs = x0 + np.arange(nx) * g
        ys = y0 + np.arange(ny) * g
        X, Y = np.meshgrid(xs, ys)
        ina_t = shapely.intersects_xy(self.area.buffer(w / 2), X, Y)
        ina_v = shapely.intersects_xy(self.area.buffer(via[0] / 2), X, Y)
        wb = box(x0 - 1, y0 - 1, x1 + 1, y1 + 1)

        def stamp(mask, geom, cond=None):
            bx0, by0, bx1, by1 = geom.bounds
            j0 = max(0, int(math.floor((bx0 - x0) / g)))
            j1 = min(nx - 1, int(math.ceil((bx1 - x0) / g)))
            i0 = max(0, int(math.floor((by0 - y0) / g)))
            i1 = min(ny - 1, int(math.ceil((by1 - y0) / g)))
            if j0 > j1 or i0 > i1:
                return
            sub = shapely.intersects_xy(geom, X[i0:i1 + 1, j0:j1 + 1], Y[i0:i1 + 1, j0:j1 + 1])
            if cond is not None:
                sub &= cond[i0:i1 + 1, j0:j1 + 1]
            mask[i0:i1 + 1, j0:j1 + 1] |= sub

        ex0, ey0, ex1, ey1 = self.edge
        free, own = {}, {}
        for l in LAYERS:
            blk = np.zeros((ny, nx), bool)
            ow = np.zeros((ny, nx), bool)
            its, tree = self.by_layer[l]
            for k in tree.query(wb):
                it = its[k]
                if it["uuid"] in ignore:
                    continue
                if net and it["net"] == net:
                    er = it["geom"].buffer(-w / 2 + 0.004)
                    if not er.is_empty:
                        stamp(ow, er)
                    continue
                if it["in_area"]:
                    stamp(blk, it["geom"].buffer(FINE + w / 2 + SAFE, 8), ina_t)
                    stamp(blk, it["geom"].buffer(RULE + w / 2 + SAFE, 8), ~ina_t)
                else:
                    stamp(blk, it["geom"].buffer(RULE + w / 2 + SAFE, 8))
            hs, ht = self.holes
            for k in ht.query(wb):
                it = hs[k]
                if it["uuid"] in ignore or (net and it["net"] == net):
                    continue
                stamp(blk, it["hole"].buffer(HOLE + w / 2 + SAFE, 8))
            m = EDGE + w / 2 + SAFE
            blk |= (X < ex0 + m) | (X > ex1 - m) | (Y < ey0 + m) | (Y > ey1 - m)
            free[l] = ~blk | ow
            own[l] = ow
        vs, vd = via
        vb = np.zeros((ny, nx), bool)
        done = set()
        for l in ALLCU:
            its, tree = self.by_layer[l]
            for k in tree.query(wb):
                it = its[k]
                if it["uuid"] in ignore or it["uuid"] in done or (net and it["net"] == net):
                    continue
                done.add(it["uuid"])
                if it["in_area"]:
                    stamp(vb, it["geom"].buffer(max(vs / 2 + FINE, vd / 2 + HOLE) + SAFE, 8), ina_v)
                    stamp(vb, it["geom"].buffer(max(vs / 2 + RULE, vd / 2 + HOLE) + SAFE, 8), ~ina_v)
                else:
                    stamp(vb, it["geom"].buffer(max(vs / 2 + RULE, vd / 2 + HOLE) + SAFE, 8))
        hs, ht = self.holes
        for k in ht.query(wb):
            it = hs[k]
            if it["uuid"] in ignore:
                continue
            r = vd / 2 + H2H if (net and it["net"] == net) else max(vd / 2 + H2H, vs / 2 + HOLE)
            stamp(vb, it["hole"].buffer(r + SAFE, 8))
        m = EDGE + vs / 2 + SAFE
        vb |= (X < ex0 + m) | (X > ex1 - m) | (Y < ey0 + m) | (Y > ey1 - m)
        return dict(X=X, Y=Y, g=g, win=win, nx=nx, ny=ny, free=free, own=own, via_ok=~vb)

    # ------------------------------------------------------------ search
    def route(self, net, src, dst, win, w=0.1, layers=("F",), g=0.025, via=(0.5, 0.3), ignore=frozenset(),
              allow_via=None, forbid=None, verbose=True):
        """src/dst: [(layer, shapely geom)]. Start cells are free cells inside src geoms,
        goal cells free cells inside dst geoms. Returns (segments, vias) or None."""
        M = self.masks(net, win, g, w, ignore, via)
        ny, nx = M["ny"], M["nx"]
        n = nx * ny
        fb = np.zeros((ny, nx), np.uint8)
        gb = np.zeros((ny, nx), np.uint8)
        for k, l in enumerate(LAYERS):
            if l in layers:
                f = M["free"][l].copy()
                if forbid is not None and l in forbid:
                    f &= ~shapely.intersects_xy(forbid[l], M["X"], M["Y"]) | M["own"][l]
                M["free"][l] = f
                fb |= f.astype(np.uint8) << k
        starts = []
        for l, geom in src:
            if l in layers:
                k = LAYERS.index(l)
                m = shapely.intersects_xy(geom, M["X"], M["Y"]) & M["free"][l]
                ii, jj = np.nonzero(m)
                starts += list(k * n + ii * nx + jj)
        for l, geom in dst:
            if l in layers:
                k = LAYERS.index(l)
                m = shapely.intersects_xy(geom, M["X"], M["Y"]) & M["free"][l]
                gb |= m.astype(np.uint8) << k
        via_ok = M["via_ok"].copy() if len(layers) > 1 else np.zeros((ny, nx), bool)
        if allow_via is not None:
            via_ok &= shapely.intersects_xy(allow_via, M["X"], M["Y"])
        if not starts or not gb.any():
            if verbose:
                print("   route %s: no start (%d) or goal (%d) cells" % (net, len(starts), int(gb.any())))
            return None
        hs = (distance_transform_edt(gb == 0) * 10.0).astype(np.float32)
        fin, fout = os.path.join(SCR, "gr_in.bin"), os.path.join(SCR, "gr_out.bin")
        seeds = np.array(sorted(set(int(s) for s in starts)), dtype=np.uint32)
        with open(fin, "wb") as fh:
            fh.write(struct.pack("III", ny, nx, len(seeds)))
            seeds.tofile(fh)
            fb.tofile(fh)
            via_ok.astype(np.uint8).tofile(fh)
            gb.tofile(fh)
            hs.tofile(fh)
        subprocess.run([GRE, fin, fout], check=True, capture_output=True)
        with open(fout, "rb") as fh:
            cnt = struct.unpack("I", fh.read(4))[0]
            ids = np.fromfile(fh, dtype=np.uint32, count=cnt)
        if not cnt:
            if verbose:
                print("   route %s: no path" % net)
            return None
        x0, y0 = win[0], win[1]
        path = [(LAYERS[int(v) // n], x0 + (int(v) % n % nx) * g, y0 + (int(v) % n // nx) * g) for v in ids]
        return self.simplify(net, path, w, via, ignore)

    def simplify(self, net, path, w, via, ignore):
        runs, cur = [], [path[0]]
        for p in path[1:]:
            if p[0] != cur[-1][0]:
                runs.append(cur)
                cur = [p]
            else:
                cur.append(p)
        runs.append(cur)
        segs, vias = [], []
        for r in runs:
            l = r[0][0]
            pts = [(round(x, 4), round(y, 4)) for _, x, y in r]
            # collinear collapse first
            cc = [pts[0]]
            for k in range(1, len(pts) - 1):
                a, b, c = cc[-1], pts[k], pts[k + 1]
                if abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) > 1e-9:
                    cc.append(b)
            if len(pts) > 1:
                cc.append(pts[-1])
            # line of sight
            keep, i = [cc[0]], 0
            while i < len(cc) - 1:
                j = i + 1
                while j + 1 < len(cc) and not self.check_track(net, l, cc[i], cc[j + 1], w, ignore):
                    j += 1
                keep.append(cc[j])
                i = j
            segs += [(l, a, c) for a, c in zip(keep, keep[1:]) if a != c]
        for a, b in zip(path, path[1:]):
            if a[0] != b[0]:
                vias.append((round(b[1], 4), round(b[2], 4)))
        return segs, vias

    # ------------------------------------------------------------ edits
    def verify(self, net, segs, vias, w, via=(0.5, 0.3), ignore=frozenset()):
        bad = []
        for l, a, c in segs:
            bad += [("seg", l, a, c) + x for x in self.check_track(net, l, a, c, w, ignore)]
        for x, y in vias:
            bad += [("via", x, y) + b for b in self.check_via(net, x, y, via[0], via[1], ignore)]
        return bad

    def add(self, net, segs, vias, w, via=(0.5, 0.3)):
        ni = self.b.FindNet(net)
        for l, a, c in segs:
            t = K.PCB_TRACK(self.b)
            t.SetStart(V(*a))
            t.SetEnd(V(*c))
            t.SetWidth(K.FromMM(w))
            t.SetLayer(LID[l])
            t.SetNet(ni)
            self.b.Add(t)
        for x, y in vias:
            v = K.PCB_VIA(self.b)
            v.SetPosition(V(x, y))
            v.SetViaType(K.VIATYPE_THROUGH)
            v.SetLayerPair(K.F_Cu, K.B_Cu)
            v.SetWidth(K.FromMM(via[0]))
            v.SetDrill(K.FromMM(via[1]))
            v.SetNet(ni)
            self.b.Add(v)

    def remove(self, uuids):
        uuids = set(uuids)
        n = 0
        for t in list(self.b.GetTracks()):
            if t.m_Uuid.AsString() in uuids:
                self.b.Delete(t)
                n += 1
        return n

    def save(self, path=None):
        self.b.Save(path or self.path)


PRO_REF = None     # a known-good rev3.kicad_pro; drc() puts it back before every run


def drc(path=PCB, tag="drc", parity=False):
    # pcbnew's BOARD.Save() can rewrite the project next to the board with default
    # rules, and a DRC run against those reports hundreds of false violations.
    # kicad-cli reads the project and custom rules named after the BOARD file, so a
    # stage saved as stageN.kicad_pcb needs stageN.kicad_pro / .kicad_dru beside it.
    if PRO_REF:
        import shutil
        base = os.path.splitext(os.path.basename(path))[0]
        dru_ref = os.path.splitext(PRO_REF)[0] + ".kicad_dru"
        for ref, ext in ((PRO_REF, ".kicad_pro"), (dru_ref, ".kicad_dru")):
            if not os.path.exists(ref):
                continue
            for name in {base, "rev3"}:
                dst = os.path.join(os.path.dirname(path), name + ext)
                if not os.path.exists(dst) or open(dst, "rb").read() != open(ref, "rb").read():
                    shutil.copyfile(ref, dst)
    out = os.path.join(SCR, tag + ".json")
    args = [KICLI, "pcb", "drc", "--format", "json", "--severity-all", "--all-track-errors", "--refill-zones", "-o", out]
    if parity:
        args.append("--schematic-parity")
    subprocess.run(args + [path], capture_output=True)
    d = json.load(open(out, encoding="utf-8"))
    c = collections.Counter(v["type"] for v in d["violations"])
    return d, c


def summary(d, c):
    print("DRC unconnected %d | %s" % (len(d["unconnected_items"]), dict(c)))
    for v in d["unconnected_items"]:
        print("   open:", " <-> ".join("%s @(%.2f,%.2f)" % (i["description"][:40], i["pos"]["x"], i["pos"]["y"]) for i in v["items"]))
    for v in d["violations"]:
        if v["type"] not in ("silk_over_copper",):
            print("   %s:" % v["type"], " | ".join("%s @(%.2f,%.2f)" % (i["description"][:40], i["pos"]["x"], i["pos"]["y"]) for i in v["items"]))
