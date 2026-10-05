import itertools, hotplug, re
# monkeypatch R38 value by editing the netlist builder: simplest is a local copy
import subprocess, tempfile, os
def run(vbus, L, R, r38, c46, c22=2.8e-6):
    net=f"""* sweep
{hotplug.DIODE}
Vs src 0 PWL(0 0 10n {vbus})
Rc src n1 {R}
Lc n1 usb {L} IC=0
C37 usb c37 0.9u IC=0
R37e c37 0 10m
C38 usb c38 100n IC=0
R38e c38 0 20m
C42 usb c42 100n IC=0
R42e c42 0 20m
R38 usb snub {r38}
C46 snub 0 {c46} IC=0
D2 usb v5 DSCH
C22 v5 c22 {c22} IC=0
R22e c22 0 10m
C27 v5 c27 100n IC=0
R27e c27 0 20m
Rload v5 0 1k
.tran 2n 60u 0 2n UIC
.control
run
meas tran pkusb MAX v(usb)
meas tran pkv5 MAX v(v5)
quit
.endc
.end
"""
    fd, path = tempfile.mkstemp(suffix=".cir", dir="."); os.write(fd, net.encode()); os.close(fd)
    out = subprocess.run(["ngspice","-b",path],capture_output=True,text=True).stdout; os.remove(path)
    v=dict(re.findall(r"^(pkusb|pkv5)\s*=\s*([-\d.eE+]+)", out, re.M))
    return float(v['pkusb']), float(v['pkv5'])
print("worst corner: Rcable=0.1 ohm; peak +5V_USB / U12 VIN")
print(f"{'R38':>5} {'C46eff':>7} | {'5.25V,0.5uH':>14} {'5.25V,1uH':>14} {'5.5V,0.5uH':>14} {'5.5V,1uH':>14}")
for r38, c46 in itertools.product((1.0, 0.68, 0.47, 0.33), (7e-6, 15e-6)):
    row=[run(v,L,0.1,r38,c46) for v,L in ((5.25,0.5e-6),(5.25,1e-6),(5.5,0.5e-6),(5.5,1e-6))]
    print(f"{r38:5.2f} {c46*1e6:6.0f}u | " + " ".join(f"{a:5.2f}/{b:5.2f}   " for a,b in row))
