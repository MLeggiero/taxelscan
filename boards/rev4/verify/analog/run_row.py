# Generates and runs row_<case>_c<pF>.cir: the row (595) switch on a fixed selected column:
# row r (taxel Ra) goes LOW and row r+1 (taxel Rb) goes HIGH at t = 1 us (595 edges ~3 ns).
# The other 30 taxels of the column stay on grounded rows. Time to 0.5 LSB at ADC_A.
import subprocess, numpy as np
VCC=3.29; LSB=(VCC-1.5e-3)/4096
TPL="""* row_{tag}.cir - row switch: ROW_r ({ra}) high->low, ROW_r+1 ({rb}) low->high at 1 us, column C {cc} pF
.include tlv9062.lib
Vp vp 0 {vcc}
Vr1 r1s 0 PWL(0 {vcc} 1u {vcc} 1.003u 0)
Vr2 r2s 0 PWL(0 0 1u 0 1.003u {vcc})
Rr1 r1s r1 25
Rr2 r2s r2 25
Rta r1 col {ra}
Rtb r2 col {rb}
Rot col 0 {rot}
Ccol col 0 {cc}p
Ron col com 250
Ccom com 0 55p
Ctr com 0 1.5p
R1 com 0 10k
XU com gain amp vp 0 tlv9062
R6 gain amp 10k
R7 gain 0 2k
Cic gain 0 4p
Cid gain com 2p
R21 amp adc 51
C31 adc 0 1n
Cpad adc 0 3p
Rsw adc sh 1k
Csh sh 0 1p
.options reltol=1e-6 abstol=1e-13 vntol=1e-8 method=gear
.control
set noaskquit
tran 2n {tstop}u
wrdata row_{tag}.txt v(adc)
.endc
.end
"""
for tag,ra,rb in (("pr2rest","50k","1meg"),("rest2pr","1meg","50k"),("sat2rest","10k","1meg")):
    for cc in (5,20,100):
        t="%s_c%d"%(tag,cc); tstop=1+max(8,14*10e3*(62+cc)*1e-12*1e6)
        open("row_%s.cir"%t,"w").write(TPL.format(tag=t,ra=ra,rb=rb,cc=cc,vcc=VCC,rot="%g"%((1e6+25)/30),tstop="%.1f"%tstop))
        subprocess.run(["ngspice","-b","row_%s.cir"%t],capture_output=True,text=True)
        d=np.loadtxt("row_%s.txt"%t); tt=d[:,0]; v=d[:,1]; fin=v[-1]
        v0=v[np.searchsorted(tt,0.99e-6)]
        bad=np.where(np.abs(v-fin)>0.5*LSB)[0]; ts=tt[bad[-1]]-1e-6
        print("%-9s C_col %3d pF: ADC %.3f -> %.4f V  t(0.5 LSB) = %.2f us"%(tag,cc,v0,fin,ts*1e6))
