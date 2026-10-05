# Generates and runs chain_<case>_c<pF>.cir: the column (mux) switch step through the whole
# rev-4 chain - 595 row source, mat taxels + 31-taxel sneak load, column capacitance,
# CD74HC4067 (Ron, CI, CCOM), R1 10k, TLV9062 x6, R21/C31, RP2350 S/H 1 pF - and reports
# the time from the mux edge until ADC_A stays within 0.5 LSB (12-bit) of its final value.
import subprocess, numpy as np, sys
VCC=3.29; AVDD=VCC-1.5e-3; LSB=AVDD/4096
TPL = """* chain_{tag}.cir - column switch X -> Y at t=1us. {desc}
.include tlv9062.lib
Vp vp 0 {vcc}
* selected row: SN74LVC595A output, VOH through ~25 ohm (datasheet VOH 2.2V@-24mA@3V -> <=33 ohm)
Vrow rowsrc 0 {vcc}
Rrow rowsrc row 25
* column X: taxel to the driven row, 31 unselected taxels to grounded rows (Rmat+25)/31,
* column capacitance = CI 5 pF (mux input) + board/FFC/mat {ccol} pF
RtX row colx {rx}
RoX colx 0 {rox}
CcX colx 0 {ctot}p
RtY row coly {ry}
RoY coly 0 {roy}
CcY coly 0 {ctot}p
* CD74HC4067: break-before-make, Ron {ron} ohm (only 4.5/6 V values are in SCHS209D; 3.3 V assumed)
Vcx cx 0 PWL(0 1 1u 1 1.01u 0)
Vcy cy 0 PWL(0 0 1.03u 0 1.04u 1)
SX colx com cx 0 swm
SY coly com cy 0 swm
.model swm SW(VT=0.5 VH=0.01 RON={ron} ROFF=1e12)
* common node: CCOM 50 pF (datasheet, 4.5 V) + 10% for 3.3 V bias, trace 1.5 pF, U7 CIC 4 pF
Ccom com 0 55p
Ctr com 0 1.5p
R1 com 0 10k
XU com gain amp vp 0 tlv9062 vos=0
R6 gain amp 10k
R7 gain 0 2k
Cic gain 0 4p
Cid gain com 2p
R21 amp adc 51
C31 adc 0 1n
Cpad adc 0 3p
* ADC: 1 pF sample capacitor ('about 1pF', RP2350 ds 12.4.3) on the pin through 1k, always tracking here
Rsw adc sh 1k
Csh sh 0 1p
.ic v(colx)={vx0} v(com)={vx0} v(coly)={vy0}
.options reltol=1e-6 abstol=1e-13 vntol=1e-8 method=gear
.control
set noaskquit
tran 2n {tstop}u uic
wrdata chain_{tag}.txt v(adc) v(com) v(amp)
.endc
.end
"""
def node(Rsel, Roth=1e6):
    rox=(Roth+25)/31.0
    return rox
def vfloat(Rsel, Roth=1e6):
    rox=(Roth+25)/31.0
    return VCC*rox/(Rsel+25+rox)
def vsel(Rsel, Roth=1e6, ron=250):
    rox=(Roth+25)/31.0; rsh=1/(1/rox+1/10e3)
    return VCC*rsh/(Rsel+25+ron+rsh)
cases = [
 ("fsdown", "pressed 50k -> rest 1M", 50e3, 1e6),
 ("fsup",   "rest 1M -> pressed 50k", 1e6, 50e3),
 ("satdown","hard press 10k (amp saturated) -> rest 1M", 10e3, 1e6),
 ("shortdown","mat short 0R -> rest 1M", 0.0, 1e6),
 ("small",  "rest 1M -> light touch 800k", 1e6, 800e3),
 ("open",   "pressed 50k -> open column (no mat, Rth=10k)", 50e3, 1e15),
]
ccols = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv)>1 else "5,20,50,100,200".split(","))]
ron=250
res={}
for tag,desc,rx,ry in cases:
    for cc in ccols:
        roy = 1e15 if ry>1e14 else (1e6+25)/31.0
        rox = (1e6+25)/31.0
        # initial conditions: X selected (settled), Y floating at its own divider voltage
        rsh=1/(1/rox+1/10e3)
        vx0=VCC*rsh/(rx+25+ron+rsh)
        vy0=0.0 if ry>1e14 else VCC*roy/(ry+25+roy)
        if ry>1e14: roy_s="1e15"
        else: roy_s="%g"%roy
        ry_s = "1e15" if ry>1e14 else "%g"%ry
        rx_s = "%g"%max(rx,1e-3)
        t="%s_c%d"%(tag,cc)
        tstop = 1+ max(8, 14*10e3*(62+cc)*1e-12*1e6)
        open("chain_%s.cir"%t,"w").write(TPL.format(tag=t,desc=desc+", column C = %d pF"%cc,vcc=VCC,rx=rx_s,rox="%g"%rox,
              ry=ry_s,roy=roy_s,ctot=cc,ron=ron,vx0=vx0,vy0=vy0,tstop="%.1f"%tstop, ccol=cc-5))
        subprocess.run(["ngspice","-b","chain_%s.cir"%t],capture_output=True,text=True)
        d=np.loadtxt("chain_%s.txt"%t); tt=d[:,0]; adc=d[:,1]
        fin=adc[-1]
        bad=np.where(np.abs(adc-fin)>0.5*LSB)[0]
        ts=tt[bad[-1]]-1e-6 if len(bad) else 0
        bad1=np.where(np.abs(adc-fin)>1.0*LSB)[0]; ts1=tt[bad1[-1]]-1e-6
        bad2=np.where(np.abs(adc-fin)>2.0*LSB)[0]; ts2=tt[bad2[-1]]-1e-6
        v0=adc[np.searchsorted(tt,0.99e-6)]
        res[(tag,cc)]=ts
        print("%-9s C_col %3d pF: ADC %.3f -> %.4f V (%5.0f LSB)  t(0.5 LSB) %6.2f us  t(1 LSB) %6.2f  t(2 LSB) %6.2f us   [%s]"%(tag,cc,v0,fin,abs(fin-v0)/LSB,ts*1e6,ts1*1e6,ts2*1e6,desc))
