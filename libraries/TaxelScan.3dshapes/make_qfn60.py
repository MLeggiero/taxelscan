"""make_qfn60.py [out.step] - 3D model of the RP2350A / RP2354A QFN-60 package.

KiCad's footprint QFN-60-1EP_7x7mm_P0.4mm_EP3.4x3.4mm names a model that KiCad
does not ship. This one is drawn from the package drawing in the RP2350
datasheet (section 14.1, Fig. 147), at nominal sizes: a 7 x 7 mm body 0.85 mm
high, standing 0.02 mm off the seating plane; 60 leads 0.18 mm wide, 0.40 mm long
and 0.203 mm thick at 0.4 mm pitch, 15 a side; a 3.40 mm exposed pad with its
pin-1 corner cut; a laser dot over pin 1.

Every piece is a straight prism, written out as a closed B-rep, so the script
needs nothing beyond Python. The axes are KiCad's model axes: x right, y towards
the top of the footprint, z up from the seating plane. Pin 1 is the top lead on
the left side, over pad 1 at footprint (-3.45, -2.8). Each lead reaches 0.01 mm
past the body so its end face never coincides with the body's side.
"""
import math
import os
import sys

D, A, A1, A3 = 7.0, 0.85, 0.02, 0.203          # body, height, standoff, lead thickness
B, L, E, N = 0.18, 0.40, 0.40, 15                # lead width and length, pitch, leads a side
D2, CUT = 3.40, 0.30                             # exposed pad, its pin-1 chamfer
DOT, DOT_AT = 0.25, 0.75                         # dot radius, its centre in from each edge
BLACK, METAL, MARK = (0.148, 0.145, 0.145), (0.824, 0.820, 0.781), (0.85, 0.85, 0.85)
NAME = "QFN-60-1EP_7x7mm_P0.4mm_EP3.4x3.4mm"


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def parts():
    """(name, convex polygon counter-clockwise from above, z0, z1, colour) for every solid."""
    h, p, out = D / 2, D2 / 2, []
    out.append(("body", rect(-h, -h, h, h), A1, A, BLACK))
    out.append(("epad", [(-p, -p), (p, -p), (p, p), (-p + CUT, p), (-p, p - CUT)], 0.0, A3, METAL))
    tip, heel = h + 0.01, h - L
    for k in range(N):
        c = (N - 1) / 2 * E - k * E                  # pin 1 first: left side, top to bottom
        out.append(("pin%d" % (k + 1), rect(-tip, c - B / 2, -heel, c + B / 2), 0.0, A3, METAL))
        out.append(("pin%d" % (k + 1 + N), rect(-c - B / 2, -tip, -c + B / 2, -heel), 0.0, A3, METAL))
        out.append(("pin%d" % (k + 1 + 2 * N), rect(heel, -c - B / 2, tip, -c + B / 2), 0.0, A3, METAL))
        out.append(("pin%d" % (k + 1 + 3 * N), rect(c - B / 2, heel, c + B / 2, tip), 0.0, A3, METAL))
    cx, cy = -h + DOT_AT, h - DOT_AT
    out.append(("pin1_mark", [(cx + DOT * math.cos(math.pi * i / 8), cy + DOT * math.sin(math.pi * i / 8))
                              for i in range(16)], A, A + 0.005, MARK))
    return out


def num(v):
    return "0." if abs(v) < 5e-10 else ("%.9f" % v).rstrip("0")


class Step:
    def __init__(self):
        self.rows, self.dirs, self.styles = [], {}, {}

    def add(self, text):
        self.rows.append(text)
        return "#%d" % len(self.rows)

    def point(self, p):
        return self.add("CARTESIAN_POINT('',(%s))" % ",".join(map(num, p)))

    def direction(self, d):
        key = tuple(round(c, 9) + 0.0 for c in d)
        if key not in self.dirs:
            self.dirs[key] = self.add("DIRECTION('',(%s))" % ",".join(map(num, key)))
        return self.dirs[key]

    def prism(self, name, poly, z0, z1):
        """A closed shell: bottom, top and one side face per polygon edge; every edge used twice, once each way."""
        n = len(poly)
        xyz = [(x, y, z0) for x, y in poly] + [(x, y, z1) for x, y in poly]
        pts = [self.point(q) for q in xyz]
        vtx = [self.add("VERTEX_POINT('',%s)" % q) for q in pts]

        def edge(a, b):
            d = [xyz[b][i] - xyz[a][i] for i in range(3)]
            length = math.sqrt(sum(c * c for c in d))
            vec = self.add("VECTOR('',%s,%s)" % (self.direction([c / length for c in d]), num(length)))
            return self.add("EDGE_CURVE('',%s,%s,%s,.T.)" % (vtx[a], vtx[b], self.add("LINE('',%s,%s)" % (pts[a], vec))))

        bottom = [edge(i, (i + 1) % n) for i in range(n)]
        top = [edge(n + i, n + (i + 1) % n) for i in range(n)]
        side = [edge(i, n + i) for i in range(n)]

        def face(loop, origin, normal, ref):
            oes = ",".join(self.add("ORIENTED_EDGE('',*,*,%s,%s)" % (e, ".T." if fwd else ".F.")) for e, fwd in loop)
            bound = self.add("FACE_OUTER_BOUND('',%s,.T.)" % self.add("EDGE_LOOP('',(%s))" % oes))
            ax = self.add("AXIS2_PLACEMENT_3D('',%s,%s,%s)" % (origin, self.direction(normal), self.direction(ref)))
            return self.add("ADVANCED_FACE('',(%s),%s,.T.)" % (bound, self.add("PLANE('',%s)" % ax)))

        faces = [face([(bottom[i], False) for i in reversed(range(n))], pts[0], (0, 0, -1), (1, 0, 0)),
                 face([(top[i], True) for i in range(n)], pts[n], (0, 0, 1), (1, 0, 0))]
        for i in range(n):
            j = (i + 1) % n
            dx, dy = poly[j][0] - poly[i][0], poly[j][1] - poly[i][1]
            s = math.hypot(dx, dy)
            faces.append(face([(bottom[i], True), (side[j], True), (top[i], False), (side[i], False)],
                              pts[i], (dy / s, -dx / s, 0), (dx / s, dy / s, 0)))
        return self.add("MANIFOLD_SOLID_BREP('%s',%s)" % (name, self.add("CLOSED_SHELL('',(%s))" % ",".join(faces))))

    def styled(self, solid, rgb):
        if rgb not in self.styles:
            c = self.add("COLOUR_RGB('',%s)" % ",".join(map(num, rgb)))
            fill = self.add("FILL_AREA_STYLE('',(%s))" % self.add("FILL_AREA_STYLE_COLOUR('',%s)" % c))
            side = self.add("SURFACE_SIDE_STYLE('',(%s))" % self.add("SURFACE_STYLE_FILL_AREA(%s)" % fill))
            self.styles[rgb] = self.add("PRESENTATION_STYLE_ASSIGNMENT((%s))" % self.add("SURFACE_STYLE_USAGE(.BOTH.,%s)" % side))
        return self.add("STYLED_ITEM('color',(%s),%s)" % (self.styles[rgb], solid))


def write(path):
    s = Step()
    app = s.add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
    s.add("APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,%s)" % app)
    product = s.add("PRODUCT('%s','%s','',(%s))" % (NAME, NAME, s.add("PRODUCT_CONTEXT('',%s,'mechanical')" % app)))
    s.add("PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(%s))" % product)
    pdef = s.add("PRODUCT_DEFINITION('design','',%s,%s)" % (s.add("PRODUCT_DEFINITION_FORMATION('','',%s)" % product),
                                                           s.add("PRODUCT_DEFINITION_CONTEXT('part definition',%s,'design')" % app)))
    mm = s.add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
    rad = s.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    sr = s.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
    unc = s.add("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),%s,'distance_accuracy_value','confusion accuracy')" % mm)
    ctx = s.add("( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((%s)) "
                "GLOBAL_UNIT_ASSIGNED_CONTEXT((%s,%s,%s)) REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY') )"
                % (unc, mm, rad, sr))
    origin = s.add("AXIS2_PLACEMENT_3D('',%s,%s,%s)" % (s.point((0, 0, 0)), s.direction((0, 0, 1)), s.direction((1, 0, 0))))
    solids = [(s.prism(name, poly, z0, z1), rgb) for name, poly, z0, z1, rgb in parts()]
    rep = s.add("ADVANCED_BREP_SHAPE_REPRESENTATION('',(%s),%s)" % (",".join([origin] + [x for x, _ in solids]), ctx))
    s.add("SHAPE_DEFINITION_REPRESENTATION(%s,%s)" % (s.add("PRODUCT_DEFINITION_SHAPE('','',%s)" % pdef), rep))
    s.add("MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',(%s),%s)"
          % (",".join(s.styled(x, rgb) for x, rgb in solids), ctx))
    with open(path, "w", newline="\n") as f:
        f.write("ISO-10303-21;\nHEADER;\n"
                "FILE_DESCRIPTION(('QFN-60 7 x 7 mm, 0.4 mm pitch, 3.4 mm exposed pad: RP2350A / RP2354A'),'2;1');\n"
                "FILE_NAME('%s.step','2026-10-17T00:00:00',(''),(''),'make_qfn60.py','','');\n"
                "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n" % NAME)
        f.writelines("#%d=%s;\n" % (i + 1, row) for i, row in enumerate(s.rows))
        f.write("ENDSEC;\nEND-ISO-10303-21;\n")
    return len(solids), len(s.rows)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), NAME + ".step")
    print("%s: %d solids, %d entities" % (out, *write(out)))
