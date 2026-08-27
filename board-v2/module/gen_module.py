#!/usr/bin/env python3
"""Derive the v2 front-end module's netlist from the rev-1 board's.

The module IS the rev-1 board with the MCU removed and a cable put in its
place, so it is built by transforming rev-1's exported netlist rather than by
redrawing it. That matters for a board whose analog chain is already proven on
hardware: every row driver, mux and buffer connection here is the one that was
measured, not one retyped from a datasheet. Only the deliberate changes are
written out longhand below, which also makes them the diff a reviewer reads.

    ./gen_module.py            writes module.net and BOM.csv, and checks both

Verification is the point. Every claim the plan makes about this circuit is
asserted at the bottom of this file, so a typo in a pin number fails here
rather than at bring-up.
"""
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REV1 = os.path.join(HERE, "../../board/outputs/sch_final.net")

# ---------------------------------------------------------------- rev-1 input
def load_rev1():
    import sexpdata
    d = sexpdata.loads(open(REV1).read())
    def find(node, key):
        return [x for x in node if isinstance(x, list) and x and str(x[0]) == key]
    nets = {}
    for n in find(find(d, "nets")[0], "net"):
        name = str(find(n, "name")[0][1])
        nets[name] = [(str(find(x, "ref")[0][1]), str(find(x, "pin")[0][1]))
                      for x in find(n, "node")]
    return nets


# ---------------------------------------------------------------- the changes
#
# Everything not named here is inherited from rev-1 unchanged.
#
# J3 is the 20-way cable to the hub. Its pinout is not arbitrary: the analog
# pair is flanked by AGND, and PWR_GND at pin 13 separates the analog block
# from the digital one. See README.md for why AGND carries no power current.
J3_PINOUT = [
    (1,  "ROW_VCC"),        (11, "AGND"),
    (2,  "PWR_GND"),        (12, "ROW_VCC_SENSE"),
    (3,  "ROW_VCC"),        (13, "PWR_GND"),
    (4,  "PWR_GND"),        (14, "MUX_S0"),
    (5,  "AVCC"),           (15, "MUX_S1"),
    (6,  "AGND"),           (16, "MUX_S2"),
    (7,  "SENSE_A_OUT"),    (17, "MUX_S3"),
    (8,  "AGND"),           (18, "ROW_DATA"),
    (9,  "SENSE_B_OUT"),    (19, "ROW_CLK_MCU"),
    (10, "AGND"),           (20, "ROW_LATCH_MCU"),
]

def build(rev1):
    nets = {k: list(v) for k, v in rev1.items() if not k.startswith("unconnected-")}

    # 1. The MCU is gone. Its footprint and every A1 node go with it.
    for name in list(nets):
        nets[name] = [(r, p) for r, p in nets[name] if r != "A1"]
    del nets["+5V"]                       # the XIAO's 5V pin was the only node

    # 2. Split the ground. Rev-1 had one GND; here ROW_VCC returns on PWR_GND
    #    and the sense divider returns on AGND, so up to 30 mA of press-
    #    correlated row current stops flowing through the analog reference.
    #    The 595s and their decoupling go to PWR_GND; the pulldowns, the muxes
    #    and the op-amp go to AGND.
    pwr_refs = {"U1", "U2", "U3", "U4", "C1", "C2", "C3", "C4", "C9"}
    gnd = nets.pop("GND")
    nets["PWR_GND"] = [(r, p) for r, p in gnd if r in pwr_refs]
    nets["AGND"]    = [(r, p) for r, p in gnd if r not in pwr_refs]

    # 3. The analog rail is its own net now, fed from the cable rather than
    #    from the same +3.3V node as the row drivers.
    nets["AVCC"] = [(r, p) for r, p in nets.pop("+3.3V") if r != "R5"]

    # 4. R5, the 0R jumper that bridged +3.3V to ROW_VCC, is gone: ROW_VCC
    #    arrives on its own conductors so it can sag independently and be
    #    measured. What replaces it is a Kelvin sense tap at the 595 VCC pins.
    nets["ROW_VCC"] = [(r, p) for r, p in nets["ROW_VCC"] if r != "R5"]
    nets["ROW_VCC"].append(("TP2", "1"))
    nets["ROW_VCC_SENSE"] = [("R8", "1")]
    nets["ROW_VCC"].append(("R8", "2"))       # 0R Kelvin tap, sensed at the pins

    # 5. 51R isolation at each buffer output. The TLV9062 drives 100 pF and a
    #    500 mm cable is 25-50 pF plus the hub input, so this is required for
    #    stability, not decoration. It sits OUTSIDE the feedback loop: the
    #    follower's output and its inverting input stay tied, and the resistor
    #    goes from that node to the cable.
    for bank, rser, tp in (("A", "R6", "TP3"), ("B", "R7", "TP4")):
        drv = nets.pop(f"ADC_{bank}")         # was op-amp out -> XIAO ADC pin
        nets[f"ADC_{bank}"] = drv + [(rser, "1")]
        nets[f"SENSE_{bank}_OUT"] = [(rser, "2"), (tp, "1")]

    # 6. Bulk on ROW_VCC sized for the row-change transient, not the static
    #    drop - the static drop is what ROW_VCC_SENSE corrects. 30 mA settling
    #    in ~5 us within 10 mV needs ~15 uF, so 22 uF. C9 grows from 10 uF and
    #    moves from the analog rail to ROW_VCC.
    nets["AVCC"] = [(r, p) for r, p in nets["AVCC"] if r != "C9"]
    nets["ROW_VCC"].append(("C9", "1"))

    # 7. ROW_DATA gets a test point. On the first assembled rev-1 board this
    #    net was open, and finding that took a long time because every row
    #    reading identically looks like a sensor fault rather than a broken
    #    trace. It is the only signal in the row chain with no series resistor.
    nets["ROW_DATA"].append(("TP1", "1"))

    # 8. Everything the MCU used to drive now arrives on the cable.
    for pin, net in J3_PINOUT:
        nets.setdefault(net, []).append(("J3", str(pin)))

    return {k: sorted(set(v)) for k, v in nets.items() if v}


# ---------------------------------------------------------------- BOM
BOM = [
    ("U1-U4", "SN74LVC595A",  "Package_SO:TSSOP-16_4.4x5mm_P0.65mm", "32 row drivers"),
    ("U5,U6", "CD74HC4067SM", "Package_SO:SSOP-24_5.3x8.2mm_P0.65mm", "32 columns, 2 banks"),
    ("U7",    "TLV9062",      "Package_SO:MSOP-8_3x3mm_P0.65mm",     "dual unity-gain buffer"),
    ("R1,R2", "3.3k",         "Resistor_SMD:R_0603_1608Metric",      "sense pulldowns, to AGND"),
    ("R3,R4", "33R",          "Resistor_SMD:R_0603_1608Metric",      "SRCLK/RCLK damping"),
    ("R6,R7", "51R",          "Resistor_SMD:R_0603_1608Metric",      "NEW: buffer output isolation"),
    ("R8",    "0R",           "Resistor_SMD:R_0603_1608Metric",      "NEW: ROW_VCC Kelvin sense tap"),
    ("C1-C8", "100nF",        "Capacitor_SMD:C_0603_1608Metric",     "per-IC decoupling"),
    ("C9",    "22uF",         "Capacitor_SMD:C_0805_2012Metric",     "CHANGED: bulk on ROW_VCC"),
    ("J1,J2", "FFC_32P_0.5mm","FlexiTac:FFC_0.5mm_FH12_32_16_12way_Composite", "rows, columns"),
    ("J3",    "FFC_20P_0.5mm","Connector_FFC-FPC:Hirose_FH12-20S-0.5SH_1x20-1MP_P0.50mm_Horizontal",
                                                                     "NEW: cable to hub"),
    ("TP1-TP4", "TestPoint",  "TestPoint:TestPoint_Pad_D1.0mm",
                              "NEW: ROW_DATA, ROW_VCC, SENSE_A/B"),
]


def emit_net(nets, path):
    with open(path, "w") as f:
        f.write("(export (version \"E\")\n  (design (source \"gen_module.py\")"
                " (tool \"taxelscan board-v2\"))\n  (nets\n")
        for i, (name, nodes) in enumerate(sorted(nets.items()), 1):
            f.write(f"    (net (code \"{i}\") (name \"{name}\")\n")
            for ref, pin in nodes:
                f.write(f"      (node (ref \"{ref}\") (pin \"{pin}\"))\n")
            f.write("    )\n")
        f.write("  )\n)\n")


def emit_bom(path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Reference", "Value", "Footprint", "Note"])
        w.writerows(BOM)


# ---------------------------------------------------------------- checks
def check(nets, rev1):
    """Assert everything the plan claims about this circuit.

    A pin number retyped wrongly is invisible in a schematic review and
    expensive at bring-up, so each claim is stated as a test here instead.
    """
    fails = []
    def want(cond, msg):
        if not cond:
            fails.append(msg)

    pins = lambda n: set(nets.get(n, []))
    net_of = {}
    for name, nodes in nets.items():
        for node in nodes:
            want(node not in net_of,
                 f"{node[0]}.{node[1]} appears on both {net_of.get(node)} and {name}")
            net_of[node] = name

    # The MCU and its 0R rail jumper are gone; nothing may reference them.
    want(not any(r in ("A1", "R5") for r, _ in net_of), "A1 or R5 still present")

    # The row chain survives intact: 32 driver outputs reach 32 FFC pins.
    rows = [n for n in nets if re.fullmatch(r"ROW_\d+", n)]
    want(len(rows) == 32, f"expected 32 ROW_n nets, got {len(rows)}")
    for n in rows:
        want(any(r == "J1" for r, _ in nets[n]), f"{n} does not reach J1")
        want(any(r.startswith("U") for r, _ in nets[n]), f"{n} has no driver")
    cols = [n for n in nets if re.fullmatch(r"COL_\d+", n)]
    want(len(cols) == 32, f"expected 32 COL_n nets, got {len(cols)}")
    for n in cols:
        want(any(r == "J2" for r, _ in nets[n]), f"{n} does not reach J2")
        want(any(r in ("U5", "U6") for r, _ in nets[n]), f"{n} reaches no mux")

    # Grounds are split, and the split is the whole point: no 595 and no
    # decoupling cap on the row rail may sit on the analog reference.
    want(("U1", "8") in pins("PWR_GND") and ("U1", "8") not in pins("AGND"),
         "595 ground is not on PWR_GND")
    want(("R1", "2") in pins("AGND"), "sense pulldown does not return to AGND")
    want(("U7", "4") in pins("AGND"), "op-amp ground is not on AGND")
    for ref, _ in nets["PWR_GND"]:
        want(ref not in ("R1", "R2", "U5", "U6", "U7"),
             f"{ref} is analog but sits on PWR_GND")

    # Isolation resistors are in series, not in parallel: the op-amp output
    # must NOT appear on the cable net, only through the resistor.
    for bank, rser in (("A", "R6"), ("B", "R7")):
        want((rser, "1") in pins(f"ADC_{bank}"), f"{rser} not on the op-amp side")
        want((rser, "2") in pins(f"SENSE_{bank}_OUT"), f"{rser} not on the cable side")
        want(not (pins(f"ADC_{bank}") & pins(f"SENSE_{bank}_OUT")),
             f"bank {bank}: op-amp output shorted past {rser}")
        want(not any(r == "J3" for r, _ in nets[f"ADC_{bank}"]),
             f"bank {bank}: cable reaches the op-amp output directly")

    # The cable is 20 conductors, each used exactly once, analog guarded.
    j3 = {int(p): n for n, nodes in nets.items() for r, p in nodes if r == "J3"}
    want(sorted(j3) == list(range(1, 21)), f"J3 pins are {sorted(j3)}")
    for sig, guards in (("SENSE_A_OUT", (6, 8)), ("SENSE_B_OUT", (8, 10))):
        pin = next(p for p, n in j3.items() if n == sig)
        for g in guards:
            want(j3[g] == "AGND", f"{sig} at pin {pin} is not flanked by AGND at {g}")
    want(j3[13] == "PWR_GND", "analog and digital blocks are not separated at pin 13")

    # ROW_VCC is sensed at the 595 supply pins, which is what makes the
    # ratiometric correction exact rather than approximate.
    want(("U1", "16") in pins("ROW_VCC"), "ROW_VCC is not the 595 supply")
    want(("R8", "2") in pins("ROW_VCC") and ("R8", "1") in pins("ROW_VCC_SENSE"),
         "ROW_VCC_SENSE is not Kelvin-tapped through R8")
    want(("C9", "1") in pins("ROW_VCC"), "bulk cap is not on ROW_VCC")

    # Nothing from rev-1 was silently dropped.
    r1_sigs = {n for n in rev1 if re.fullmatch(r"(ROW|COL)_\d+|SR_CHAIN_\d+", n)}
    want(r1_sigs <= set(nets), f"lost from rev-1: {sorted(r1_sigs - set(nets))}")
    return fails


def main():
    rev1 = load_rev1()
    nets = build(rev1)
    fails = check(nets, rev1)
    emit_net(nets, os.path.join(HERE, "module.net"))
    emit_bom(os.path.join(HERE, "BOM.csv"))

    refs = sorted({r for nodes in nets.values() for r, _ in nodes})
    print(f"module netlist: {len(nets)} nets, {len(refs)} components")
    print(f"  {' '.join(refs)}")
    if fails:
        print(f"\n{len(fails)} CHECK(S) FAILED:")
        for f in fails:
            print(f"  {f}")
        return 1
    print(f"\nall {len(nets)} nets check out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
