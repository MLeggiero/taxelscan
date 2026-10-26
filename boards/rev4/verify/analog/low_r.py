# What a lower-resistance mat can do to the board: DC currents and voltages, worst case (no ngspice; nodal solve)
#
# The mat is 32 rows x 32 columns of taxels. U1-U4 (SN74LVC595A) drive the rows push-pull
# with OE tied low, so every row is always driven: the scanned row high, the other 31 low.
# A column joins its taxels on every row, so two pressed taxels on one column join the high
# row to a low one: the 595 outputs then drive each other through the mat, which nothing on
# the board limits but the taxels and the outputs' own resistance. The selected columns go
# through U5 / U6 (CD74HC4067) to SENSE_A / SENSE_B, held by R1 / R2 (10k) and read by U7.
#
# Every current a pressed taxel can cause only grows as any taxel's resistance falls
# (Rayleigh's monotonicity: the mat is a resistor network between the high row and the low
# ones), so the whole mat pressed at the sensor's lowest resistance bounds every pattern.
#
#   python3 low_r.py
import math
import numpy as np

V = 3.43          # +3.3V at the TPS62162's maximum output (3.18-3.43 V): the worst case for current
R_OUT = 10.0      # SN74LVC595A output, a strong part. The datasheet bounds only the weak side: VOH >= 2.2 V
                  # at -24 mA and VOL <= 0.55 V at 24 mA (VCC 3 V) -> <= 33 / 23 ohm. A low value gives
                  # more current; real outputs also saturate (not modelled), so these currents are upper bounds
R_ON = 70.0       # CD74HC4067 switch, typical at 4.5 V (higher at 3.3 V); low gives more current
R_PD = 10e3       # R1 / R2
G = 1 + 10e3 / 2e3
AMP_MAX = 3.265   # U7's output limit on +3.3V (dc_range.py)
N = 32
PKG = [range(0, 8), range(8, 16), range(16, 24), range(24, 32)]    # U1..U4 drive ROW_0-7 .. ROW_24-31
# SN74LVC595A (SCASE93A) and CD74HC4067 (SCHS209) limits
IOH_REC, IO_MAX, ICC_MAX = 24e-3, 50e-3, 100e-3
SW_MAX = 25e-3


def solve(high, R, sel=0):
    """rows in `high` driven to V, the rest to 0; R[i, j] the taxel between row i and column j;
    column sel of bank A (and sel + 16 of bank B) switched to its sense node.
    -> row output currents (sourced > 0), column voltages, sense voltages, switch currents"""
    n = N + N + 2                         # rows, columns, SENSE_A, SENSE_B
    Gm = np.zeros((n, n))
    I = np.zeros(n)

    def g(a, b, cond):
        Gm[a, a] += cond
        Gm[b, b] += cond
        Gm[a, b] -= cond
        Gm[b, a] -= cond
    for i in range(N):
        Gm[i, i] += 1 / R_OUT
        I[i] += (V if i in high else 0.0) / R_OUT
        for j in range(N):
            if np.isfinite(R[i, j]):
                g(i, N + j, 1 / R[i, j])
    for bank, col in ((0, sel), (1, sel + 16)):
        s = 2 * N + bank
        g(N + col, s, 1 / R_ON)
        Gm[s, s] += 1 / R_PD
    for j in range(N):                    # a column with no taxel at all would float: tie it weakly
        Gm[N + j, N + j] += 1e-12
    v = np.linalg.solve(Gm, I)
    rows = [((V if i in high else 0.0) - v[i]) / R_OUT for i in range(N)]
    sense = v[2 * N:]
    sw = [(v[N + sel] - sense[0]) / R_ON, (v[N + sel + 16] - sense[1]) / R_ON]
    return rows, v[N:2 * N], sense, sw


def stress(high, R):
    rows, cols, sense, sw = solve(high, R)
    pin = max(abs(x) for x in rows)
    vcc = max(sum(max(rows[i], 0) for i in p) for p in PKG)
    gnd = max(sum(max(-rows[i], 0) for i in p) for p in PKG)
    return dict(pin=pin, vcc=vcc, gnd=gnd, rail=sum(max(x, 0) for x in rows), sw=max(abs(x) for x in sw),
                sense=max(sense), amp=min(G * max(sense), AMP_MAX), col=(min(cols), max(cols)),
                heat=max(x * x * R_OUT for x in rows))


def mat(r, k=N, rest=float("inf")):
    """a k x k patch pressed at r from row 0 / column 0 (the scanned row, the selected columns), the rest at `rest`"""
    R = np.full((N, N), rest)
    R[:k, :k] = r
    return R


def threshold(f, limit, lo=1.0, hi=1e7):
    """the lowest taxel resistance at which f(r) stays within limit (f falls as r rises)"""
    if f(hi) > limit:
        return float("inf")
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        if f(mid) > limit:
            lo = mid
        else:
            hi = mid
    return hi


SCAN = {0}                                # ROW_0 high, the other 31 low
BOOT = set(PKG[0])                        # power-up: the registers are unknown; one 595 all high, the rest low

print("Supply %.2f V, 595 outputs %.0f ohm, mux %.0f ohm, pull-downs %.0f ohm, gain %.0f" % (V, R_OUT, R_ON, R_PD, G))
print("Limits: 595 output %.0f mA operating / %.0f mA absolute, %.0f mA through one 595's VCC or GND; "
      "4067 switch %.0f mA" % (IOH_REC * 1e3, IO_MAX * 1e3, ICC_MAX * 1e3, SW_MAX * 1e3))
print()
print("Whole mat pressed at r, scanning (one row high). SENSE and U7 stay low here: the 31 low rows pull every")
print("column down through the mat; alone, a taxel this low reads full scale (U7 at its %.3f V limit)." % AMP_MAX)
print("  %9s %9s %9s %9s %9s %9s %8s %8s" % ("r (ohm)", "pin mA", "595 VCC", "595 GND", "rail mA", "4067 mA", "SENSE V", "U7 out"))
for r in (1e6, 100e3, 38e3, 10e3, 4.7e3, 2.2e3, 1e3, 470, 220, 100, 47, 10, 1):
    s = stress(SCAN, mat(r))
    print("  %9.0f %9.2f %9.2f %9.2f %9.1f %9.3f %8.3f %8.3f%s" % (
        r, s["pin"] * 1e3, s["vcc"] * 1e3, s["gnd"] * 1e3, s["rail"] * 1e3, s["sw"] * 1e3, s["sense"], s["amp"],
        "   <- past the operating rating" if s["pin"] > IOH_REC and s["pin"] <= IO_MAX else
        "   <- PAST THE ABSOLUTE MAXIMUM" if s["pin"] > IO_MAX else ""))
print()
print("Lowest taxel resistance that keeps every rating, whole mat pressed (bounds every pattern):")
print("  scanning, 595 output <= %.0f mA:    %6.0f ohm" % (IOH_REC * 1e3, threshold(lambda r: stress(SCAN, mat(r))["pin"], IOH_REC)))
print("  scanning, 595 output <= %.0f mA:    %6.0f ohm" % (IO_MAX * 1e3, threshold(lambda r: stress(SCAN, mat(r))["pin"], IO_MAX)))
print("  scanning, one 595 <= %.0f mA:      %6.0f ohm" % (ICC_MAX * 1e3, threshold(lambda r: max(stress(SCAN, mat(r))["vcc"], stress(SCAN, mat(r))["gnd"]), ICC_MAX)))
print("  power-up, one 595 <= %.0f mA:      %6.0f ohm  (8 rows of one 595 high, the other 24 low)" % (
    ICC_MAX * 1e3, threshold(lambda r: max(stress(BOOT, mat(r))["vcc"], stress(BOOT, mat(r))["gnd"]), ICC_MAX)))
print("  power-up, 595 output <= %.0f mA:    %6.0f ohm" % (IO_MAX * 1e3, threshold(lambda r: stress(BOOT, mat(r))["pin"], IO_MAX)))
# the switch's worst case is the selected taxel alone, shorted: the whole mat pressed pulls its column down
print("  any r, 4067 switch: %.3f mA with the selected taxel alone shorted (limit %.0f mA)" % (
    stress(SCAN, mat(1e-3, 1))["sw"] * 1e3, SW_MAX * 1e3))
print()
print("A k x k press over the scanned row (smaller presses need lower resistance to do harm):")
print("  %4s %14s %14s" % ("k", "<= 24 mA from", "<= 50 mA from"))
for k in (2, 3, 4, 6, 8, 12, 16, 24, 32):
    print("  %4d %12.0f ohm %12.0f ohm" % (k, threshold(lambda r: stress(SCAN, mat(r, k))["pin"], IOH_REC),
                                          threshold(lambda r: stress(SCAN, mat(r, k))["pin"], IO_MAX)))
print()
s = stress(SCAN, mat(1e-3))
print("Dead short (a row shorted to a column that touches a low row, or row to row): %.0f mA from one output, "
      "%.2f W in it at %.0f ohm - the outputs' own resistance is the only limit" % (s["pin"] * 1e3, s["heat"], R_OUT))
print()
# the sensor this board is built for: about 1 Mohm at rest, 50 kohm pressed, readings clip below 38 kohm
s = stress(SCAN, mat(38e3))
b = stress(BOOT, mat(38e3))
print("The 1 Mohm CNT sensor, whole mat at the 38 kohm clip point: %.2f mA per output scanning, "
      "%.1f mA through one 595 at power-up" % (s["pin"] * 1e3, max(b["vcc"], b["gnd"]) * 1e3))
# R1 / R2 lowered for a lower-resistance sensor: the switch then carries V / (R1 + Ron)
for rpd in (1e3, 330, 150, 100, 47):
    R_PD = rpd
    s = stress(SCAN, mat(1e-3, 1))
    print("R1 / R2 = %4.0f ohm: 4067 switch %.1f mA with the selected taxel shorted%s" % (
        rpd, s["sw"] * 1e3, "  <- PAST ITS 25 mA" if s["sw"] > SW_MAX else ""))
R_PD = 10e3
