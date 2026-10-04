"""freeroute_rev4.py - re-route rev-4 with freerouting, end to end.

    python3 freeroute_rev4.py prep   src.kicad_pcb workdir
    python3 freeroute_rev4.py route  workdir --jar freerouting-2.4.1.jar [--java java] [--passes 40]
    python3 freeroute_rev4.py finish workdir
    python3 freeroute_rev4.py all    src.kicad_pcb workdir --jar ...

prep    every track and via comes off except GND's and +3.3 V's: the pad-to-via
        ties that join each SMD pad to its plane. preroute_rev4.py then routes
        what freerouting cannot be trusted with - USB_D, the RS-485 pairs, the
        ADC corner - and redraws the planes' ties octilinear around them. Then
        pcbnew's Specctra export, and dsn_prep.py --plane-keepouts
        --fid-keepouts --fixed makes it fit for freerouting.
        -> workdir/stripped.kicad_pcb, base.kicad_pcb, base.dsn, route.dsn
route   freerouting, headless: up to --passes routing passes, then its
        optimiser (3 threads); fan-out and automatic neck-down off (neck-down
        took tracks to 0.075 mm, under the board's 0.10 mm minimum).
        -> workdir/route.ses, route.log
finish  ses_import.py (the session onto base.kicad_pcb, plane ties and fixed
        nets put back and checked), then fr_finish.py (copper violations off,
        everything open joined with rr3's octilinear rip-up and reroute,
        pruned, the 0.10 mm tracks widened where there is room, DRC).
        -> workdir/imported.kicad_pcb, final.kicad_pcb

finalize_rev4.py workdir/final.kicad_pcb then installs and checks the result.
"""
import argparse
import collections
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PLANE = ("GND", "+3.3V")


def run(args, log=None):
    print("$", " ".join(os.path.basename(a) if i == 1 else a for i, a in enumerate(args)), flush=True)
    if log:
        with open(log, "w") as fh:
            r = subprocess.run(args, stdout=fh, stderr=subprocess.STDOUT)
    else:
        r = subprocess.run(args)
    return r.returncode


def prep(src, wd):
    import pcbnew as K
    os.makedirs(wd, exist_ok=True)
    b = K.LoadBoard(src)
    n = collections.Counter()
    for t in list(b.GetTracks()):
        kind = "via" if t.Type() == K.PCB_VIA_T else "track"
        if t.GetNetname() in PLANE:
            n["kept " + kind] += 1
        else:
            n["took off " + kind] += 1
            b.Delete(t)
    print("strip:", dict(n))
    stripped = os.path.join(wd, "stripped.kicad_pcb")
    b.Save(stripped)
    base = os.path.join(wd, "base.kicad_pcb")
    if run([PY, os.path.join(HERE, "preroute_rev4.py"), stripped, base], os.path.join(wd, "preroute.log")):
        sys.exit("pre-route failed, see preroute.log")
    print(open(os.path.join(wd, "preroute.log")).read().strip())
    b = K.LoadBoard(base)
    if not K.ExportSpecctraDSN(b, os.path.join(wd, "base.dsn")):
        sys.exit("Specctra export failed")
    import dsn_prep
    dsn_prep.main(os.path.join(wd, "base.dsn"), os.path.join(wd, "route.dsn"), plane_keepouts=True, fid=True,
                  fixed=fixed_nets())


def fixed_nets():
    import preroute_rev4
    return list(preroute_rev4.FIXED)


def route(wd, jar, java, passes):
    return run([java, "-jar", jar, "-de", os.path.join(wd, "route.dsn"), "-do", os.path.join(wd, "route.ses"),
                "-mp", str(passes), "-mt", "3", "--gui.enabled=false", "--router.fanout.enabled=false",
                "--router.automatic_neckdown=false"], os.path.join(wd, "route.log"))


def finish(wd):
    base, ses = os.path.join(wd, "base.kicad_pcb"), os.path.join(wd, "route.ses")
    imp, fin = os.path.join(wd, "imported.kicad_pcb"), os.path.join(wd, "final.kicad_pcb")
    fx = "--fixed=" + ",".join(fixed_nets())
    if run([PY, os.path.join(HERE, "ses_import.py"), base, ses, imp, fx]):
        sys.exit("session import lost plane ties or fixed copper")
    return run([PY, os.path.join(HERE, "fr_finish.py"), imp, fin, fx])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("prep", "route", "finish", "all"))
    ap.add_argument("args", nargs="+")
    ap.add_argument("--jar")
    ap.add_argument("--java", default=shutil.which("java") or "java")
    ap.add_argument("--passes", type=int, default=40)
    a = ap.parse_args()
    if a.step in ("prep", "all"):
        prep(os.path.abspath(a.args[0]), os.path.abspath(a.args[1]))
    wd = os.path.abspath(a.args[-1])
    if a.step in ("route", "all"):
        if not a.jar:
            sys.exit("--jar: the freerouting executable jar")
        route(wd, a.jar, a.java, a.passes)
    if a.step in ("finish", "all"):
        return finish(wd)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main())
