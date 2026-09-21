#!/usr/bin/env python3
"""Board operations that only KiCad's own Python module can do.

Run with KiCad's interpreter, NOT the system one:

    "C:/Program Files/KiCad/10.0/bin/python.exe" kicad_tools.py <command>

    fill            fill every zone and save - the board file has carried zone
                    OUTLINES with no computed copper this whole time
    dsn  <out.dsn>  export Specctra DSN for freerouting
    ses  <in.ses>   import a Specctra session and save
    stats           connectivity summary: ratsnest still outstanding, per net

WHY. `kicad-cli` has no DSN export, no SES import, and no way to persist a zone
fill - it can only refill in memory for the duration of a DRC run. Everything
in this repo up to now has therefore been written by text surgery, which works
for placement and for tracks but cannot compute a polygon fill and cannot talk
Specctra. The pcbnew module can do all three, so the round trip to freerouting
and back no longer needs the GUI.
"""
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "rev3.kicad_pcb")


def load():
    return pcbnew.LoadBoard(BOARD)


def cmd_fill():
    bd = load()
    zones = list(bd.Zones())
    print("zones: %d" % len(zones))
    filler = pcbnew.ZONE_FILLER(bd)
    filler.Fill(bd.Zones())
    for z in zones:
        n = z.GetNetname()
        area = sum(z.GetFilledPolysList(l).Area()
                   for l in z.GetLayerSet().Seq())
        print("  %-8s on %-8s filled area %.1f mm2"
              % (n, bd.GetLayerName(z.GetLayerSet().Seq()[0]),
                 area / 1e12))
    bd.Save(BOARD)
    print("saved with fills")


def cmd_dsn(out):
    bd = load()
    ok = pcbnew.ExportSpecctraDSN(bd, out)
    print("DSN -> %s  %s" % (out, "ok" if ok else "FAILED"))
    return 0 if ok else 1


def cmd_ses(path):
    bd = load()
    ok = pcbnew.ImportSpecctraSES(bd, path)
    if ok:
        bd.Save(BOARD)
    print("SES <- %s  %s" % (path, "imported and saved" if ok else "FAILED"))
    return 0 if ok else 1


def cmd_stats():
    bd = load()
    filler = pcbnew.ZONE_FILLER(bd)
    filler.Fill(bd.Zones())
    # BuildConnectivity() before asking, or the answer is computed from
    # whatever connectivity the file was saved with - which after a fill or an
    # imported session is stale. Without it this reported 46 where kicad-cli
    # DRC said 168, and the low number was quoted as progress for several
    # rounds. GetUnconnectedCount(True) counts only VISIBLE layers, which is a
    # second way to get a comfortable wrong answer; pass False.
    bd.BuildConnectivity()
    conn = bd.GetConnectivity()
    conn.RecalculateRatsnest()
    total = conn.GetUnconnectedCount(False)   # False = ALL, not just visible layers
    print("unconnected ratsnest edges: %d" % total)
    per = {}
    for i in range(bd.GetNetInfo().GetNetCount()):
        code = i
        name = bd.GetNetInfo().GetNetItem(code).GetNetname()
        if not name:
            continue
        n = conn.GetRatsnestForNet(code)
        if n:
            try:
                cnt = len(n.GetUnconnected())
            except Exception:
                cnt = 0
            if cnt:
                per[name] = cnt
    for k, v in sorted(per.items(), key=lambda kv: -kv[1]):
        print("   %-16s %d" % (k, v))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    c = sys.argv[1]
    if c == "fill":
        sys.exit(cmd_fill() or 0)
    if c == "dsn":
        sys.exit(cmd_dsn(sys.argv[2]))
    if c == "ses":
        sys.exit(cmd_ses(sys.argv[2]))
    if c == "stats":
        sys.exit(cmd_stats())
    sys.exit("unknown command: " + c)
