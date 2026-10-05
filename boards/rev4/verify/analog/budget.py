# Frame-time budget for a 32x32 rev-4 board on the internal ADC, from the simulated settle times
# (run_chain.py, worst case over pressed->rest, rest->pressed, saturated->rest, short->rest, ->open)
# and rev-1's measured firmware overhead (firmware/README.md frame-rate table, 16x32).
import numpy as np
cc=np.array([5,20,50,100,200.])
settle_worst=np.array([5.45,6.60,8.80,12.30,18.84])   # incl. open column / mat short
settle_mat  =np.array([4.72,5.72,7.68,10.84,16.93])   # connected mat, worst = saturated press -> rest
# rev-1 overhead per mux setting, from firmware/README.md (scan - row overhead)/288 - (settle+conv)
rev1=[("ovs1 s3",4730,3,8),("ovs1 s5",5260,5,8),("ovs1 s8",6160,8,8),("ovs1 s12",7340,12,8),("ovs1 sp2 s15",8160,15,8),("ovs2 sp2 s15",10860,15,16)]
for n,scan,s,conv in rev1:
    rowov=16*(3+5)+2*(3+15)
    print("rev-1 %-13s per mux setting %.2f us, overhead %.2f us"%(n,(scan-rowov)/288.,(scan-rowov)/288.-s-conv))
NSET=34*16; ROWOV=32*(3+5)+2*(3+15)
cfgs=[("RR ovs1, no discard, no fw overhead",4,0),("ovs1 + discard, rev-1 overhead",8,4.8),("ovs1, no discard, rev-1 overhead",4,4.8),("rev-1 defaults (ovs2 spread2 discard)",16,6.1)]
print("\nC_col pF | settle us (mat / worst) | frame ms and fps per config")
for i,c in enumerate(cc):
    row="%5.0f    | %5.2f / %5.2f |"%(c,settle_mat[i],settle_worst[i])
    for name,conv,ovh in cfgs:
        t=NSET*(settle_worst[i]+conv+ovh)+ROWOV
        row+=" %5.2f ms %4.0f fps |"%(t/1e3,1e6/t)
    print(row)
print("configs:", [c[0] for c in cfgs])
# rev-1 default settle 15 us at 32x32
t=NSET*(15+16+6.1)+ROWOV; print("rev-1 defaults as shipped (s15 ovs2 sp2 discard) at 32x32: %.2f ms = %.1f fps"%(t/1e3,1e6/t))
# 60 Hz budget per PLAN: scan must end by ~11.2 ms
for name,conv,ovh in cfgs:
    smax=(11200-ROWOV)/NSET-conv-ovh
    cmax=np.interp(smax,settle_worst,cc); cmat=np.interp(smax,settle_mat,cc)
    print("11.2 ms scan, %-40s: settle budget %.2f us -> C_col <= %.0f pF (worst) / %.0f pF (mat)"%(name,smax,cmax,cmat))
# plan's settleUs=6 us: what C_col does it cover
print("settleUs 6 us covers C_col <= %.0f pF (worst) / %.0f pF (mat)"%(np.interp(6,settle_worst,cc),np.interp(6,settle_mat,cc)))
print("settleUs 15 us covers C_col <= %.0f pF (worst) / %.0f pF (mat)"%(np.interp(15,settle_worst,cc),np.interp(15,settle_mat,cc)))
