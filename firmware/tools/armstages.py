#!/usr/bin/env python3
"""Per-stage cost of condProcess() on the target core.

Answers the question that decides whether the 8-sensor build is feasible:
of the 516 cycles per taxel measured on hardware, which stage owns them?

Method is the same as tools/armcycles.sh - cross-compile condition.cpp for a
real Cortex-M, map every instruction back to its source line with DWARF - but
grouped by the stage-marker comments in condProcess() instead of one stage.

    ./armstages.py [cortex-m7|cortex-m33]

Two things this does NOT model, both of which matter for absolute numbers and
neither of which affects the ranking between stages: it counts instructions
rather than simulating the pipeline (no dual-issue, no cache, no memory
stalls), and it counts each instruction once regardless of how many times its
loop actually runs. Per-taxel stages all run nrow*ncol times, so they compare
directly with each other; the per-frame setup does not, and is marked as such.
"""
import collections
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "../taxelscan/condition.cpp")

# (marker substring, label, runs-per-taxel?)
STAGES = [
    ("---- stage 4: temporal median",     "4  median-of-3",          True),
    ("---- per-frame rate budgets",       "   per-frame setup",      False),
    ("---- stage 5: adaptive baseline",   "5  adaptive baseline",    True),
    ("---- stage 6: one-euro",            "6  one-euro",             True),
    ("---- stage 7 + 8: sigma",           "7+8 threshold/debounce",  True),
    ("---- stage 9: isolated-speck",      "9  despeckle",            True),
    ("---- stage 10: connected",          "10 connected components", True),
    ("---- accept / reject",              "   contact list",         False),
    ("---- release eligibility",          "   release eligibility",  True),
    ("---- output map",                   "   output map",           True),
]
COST = {"vdiv.f32": 14, "udiv": 10, "sdiv": 10, "vsqrt.f32": 14}


def build(cpu):
    fpu = "fpv5-sp-d16" if cpu == "cortex-m33" else "fpv5-d16"
    obj, asm = f"/tmp/as-{cpu}.o", f"/tmp/as-{cpu}.asm"
    subprocess.run(
        ["arm-none-eabi-g++", "-O2", "-g", "-std=c++17", "-mthumb",
         f"-mcpu={cpu}", "-mfloat-abi=hard", f"-mfpu={fpu}",
         "-I" + os.path.join(HERE, "../sim/shim"),
         "-I" + os.path.join(HERE, "../taxelscan"),
         "-c", SRC, "-o", obj], check=True)
    with open(asm, "w") as f:
        subprocess.run(["arm-none-eabi-objdump", "-dl", obj], stdout=f, check=True)
    return asm


def attribute(asm):
    """instructions per source line, restricted to condProcess()."""
    cur, infn, per = None, False, collections.defaultdict(collections.Counter)
    for line in open(asm):
        if re.match(r"^[0-9a-f]+ <", line):
            infn = "condProcess" in line
        m = re.match(r".*condition\.cpp:(\d+)", line)
        if m:
            cur = int(m.group(1))
            continue
        m = re.match(r"\s+[0-9a-f]+:\s+[0-9a-f ]+\s+(\S+)", line)
        if m and cur and infn:
            per[cur][m.group(1)] += 1
    return per


def main():
    cpu = sys.argv[1] if len(sys.argv) > 1 else "cortex-m7"
    lines = open(SRC).read().splitlines()

    bounds = []
    for marker, label, per_taxel in STAGES:
        hit = next((i + 1 for i, l in enumerate(lines) if marker in l), None)
        if hit is None:
            sys.exit(f"stage marker not found, has condition.cpp moved on?  {marker}")
        bounds.append((hit, label, per_taxel))
    bounds.sort()
    end = next(i + 1 for i, l in enumerate(lines) if l.startswith("int32_t condBaseline"))

    per = attribute(build(cpu))
    rows, total = [], 0
    for n, (start, label, per_taxel) in enumerate(bounds):
        stop = bounds[n + 1][0] - 1 if n + 1 < len(bounds) else end
        o = collections.Counter()
        for k in range(start, stop + 1):
            o.update(per[k])
        cyc = sum(COST.get(k, 1) * v for k, v in o.items())
        rows.append((label, sum(o.values()), cyc, per_taxel))
        if per_taxel:
            total += cyc

    print(f"\ncondProcess() on {cpu}, static instruction cost by stage")
    print(f"{'stage':26} {'insns':>6} {'~cycles':>8} {'share':>7}")
    print("-" * 50)
    for label, n, cyc, per_taxel in rows:
        share = f"{100.0 * cyc / total:5.1f}%" if per_taxel and total else "   n/a"
        print(f"{label:26} {n:6} {cyc:8} {share:>7}")
    print("-" * 50)
    print(f"{'per-taxel total':26} {'':6} {total:8}")
    print("\nShare is of the per-taxel total; the per-frame rows run once a frame,\n"
          "not once a taxel, so they are excluded from it rather than compared.")


if __name__ == "__main__":
    main()
