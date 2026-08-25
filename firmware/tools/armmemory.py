#!/usr/bin/env python3
"""Per-taxel RAM cost of the conditioning pipeline, read off the ARM object.

Hand-counting struct sizes is how you miss a 2 KB scratch buffer hidden inside
a function, or credit a saving to an array that was never per-taxel. This reads
the sizes out of the compiled object and classifies each array by how it was
declared, so the total is what the linker will actually allocate.

    ./armmemory.py [sensors]        default 8

The distinction that matters is MAX_TAXELS versus MAX_LABELS. Arrays indexed by
label are sized by the label ceiling and stay flat as sensors are added, as
long as connected components runs per sensor rather than across the whole
array - so they must not be folded into a per-taxel figure.

Needs arm-none-eabi-g++ (Debian/Ubuntu: apt install gcc-arm-none-eabi).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "../taxelscan/condition.cpp")
OBJ = "/tmp/taxel-mem.o"

# scan.cpp needs Pico SDK headers the sim does not shim, so its three frame
# buffers are named here rather than measured. They are unambiguous:
#   rawFrame uint16, drFrame int16, drBuf[2] int16
SCAN_BYTES_PER_TAXEL = 2 + 2 + 4

# STM32H743: 1 MB of SRAM, but not contiguous. The per-taxel state has to live
# in one region, and AXI SRAM is the largest.
AXI_SRAM_KB = 512


def build():
    subprocess.run(
        ["arm-none-eabi-g++", "-O2", "-std=c++17", "-mthumb", "-mcpu=cortex-m7",
         "-mfloat-abi=hard", "-mfpu=fpv5-d16",
         "-I" + os.path.join(HERE, "../sim/shim"),
         "-I" + os.path.join(HERE, "../taxelscan"),
         "-c", SRC, "-o", OBJ], check=True)


def symbols():
    out = subprocess.run(
        ["arm-none-eabi-nm", "--print-size", "--size-sort", "--radix=d", OBJ],
        capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 4 and p[2].lower() in "bd":
            # Strip the C++ mangling prefixes for file-static and function-local
            # statics so the name matches the source declaration.
            name = re.sub(r"^\d+", "", re.sub(r"^_ZL\d+|^_ZZ.*?E\d+", "", p[3]))
            yield name, int(p[1])


def main():
    sensors = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    src = open(SRC).read()
    per_taxel = set(re.findall(r"\b(\w+)\s*\[(?:\d+\]\[)?MAX_TAXELS\]", src))
    per_label = set(re.findall(r"\b(\w+)\s*\[MAX_LABELS\]", src))

    # MAX_TAXELS as the object was actually compiled.
    scan_h = open(os.path.join(HERE, "../taxelscan/scan.h")).read()
    rows = int(re.search(r"MAX_ROWS\s*=\s*(\d+)", scan_h).group(1))
    chans = int(re.search(r"MAX_CHANS\s*=\s*(\d+)", scan_h).group(1))
    banks = int(re.search(r"MAX_BANKS\s*=\s*(\d+)", scan_h).group(1))
    taxels = rows * chans * banks

    build()
    cond = lab = other = 0
    detail = []
    for name, size in symbols():
        if name in per_taxel:
            cond += size
            detail.append((size / taxels, name, size))
        elif name in per_label:
            lab += size
        else:
            other += size

    detail.sort(reverse=True)
    print(f"condition.cpp compiled at MAX_TAXELS = {taxels} "
          f"({rows} x {chans} x {banks})\n")
    print(f"  {'array':12} {'bytes':>7} {'B/taxel':>8}")
    for pt, name, size in detail:
        print(f"  {name:12} {size:7} {pt:8.2f}")

    per = cond / taxels + SCAN_BYTES_PER_TAXEL
    print(f"\n  condition.cpp per-taxel   {cond / taxels:6.2f} B/taxel"
          f"  ({len(detail)} arrays)")
    print(f"  scan.cpp per-taxel        {SCAN_BYTES_PER_TAXEL:6.2f} B/taxel"
          f"  (rawFrame + drFrame + drBuf[2])")
    print(f"  TOTAL                     {per:6.2f} B/taxel")
    print(f"\n  label-indexed arrays      {lab:6d} B flat"
          f"  (MAX_LABELS; flat only while CCL is per-sensor)")
    print(f"  other statics             {other:6d} B flat")

    total_kb = per * taxels * sensors / 1024
    print(f"\n  {sensors} sensors x {taxels} taxels = {taxels * sensors} taxels"
          f"  ->  {total_kb:.1f} KB")
    spare = AXI_SRAM_KB - total_kb
    verdict = "FITS" if spare > 0 else "DOES NOT FIT"
    print(f"  against {AXI_SRAM_KB} KB contiguous AXI SRAM (STM32H743): "
          f"{verdict}, {spare:.0f} KB spare")


if __name__ == "__main__":
    main()
