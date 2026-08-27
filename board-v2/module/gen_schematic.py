#!/usr/bin/env python3
"""Generate the module's KiCad schematic by transforming rev-1's.

Same argument as gen_module.py: for a circuit already measured on hardware,
transforming the proven drawing beats redrawing it. Every 595, mux and buffer
keeps the position, orientation and labels it was verified with, and the diff
is the change rather than the whole sheet.

    ./gen_schematic.py       writes module.kicad_sch, then checks it with KiCad

Output is KiCad 7 format on purpose. kiutils writes it natively, kicad-cli 7 can
then load it and export a netlist for checking, and KiCad 10 opens and upgrades
it on the way in. Generating v10 directly would mean nothing could verify it.

POWER SYMBOLS BECOME GLOBAL LABELS. Partly because kiutils' round-trip does not
preserve whatever makes an implicit power net resolve - the three power nets in
rev-1 come back as <NO NET> - but mostly because this board has TWO grounds.
PWR_GND carries up to 30 mA of press-correlated row current and AGND must carry
none, and two identical-looking ground symbols is exactly how that distinction
gets lost at layout. Named labels put it on the sheet.
"""
import math
import os
import shutil
import subprocess
import sys

from kiutils.schematic import Schematic
from kiutils.items.common import Position, Property, Effects, Font
from kiutils.items.schitems import GlobalLabel, Connection, SchematicSymbol

HERE = os.path.dirname(os.path.abspath(__file__))
REV1 = os.path.join(HERE, "../../board/taxelscan.kicad_sch")
OUT = os.path.join(HERE, "module.kicad_sch")

# Which side of the split ground each component's return belongs on. The 595s
# and the bulk/decoupling on the row rail carry row current; everything in the
# sense chain must not. See README.md.
PWR_GND_REFS = {"U1", "U2", "U3", "U4", "C1", "C2", "C3", "C4", "C9"}

# C9 is the bulk cap and it moves off the analog rail onto ROW_VCC, sized for
# the row-change transient rather than the static drop. Its old +3.3V symbol
# must follow it, or the schematic quietly disagrees with module.net - which is
# exactly what KiCad's netlist export caught the first time round.
ROW_VCC_REFS = {"C9"}


def pin_xy(sym, lib, pin):
    """Absolute position of one pin of a placed symbol."""
    ang = math.radians(sym.position.angle or 0)
    ca, sa = math.cos(ang), math.sin(ang)
    px, py = pin.position.X, pin.position.Y
    if sym.mirror == "y":
        px = -px
    elif sym.mirror == "x":
        py = -py
    return (sym.position.X + px * ca + py * sa,
            sym.position.Y + px * sa - py * ca)


def label(text, x, y, angle=0):
    return GlobalLabel(text=text, position=Position(x, y, angle), shape="input",
                       effects=Effects(font=Font(width=1.27, height=1.27)))


def ref_of(sym):
    return next((p.value for p in sym.properties if p.key == "Reference"), "")


def val_of(sym):
    return next((p.value for p in sym.properties if p.key == "Value"), "")


def build():
    sch = Schematic().from_file(REV1)
    libs = {ls.libId: ls for ls in sch.libSymbols}

    # ---- 1. what goes away -------------------------------------------------
    # A1 is the MCU. R5 is the 0R that bridged +3.3V to ROW_VCC; ROW_VCC now
    # arrives on its own conductors so it can sag independently and be
    # measured. Power symbols become labels (see the module docstring).
    drop, power_labels = [], []
    for sym in sch.schematicSymbols:
        ref, val = ref_of(sym), val_of(sym)
        if ref in ("A1", "R5") or ref.startswith("#FLG"):
            drop.append(sym)
            continue
        if not ref.startswith("#PWR"):
            continue
        drop.append(sym)
        lib = libs.get(sym.libId)
        if lib is None:
            continue
        for unit in lib.units:
            for pin in unit.pins:
                x, y = pin_xy(sym, lib, pin)
                power_labels.append((val, x, y))

    for sym in drop:
        sch.schematicSymbols.remove(sym)

    # ---- 2. place a named label where each power symbol used to be ---------
    # Every pin of every remaining component keeps the position it was drawn
    # at, so attributing a ground to PWR_GND or AGND is a question of which
    # pin the old symbol was touching.
    pins = {}
    for sym in sch.schematicSymbols:
        lib = libs.get(sym.libId)
        if lib is None:
            continue
        for unit in lib.units:
            try:
                uidx = int(unit.entryName.split("_")[-2])
            except (ValueError, IndexError):
                uidx = 0
            if uidx not in (0, sym.unit):
                continue
            for pin in unit.pins:
                pins[pin_xy(sym, lib, pin)] = ref_of(sym)

    def nearest_ref(x, y, limit=5.1):
        best, bd = None, limit
        for (px, py), r in pins.items():
            d = math.hypot(px - x, py - y)
            if d < bd:
                best, bd = r, d
        return best

    renamed = {"GND": 0, "AGND": 0, "PWR_GND": 0, "AVCC": 0, "dropped": 0}
    for val, x, y in power_labels:
        if val == "+5V":                       # the XIAO's pin was its only node
            renamed["dropped"] += 1
            continue
        if val == "GND":
            owner = nearest_ref(x, y)
            name = "PWR_GND" if owner in PWR_GND_REFS else "AGND"
        elif val == "+3.3V":
            owner = nearest_ref(x, y)
            name = "ROW_VCC" if owner in ROW_VCC_REFS else "AVCC"
        else:
            name = val
        renamed[name] = renamed.get(name, 0) + 1
        sch.globalLabels.append(label(name, x, y))
    return sch, libs, pins, renamed


# The new parts, and the net at each pin. Positions are chosen below the
# existing drawing so nothing proven moves.
NEW_PARTS = [
    # ref,  lib,                          value,          footprint, pin->net
    ("R6",  "Device:R", "51R",  "Resistor_SMD:R_0603_1608Metric",
     {"1": "ADC_A", "2": "SENSE_A_OUT"}),
    ("R7",  "Device:R", "51R",  "Resistor_SMD:R_0603_1608Metric",
     {"1": "ADC_B", "2": "SENSE_B_OUT"}),
    ("R8",  "Device:R", "0R",   "Resistor_SMD:R_0603_1608Metric",
     {"1": "ROW_VCC_SENSE", "2": "ROW_VCC"}),
    ("TP1", "Connector:TestPoint", "TestPoint", "TestPoint:TestPoint_Pad_D1.0mm",
     {"1": "ROW_DATA"}),
    ("TP2", "Connector:TestPoint", "TestPoint", "TestPoint:TestPoint_Pad_D1.0mm",
     {"1": "ROW_VCC"}),
    ("TP3", "Connector:TestPoint", "TestPoint", "TestPoint:TestPoint_Pad_D1.0mm",
     {"1": "SENSE_A_OUT"}),
    ("TP4", "Connector:TestPoint", "TestPoint", "TestPoint:TestPoint_Pad_D1.0mm",
     {"1": "SENSE_B_OUT"}),
]

J3_PINOUT = [
    "ROW_VCC", "PWR_GND", "ROW_VCC", "PWR_GND", "AVCC",
    "AGND", "SENSE_A_OUT", "AGND", "SENSE_B_OUT", "AGND",
    "AGND", "ROW_VCC_SENSE", "PWR_GND", "MUX_S0", "MUX_S1",
    "MUX_S2", "MUX_S3", "ROW_DATA", "ROW_CLK_MCU", "ROW_LATCH_MCU",
]


def make_testpoint_symbol():
    """A one-pin symbol. Nothing in rev-1's libraries is close enough to reuse."""
    from kiutils.symbol import Symbol, SymbolPin
    sym = Symbol().create_new(id="TestPoint", reference="TP", value="TestPoint",
                              footprint="TestPoint:TestPoint_Pad_D1.0mm")
    sym.libraryNickname, sym.entryName = "Connector", "TestPoint"
    sym.hidePinNumbers = True
    unit = Symbol(libraryNickname=None, entryName="TestPoint_1_1")
    unit.pins.append(SymbolPin(electricalType="passive", graphicalStyle="line",
                               position=Position(0, 2.54, 270), length=2.032,
                               name="1", number="1"))
    sym.units = [unit]
    return sym


def make_conn20(conn32):
    """Derive a 20-way connector from rev-1's proven 32-way symbol."""
    import copy
    c = copy.deepcopy(conn32)
    c.libId = "Connector_Generic:Conn_01x20"
    c.entryName = "Conn_01x20"
    for unit in c.units:
        # entryName only: assigning libId re-derives it and drops the _1_1
        # unit suffix, which makes KiCad reject the whole library entry.
        unit.entryName = unit.entryName.replace("Conn_01x32", "Conn_01x20")
        unit.pins = [p for p in unit.pins if p.number.isdigit() and int(p.number) <= 20]
    return c


def place(sch, libs, lib_id, ref, value, footprint, at, pin_nets, unit=1):
    """Drop a symbol and put a global label on each of its pins.

    The sheet is label-driven, as rev-1 is: a label sitting on a pin is the
    connection, so no wire is needed. kicad-cli confirms that in check().
    """
    lib = libs[lib_id]
    nick, entry = lib_id.split(":")
    sym = SchematicSymbol(
        libraryNickname=nick, entryName=entry, position=Position(at[0], at[1], 0),
        unit=unit, inBom=True, onBoard=True)
    shown = Effects(font=Font(width=1.27, height=1.27))
    hidden = Effects(font=Font(width=1.27, height=1.27), hide=True)
    sym.properties = [
        Property(key="Reference", value=ref, position=Position(at[0], at[1] - 3.8, 0),
                 effects=shown),
        Property(key="Value", value=value, position=Position(at[0], at[1] + 3.8, 0),
                 effects=shown),
        Property(key="Footprint", value=footprint, position=Position(at[0], at[1], 0),
                 effects=hidden),
        Property(key="Datasheet", value="~", position=Position(at[0], at[1], 0),
                 effects=hidden),
    ]
    sch.schematicSymbols.append(sym)
    for unit_sym in lib.units:
        try:
            uidx = int(unit_sym.entryName.split("_")[-2])
        except (ValueError, IndexError):
            uidx = 0
        if uidx not in (0, unit):
            continue
        for pin in unit_sym.pins:
            net = pin_nets.get(pin.number)
            if net:
                x, y = pin_xy(sym, lib, pin)
                sch.globalLabels.append(label(net, x, y))
    return sym


def main():
    sch, libs, pins, renamed = build()

    # New library symbols. Conn_01x20 is derived from rev-1's proven 32-way
    # part so the pin geometry cannot disagree with it.
    libs["Connector_Generic:Conn_01x20"] = make_conn20(libs["Connector_Generic:Conn_01x32"])
    libs["Connector:TestPoint"] = make_testpoint_symbol()
    sch.libSymbols.append(libs["Connector_Generic:Conn_01x20"])
    sch.libSymbols.append(libs["Connector:TestPoint"])

    # Place below the existing drawing so nothing proven moves.
    ymax = max(s.position.Y for s in sch.schematicSymbols)
    y0 = math.ceil((ymax + 30) / 2.54) * 2.54
    x = 60.96
    for i, (ref, lib_id, value, fp, nets) in enumerate(NEW_PARTS):
        place(sch, libs, lib_id, ref, value, fp, (x + i * 25.4, y0), nets)
    place(sch, libs, "Connector_Generic:Conn_01x20", "J3", "FFC_20P_0.5mm",
          "Connector_FFC-FPC:Hirose_FH12-20S-0.5SH_1x20-1MP_P0.50mm_Horizontal",
          (x + len(NEW_PARTS) * 25.4 + 25.4, y0 + 25.4),
          {str(i + 1): n for i, n in enumerate(J3_PINOUT)})

    # KiCad 7 on the way out; KiCad 10 upgrades it on the way in.
    sch.version, sch.generator = "20230121", "taxelscan_gen_schematic"
    sch.to_file(OUT)
    print(f"wrote {os.path.relpath(OUT)}")
    print(f"  {len(sch.schematicSymbols)} symbols, {len(sch.globalLabels)} labels")
    print(f"  power symbols converted to labels: {renamed}")
    return check()


def check():
    """Have KiCad export a netlist and compare it to the verified design.

    This is the only check worth much. Everything up to here is this script
    agreeing with itself; this is KiCad's own parser and connectivity engine
    reading what was written and saying what it is actually connected to. It
    caught a real fault the first time it ran - C9, the bulk cap, was moved to
    ROW_VCC in module.net but left on the analog rail here.
    """
    import sexpdata
    if not shutil.which("kicad-cli"):
        print("\n  kicad-cli not found: SKIPPING the netlist check.\n"
              "  Install KiCad (apt install kicad) to verify this file.")
        return 0
    out = "/tmp/module-cli.net"
    subprocess.run(["kicad-cli", "sch", "export", "netlist", "--output", out, OUT],
                   check=True, capture_output=True)
    if not os.path.exists(out):
        print("\n  KiCad FAILED TO LOAD the generated schematic")
        return 1

    def load(path):
        d = sexpdata.loads(open(path).read())
        def find(n, k):
            return [x for x in n if isinstance(x, list) and x and str(x[0]) == k]
        return {str(find(n, "name")[0][1]):
                set((str(find(x, "ref")[0][1]), str(find(x, "pin")[0][1]))
                    for x in find(n, "node"))
                for n in find(find(d, "nets")[0], "net")}

    strip = lambda d: {k: v for k, v in d.items()
                       if not k.startswith(("unconnected-", "Net-"))}
    got = strip(load(out))
    want = strip(load(os.path.join(HERE, "module.net")))
    bad = [k for k in set(got) | set(want) if got.get(k, set()) != want.get(k, set())]
    print(f"\n  KiCad loads it and exports {len(got)} nets")
    if bad:
        print(f"  {len(bad)} DISAGREE with module.net:")
        for k in sorted(bad):
            a, b = want.get(k, set()), got.get(k, set())
            print(f"    {k}: missing={sorted(a - b)} extra={sorted(b - a)}")
        return 1
    print(f"  all {len(want)} match module.net exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
