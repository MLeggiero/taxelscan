#!/usr/bin/env python3
"""Break gen_hub.py on purpose, thirteen ways, and confirm it notices.

A check that has never failed is a comment. This runs gen_hub.py's own check()
against deliberately damaged copies of the generator - each one a mistake that
could plausibly be made and would be expensive to find on hardware - and
reports which check caught it.

    ./check_faults.py

Nothing is written to the repository; the damaged copy lives in a temp file and
the real hub.net is never touched. The table in README.md is this output.
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
GEN = os.path.join(HERE, "gen_hub.py")

# label, text to find in gen_hub.py, what to replace it with
FAULTS = [
    ("nothing injected (must pass)", None, None),

    ("SENSE_A and SENSE_B swapped in the cable map",
     '"SENSE_A_OUT": "M{k}_SENSE_A", "SENSE_B_OUT": "M{k}_SENSE_B",',
     '"SENSE_A_OUT": "M{k}_SENSE_B", "SENSE_B_OUT": "M{k}_SENSE_A",'),

    ("two mux selects swapped in the cable map",
     '"MUX_S0": "M{k}_MUX_S0", "MUX_S1": "M{k}_MUX_S1",',
     '"MUX_S0": "M{k}_MUX_S1", "MUX_S1": "M{k}_MUX_S0",'),

    ("two buffer outputs crossed",
     'BUF_OUT = ["1Y0", "1Y1", "1Y2", "1Y3", "2Y0", "2Y1", "2Y2"]',
     'BUF_OUT = ["1Y1", "1Y0", "1Y2", "1Y3", "2Y0", "2Y1", "2Y2"]'),

    ("a sense line moved to a pin with no ADC on it",
     'ADC3_SENSE_B = ["PF3",', 'ADC3_SENSE_B = ["PE12",'),

    ("bank B put on ADC2 pins, which cannot hold eight",
     'ADC3_SENSE_B = ["PF3", "PF4", "PF5", "PF6", "PF7", "PF8", "PF9", "PF10"]',
     'ADC3_SENSE_B = ["PF11", "PF12", "PF14", "PC1", "PF3", "PF4", "PF5", "PF6"]'),

    ("a module enable landed on a mux select pin",
     'MOD_EN = ["PD%d" % k for k in range(NMOD)]',
     'MOD_EN = ["PE4"] + ["PD%d" % k for k in range(1, NMOD)]'),

    ("one branch taken to the connector unterminated",
     '            n("M%d_%s" % (k, sig), pack, 8 - elem)',
     '            n("M%d_%s" % (k, sig), pack, 8 - elem) if (k or i) else None'),

    ("the two grounds bridged a second time",
     '    n("PWR_GND", "D1", "1")',
     '    res("PWR_GND", "AGND", "0R", R0805, "second join")\n'
     '    n("PWR_GND", "D1", "1")'),

    ("a sense line left without its 1 nF terminator",
     '            cap(net, "AGND", "1nF", C0603,',
     '            (k or bank != "A") and cap(net, "AGND", "1nF", C0603,'),

    ("the analog regulator returned on the digital ground",
     '("U19", "AVCC", "AGND", "8 x 3 mA analog")',
     '("U19", "AVCC", "PWR_GND", "8 x 3 mA analog")'),

    ("one module's rail fed from the LDO, unswitched",
     '        n("M%d_ROW_VCC" % k, sw, p(sw, "OUT%d" % ch))',
     '        n("M%d_ROW_VCC" % k if k else "ROW_VCC", sw, p(sw, "OUT%d" % ch))'),

    ("an MCU ground pin left off the plane",
     '    for i in range(9):\n        mcu("PWR_GND", "VSS", i)',
     '    for i in range(8):\n        mcu("PWR_GND", "VSS", i)'),

    ("a pin name misspelt",
     'n("PHY_RBIAS", "U2", p("U2", "RBIAS"))',
     'n("PHY_RBIAS", "U2", p("U2", "RBAIS"))'),
]


def run(src, path, bom):
    open(path, "w", encoding="utf-8").write(src)
    spec = importlib.util.spec_from_file_location("hub_probe", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        nets, pas = mod.build()
        return sorted(set(mod.check(nets, pas, mod.emit_bom(pas, bom))))
    except SystemExit as exc:
        return ["generator refused to run: " + str(exc).splitlines()[0]]
    except Exception as exc:                       # a crash is not a check
        return ["!! %s: %s" % (type(exc).__name__, exc)]


def main():
    base = open(GEN, encoding="utf-8").read()
    # The copy runs from a temp directory, so give it an absolute path to the
    # module netlist it checks itself against.
    base = base.replace(
        'MODULE_NET = os.path.join(HERE, "..", "v2-module", "module.net")',
        'MODULE_NET = r"%s"' % os.path.join(HERE, "..", "v2-module",
                                            "module.net").replace("\\", "/"))
    tmp = tempfile.mkdtemp(prefix="hubcheck")
    bom = os.path.join(tmp, "bom.csv")
    bad = 0
    for i, (label, old, new) in enumerate(FAULTS):
        probe = os.path.join(tmp, "probe%02d.py" % i)
        src = base
        if old is not None:
            if old not in src:
                print("  %-52s SITE GONE - update this file" % label)
                bad += 1
                continue
            src = src.replace(old, new, 1)
        fails = run(src, probe, bom)
        crashed = any(f.startswith("!!") for f in fails)
        if old is None:
            ok = not fails
        else:
            ok = bool(fails) and not crashed
        bad += not ok
        print("  %-52s %s" % (label, "pass" if old is None and ok else
                              "caught" if ok else "*** NOT CAUGHT ***"))
        if fails and old is not None:
            print("      %s" % fails[0][:100])
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d of %d behaved as intended" % (len(FAULTS) - bad, len(FAULTS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
