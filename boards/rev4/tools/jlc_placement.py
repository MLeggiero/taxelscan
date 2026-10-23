"""jlc_placement.py [board] - JLCPCB's placement corrections, from each LCSC part's own footprint.

    python3 jlc_placement.py                            # ../rev4.kicad_pcb, writes ../jlc_placement.csv
    python3 jlc_placement.py --check ../fab/rev4-cpl.csv  # places each LCSC footprint as a CPL says

JLCPCB's placement preview, and its machines, put the LCSC part's own footprint
(the EasyEDA one) at the CPL's Mid X / Mid Y and turn it by the CPL's rotation.
That footprint's idea of 0 degrees and of its origin often differs from KiCad's:
a TSSOP with its pins along x instead of y, a SOT-23 turned over, a pin header
whose origin is its middle rather than pin 1. Rather than guess from a table of
package conventions, this fetches every LCSC part's footprint from EasyEDA's
component API (cached in <checkout>/tmp/easyeda), matches its pads to KiCad's
by pad number, and fits the rotation and origin offset that carry KiCad's
footprint frame onto it:

    LCSC pads  =  R(delta) . KiCad pads  +  t

so that a part KiCad places at P, turned by theta, is sent to JLCPCB as

    rotation  theta - delta
    position  P + R(theta) . o,     o = JLCPCB's origin in KiCad's footprint frame

Both frames are y-up, angles counter-clockwise, as KiCad's position file and
JLCPCB's CPL have them. delta is snapped to a multiple of 90 degrees (the fit
itself is reported), and the worst pad-centre distance after the fit says how
well the two footprints agree: land patterns differ a little between libraries,
so a few tenths of a millimetre is normal; a large value means the pads do not
correspond and the part needs a look by hand. Polarity follows the pad
numbers, which this board's diodes and LED share with their LCSC footprints
(pad 1 the cathode in both; the LCSC symbols say so).

Writes ../jlc_placement.csv - LCSC, KiCad footprint, LCSC package, pads
matched, rotation, offset x / y (KiCad footprint frame, mm), worst error -
which make_fab.py applies to the CPLs. Run it again whenever a part, its
footprint or its LCSC number changes.

--check reads a finished CPL, puts every LCSC footprint where and how it says,
and reports each part's worst pad-centre distance from KiCad's pads (by pad
number); exit 1 if any is over 0.35 mm.
"""
import csv
import json
import math
import os
import re
import sys
import time
import urllib.request

import pcbnew as K

HERE = os.path.dirname(os.path.abspath(__file__))
REV4 = os.path.normpath(os.path.join(HERE, ".."))
CACHE = os.path.normpath(os.path.join(REV4, "..", "..", "tmp", "easyeda"))
API = "https://easyeda.com/api/products/%s/components?version=6.4.19.5"
OUT = os.path.join(REV4, "jlc_placement.csv")
mm = K.ToMM


def refs_of(field):
    out = []
    for ref in field.replace(" ", "").split(","):
        m = re.fullmatch(r"([A-Z]+)(\d+)-[A-Z]*(\d+)", ref)
        if m:
            out += ["%s%d" % (m.group(1), i) for i in range(int(m.group(2)), int(m.group(3)) + 1)]
        elif ref:
            out.append(ref)
    return out


def fetch(lcsc):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, lcsc + ".json")
    if not os.path.exists(path):
        req = urllib.request.Request(API % lcsc, headers={"User-Agent": "taxelscan-jlc-placement"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        json.loads(data)                       # refuse anything that is not JSON
        with open(path, "wb") as fh:
            fh.write(data)
        time.sleep(0.3)
    return json.load(open(path, encoding="utf-8"))["result"]


def lcsc_pads(lcsc):
    """the LCSC footprint's pads by number, footprint-local, mm, y-up, and its package name"""
    fp = fetch(lcsc)["packageDetail"]["dataStr"]
    ox, oy = float(fp["head"]["x"]), float(fp["head"]["y"])
    pads = {}
    for s in fp["shape"]:
        if s.startswith("PAD~"):
            f = s.split("~")
            x, y = (float(f[2]) - ox) * 0.254, -(float(f[3]) - oy) * 0.254    # 10 mil units, y down
            # a USB-C pad named for both of its pins ("A1B12") stands for each
            names = re.findall(r"[AB]\d+", f[8]) if re.fullmatch(r"([AB]\d+){2,}", f[8]) else [f[8]]
            for n in names:
                pads.setdefault(n, []).append((x, y))
    return pads, fp["head"]["c_para"].get("package", "")


def rot(v, deg):
    a = math.radians(deg)
    return (v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a))


def fit(pk, pj):
    """delta (degrees, least squares), delta snapped to 90, translation t, worst pad error"""
    n = len(pk)
    ck = (sum(p[0] for p in pk) / n, sum(p[1] for p in pk) / n)
    cj = (sum(p[0] for p in pj) / n, sum(p[1] for p in pj) / n)
    sx = sy = 0.0
    for a, b in zip(pk, pj):
        ax, ay, bx, by = a[0] - ck[0], a[1] - ck[1], b[0] - cj[0], b[1] - cj[1]
        sx += ax * bx + ay * by
        sy += ax * by - ay * bx
    d = math.degrees(math.atan2(sy, sx)) if (sx or sy) else 0.0
    dq = (round(d / 90.0) * 90) % 360
    rk = [rot(p, dq) for p in pk]
    t = (sum(b[0] - a[0] for a, b in zip(rk, pj)) / n, sum(b[1] - a[1] for a, b in zip(rk, pj)) / n)
    err = max(math.dist((a[0] + t[0], a[1] + t[1]), b) for a, b in zip(rk, pj))
    return d, dq, t, err


def bom_lcsc():
    lcsc_of = {}
    for r in csv.DictReader(open(os.path.join(REV4, "BOM.csv"), encoding="utf-8")):
        if r.get("LCSC", "").strip():
            for ref in refs_of(r["Reference"]):
                lcsc_of[ref] = r["LCSC"].strip()
    return lcsc_of


def check(cpl, board):
    lcsc_of = bom_lcsc()
    b = K.LoadBoard(board)
    worst = []
    for row in csv.DictReader(open(cpl, encoding="utf-8")):
        ref = row["Designator"]
        f = b.FindFootprintByReference(ref)
        if f is None or ref not in lcsc_of:
            worst.append((99.0, ref))
            continue
        lp, _pkg = lcsc_pads(lcsc_of[ref])
        x, y, th = float(row["Mid X"]), float(row["Mid Y"]), float(row["Rotation"])
        kp = {}
        for p in f.Pads():
            if p.GetNumber():
                kp.setdefault(p.GetNumber(), []).append((mm(p.GetPosition().x), -mm(p.GetPosition().y)))
        d = 0.0
        for n, pts in lp.items():
            for q in pts if n in kp else ():
                w = rot(q, th)
                d = max(d, min(math.dist((x + w[0], y + w[1]), k) for k in kp[n]))
        worst.append((round(d, 3), ref))
    worst.sort(reverse=True)
    off = [w for w in worst if w[0] > 0.35]
    print("%s: %d parts; worst pad-centre distance of the LCSC footprint, placed as the CPL says, "
          "from KiCad's pads: %s" % (os.path.basename(cpl), len(worst), worst[:4]))
    for d, ref in off:
        print("  OFF ITS PADS: %s by %.2f mm" % (ref, d))
    return 1 if off else 0


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--check":
        return check(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else os.path.join(REV4, "rev4.kicad_pcb"))
    board = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REV4, "rev4.kicad_pcb")
    lcsc_of = bom_lcsc()
    b = K.LoadBoard(board)
    seen, bad = {}, []
    for f in sorted(b.GetFootprints(), key=lambda f: f.GetReference()):
        ref = f.GetReference()
        if ref not in lcsc_of:
            continue
        key = (lcsc_of[ref], f.GetFPID().GetLibItemName().wx_str())
        kp = {}
        for p in f.Pads():
            if p.GetNumber():
                q = p.GetFPRelativePosition()
                kp.setdefault(p.GetNumber(), []).append((mm(q.x), -mm(q.y)))
        lp, pkg = lcsc_pads(key[0])
        common = sorted(set(kp) & set(lp))
        mean = lambda v: (sum(x for x, _ in v) / len(v), sum(y for _, y in v) / len(v))
        pk, pj = [mean(kp[n]) for n in common], [mean(lp[n]) for n in common]
        if len(common) < 2:
            bad.append("%s %s: only %d pad(s) match by number" % (ref, key[0], len(common)))
            continue
        d, dq, t, err = fit(pk, pj)
        o = rot((-t[0], -t[1]), -dq)               # JLCPCB's origin, in KiCad's footprint frame
        row = (key[0], key[1], pkg, len(common), dq, round(o[0], 3) + 0.0, round(o[1], 3) + 0.0, round(err, 3))
        if key in seen and seen[key] != row:
            bad.append("%s: %s fits differently on another part" % (ref, key))
        seen[key] = row
        if err > 0.35 or abs(d - round(d / 90.0) * 90) > 2.0:
            bad.append("%s %s: pads do not correspond (fit %.1f deg, worst %.2f mm)" % (ref, key[0], d, err))
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["LCSC", "Footprint", "LCSC package", "Pads matched", "Rotation",
                    "Offset X", "Offset Y", "Worst error"])
        for row in sorted(seen.values(), key=lambda r: (r[1], r[0])):
            w.writerow(row)
    moved = [r for r in seen.values() if r[4] or math.hypot(r[5], r[6]) >= 0.05]
    print("%d LCSC footprint(s) checked against KiCad's; %d need a rotation or an offset:" % (len(seen), len(moved)))
    for r in sorted(moved, key=lambda r: r[1]):
        print("   %-10s %-44s rotation -%3d  offset (%6.3f, %6.3f) mm  worst %.2f mm" % (r[0], r[1][:44], r[4], r[5], r[6], r[7]))
    for m in bad:
        print("  CHECK BY HAND:", m)
    print("  -> %s" % OUT)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
