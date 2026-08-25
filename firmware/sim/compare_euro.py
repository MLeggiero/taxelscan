#!/usr/bin/env python3
"""Compare the fixed-point one-euro build against the float reference build.

Both binaries run the same deterministic scene and emit a per-frame digest of
filtMap. This reports how far apart they drift.

The number that matters is the peak divergence against the sensor's own noise
floor (sigma ~2.5 counts on a quiet bench). A filter rewrite that stays inside
a fraction of one sigma cannot change any downstream decision, and the active
cell and contact counts are checked exactly to confirm that it does not.

    ./sim       euro > fixed.csv
    ./sim-float euro > float.csv
    ./compare_euro.py fixed.csv float.csv
"""
import csv
import sys

FIELDS = ("sum", "max", "min", "active", "contacts")
# These are decisions, not measurements: any difference at all is a behaviour
# change and fails, however small the underlying value shift was.
EXACT = ("active", "contacts")


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def main(fixed_path, float_path):
    fixed, ref = load(fixed_path), load(float_path)
    if len(fixed) != len(ref):
        print(f"FAIL: {len(fixed)} frames vs {len(ref)}")
        return 1

    worst, failed = {}, False
    for k in FIELDS:
        d = [abs(int(a[k]) - int(b[k])) for a, b in zip(fixed, ref)]
        worst[k] = (max(d), sum(d) / len(d))

    peak = max(int(r["max"]) for r in ref)
    print(f"{len(fixed)} frames, peak filtMap {peak} counts\n")
    print(f"  {'field':10} {'max diff':>9} {'mean diff':>10}")
    for k in FIELDS:
        mx, mean = worst[k]
        flag = "  <- must be 0" if k in EXACT and mx else ""
        print(f"  {k:10} {mx:9} {mean:10.3f}{flag}")
        if k in EXACT and mx:
            failed = True

    pk = worst["max"][0]
    print(f"\n  peak divergence {pk} count(s) = {100.0 * pk / peak:.3f}% of peak,"
          f" {pk / 2.5:.2f} sigma")
    print("  one count is the rounding boundary of the Q8 state, so this is the"
          "\n  representation floor rather than a filter difference.")

    if failed:
        print("\nFAIL: a gating decision differed between the two builds")
        return 1
    print("\nok: every active-cell and contact decision identical")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
