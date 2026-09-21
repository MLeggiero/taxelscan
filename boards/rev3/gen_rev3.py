#!/usr/bin/env python3
"""Derive rev-3's netlist from the rev-1 board's.

rev-3 is rev-1's analog chain, unchanged in topology, with three things done to
it: the sense pulldowns rescaled for a 1 MOhm carbon-nanotube sensor, the
TLV9062 turned from a unity-gain follower into a gain stage, and the XIAO
replaced by a bare RP2354A, a 16-bit converter and an RS-485 transceiver so
eight boards can run as one instrument on one harness.

So it is built by transforming rev-1's exported netlist rather than by
redrawing it. Every row driver, mux and column connection here is the one that
was measured on hardware; only the deliberate changes are written out longhand
below, which also makes them the diff a reviewer reads.

    ./gen_rev3.py            writes rev3.net and BOM.csv, and checks both
    ./check_faults.py        breaks this file on purpose to prove the checks bite

Standard library only. It reads KiCad's own symbol libraries, so it needs KiCad
installed but no Python packages.

TWO RULES, both inherited from the generators before this one:

  1. Pin numbers are LOOKED UP BY NAME, never typed. p("U8", "VREF") returns
     the real pin 10 of the MSOP-10 and p("U8", "VREFF") stops the generator.
     Stock symbols are used where possible; project power symbols include
     datasheet links and an independently checked package pin map.

  2. Every claim the README makes about this circuit is asserted in check()
     below, so a mistake fails here rather than at bring-up.
"""
import csv
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REV1 = os.path.join(HERE, "..", "rev1", "outputs", "sch_final.net")
OUT_NET = os.path.join(HERE, "rev3.net")
OUT_BOM = os.path.join(HERE, "BOM.csv")

SYMBOL_DIRS = [
    r"C:/Program Files/KiCad/*/share/kicad/symbols",
    "/usr/share/kicad/symbols",
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    os.environ.get("KICAD_SYMBOL_DIR", ""),
]


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
FPDIRS = [d.replace("symbols", "footprints") for d in [SYMDIR]] + [
    os.path.join(HERE, "..", "..", "libraries")]
_symcache = {}


def symbol_path(lib):
    local = os.path.join(HERE, lib + ".kicad_sym")
    return local if os.path.isfile(local) else os.path.join(SYMDIR, lib + ".kicad_sym")


def footprint_exists(fp):
    """Is this footprint actually in a library on this machine?

    Two of the footprint names in the first version of PARTS were invented -
    the QFN-60's exposed pad is 3.4 mm, not 3.2, and the DIP switch name was
    guessed outright. Both looked entirely plausible in a BOM and neither
    exists. KiCad reports that as a footprint_link_issue WARNING, which is
    easy to scroll past; a board cannot be built from either.
    """
    if ":" not in fp:
        return False
    lib, name = fp.split(":", 1)
    return any(os.path.exists(os.path.join(d, lib + ".pretty", name + ".kicad_mod"))
               for d in FPDIRS)


def sym(lib, name):
    """{pin name: [pin numbers]} for one library symbol, following (extends)."""
    key = (lib, name)
    if key in _symcache:
        return _symcache[key]
    path = symbol_path(lib)
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
    _symcache[key] = out
    return out


# --------------------------------------------------------------------- parts
#
# ref, library, symbol, ordered part, footprint, LCSC, note
#
# One substitution, and it is the one this repo already makes: rev-1 orders an
# SN74LVC595A against a 74HC595D symbol, so ordering an SN65HVD75DR against a
# MAX3485 symbol is the same move. Both are the industry-standard 8-pin
# half-duplex RS-485 pinout (RO / ~RE / DE / DI / GND / A / B / VCC), which is
# why the substitution is safe: the symbol and the part cannot disagree about a
# pin without disagreeing about the whole family.
PARTS = [
    # --- inherited from rev-1, unchanged in function -----------------------
    ("U1-U4", "74xx", "74HC595", "SN74LVC595APW",
     "Package_SO:TSSOP-16_4.4x5mm_P0.65mm", "C52287685",
     "32 row drivers; unselected rows held LOW. TI SN74LVC595APWR. (Was "
     "C6062, which is a 74LVC138 decoder.)"),
    ("U5,U6", "74xx", "CD74HC4067SM", "CD74HC4067SM96",
     "Package_SO:SSOP-24_5.3x8.2mm_P0.65mm", "C98457",
     "32 columns, 2 banks of 16"),
    ("U7", "Amplifier_Operational", "TLV9062", "TLV9062IDGKR",
     "Package_SO:MSOP-8_3x3mm_P0.65mm", "C398356",
     "Gain stage, was a unity-gain follower. TI TLV9062IDGKR: DGK = "
     "VSSOP-8 on 0.65 mm, which is what this footprint is (the D suffix is "
     "SOIC-8 and does NOT fit)."),

    # --- new: the converter ------------------------------------------------
    ("U8", "Analog_ADC", "LTC1865L-MS", "LTC1865LIMS",
     "Package_SO:MSOP-10_3x3mm_P0.5mm", "C580457",
     "Dual 16-bit SAR, 150 ksps, VREF tapped off ROW_VCC. ADI "
     "LTC1865LIMS#PBF, the -40 to 85 C grade (C678973 is the 0 to 70 C "
     "CMS). LOW STOCK: confirm the quantity on the order day"),

    # --- new: the MCU ------------------------------------------------------
    ("U9", "MCU_RaspberryPi", "RP2354A", "RP2354A",
     "Package_DFN_QFN:QFN-60-1EP_7x7mm_P0.4mm_EP3.4x3.4mm", "C41378174",
     "NEW: replaces the XIAO module; 2 MB flash stacked in package. Thermal vias are added at layout at 0.3 mm - the library's _ThermalVias variant uses 0.2 mm, below JLCPCB's standard tier"),

    # --- new: the bus ------------------------------------------------------
    ("U10", "Interface_UART", "MAX3485", "SN65HVD75DR",
     "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "C57928",
     "NEW: RS-485 data, half duplex"),
    ("U11", "Interface_UART", "MAX3485", "SN65HVD75DR",
     "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "C57928",
     "NEW: RS-485 sync, receive only (DE and DI strapped low)"),

    # --- new: power --------------------------------------------------------
    ("U12", "Regulator_Switching", "TLV62569DBV", "TLV62569DBVR",
     "Package_TO_SOT_SMD:SOT-23-5", "C141836",
     "5 V -> 3.3 V buck. TI TLV62569DBVR. (Was C144206, a MIC2954 LDO in "
     "SOT-223.)"),
    ("U13", "Interface_USB", "TUSB320", "TUSB320LAIRWBR",
     "Package_DFN_QFN:Texas_X2QFN-12_1.6x1.6mm_P0.4mm", "C132554",
     "USB-C sink/current detection, GPIO mode; LAI required for dynamic current updates. Same 12-pin map as stock TUSB320 symbol; internal Rd replaces R13/R14"),
    ("U14", "TaxelScanPower", "TPS2553DBV", "TPS2553DBVR",
     "Package_TO_SOT_SMD:SOT-23-6", "C55266",
     "USB-to-harness current-limited switch; default OFF. TI TPS2553DBVR - "
     "not the -1 variant. R27=82.5k; Q1 switches R28 in parallel for "
     "higher CC budget"),
    ("Q1", "Transistor_FET", "2N7002", "2N7002PW",
     "Package_TO_SOT_SMD:SOT-323_SC-70", "C255579",
     "Select high harness current limit only after CC advertises >=1.5A"),
    ("D4", "Device", "D_Schottky", "PMEG2010ER",
     "Diode_SMD:Nexperia_CFP3_SOD-123W", "C82288",
     "Nexperia PMEG2010ER. 1A series reverse barrier: U14 OUT anode -> "
     "harness cathode. Prevents powered harness backfeeding USB even "
     "during U14 reverse-comparator delay"),
    ("R27,R28", "Device", "R", "82.5k",
     "Resistor_SMD:R_0402_1005Metric", "C852928",
     "Current-limit resistors; nominal 321 mA / 632 mA, tolerance bounds "
     "in USB_POWER.md. THIN FILM 0.1% 25ppm/C (Yageo RT0402BRD0782K5L)"),
    ("R29,R30", "Device", "R", "4.7k",
     "Resistor_SMD:R_0402_1005Metric", "C281778",
     "Pulldowns: current-select gate / harness enable; reset and unpowered "
     "MCU leave harness feed OFF. THIN FILM 0.1% 25ppm/C (Yageo "
     "RT0402BRD074K7L)"),
    ("R31-R33", "Device", "R", "10k",
     "Resistor_SMD:R_0402_1005Metric", "C844452",
     "3.3V pullups: power fault and CC OUT1/OUT2; same supply as U13 "
     "prevents backpower. THIN FILM 0.1% 25ppm/C (Vishay TNPW040210K0BEED, "
     "the same reel as every 10k)"),
    ("R34", "Device", "R", "866k",
     "Resistor_SMD:R_0402_1005Metric", "C149916",
     "VBUS_DET series resistor. THIN FILM 0.5% 50ppm/C (Ever Ohms "
     "TR0402D866KQ1050): 861.7-870.3k, inside the TUSB320's 855-920k "
     "window. No 0402 thin-film 887k is stocked; 887k 1% thick film "
     "(Panasonic ERJ-2RKF8873X, C409935) is the fallback"),
    ("C40,C42", "Device", "C", "100nF",
     "Capacitor_SMD:C_0402_1005Metric", "C85858",
     "Local bypass at U13 VDD / U14 IN. Murata GCM155R71H104KE02D, X7R 50 "
     "V, AEC-Q200"),
    ("C41", "Device", "C", "1uF",
     "Capacitor_SMD:C_0402_1005Metric", "C52923",
     "U13 local supply reservoir. Samsung CL05A105KA5NQNC, X5R 25 V"),
    ("D5", "Power_Protection", "USBLC6-2P6", "USBLC6-2P6",
     "Package_TO_SOT_SMD:SOT-666", "C15999",
     "USB ESD: D+, D- and VBUS clamped at the connector, on the connector "
     "side of R23/R24. ST USBLC6-2P6 (SOT-666, 3.5 pF per line, 17 V at 5 "
     "A). The RP2354A's USB pins carry HBM protection only. The RS-485 bus "
     "pins need no external part: the SN65HVD75 has +-12 kV IEC 61000-4-2 "
     "contact protection on chip. CC1/CC2 rely on the TUSB320's +-7 kV HBM"),
    ("R38", "Device", "R", "1R",
     "Resistor_SMD:R_0603_1608Metric", "C861215",
     "Hot-plug damper, first element in series with C46 across +5V_USB. A live USB cable "
     "into ceramic-only input capacitance rings: the LC estimate peaked at "
     "6.2-7.3 V against U12's 6 V absolute maximum. 1R + 10uF puts the "
     "damping resistance near sqrt(L/C) of a 1 m cable into ~6 uF. THIN "
     "FILM 0.1% 25ppm/C (Yageo RT0603BRD071RL) - no 0402 1R thin film is "
     "stocked, so 0603"),
    ("C46", "Device", "C", "10uF",
     "Capacitor_SMD:C_0805_2012Metric", "C3039694",
     "Hot-plug damper reservoir, behind R38 so it damps the ring instead "
     "of joining it. Samsung CL21B106KAYQNNE, X7R 25 V, the same "
     "reel as C9/C10/C39"),

    # --- passives ----------------------------------------------------------
    ("R1,R2", "Device", "R", "10k",
     "Resistor_SMD:R_0402_1005Metric", "C844452",
     "Sense pulldowns, changed from 3.3k. FIT ON TEST. THIN FILM 0.1% "
     "25ppm/C (Vishay TNPW040210K0BEED) - these set the transfer function, "
     "and an R1/R2 tempco mismatch is a bank-to-bank GAIN drift, which the "
     "dark reference cannot remove"),
    ("R3,R4", "Device", "R", "33R",
     "Resistor_SMD:R_0402_1005Metric", "C852729", "SRCLK/RCLK damping. THIN FILM 0.1% 25ppm/C (Yageo RT0402BRD0733RL)"),
    ("R5", "Device", "R", "0R",
     "Resistor_SMD:R_0402_1005Metric", "C2654001",
     "+3.3V -> ROW_VCC link; VREF is tapped on the ROW_VCC side. Vishay "
     "MCS04020Z0000ZE000, a thin-film 0R jumper"),
    ("R6,R8", "Device", "R", "10k",
     "Resistor_SMD:R_0402_1005Metric", "C844452",
     "Gain feedback. THIN FILM 0.1% 25ppm/C (Vishay TNPW040210K0BEED) - "
     "with R7/R9 these set G per bank, so a mismatch is a bank-to-bank "
     "gain error. Same reel as R1/R2"),
    ("R7,R9", "Device", "R", "2k",
     "Resistor_SMD:R_0402_1005Metric", "C844262",
     "Gain set to GND. G = 1 + R6/R7 = 6. FIT ON TEST. THIN FILM 0.1% "
     "25ppm/C (Vishay TNPW04022K00BEED), and ideally the same lot as R6/R8 "
     "so the two banks track"),
    ("R10", "Device", "R", "10R",
     "Resistor_SMD:R_0402_1005Metric", "C705629",
     "VREF filter, with C10. THIN FILM 0.1% 25ppm/C (Yageo "
     "RT0402BRD0710RL). (Was C25104, which is 330R.)"),
    ("R11,R12", "Device", "R", "120R",
     "Resistor_SMD:R_0603_1608Metric", "C861094",
     "Bus termination. DNF except on the two end boards: make_fab.py "
     "leaves them out of the default assembly files and writes an "
     "end-board set. THIN FILM 0.1% 25ppm/C, 0.1 W (Yageo "
     "RT0603BRD07120RL); ~35 mW each at a 2 V differential"),
    ("R15", "Device", "R", "100k",
     "Resistor_SMD:R_0402_1005Metric", "C852472", "Rail monitor, top. THIN FILM 0.1% 25ppm/C (Yageo RT0402BRD07100KL)"),
    ("R16", "Device", "R", "47k",
     "Resistor_SMD:R_0402_1005Metric", "C728561", "Rail monitor, bottom. THIN FILM 0.1% 25ppm/C (Yageo "
     "RT0402BRD0747KL). (Was C25819, an 0603.)"),
    ("R17", "Device", "R", "1k",
     "Resistor_SMD:R_0402_1005Metric", "C852624", "Status LED. THIN FILM 0.1% 25ppm/C (Yageo RT0402BRD071KL)"),
    ("R21,R22", "Device", "R", "51R",
     "Resistor_SMD:R_0402_1005Metric", "C852830",
     "Series into the ADC, outside the feedback loop. With C31/C32 this is "
     "the charge reservoir the SAR sample-and-hold needs. THIN FILM 0.1% "
     "25ppm/C (Yageo RT0402BRD0751RL). Never 5.1k: 5.1k x 1nF = 5.1 us "
     "against a ~1 us acquisition window"),
    ("R20", "Device", "R", "10k",
     "Resistor_SMD:R_0402_1005Metric", "C844452",
     "RUN pull-up. The pin has a weak internal one; this makes it a net. "
     "THIN FILM 0.1% 25ppm/C (Vishay TNPW040210K0BEED)"),
    ("R23,R24", "Device", "R", "27R",
     "Resistor_SMD:R_0402_1005Metric", "C852682",
     "USB series termination, one per line, at the MCU end (the RP2350 "
     "design guide value). THIN FILM 0.1% 25ppm/C (Yageo RT0402BRD0727RL). "
     "LOW STOCK"),
    ("R25", "Device", "R", "1k",
     "Resistor_SMD:R_0402_1005Metric", "C852624",
     "BOOTSEL series. ~QSPI_SS is bonded to the stacked flash die, so the "
     "button is a stub on a live chip-select at up to 133 MHz; this keeps "
     "it behind a resistor. THIN FILM 0.1% 25ppm/C (Yageo RT0402BRD071KL)"),
    ("R26", "Device", "R", "10R",
     "Resistor_SMD:R_0402_1005Metric", "C705629",
     "ADC_AVDD filter, with C36. The internal ADC reads RAIL_MON; this "
     "keeps IOVDD switching noise off its supply. THIN FILM 0.1% 25ppm/C "
     "(Yageo RT0402BRD0710RL)"),
    ("R18", "Device", "R", "180k",
     "Resistor_SMD:R_0402_1005Metric", "C852573",
     "Buck feedback, top. THIN FILM 0.1% 25ppm/C (Yageo RT0402BRD07180KL). "
     "With 0.1% parts and the 0.6 V +-1.5% reference the rail spans "
     "3.24-3.34 V. (Was C25811, a 200k 0603.)"),
    ("R19", "Device", "R", "40.2k",
     "Resistor_SMD:R_0402_1005Metric", "C852775",
     "Buck feedback, bottom. 0.6 x (1 + 180/40.2) = 3.29 V. THIN FILM 0.1% "
     "25ppm/C (Yageo RT0402BRD0740K2L). (Was C25752, which is 12k.)"),
    ("C1-C7", "Device", "C", "100nF",
     "Capacitor_SMD:C_0402_1005Metric", "C85858",
     "Per-IC decoupling. Murata GCM155R71H104KE02D, X7R 50 V, AEC-Q200"),
    ("C8", "Device", "C", "1uF",
     "Capacitor_SMD:C_0402_1005Metric", "C52923",
     "U8 (LTC1865L) VCC bypass, 1 uF: the datasheet's Bypassing section "
     "asks for at least 1 uF at VCC, and the nearest bulk (C9) is 33 mm "
     "away. Samsung CL05A105KA5NQNC, X5R 25 V, keeps ~80% at 3.3 V"),
    ("C9", "Device", "C", "10uF",
     "Capacitor_SMD:C_0805_2012Metric", "C3039694",
     "Bulk on +3.3V, as rev-1. Samsung CL21B106KAYQNNE, X7R 25 V. At 1 "
     "MOhm a row draws 106 uA, so there is no row-change transient to "
     "absorb on ROW_VCC"),
    ("C10", "Device", "C", "10uF",
     "Capacitor_SMD:C_0805_2012Metric", "C3039694",
     "VREF reservoir, with R10. Samsung CL21B106KAYQNNE, X7R 25 V - a 6.3 "
     "V 10uF 0805 loses most of its capacitance to DC bias on a 3.3V rail"),
    ("C11", "Device", "C", "100nF",
     "Capacitor_SMD:C_0201_0603Metric", "C76939",
     "RP2354A IOVDD bypass, 0201 so it sits between two used 0.4 mm-pitch "
     "pins. Murata GRM033R61E104KE14D, X5R 25 V"),
    ("C12", "Device", "C", "100nF",
     "Capacitor_SMD:C_0402_1005Metric", "C85858",
     "Transceiver decoupling. Murata GCM155R71H104KE02D, X7R 50 V, "
     "AEC-Q200"),
    ("C13-C18", "Device", "C", "100nF",
     "Capacitor_SMD:C_0201_0603Metric", "C76939",
     "RP2354A bypass, 0201 (see C11). Murata GRM033R61E104KE14D, X5R 25 V"),
    ("C24,C25", "Device", "C", "100nF",
     "Capacitor_SMD:C_0201_0603Metric", "C76939",
     "VCORE decoupling. Murata GRM033R61E104KE14D, X5R 25 V"),
    ("C26", "Device", "C", "10nF",
     "Capacitor_SMD:C_0402_1005Metric", "C22400107",
     "VREF HF. Murata GRM1555C1H103JE01D, C0G 50 V (a 100 nF C0G 0402 does "
     "not exist; C10 carries the bulk)"),
    ("C28,C29", "Device", "C", "100nF",
     "Capacitor_SMD:C_0402_1005Metric", "C85858",
     "MCU decoupling. Murata GCM155R71H104KE02D, X7R 50 V, AEC-Q200"),
    ("C27", "Device", "C", "100nF",
     "Capacitor_SMD:C_0402_1005Metric", "C85858",
     "Buck input, HF; C22 alone is bulk. Murata GCM155R71H104KE02D, X7R 50 "
     "V, AEC-Q200"),
    ("C31,C32", "Device", "C", "1nF",
     "Capacitor_SMD:C_0402_1005Metric", "C437434",
     "ADC charge reservoir, C0G MANDATORY. Murata GCM1555C1H102JA16D, C0G "
     "50 V, AEC-Q200. 51R x 1nF = 51 ns against a ~1 us acquisition. (Was "
     "C1523, which is X7R.)"),
    ("C33,C34", "Device", "C", "100nF",
     "Capacitor_SMD:C_0201_0603Metric", "C76939",
     "MCU decoupling, one cap per supply pin with C35/C36. Murata "
     "GRM033R61E104KE14D, X5R 25 V"),
    ("C35", "Device", "C", "100nF",
     "Capacitor_SMD:C_0201_0603Metric", "C76939",
     "Third VCORE cap, so each of the three DVDD pins has its own. Murata "
     "GRM033R61E104KE14D, X5R 25 V"),
    ("C36", "Device", "C", "100nF",
     "Capacitor_SMD:C_0201_0603Metric", "C76939",
     "ADC_AVDD, on the quiet side of R26. Murata GRM033R61E104KE14D, X5R "
     "25 V"),
    ("R35", "Device", "R", "33R",
     "Resistor_SMD:R_0402_1005Metric", "C852729",
     "VREG_AVDD filter, with C43 - the RP2350 design guide's 33R + 4.7uF. "
     "THIN FILM 0.1% 25ppm/C (Yageo RT0402BRD0733RL)"),
    ("C43,C44", "Device", "C", "4.7uF",
     "Capacitor_SMD:C_0603_1608Metric", "C69335",
     "C43 is the VREG_AVDD filter capacitor (with R35); C44 is the "
     "VREG_VIN input reservoir at U9.49. Samsung CL10A475KA8NQNC, X5R 25 V"),
    ("R37", "Device", "R", "10k",
     "Resistor_SMD:R_0402_1005Metric", "C844452",
     "SYNC_DE pull-down: every board is receive-only on the sync pair until "
     "its firmware drives GPIO13, and stays so through reset. 10k is within "
     "TI's 1-10k advice for enable lines. THIN FILM 0.1% 25ppm/C (Vishay "
     "TNPW040210K0BEED, the same reel as every 10k)"),
    ("R36", "Device", "R", "1k",
     "Resistor_SMD:R_0402_1005Metric", "C852624",
     "XOUT series resistor, the design guide's R2 - keeps the crystal from "
     "being over-driven at 3.3 V IOVDD. THIN FILM 0.1% 25ppm/C (Yageo "
     "RT0402BRD071KL)"),
    ("C45", "Device", "C", "100nF",
     "Capacitor_SMD:C_0402_1005Metric", "C85858",
     "RAIL_MON reservoir at U9.42 (GPIO28/ADC2): R15/R16 present 32k to "
     "the internal SAR. Murata GCM155R71H104KE02D, X7R 50 V, AEC-Q200"),
    ("C39", "Device", "C", "10uF",
     "Capacitor_SMD:C_0805_2012Metric", "C3039694",
     "Harness entry reservoir, behind the controlled USB feed. Samsung "
     "CL21B106KAYQNNE, X7R 25 V"),
    ("C37", "Device", "C", "1uF",
     "Capacitor_SMD:C_0805_2012Metric", "C28323",
     "Reduced VBUS capacitance; attach inrush still needs measuring. >=16 "
     "V because hot-plugged VBUS rings above 5 V: Samsung CL21B105KBFNNNE, "
     "X7R 50 V"),
    ("C38", "Device", "C", "100nF",
     "Capacitor_SMD:C_0402_1005Metric", "C85858",
     "VBUS high frequency, at J5; C37 alone is bulk. Murata "
     "GCM155R71H104KE02D, X7R 50 V, AEC-Q200"),
    ("C19", "Device", "C", "4.7uF",
     "Capacitor_SMD:C_0603_1608Metric", "C69335", "VCORE bulk. Samsung CL10A475KA8NQNC, X5R 25 V"),
    ("C20,C21", "Device", "C", "15pF",
     "Capacitor_SMD:C_0402_1005Metric", "C338103",
     "Crystal load. C0G MANDATORY - an X7R here moves the USB clock with "
     "temperature. 15 pF each is the design-guide value for the "
     "ABM8-272-T3 (CL 10 pF with ~3 pF strays). TDK CGA2B2C0G1H150JT0Y0F, "
     "C0G 50 V, AEC-Q200"),
    ("C22", "Device", "C", "4.7uF",
     "Capacitor_SMD:C_0603_1608Metric", "C69335",
     "Buck input, reduced from 22uF for USB attach inrush. Samsung "
     "CL10A475KA8NQNC, X5R 25 V (the 25 V rating keeps more capacitance at "
     "5 V bias than a 16 V part); validate at bias"),
    ("C23", "Device", "C", "22uF",
     "Capacitor_SMD:C_0805_2012Metric", "C45783",
     "Buck OUTPUT (+3.3V). Samsung CL21A226MAQNNNE, X5R 25 V - no "
     "name-brand X7R >=25 V 22uF 0805 is stocked; this capacitor sets loop "
     "stability and output ripple"),
    ("L1", "Device", "L", "AOTA-B201610S3R3-101-T",
     "FlexiTac:L_0806_2016Metric", "C42411119",
     "3.3uH, the RP2350 design guide's ONLY recommended part - Abracon "
     "AOTA-B201610S3R3-101-T, polarity-marked, on a project 0806 footprint "
     "whose pad 1 is the dot. The dot goes on the OUTPUT (VCORE) end: "
     "RP2350 datasheet section 6.3.8, Figures 23 and 25"),
    ("L2", "Device", "L", "2.2uH",
     "Inductor_SMD:L_1210_3225Metric", "C2045365",
     "Buck output inductor. Murata DFE322520FD-2R2M=P2 (AEC-Q200): Isat 5 "
     "A, 46 mOhm. The TLV62569 high-side current limit is 3 A MINIMUM, so "
     "Isat must be >= 3 A - a generic 2.2uH 1210 rated 1 A saturates at "
     "startup or into a short"),
    ("D1,D2", "Device", "D_Schottky", "PMEG2005AEA",
     "Diode_SMD:D_SOD-323", "C179428",
     "OR the bus 5 V against USB VBUS. Nexperia PMEG2005AEA, 20 V 0.5 A "
     "Schottky in SOD-323 (SS0520 is not stocked in SOD-323; C261671 does "
     "not exist)"),
    ("D3", "Device", "LED", "LED_Green",
     "LED_SMD:LED_0603_1608Metric", "C125098", "Status. Lite-On LTST-C191KGKT, 574 nm, Vf ~2.0 V: ~1.3 mA through "
     "R17 (a 3 V true-green LED would get ~0.3 mA). C72043 is discontinued"),
    ("Y1", "Device", "Crystal_GND24", "ABM8-272-T3",
     "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "C20625731",
     "12 MHz, CL 10 pF, ESR <= 50R - Abracon ABM8-272-T3, the design "
     "guide's recommended crystal, with 15 pF loads and R36"),

    # --- connectors and switches -------------------------------------------
    ("J1,J2", "Connector_Generic_MountingPin", "Conn_01x32_MountingPin",
     "FFC_32P_0.5mm", "FlexiTac:FFC_0.5mm_FH12_32_16_12way_Composite", "C597985",
     "Rows, columns - unchanged from rev-1 except that the shell is now a "
     "pin. Hirose FH12-32S-0.5SH(55), bottom contact, as the footprint "
     "assumes. LOW STOCK (44)"),
    ("J3,J4", "Connector_Generic_MountingPin", "Conn_01x06_MountingPin",
     "Bus_6P",
     "Connector_JST:JST_GH_SM06B-GHS-TB_1x06-1MP_P1.25mm_Horizontal", "C133065",
     "Harness in / out, wired identically so the bus passes through. JST "
     "SM06B-GHS-TB. (Was C160404, a 4-pin SH connector.)"),
    ("J5", "Connector", "USB_C_Receptacle_USB2.0_16P", "USB-C",
     "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", "C165948",
     "NEW: bring-up, BOOTSEL and standalone use"),
    ("J6", "Connector_Generic", "Conn_01x04", "SWD",
     "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", "C5184785",
     "SWDIO, SWCLK, GND, RUN - debug and reset. Wurth 61300411121, gold, "
     "through-hole (hand or wave soldered). 2.54 mm on purpose: it is what "
     "a debug probe cable and jumper wires actually fit"),
    ("SW1", "Switch", "SW_Push", "BOOTSEL",
     "Button_Switch_SMD:SW_SPST_B3U-1000P", "C231329",
     "Pulls QSPI_SS low. Omron B3U-1000P. (Was C139797, an ALPS 4.2 x 3.2 "
     "mm switch that does not fit.)"),
    ("JP1-JP3", "Jumper", "SolderJumper_2_Open", "ADDR",
     "Jumper:SolderJumper-2_P1.3mm_Bridged_Pad1.0x1.5mm", "",
     "NEW: 3-bit board address, 8 boards per harness. Was a DIP switch at "
     "56.8 mm2; three jumpers are 24.8 and the address stays readable by eye"),
]

# ref -> (lib, symbol) for every reference the generator can name a pin on.
SYMOF = {}
for refs, lib, symbol, _v, _fp, _l, _n in PARTS:
    for ref in refs.replace(" ", "").split(","):
        m = re.fullmatch(r"([A-Z]+)(\d+)-[A-Z]*(\d+)", ref)
        if m:
            for i in range(int(m.group(2)), int(m.group(3)) + 1):
                SYMOF["%s%d" % (m.group(1), i)] = (lib, symbol)
        else:
            SYMOF[ref] = (lib, symbol)


def p(ref, pin_name, index=0):
    """The real pin number of `pin_name` on `ref`, from KiCad's own library."""
    if ref not in SYMOF:
        sys.exit("no symbol declared for %s" % ref)
    lib, symbol = SYMOF[ref]
    pins = sym(lib, symbol)
    if pin_name not in pins:
        sys.exit("%s (%s:%s) has no pin named %r" % (ref, lib, symbol, pin_name))
    nums = pins[pin_name]
    if index >= len(nums):
        sys.exit("%s pin %r has %d number(s), asked for #%d"
                 % (ref, pin_name, len(nums), index))
    return nums[index]


# ---------------------------------------------------------------- rev-1 input
def load_rev1():
    """{net name: [(ref, pin)]} from rev-1's exported netlist."""
    text = open(REV1, encoding="utf-8").read()
    nets = {}
    for name, body in re.findall(
            r'\(net\s*\(code "?\d+"?\)\s*\(name "([^"]+)"\)(.*?)(?=\n\s*\(net\s|\Z)',
            text, re.S):
        nets[name] = [(r, pin) for r, pin in
                      re.findall(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', body)]
    if not nets:
        sys.exit("could not parse %s" % REV1)
    return nets


# ---------------------------------------------------------------- the changes
#
# Everything not named here is inherited from rev-1 unchanged.
#
# The GPIO map reproduces rev-1's on purpose. scan.h sets MUX_S0..S3 with a
# single store, which only works while they stay contiguous and in order, and
# the row control lines keep their rev-1 bit positions so that line ports
# across unchanged rather than being rewritten and re-reasoned.
GPIO = {
    "ROW_LATCH_MCU": 1,     # rev-1 GPIO1
    "ROW_CLK_MCU":   2,     # rev-1 GPIO2
    "ROW_DATA":      3,     # rev-1 GPIO3
    "MUX_S0":        4,     # rev-1 GPIO4..7, contiguous: one store
    "MUX_S1":        5,
    "MUX_S2":        6,
    "MUX_S3":        7,
    "BUS_DI":        8,     # UART1 TX
    "BUS_RO":        9,     # UART1 RX
    "BUS_DE":       10,     # driver enable, tied to ~RE
    "SYNC_OUT":     11,     # broadcast frame-start, differential
    "SYNC_DE":      13,     # master only: pulse high to drive the frame-start; R37 holds it low
    "USB_BUS_EN":   24,     # active high, external pulldown: default OFF (was 12; moved off the crowded bottom row, audit 2026-09-10)
    "USB_ILIM_HI":   0,     # Q1; LOW selects the USB 2.0 budget (was 13)
    "USB_CC_OUT1":  27,     # TUSB320LAI open-drain status (was 14)
    "USB_CC_OUT2":  29,     # (was 15)
    "ADC_SDO":      16,     # SPI0 RX
    "ADC_CONV":     17,     # SPI0 CSn
    "ADC_SCK":      18,     # SPI0 SCK
    "ADC_SDI":      19,     # SPI0 TX
    "ADDR0":        20,     # 3-bit board address
    "ADDR1":        21,
    "ADDR2":        22,
    "USB_PWR_FAULT":23,     # active low, latch off in power policy
    "STATUS":       25,
    "RAIL_MON":     28,     # ADC2 (GPIO28): 5 V rail, through R15/R16. Was ADC0/GPIO26, whose pin sits behind C35 on the right edge
}


def build(rev1):
    nets = {k: list(v) for k, v in rev1.items()
            if not k.startswith("unconnected-")}

    def n(net, ref, pin):
        nets.setdefault(net, []).append((ref, str(pin)))

    # 1. The MCU module is gone. Its footprint and every A1 node go with it.
    for name in list(nets):
        nets[name] = [(r, pin) for r, pin in nets[name] if r != "A1"]
    del nets["+5V"]                      # the XIAO's 5V pin was the only node

    # 2. ONE ground. rev-1 had one; the v2 module split it because up to 30 mA
    #    of press-correlated row current returned through the analog reference.
    #    At 1 MOhm a driven row sources 32 x 3.3 uA ~= 106 uA, so that argument
    #    is gone and the split with it. An unbroken plane is better for a
    #    high-impedance front end anyway.

    # 3. The TLV9062 stops being a follower and becomes a non-inverting gain
    #    stage. rev-1 tied each output to its own inverting input; here the
    #    inverting input goes to a divider instead, so G = 1 + Rf/Rg.
    #
    #    The gain sits AFTER the high-impedance node, so it costs nothing in
    #    settling time - which is the whole reason the pulldown can stay small
    #    and the sneak-path share stay low. Amplifier offset and mux leakage
    #    are amplified with the signal, but both are static and the dark
    #    reference subtracts them every frame.
    for unit, (bank, rf, rg) in enumerate((("A", "R6", "R7"), ("B", "R8", "R9"))):
        del nets["ADC_%s" % bank]        # was op-amp out + in-, tied together
        n("GAIN_%s" % bank, "U7", p("U7", "-", unit))
        n("GAIN_%s" % bank, rf, "1")
        n("GAIN_%s" % bank, rg, "1")
        n("AMP_%s" % bank, "U7", p("U7", "", unit))
        n("AMP_%s" % bank, rf, "2")
        n("GND", rg, "2")

    # 4. The converter. VREF is tapped off ROW_VCC through an RC rather than
    #    driven from a precision reference, and that is a deliberate choice:
    #    the row drive and the reference then move together, so the code is
    #    (Rpd_eff / (Rs + Rpd_eff)) * G * 65536 with the rail cancelled out of
    #    it entirely. It deletes a part AND deletes rev-1's rail-sag
    #    correction. R10/C10 keep the reference quiet at high frequency while
    #    letting it track the rail at DC.
    # The op-amp does NOT drive the converter directly. An SAR samples onto a
    # switched capacitor, and closing that switch throws charge back at
    # whatever is driving it; at 16 bits the disturbance has to settle before
    # the conversion starts. So each bank goes through 51R into a 1nF C0G
    # sitting at the ADC pin, which supplies the sampling charge locally.
    # 51R x 1nF = 51 ns against a ~1 us acquisition window.
    #
    # The resistor is OUTSIDE the feedback loop - the loop closes at the
    # op-amp output through R6/R8 - so its drop costs no accuracy. This is the
    # same network ../v2-module/README.md specified for the hub's ADC inputs.
    for bank, rs, cs in (("A", "R21", "C31"), ("B", "R22", "C32")):
        n("AMP_%s" % bank, rs, "1")
        n("ADC_%s" % bank, rs, "2")
        n("ADC_%s" % bank, cs, "1")
        n("GND", cs, "2")
    n("ADC_A", "U8", p("U8", "CH0"))
    n("ADC_B", "U8", p("U8", "CH1"))
    n("VREF", "U8", p("U8", "V_{REF}"))
    n("VREF", "R10", "2")
    n("VREF", "C10", "1")
    n("ROW_VCC", "R10", "1")
    n("GND", "C10", "2")
    n("+3.3V", "U8", p("U8", "V_{CC}"))
    n("GND", "U8", p("U8", "AGND"))
    n("GND", "U8", p("U8", "DGND"))
    for sig, pin in (("ADC_CONV", "CONV"), ("ADC_SDI", "SDI"),
                     ("ADC_SDO", "SDO"), ("ADC_SCK", "SCK")):
        n(sig, "U8", p("U8", pin))
    n("+3.3V", "C11", "1")
    n("GND", "C11", "2")
    n("VREF", "C26", "1")          # C0G beside the bulk
    n("GND", "C26", "2")

    # 5. The MCU. Everything the XIAO used to drive now comes off the RP2354A
    #    at the same logical position - see GPIO above.
    for sig, gpio in GPIO.items():
        name = "GPIO%d" % gpio
        if gpio in (26, 27, 28, 29):
            name = "GPIO%d/ADC%d" % (gpio, gpio - 26)
        n(sig, "U9", p("U9", name))

    # USB, with the series resistors the design guide asks for. The MCU side
    # and the connector side are DIFFERENT nets - that is the whole point of a
    # series element, and naming them the same is how a resistor ends up
    # shorted out by a net tie without anyone noticing.
    n("USB_D_P", "U9", p("U9", "USB_DP"))
    n("USB_D_N", "U9", p("U9", "USB_DM"))
    n("USB_D_P", "R23", "1")
    n("USBC_D_P", "R23", "2")
    n("USB_D_N", "R24", "1")
    n("USBC_D_N", "R24", "2")
    n("XIN", "U9", p("U9", "XIN"))
    # 1k in series with XOUT, the design guide's R2: it limits the drive so
    # the crystal is not over-driven at 3.3 V IOVDD. The MCU side and the
    # crystal side are different nets so the resistor cannot be shorted out.
    n("XOUT_MCU", "U9", p("U9", "XOUT"))
    n("XOUT_MCU", "R36", "1")
    n("XOUT", "R36", "2")
    n("RUN", "U9", p("U9", "RUN"))
    n("SWCLK", "U9", p("U9", "SWCLK"))
    n("SWDIO", "U9", p("U9", "SWDIO"))
    n("BOOTSEL", "U9", p("U9", "~{QSPI_SS}"))

    # Supplies. IOVDD and DVDD have several pins each and every one of them
    # has to land on the plane; counting them by hand is exactly the mistake
    # the hub's "MCU VSS pin(s) are not on PWR_GND" check exists to catch, so
    # the pin list is walked rather than enumerated.
    for name, net in (("IOVDD", "+3.3V"), ("QSPI_IOVDD", "+3.3V"),
                      ("USB_OTP_VDD", "+3.3V"), ("ADC_AVDD", "ADC_AVDD"),
                      ("VREG_VIN", "+3.3V"), ("VREG_AVDD", "VREG_AVDD"),
                      ("DVDD", "VCORE"), ("GND", "GND"), ("VREG_PGND", "GND")):
        for i in range(len(sym(*SYMOF["U9"])[name])):
            n(net, "U9", p("U9", name, i))

    # ADC_AVDD is its own rail now, fed through R26 and bypassed by C36. It
    # supplies the internal ADC, which reads RAIL_MON; sharing it with IOVDD
    # meant the converter's supply moved every time an I/O pin switched.
    n("+3.3V", "R26", "1")
    n("ADC_AVDD", "R26", "2")
    n("ADC_AVDD", "C36", "1")
    n("GND", "C36", "2")

    # VREG_AVDD is the core regulator's analogue supply. The RP2350 hardware
    # design guide: it "is very sensitive to noise, and therefore needs to be
    # filtered ... an RC filter of 33R and 4.7uF is adequate". It was tied
    # straight to +3.3V. VREG_VIN, the regulator's switched input, wants its
    # own 4.7uF at the pin as well (the guide's C6); it had a 100nF only.
    n("+3.3V", "R35", "1")
    n("VREG_AVDD", "R35", "2")
    n("VREG_AVDD", "C43", "1")
    n("GND", "C43", "2")
    n("VREG_AVDD", "C16", "1")    # the 100nF that sits at pin 46
    n("GND", "C16", "2")
    n("+3.3V", "C44", "1")
    n("GND", "C44", "2")

    # The core regulator is a buck on RP2350, not RP2040's LDO: it needs an
    # inductor on VREG_LX and its feedback taken at the core rail.
    n("VREG_LX", "U9", p("U9", "VREG_LX"))
    # L1 is polarised: the RP2350 datasheet (section 6.3.8, Figures 23 and 25)
    # puts its orientation dot on the OUTPUT end, and the footprint's pad 1 is
    # the dot. Pin 1 therefore goes to VCORE and pin 2 to the switch node.
    n("VCORE", "L1", "1")
    n("VREG_LX", "L1", "2")
    n("VCORE", "U9", p("U9", "VREG_FB"))
    n("VCORE", "C19", "1")
    n("GND", "C19", "2")
    # One per DVDD pin: the RP2354A has three, and the core rail had bulk only.
    for ref in ("C24", "C25", "C35"):
        n("VCORE", ref, "1")
        n("GND", ref, "2")

    # QSPI_SD0..3 and QSPI_SCLK are bonded to the stacked flash die inside the
    # RP2354A package and are deliberately left unconnected here. Only
    # ~QSPI_SS is brought out, because BOOTSEL needs to pull it low.
    n("BOOTSEL", "R25", "1")
    n("BOOTSEL_SW", "R25", "2")
    n("BOOTSEL_SW", "SW1", "1")
    n("GND", "SW1", "2")

    # RUN and the debug port. Left as they were, RUN floated and SWCLK/SWDIO
    # were one-pin nets - ERC calls those isolated labels, and it is right:
    # a reset line with nothing holding it is a board that resets when a hand
    # comes near it. J6 also gives a way to pulse RUN for BOOTSEL without
    # power-cycling a board that is halfway down a harness.
    n("RUN", "R20", "1")
    n("+3.3V", "R20", "2")
    for pin, net in ((1, "SWDIO"), (2, "SWCLK"), (3, "GND"), (4, "RUN")):
        n(net, "J6", p("J6", "Pin_%d" % pin))

    # One 100nF per supply pin on this rail, at last. It was 14 caps against
    # 17 pins; moving ADC_AVDD onto its own rail took one pin off, and C33/C34
    # cover the last two. The check below counts it rather than trusting this
    # comment, because the comment was wrong twice before.
    for ref in ("C12", "C13", "C14", "C15", "C28", "C29", "C33", "C34"):
        n("+3.3V", ref, "1")
        n("GND", ref, "2")

    # Crystal, for USB. The RP2350 can run its own ring oscillator but USB
    # cannot be clocked from it.
    n("XIN", "Y1", "1")
    n("XOUT", "Y1", "3")
    n("GND", "Y1", p("Y1", "G", 0))
    n("GND", "Y1", p("Y1", "G", 1))
    n("XIN", "C20", "1")
    n("GND", "C20", "2")
    n("XOUT", "C21", "1")
    n("GND", "C21", "2")

    # The two 5 V rails had no capacitor of their own. +5V_USB reached a
    # reservoir only through D2, and a diode into 22 uF is a peak detector,
    # not decoupling; +5V_BUS passes straight through J4 to J3, so on the
    # board nearest the supply it carries all eight boards' current down a
    # harness whose inductance nothing was damping.
    for ref in ("C37", "C38"):
        n("+5V_USB", ref, "1")
        n("GND", ref, "2")
    n("+5V_BUS", "C39", "1")
    n("GND", "C39", "2")

    # Status LED and the 5 V rail monitor.
    n("STATUS", "R17", "1")
    n("LED_A", "R17", "2")
    n("LED_A", "D3", p("D3", "A"))
    n("GND", "D3", p("D3", "K"))
    n("+5V", "R15", "1")
    n("RAIL_MON", "R15", "2")
    n("RAIL_MON", "R16", "1")
    n("GND", "R16", "2")
    n("RAIL_MON", "C45", "1")     # charge reservoir for the internal SAR
    n("GND", "C45", "2")

    # 6. The bus. U10 is the data transceiver, half duplex, with ~RE and DE
    #    tied so one GPIO turns the driver around. U11 only ever receives: it
    #    is the frame-start broadcast, and a board that could drive it would
    #    be a board that could desynchronise every other board on the harness.
    n("BUS_RO", "U10", p("U10", "RO"))
    n("BUS_DE", "U10", p("U10", "DE"))
    n("BUS_DE", "U10", p("U10", "~{RE}"))
    n("BUS_DI", "U10", p("U10", "DI"))
    n("GND", "U10", p("U10", "GND"))
    n("BUS_P", "U10", p("U10", "A"))
    n("BUS_N", "U10", p("U10", "B"))
    n("+3.3V", "U10", p("U10", "VCC"))

    n("SYNC_OUT", "U11", p("U11", "RO"))
    # The master is one of these boards (USB_POWER.md), so the sync driver
    # cannot be strapped off on all of them. DE goes to a GPIO that only the
    # master's firmware asserts; R37 holds it low through reset, while a board
    # is unprogrammed, and on every other board. DI stays on GND, so a driven
    # pair can only ever pull SYNC low: the frame-start pulse and nothing else.
    n("SYNC_DE", "U11", p("U11", "DE"))
    n("SYNC_DE", "R37", "1")
    n("GND", "R37", "2")
    n("GND", "U11", p("U11", "~{RE}"))      # always listening, the master included
    n("GND", "U11", p("U11", "DI"))
    n("GND", "U11", p("U11", "GND"))
    n("SYNC_P", "U11", p("U11", "A"))
    n("SYNC_N", "U11", p("U11", "B"))
    n("+3.3V", "U11", p("U11", "VCC"))

    n("+3.3V", "C17", "1")
    n("GND", "C17", "2")
    n("+3.3V", "C18", "1")
    n("GND", "C18", "2")

    # Termination across each pair, fitted only on the two end boards.
    n("BUS_P", "R11", "1")
    n("BUS_N", "R11", "2")
    n("SYNC_P", "R12", "1")
    n("SYNC_N", "R12", "2")

    # 7. The harness. Two connectors wired identically, so the bus and the
    #    power pass straight through a board rather than terminating in it.
    #    That is what makes a chain a chain: pull one board out and the
    #    harness is broken, which is the honest failure mode and the one that
    #    is obvious on a bench.
    for conn in ("J3", "J4"):
        for pin, net in ((1, "+5V_BUS"), (2, "GND"), (3, "BUS_P"),
                         (4, "BUS_N"), (5, "SYNC_P"), (6, "SYNC_N")):
            n(net, conn, p(conn, "Pin_%d" % pin))

    # Every connector shell to ground. The FH12 and JST GH footprints carry
    # mounting pads the plain Conn_01x* symbols have no pin for, so KiCad
    # warned twelve times that it could find no net for them and left them
    # floating. ../v2-module/README.md logged the same complaint and called it
    # worth fixing in the symbol; this is that fix. On THIS board it is more
    # than tidiness - a floating shell sits directly over 32 column lines at
    # megohm impedance, which is an antenna on the one board built to avoid
    # them, and grounding it makes it a shield instead.
    for conn in ("J1", "J2", "J3", "J4"):
        n("GND", conn, p(conn, "MountPin"))

    # 8. Power. The two 5 V sources are OR'd with Schottkys so plugging USB
    #    into a board on a powered harness does not back-drive the chain.
    n("+5V_BUS", "D1", p("D1", "A"))
    n("+5V", "D1", p("D1", "K"))
    n("+5V_USB", "D2", p("D2", "A"))
    n("+5V", "D2", p("D2", "K"))

    n("+5V", "U12", p("U12", "VIN"))
    n("+5V", "U12", p("U12", "EN"))
    n("GND", "U12", p("U12", "GND"))
    n("SW_NODE", "U12", p("U12", "SW"))
    n("SW_NODE", "L2", "1")
    n("+3.3V", "L2", "2")
    # The TLV62569 is an ADJUSTABLE part: FB servos to an internal reference,
    # so it takes a divider tap and NOT the output. Tying it to +3.3V directly
    # would regulate the rail down to the reference and brown out the board -
    # a mistake that looks like a dead regulator rather than a wiring error.
    n("+3.3V", "R18", "1")
    n("FB", "R18", "2")
    n("FB", "R19", "1")
    n("FB", "U12", p("U12", "FB"))
    n("GND", "R19", "2")
    n("+5V", "C22", "1")
    n("GND", "C22", "2")
    n("+5V", "C27", "1")
    n("GND", "C27", "2")
    n("+3.3V", "C23", "1")
    n("GND", "C23", "2")

    # USB-C, USB 2.0 data. U13 supplies both Rd terminations, including when
    # unpowered. External 5.1k resistors must NOT be fitted in parallel.
    for i in range(len(sym(*SYMOF["J5"])["VBUS"])):
        n("+5V_USB", "J5", p("J5", "VBUS", i))
    for i in range(len(sym(*SYMOF["J5"])["GND"])):
        n("GND", "J5", p("J5", "GND", i))
    for i in range(len(sym(*SYMOF["J5"])["D+"])):
        n("USBC_D_P", "J5", p("J5", "D+", i))
    for i in range(len(sym(*SYMOF["J5"])["D-"])):
        n("USBC_D_N", "J5", p("J5", "D-", i))
    n("USB_CC1", "J5", p("J5", "CC1"))
    n("USB_CC1", "U13", p("U13", "CC1"))
    n("USB_CC2", "J5", p("J5", "CC2"))
    n("USB_CC2", "U13", p("U13", "CC2"))
    n("GND", "J5", p("J5", "SHIELD"))

    # ESD at the connector. D+, D- and VBUS clamp to GND on the CONNECTOR
    # side of the series resistors, so a discharge on the cable meets the TVS
    # first and the 27 R second. The USBLC6-2P6 brings each line out on two
    # pins (1/6 and 3/4) so the trace can pass straight through it.
    for i in range(len(sym(*SYMOF["D5"])["I/O1"])):
        n("USBC_D_P", "D5", p("D5", "I/O1", i))
    for i in range(len(sym(*SYMOF["D5"])["I/O2"])):
        n("USBC_D_N", "D5", p("D5", "I/O2", i))
    n("+5V_USB", "D5", p("D5", "VBUS"))
    n("GND", "D5", p("D5", "GND"))

    # Hot-plug damper. A live cable's inductance into the ceramic capacitance
    # on +5V_USB rings; a 10 uF through 1 R across the rail absorbs the ring
    # instead of adding to it (a bare 10 uF would only lower the frequency).
    # The resistor is what makes it a damper, hence its own net and a check
    # that it cannot be shorted out.
    n("+5V_USB", "R38", "1")
    n("USB_SNUB", "R38", "2")
    n("USB_SNUB", "C46", "1")
    n("GND", "C46", "2")

    # PORT low = sink only; ADDR floating = GPIO mode. U13 uses the same
    # 3.3V supply as its pullups and the MCU, including on bus-powered boards.
    for pin in ("PORT", "~{EN}", "GND"):
        n("GND", "U13", p("U13", pin))
    n("+3.3V", "U13", p("U13", "VDD"))
    for ref in ("C40", "C41"):
        n("+3.3V", ref, "1")
        n("GND", ref, "2")
    n("+5V_USB", "R34", "1")
    n("USB_VBUS_DET", "R34", "2")
    n("USB_VBUS_DET", "U13", p("U13", "VBUS_DET"))
    for sig, pin, pullup in (("USB_CC_OUT1", "SDA/OUT1", "R32"),
                              ("USB_CC_OUT2", "SCL/OUT2", "R33")):
        n(sig, "U13", p("U13", pin))
        n(sig, pullup, "1")
        n("+3.3V", pullup, "2")

    # Protected power injection, from raw USB rather than through local D2.
    # D4 blocks reverse current continuously; U14's own reverse protection
    # alone has a millisecond response and cannot provide that guarantee.
    n("+5V_USB", "U14", p("U14", "IN"))
    n("+5V_USB", "C42", "1")
    n("GND", "C42", "2")
    n("GND", "U14", p("U14", "GND"))
    n("USB_BUS_EN", "U14", p("U14", "EN"))
    n("USB_BUS_EN", "R30", "1")
    n("GND", "R30", "2")
    n("USB_PWR_FAULT", "U14", p("U14", "~{FAULT}"))
    n("USB_PWR_FAULT", "R31", "1")
    n("+3.3V", "R31", "2")
    n("USB_BUS_SW", "U14", p("U14", "OUT"))
    n("USB_BUS_SW", "D4", p("D4", "A"))
    n("+5V_BUS", "D4", p("D4", "K"))
    n("USB_ILIM", "U14", p("U14", "ILIM"))
    n("USB_ILIM", "R27", "1")
    n("GND", "R27", "2")
    n("USB_ILIM", "R28", "1")
    n("USB_ILIM_LOW", "R28", "2")
    n("USB_ILIM_LOW", "Q1", p("Q1", "D"))
    n("GND", "Q1", p("Q1", "S"))
    n("USB_ILIM_HI", "Q1", p("Q1", "G"))
    n("USB_ILIM_HI", "R29", "1")
    n("GND", "R29", "2")

    # 9. Board address. Three 2-pad solder jumpers, each strapping its ADDR
    #    line to ground. The MCU holds the lines up internally, so a CLOSED
    #    jumper reads 0 and firmware must enable the internal pull-ups. The
    #    footprint is the bridged variant, so boards arrive with all three
    #    closed - every board is address 0 until two of the bridges are cut.
    for i, sig in enumerate(("ADDR0", "ADDR1", "ADDR2")):
        jp = "JP%d" % (i + 1)
        n(sig, jp, p(jp, "A"))
        n("GND", jp, p(jp, "B"))

    return {k: sorted(set(v)) for k, v in nets.items() if v}


# ----------------------------------------------------------------- emitters
def emit_net(nets, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write('(export (version "E")\n  (design (source "gen_rev3.py")'
                ' (tool "taxelscan rev3"))\n  (nets\n')
        for i, (name, nodes) in enumerate(sorted(nets.items()), 1):
            f.write('    (net (code "%d") (name "%s")\n' % (i, name))
            for ref, pin in nodes:
                f.write('      (node (ref "%s") (pin "%s"))\n' % (ref, pin))
            f.write('    )\n')
        f.write('  )\n)\n')


def emit_bom(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Reference", "Value", "Footprint", "LCSC", "Note"])
        for refs, _lib, _sym, value, fp, lcsc, note in PARTS:
            w.writerow([refs, value, fp, lcsc, note])


# ------------------------------------------------------------------- checks
def bom_value(ref):
    """The BOM value for one reference, from PARTS."""
    for refs, _lib, _sym, value, _fp, _lcsc, _note in PARTS:
        for r in refs.replace(" ", "").split(","):
            m = re.fullmatch(r"([A-Z]+)(\d+)-[A-Z]*(\d+)", r)
            if m:
                if (ref.rstrip("0123456789") == m.group(1)
                        and int(m.group(2)) <= int(ref[len(m.group(1)):])
                        <= int(m.group(3))):
                    return value
            elif r == ref:
                return value
    return None


def check(nets, rev1):
    """Assert everything README.md claims about this circuit."""
    fails = []

    def want(cond, msg):
        if not cond:
            fails.append(msg)

    pins = lambda name: set(nets.get(name, []))

    # No pin may sit on two nets. This is the check that catches a copy-paste
    # in any of the blocks above, and it is cheap enough to run first.
    net_of = {}
    for name, nodes in nets.items():
        for node in nodes:
            want(node not in net_of, "%s.%s appears on both %s and %s"
                 % (node[0], node[1], net_of.get(node), name))
            net_of[node] = name

    # The module and its nets are gone.
    want(not any(r == "A1" for r, _ in net_of), "A1 (the XIAO) is still present")
    want("+5V_BUS" in nets and "+5V_USB" in nets, "the 5 V sources are not separate")

    # --- the matrix survives rev-1 intact ---------------------------------
    rows = [n for n in nets if re.fullmatch(r"ROW_\d+", n)]
    want(len(rows) == 32, "expected 32 ROW_n nets, got %d" % len(rows))
    for name in rows:
        want(any(r == "J1" for r, _ in nets[name]), "%s does not reach J1" % name)
        want(any(r in ("U1", "U2", "U3", "U4") for r, _ in nets[name]),
             "%s has no row driver" % name)
    cols = [n for n in nets if re.fullmatch(r"COL_\d+", n)]
    want(len(cols) == 32, "expected 32 COL_n nets, got %d" % len(cols))
    for name in cols:
        want(any(r == "J2" for r, _ in nets[name]), "%s does not reach J2" % name)
        want(any(r in ("U5", "U6") for r, _ in nets[name]),
             "%s reaches no mux" % name)

    # Bank A is COL_0..15 and bank B is COL_16..31. The splitter board depends
    # on this and nothing else: it is what puts a whole 32x16 mat on one mux,
    # one gain stage and one ADC channel, so two mats never share a sense node.
    for name in cols:
        idx = int(name.split("_")[1])
        mux = "U5" if idx < 16 else "U6"
        want(any(r == mux for r, _ in nets[name]),
             "%s is not on %s - the splitter's bank assumption breaks" % (name, mux))

    # --- the gain stage ----------------------------------------------------
    # The follower is gone: an op-amp output must NOT appear on its own
    # inverting input, or the gain is 1 and the board reads a tenth of scale.
    for unit, (bank, rf, rg) in enumerate((("A", "R6", "R7"), ("B", "R8", "R9"))):
        out = ("U7", p("U7", "", unit))
        inn = ("U7", p("U7", "-", unit))
        inp = ("U7", p("U7", "+", unit))
        want(net_of.get(out) == "AMP_%s" % bank,
             "bank %s: op-amp output is on %s" % (bank, net_of.get(out)))
        want(net_of.get(inn) == "GAIN_%s" % bank,
             "bank %s: inverting input is on %s" % (bank, net_of.get(inn)))
        want(net_of.get(out) != net_of.get(inn),
             "bank %s: output tied to inverting input - still a follower" % bank)
        want(net_of.get(inp) == "SENSE_%s" % bank,
             "bank %s: non-inverting input is not on the sense node" % bank)
        # Rf from output to the divider, Rg from the divider to ground.
        want((rf, "1") in pins("GAIN_%s" % bank) and (rf, "2") in pins("AMP_%s" % bank),
             "bank %s: %s is not the feedback resistor" % (bank, rf))
        want((rg, "1") in pins("GAIN_%s" % bank) and (rg, "2") in pins("GND"),
             "bank %s: %s does not set the gain against ground" % (bank, rg))
        # The sense node carries the pulldown, the mux common and nothing else
        # that could load it. Anything extra here costs settling time.
        pulldown = "R1" if bank == "A" else "R2"
        mux = "U5" if bank == "A" else "U6"
        sense = {r for r, _ in nets["SENSE_%s" % bank]}
        want(sense == {pulldown, mux, "U7"},
             "SENSE_%s carries %s, want {%s, %s, U7}"
             % (bank, sorted(sense), pulldown, mux))

    # --- the converter -----------------------------------------------------
    for bank, ch, rs, cs in (("A", "CH0", "R21", "C31"),
                             ("B", "CH1", "R22", "C32")):
        want(("U8", p("U8", ch)) in pins("ADC_%s" % bank),
             "ADC %s is not on bank %s" % (ch, bank))
        # Series, not parallel: the op-amp output must NOT appear on the ADC
        # net, or the reservoir is shorted out and the SAR samples straight
        # off the amplifier. Same shape as the v2 module's 51R check.
        want((rs, "1") in pins("AMP_%s" % bank), "%s is not on the amp side" % rs)
        want((rs, "2") in pins("ADC_%s" % bank), "%s is not on the ADC side" % rs)
        want(not (pins("AMP_%s" % bank) & pins("ADC_%s" % bank)),
             "bank %s: the amp output is shorted past %s" % (bank, rs))
        want((cs, "1") in pins("ADC_%s" % bank) and (cs, "2") in pins("GND"),
             "bank %s: no charge reservoir at the ADC pin" % bank)
        # The feedback must still close at the OUTPUT, not after the resistor,
        # or the 51R lands inside the loop and the amp drives the ADC again.
        rf = "R6" if bank == "A" else "R8"
        want((rf, "2") in pins("AMP_%s" % bank),
             "bank %s: %s takes feedback from the wrong side of %s"
             % (bank, rf, rs))

    # No two different values may share an LCSC number. C25905 was on BOTH
    # R13/R14 (5.1k, the USB-C CC pulldowns) and R21/R22 (51R, the ADC series
    # resistors) - a copy-paste that would have been ordered without comment
    # and made the charge reservoir 5.1k x 1nF = 5.1 us against a ~1 us
    # acquisition window. The converter would have read noise and the board
    # would have looked like a bad sensor.
    seen = {}
    for refs, _lib, _sym, value, _fp, lcsc, _note in PARTS:
        if not lcsc:
            continue
        if lcsc in seen and seen[lcsc][0] != value:
            want(False, "LCSC %s is on both %s (%s) and %s (%s)"
                 % (lcsc, seen[lcsc][1], seen[lcsc][0], refs, value))
        seen.setdefault(lcsc, (value, refs))

    # A rail that arrives on a connector needs a reservoir at the connector.
    # Neither of these had one; the audit that found it is in README.md.
    for rail in ("+5V_USB", "+5V_BUS"):
        cs = [r for r, _ in nets.get(rail, []) if r.startswith("C")]
        bulk = [c for c in cs if bom_value(c).endswith("uF")]
        want(bulk, "%s arrives on a connector with no bulk capacitor" % rail)
    want([c for c, _ in nets.get("+5V_USB", [])
          if c.startswith("C") and bom_value(c) == "100nF"],
         "+5V_USB has bulk but no high-frequency capacitor")

    # The two switch nodes must stay bare. A capacitor on either is a fault,
    # not an improvement: it is the node the inductor current commutates
    # through, and loading it wrecks the regulator it belongs to.
    for node in ("SW_NODE", "VREG_LX"):
        want(not [r for r, _ in nets.get(node, []) if r.startswith("C")],
             "%s has a capacitor on it - that is a switch node" % node)

    # Every rail that feeds a chip needs a high-frequency path to ground, not
    # just bulk. VCORE, VREF and +5V each had bulk only.
    for rail in ("+3.3V", "VCORE", "VREF", "+5V", "ROW_VCC", "ADC_AVDD"):
        hf = [r for r, _ in nets.get(rail, [])
              if r.startswith("C") and bom_value(r) in ("100nF", "10nF")]
        want(hf, "%s has no 100nF decoupling, only bulk" % rail)
    # The regulator's analogue supply: RC-filtered from +3.3V, per the
    # RP2350 design guide, and reaching nothing but the pin and its cap.
    want(("R35", "1") in pins("+3.3V") and ("R35", "2") in pins("VREG_AVDD"),
         "VREG_AVDD is not filtered off +3.3V through R35")
    want({r for r, _ in nets.get("VREG_AVDD", [])} == {"R35", "C43", "C16", "U9"},
         "VREG_AVDD carries %s, want R35, C43, C16 and the pin only"
         % sorted({r for r, _ in nets.get("VREG_AVDD", [])}))
    want(bom_value("C43") == "4.7uF" and bom_value("R35") == "33R",
         "VREG_AVDD filter is not 33R + 4.7uF")
    want(("C44", "1") in pins("+3.3V") and bom_value("C44") == "4.7uF",
         "VREG_VIN has no 4.7uF reservoir on +3.3V")
    want(bom_value("L1").startswith("AOTA-B201610S3R3"),
         "L1 is not the design guide's polarity-marked inductor")
    # The crystal circuit is the design guide's, part for part.
    want(("R36", "1") in pins("XOUT_MCU") and ("R36", "2") in pins("XOUT"),
         "XOUT has no series resistor between the MCU and the crystal")
    want(not any(r == "Y1" for r, _ in nets.get("XOUT_MCU", [])),
         "the crystal is on the MCU side of R36")
    want(bom_value("R36") == "1k" and bom_value("Y1") == "ABM8-272-T3"
         and bom_value("C20") == "15pF",
         "crystal circuit is not ABM8-272-T3 / 15pF / 1k")
    want(("C45", "1") in pins("RAIL_MON") and ("C45", "2") in pins("GND"),
         "RAIL_MON has no reservoir capacitor at the ADC pin")
    # VREF must reach the ROW_VCC side of R5, not the +3.3V side, or the
    # measurement stops being ratiometric and the rail stops cancelling.
    want(("R10", "2") in pins("VREF") and ("R10", "1") in pins("ROW_VCC"),
         "VREF is not filtered off ROW_VCC through R10")
    want(("U8", p("U8", "V_{REF}")) in pins("VREF"), "ADC VREF is not on VREF")
    want(not any(r == "U8" and pin == p("U8", "V_{REF}")
                 for r, pin in nets.get("+3.3V", [])),
         "ADC VREF is tied straight to +3.3V, bypassing the RC and the tap")
    want(("R5", "1") in pins("+3.3V") and ("R5", "2") in pins("ROW_VCC"),
         "R5 no longer links +3.3V to ROW_VCC")

    # --- the MCU -----------------------------------------------------------
    # Mux selects must stay contiguous and in order: scan.h writes all four
    # with a single store, and any other arrangement puts an intermediate
    # code on the selects mid-transition.
    sel = [GPIO["MUX_S%d" % i] for i in range(4)]
    want(sel == list(range(sel[0], sel[0] + 4)),
         "MUX_S0..S3 are on GPIO %s - not contiguous, scan.h's single store breaks" % sel)
    want(len(set(GPIO.values())) == len(GPIO), "two signals share one GPIO")

    # Every supply pin of the MCU lands somewhere. Counting these by hand is
    # the mistake; walking the symbol is the fix.
    mcu = sym(*SYMOF["U9"])
    for name, net in (("IOVDD", "+3.3V"), ("DVDD", "VCORE"), ("GND", "GND"),
                      ("ADC_AVDD", "ADC_AVDD"), ("VREG_AVDD", "VREG_AVDD"),
                      ("VREG_VIN", "+3.3V")):
        missing = [num for num in mcu[name] if ("U9", num) not in pins(net)]
        want(not missing, "MCU %s pin(s) %s are not on %s" % (name, missing, net))

    # One 100nF per IC supply pin, counted rather than asserted in a comment -
    # the comment claimed "14 of 17" and was wrong twice as parts moved.
    #
    # Only pins whose SYMBOL name is a supply name count. Everything else on
    # the rail - resistors, connectors, the regulator's VREG_FB sense pin -
    # is not something that needs bypassing, and counting them made this read
    # 21 pins on a rail that has 16.
    SUPPLY_NAMES = ("IOVDD", "DVDD", "QSPI_IOVDD", "USB_OTP_VDD", "VREG_VIN",
                    "VREG_AVDD", "ADC_AVDD", "AVDD", "VCC", "VDD", "V+", "VS")
    for rail in ("+3.3V", "VCORE", "ADC_AVDD", "VREG_AVDD"):
        supply = set()
        for r, pin in nets.get(rail, []):
            if not r.startswith("U") or r not in SYMOF:
                continue
            names = sym(*SYMOF[r])
            if any(pin in names.get(nm, []) for nm in SUPPLY_NAMES):
                supply.add((r, pin))
        caps = [r for r, _ in nets.get(rail, [])
                if r.startswith("C") and bom_value(r) in ("100nF", "1uF", "4.7uF")]
        want(len(caps) >= len(supply),
             "%s has %d IC supply pin(s) and only %d x 100nF - one per pin "
             "is the target" % (rail, len(supply), len(caps)))

    # The series elements. Each of these exists because the thing on the far
    # side of it is a stub on something fast, and each is easy to delete by
    # accident when a net gets renamed.
    want(("R23", "1") in pins("USB_D_P") and ("R23", "2") in pins("USBC_D_P"),
         "USB_D_P has no series resistor between the MCU and the connector")
    want(("R24", "1") in pins("USB_D_N") and ("R24", "2") in pins("USBC_D_N"),
         "USB_D_N has no series resistor between the MCU and the connector")
    want(not any(r == "J5" for r, _ in nets.get("USB_D_P", [])
                 ) and not any(r == "J5" for r, _ in nets.get("USB_D_N", [])),
         "the connector is on the MCU side of the USB series resistors")
    want(("R25", "1") in pins("BOOTSEL") and ("R25", "2") in pins("BOOTSEL_SW"),
         "BOOTSEL has no series resistor - the switch hangs off ~QSPI_SS, "
         "which is a live chip-select to the stacked flash die")
    want(not any(r == "SW1" for r, _ in nets.get("BOOTSEL", [])),
         "SW1 is on the MCU side of R25")
    want(("R26", "1") in pins("+3.3V") and ("R26", "2") in pins("ADC_AVDD"),
         "ADC_AVDD is not filtered off +3.3V through R26")
    want(not (pins("ADC_AVDD") & {("U9", num) for num in mcu["IOVDD"]}),
         "ADC_AVDD is shorted back onto IOVDD")
    # The core rail is generated, not fed from 3.3 V.
    want(("U9", p("U9", "VREG_FB")) in pins("VCORE"), "VREG_FB is not on the core rail")
    want(("L1", "2") in pins("VREG_LX") and ("L1", "1") in pins("VCORE"),
         "the core regulator has no inductor between VREG_LX and DVDD, or its "
         "dot (pin 1) is not on the output end")
    want(not (pins("VCORE") & pins("+3.3V")), "the core rail is shorted to +3.3V")
    # QSPI is bonded to the stacked flash inside the package; only ~QSPI_SS
    # comes out, for BOOTSEL.
    for name in ("QSPI_SD0", "QSPI_SD1", "QSPI_SD2", "QSPI_SD3", "QSPI_SCLK"):
        want(not any(("U9", num) in net_of for num in mcu[name]),
             "%s is wired, but it is bonded to the stacked flash" % name)

    # RUN must be held, and the debug pins must go somewhere. Both were
    # one-pin nets in the first version of this generator.
    want(("R20", "1") in pins("RUN") and ("R20", "2") in pins("+3.3V"),
         "RUN is not pulled up through R20")
    for sig in ("RUN", "SWCLK", "SWDIO"):
        want(len(nets.get(sig, [])) >= 2, "%s is a one-pin net" % sig)
        want(any(r == "J6" for r, _ in nets.get(sig, [])),
             "%s does not reach the debug connector" % sig)
    want(("J6", p("J6", "Pin_3")) in pins("GND"),
         "the debug connector has no ground reference")
    for i in range(1, 4):
        jp = "JP%d" % i
        want((jp, p(jp, "B")) in pins("GND"),
             "%s does not pull its address bit to ground" % jp)

    # --- the bus -----------------------------------------------------------
    # ~RE and DE tied: one GPIO turns the driver around. Split across two
    # GPIOs and a firmware slip leaves the driver enabled, which takes the
    # whole harness down rather than one board.
    want(net_of.get(("U10", p("U10", "DE"))) == net_of.get(("U10", p("U10", "~{RE}"))),
         "the data transceiver's DE and ~RE are not tied")
    # A board that drives the frame-start pair can desynchronise every other
    # board, so driving it must take a deliberate act of firmware: DI strapped
    # low (a driven pair can only pull SYNC low), DE on a GPIO with a real
    # pull-down (so reset and blank boards are receive-only), ~RE always on.
    want(("U11", p("U11", "DI")) in pins("GND"),
         "the sync transceiver's DI is not strapped to GND")
    want(("U11", p("U11", "DE")) in pins("SYNC_DE") and ("U9", p("U9", "GPIO%d" % GPIO["SYNC_DE"])) in pins("SYNC_DE"),
         "the sync driver's DE does not reach its GPIO")
    pulldown = [r for r, pin in nets.get("SYNC_DE", []) if r.startswith("R")
                and (r, "2" if pin == "1" else "1") in pins("GND") and bom_value(r) in ("4.7k", "10k")]
    want(pulldown, "the sync driver's DE has no pull-down (<= 10k) to GND - a blank board could drive the pair")
    want(not any(r.startswith("R") and r not in pulldown for r, _ in nets.get("SYNC_DE", [])),
         "something other than the pull-down sits on SYNC_DE")
    want(("U11", p("U11", "~{RE}")) in pins("GND"),
         "the sync receiver is not permanently enabled")
    want(net_of.get(("U11", p("U11", "RO"))) == "SYNC_OUT",
         "the sync receiver's output does not reach the MCU")

    # Both harness connectors carry the same six nets, or the bus stops at a
    # board instead of passing through it.
    j3 = {p_: net_of.get(("J3", p_)) for _r, p_ in nets_of_ref(nets, "J3")}
    j4 = {p_: net_of.get(("J4", p_)) for _r, p_ in nets_of_ref(nets, "J4")}
    want(sorted(j3) == ["1", "2", "3", "4", "5", "6", "MP"],
         "J3 carries %s, want the six harness conductors and the shell"
         % sorted(j3))
    want(j3 == j4, "J3 and J4 differ: %s vs %s - the bus does not pass through"
         % (j3, j4))
    want(j3.get("1") == "+5V_BUS" and j3.get("2") == "GND",
         "the harness does not carry power on pins 1 and 2")
    # The two pairs must not be crossed. Swapping A and B inverts the line for
    # every board downstream, which looks like a dead bus rather than a
    # miswired one.
    want(j3.get("3") == "BUS_P" and j3.get("4") == "BUS_N",
         "the data pair is not on harness pins 3/4")
    want(j3.get("5") == "SYNC_P" and j3.get("6") == "SYNC_N",
         "the sync pair is not on harness pins 5/6")
    want(("R11", "1") in pins("BUS_P") and ("R11", "2") in pins("BUS_N"),
         "the data pair has no termination position")
    want(("R12", "1") in pins("SYNC_P") and ("R12", "2") in pins("SYNC_N"),
         "the sync pair has no termination position")

    # --- power -------------------------------------------------------------
    # OR'ing, not paralleling. If either source reaches +5V directly, plugging
    # USB into a powered harness back-drives the chain.
    # Comparing node sets is not enough here: moving VBUS onto +5V leaves the
    # two nets sharing no NODE while shorting the two sources together, which
    # is exactly the fault this is meant to catch. So name who is allowed on
    # each rail instead.
    #
    # The decoupling is named explicitly rather than waved through with a
    # "capacitors are always fine" rule. The point of this check is that
    # nothing may bridge the two 5 V sources, and a capacitor bridging them
    # would do it just as effectively as a wire.
    for src, diode, allowed in (("+5V_BUS", "D1", {"J3", "J4", "C39", "D4"}),
                                ("+5V_USB", "D2", {"J5", "C37", "C38", "U14", "C42", "R34", "D5", "R38"})):
        want((diode, p(diode, "A")) in pins(src),
             "%s does not feed %s's anode" % (src, diode))
        want((diode, p(diode, "K")) in pins("+5V"),
             "%s's cathode is not on +5V" % diode)
        on_src = {r for r, _ in nets.get(src, [])}
        want(on_src == allowed | {diode},
             "%s carries %s, want %s" % (src, sorted(on_src),
                                         sorted(allowed | {diode})))
        on_5v = {r for r, _ in nets.get("+5V", [])}
        want(not (on_5v & allowed),
             "%s reaches +5V directly, bypassing %s" % (sorted(on_5v & allowed), diode))
    # Independently verify the ordered packages, including stock-symbol alias.
    for ref, expected in {
        "U13": {"CC1":"1", "CC2":"2", "PORT":"3", "VBUS_DET":"4", "ADDR":"5",
                "~{INT}/OUT3":"6", "SDA/OUT1":"7", "SCL/OUT2":"8", "ID":"9",
                "GND":"10", "~{EN}":"11", "VDD":"12"},
        "U14": {"IN":"1", "GND":"2", "EN":"3", "~{FAULT}":"4", "ILIM":"5", "OUT":"6"},
        "Q1": {"G":"1", "S":"2", "D":"3"},
        "D4": {"K":"1", "A":"2"},
    }.items():
        want(all(p(ref, name) == number for name, number in expected.items()),
             ref + " ordered package pin map does not match TI datasheet")
    want(bom_value("U13") == "TUSB320LAIRWBR", "U13 requires LAI dynamic CC updates")
    for num in (1, 2):
        want(pins("USB_CC%d" % num) == {("J5", p("J5", "CC%d" % num)),
                                        ("U13", p("U13", "CC%d" % num))},
             "CC termination must be U13 internal Rd only")
    for pin in ("PORT", "~{EN}", "GND"):
        want(("U13", p("U13", pin)) in pins("GND"), "U13 must stay enabled, sink only")
    want(("U13", p("U13", "ADDR")) not in net_of, "U13 ADDR must float for GPIO mode")
    for net, nodes in {
        "USB_BUS_SW": {("U14", "6"), ("D4", p("D4", "A"))},
        "USB_ILIM": {("U14", "5"), ("R27", "1"), ("R28", "1")},
        "USB_ILIM_LOW": {("R28", "2"), ("Q1", p("Q1", "D"))},
        "USB_VBUS_DET": {("R34", "2"), ("U13", "4")},
    }.items():
        want(pins(net) == nodes, "USB power topology mismatch: " + net)
    want(("D4", p("D4", "K")) in pins("+5V_BUS"), "D4 must block harness-to-USB current")
    for ref, net in (("R29", "USB_ILIM_HI"), ("R30", "USB_BUS_EN"), ("R27", "USB_ILIM")):
        want((ref,"1") in pins(net) and (ref,"2") in pins("GND"), ref + " default-state path missing")
    want(("Q1", p("Q1", "S")) in pins("GND") and
         ("Q1", p("Q1", "G")) in pins("USB_ILIM_HI"), "Q1 gate/source wiring wrong")
    for ref in ("R27", "R28"):
        want(bom_value(ref) == "82.5k", ref + " current-limit value wrong")
    want(bom_value("R34") in ("866k", "887k"),
         "VBUS_DET must use 866k 0.5% thin film (or 887k 1%): TI's window is 855-920k")
    for ref in ("R29", "R30"):
        want(bom_value(ref) == "4.7k", ref + " must hold the control pin LOW at reset")
    for sig, gpio in {"USB_BUS_EN":24,"USB_ILIM_HI":0,"USB_CC_OUT1":27,"USB_CC_OUT2":29,"USB_PWR_FAULT":23}.items():
        want(GPIO.get(sig)==gpio, "USB power GPIO differs from firmware: " + sig)
    for ref, pin, net in (("U14","IN","+5V_USB"),("U14","EN","USB_BUS_EN"),
                           ("U14","~{FAULT}","USB_PWR_FAULT"),("U14","GND","GND"),
                           ("U13","VDD","+3.3V"),("U13","SDA/OUT1","USB_CC_OUT1"),
                           ("U13","SCL/OUT2","USB_CC_OUT2")):
        want((ref,p(ref,pin)) in pins(net), "USB power pin disconnected: " + ref + "." + pin)
    want(("L2", "2") in pins("+3.3V") and ("L2", "1") in pins("SW_NODE"),
         "the buck has no inductor between SW and +3.3V")
    want(not any(r == "U12" and pin == p("U12", "SW") for r, pin in nets["+3.3V"]),
         "the buck's switch node is shorted to its output")
    # FB on a divider tap, never on the output: see the note in build().
    want(("U12", p("U12", "FB")) not in pins("+3.3V"),
         "the buck's FB is tied to its output - it will regulate to the reference")
    want(("R18", "1") in pins("+3.3V") and ("R18", "2") in pins("FB")
         and ("R19", "1") in pins("FB") and ("R19", "2") in pins("GND"),
         "the buck feedback divider is not between +3.3V and GND")
    # ... and its VALUES. Topology alone passes with E24 parts that would put
    # the rail at 3.62 V, i.e. at the MCU's absolute maximum.
    want(bom_value("R18") == "180k" and bom_value("R19") == "40.2k",
         "the buck divider is not 180k/40.2k - the rail will not be 3.29 V")

    # No connector shell may be left floating.
    for conn in ("J1", "J2", "J3", "J4"):
        want((conn, p(conn, "MountPin")) in pins("GND"),
             "%s's shell is not grounded" % conn)

    # --- ESD and hot-plug damping on the USB connector ---------------------
    # The TVS sits on the connector side of R23/R24, both of each line's
    # pass-through pins on the same net, VBUS pin on the raw VBUS rail.
    tvs = sym(*SYMOF["D5"])
    want(all(("D5", num) in pins("USBC_D_P") for num in tvs["I/O1"]),
         "the USB TVS I/O1 pins are not both on USBC_D_P")
    want(all(("D5", num) in pins("USBC_D_N") for num in tvs["I/O2"]),
         "the USB TVS I/O2 pins are not both on USBC_D_N")
    want(not any(r == "D5" for r, _ in nets.get("USB_D_P", []) + nets.get("USB_D_N", [])),
         "the USB TVS sits on the MCU side of the series resistors")
    want(("D5", p("D5", "VBUS")) in pins("+5V_USB") and ("D5", p("D5", "GND")) in pins("GND"),
         "the USB TVS does not clamp VBUS to GND")
    # The damper is a SERIES RC from +5V_USB to GND: the resistor is the
    # point, so its node is named and must carry exactly the two parts.
    want(pins("USB_SNUB") == {("R38", "2"), ("C46", "1")},
         "the hot-plug damper is not a series RC: USB_SNUB carries %s"
         % sorted(pins("USB_SNUB")))
    want(("R38", "1") in pins("+5V_USB") and ("C46", "2") in pins("GND"),
         "the hot-plug damper does not span +5V_USB to GND")
    want(bom_value("R38") == "1R" and bom_value("C46") == "10uF",
         "the hot-plug damper is not 1R + 10uF")

    # --- every part can actually be placed ---------------------------------
    for refs, _lib, _sym, _v, fp, _lcsc, _note in PARTS:
        want(footprint_exists(fp), "%s: footprint %s is in no library" % (refs, fp))

    # --- nothing from rev-1 was silently dropped ---------------------------
    kept = {n for n in rev1 if re.fullmatch(r"(ROW|COL)_\d+|SR_CHAIN_\d+", n)}
    want(kept <= set(nets), "lost from rev-1: %s" % sorted(kept - set(nets)))
    for name in ("ROW_CLK", "ROW_LATCH", "ROW_DATA", "MUX_S0", "MUX_S1",
                 "MUX_S2", "MUX_S3", "SENSE_A", "SENSE_B", "ROW_VCC"):
        want(name in nets, "rev-1 net %s went missing" % name)

    return fails


def nets_of_ref(nets, ref):
    return [(r, pin) for nodes in nets.values() for r, pin in nodes if r == ref]


def main():
    rev1 = load_rev1()
    nets = build(rev1)
    fails = check(nets, rev1)
    emit_net(nets, OUT_NET)
    emit_bom(OUT_BOM)

    refs = sorted({r for nodes in nets.values() for r, _ in nodes})
    conns = sum(len(v) for v in nets.values())
    print("rev3 netlist: %d nets, %d components, %d connections"
          % (len(nets), len(refs), conns))
    print("  " + " ".join(refs))
    if fails:
        print("\n%d CHECK(S) FAILED:" % len(fails))
        for f in fails:
            print("  " + f)
        return 1
    print("\nall %d nets check out" % len(nets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
