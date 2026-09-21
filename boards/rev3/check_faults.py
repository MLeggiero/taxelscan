#!/usr/bin/env python3
"""Break gen_rev3.py on purpose, twenty-one ways, and confirm it notices.

A check that has never failed is a comment. This runs gen_rev3.py's own
check() against deliberately damaged copies of the generator - each one a
mistake that could plausibly be made and would be expensive to find on
hardware - and reports which check caught it.

    ./check_faults.py

Nothing is written to the repository; the damaged copies live in a temp
directory and the real rev3.net is never touched. The table in README.md is
this output.
"""
import importlib.util
import os
import shutil
import sys
import tempfile

# Same path + same second + same size = Python serves a stale .pyc and the
# harness silently grades the previous fault. Give every probe its own name
# and write no bytecode at all.
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "gen_rev3.py")

# label, text to find in gen_rev3.py, what to replace it with
FAULTS = [
    ("USB feed bypasses the reverse barrier",
     'n("USB_BUS_SW", "U14", p("U14", "OUT"))',
     'n("+5V_BUS", "U14", p("U14", "OUT"))'),
    ("USB feed defaults ON",
     'n("USB_BUS_EN", "U14", p("U14", "EN"))',
     'n("+3.3V", "U14", p("U14", "EN"))'),
    ("USB-C controller silently changed to old GPIO variant",
     '"TUSB320LAIRWBR",', '"TUSB320IRWBR",'),
    ("CC1 has an extra pulldown in parallel with internal Rd",
     'n("USB_CC1", "U13", p("U13", "CC1"))',
     'n("USB_CC1", "U13", p("U13", "CC1"))\n    n("USB_CC1", "R30", "1")'),
    ("USB-C controller changed from sink to source",
     'for pin in ("PORT", "~{EN}", "GND"):',
     'for pin in ("~{EN}", "GND"):'),
    ("USB power current-limit resistor misvalued",
     '("R27,R28", "Device", "R", "82.5k",',
     '("R27,R28", "Device", "R", "8.25k",'),
    ("USB fault reporting removed",
     'n("USB_PWR_FAULT", "U14", p("U14", "~{FAULT}"))',
     '# fault reporting omitted'),
    ("the USB TVS put on the MCU side of the series resistors",
     'n("USBC_D_P", "D5", p("D5", "I/O1", i))',
     'n("USB_D_P", "D5", p("D5", "I/O1", i))'),
    ("the hot-plug damper's resistor shorted out",
     'n("USB_SNUB", "C46", "1")',
     'n("+5V_USB", "C46", "1")'),
    ("nothing injected (must pass)", None, None),

    ("the op-amp left as a unity-gain follower",
     'n("GAIN_%s" % bank, "U7", p("U7", "-", unit))',
     'n("AMP_%s" % bank, "U7", p("U7", "-", unit))'),

    ("feedback and gain-set resistors swapped",
     '(("A", "R6", "R7"), ("B", "R8", "R9"))',
     '(("A", "R7", "R6"), ("B", "R9", "R8"))'),

    ("VREF tied to +3.3V instead of tapped off ROW_VCC",
     'n("ROW_VCC", "R10", "1")',
     'n("+3.3V", "R10", "1")'),

    ("the two ADC channels crossed",
     'n("ADC_A", "U8", p("U8", "CH0"))\n    n("ADC_B", "U8", p("U8", "CH1"))',
     'n("ADC_B", "U8", p("U8", "CH0"))\n    n("ADC_A", "U8", p("U8", "CH1"))'),

    ("the ADC's charge reservoir shorted out by the amplifier",
     'n("ADC_%s" % bank, rs, "2")',
     'n("AMP_%s" % bank, rs, "2")'),

    ("a rail left with bulk but no high-frequency decoupling",
     'for ref in ("C24", "C25", "C35"):',
     'for ref in ():'),

    ("a column moved to the other mux, breaking the splitter",
     '    # 3. The TLV9062 stops being a follower',
     '    nets["COL_16"] = [(("U5" if r == "U6" else r), q)\n'
     '                      for r, q in nets["COL_16"]]\n'
     '    # 3. The TLV9062 stops being a follower'),

    ("a column dropped from the mux",
     '    # 2. ONE ground.',
     '    del nets["COL_31"]\n    # 2. ONE ground.'),

    ("the data transceiver's DE and ~RE split onto two nets",
     'n("BUS_DE", "U10", p("U10", "~{RE}"))',
     'n("BUS_RE", "U10", p("U10", "~{RE}"))'),

    ("the sync driver's pull-down removed, so a blank board can drive the pair",
     'n("GND", "R37", "2")',
     'n("SYNC_DE", "R37", "2")'),

    ("the sync driver's data input taken off GND",
     'n("GND", "U11", p("U11", "DI"))',
     'n("SYNC_DE", "U11", p("U11", "DI"))'),

    ("the harness data pair crossed between the two connectors",
     '((1, "+5V_BUS"), (2, "GND"), (3, "BUS_P"),\n'
     '                         (4, "BUS_N"), (5, "SYNC_P"), (6, "SYNC_N"))',
     '((1, "+5V_BUS"), (2, "GND"),\n'
     '                         (3, "BUS_P" if conn == "J3" else "BUS_N"),\n'
     '                         (4, "BUS_N" if conn == "J3" else "BUS_P"),\n'
     '                         (5, "SYNC_P"), (6, "SYNC_N"))'),

    ("USB VBUS taken straight to +5V, bypassing its diode",
     'n("+5V_USB", "J5", p("J5", "VBUS", i))',
     'n("+5V", "J5", p("J5", "VBUS", i))'),

    ("one MCU supply pin left off the plane",
     'for i in range(len(sym(*SYMOF["U9"])[name])):',
     'for i in range(len(sym(*SYMOF["U9"])[name]) - 1):'),

    ("a mux select moved off the contiguous run",
     '"MUX_S2":        6,',
     '"MUX_S2":        12,'),

    ("the core rail fed from +3.3V instead of the internal regulator",
     'n("VCORE", "L1", "1")',
     'n("+3.3V", "L1", "1")'),

    ("the core-regulator inductor turned round (dot on the switch node)",
     'n("VCORE", "L1", "1")\n    n("VREG_LX", "L1", "2")',
     'n("VCORE", "L1", "2")\n    n("VREG_LX", "L1", "1")'),

    ("a QSPI pin wired, though it is bonded to the stacked flash",
     'n("BOOTSEL", "R25", "1")',
     'n("QSPI0", "U9", p("U9", "QSPI_SD0"))\n    n("BOOTSEL", "R25", "1")'),

    ("the USB series resistors shorted out by the connector net",
     'n("USBC_D_P", "J5", p("J5", "D+", i))',
     'n("USB_D_P", "J5", p("J5", "D+", i))'),

    ("the BOOTSEL switch hung straight off ~QSPI_SS",
     'n("BOOTSEL_SW", "SW1", "1")',
     'n("BOOTSEL", "SW1", "1")'),

    ("ADC_AVDD tied back to +3.3V, bypassing its filter",
     'n("ADC_AVDD", "R26", "2")',
     'n("+3.3V", "R26", "2")'),

    ("one LCSC number used for two different values",
     '"Resistor_SMD:R_0402_1005Metric", "C852624",\n     "BOOTSEL series.',
     '"Resistor_SMD:R_0402_1005Metric", "C705629",\n     "BOOTSEL series.'),

    ("the buck's FB tied to its output instead of a divider tap",
     'n("FB", "U12", p("U12", "FB"))',
     'n("+3.3V", "U12", p("U12", "FB"))'),

    ("a connector shell left floating",
     '("J1", "J2", "J3", "J4"):\n        n("GND", conn, p(conn, "MountPin"))',
     '("J1", "J2", "J3"):\n        n("GND", conn, p(conn, "MountPin"))'),

    ("a footprint name that is not in any library",
     '"Package_SO:MSOP-10_3x3mm_P0.5mm", "C580457",',
     '"Package_SO:MSOP-10_3x3mm_P0.9mm", "C580457",'),

    ("a pin name misspelt",
     'p("U8", "V_{REF}")',
     'p("U8", "V_{REFF}")'),
]


def run(src, path):
    open(path, "w", encoding="utf-8").write(src)
    spec = importlib.util.spec_from_file_location("rev3_probe", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        rev1 = mod.load_rev1()
        nets = mod.build(rev1)
        return sorted(set(mod.check(nets, rev1)))
    except SystemExit as exc:
        return ["generator refused to run: " + str(exc).splitlines()[0]]
    except Exception as exc:                       # a crash is not a check
        return ["!! %s: %s" % (type(exc).__name__, exc)]


def main():
    base = open(GEN, encoding="utf-8").read()
    # The copies run from a temp directory, so give them an absolute path to
    # the rev-1 netlist they derive from and to their own output files.
    tmp = tempfile.mkdtemp(prefix="rev3check")
    # A probe runs from a temp directory, so every path the generator derives
    # from HERE - the rev-1 netlist, and the local footprint library - would
    # point into that temp directory and silently fail to resolve. Pinning HERE
    # itself fixes all of them at once. The probe only calls load/build/check,
    # never an emitter, so nothing is written back to the repository.
    base = base.replace(
        'HERE = os.path.dirname(os.path.abspath(__file__))',
        'HERE = r"%s"' % HERE.replace("\\", "/"))

    bad = 0
    for i, (label, old, new) in enumerate(FAULTS):
        probe = os.path.join(tmp, "probe%02d.py" % i)
        src = base
        if old is not None:
            if old not in src:
                print("  %-58s SITE GONE - update this file" % label)
                bad += 1
                continue
            src = src.replace(old, new, 1)
        fails = run(src, probe)
        crashed = any(f.startswith("!!") for f in fails)
        if old is None:
            ok = not fails
        else:
            ok = bool(fails) and not crashed
        bad += not ok
        print("  %-58s %s" % (label, "pass" if old is None and ok else
                              "caught" if ok else "*** NOT CAUGHT ***"))
        if fails and old is not None:
            print("      %s" % fails[0][:104])
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d of %d behaved as intended" % (len(FAULTS) - bad, len(FAULTS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
