#!/usr/bin/env python3
"""Generate rev-3's KiCad schematic from rev3.net, then let KiCad check it.

Unlike ../v2-module/gen_schematic.py, this one does not transform rev-1's
drawing. rev-3 keeps rev-1's analog *topology* but replaces the MCU, the
converter and the whole power and bus section, so more than half the sheet
would be new symbols placed by hand anyway. Building it from the netlist
instead means the sheet cannot disagree with rev3.net about connectivity -
they come from the same source, and kicad-cli then proves it independently.

    ./gen_schematic.py       writes rev3.kicad_sch, then checks it with KiCad

The drawing is arranged by layout_schematic.py into six functional sections.
Local circuits use wires, while repeated ports and section crossings use net
labels. KiCad's exported netlist verifies that the drawing still agrees with
rev3.net. Existing sheet and symbol UUIDs are retained to preserve PCB links.

Output is KiCad 7 format on purpose: kiutils writes it natively, kicad-cli
loads it and exports a netlist to check against, and KiCad 8 or newer upgrades
it on the way in. Emitting v10 directly would mean nothing here could verify it.

Needs: pip install kiutils
"""
import copy
import csv
import uuid
import io
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

from kiutils.schematic import Schematic
from kiutils.symbol import SymbolLib
from kiutils.items.common import Position, Property, Effects, Font
from kiutils.items.schitems import (GlobalLabel, NoConnect, SchematicSymbol,
                                    SymbolProjectInstance, SymbolProjectPath)

import gen_rev3 as G

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "rev3.kicad_sch")
NET = os.path.join(HERE, "rev3.net")
BOM = os.path.join(HERE, "BOM.csv")

# Every rail needs something telling ERC it is driven. The board has no power
# symbols - the rails are global labels - so nothing is implicitly a source.
# rev-1's lone PWR_FLAG left with its #FLG symbols when the XIAO went, and the
# first ERC run on the v2 module sheet found four undriven rails as a result.
# SW_NODE and VREG_LX are deliberately NOT here. Both are switch nodes already
# driven by a real output pin, and a PWR_FLAG on top of one is a second driver -
# ERC calls that pin_to_pin and it is right.
#
# ADC_AVDD joins them for the same reason ROW_VCC and VREF already had to: it
# reaches its pin through a series resistor, so the only thing ERC can see on
# the net is a power INPUT, and it correctly says nothing is driving it.
PWR_FLAG_NETS = ["+3.3V", "+5V", "+5V_BUS", "+5V_USB", "GND",
                 "VCORE", "ROW_VCC", "VREF", "FB", "ADC_AVDD", "VREG_AVDD"]

# A solder jumper is copper, not a part: nothing is placed on it, and its
# footprint says so with (attr exclude_from_pos_files exclude_from_bom). The
# symbol has to agree or KiCad reports a footprint_symbol_mismatch on every
# one of them.
NOT_A_PART = {"JP1", "JP2", "JP3"}

# Drawing order, grouped so the sheet reads the way the signal flows rather
# than in reference-designator order.
ORDER = [
    "J1", "U1", "U2", "U3", "U4", "R3", "R4",           # rows
    "J2", "U5", "U6",                                    # columns
    "R1", "R2", "U7", "R6", "R7", "R8", "R9",            # sense + gain
    "U8", "R10", "C10", "R5",                            # converter + VREF
    "U9", "Y1", "C20", "C21", "R36", "L1", "C19", "R35", "C43", "C44", "SW1",  # MCU
    "JP1", "JP2", "JP3", "D3", "R17", "R15", "R16", "C45",  # address, status
    "U10", "U11", "R11", "R12", "J3", "J4",              # bus
    "J5", "U13", "U14", "D1", "D2", "D4",              # USB
    "J6", "R20",                                         # debug, reset
    "U12", "L2", "R18", "R19", "C22", "C23",             # power
]


# ------------------------------------------------------- raw symbol geometry
#
# kiutils drops the "_unit_style" suffix when it reads a symbol, so every unit
# comes back named after the symbol and the unit INDEX is lost. It restores
# them positionally on write, which is why appending a multi-unit symbol to
# libSymbols still produces a valid file - but it means the unit a pin belongs
# to cannot be read back from kiutils. So the geometry is parsed from KiCad's
# files directly, and the two agree because both are ordered.
def _block(lib, name):
    path = G.symbol_path(lib)
    text = open(path, encoding="utf-8").read()
    i = text.find('\t(symbol "%s"' % name)
    if i < 0:
        sys.exit("symbol %s:%s not found" % (lib, name))
    j = text.find('\n\t(symbol "', i + 3)
    return text[i:j if j > 0 else len(text)]


_units_cache = {}


def units_of(lib, name):
    """{unit index: [(pin number, x, y, angle)]}, following (extends)."""
    key = (lib, name)
    if key in _units_cache:
        return _units_cache[key]
    blk = _block(lib, name)
    out = {}
    for m in re.finditer(r'\(symbol "%s_(\d+)_(\d+)"(.*?)(?=\n\t\t\(symbol "|\Z)'
                         % re.escape(name), blk, re.S):
        idx, body = int(m.group(1)), m.group(3)
        for pm in re.finditer(
                r'\(pin \w+ \w+\s*\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)'
                r'.*?\(number "([^"]*)"', body, re.S):
            out.setdefault(idx, []).append(
                (pm.group(4), float(pm.group(1)), float(pm.group(2)),
                 float(pm.group(3) or 0)))
    if not out:
        ext = re.search(r'\(extends "([^"]*)"', blk)
        if ext:
            out = units_of(lib, ext.group(1))
    _units_cache[key] = out
    return out


# ------------------------------------------------------------------- inputs
def load_net(path=NET):
    """{ref: {pin: net}} from the generated netlist."""
    text = open(path, encoding="utf-8").read()
    out = {}
    for name, body in re.findall(
            r'\(net \(code "\d+"\) \(name "([^"]+)"\)(.*?)\n    \)', text, re.S):
        for ref, pin in re.findall(
                r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)\)', body):
            out.setdefault(ref, {})[pin] = name
    if not out:
        sys.exit("could not parse %s - run ./gen_rev3.py first" % path)
    return out


def load_bom(path=BOM):
    """{ref: (value, footprint)}, expanded from the BOM's reference ranges.

    BOM.csv is the single source of truth for what each part IS. The netlist
    check compares nets and ERC does not read values, so without this the
    sheet could say 3.3k where the BOM says 10k and nothing would notice -
    which is exactly the mistake C9 made twice on the v2 module.
    """
    out = {}
    for row in csv.DictReader(open(path, encoding="utf-8")):
        for ref in row["Reference"].replace(" ", "").split(","):
            m = re.fullmatch(r"([A-Z]+)(\d+)-[A-Z]*(\d+)", ref)
            span = (range(int(m.group(2)), int(m.group(3)) + 1) if m else None)
            for r in ([("%s%d" % (m.group(1), i)) for i in span] if m else [ref]):
                out[r] = (row["Value"], row["Footprint"])
    return out


# ------------------------------------------------------------------ drawing
GRID = 1.27          # KiCad's connection grid

# One kicad-cli ERC violation spans three lines:
#     [rule_name]: human readable text
#         ; error
#         @(x mm, y mm): Symbol U1 Pin 16 [...]
# The severity sits on its own line after a semicolon. An earlier version of
# this looked for "Severity: error", matched nothing, and reported a confident
# "0 errors, 0 warnings" over a report holding 130 violations.
#
# ERC writes the severity line bare ("; error") and DRC prefixes it
# ("Local override; error"), so the prefix has to be optional or the same
# mistake happens twice: gen_pcb.py reported "0 violations" over a report
# holding 896 of them.
VIOLATION_RE = r"^\s*\[(\w+)\]: (.*)\n\s*[^;\n]*; (\w+)"


def snap(v):
    """To the 1.27 mm connection grid.

    Labels landing off-grid still connect - the netlist proved that - but
    KiCad flags all 80 of them, and an off-grid junction is genuinely fragile:
    anything later edited on-grid will miss it. Placing on-grid costs nothing.
    """
    return round(v / GRID) * GRID


def label(text, x, y, angle=0):
    return GlobalLabel(text=text, position=Position(x, y, angle), shape="input",
                       effects=Effects(font=Font(width=1.27, height=1.27)))


def flatten(symlibs, lib, name):
    """A library symbol with (extends) resolved, as KiCad caches it in a sheet.

    A schematic's lib_symbols has to stand on its own: an entry that extends a
    parent which is not also present will not resolve when the sheet is opened
    somewhere else.
    """
    src = next((s for s in symlibs[lib].symbols if s.entryName == name), None)
    if src is None:
        sys.exit("symbol %s:%s not in library" % (lib, name))
    if not src.extends:
        out = copy.deepcopy(src)
    else:
        parent = flatten(symlibs, lib, src.extends)
        out = copy.deepcopy(parent)
        out.properties = copy.deepcopy(src.properties)
        out.extends = None
    out.entryName = name
    out.libId = "%s:%s" % (lib, name)
    for unit in out.units:
        unit.entryName = name
    return out


def place(sch, lib_id, ref, value, footprint, unit, at, pin_nets,
          with_common=True, project="rev3"):
    """One symbol instance, with a global label on every pin that has a net."""
    lib, name = lib_id.split(":")
    sym = SchematicSymbol(libraryNickname=lib, entryName=name, unit=unit,
                          position=Position(at[0], at[1], 0),
                          inBom=ref not in NOT_A_PART, onBoard=True,
                          uuid=str(uuid.uuid4()))
    # KiCad 7 moved a symbol's reference AND its unit number into an instances
    # block; the top-level (unit N) and the Reference property are legacy.
    # Without this, `kicad-cli sch upgrade` rebuilds the instance data from
    # scratch and every unit defaults to 1 - which silently collapsed all three
    # units of the dual op-amp onto each other. The KiCad 7 netlist check did
    # not see it, because in that format the export still read the legacy
    # fields. Only ERC on the upgraded file caught it, which is why check()
    # now runs against both formats.
    sym.instances = [SymbolProjectInstance(
        name=project,
        paths=[SymbolProjectPath(sheetInstancePath="/" + sch.uuid,
                                 reference=ref, unit=unit)])]
    shown = Effects(font=Font(width=1.27, height=1.27))
    hidden = Effects(font=Font(width=1.27, height=1.27), hide=True)
    sym.properties = [
        Property(key="Reference", value=ref,
                 position=Position(at[0], at[1] - 2.54, 0), effects=shown),
        Property(key="Value", value=value,
                 position=Position(at[0], at[1] + 2.54, 0), effects=shown),
        Property(key="Footprint", value=footprint,
                 position=Position(at[0], at[1], 0), effects=hidden),
        Property(key="Datasheet", value="~",
                 position=Position(at[0], at[1], 0), effects=hidden),
    ]
    sch.schematicSymbols.append(sym)
    placed = 0
    # Unit 0 is KiCad's "common to every unit" - it is where a part's power
    # pins usually live, and PWR_FLAG's single pin lives there too. Skipping
    # it silently drops those pins: the first version of this placed no label
    # on any flag at all, so every rail came back undriven.
    #
    # Common pins belong to the FIRST unit placed and only that one. Repeating
    # them on each unit would put the same pin at several positions and short
    # whatever nets landed there.
    pins = list(units_of(lib, name).get(unit, []))
    if with_common:
        pins += units_of(lib, name).get(0, [])
    for number, px, py, _ang in pins:
        net = pin_nets.get(number)
        if not net:
            # Declare the intent. Without a no-connect KiCad calls every
            # deliberately-unused pin an error, and the ones here are all
            # deliberate: the stacked-flash QSPI bus, spare GPIO, the last
            # shift register's carry-out, and USB-C's sideband pair.
            sch.noConnects.append(
                NoConnect(position=Position(snap(at[0] + px), snap(at[1] - py))))
            continue
        # A symbol's pin (at x y) is its CONNECTION endpoint, and the schematic
        # Y axis runs opposite the symbol Y axis, which is why py is subtracted.
        sch.globalLabels.append(label(net, snap(at[0] + px), snap(at[1] - py)))
        placed += 1
    return placed


def extent(lib, name, unit):
    """(width, height) of one unit's pin field, for packing."""
    pins = units_of(lib, name).get(unit, []) + units_of(lib, name).get(0, [])
    if not pins:
        return (10.0, 10.0)
    xs = [p[1] for p in pins]
    ys = [p[2] for p in pins]
    return (max(xs) - min(xs) + 30.0, max(ys) - min(ys) + 24.0)


def build():
    sch = Schematic.create_new()
    sch.uuid = sch.uuid or str(uuid.uuid4())
    previous = Schematic.from_file(OUT) if os.path.exists(OUT) else None
    if previous:
        sch.uuid = previous.uuid
    sch.paper.paperSize = "A1"                       # 841 x 594 mm
    sheet_w = 820.0

    netmap = load_net()
    bom = load_bom()

    refs = list(ORDER) + sorted(r for r in netmap if r not in ORDER)
    missing = [r for r in refs if r not in netmap]
    if missing:
        sys.exit("in ORDER but not in the netlist: %s" % missing)

    symlibs = {}
    seen = {}
    x, y, row_h = 20.0, 30.0, 0.0
    labels_placed = 0

    for ref in refs:
        lib_id = "%s:%s" % G.SYMOF[ref]
        lib, name = lib_id.split(":")
        if lib not in symlibs:
            symlibs[lib] = SymbolLib().from_file(
                G.symbol_path(lib))
        if lib_id not in seen:
            seen[lib_id] = flatten(symlibs, lib, name)
            sch.libSymbols.append(seen[lib_id])
        value, footprint = bom.get(ref, ("?", ""))

        units = sorted(u for u in units_of(lib, name) if u != 0) or [1]
        for i, unit in enumerate(units):
            w, h = extent(lib, name, unit)
            if x + w > sheet_w:
                x, y, row_h = 20.0, y + row_h + 12.0, 0.0
            labels_placed += place(sch, lib_id, ref, value, footprint, unit,
                                   (snap(x + w / 2.0), snap(y + h / 2.0)),
                                   netmap[ref], with_common=(i == 0))
            x += w + 8.0
            row_h = max(row_h, h)

    # One PWR_FLAG per rail, so ERC has a source for each.
    if "power" not in symlibs:
        symlibs["power"] = SymbolLib().from_file(
            os.path.join(G.SYMDIR, "power.kicad_sym"))
    flag = flatten(symlibs, "power", "PWR_FLAG")
    sch.libSymbols.append(flag)
    x, y = 20.0, y + row_h + 20.0
    for i, net in enumerate(PWR_FLAG_NETS):
        place(sch, "power:PWR_FLAG", "#FLG%02d" % i, "PWR_FLAG", "", 1,
              (snap(x + i * 30.0), snap(y)), {"1": net})

    if previous:
        existing = {(s.properties[0].value, s.unit): s for s in previous.schematicSymbols}
        for s in sch.schematicSymbols:
            old = existing.get((s.properties[0].value, s.unit))
            if old:
                s.uuid = old.uuid
                s.pins = copy.deepcopy(old.pins)
    from layout_schematic import apply_layout
    apply_layout(sch, netmap)
    return sch, len(sch.labels)


# ------------------------------------------------------------------- checks
def find_cli():
    """kicad-cli, wherever KiCad put it. It is not on PATH on Windows."""
    found = shutil.which("kicad-cli")
    if found:
        return found
    import glob as _glob
    for pat in (r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\*\bin\kicad-cli.exe",
                "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
                "/usr/lib/kicad/bin/kicad-cli", "/usr/bin/kicad-cli"):
        hits = sorted(_glob.glob(pat), reverse=True)
        if hits:
            return hits[0]
    return None


def parse_netlist(path):
    """({net: {(ref, pin)}}, {ref: (value, footprint)}) from a KiCad netlist.

    Parsed as an s-expression rather than with line regexes. The first version
    of this used regexes shaped like rev-1's single-line netlist, and KiCad 10
    exports the same data tab-indented across many lines - so every pattern
    missed. The nets check failed loudly, which was fine, but the PARTS check
    matched nothing at all and reported "0 disagree", which is a check that
    cannot fail. Reading the tree removes the whole class of mistake.
    """
    import sexpdata

    def sym(x):
        return str(x.value()) if isinstance(x, sexpdata.Symbol) else x

    def kids(node, key):
        return [x for x in node
                if isinstance(x, list) and x and sym(x[0]) == key]

    def val(node, key, default=None):
        got = kids(node, key)
        return str(got[0][1]) if got and len(got[0]) > 1 else default

    tree = sexpdata.loads(open(path, encoding="utf-8").read())
    nets = {}
    for block in kids(tree, "nets"):
        for net in kids(block, "net"):
            name = val(net, "name")
            nodes = {(val(n, "ref"), val(n, "pin")) for n in kids(net, "node")}
            if name and nodes:
                nets[name.lstrip("/")] = nodes
    parts = {}
    for block in kids(tree, "components"):
        for comp in kids(block, "comp"):
            ref = val(comp, "ref")
            if ref and not ref.startswith("#"):
                parts[ref] = (val(comp, "value", ""), val(comp, "footprint", ""))
    return nets, parts


def report_fails(fails):
    if not fails:
        return 0
    print("\n%d CHECK(S) FAILED:" % len(fails))
    for f in fails[:25]:
        print("  " + f)
    if len(fails) > 25:
        print("  ... and %d more" % (len(fails) - 25))
    return 1


def check():
    """Hand the sheet to KiCad and ask it three questions.

    These are worth more than everything above them, which is only the script
    agreeing with itself. This is KiCad's own parser, connectivity engine and
    rule checker reading what was written.

      nets   does KiCad see the same connectivity rev3.net specifies?
      parts  is each symbol the part BOM.csv names, in value and footprint?
      erc    does the sheet survive the rule checker?
    """
    cli = find_cli()
    if not cli:
        print("\n  kicad-cli not found: SKIPPING every check.\n"
              "  The file is written but NOTHING has verified it.")
        return 1

    want, _ = parse_netlist(NET)

    tmp = tempfile.mkdtemp(prefix="rev3sch")
    netfile = os.path.join(tmp, "sch.net")
    ercfile = os.path.join(tmp, "erc.rpt")
    fails = []

    r = subprocess.run([cli, "sch", "export", "netlist", "--output", netfile, OUT],
                       capture_output=True, text=True)
    if not os.path.exists(netfile):
        print("\n  kicad-cli could not export a netlist:\n   ",
              (r.stderr or r.stdout).strip()[:400])
        shutil.rmtree(tmp, ignore_errors=True)
        return 1

    got, parts = parse_netlist(netfile)
    got = {n: v for n, v in got.items() if not n.startswith("unconnected-")}

    matched = 0
    for name, nodes in sorted(want.items()):
        if name not in got:
            fails.append("net %s is not in KiCad's netlist" % name)
        elif got[name] != nodes:
            fails.append("net %s: KiCad has %s that rev3.net does not, and is "
                         "missing %s" % (name, sorted(got[name] - nodes) or "-",
                                         sorted(nodes - got[name]) or "-"))
        else:
            matched += 1
    extra = [n for n in got if n not in want]
    if extra:
        fails.append("KiCad sees %d net(s) not in rev3.net: %s"
                     % (len(extra), sorted(extra)[:6]))
    print("  nets   %d of %d match rev3.net" % (matched, len(want)))

    # Parts. This has to see a non-empty set or it is not a check at all.
    bom = load_bom()
    if not parts:
        fails.append("the netlist export listed no components - the parts "
                     "check did not run")
    parts_bad = 0
    for ref, (value, fp) in sorted(parts.items()):
        wv, wf = bom.get(ref, (None, None))
        if wv is None:
            fails.append("%s is on the sheet but not in BOM.csv" % ref)
            parts_bad += 1
        elif (value, fp) != (wv, wf):
            fails.append("%s is %s / %s on the sheet, %s / %s in BOM.csv"
                         % (ref, value, fp, wv, wf))
            parts_bad += 1
    for ref in bom:
        if ref not in parts:
            fails.append("%s is in BOM.csv but not on the sheet" % ref)
            parts_bad += 1
    print("  parts  %d on the sheet, %d disagree with BOM.csv"
          % (len(parts), parts_bad))

    # kicad-cli writes each violation as three lines:
    #     [rule_name]: human text
    #         ; error
    #         @(x mm, y mm): Symbol U1 Pin 16 [...]
    # The severity is on its own line after a semicolon. An earlier version of
    # this looked for "Severity: error", matched nothing, and printed a
    # confident "0 errors, 0 warnings" over a report holding 130 violations.
    # A report that cannot be parsed is now a failure, not a pass.
    subprocess.run([cli, "sch", "erc", "--output", ercfile, "--severity-all",
                    "--exit-code-violations", OUT], capture_output=True, text=True)
    if not os.path.exists(ercfile):
        print("  ERC    no report produced")
        fails.append("ERC produced no report, so nothing checked the sheet")
        shutil.rmtree(tmp, ignore_errors=True)
        return report_fails(fails)

    report = io.open(ercfile, encoding="utf-8").read()
    violations = re.findall(
        r"^\s*\[(\w+)\]: (.*)\n\s*; (\w+)\n\s*(@.*)$", report, re.M)
    if not violations and "Found 0 " not in report and (
            re.search(r"^\s*\[\w+\]:", report, re.M)):
        print("  ERC    report present but unparseable")
        fails.append("the ERC report has violations this script cannot read - "
                     "the format changed, so the check is not running")
        shutil.rmtree(tmp, ignore_errors=True)
        return report_fails(fails)

    errs = sum(1 for v in violations if v[2].lower() == "error")
    warns = sum(1 for v in violations if v[2].lower() == "warning")
    print("  ERC    %d error(s), %d warning(s)" % (errs, warns))
    if errs:
        kinds = {}
        for rule, text, sev, where in violations:
            if sev.lower() == "error":
                kinds.setdefault(rule, []).append(where.strip())
        for rule, wheres in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
            print("           %-26s x%-4d %s" % (rule, len(wheres), wheres[0][:64]))
        fails.append("ERC reported %d error(s)" % errs)
    elif warns:
        kinds = {}
        for rule, text, sev, where in violations:
            if sev.lower() == "warning":
                kinds.setdefault(rule, []).append(where.strip())
        for rule, wheres in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
            print("           %-26s x%-4d %s" % (rule, len(wheres), wheres[0][:64]))

    # ---- and again, on the file KiCad 10 will actually open -----------------
    #
    # This board is written in KiCad 7 format so kicad-cli can verify it, but
    # KiCad 8+ upgrades it on the way in, and the upgraded file is what a human
    # opens. The two are NOT equivalent: an earlier version of this generator
    # produced a sheet whose KiCad 7 netlist matched rev3.net exactly, 121 of
    # 121, while the SAME sheet upgraded reported six different_unit_net errors
    # - all three units of the dual op-amp had collapsed onto unit 1, because
    # KiCad 7 keeps a symbol's unit in an instances block that was never
    # written and the upgrade rebuilt it from nothing.
    #
    # A check that only looks at the format nobody opens is not a check.
    up = os.path.join(tmp, "upgraded.kicad_sch")
    shutil.copy(OUT, up)
    for side in ("fp-lib-table", "sym-lib-table"):
        src = os.path.join(HERE, side)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(tmp, side))
    subprocess.run([cli, "sch", "upgrade", up], capture_output=True, text=True)

    upnet = os.path.join(tmp, "up.net")
    subprocess.run([cli, "sch", "export", "netlist", "--output", upnet, up],
                   capture_output=True, text=True)
    if os.path.exists(upnet):
        got2, _ = parse_netlist(upnet)
        got2 = {n: v for n, v in got2.items() if not n.startswith("unconnected-")}
        bad = sum(1 for name, nodes in want.items() if got2.get(name) != nodes)
        print("  nets*  %d of %d still match after upgrade to KiCad 10"
              % (len(want) - bad, len(want)))
        if bad:
            fails.append("%d net(s) change meaning when the sheet is upgraded"
                         % bad)
    else:
        fails.append("could not export a netlist from the upgraded sheet")

    uperc = os.path.join(tmp, "up_erc.rpt")
    subprocess.run([cli, "sch", "erc", "--output", uperc, "--severity-all", up],
                   capture_output=True, text=True)
    if os.path.exists(uperc):
        rep2 = io.open(uperc, encoding="utf-8").read()
        v2 = re.findall(VIOLATION_RE, rep2, re.M)
        e2 = [x for x in v2 if x[2].lower() == "error"]
        w2 = [x for x in v2 if x[2].lower() == "warning"]
        print("  ERC*   %d error(s), %d warning(s) after upgrade"
              % (len(e2), len(w2)))
        if e2:
            kinds = {}
            for rule, _text, _sev in e2:
                kinds[rule] = kinds.get(rule, 0) + 1
            for rule, cnt in sorted(kinds.items(), key=lambda kv: -kv[1]):
                print("           %-26s x%d" % (rule, cnt))
            fails.append("the upgraded sheet has %d ERC error(s)" % len(e2))
    else:
        fails.append("could not run ERC on the upgraded sheet")
    shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print("\n%d CHECK(S) FAILED:" % len(fails))
        for f in fails[:25]:
            print("  " + f)
        return 1
    return 0


def main():
    sch, labels = build()
    sch.to_file(OUT)
    print("rev3 schematic: %d symbol instances, %d net labels"
          % (len(sch.schematicSymbols), len(sch.labels)))
    rc = check()
    if rc == 0:
        print("\nthe sheet, the netlist and the BOM all agree, and ERC is clean")
    return rc


if __name__ == "__main__":
    sys.exit(main())
