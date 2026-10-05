# Generates and runs opamp_loop_*.cir: loop gain / phase margin of U7 for several loads and
# model corners. Writes the deck files so they can be re-run by hand.
import subprocess, re
TPL = """* opamp_loop_{tag}.cir - loop gain of U7, {desc}
.include tlv9062.lib
Vp vp 0 3.29
Vin sense 0 DC {vdc}
XU sense inm amp vp 0 tlv9062 vos=0 ro={ro} fp2={fp2}
{fb}
Cic gain 0 4p
Cid gain sense 2p
Ctr gain 0 0.5p
Vt inm gain DC 0 AC 1
{load}
.control
set noaskquit
ac dec 400 100 300meg
let T = -v(gain)/v(inm)
let Tdb = db(T)
let Tph = 180/pi*cph(T)
meas ac t0db find Tdb at=1k
meas ac ph1k find Tph at=1k
meas ac fc when Tdb=0
meas ac phfc find Tph at=fc
meas ac gm180 find Tdb when Tph=-180
let pm = phfc + 180
print pm
.endc
.end
"""
cases = [
 ("g1_rl10k",  "unity gain, RL 10k + CL 10pF (datasheet PM condition)", "Rfb gain amp 1m\nRgnd gain 0 1e12", "RL amp 0 10k\nCL amp 0 10p", 100, "12meg", 1.65),
 ("asbuilt",   "G=6, R21 51R + C31 1nF + 3pF pad (as built)", "R6 gain amp 10k\nR7 gain 0 2k", "R21 amp adc 51\nC31 adc 0 1n\nCpad adc 0 3p", 100, "12meg", 0.2),
 ("noload",    "G=6, feedback network only", "R6 gain amp 10k\nR7 gain 0 2k", "Rx amp 0 1e12", 100, "12meg", 0.2),
 ("direct1n",  "G=6, 1nF straight on the output (no R21)", "R6 gain amp 10k\nR7 gain 0 2k", "C31 amp 0 1n", 100, "12meg", 0.2),
 ("asbuilt_ro160", "G=6, as built, ZO=160 ohm", "R6 gain amp 10k\nR7 gain 0 2k", "R21 amp adc 51\nC31 adc 0 1n\nCpad adc 0 3p", 160, "12meg", 0.2),
 ("asbuilt_ro160_fp8", "G=6, as built, ZO=160 ohm, 2nd pole 8 MHz (PM(G=1)~45 deg)", "R6 gain amp 10k\nR7 gain 0 2k", "R21 amp adc 51\nC31 adc 0 1n\nCpad adc 0 3p", 160, "8meg", 0.2),
 ("g1_rl10k_fp8",  "unity gain check for the 8 MHz corner", "Rfb gain amp 1m\nRgnd gain 0 1e12", "RL amp 0 10k\nCL amp 0 10p", 100, "8meg", 1.65),
 ("asbuilt_ro50", "G=6, as built, ZO=50 ohm", "R6 gain amp 10k\nR7 gain 0 2k", "R21 amp adc 51\nC31 adc 0 1n\nCpad adc 0 3p", 50, "12meg", 0.2),
]
for tag, desc, fb, load, ro, fp2, vdc in cases:
    fn = "opamp_loop_%s.cir" % tag
    open(fn, "w").write(TPL.format(tag=tag, desc=desc, fb=fb, load=load, ro=ro, fp2=fp2, vdc=vdc))
    out = subprocess.run(["ngspice", "-b", fn], capture_output=True, text=True).stdout
    vals = dict(re.findall(r"^(t0db|ph1k|fc|phfc|gm180|pm)\s*=\s*([-0-9.eE+]+)", out, re.M))
    print("%-20s T(1k)=%6.1f dB ph=%6.1f  fc=%7.3f MHz  PM=%5.1f deg  GM=%s dB   (%s)" % (
        tag, float(vals.get('t0db','nan')), float(vals.get('ph1k','nan')), float(vals.get('fc','nan'))/1e6,
        float(vals.get('pm','nan')), ("%.1f" % -float(vals['gm180'])) if 'gm180' in vals else "n/a", desc))
