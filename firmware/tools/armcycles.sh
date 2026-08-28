#!/bin/bash
# What does a pipeline stage actually cost on the target core?
#
# x86 timings from sim/ answer "did this change the behaviour". They do not
# answer "is this faster on the MCU": x86 has a fast pipelined FP divider and
# Cortex-M does not, so a change that looks free on a PC can be a pessimisation
# on the board. This cross-compiles condition.cpp for a real Cortex-M, maps
# every instruction back to its source line with DWARF, and totals the lines
# that make up one stage.
#
#   ./armcycles.sh                       one-euro, float vs fixed, M33 and M7
#   ./armcycles.sh <lo> <hi> [<lo2> <hi2>]   any other source line range
#
# Needs arm-none-eabi-g++ (Debian/Ubuntu: apt install gcc-arm-none-eabi).
#
# Cycle figures weight the divides by their ARM TRM costs and everything else
# at 1, which is rough but enough to rank two implementations of one stage. It
# does not model dual-issue, cache or memory stalls, so treat the numbers as a
# comparison between builds, never as an absolute frame budget.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
FW="$HERE/.."
command -v arm-none-eabi-g++ >/dev/null || {
  echo "arm-none-eabi-g++ not found; install gcc-arm-none-eabi" >&2; exit 1; }

build() {  # <cpu> <fixed-flag>
  local cpu=$1 fixed=$2 fpu
  [ "$cpu" = cortex-m33 ] && fpu=fpv5-sp-d16 || fpu=fpv5-d16
  arm-none-eabi-g++ -O2 -g -std=c++17 -mthumb -mcpu="$cpu" -mfloat-abi=hard \
    -mfpu=$fpu -I"$FW/sim/shim" -I"$FW/taxelscan" -DTAXEL_EURO_FIXED=$fixed \
    -c "$FW/taxelscan/condition.cpp" -o "/tmp/ac-$cpu-$fixed.o"
  arm-none-eabi-objdump -dl "/tmp/ac-$cpu-$fixed.o" > "/tmp/ac-$cpu-$fixed.asm"
}

for cpu in cortex-m33 cortex-m7; do for f in 0 1; do build $cpu $f; done; done

RANGES_ARG="$*"
python3 - "$RANGES_ARG" <<'PY'
import re, sys, collections

def parse(path):
    cur, per = None, collections.defaultdict(collections.Counter)
    for line in open(path):
        m = re.match(r'.*condition\.cpp:(\d+)', line)
        if m:
            cur = int(m.group(1)); continue
        m = re.match(r'\s+[0-9a-f]+:\s+[0-9a-f ]+\s+(\S+)', line)
        if m and cur:
            per[cur][m.group(1)] += 1
    return per

def find(pat, path='../taxelscan/condition.cpp'):
    import os
    p = os.path.join(os.path.dirname(__file__) if '__file__' in dir() else '.', path)
    for i, l in enumerate(open(p), 1):
        if pat in l:
            return i
    return None

arg = sys.argv[1].split() if len(sys.argv) > 1 and sys.argv[1].strip() else []
if arg:
    nums = [int(x) for x in arg]
    RANGES = {0: list(zip(nums[::2], nums[1::2])), 1: list(zip(nums[::2], nums[1::2]))}
    LABEL = f"source lines {nums}"
else:
    # stage 6 body plus the alpha helper it inlines, located by marker so the
    # ranges survive edits to the file above them.
    import os
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '../taxelscan/condition.cpp')
    lines = open(src).read().splitlines()
    def at(pat, start=0):
        for i in range(start, len(lines)):
            if pat in lines[i]:
                return i + 1
        raise SystemExit(f"marker not found: {pat}")
    s6 = at('---- stage 6: one-euro')
    s7 = at('---- stage 7 + 8')
    fl = at('static inline float alphaFor')
    fx = at('static inline uint32_t alphaQ16')
    RANGES = {0: [(s6, s7), (fl, fl + 4)], 1: [(s6, s7), (fx, fx + 4)]}
    LABEL = "one-euro (stage 6 + inlined alpha helper)"

COST = {'vdiv.f32': 14, 'udiv': 10, 'sdiv': 10, 'vsqrt.f32': 14}
print(f"{LABEL}\n")
for cpu in ('cortex-m33', 'cortex-m7'):
    print(f"=== {cpu} ===")
    for f in (0, 1):
        per = parse(f'/tmp/ac-{cpu}-{f}.asm')
        o = collections.Counter()
        for lo, hi in RANGES[f]:
            for k in range(lo, hi + 1):
                o.update(per[k])
        n = sum(o.values())
        cyc = sum(COST.get(k, 1) * v for k, v in o.items())
        g = lambda p: sum(v for k, v in o.items() if re.match(p, k))
        print(f"  {'float' if f == 0 else 'fixed':6} insns {n:4}"
              f"  vdiv {g(r'vdiv')}  float-ops {g(r'v(add|sub|mul|cvt|abs|neg|cmp|ldr|str|mov|fma)'):3}"
              f"  int-div {g(r'[su]div$')}  64mul {g(r'(um|sm)(ull|lal)$')}"
              f"   ~{cyc} cycles")
PY
