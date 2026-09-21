#!/usr/bin/env python3
"""Generate the module's KiCad schematic by transforming rev-1's.

Same argument as gen_module.py: for a circuit already measured on hardware,
transforming the proven drawing beats redrawing it. Every 595, mux and buffer
keeps the position, orientation and labels it was verified with, and the diff
is the change rather than the whole sheet.

    ./gen_schematic.py       writes module.kicad_sch, then checks it with KiCad

Output is KiCad 7 format on purpose. kiutils writes it natively, kicad-cli
loads it and exports a netlist to check against, and KiCad 8 or newer upgrades
it on the way in. Generating v10 directly would mean nothing could verify it.

POWER SYMBOLS BECOME GLOBAL LABELS. Partly because kiutils' round-trip does not
preserve whatever makes an implicit power net resolve - the three power nets in
rev-1 come back as <NO NET> - but mostly because this board has TWO grounds.
PWR_GND carries up to 30 mA of press-correlated row current and AGND must carry
none, and two identical-looking ground symbols is exactly how that distinction
gets lost at layout. Named labels put it on the sheet.
"""
import csv
import glob
import io
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

from kiutils.schematic import Schematic
from kiutils.items.common import Position, Property, Effects, Font
from kiutils.items.schitems import GlobalLabel, Connection, SchematicSymbol

HERE = os.path.dirname(os.path.abspath(__file__))
REV1 = os.path.join(HERE, "../rev1/taxelscan.kicad_sch")
OUT = os.path.join(HERE, "module.kicad_sch")
BOM = os.path.join(HERE, "BOM.csv")

# Which side of the split ground each component's return belongs on. The 595s
# and the bulk/decoupling on the row rail carry row current; everything in the
# sense chain must not. See README.md.
PWR_GND_REFS = {"U1", "U2", "U3", "U4", "C1", "C2", "C3", "C4", "C9"}

# C9 is the bulk cap and it moves off the analog rail onto ROW_VCC, sized for
# the row-change transient rather than the static drop. Its old +3.3V symbol
# must follow it, or the schematic quietly disagrees with module.net - which is
# exactly what KiCad's netlist export caught the first time round.
ROW_VCC_REFS = {"C9"}

# Every power net needs something asserting it is driven. rev-1 had a single
# PWR_FLAG; it left with the #FLG symbols and nothing replaced it, so all four
# rails came back undriven the first time ERC was ever run on this sheet.
# One flag per rail, and the two grounds each get their own.
PWR_FLAG_NETS = ["ROW_VCC", "PWR_GND", "AVCC", "AGND"]


def load_bom(path=BOM):
    """ref -> (value, footprint) from BOM.csv, expanding 'U1-U4' and 'J1,J2'.

    BOM.csv is the single source of truth for what each part IS: gen_module.py
    writes it, this script applies it. The two used to agree only by hand, and
    they did not. C9 became 22 uF on an 0805 land in the BOM while the
    schematic kept rev-1's 10 uF on an 0603, and neither check could see it -
    ERC does not read values, and the netlist check compares nets. Driving both
    from one file makes that drift impossible rather than merely detectable.
    """
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for tok in (t.strip() for t in row["Reference"].split(",")):
                m = re.fullmatch(r"([A-Za-z#]+)(\d+)-(?:[A-Za-z#]+)?(\d+)", tok)
                refs = ([m.group(1) + str(i)
                         for i in range(int(m.group(2)), int(m.group(3)) + 1)]
                        if m else ([tok] if tok else []))
                for ref in refs:
                    out[ref] = (row["Value"], row["Footprint"])
    return out


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


def unit_pins(lib, want_unit):
    """Pins of the library units belonging to want_unit; 0 means shared."""
    for unit in lib.units:
        try:
            uidx = int(unit.entryName.split("_")[-2])
        except (ValueError, IndexError):
            uidx = 0
        if uidx not in (0, want_unit):
            continue
        for pin in unit.pins:
            yield pin


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
        for pin in unit_pins(lib, sym.unit):
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
# existing drawing so nothing proven moves. Value and footprint are settled
# afterwards from BOM.csv, which is the authority on both.
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
    for pin in unit_pins(lib, unit):
        net = pin_nets.get(pin.number)
        if net:
            x, y = pin_xy(sym, lib, pin)
            sch.globalLabels.append(label(net, x, y))
    return sym


def prune_dangling(sch, libs):
    """Delete the drawing left behind by the symbols build() removed.

    A1, R5 and rev-1's PWR_FLAG went away but their wires, no-connect flags
    and labels stayed on the sheet - 19 of the 29 violations the first ERC run
    reported, and every one of them invisible to the netlist check, because
    debris hanging off an otherwise correct net does not change what that net
    joins. Which is also why deleting it here is safe: check() re-exports the
    netlist afterwards and it still has to be the same 87 nets.

    Only a pin is anchored outright. A label holds if it sits on a pin or on a
    live wire; a wire holds if both ends sit on a pin, a live label, a junction
    or another live wire. Those two support each other, so this runs to a
    fixpoint rather than in one pass - cutting a wire can strand the label that
    was riding it, and cutting that label can strand the next wire along.
    """
    def q(x, y):
        return (round(x, 3), round(y, 3))

    pinpts = set()
    for sym in sch.schematicSymbols:
        lib = libs.get(sym.libId)
        if lib is None:
            continue
        for pin in unit_pins(lib, sym.unit):
            pinpts.add(q(*pin_xy(sym, lib, pin)))

    junctions = {q(j.position.X, j.position.Y) for j in sch.junctions}

    def touches(pt, wire):
        (ax, ay), (bx, by) = ((p.X, p.Y) for p in wire.points[:2])
        if q(ax, ay) == pt or q(bx, by) == pt:
            return True
        if abs((bx - ax) * (pt[1] - ay) - (by - ay) * (pt[0] - ax)) > 1e-6:
            return False
        dot = (pt[0] - ax) * (bx - ax) + (pt[1] - ay) * (by - ay)
        return 0 <= dot <= (bx - ax) ** 2 + (by - ay) ** 2

    wires = [g for g in sch.graphicalItems if isinstance(g, Connection)
             and g.type == "wire" and len(g.points) >= 2]
    labels = [(lst, lb) for lst in (sch.labels, sch.globalLabels,
                                    sch.hierarchicalLabels) for lb in lst]
    dead = set()
    while True:
        live_w = [w for w in wires if id(w) not in dead]
        live_l = [(lst, lb) for lst, lb in labels if id(lb) not in dead]
        held = pinpts | junctions | {q(lb.position.X, lb.position.Y)
                                     for _, lb in live_l}
        gone = {id(lb) for _, lb in live_l
                if q(lb.position.X, lb.position.Y) not in pinpts
                and not any(touches(q(lb.position.X, lb.position.Y), w)
                            for w in live_w)}
        gone |= {id(w) for w in live_w
                 if any(q(p.X, p.Y) not in held
                        and not any(touches(q(p.X, p.Y), o)
                                    for o in live_w if o is not w)
                        for p in w.points[:2])}
        if not gone:
            break
        dead |= gone

    nc_dead = {id(nc) for nc in sch.noConnects
               if q(nc.position.X, nc.position.Y) not in pinpts}
    sch.graphicalItems = [g for g in sch.graphicalItems if id(g) not in dead]
    sch.noConnects = [nc for nc in sch.noConnects if id(nc) not in nc_dead]
    for lst in (sch.labels, sch.globalLabels, sch.hierarchicalLabels):
        lst[:] = [lb for lb in lst if id(lb) not in dead]
    n_w = sum(1 for w in wires if id(w) in dead)
    n_l = sum(1 for _, lb in labels if id(lb) in dead)
    return n_w, n_l, len(nc_dead)


def apply_bom(sch):
    """Make every symbol say what BOM.csv says it is. Returns (ok, changes)."""
    bom = load_bom()
    absent = sorted(set(bom) - {ref_of(s) for s in sch.schematicSymbols})
    if absent:
        print("  BOM.csv lists parts that are not on the sheet: " + str(absent))
        return False, []
    changed = []
    for sym in sch.schematicSymbols:
        want = bom.get(ref_of(sym))
        if not want:
            continue
        for prop in sym.properties:
            if prop.key not in ("Value", "Footprint"):
                continue
            new = want[0] if prop.key == "Value" else want[1]
            if prop.value != new:
                changed.append("%s.%s: %r -> %r"
                               % (ref_of(sym), prop.key, prop.value, new))
                prop.value = new
    return True, changed


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

    # One PWR_FLAG per rail. They go to the right of J3 on the same row as the
    # test points: the sheet is A1, so there is room across but almost none
    # below - J3's twenty pins already reach within 15 mm of the bottom edge.
    # They are annotation rather than circuit: out of the BOM, off the board,
    # and check() ignores #-prefixed refs on both sides of every comparison.
    flag_x = x + (len(NEW_PARTS) + 3) * 25.4
    for i, net in enumerate(PWR_FLAG_NETS):
        flag = place(sch, libs, "power:PWR_FLAG", "#FLG%02d" % i, "PWR_FLAG",
                     "", (flag_x + i * 25.4, y0), {"1": net})
        flag.inBom, flag.onBoard = False, False

    gone_w, gone_l, gone_nc = prune_dangling(sch, libs)
    ok, corrected = apply_bom(sch)
    if not ok:
        return 1

    # KiCad 7 on the way out; KiCad 8 or newer upgrades it on the way in.
    sch.version, sch.generator = "20230121", "taxelscan_gen_schematic"
    sch.to_file(OUT)
    print("wrote " + os.path.relpath(OUT))
    print("  %d symbols, %d labels" % (len(sch.schematicSymbols),
                                       len(sch.globalLabels)))
    print("  power symbols converted to labels: " + str(renamed))
    print("  dangling from removed parts: %d wires, %d labels, %d no-connects"
          % (gone_w, gone_l, gone_nc))
    if corrected:
        print("  taken from BOM.csv: %d correction(s)" % len(corrected))
        for c in corrected:
            print("    " + c)
    return check()


def find_cli():
    """kicad-cli, wherever KiCad put it. It is not on PATH on Windows."""
    found = shutil.which("kicad-cli")
    if found:
        return found
    for pat in (r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\*\bin\kicad-cli.exe",
                "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
                "/usr/lib/kicad/bin/kicad-cli"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def check():
    """Make KiCad read what was written and say whether it is right.

    Three questions, because each of the first two has a blind spot the others
    cover:

      nets   does it connect what module.net says? KiCad's own parser and
             connectivity engine, not this script agreeing with itself. It
             earned its keep immediately - C9 had been moved to ROW_VCC in
             module.net but left on the analog rail here.
      parts  is each one the part BOM.csv specifies? The netlist check compares
             nets and cannot see a value, which is how C9 went on sitting at
             10 uF on an 0603 land long after its net was fixed.
      ERC    electrical rules. Never run on this sheet at all until 2026-08-28,
             because KiCad 7's CLI had no erc subcommand; 8 added it. It found
             four undriven rails and nineteen fragments of deleted parts.
    """
    import sexpdata
    cli = find_cli()
    if not cli:
        print("\n  kicad-cli not found: SKIPPING every check.\n"
              "  Install KiCad 8 or newer to verify this file.")
        return 0

    def find(node, key):
        return [x for x in node if isinstance(x, list) and x and str(x[0]) == key]

    tmp = tempfile.mkdtemp(prefix="taxelscan-")
    try:
        netfile = os.path.join(tmp, "cli.net")
        ercfile = os.path.join(tmp, "erc.rpt")
        subprocess.run([cli, "sch", "export", "netlist", "--output", netfile, OUT],
                       check=True, capture_output=True)
        if not os.path.exists(netfile):
            print("\n  KiCad FAILED TO LOAD the generated schematic")
            return 1
        doc = sexpdata.loads(io.open(netfile, encoding="utf-8").read())
        rc = 0

        # ---- nets ----------------------------------------------------------
        def nets_of(d):
            # #-prefixed refs are power flags and other annotation. They carry
            # no copper, so module.net does not list them and neither do we.
            return {str(find(n, "name")[0][1]):
                    set((str(find(x, "ref")[0][1]), str(find(x, "pin")[0][1]))
                        for x in find(n, "node")
                        if not str(find(x, "ref")[0][1]).startswith("#"))
                    for n in find(find(d, "nets")[0], "net")}

        def strip(d):
            return {k: v for k, v in d.items()
                    if not k.startswith(("unconnected-", "Net-"))}

        got = strip(nets_of(doc))
        want = strip(nets_of(sexpdata.loads(
            io.open(os.path.join(HERE, "module.net"), encoding="utf-8").read())))
        bad = [k for k in set(got) | set(want)
               if got.get(k, set()) != want.get(k, set())]
        print("\n  KiCad loads it and exports %d nets" % len(got))
        if bad:
            rc = 1
            print("  %d DISAGREE with module.net:" % len(bad))
            for k in sorted(bad):
                a, b = want.get(k, set()), got.get(k, set())
                print("    %s: missing=%s extra=%s"
                      % (k, sorted(a - b), sorted(b - a)))
        else:
            print("  all %d match module.net exactly" % len(want))

        # ---- parts ---------------------------------------------------------
        def prop(comp, key):
            hit = find(comp, key)
            return str(hit[0][1]) if hit and len(hit[0]) > 1 else ""

        seen = {prop(c, "ref"): (prop(c, "value"), prop(c, "footprint"))
                for c in find(find(doc, "components")[0], "comp")
                if not prop(c, "ref").startswith("#")}
        bom = load_bom()
        wrong = {r: (seen.get(r), bom[r]) for r in bom if seen.get(r) != bom[r]}
        extra = sorted(set(seen) - set(bom))
        if wrong or extra:
            rc = 1
            print("  %d PART(S) DISAGREE with BOM.csv:" % (len(wrong) + len(extra)))
            for r in sorted(wrong):
                print("    %s: is %s should be %s" % (r, wrong[r][0], wrong[r][1]))
            for r in extra:
                print("    %s: on the sheet, absent from BOM.csv" % r)
        else:
            print("  all %d parts match BOM.csv in value and footprint" % len(bom))

        # ---- ERC -----------------------------------------------------------
        subprocess.run([cli, "sch", "erc", "--output", ercfile, "--severity-all",
                        "--exit-code-violations", OUT], capture_output=True)
        report = (io.open(ercfile, encoding="utf-8").read()
                  if os.path.exists(ercfile) else "")
        m = re.search(r"Errors (\d+)\s+Warnings (\d+)", report)
        if not m:
            print("  ERC produced no report")
            return 1
        errors, warnings = int(m.group(1)), int(m.group(2))
        print("  ERC: %d errors, %d warnings" % (errors, warnings))
        for kind, text in re.findall(r"\[(\w+)\]: ([^\n]+)", report):
            print("    %s: %s" % (kind, text.strip()))
        if errors:
            rc = 1
        return rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
