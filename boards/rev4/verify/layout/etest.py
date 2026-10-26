"""etest.py [gerbers] [netlist] [board] - an electrical test of the Gerber and drill files themselves.

    python3 etest.py ../../fab/gerbers ../../rev4.net ../../rev4.kicad_pcb

A bare-board e-test, done on the files the fab gets rather than on the board.
gerbv, not KiCad, renders each copper layer and both drill files at 10 um a
pixel. Copper that touches on a layer is one piece; a plated hole joins the
pieces it passes through on every layer; an unplated hole cuts its copper away.
Every pad in the board's IPC-D-356 test list (kicad-cli writes it; it is used
here only for each pad's position and side) takes the piece under its centre,
and its net comes from the netlist (gen_rev4.py's rev4.net), not from the board.
Then:

    open    a net whose pads lie on more than one piece
    short   a piece that carries pads of more than one net; the bridged ADDR
            jumpers JP1-JP3 tie ADDR0..2 to GND by design and are allowed
    stray   copper that no pad reaches, with its area

A pad on no net (a fiducial, an unused pin) is a net of its own, so copper
joining it to anything else is a short too.

and the drill hits per tool against the board's vias and holes.

Needs gerbv (apt install gerbv), numpy, scipy and Pillow; kicad-cli for the
test list. Exit 0 only if there is no open, no short and no stray copper.
"""
import collections
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
REV4 = os.path.normpath(os.path.join(HERE, "..", ".."))
PX = 100                          # pixels a millimetre: 10 um
COPPER = ("F_Cu", "GND", "PWR", "B_Cu")     # top to bottom; In1 and In2 carry their layer names
TIES = {frozenset({"GND", "ADDR0", "ADDR1", "ADDR2"})}


def coords(path):
    """the X / Y of every coordinate in a Gerber file with format 4.6, mm"""
    s = open(path, encoding="utf-8").read()
    return [(int(x) / 1e6, int(y) / 1e6) for x, y in re.findall(r"^X(-?\d+)Y(-?\d+)D0[12]", s, re.M)]


def render(path, x0, y0, w, h, out):
    """gerbv's own rendering of one file, white on black, 10 um a pixel, no antialiasing"""
    r = subprocess.run(["gerbv", "-x", "png", "-D", str(int(PX * 25.4)), "-O", "%.6f;%.6f" % (x0 / 25.4, y0 / 25.4),
                        "-W", "%.6fx%.6f" % (w / 25.4, h / 25.4), "-B", "0", "-b", "#000000", "-f", "#FFFFFFFF",
                        "-o", out, path], capture_output=True, text=True)
    if not os.path.exists(out):
        sys.exit("gerbv failed on %s: %s" % (path, r.stderr[-300:]))
    return np.asarray(Image.open(out).convert("L")) > 127


def netlist(path):
    s = open(path, encoding="utf-8").read()
    net_of = {}
    for chunk in s.split("(net (code")[1:]:
        name = re.search(r'\(name "([^"]*)"\)', chunk).group(1).lstrip("/")
        for ref, pin in re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', chunk):
            net_of[(ref, pin)] = name
    return net_of


def test_list(board, tmp):
    """(ref, pin, x, y, sides) for every pad of the IPC-D-356 list; Gerber coordinates, mm"""
    out = os.path.join(tmp, "board.ipc")
    subprocess.run([shutil.which("kicad-cli") or "kicad-cli", "pcb", "export", "ipcd356", "-o", out, board],
                   capture_output=True, text=True)
    pads = []
    for line in open(out, encoding="utf-8"):
        if not line.startswith(("317", "327")) or line[20:26].strip() == "VIA":
            continue
        m = re.search(r"A(\d\d)X([+-]\d+)Y([+-]\d+)", line)
        side = {"01": "F", "02": "B"}.get(m.group(1), "FB")
        x, y = int(m.group(2)) * 0.00254, int(m.group(3)) * 0.00254     # 0.0001 inch
        pads.append((line[20:26].strip(), line[27:31].strip(), x, y, side))
    return pads


def main():
    gerbers = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REV4, "fab", "gerbers")
    net_of = netlist(sys.argv[2] if len(sys.argv) > 2 else os.path.join(REV4, "rev4.net"))
    board = sys.argv[3] if len(sys.argv) > 3 else os.path.join(REV4, "rev4.kicad_pcb")
    name = lambda f: os.path.join(gerbers, "rev4-%s" % f)
    tmp = tempfile.mkdtemp()

    edge = coords(name("Edge_Cuts.gbr"))
    x0, x1 = min(p[0] for p in edge) - 1.0, max(p[0] for p in edge) + 1.0
    y0, y1 = min(p[1] for p in edge) - 1.0, max(p[1] for p in edge) + 1.0
    w, h = round(x1 - x0), round(y1 - y0)
    to_px = lambda x, y: (int(round((y0 + h - y) * PX)), int(round((x - x0) * PX)))     # row, column

    pth = render(name("PTH.drl"), x0, y0, w, h, os.path.join(tmp, "pth.png"))
    npth = render(name("NPTH.drl"), x0, y0, w, h, os.path.join(tmp, "npth.png"))
    four = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    labels, counts, area = {}, {}, {}
    for layer in COPPER:
        cu = render(name("%s.gbr" % layer), x0, y0, w, h, os.path.join(tmp, layer + ".png")) & ~npth
        labels[layer], counts[layer] = ndimage.label(cu, structure=four)
        area[layer] = np.bincount(labels[layer].ravel())

    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for layer in COPPER:
        for i in range(1, counts[layer] + 1):
            find((layer, i))
    holes, nholes = ndimage.label(pth, structure=four)
    for k, sl in enumerate(ndimage.find_objects(holes), start=1):
        inside = holes[sl] == k
        pieces = [(layer, int(i)) for layer in COPPER for i in np.unique(labels[layer][sl][inside]) if i]
        for p in pieces[1:]:
            parent[find(p)] = find(pieces[0])

    # each pad: the piece under its centre, on the side it is reached from
    piece_nets = collections.defaultdict(set)
    piece_pads = collections.defaultdict(list)
    net_pieces = collections.defaultdict(set)
    missed, unknown = [], []
    pads = test_list(board, tmp)
    for ref, pin, x, y, side in pads:
        r, c = to_px(x, y)
        layers = ["F_Cu"] if side == "F" else ["B_Cu"] if side == "B" else list(COPPER)
        hit = [(layer, int(labels[layer][r, c])) for layer in layers if labels[layer][r, c]]
        if not hit:
            missed.append("%s.%s at (%.3f, %.3f)" % (ref, pin, x, -y))
            continue
        root = find(hit[0])
        net = net_of.get((ref, pin))
        if net is None:                 # a pin on no net (NC, a fiducial): its own, so a short still shows
            unknown.append("%s.%s" % (ref, pin))
            net = "(no net) %s.%s" % (ref, pin)
        piece_nets[root].add(net)
        piece_pads[root].append("%s.%s" % (ref, pin))
        net_pieces[net].add(root)

    opens = {n: len(p) for n, p in net_pieces.items() if len(p) > 1}
    shorts = [sorted(n) for root, n in piece_nets.items() if len(n) > 1 and frozenset(n) not in TIES]
    ties = [sorted(n) for root, n in piece_nets.items() if frozenset(n) in TIES]
    stray = collections.Counter()
    for layer in COPPER:
        for i in range(1, counts[layer] + 1):
            if find((layer, i)) not in piece_nets:
                stray[layer] += area[layer][i] / PX ** 2
    tested = {n for n in net_pieces if not n.startswith("(no net)")}
    multi = collections.Counter(net_of.values())
    untested = sorted(n for n, k in multi.items() if k > 1 and n not in tested)

    hits = collections.Counter()
    tool = None
    for line in open(name("PTH.drl"), encoding="utf-8"):
        if re.fullmatch(r"T\d+C[\d.]+\s*", line):
            continue
        m = re.fullmatch(r"(T\d+)\s*", line)
        if m:
            tool = m.group(1)
        elif line.startswith("X"):
            hits[(tool, "slot" if "G85" in line else "hole")] += 1
    sizes = dict(re.findall(r"^(T\d+)C([\d.]+)", open(name("PTH.drl"), encoding="utf-8").read(), re.M))

    print("%s: gerbv at %d um a pixel, %d x %d mm; %d plated holes and slots, %d unplated" % (
        gerbers, 1000 // PX, w, h, nholes, ndimage.label(npth)[1]))
    print("  copper pieces per layer: %s" % ", ".join("%s %d" % (l, counts[l]) for l in COPPER))
    print("  pads tested: %d of the IPC-D-356 list, %d of them on no net (%s); %d not on copper at their centre" % (
        len(pads) - len(missed), len(unknown), " ".join(unknown), len(missed)))
    for x in missed[:10]:
        print("      " + x)
    print("  nets tested: %d; nets of two or more pins not reached: %s" % (len(tested), untested or "none"))
    print("  opens:  %d %s" % (len(opens), opens or ""))
    print("  shorts: %d %s" % (len(shorts), shorts[:10] or ""))
    print("  by design: %s (JP1-JP3 bridged)" % ties)
    print("  stray copper no pad reaches: %s" % ({l: round(a, 3) for l, a in stray.items()} or "none"))
    print("  PTH drill hits: %s" % ", ".join("%s %s mm: %d %s" % (t, sizes.get(t, "?"), n, k) for (t, k), n in sorted(hits.items())))
    shutil.rmtree(tmp)
    ok = not opens and not shorts and not stray and not missed and not untested
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
