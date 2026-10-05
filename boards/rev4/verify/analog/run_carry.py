# Generates and runs carry_<tacq>_<mode>.cir: round-robin ADC1 -> ADC3 -> ADC1 ... on the
# RP2350's single SAR. One 1 pF sample capacitor (RP2350 ds 12.4.3 "about 1pF") is switched
# for t_acq onto ADC_A, held for the rest of the 2 us conversion, then switched onto ADC_B.
# Bank A sits near full scale (3.12 V), bank B at rest (0.150 V) or dark (~0 V).
# mode 'prev': the cap arrives carrying the other channel's sample (charge-redistribution SAR)
# mode 'rail': worst case, the cap is reset to the opposite rail before every acquisition
# 'noC'      : rev-1 style, no 51R/1nF at the pin (amp output straight to the pin + 5 pF pad)
import subprocess, numpy as np
VCC=3.29; AVDD=VCC-1.5e-3; LSB=AVDD/4096
TPL = """* carry_{tag}.cir - S/H carry-over between ADC_A and ADC_B, t_acq = {tacq} ns, Rsw = {rsw}, {mode}
.include tlv9062.lib
Vp vp 0 {vcc}
VsA sa 0 0.520
VsB sb 0 {vsb}
XA sa ga ampa vp 0 tlv9062
R6 ga ampa 10k
R7 ga 0 2k
XB sb gb ampb vp 0 tlv9062
R8 gb ampb 10k
R9 gb 0 2k
{netA}
{netB}
* sample capacitor and its switches: A acquires at 0,4,8.. us; B at 2,6,10.. us (each for t_acq)
Csh sh 0 1p
SA adca sh ca 0 swm
SB adcb sh cb 0 swm
Vca ca 0 PULSE(0 1 0 1n 1n {tacqs} 4u)
Vcb cb 0 PULSE(0 1 2u 1n 1n {tacqs} 4u)
{reset}
.model swm SW(VT=0.5 VH=0.01 RON={rsw} ROFF=1e13)
.options reltol=1e-7 abstol=1e-15 vntol=1e-9 method=gear
.control
set noaskquit
tran 0.5n 20.5u
wrdata carry_{tag}.txt v(sh) v(adca) v(adcb)
.endc
.end
"""
def run(tacq, rsw, mode, vsb=0.025, rc=True):
    tag="%s_t%d_r%d_vb%d%s"%(mode,tacq,rsw,int(vsb*1e3),"" if rc else "_noC")
    if rc:
        netA="R21 ampa adca 51\nC31 adca 0 1n\nCpa adca 0 3p"
        netB="R22 ampb adcb 51\nC32 adcb 0 1n\nCpb adcb 0 3p"
    else:
        netA="Rta ampa adca 1m\nCpa adca 0 5p"
        netB="Rtb ampb adcb 1m\nCpb adcb 0 5p"
    reset=""
    if mode=="rail":
        # before A's window, reset to 0 V (opposite of A's 3.12 V); before B's window, to AVDD
        reset=("Vra ra 0 PULSE(0 1 3.9u 1n 1n 50n 4u)\nSRa sh r0 ra 0 swr\nVr0 r0 0 0\n"
               "Vrb rb 0 PULSE(0 1 1.9u 1n 1n 50n 4u)\nSRb sh r1 rb 0 swr\nVr1 r1 0 %.4f\n"
               ".model swr SW(VT=0.5 VH=0.01 RON=10 ROFF=1e13)"%AVDD)
    open("carry_%s.cir"%tag,"w").write(TPL.format(tag=tag,tacq=tacq,rsw=rsw,mode=mode,vcc=VCC,vsb=vsb,
        netA=netA,netB=netB,tacqs="%gn"%(tacq-2),reset=reset))
    subprocess.run(["ngspice","-b","carry_%s.cir"%tag],capture_output=True,text=True)
    d=np.loadtxt("carry_%s.txt"%tag); t=d[:,0]; sh=d[:,1]; va=d[:,3]; vb=d[:,5]
    # true values: the settled pin voltages just before each window
    errs_a=[]; errs_b=[]
    for k in range(2,5):
        ta=4e-6*k; tb=ta+2e-6
        ia_end=np.searchsorted(t, ta+tacq*1e-9-1e-9); ib_end=np.searchsorted(t, tb+tacq*1e-9-1e-9)
        ia_pre=np.searchsorted(t, ta-50e-9); ib_pre=np.searchsorted(t, tb-50e-9)
        errs_a.append((sh[ia_end]-va[ia_pre])/LSB); errs_b.append((sh[ib_end]-vb[ib_pre])/LSB)
    dip_b = (vb[np.searchsorted(t,10e-6-50e-9)] - vb[(t>10e-6)&(t<12e-6)].min()) if True else 0
    dip_b = max(abs(vb[np.searchsorted(t,10e-6-50e-9)] - vb[(t>10e-6)&(t<12e-6)].min()), abs(vb[(t>10e-6)&(t<12e-6)].max()-vb[np.searchsorted(t,10e-6-50e-9)]))
    return np.mean(errs_a), np.mean(errs_b), dip_b/LSB
print("t_acq  Rsw   mode  pin-C   err bankA(FS) LSB   err bankB(rest) LSB   max pin kick on B (LSB)")
for mode in ("prev","rail"):
    for rsw in (1000,):
        for tacq in (20,50,100,200,500,1000):
            ea,eb,kb=run(tacq,rsw,mode)
            print("%4d ns %5d  %s  1nF    %+8.3f            %+8.3f            %.2f"%(tacq,rsw,mode,ea,eb,kb))
for tacq in (20,50,100,200,500):
    ea,eb,kb=run(tacq,1000,"prev",rc=False)
    print("%4d ns %5d  prev  none   %+8.3f            %+8.3f            %.2f   (rev-1-style: amp straight to pin)"%(tacq,1000,ea,eb,kb))
for rsw in (200,5000):
    ea,eb,kb=run(100,rsw,"prev")
    print("%4d ns %5d  prev  1nF    %+8.3f            %+8.3f            %.2f   (switch R corner)"%(100,rsw,ea,eb,kb))
