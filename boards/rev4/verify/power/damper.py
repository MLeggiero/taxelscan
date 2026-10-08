#!/usr/bin/env python3
"""damper.py - the USB-C hot-plug peak at +5V_USB and at U12's input, per damper and cable.

    python3 damper.py            (needs ngspice)

The worst case is a stiff, always-on 5 V supply on a captive cable: VBUS steps
onto the board's ceramic input capacitance through the cable's inductance and
rings towards twice VBUS. A compliant USB-C source turns VBUS on only after it
sees Rd, and an A-to-C cable adds 0.3 ohm or more; both stay near 5.3 V
(hotplug.py). The model is hotplug.py's: source step -> cable R, L -> +5V_USB
(C37 1 uF at ~0.9 uF effective, C38 / C42 100 nF, R34's 961 k) -> the R38 / C46
damper -> D2 -> +5V (C22 4.7 uF at ~2.8 uF effective, C27 100 nF, 1 k load).

The limits: U14 (TPS2553) IN 7 V absolute maximum on +5V_USB; U12 6 V when it
was the TLV62569, 20 V now that it is the TPS62162. C46's effective value is its
capacitance at 5 V bias: 10 uF X7R 0805 ~7 uF, 22 uF X5R 0805 ~12 uF.
"""
import os
import re
import subprocess
import tempfile

DIODE = ".model DSCH D(IS=3.1u N=1.0 RS=0.1 CJO=66p BV=20 IBV=100u)"   # fitted to PMEG2005AEA VF table
CORNERS = [(5.0, 1.5e-6, 0.08), (5.25, 1.5e-6, 0.08), (5.5, 0.5e-6, 0.08), (5.5, 1.0e-6, 0.08),
           (5.5, 1.5e-6, 0.08), (5.5, 2.0e-6, 0.10)]
DAMPERS = [("none", None, None), ("1R + 10uF (was)", 1.0, 7e-6), ("1R + 22uF", 1.0, 12e-6),
           ("0.82R + 22uF", 0.82, 12e-6), ("0.68R + 22uF (fitted)", 0.68, 12e-6), ("0.47R + 22uF", 0.47, 12e-6)]


def run(vbus, L, R, r38, c46, c22=2.8e-6, tstop=80e-6):
    net = ["* hot plug", DIODE,
           "Vs src 0 PWL(0 0 10n %g)" % vbus, "Rc src n1 %g" % R, "Lc n1 usb %g IC=0" % L,
           "C37 usb c37 0.9u IC=0", "R37e c37 0 10m",
           "C38 usb c38 100n IC=0", "R38e c38 0 20m",
           "C42 usb c42 100n IC=0", "R42e c42 0 20m",
           "Rb usb 0 961k"]
    if r38:
        net += ["R38 usb snub %g" % r38, "C46 snub 0 %g IC=0" % c46]
    net += ["D2 usb v5 DSCH",
            "C22 v5 c22 %g IC=0" % c22, "R22e c22 0 10m",
            "C27 v5 c27 100n IC=0", "R27e c27 0 20m",
            "Rload v5 0 1k",
            ".tran 2n %g 0 2n UIC" % tstop, ".control", "run",
            "meas tran pkusb MAX v(usb)", "meas tran pkv5 MAX v(v5)", "quit", ".endc", ".end"]
    fd, path = tempfile.mkstemp(suffix=".cir", dir=os.path.dirname(os.path.abspath(__file__)))
    os.write(fd, "\n".join(net).encode())
    os.close(fd)
    try:
        out = subprocess.run(["ngspice", "-b", path], capture_output=True, text=True).stdout
    finally:
        os.remove(path)
    v = dict(re.findall(r"^(pkusb|pkv5)\s*=\s*([-\d.eE+]+)", out, re.M))
    return float(v["pkusb"]), float(v["pkv5"])


if __name__ == "__main__":
    print("peak +5V_USB / peak U12 VIN (V); source V, cable L, cable + contact R")
    print("%-22s " % "R38 + C46" + " ".join("%4.2fV %3.1fuH %4.2fR " % (v, L * 1e6, R) for v, L, R in CORNERS))
    for name, r38, c46 in DAMPERS:
        print("%-22s " % name + " ".join("  %4.2f / %4.2f    " % run(v, L, R, r38, c46) for v, L, R in CORNERS),
              flush=True)
