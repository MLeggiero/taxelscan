#!/usr/bin/env python3
"""
Turn the scanner's `v` raw-ADC dump into a spectrum.

    python spectrum.py --port COM10 --row 4 --col 4
    python spectrum.py --port COM10 --row 4 --col 4 --n 4096 --khz 96
    python spectrum.py --file dump.txt

WHY THIS EXISTS

There is no anti-alias filter anywhere in the TaxelScan signal chain. The netlist
is unambiguous: the TLV9062 output goes straight to the RP2350 ADC pin, with no
series resistor and no capacitor. Each taxel is sampled once per frame, so the
per-taxel sample rate is the frame rate - a few tens of hertz. Everything above
half of that folds down into the passband.

On a bench that mostly does not matter. On a robot arm it matters a great deal:
motor PWM at 10-30 kHz, servo current loops at 1-8 kHz and switching regulators
all alias to some arbitrary low frequency and appear as a patch that drifts
around the map, indistinguishable by eye from a light touch. Guessing which one
is doing it wastes a day.

So: park one taxel, sample the ADC at a known fixed rate through the FIFO, and
look. Once a line is identified, set the dwell so that

    oversample * spreadUs == 1e6 / f_interference

and the boxcar formed by the spread samples nulls that frequency exactly, along
with its harmonics. That is a real notch, not a hope.

Run it twice: with the arm powered but stationary, and with it moving. The
difference is the part you can actually do something about.
"""

import argparse
import cmath
import math
import re
import sys
import time


# --------------------------------------------------------------------- capture

def from_serial(port, row, col, n, khz, baud=115200):
    import serial
    ser = serial.Serial(port, baud, timeout=1.0)
    try:
        ser.set_buffer_size(rx_size=1 << 18)
    except Exception:
        pass
    time.sleep(2.0)
    ser.reset_input_buffer()
    for c in ("x", "m 0"):
        ser.write((c + "\n").encode())
        time.sleep(0.2)
    ser.reset_input_buffer()
    ser.write(("v %d %d %d %d\n" % (row, col, n, khz)).encode())

    out, t0 = [], time.time()
    while time.time() - t0 < 30:
        line = ser.readline().decode("utf-8", "replace")
        if not line:
            continue
        out.append(line)
        if "end spectrum" in line:
            break
    ser.close()
    return "".join(out)


def parse(text):
    fs, meta = None, ""
    vals = []
    for line in text.splitlines():
        m = re.search(r"fs=([\d.]+)", line)
        if m:
            fs = float(m.group(1))
            meta = line.lstrip("# ").strip()
            continue
        if line.startswith("#"):
            continue
        for tok in line.replace(",", " ").split():
            if tok.isdigit():
                vals.append(int(tok))
    if fs is None:
        sys.exit("no 'fs=' header found - is this a `v` dump?")
    if len(vals) < 64:
        sys.exit("only %d samples parsed; expected hundreds" % len(vals))
    return fs, vals, meta


# ------------------------------------------------------------------------ math

def fft(a):
    """Iterative radix-2 FFT. numpy is used when present; this is the fallback
    so the tool works on a bare Python install, which is often what is actually
    on the machine plugged into the robot."""
    n = len(a)
    if n & (n - 1):
        raise ValueError("length must be a power of two")
    j = 0
    a = list(a)
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    ln = 2
    while ln <= n:
        ang = -2 * math.pi / ln
        wl = cmath.exp(1j * ang)
        for i in range(0, n, ln):
            w = 1 + 0j
            for k in range(i, i + ln // 2):
                u = a[k]
                v = a[k + ln // 2] * w
                a[k] = u + v
                a[k + ln // 2] = u - v
                w *= wl
        ln <<= 1
    return a


def spectrum(vals, fs):
    n = 1 << (len(vals).bit_length() - 1)      # largest power of two that fits
    x = vals[:n]
    mean = sum(x) / n
    # Hann window: without it a line that is not exactly on a bin smears across
    # dozens of them and the peak list becomes meaningless.
    win = [(0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))) for i in range(n)]
    xw = [(x[i] - mean) * win[i] for i in range(n)]
    try:
        import numpy as np
        mag = abs(np.fft.rfft(np.array(xw)))
        mag = [float(v) for v in mag]
    except ImportError:
        sp = fft([complex(v, 0) for v in xw])
        mag = [abs(sp[i]) for i in range(n // 2 + 1)]
    # coherent gain of a Hann window is 0.5
    scale = 2.0 / (n * 0.5)
    mag = [m * scale for m in mag]
    freqs = [i * fs / n for i in range(len(mag))]
    return freqs, mag, n, mean


# ---------------------------------------------------------------------- report

def report(freqs, mag, n, mean, fs, vals, meta, top,
           TAXELS=512, SETTLE_US=15, FRAME_US=12500):
    lo = max(1, int(round(20.0 / (fs / n))))    # ignore DC and the first few bins
    body = mag[lo:]
    floor = sorted(body)[len(body) // 2]        # median magnitude = the noise floor

    peaks = []
    for i in range(lo + 1, len(mag) - 1):
        if mag[i] > mag[i - 1] and mag[i] >= mag[i + 1] and mag[i] > 6 * floor:
            peaks.append((mag[i], freqs[i]))
    peaks.sort(reverse=True)

    rms = math.sqrt(sum((v - mean) ** 2 for v in vals[:n]) / n)
    print(f"# {meta}")
    print(f"# {n} samples at {fs:.0f} Hz, bin {fs/n:.1f} Hz, "
          f"span {n/fs*1000:.1f} ms")
    print(f"# mean {mean:.1f} counts, rms {rms:.2f} counts, "
          f"peak-to-peak {max(vals[:n])-min(vals[:n])}")
    print(f"# noise floor {floor:.4f} counts/bin")
    print()

    if not peaks:
        print("No line stands out above 6x the noise floor. The interference here")
        print("is broadband, so a dwell notch has nothing to bite on - reduce it")
        print("with more oversampling (larger `o ovs`) and the temporal filters,")
        print("and if that is not enough the fix is shielding, not firmware.")
        return

    print(f"{'freq Hz':>10}  {'counts':>8}  {'x floor':>8}  {'dwell':>8}   "
          f"cost at {TAXELS} taxels")
    for m, f in peaks[:top]:
        # A boxcar of N samples spread over T has its first null at 1/T, so the
        # DWELL is pinned at 1/f no matter how many samples fill it. Only the
        # frequency decides whether the notch is affordable.
        us = 1e6 / f
        frame_ms = TAXELS * (SETTLE_US + us) / 1000.0
        if frame_ms <= 40:
            cost = f"{1000.0/frame_ms:5.0f} fps  reachable"
        elif frame_ms <= 200:
            cost = f"{1000.0/frame_ms:5.1f} fps  expensive"
        else:
            cost = f"{frame_ms:7.0f} ms  NOT reachable"
        print(f"{f:10.1f}  {m:8.3f}  {m/floor:8.1f}  {us:7.0f}us   {cost}")

    print()
    reachable = [(m, f) for m, f in peaks
                 if TAXELS * (SETTLE_US + 1e6 / f) / 1000.0 <= 40]
    if reachable:
        m, f = reachable[0]
        us = 1e6 / f
        best = None
        for ovs in (2, 4, 8, 16):
            sp = us / ovs
            if 1.0 <= sp <= 200.0:
                best = (ovs, sp)
                break
        if best:
            print(f"Strongest REACHABLE line is {f:.0f} Hz. Null it with:")
            print(f"    o ovs {best[0]}")
            print(f"    o spread {best[1]:.0f}")
            print(f"which makes the dwell {us:.0f} us and puts a boxcar null on "
                  f"{f:.0f} Hz and its harmonics.")
    else:
        print("None of these lines can be nulled in the dwell. That is the normal")
        print("answer below ~20 kHz: the dwell would have to be 1/f, and at "
              f"{TAXELS} taxels")
        print("anything longer than about 80 us per taxel destroys the frame rate.")
        print("What is left, in order of what actually helps:")
        print("  - raise `o ovs` for the sqrt(N) white-noise reduction it does buy")
        print("  - lean on the temporal filters (median + one-euro) after aliasing")
        print("  - shield the mat and the FFC tails, which is the real fix")

    strongest = peaks[0][1]
    fps = 1e6 / FRAME_US
    alias = abs(strongest - round(strongest / fps) * fps)
    print()
    print(f"Aliasing: each taxel is sampled once per frame, so at {fps:.0f} Hz the")
    print(f"{strongest:.0f} Hz line folds down to about {alias:.2f} Hz in the map.")
    if alias < 0.5:
        print("That is essentially DC. This line is close to an exact multiple of the")
        print("frame rate, so it does not shimmer - it appears as a STEADY offset,")
        print("indistinguishable from a light press that never goes away. It is the")
        print("worst case, and no temporal filter can touch it: nudge the frame")
        print(f"period ('o period {int(FRAME_US * 1.07)}') so it aliases somewhere visible,")
        print("or null it in the dwell as above.")
    else:
        print("That is the rate at which a phantom patch appears to breathe.")

    # A crude ASCII spectrum: enough to see whether it is one line or a forest.
    print()
    W, H = 72, 14
    step = max(1, (len(mag) - lo) // W)
    cols = []
    for i in range(W):
        s = lo + i * step
        cols.append(max(mag[s:s + step] or [0]))
    top_v = max(cols) or 1
    for row in range(H, 0, -1):
        line = "".join("#" if c / top_v * H >= row else " " for c in cols)
        print(f"|{line}|")
    print("+" + "-" * W + "+")
    print(f" {freqs[lo]:.0f} Hz" + " " * (W - 16) + f"{freqs[lo + W*step-1]:.0f} Hz")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--file")
    ap.add_argument("--row", type=int, default=0)
    ap.add_argument("--col", type=int, default=0)
    ap.add_argument("--n", type=int, default=2048)
    ap.add_argument("--khz", type=int, default=48, help="ADC sample rate")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--taxels", type=int, default=512,
                    help="rows*cols in service, for the dwell cost estimate")
    ap.add_argument("--settle", type=int, default=15)
    ap.add_argument("--period", type=int, default=12500,
                    help="frame period in us, for the alias estimate")
    ap.add_argument("--save", help="write the raw capture here as well")
    args = ap.parse_args()

    if args.file:
        text = open(args.file, encoding="utf-8", errors="replace").read()
    elif args.port:
        text = from_serial(args.port, args.row, args.col, args.n, args.khz)
        if args.save:
            open(args.save, "w", encoding="utf-8").write(text)
    else:
        sys.exit("give --port or --file")

    fs, vals, meta = parse(text)
    freqs, mag, n, mean = spectrum(vals, fs)
    report(freqs, mag, n, mean, fs, vals, meta, args.top,
           args.taxels, args.settle, args.period)


if __name__ == "__main__":
    main()
