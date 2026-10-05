#!/usr/bin/env python3
"""USB-C hot-plug ringing at +5V_USB and U12 VIN (+5V), rev-4 TaxelScan.
Lumped model: ideal source step -> cable (L, R) -> J5 VBUS node (+5V_USB):
C37 1uF(eff 0.9u) + C38/C42 100nF; damper R38 1R + C46 (eff); optional D5 VBUS clamp;
D2 PMEG2005AEA -> +5V: C22 (eff) + C27 100nF; U12 not yet switching (light load).
"""
import subprocess, itertools, re, sys

DIODE = ".model DSCH D(IS=3.1u N=1.0 RS=0.1 CJO=66p BV=20 IBV=100u)"   # fitted to PMEG2005AEA VF table
ZEN   = ".model DZ D(IS=1e-14 N=1 BV={bv} IBV=1m RS=2.2)"                 # USBLC6 VBUS-GND, VBR min 6V@1mA

def run(vbus, L, R, damper=True, c46=7e-6, c22=2.8e-6, d5=None, v5_init=0.0, usb_init=0.0, tstop=60e-6, rload=1e3):
    lines = [f"* hotplug vbus={vbus} L={L} R={R} damper={damper}",
             DIODE]
    if d5: lines.append(ZEN.format(bv=d5))
    lines += [
        f"Vs src 0 PWL(0 0 10n {vbus})",
        f"Rc src n1 {R}",
        f"Lc n1 usb {L} IC=0",
        # C37 1uF X7R 50V 0805 (eff 0.9u), C38/C42 100n
        "C37 usb c37 0.9u IC={u}".format(u=usb_init), "R37e c37 0 10m",
        "C38 usb c38 100n IC={u}".format(u=usb_init), "R38e c38 0 20m",
        "C42 usb c42 100n IC={u}".format(u=usb_init), "R42e c42 0 20m",
        "Rbleed usb 0 961k",
    ]
    if damper:
        lines += ["R38 usb snub 1", f"C46 snub 0 {c46} IC={usb_init}"]
    if d5:
        lines += ["DZ5 0 usb DZ"]
    lines += [
        "D2 usb v5 DSCH",
        f"C22 v5 c22 {c22} IC={v5_init}", "R22e c22 0 10m",
        f"C27 v5 c27 100n IC={v5_init}", "R27e c27 0 20m",
        f"Rload v5 0 {rload}",
        f".tran 2n {tstop} 0 2n UIC",
        ".control", "run",
        "meas tran pkusb MAX v(usb)", "meas tran pkv5 MAX v(v5)",
        "meas tran fin v(v5) AT={t}".format(t=tstop*0.99),
        "quit", ".endc", ".end"]
    net = "\n".join(lines)
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".cir", dir=".")
    os.write(fd, net.encode()); os.close(fd)
    out = subprocess.run(["ngspice", "-b", path], capture_output=True, text=True).stdout
    os.remove(path)
    vals = dict(re.findall(r"^(pkusb|pkv5|fin)\s*=\s*([-\d.eE+]+)", out, re.M))
    return float(vals.get("pkusb", "nan")), float(vals.get("pkv5", "nan")), float(vals.get("fin","nan"))

if __name__ == "__main__":
    print("Cold hot-plug (board unpowered). Peak +5V_USB / peak U12 VIN (+5V)")
    print(f"{'Vbus':>5} {'L uH':>5} {'R ohm':>6} | {'no damper':>17} | {'damper C46=7u':>17} | {'damper C46=10u':>17} | {'no damper +D5 6.0V':>18}")
    for vbus, L, R in itertools.product((5.25, 5.5), (0.5e-6, 1.0e-6), (0.1, 0.2, 0.43)):
        a = run(vbus, L, R, damper=False)
        b = run(vbus, L, R, damper=True, c46=7e-6)
        c = run(vbus, L, R, damper=True, c46=10e-6)
        d = run(vbus, L, R, damper=False, d5=6.0)
        print(f"{vbus:5.2f} {L*1e6:5.1f} {R:6.2f} | {a[0]:6.2f} / {a[1]:6.2f}  | {b[0]:6.2f} / {b[1]:6.2f}  | {c[0]:6.2f} / {c[1]:6.2f}  | {d[0]:6.2f} / {d[1]:6.2f}")
    print()
    print("Nominal-C variant (C22 = 4.7u, C46 = 10u, C37 = 0.9u)")
    for vbus, L, R in itertools.product((5.25, 5.5), (1.0e-6,), (0.1, 0.2)):
        a = run(vbus, L, R, damper=False, c22=4.7e-6)
        c = run(vbus, L, R, damper=True, c46=10e-6, c22=4.7e-6)
        print(f"{vbus:5.2f} {L*1e6:5.1f} {R:6.2f} | nodamp {a[0]:6.2f} / {a[1]:6.2f}  | damp {c[0]:6.2f} / {c[1]:6.2f}")
    print()
    print("USB hot-plug onto a harness-powered board (+5V pre-charged 4.4 V, +5V_USB at 3.0 V from D2 leakage)")
    for vbus, L, R in itertools.product((5.25, 5.5), (0.5e-6, 1.0e-6), (0.1, 0.2, 0.43)):
        a = run(vbus, L, R, damper=False, v5_init=4.4, usb_init=3.0, rload=150)
        b = run(vbus, L, R, damper=True, c46=7e-6, v5_init=4.4, usb_init=3.0, rload=150)
        print(f"{vbus:5.2f} {L*1e6:5.1f} {R:6.2f} | nodamp {a[0]:6.2f} / {a[1]:6.2f}  | damp {b[0]:6.2f} / {b[1]:6.2f}")
