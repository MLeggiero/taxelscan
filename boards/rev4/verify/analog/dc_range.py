# DC signal range of the rev-4 chain (no ngspice needed; closed form)
import math
VCC=3.29      # +3.3V rail nominal as first analysed (TLV62569, 3.24-3.34 V); the TPS62162 that replaced it gives 3.30 V (3.18-3.43). The results are ratios of the rail
Rpd=10e3; G=1+10e3/2e3
Ron=250.0     # CD74HC4067 at VCC=3.3 V, VIS~0 (datasheet only gives 70/160 ohm at 4.5 V) -> assumption
Rout=25.0     # SN74LVC595A, from VOH 2.2V @ -24 mA @ 3 V -> <=33 ohm
AVDD=VCC-150e-6*10   # R26 drop with ~150 uA ADC current
LSB=AVDD/4096
VOH_amp=VCC-0.027    # TLV9062: Ro~100 ohm x 0.27 mA into R6+R7 (12k) ; datasheet 15-20 mV @10k
def vsense(Rsel, Rothers=1e6, n_others=31):
    Rsh = 1/(1/Rpd + n_others/(Rothers+Rout))   # pulldown || 31 unselected taxels to grounded rows
    return VCC*Rsh/(Rsel+Ron+Rout+Rsh), Rsh
print("LSB at ADC = %.1f uV, at sense node = %.1f uV"%(LSB*1e6, LSB/G*1e6))
for R in (1e6, 800e3, 500e3, 200e3, 100e3, 50e3, 40e3, 30e3, 20e3, 10e3, 0):
    v,Rsh=vsense(R)
    va=min(G*v, VOH_amp)
    print("Rmat %8.0f  Vsense %7.2f mV  Vadc %6.3f V  code %5.0f  %s"%(R, v*1e3, G*v, min(G*v,VOH_amp)/LSB, "SAT" if G*v>VOH_amp else ""))
# saturation threshold
v_sat=VOH_amp/G
_,Rsh=vsense(1e6)
Rsat = VCC*Rsh/v_sat - Rsh - Ron - Rout
print("Rsh(at rest, 31 others at 1M) = %.0f ohm ; saturation below Rmat = %.1f kohm"%(Rsh, Rsat/1e3))
# sensitivity, ohm per LSB
for R in (1e6, 50e3):
    v1,_=vsense(R); v2,_=vsense(R*1.001)
    dvdR = (v1-v2)/(R*0.001)*G
    print("at %.0f ohm: %.1f ohm per LSB"%(R, LSB/dvdR))
# crosstalk: k other taxels in the same column pressed at 50k
for k in (0,1,5,10,31):
    Rsh = 1/(1/Rpd + k/(50e3+Rout) + (31-k)/(1e6+Rout))
    v = VCC*Rsh/(50e3+Ron+Rout+Rsh)
    print("50k taxel, %2d others in column pressed at 50k: Vadc %.3f V (%.0f%% of alone)"%(k, G*v, 100*G*v/(G*vsense(50e3)[0])))
# Thevenin resistance at the sense node (for settling)
for Rs,lab in ((1e6,"rest"),(50e3,"pressed"),(1e12,"open column/no mat")):
    Rsh = 1/(1/Rpd + 31/(1e6+Rout)) if Rs<1e12 else Rpd
    Rth = 1/(1/Rsh + 1/(Rs+Ron+Rout))
    print("Rth %-20s %.0f ohm"%(lab, Rth))
# RAIL_MON
for v5 in (4.5, 5.0, 5.25, 5.5, 6.0, 7.3):
    vm=v5*47/147
    print("+5V %.2f V -> RAIL_MON %.3f V code %.0f"%(v5, vm, vm/LSB))
print("RAIL_MON Thevenin %.1f kohm, tau with 100nF = %.2f ms"%(1/(1/100e3+1/47e3)/1e3, 1/(1/100e3+1/47e3)*100e-9*1e3))
print("RAIL_MON 1 uA pin leakage error = %.1f mV at pin = %.0f mV at +5V"%(1e-6*31.97e3*1e3, 1e-6*31.97e3*147/47*1e3))
print("S/H kick on 100 nF: %.1f uV for 3.3 V difference"%(3.3*1e-12/100e-9*1e6))
# ADC noise from ENOB
for enob in (9.0, 9.2, 9.5):
    sig = 2**(12-enob)/math.sqrt(12)
    print("ENOB %.1f -> %.2f LSB rms (%.0f uV), %.1f LSB p-p (6.6 sigma), noise-free bits %.2f; ovs2: %.2f LSB rms"%(enob, sig, sig*LSB*1e6, 6.6*sig, 12-math.log2(6.6*sig), sig/math.sqrt(2)))
# rev-3 16-bit chain predicted: 17 LSB16 p-p
print("rev-3 16-bit chain: 17 LSB16 p-p = %.2f LSB12 p-p (%.0f uV p-p at ADC)"%(17/16, 17*3.29/65536*1e6))
