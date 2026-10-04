"""ses_import.py base.kicad_pcb session.ses out.kicad_pcb [--fixed NET,...] - bring freerouting's result back.

The base is the board the DSN was exported from (signal copper stripped, the
GND / +3.3 V plane ties kept). pcbnew's session import replaces every track and
via with the session's, so the plane ties must come back with it. With a DSN
from dsn_prep.py without --plane-keepouts freerouting was told they are
protected and returns them; with --plane-keepouts they were never in the
session, and every tie missing after the import is copied back from the base,
exactly. The same goes for the nets pre-routed before freerouting
(preroute_rev4.py, --fixed): their copper was keepouts in the DSN and comes
back from the base. Either way each one of them must be there, at the same
place, before the result is accepted. Then KiCad's DRC runs on the result (zones refilled,
the board's real rules from rev4.kicad_dru) and the routing is measured with
jag.py.
"""
import collections
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew as K
import jag

HERE = os.path.dirname(os.path.abspath(__file__))
REV4 = os.path.normpath(os.path.join(HERE, ".."))
PLANE_NETS = ["GND", "+3.3V"]


def plane_ties(b):
    return {tie_key(t) for t in b.GetTracks() if t.GetNetname() in PLANE_NETS}


def tie_key(t):
    if t.Type() == K.PCB_VIA_T:
        p = t.GetPosition()
        return ("via", t.GetNetname(), p.x // 1000, p.y // 1000)
    a, c = sorted([(t.GetStart().x // 1000, t.GetStart().y // 1000), (t.GetEnd().x // 1000, t.GetEnd().y // 1000)])
    return ("trk", t.GetNetname(), a, c, t.GetLayer())


def restore_placement(base, b):
    """pcbnew's session import also applies the session's placement, which the
    DSN carries to 0.1 um: every footprint comes back up to ~70 nm off. Put each
    one back exactly where the base board has it."""
    where = {f.GetReference(): (f.GetPosition(), f.GetOrientation()) for f in base.GetFootprints()}
    n = 0
    for f in b.GetFootprints():
        p, o = where[f.GetReference()]
        if f.GetPosition() != p or f.GetOrientation() != o:
            f.SetPosition(p)
            f.SetOrientation(o)
            n += 1
    return n


def restore_ties(base, b, missing):
    """Copy the base board's plane ties named in `missing` into b."""
    n = 0
    for t in base.GetTracks():
        if t.GetNetname() in PLANE_NETS and tie_key(t) in missing:
            c = t.Duplicate()
            c.SetNet(b.FindNet(t.GetNetname()))
            b.Add(c)
            n += 1
    return n


def drc(path, tag):
    base = os.path.splitext(path)[0]
    for ext in (".kicad_pro", ".kicad_dru"):
        shutil.copyfile(os.path.join(REV4, "rev4" + ext), base + ext)
    out = base + "." + tag + ".json"
    cli = shutil.which("kicad-cli") or "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
    subprocess.run([cli, "pcb", "drc", "--refill-zones", "--severity-all", "--all-track-errors",
                    "--format", "json", "-o", out, path], capture_output=True)
    return json.load(open(out, encoding="utf-8"))


def summary(d, show=12):
    kinds = collections.Counter(v["type"] for v in d["violations"])
    print("DRC: unconnected %d | %s" % (len(d["unconnected_items"]), dict(kinds)))
    nets = collections.Counter()
    for v in d["unconnected_items"]:
        for it in v["items"][:1]:
            desc = it["description"]
            nets[desc[desc.find("[") + 1:desc.find("]")] if "[" in desc else desc] += 1
    if nets:
        print("   unconnected by net:", dict(nets.most_common(40)))
    for v in [v for v in d["violations"] if v["type"] not in ("silk_overlap", "silk_over_copper")][:show]:
        print("   %s: %s" % (v["type"], " | ".join("%s @(%.2f,%.2f)" % (i["description"][:44], i["pos"]["x"], i["pos"]["y"])
                                                for i in v["items"])))


def main(base, ses, out):
    b = K.LoadBoard(base)
    before = plane_ties(b)
    if not K.ImportSpecctraSES(b, ses):
        sys.exit("session import failed")
    print("placement: %d footprint(s) put back exactly" % restore_placement(K.LoadBoard(base), b))
    after = plane_ties(b)
    extra = after - before
    if before - after:
        n = restore_ties(K.LoadBoard(base), b, before - after)
        print("plane ties: %d not in the session, copied back from the base" % n)
        after = plane_ties(b)
    lost = before - after
    print("plane ties: %d before, %d after, %d lost, %d new" % (len(before), len(after), len(lost), len(extra)))
    for x in sorted(lost)[:10]:
        print("   lost", x)
    b.Save(out)
    d = drc(out, "drc")
    summary(d)
    tot = jag.table(K.LoadBoard(out))
    L = sum(r[0] for r in tot.values()); off = sum(r[1] for r in tot.values())
    tiny = sum(r[2] for r in tot.values()); n = sum(r[3] for r in tot.values())
    vias = sum(1 for t in K.LoadBoard(out).GetTracks() if t.Type() == K.PCB_VIA_T)
    print("routing: %d segments, %.0f mm, %d vias; off-45 %.0f mm (%.0f%%); %d segments < 0.1 mm"
          % (n, L, vias, off, 100 * off / max(L, 1e-9), tiny))
    return 0 if not lost else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for a in sys.argv[1:]:
        if a.startswith("--fixed="):
            PLANE_NETS += [n for n in a.split("=", 1)[1].split(",") if n]
    sys.exit(main(*args[:3]))
