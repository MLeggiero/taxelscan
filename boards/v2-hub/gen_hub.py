#!/usr/bin/env python3
"""Build the v2 hub's netlist, and check it against what it has to match.

The module could be DERIVED - it was rev-1 with the MCU cut out, so every
connection came from a circuit already measured on hardware. The hub has no
such predecessor, so this file states it longhand. What it does NOT do is
retype pin numbers: every pin is looked up by NAME in KiCad's own symbol
libraries at generation time, so mcu("PA6") fails loudly if that pin does not
exist rather than quietly emitting a wrong number.

The hub's real content is the pin assignment, and the assignment is
over-constrained. OTG_HS in ULPI mode nails twelve pins to specific pads, seven
of which are ADC-capable, and the sixteen sense lines then do not fit on
ADC1+ADC2 alone. ADC_NOTE below works that out; check() asserts the result.

    ./gen_hub.py            writes hub.net and BOM.csv, and checks both

Standard library only. It reads .kicad_sym and ../v2-module/module.net with
regexes rather than pulling in kiutils, so it runs wherever KiCad is installed.
"""
import csv
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_NET = os.path.join(HERE, "..", "v2-module", "module.net")
OUT_NET = os.path.join(HERE, "hub.net")
OUT_BOM = os.path.join(HERE, "BOM.csv")

SYMBOL_DIRS = [
    r"C:/Program Files/KiCad/*/share/kicad/symbols",
    "/usr/share/kicad/symbols",
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    os.environ.get("KICAD_SYMBOL_DIR", ""),
]

NMOD = 8
SWITCH_REFS = ["U12", "U13", "U14", "U15"]


# ------------------------------------------------------------ symbol lookup
def symbol_dir():
    for pat in SYMBOL_DIRS:
        if not pat:
            continue
        for d in sorted(glob.glob(pat), reverse=True):
            if os.path.exists(os.path.join(d, "74xx.kicad_sym")):
                return d
    sys.exit("cannot find KiCad's symbol libraries; set KICAD_SYMBOL_DIR")


SYMDIR = symbol_dir()
_cache = {}


def sym(lib, name):
    """{pin name: [pin numbers]} for one library symbol, following (extends)."""
    key = (lib, name)
    if key in _cache:
        return _cache[key]
    path = os.path.join(SYMDIR, lib + ".kicad_sym")
    if not os.path.exists(path):
        sys.exit("no such library: " + path)
    text = open(path, encoding="utf-8").read()
    i = text.find('(symbol "%s"' % name)
    if i < 0:
        sys.exit("symbol %s:%s not found" % (lib, name))
    j = text.find('\n\t(symbol "', i + 10)
    blk = text[i:j if j > 0 else len(text)]
    out = {}
    for pname, number in re.findall(
            r'\(pin \w+ \w+.*?\(name "([^"]*)".*?\(number "([^"]*)"', blk, re.S):
        out.setdefault(pname, []).append(number)
    if not out:
        ext = re.search(r'\(extends "([^"]*)"', blk)
        if ext:
            out = sym(lib, ext.group(1))
    _cache[key] = out
    return out


# ------------------------------------------------------------------- parts
#
# ref(s), library, symbol, ordered part, footprint, note
#
# Three substitutions from the plan in ../v2-hub/README.md, all made so the pin
# numbers can be LOOKED UP instead of typed out of a datasheet:
#
#   USB3320C     -> USB3300-EZK. Same SMSC ULPI family, same QFN-32, same job.
#   74LVC16244A  -> 8 x SN74LVC244A, one per module. Eight octal packages carry
#                   the same 56 branches as four 16-bit ones in less total area
#                   (8 x 28.6 mm2 against 4 x 76 mm2), and one package beside
#                   each connector keeps a module's seven branches together
#                   instead of interleaving eight modules across four parts.
#   8 x TPS2553  -> 4 x TPS2561, a dual of the same thing.
#
# rev-1 already orders SN74LVC595A against a 74HC595D symbol, so using a
# 74HC244 symbol for an LVC part is what this repo does anyway.
ICS = [
    ("U1", "MCU_ST_STM32H7", "STM32H743ZITx", "STM32H743ZIT6",
     "Package_QFP:LQFP-144_20x20mm_P0.5mm", "480 MHz M7; ADC1/2/3 all 16-bit"),
    ("U2", "Interface_USB", "USB3300-EZK", "USB3300-EZK",
     "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
     "ULPI high-speed PHY"),
    ("U3-U10", "74xx", "74HC244", "SN74LVC244APW",
     "Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm",
     "one per module: 7 branches buffered, 1 spare"),
    ("U11", "74xx", "CD74HC4067SM", "CD74HC4067SM",
     "Package_SO:SSOP-24_5.3x8.2mm_P0.65mm",
     "housekeeping: 8 module rails + the hub's own"),
    ("U12-U15", "Interface_USB", "TPS2561", "TPS2561DRCR",
     "Package_SON:VSON-10-1EP_3x3mm_P0.5mm_EP1.65x2.4mm_ThermalVias",
     "dual current-limited switch, two modules each"),
    ("U16", "Regulator_Switching", "TPS563201", "TPS563201DDCR",
     "Package_TO_SOT_SMD:SOT-23-6", "buck 5V -> 3.64V"),
    ("U17,U18", "Regulator_Linear", "TLV75733PDBV", "TLV75733PDBVR",
     "Package_TO_SOT_SMD:SOT-23-5", "VDD_D and ROW_VCC, 1 A each"),
    ("U19", "Regulator_Linear", "LP5907MFX-3.3", "LP5907MFX-3.3",
     "Package_TO_SOT_SMD:SOT-23-5", "AVCC: 6.5 uVrms, feeds the op-amps"),
    ("Y1", "Device", "Crystal_GND24_Small", "24MHz",
     "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "PHY reference"),
    ("Y2", "Device", "Crystal_GND24_Small", "25MHz",
     "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "MCU HSE"),
    ("J1-J8", "Connector_Generic", "Conn_01x20", "FFC_20P_0.5mm",
     "Connector_FFC-FPC:Hirose_FH12-20S-0.5SH_1x20-1MP_P0.50mm_Horizontal",
     "one per module; pinout is module J3's, checked against it"),
    ("J9", "Connector", "USB_C_Receptacle_USB2.0_16P", "USB_C",
     "Connector_USB:USB_C_Receptacle_USB2.0_16P_Vertical", "to the host"),
    ("J10", "Connector", "Barrel_Jack", "5V_2A",
     "Connector_BarrelJack:BarrelJack_Horizontal",
     "USB's 500 mA default cannot carry 572 mA"),
    ("J11", "Connector_Generic", "Conn_01x05", "SWD",
     "Connector_PinHeader_1.27mm:PinHeader_1x05_P1.27mm_Vertical", "SWD + reset"),
]

# ref -> (library, symbol) for every IC, connector and crystal.
SYMOF = {}
for refspec, lib, symbol, _v, _fp, _n in ICS:
    for tok in refspec.split(","):
        m = re.fullmatch(r"([A-Z]+)(\d+)-[A-Z]*(\d+)", tok)
        rs = ([m.group(1) + str(i) for i in
               range(int(m.group(2)), int(m.group(3)) + 1)] if m else [tok])
        for r in rs:
            SYMOF[r] = (lib, symbol)


def p(ref, pin_name, index=0):
    """Pin NUMBER of a named pin on a placed part, from the real symbol."""
    lib, symbol = SYMOF[ref]
    nums = sym(lib, symbol).get(pin_name)
    if not nums:
        sys.exit("%s (%s:%s) has no pin named %r\n  it has: %s"
                 % (ref, lib, symbol, pin_name,
                    ", ".join(sorted(sym(lib, symbol)))))
    if index >= len(nums):
        sys.exit("%s has only %d pin(s) named %r" % (ref, len(nums), pin_name))
    return nums[index]


# --------------------------------------------------------- pin assignment
#
# ULPI is not a choice. OTG_HS in ULPI mode has one mapping on this package,
# and DIR/NXT are the tight ones: their alternates are PI11 and PH4, and
# LQFP144 carries neither port I nor PH2..PH5. So all twelve are fixed.
ULPI = {
    "ULPI_CK": "PA5",  "ULPI_D0": "PA3",  "ULPI_D1": "PB0",  "ULPI_D2": "PB1",
    "ULPI_D3": "PB10", "ULPI_D4": "PB11", "ULPI_D5": "PB12", "ULPI_D6": "PB13",
    "ULPI_D7": "PB5",  "ULPI_DIR": "PC2_C", "ULPI_STP": "PC0",
    "ULPI_NXT": "PC3_C",
}

# ADC_NOTE - why bank B is on ADC3 and not on ADC2.
#
# Seven of those twelve ULPI pins are ADC-capable: PA3, PA5, PB0, PB1, PC0 and
# the two _C pads PC2/PC3. What is left that can reach ADC1 or ADC2 on LQFP144
# is PA0 PA1 PA2 PA4 PA6 PA7 PC1 PC4 PC5 PF11 PF12 PF13 PF14 - thirteen pins
# for sixteen sense lines. Three short, with no way to move ULPI and free more.
#
# So bank A goes to ADC1 and bank B to ADC3, which owns eight pins (PF3..PF10)
# no other ADC can reach. The cost is real and it lands in firmware: ADC3 sits
# in power domain D3, its BDMA can only write SRAM4, and a frame therefore
# arrives in two memories. On THIS part that is only plumbing, because the
# H743's ADC3 is 16-bit like the other two. On an H723 ADC3 is 12-bit and the
# same split would silently halve bank B's resolution.
ADC1_SENSE_A = ["PA0", "PA1", "PA2", "PA4", "PA6", "PA7", "PC4", "PC5"]
ADC3_SENSE_B = ["PF3", "PF4", "PF5", "PF6", "PF7", "PF8", "PF9", "PF10"]
ADC2_HOUSEKEEPING = "PF13"

# Every ADC-capable pin this package has, so check() can tell a real analog pin
# from a wish. ADC1/2 share all of these except the PF pairs; ADC3 has its own
# PF block. PC2_C/PC3_C are ADC3's direct inputs and are spent on ULPI.
ADC12_PINS = set("PA0 PA1 PA2 PA3 PA4 PA5 PA6 PA7 PB0 PB1 PC0 PC1 PC2_C PC3_C "
                 "PC4 PC5 PF11 PF12 PF13 PF14".split())
ADC3_PINS = set("PF3 PF4 PF5 PF6 PF7 PF8 PF9 PF10 PC0 PC1 PC2_C PC3_C".split())

# The scan control group reproduces rev-1's GPIO layout on purpose: latch,
# clock, data, then the four mux selects contiguous above them. scan.h sets
# S0..S3 with a single store so no intermediate code ever appears on the
# selects, and keeping the same bit positions ports that to GPIOE->ODR.
CONTROL = {
    "ROW_LATCH": "PE1", "ROW_CLK": "PE2", "ROW_DATA": "PE3",
    "MUX_S0": "PE4", "MUX_S1": "PE5", "MUX_S2": "PE6", "MUX_S3": "PE7",
}
# The module calls the cable side of the two damped signals ROW_CLK_MCU and
# ROW_LATCH_MCU, keeping ROW_CLK/ROW_LATCH for the far side of its R3/R4. The
# hub drives the cable, so it is the _MCU nets it lands on.
CABLE_ALIAS = {"ROW_CLK_MCU": "ROW_CLK", "ROW_LATCH_MCU": "ROW_LATCH"}

HOUSEKEEP_SEL = {"HK_S0": "PE8", "HK_S1": "PE9", "HK_S2": "PE10",
                 "HK_S3": "PE11"}

# Port D is the power-control port: the low byte enables the eight modules, the
# high byte reads their faults. One register write, one register read - and the
# fault vector says WHICH module, which is the same reason the conditioning
# telemetry is per mat rather than summed.
MOD_EN = ["PD%d" % k for k in range(NMOD)]
MOD_FAULT = ["PD%d" % (8 + k) for k in range(NMOD)]

# Current, in mA, by rail. Enough to size the tree and to say what the 5 V
# input has to supply - which is the number that rules out bus power.
#   regulator: (rating, [(what, mA), ...])
LOADS = {
    "U17": (1000, [("STM32H743 at 480 MHz, peripherals on", 270),
                   ("USB3300 in high-speed", 30),
                   ("8 x LVC244 driving 7 x ~30 pF of cable", 40),
                   ("fault pullups, LED, margin", 10)]),
    "U18": (1000, [("8 modules x 30 mA of row drive", 8 * 30)]),
    "U19": (250, [("8 modules x 3 mA of mux and op-amp", 8 * 3)]),
}
BUCK_V, BUCK_EFF, BUCK_RATING = 3.64, 0.90, 3000

MISC = {
    "SWDIO": "PA13", "SWCLK": "PA14",
    "UART_TX": "PB6", "UART_RX": "PB7",
    "PHY_RESET": "PG0", "LED_STATUS": "PG1",
    "MCU_OSC_IN": "PH0", "MCU_OSC_OUT": "PH1",
}

# How a module-side net name becomes a hub net name. Grounds and AVCC are
# global on the hub - this is the board where the eight cables come together -
# and everything else is per module. Used to wire J1..J8 and, in check(),
# to prove nothing on the module's J3 was left unmapped.
CABLE_MAP = {
    "PWR_GND": "PWR_GND", "AGND": "AGND", "AVCC": "AVCC",
    "ROW_VCC": "M{k}_ROW_VCC",
    "SENSE_A_OUT": "M{k}_SENSE_A", "SENSE_B_OUT": "M{k}_SENSE_B",
    "ROW_VCC_SENSE": "M{k}_RAIL",
    "MUX_S0": "M{k}_MUX_S0", "MUX_S1": "M{k}_MUX_S1",
    "MUX_S2": "M{k}_MUX_S2", "MUX_S3": "M{k}_MUX_S3",
    "ROW_DATA": "M{k}_ROW_DATA", "ROW_CLK_MCU": "M{k}_ROW_CLK",
    "ROW_LATCH_MCU": "M{k}_ROW_LATCH",
}

# The seven signals that fan out, in buffer order: 1A0..1A3 then 2A0..2A2.
FANOUT = ["ROW_DATA", "ROW_CLK", "ROW_LATCH", "MUX_S0", "MUX_S1", "MUX_S2",
          "MUX_S3"]
BUF_IN = ["1A0", "1A1", "1A2", "1A3", "2A0", "2A1", "2A2"]
BUF_OUT = ["1Y0", "1Y1", "1Y2", "1Y3", "2Y0", "2Y1", "2Y2"]
BUF_REFS = ["U%d" % (3 + k) for k in range(NMOD)]


# --------------------------------------------------------------- the module
def module_j3():
    """{pin number: net name} for J3 on the module, read from module.net."""
    if not os.path.exists(MODULE_NET):
        sys.exit("cannot read %s - run ../v2-module/gen_module.py first"
                 % MODULE_NET)
    text = open(MODULE_NET).read()
    out = {}
    for blk in re.split(r"\n    \(net ", text)[1:]:
        name = re.search(r'\(name "([^"]*)"\)', blk).group(1)
        for ref, num in re.findall(r'\(ref "([^"]*)"\) \(pin "([^"]*)"\)', blk):
            if ref == "J3":
                out[num] = name
    return out


# ----------------------------------------------------------------- passives
class Passives(object):
    """Allocate a refdes and record what it is, so the BOM cannot drift.

    Every passive in the netlist gets its value and footprint here, at the
    moment it is created. That makes 'the BOM lists exactly the parts the
    netlist uses' true by construction, and check() asserts it anyway.
    """

    def __init__(self):
        self.n = {}
        self.spec = {}

    def new(self, prefix, value, footprint, note):
        self.n[prefix] = self.n.get(prefix, 0) + 1
        ref = "%s%d" % (prefix, self.n[prefix])
        self.spec[ref] = (value, footprint, note)
        return ref


C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
R0603 = "Resistor_SMD:R_0603_1608Metric"
R0805 = "Resistor_SMD:R_0805_2012Metric"


# -------------------------------------------------------------------- build
def build():
    nets = {}
    pas = Passives()

    def n(net, ref, pin_number):
        nets.setdefault(net, []).append((ref, str(pin_number)))

    def mcu(net, pin_name, index=0):
        n(net, "U1", p("U1", pin_name, index))

    def cap(net_a, net_b, value, fp, note):
        ref = pas.new("C", value, fp, note)
        n(net_a, ref, "1")
        n(net_b, ref, "2")
        return ref

    def two(prefix, net_a, net_b, value, fp, note):
        ref = pas.new(prefix, value, fp, note)
        n(net_a, ref, "1")
        n(net_b, ref, "2")
        return ref

    def res(net_a, net_b, value, fp, note):
        ref = pas.new("R", value, fp, note)
        n(net_a, ref, "1")
        n(net_b, ref, "2")
        return ref

    # -- 1. the eight cables --------------------------------------------
    #
    # Wired straight from the module's own J3, so a change there fails here
    # rather than producing a hub that looks right and is mirrored.
    j3 = module_j3()
    for k in range(NMOD):
        conn = "J%d" % (k + 1)
        for num, mod_net in sorted(j3.items(), key=lambda x: int(x[0])):
            n(CABLE_MAP[mod_net].format(k=k), conn, num)

    # -- 2. control fanout ----------------------------------------------
    #
    # One octal buffer per module. The seven branches leave together and each
    # gets its own series resistor: at LVC edge rates a 500 mm cable is a
    # transmission line (6 ns round trip against a 2 ns edge), and ROW_DATA
    # and the four selects have no series resistor at the far end the way
    # ROW_CLK and ROW_LATCH do through the module's R3/R4.
    for k in range(NMOD):
        buf = BUF_REFS[k]
        n("VDD_D", buf, p(buf, "VCC"))
        n("PWR_GND", buf, p(buf, "GND"))
        n("PWR_GND", buf, p(buf, "1OE"))       # outputs always enabled
        n("PWR_GND", buf, p(buf, "2OE"))
        n("PWR_GND", buf, p(buf, "2A3"))       # spare input, never floating
        cap("VDD_D", "PWR_GND", "100nF", C0603, "buffer decoupling")
        # two 4-element arrays carry the seven branches, one element spare
        arr = [pas.new("RN", "100R", "Resistor_SMD:R_Array_Convex_4x0603",
                       "series termination, module %d" % k) for _ in (0, 1)]
        for i, sig in enumerate(FANOUT):
            n(sig, buf, p(buf, BUF_IN[i]))                 # shared MCU signal
            n("M%d_%s_B" % (k, sig), buf, p(buf, BUF_OUT[i]))
            pack, elem = arr[i // 4], i % 4                # R1..R4 = 1&8 2&7 ..
            n("M%d_%s_B" % (k, sig), pack, elem + 1)
            n("M%d_%s" % (k, sig), pack, 8 - elem)

    for sig, port in CONTROL.items():
        mcu(sig, port)

    # -- 3. analog in ----------------------------------------------------
    #
    # The module README asks for 1 nF C0G to AGND on each sense line at this
    # end, as a charge reservoir for the sample-and-hold: 51 ohm x 1.05 nF is
    # 54 ns against a 15 us dwell. That is this board's job, so it is here.
    for k in range(NMOD):
        for bank, pins_ in (("A", ADC1_SENSE_A), ("B", ADC3_SENSE_B)):
            net = "M%d_SENSE_%s" % (k, bank)
            mcu(net, pins_[k])
            cap(net, "AGND", "1nF", C0603,
                "sample-and-hold reservoir, module %d bank %s" % (k, bank))

    # -- 4. housekeeping mux --------------------------------------------
    #
    # Eight rail senses plus the hub's own ROW_VCC on channel 8, so firmware
    # can read the cable's IR drop directly as the difference of two channels
    # rather than inferring it. Channels 9..15 spare.
    for k in range(NMOD):
        n("M%d_RAIL" % k, "U11", p("U11", "I%d" % k))
    n("ROW_VCC", "U11", p("U11", "I8"))
    n("HK_COM", "U11", p("U11", "COM"))
    n("AVCC", "U11", p("U11", "VCC"))
    n("AGND", "U11", p("U11", "GND"))
    n("AGND", "U11", p("U11", "~{E}"))
    for i, (sig, port) in enumerate(sorted(HOUSEKEEP_SEL.items())):
        n(sig, "U11", p("U11", "S%d" % i))
        mcu(sig, port)
    mcu("HK_COM", ADC2_HOUSEKEEPING)
    cap("AVCC", "AGND", "100nF", C0603, "housekeeping mux decoupling")

    # -- 5. per-module power switching -----------------------------------
    #
    # Only ROW_VCC is switched. It is the rail with 22 uF of bulk on the far
    # end of 0.41 ohm of cable, so it is the one whose hot-plug inrush is
    # otherwise unlimited; AVCC's 400 nF is not worth interrupting, and
    # breaking the analog rail per module would upset the others.
    for k in range(NMOD):
        sw = SWITCH_REFS[k // 2]
        ch = (k % 2) + 1
        if k % 2 == 0:
            n("ROW_VCC", sw, p(sw, "IN", 0))
            n("ROW_VCC", sw, p(sw, "IN", 1))
            n("PWR_GND", sw, p(sw, "GND"))
            n("PWR_GND", sw, p(sw, "PAD"))
            res("ILIM%d" % (k // 2 + 1), "PWR_GND", "150k", R0603,
                "TPS2561 current limit - VERIFY against the datasheet curve")
            n("ILIM%d" % (k // 2 + 1), sw, p(sw, "ILM"))
            cap("ROW_VCC", "PWR_GND", "100nF", C0603, "switch input")
        n("M%d_ROW_VCC" % k, sw, p(sw, "OUT%d" % ch))
        n("M%d_EN" % k, sw, p(sw, "EN%d" % ch))
        n("M%d_FAULT" % k, sw, p(sw, "~{FAULT%d}" % ch))
        mcu("M%d_EN" % k, MOD_EN[k])
        mcu("M%d_FAULT" % k, MOD_FAULT[k])
        res("VDD_D", "M%d_FAULT" % k, "10k", R0603,
            "open-drain fault pullup, module %d" % k)
        cap("M%d_ROW_VCC" % k, "PWR_GND", "100nF", C0603,
            "switch output, module %d" % k)

    # -- 6. the ULPI PHY --------------------------------------------------
    for sig, port in ULPI.items():
        mcu(sig, port)
    phy = {"ULPI_CK": "CLKOUT", "ULPI_DIR": "DIR", "ULPI_STP": "STP",
           "ULPI_NXT": "NXT"}
    for i in range(8):
        phy["ULPI_D%d" % i] = "DATA%d" % i
    for net, pin_name in phy.items():
        n(net, "U2", p("U2", pin_name))
    n("PHY_RESET", "U2", p("U2", "RESET"))
    mcu("PHY_RESET", MISC["PHY_RESET"])
    for i in range(4):
        n("VDD_D", "U2", p("U2", "VDD3.3", i))
        cap("VDD_D", "PWR_GND", "100nF", C0603, "PHY 3.3V decoupling")
    for i in range(2):
        n("PHY_VDD18", "U2", p("U2", "VDD1.8", i))
        n("PWR_GND", "U2", p("U2", "GND", i))
    n("PWR_GND", "U2", p("U2", "GND", 2))
    n("PHY_VDDA18", "U2", p("U2", "VDDA1.8"))
    n("PHY_VDD18", "U2", p("U2", "REG_EN"))        # internal 1.8V regulator on
    cap("PHY_VDD18", "PWR_GND", "1uF", C0603, "PHY internal 1.8V")
    cap("PHY_VDDA18", "PWR_GND", "1uF", C0603, "PHY analog 1.8V")
    res("PHY_RBIAS", "PWR_GND", "8.06k", R0603, "PHY bias, 1%")
    n("PHY_RBIAS", "U2", p("U2", "RBIAS"))
    n("USB_DP", "U2", p("U2", "DP"))
    n("USB_DM", "U2", p("U2", "DM"))
    n("USB_VBUS", "U2", p("U2", "VBUS"))
    n("PWR_GND", "U2", p("U2", "ID"))              # device only
    n("PWR_GND", "U2", p("U2", "EXTVBUS"))
    cap("USB_VBUS", "PWR_GND", "100nF", C0603, "VBUS detect")
    n("PHY_XI", "U2", p("U2", "XI"))
    n("PHY_XO", "U2", p("U2", "XO"))
    n("PHY_XI", "Y1", "1")
    n("PHY_XO", "Y1", "3")
    n("PWR_GND", "Y1", "2")
    n("PWR_GND", "Y1", "4")
    cap("PHY_XI", "PWR_GND", "18pF", C0603, "24 MHz load")
    cap("PHY_XO", "PWR_GND", "18pF", C0603, "24 MHz load")

    # -- 7. USB-C --------------------------------------------------------
    for pn in ("A1", "B1", "A12", "B12"):
        n("PWR_GND", "J9", pn)
    for pn in ("A4", "B4", "A9", "B9"):
        n("USB_VBUS", "J9", pn)
    n("USB_DP", "J9", "A6")
    n("USB_DP", "J9", "B6")
    n("USB_DM", "J9", "A7")
    n("USB_DM", "J9", "B7")
    res("USB_CC1", "PWR_GND", "5.1k", R0603, "USB-C sink advertisement")
    res("USB_CC2", "PWR_GND", "5.1k", R0603, "USB-C sink advertisement")
    n("USB_CC1", "J9", "A5")
    n("USB_CC2", "J9", "B5")
    n("PWR_GND", "J9", "SH")

    # -- 8. the MCU itself ------------------------------------------------
    #
    # VSSA joins VSS. They are bonded inside the package, so giving them
    # separate nets would be a fiction that layout could not honour; the ADC
    # therefore references PWR_GND while the sense signals return on AGND.
    # See README.md - the residual offset is static and the per-frame dark
    # reference removes it.
    for i in range(11):
        mcu("VDD_D", "VDD", i)
        cap("VDD_D", "PWR_GND", "100nF", C0603, "MCU VDD decoupling")
    for i in range(9):
        mcu("PWR_GND", "VSS", i)
    mcu("PWR_GND", "VSSA")
    mcu("VDD_D", "VBAT")
    cap("VDD_D", "PWR_GND", "100nF", C0603, "VBAT")
    mcu("VDDA_MCU", "VDDA")
    two("FB", "AVCC", "VDDA_MCU", "600R@100MHz",
        "Inductor_SMD:L_0603_1608Metric",
        "keeps the MCU's analog supply off the cable-fed AVCC rail")
    cap("VDDA_MCU", "PWR_GND", "100nF", C0603, "VDDA")
    cap("VDDA_MCU", "PWR_GND", "1uF", C0603, "VDDA")
    # VREF+ shares the filtered analog supply. The scan is ratiometric twice
    # over - a taxel reads V/VREF and the rail sense reads ROW_VCC/VREF, so
    # dividing one by the other cancels BOTH the rail and the reference. An
    # external reference would buy accuracy the measurement does not use.
    mcu("VDDA_MCU", "VREF+")
    cap("VDDA_MCU", "PWR_GND", "1uF", C0805, "VREF+")
    mcu("VDD_D", "VDD33_USB")
    cap("VDD_D", "PWR_GND", "4.7uF", C0805, "VDD33_USB")
    for i in range(2):
        mcu("VCAP%d" % (i + 1), "VCAP", i)
        cap("VCAP%d" % (i + 1), "PWR_GND", "2.2uF", C0603, "core regulator")
    mcu("VDD_D", "PDR_ON")
    res("BOOT0", "PWR_GND", "10k", R0603, "boot from flash")
    mcu("BOOT0", "BOOT0")
    mcu("NRST", "NRST")
    cap("NRST", "PWR_GND", "100nF", C0603, "reset")
    mcu("MCU_OSC_IN", MISC["MCU_OSC_IN"])
    mcu("MCU_OSC_OUT", MISC["MCU_OSC_OUT"])
    n("MCU_OSC_IN", "Y2", "1")
    n("MCU_OSC_OUT", "Y2", "3")
    n("PWR_GND", "Y2", "2")
    n("PWR_GND", "Y2", "4")
    cap("MCU_OSC_IN", "PWR_GND", "18pF", C0603, "25 MHz load")
    cap("MCU_OSC_OUT", "PWR_GND", "18pF", C0603, "25 MHz load")
    for sig in ("SWDIO", "SWCLK", "UART_TX", "UART_RX", "LED_STATUS"):
        mcu(sig, MISC[sig])
    n("SWCLK", "J11", "1")
    n("SWDIO", "J11", "2")
    n("PWR_GND", "J11", "3")
    n("VDD_D", "J11", "4")
    n("NRST", "J11", "5")
    res("LED_STATUS", "LED_A", "1k", R0603, "status LED")
    cap("VDD_D", "PWR_GND", "10uF", C0805, "MCU bulk")
    cap("VDD_D", "PWR_GND", "10uF", C0805, "MCU bulk")

    # -- 9. power tree ----------------------------------------------------
    #
    # 5 V straight to an LDO would burn 1.7 V x 0.6 A = 1 W in a SOT-23. The
    # buck drops to 3.64 V first, leaving each LDO 340 mV of headroom and
    # about 200 mW total.
    n("+5V", "J10", "1")
    n("PWR_GND", "J10", "2")
    n("+5V", "U16", p("U16", "VIN"))
    n("PWR_GND", "U16", p("U16", "GND"))
    n("+5V", "U16", p("U16", "EN"))
    n("SW", "U16", p("U16", "SW"))
    n("VBST", "U16", p("U16", "VBST"))
    n("FB", "U16", p("U16", "VFB"))
    cap("VBST", "SW", "100nF", C0603, "bootstrap")
    cap("+5V", "PWR_GND", "10uF", C0805, "buck input")
    cap("+5V", "PWR_GND", "100nF", C0603, "buck input")
    ind = pas.new("L", "2.2uH", "Inductor_SMD:L_1210_3225Metric", "buck output")
    n("SW", ind, "1")
    n("+3V6", ind, "2")
    cap("+3V6", "PWR_GND", "22uF", C0805, "buck output")
    cap("+3V6", "PWR_GND", "22uF", C0805, "buck output")
    res("+3V6", "FB", "37.4k", R0603, "3.64 V = 0.768 x (1 + 37.4/10)")
    res("FB", "PWR_GND", "10k", R0603, "buck feedback")

    for ldo, out, gnd, note in (("U17", "VDD_D", "PWR_GND", "MCU, PHY, buffers"),
                                ("U18", "ROW_VCC", "PWR_GND", "8 x 30 mA rows"),
                                ("U19", "AVCC", "AGND", "8 x 3 mA analog")):
        n("+3V6", ldo, p(ldo, "IN"))
        n(gnd, ldo, p(ldo, "GND"))
        n("+3V6", ldo, p(ldo, "EN"))
        n(out, ldo, p(ldo, "OUT"))
        cap("+3V6", gnd, "1uF", C0603, "LDO input: " + note)
        cap(out, gnd, "10uF", C0805, "LDO output: " + note)

    # The grounds meet here and nowhere else. 0805 so the link is not the
    # limiting conductor for the row return, and one component so the star
    # point is a place on the board rather than an intention.
    res("PWR_GND", "AGND", "0R", R0805,
        "THE single-point ground join - see README")

    # LED cathode to the digital ground, not the analog one.
    n("PWR_GND", "D1", "1")
    n("LED_A", "D1", "2")
    pas.spec["D1"] = ("LED", "LED_SMD:LED_0603_1608Metric", "status")

    return {k: sorted(set(v)) for k, v in nets.items() if v}, pas


def budget():
    """Per-rail current, and what the 5 V input therefore has to deliver."""
    per = {r: sum(mA for _, mA in items) for r, (_, items) in LOADS.items()}
    total = sum(per.values())
    return per, total, total * BUCK_V / 5.0 / BUCK_EFF


# --------------------------------------------------------------- emit
def emit_net(nets, path):
    with open(path, "w") as f:
        f.write('(export (version "E")\n  (design (source "gen_hub.py")'
                ' (tool "taxelscan board-v2"))\n  (nets\n')
        for i, (name, nodes) in enumerate(sorted(nets.items()), 1):
            f.write('    (net (code "%d") (name "%s")\n' % (i, name))
            for ref, pn in nodes:
                f.write('      (node (ref "%s") (pin "%s"))\n' % (ref, pn))
            f.write("    )\n")
        f.write("  )\n)\n")


def emit_bom(pas, path):
    """One row per distinct (value, footprint, purpose), refs collapsed."""
    rows = []
    for refspec, _lib, _sym, value, fp, note in ICS:
        rows.append([refspec, value, fp, note])
    groups = {}
    for ref, (value, fp, note) in pas.spec.items():
        key = (value, fp, re.sub(r"\s*\d+$", "", note.split(",")[0]))
        groups.setdefault(key, []).append(ref)
    for (value, fp, note), refs in sorted(groups.items(),
                                          key=lambda x: x[1][0]):
        refs.sort(key=lambda r: (re.sub(r"\d", "", r), int(re.sub(r"\D", "", r))))
        nums = [int(re.sub(r"\D", "", r)) for r in refs]
        contiguous = (len({re.sub(r"\d", "", r) for r in refs}) == 1
                      and nums == list(range(nums[0], nums[0] + len(nums))))
        span = (refs[0] + "-" + refs[-1]) if contiguous and len(refs) > 2             else ",".join(refs)
        rows.append([span, value, fp, "%s (x%d)" % (note, len(refs))
                     if len(refs) > 1 else note])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Reference", "Value", "Footprint", "Note"])
        w.writerows(rows)
    return rows


# --------------------------------------------------------------- checks
def check(nets, pas, bom_rows):
    """Assert what the plan claims. Each of these can actually fail.

    The point is not that the script agrees with itself - it is that a wrong
    pin, a mirrored connector or a stranded module fails HERE rather than at
    bring-up, when eight cables are already plugged in.
    """
    fails = []

    def want(cond, msg):
        if not cond:
            fails.append(msg)

    net_of = {}
    for name, nodes in nets.items():
        for node in nodes:
            want(node not in net_of, "%s.%s is on both %s and %s"
                 % (node[0], node[1], net_of.get(node), name))
            net_of[node] = name
    pins_on = lambda net: set(nets.get(net, []))

    # 1. Every pin number in the netlist exists on the real symbol.
    for ref, num in net_of:
        if ref in SYMOF:
            lib, symbol = SYMOF[ref]
            allnums = {x for v in sym(lib, symbol).values() for x in v}
            want(num in allnums, "%s has no pin %s (%s:%s)"
                 % (ref, num, lib, symbol))

    # 2. The cables match the module's, pin for pin.
    j3 = module_j3()
    want(len(j3) == 20, "module J3 has %d pins, not 20" % len(j3))
    for num, mod_net in j3.items():
        want(mod_net in CABLE_MAP,
             "module J3 pin %s carries %s and CABLE_MAP does not name it"
             % (num, mod_net))
    for k in range(NMOD):
        conn = "J%d" % (k + 1)
        for num, mod_net in j3.items():
            want(net_of.get((conn, num)) == CABLE_MAP[mod_net].format(k=k),
                 "%s.%s is %s, module J3.%s is %s"
                 % (conn, num, net_of.get((conn, num)), num, mod_net))
        # the analog guard structure survives to this end of the cable
        want(net_of[(conn, "6")] == net_of[(conn, "8")] == "AGND",
             "%s pin 7 is not flanked by AGND" % conn)
        want(net_of[(conn, "10")] == net_of[(conn, "11")] == "AGND",
             "%s pin 9 is not flanked by AGND" % conn)
    # Which output a '244 input drives is a fact about the part, and it is
    # written in the symbol's pin NAMES: nAm drives nYm. Recovering it that way
    # means BUF_IN/BUF_OUT are checked rather than trusted - crossing two of
    # them is invisible to a trace that uses the same two lists to walk.
    lib0, sym0 = SYMOF[BUF_REFS[0]]
    pair_of = {}
    for nm in sym(lib0, sym0):
        m = re.fullmatch(r"(\d)A(\d)", nm)
        if m and ("%sY%s" % m.groups()) in sym(lib0, sym0):
            pair_of[nm] = "%sY%s" % m.groups()
    want(len(pair_of) == 8, "the buffer symbol has %d A/Y pairs, not 8"
         % len(pair_of))
    for i, a in enumerate(BUF_IN):
        want(pair_of.get(a) == BUF_OUT[i],
             "%s drives %s, but the fanout wires it to %s"
             % (a, pair_of.get(a), BUF_OUT[i]))

    adc1 = {p("U1", x) for x in ADC1_SENSE_A}
    adc3 = {p("U1", x) for x in ADC3_SENSE_B}
    hk_in = {p("U11", "I%d" % i) for i in range(NMOD)}
    sw_out = {(r, p(r, "OUT%d" % c)) for r in SWITCH_REFS for c in (1, 2)}
    mcu_port = {pn: nm for nm, pns in sym(*SYMOF["U1"]).items() for pn in pns}
    holder = {}
    for nm, nodes in nets.items():
        for node in nodes:
            holder[node] = nm

    def to_mcu(net):
        """Connector pin -> series array -> buffer -> the MCU pin driving it."""
        for r, pn in nets.get(net, []):
            if not r.startswith("RN"):
                continue
            up = holder.get((r, str(9 - int(pn))))      # 1&8, 2&7, 3&6, 4&5
            for br, bpn in nets.get(up, []):
                if br not in BUF_REFS:
                    continue
                back = {p(br, y): a for a, y in pair_of.items()}
                if bpn not in back:
                    continue
                src = holder.get((br, p(br, back[bpn])))
                return [x for x in nets.get(src, []) if x[0] == "U1"]
        return []

    ROLE = {
        "SENSE_A_OUT": (lambda net: any(r == "U1" and n in adc1
                                        for r, n in nets.get(net, [])),
                        "an ADC1 pin"),
        "SENSE_B_OUT": (lambda net: any(r == "U1" and n in adc3
                                        for r, n in nets.get(net, [])),
                        "an ADC3 pin"),
        "ROW_VCC_SENSE": (lambda net: any(r == "U11" and n in hk_in
                                          for r, n in nets.get(net, [])),
                          "the housekeeping mux"),
        "ROW_VCC": (lambda net: any((r, n) in sw_out
                                    for r, n in nets.get(net, [])),
                    "a current-limited switch output"),
    }
    for k in range(NMOD):
        conn = "J%d" % (k + 1)
        for num, mod_net in j3.items():
            hub_net = net_of.get((conn, num))
            if mod_net in ROLE:
                ok, what = ROLE[mod_net]
                want(ok(hub_net), "%s.%s is %s on the module, but %s does not "
                     "reach %s" % (conn, num, mod_net, hub_net, what))
            elif CABLE_ALIAS.get(mod_net, mod_net) in CONTROL:
                port = CONTROL[CABLE_ALIAS.get(mod_net, mod_net)]
                landed = to_mcu(hub_net)
                want(landed == [("U1", p("U1", port))],
                     "%s.%s is %s on the module; tracing %s back through its "
                     "resistor and buffer lands on %s, not %s"
                     % (conn, num, mod_net, hub_net,
                        ", ".join(mcu_port[pn] for _, pn in landed)
                        or "nothing", port))
            else:
                want(hub_net == mod_net, "%s.%s is %s, not the global %s"
                     % (conn, num, hub_net, mod_net))

    # nothing crosses between modules
    for name in nets:
        m = re.match(r"M(\d)_", name)
        if m:
            conns = {r for r, _ in nets[name] if r.startswith("J")}
            want(conns <= {"J%d" % (int(m.group(1)) + 1)},
                 "%s reaches %s" % (name, sorted(conns)))

    # 3. The analog assignment is the one ADC_NOTE argues for.
    used = {}
    for name, port in list(ULPI.items()) + list(CONTROL.items()) + \
            list(HOUSEKEEP_SEL.items()) + list(MISC.items()) + \
            [("M%d_EN" % k, MOD_EN[k]) for k in range(NMOD)] + \
            [("M%d_FAULT" % k, MOD_FAULT[k]) for k in range(NMOD)] + \
            [("M%d_SENSE_A" % k, ADC1_SENSE_A[k]) for k in range(NMOD)] + \
            [("M%d_SENSE_B" % k, ADC3_SENSE_B[k]) for k in range(NMOD)] + \
            [("HK_COM", ADC2_HOUSEKEEPING)]:
        want(port not in used, "%s is on both %s and %s"
             % (port, used.get(port), name))
        used[port] = name
    for k in range(NMOD):
        want(ADC1_SENSE_A[k] in ADC12_PINS,
             "%s cannot reach ADC1" % ADC1_SENSE_A[k])
        want(ADC3_SENSE_B[k] in ADC3_PINS,
             "%s cannot reach ADC3" % ADC3_SENSE_B[k])
    want(ADC2_HOUSEKEEPING in ADC12_PINS,
         "%s cannot reach ADC2" % ADC2_HOUSEKEEPING)
    want(len(set(ADC1_SENSE_A) | set(ADC3_SENSE_B)) == 16,
         "the sixteen sense lines do not land on sixteen distinct pins")
    # the collision that forced bank B onto ADC3 - if it ever stops being
    # true, this assignment is more conservative than it needs to be
    spare = ADC12_PINS - set(ULPI.values()) - {ADC2_HOUSEKEEPING}
    want(len(spare) < 16,
         "ADC1/ADC2 now have %d free pins; bank B need not be on ADC3"
         % len(spare))
    for sig, port in ULPI.items():
        want(used[port] == sig, "%s is not on %s" % (sig, port))

    # 4. Fanout: every module gets every signal, once, through its own R.
    for sig in FANOUT:
        drivers = [r for r, _ in nets[sig] if r == "U1"]
        want(len(drivers) == 1, "%s has %d MCU pins" % (sig, len(drivers)))
        want(len([r for r, _ in nets[sig] if r.startswith("U")]) == NMOD + 1,
             "%s does not reach all %d buffers" % (sig, NMOD))
    for k in range(NMOD):
        for sig in FANOUT:
            out = "M%d_%s" % (k, sig)
            want(any(r.startswith("RN") for r, _ in nets[out]),
                 "%s does not pass through a series resistor" % out)
            want(any(r == "J%d" % (k + 1) for r, _ in nets[out]),
                 "%s does not reach its connector" % out)
            raw = "M%d_%s_B" % (k, sig)
            want(not any(r.startswith("J") for r, _ in nets[raw]),
                 "%s reaches a connector unterminated" % raw)

    # 5. Analog integrity.
    for k in range(NMOD):
        for bank in "AB":
            net = "M%d_SENSE_%s" % (k, bank)
            caps = [r for r, _ in nets[net] if r.startswith("C")]
            want(len(caps) == 1, "%s has %d terminations, want 1"
                 % (net, len(caps)))
            want(all(pas.spec[c][0] == "1nF" for c in caps),
                 "%s is terminated with %s, not 1nF"
                 % (net, [pas.spec[c][0] for c in caps]))
            port = ADC1_SENSE_A[k] if bank == "A" else ADC3_SENSE_B[k]
            want(("U1", p("U1", port)) in pins_on(net),
                 "%s does not reach the MCU on %s" % (net, port))
    want(("U11", p("U11", "VCC")) in pins_on("AVCC")
         and ("U11", p("U11", "GND")) in pins_on("AGND"),
         "the housekeeping mux is not on the analog rail")
    want(("U19", p("U19", "GND")) in pins_on("AGND"),
         "the AVCC regulator does not return on AGND")

    # 6. The grounds meet exactly once, at a part rather than by accident.
    bridged = {}
    for name, nodes in nets.items():
        for ref, pn in nodes:
            if ref in pas.spec:
                bridged.setdefault(ref, {})[pn] = name
    joins = sorted(r for r, pn in bridged.items()
                   if set(pn.values()) == {"PWR_GND", "AGND"})
    want(len(joins) == 1, "PWR_GND and AGND meet at %d places: %s"
         % (len(joins), joins or "none - they are separate boards"))
    if len(joins) == 1:
        want(pas.spec[joins[0]][0] == "0R",
             "the ground join is %s, not 0R" % pas.spec[joins[0]][0])
    for ref, _ in nets["AGND"]:
        want(ref not in ("U16", "U17", "U18") and not ref.startswith("Y"),
             "%s returns on AGND; only the sense path may" % ref)

    # 7. Power: every module switched, every switch reporting.
    for k in range(NMOD):
        sw = [r for r, _ in nets["M%d_ROW_VCC" % k] if r.startswith("U")]
        want(len(sw) == 1 and sw[0] in SWITCH_REFS,
             "module %d's ROW_VCC comes from %s" % (k, sw))
        want(len(nets["M%d_FAULT" % k]) == 3,
             "module %d's fault line is not switch + pullup + MCU" % k)
    want(not any(r in SWITCH_REFS for r, _ in nets["AVCC"]),
         "AVCC passes through a switch; it is meant to be unswitched")

    # 8. Every MCU supply pin is decoupled, and the BOM is the netlist's.
    for name in ("VDD_D", "VDDA_MCU", "VCAP1", "VCAP2"):
        want(any(r.startswith("C") for r, _ in nets[name]),
             "%s has no capacitor on it" % name)
    for pin_name, rail in (("VDD", "VDD_D"), ("VSS", "PWR_GND")):
        nums = sym(*SYMOF["U1"])[pin_name]
        missing = [x for x in nums if ("U1", x) not in pins_on(rail)]
        want(not missing, "MCU %s pin(s) %s are not on %s"
             % (pin_name, missing, rail))
    want(len([1 for r, _ in nets["VDD_D"] if r.startswith("C")])
         >= len(sym(*SYMOF["U1"])["VDD"]),
         "fewer decoupling caps than VDD pins")
    for row in bom_rows:
        span = row[0]
        listed = set()
        for tok in span.split(","):
            m = re.fullmatch(r"([A-Z]+)(\d+)-[A-Z]*(\d+)", tok)
            listed |= ({m.group(1) + str(i) for i in
                        range(int(m.group(2)), int(m.group(3)) + 1)}
                       if m else {tok})
        want(all(r in SYMOF or r in pas.spec for r in listed),
             "BOM line %r names refs that do not exist: %s"
             % (span, sorted(r for r in listed
                             if r not in SYMOF and r not in pas.spec)))
    # 9. Every regulator carries its rail with margin, and the 5 V input is
    #    beyond what a USB port offers - which is why J10 exists.
    per, total, from_5v = budget()
    for ref, (rating, _) in LOADS.items():
        want(per[ref] <= rating * 0.8,
             "%s carries %d mA of its %d mA rating - under 20%% headroom"
             % (ref, per[ref], rating))
    want(total <= BUCK_RATING * 0.8,
         "the buck carries %d mA of %d mA" % (total, BUCK_RATING))
    # The plan justified the separate 5 V input as "572 mA, more than USB's
    # 500 mA". That compares a 3.3 V total against a 5 V limit, and the buck
    # steps DOWN, so the input current is the smaller number - 614 mA of 3.3 V
    # load is about 496 mA at 5 V, marginally UNDER the bus allowance.
    #
    # The input stays, for the reason that actually holds: 500 mA is what a
    # device may draw AFTER enumerating, and 100 mA before. This board has to
    # bring up an MCU and a high-speed PHY to enumerate at all, and the modules
    # pull row current the moment they are enabled. Sitting a percent under the
    # post-enumeration limit on estimates this rough is not a power supply.
    want(from_5v > 0.8 * 500,
         "at %d mA from 5 V the board is now comfortably inside USB's "
         "allowance; bus power is worth reconsidering" % from_5v)

    refs_in_net = {r for r, _ in net_of}
    known = set(SYMOF) | set(pas.spec)
    want(refs_in_net <= known, "in the netlist but not the BOM: %s"
         % sorted(refs_in_net - known))
    want(known <= refs_in_net, "in the BOM but not the netlist: %s"
         % sorted(known - refs_in_net))

    return fails


# ----------------------------------------------------------------- main
def main():
    nets, pas = build()
    emit_net(nets, OUT_NET)
    rows = emit_bom(pas, OUT_BOM)
    fails = check(nets, pas, rows)

    parts = len(SYMOF) + len(pas.spec)
    nodes = sum(len(v) for v in nets.values())
    print("wrote hub.net and BOM.csv")
    print("  %d nets, %d parts, %d connections" % (len(nets), parts, nodes))
    print("  %d MCU pins used of 144" % len([1 for v in nets.values()
                                             for r, _ in v if r == "U1"]))
    print("  %d BOM lines" % len(rows))
    per, total, from_5v = budget()
    print("  %s = %d mA on 3.3 V, %d mA from the 5 V input"
          % (" + ".join("%d" % per[r] for r in sorted(per)), total, from_5v))
    if fails:
        print("\n%d CHECK(S) FAILED" % len(fails))
        for f in sorted(set(fails)):
            print("  " + f)
        return 1
    print("  all checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
