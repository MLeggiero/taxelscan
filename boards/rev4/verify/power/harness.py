#!/usr/bin/env python3
"""Hot-plugging a rev-4 board onto a live harness (no damper on +5V_BUS).
Upstream node A (adjacent board's J4: C39 + its C22 via D1, eff ~10 uF) fed from the master's
+5V_BUS (stiff Vh) through Rsrc/Lsrc; hot-plugged segment Lh/Rh; node B = new board's +5V_BUS
(C39 eff 7 uF) -> D1 -> +5V (C22 eff 2.8 uF + C27) = U12 VIN."""
import subprocess, itertools, re, tempfile, os
DIODE = ".model DSCH D(IS=3.1u N=1.0 RS=0.1 CJO=66p BV=20 IBV=100u)"
def run(vh, Lh, Rh, CA=10e-6, Rsrc=0.3, Lsrc=0.5e-6, c39=7e-6, damp=None, tstop=80e-6):
    net = [ "* harness hot plug", DIODE,
        f"Vh src 0 {vh}",
        f"Rs src n0 {Rsrc}", f"Ls n0 a {Lsrc} IC=0",
        f"CA a ca {CA} IC={vh}", "RAe ca 0 10m",
        # switch: closes at 1us
        "Sw a b2 ctl 0 SWMOD", ".model SWMOD SW(VT=0.5 VH=0.1 RON=1m ROFF=1e9)",
        "Vctl ctl 0 PWL(0 0 1u 0 1.01u 1)",
        f"Rh b2 n1 {Rh}", f"Lh n1 b {Lh} IC=0",
        f"C39 b c39 {c39} IC=0", "R39e c39 0 10m",
        "D1 b v5 DSCH",
        "C22 v5 c22 2.8u IC=0", "R22e c22 0 10m",
        "C27 v5 c27 100n IC=0", "R27e c27 0 20m",
        "Rload v5 0 1k",
    ]
    if damp:
        R, C = damp
        net += [f"Rd b snb {R}", f"Cd snb 0 {C} IC=0"]
    net += [f".tran 2n {tstop} 0 5n UIC", ".control", "run",
        "meas tran pkb MAX v(b)", "meas tran pkv5 MAX v(v5)", "quit", ".endc", ".end"]
    fd, path = tempfile.mkstemp(suffix=".cir", dir="."); os.write(fd, "\n".join(net).encode()); os.close(fd)
    out = subprocess.run(["ngspice", "-b", path], capture_output=True, text=True).stdout; os.remove(path)
    v = dict(re.findall(r"^(pkb|pkv5)\s*=\s*([-\d.eE+]+)", out, re.M))
    return float(v.get("pkb","nan")), float(v.get("pkv5","nan"))
if __name__ == "__main__":
    print("Vh = harness voltage at the master (VBUS - U14 - D4 at light load)")
    print(f"{'Vh':>5} {'Lh uH':>6} {'Rh':>5} {'CA uF':>6} | peak +5V_BUS(new) / peak U12 VIN")
    for vh, Lh, Rh, CA in itertools.product((4.8, 5.05), (0.3e-6, 0.6e-6), (0.1, 0.2, 0.35), (10e-6, 40e-6)):
        b, v5 = run(vh, Lh, Rh, CA=CA)
        print(f"{vh:5.2f} {Lh*1e6:6.1f} {Rh:5.2f} {CA*1e6:6.0f} | {b:6.2f} / {v5:6.2f}")
