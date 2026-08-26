#!/usr/bin/env python3
"""Compare two per-frame digests of the conditioning pipeline.

Both modes read the CSV that `sim euro` emits - one row per frame of a
deterministic scene, carrying the filtMap sum, max and min plus the active-cell
and contact counts.

    compare_runs.py a.csv b.csv        tolerant: a numeric change is allowed,
                                       a change in a gating DECISION is not
    compare_runs.py --exact a.csv b.csv   nothing may differ at all

Which mode to use follows from what the change was supposed to do.

A change to the ARITHMETIC - a different filter, a different number format -
moves values slightly by construction, so demanding equality would only ever
produce a false alarm. What must not move is any decision downstream of those
values: the active-cell and contact counts are checked exactly, and the value
drift is reported against the sensor's own noise floor (sigma ~2.5 counts on a
quiet bench) so it can be judged rather than guessed at.

A change to the REPRESENTATION - packing activity into bitmaps, reindexing an
array, splitting a loop - is supposed to compute exactly what it computed
before. There --exact is the honest test, and it is a much sharper one: it
catches an off-by-one at an array edge that a tolerant comparison, or a run of
pass/fail verdicts, would sail straight past.
"""
import csv
import sys

FIELDS = ("sum", "max", "min", "active", "contacts")
# Decisions rather than measurements: any difference is a behaviour change.
DECISIONS = ("active", "contacts")
SIGMA = 2.5


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def main(argv):
    exact = "--exact" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if len(paths) != 2:
        print(__doc__)
        return 2
    got, ref = load(paths[0]), load(paths[1])

    if len(got) != len(ref):
        print(f"FAIL: {len(got)} frames vs {len(ref)}")
        return 1

    worst = {}
    for k in FIELDS:
        d = [abs(int(a[k]) - int(b[k])) for a, b in zip(got, ref)]
        worst[k] = (max(d), sum(d) / len(d))

    if exact:
        bad = [k for k in FIELDS if worst[k][0]]
        if not bad:
            print(f"ok: {len(got)} frames identical on every field")
            return 0
        print(f"FAIL: a representation change altered the output\n")
        for k in bad:
            first = next(i for i, (a, b) in enumerate(zip(got, ref))
                         if int(a[k]) != int(b[k]))
            print(f"  {k:9} first differs at frame {first}: "
                  f"{got[first][k]} vs {ref[first][k]} (max diff {worst[k][0]})")
        return 1

    peak = max(int(r["max"]) for r in ref)
    print(f"{len(got)} frames, peak filtMap {peak} counts\n")
    print(f"  {'field':10} {'max diff':>9} {'mean diff':>10}")
    failed = False
    for k in FIELDS:
        mx, mean = worst[k]
        flag = "  <- must be 0" if k in DECISIONS and mx else ""
        print(f"  {k:10} {mx:9} {mean:10.3f}{flag}")
        failed |= bool(k in DECISIONS and mx)

    pk = worst["max"][0]
    print(f"\n  peak divergence {pk} count(s) = {100.0 * pk / peak:.3f}% of peak,"
          f" {pk / SIGMA:.2f} sigma")

    if failed:
        print("\nFAIL: a gating decision differed")
        return 1
    print("\nok: every active-cell and contact decision identical")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
