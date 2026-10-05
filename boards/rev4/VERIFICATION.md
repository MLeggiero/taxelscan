# rev-4 functional verification — 5 October 2026

The board as committed at `7ca63a4` (`rev4.kicad_pcb`, `rev4.kicad_sch`, `rev4.net`,
`BOM.csv`, `fab/`) was checked for one question: built as it stands, will it work?

Every IC was checked pin by pin against its datasheet. Datasheet text was extracted on
the day from Raspberry Pi, TI, Nexperia, JST, Hirose, HRO, Omron and Lite-On; ST's site
was unreachable, so the USBLC6-2P6 was checked against its standard SOT-666 pinout. The
analog chain and the USB hot-plug were simulated in ngspice. The routed copper was
measured on the board itself. The project's own checks were re-run.

The scripts are in [verify/](verify/README.md).

## Verdict

**The circuit is correct and complete. Nothing found would stop the board powering up,
booting, enumerating on USB, scanning the mat or talking RS-485.** The rev-4 netlist is
rev-3's audited circuit with exactly the intended changes. The ADC and its reference
network are gone, and four MCU pins moved; the other 128 nets have the same pins. The
routing is DRC- and parity-clean and sound on every layout measure taken.

Three things should be dealt with before ordering. None of them was introduced by rev-4:

1. **The RP2354A's core regulator is not laid out the way Raspberry Pi require.** Its
   input and output capacitors sit 5.7–5.9 mm from the regulator pins, against 1–2 mm
   in the reference. The placement is rev-3's.
2. **Hot-plug from a stiff, always-on 5 V supply can still exceed the buck's 6 V absolute
   maximum**, even with the R38 / C46 damper. A compliant USB-C source or an A-to-C cable
   does not.
3. **The harness cable is not specified, and the obvious cable is the wrong one.**
   J3 and J4 are the same part turned 180°, so a cable laid straight across between two
   boards joins pin 1 to pin 6.

The rest are minor. The rev-4 acquisition firmware does not exist yet (as for rev-3), and
the pieces that do exist need three corrections before they meet this board.

| check (re-run on the committed files) | result |
|---|---|
| KiCad 10.0.6 DRC, zones refilled, all track errors | 0 unconnected, 0 copper violations; rev-3's 5 silkscreen warnings |
| Schematic parity | 199 items, all the leading-`/` name prefix or J5's two open SBU pins |
| ERC | 0 errors; 7 `lib_symbol_mismatch` warnings (the embedded KiCad-7 symbols) |
| `gen_rev4.py` / `check_faults.py` | 135 of 135 nets / 40 of 40 faults behave |
| `firmware/rev3_power` native tests | pass for rev-3 and for `-DTAXELSCAN_BOARD_REV=4` |
| netlist against rev-3 (`verify/netdiff.py`) | removed U8, C8, C10, C26, R10 and the ADC_* / VREF nets. GPIO18/19 now carry USB_CC_OUT1/2, GPIO27/29 carry ADC_A/ADC_B, GPIO16/17 are free. Nothing else changed |
| `fab/` against the board | 311 via drills = 311 vias; 4 J5 shield slots, 4 J6 holes, 2 NPTH pegs; paste, mask and silk gerbers identical apart from dates; CPL and BOM unchanged |

## Findings

| # | | finding | consequence | fix | since |
|---|---|---|---|---|---|
| 1 | **MAJOR** | Core regulator layout departs from RP2350 datasheet §6.3.8 / Fig. 26, which "must be strictly followed". CIN (C44, 4.7 µF) is 5.7 mm from VREG_VIN and reaches it only through the planes; pin 49 has only C17 (100 nF) locally. COUT's (C19) ground is 5.9 mm from VREG_PGND. The LX → L1 → C19 → GND → PGND loop is about 20 mm² against 2–3 mm² in Raspberry Pi's minimal design. | The design guide (§2.1) says other layouts "work… well enough to execute code" but may not hold the output correct across load. DVDD must stay within 1.05–1.16 V. | Re-place L1, C19 and C44 as Fig. 26: CIN straddling pins 49 / 47, COUT beside it sharing PGND's ground through two vias, L1 above, LX straight up between them; move R23 / R24 and the VREG_AVDD filter to make room. At minimum, scope VCORE at pin 23 under load steps at bring-up. | rev-3 |
| 2 | **MAJOR** | Hot plug, simulated: from a fixed supply with a captive cable (1.5 µH, 0.08 Ω), U12's VIN peaks at 6.3–6.5 V and +5V_USB at 6.6–6.8 V, even with the damper. Limits are 6 V (TLV62569) and 7 V (TPS2553). With cable loop resistance 0.1 Ω the peak is 6.1–6.7 V. Compliant USB-C sources (VBUS on only after Rd is seen) and A-to-C cables (≥ 0.3 Ω) stay ≤ 5.3 V. | U12 overstressed on stiff, always-on 5 V supplies. | R38 0.47 Ω with C46 22 µF (simulated ≤ 5.9 V at the worst corner, at more attach capacitance), or a buck rated ≥ 7 V. Scope U12 VIN at bring-up either way. | rev-3 (AUDIT §7.3 item 5; the damper of §9 reduced it) |
| 3 | **MAJOR** (integration) | No harness cable is specified. J4's pins run 1→6 down the left edge and J3's 6→1 down the right, so between two boards a cable laid straight across joins pin 1 to pin 6. | +5V_BUS lands on SYNC_N and GND on SYNC_P; the BUS pair is swapped; the next board gets no power; an end board's R12 dissipates 208 mW against 0.1 W. | Specify GHR-06V-S housings wired pin n ↔ pin n (the wires cross between adjacent boards), twisted pairs on 3/4 and 5/6; mark pin 1 at J3 / J4 in silk. | rev-3 |
| 4 | MINOR | QSPI_IOVDD (pin 54) supplies the internal flash but has no capacitor of its own. Pin 54 is not tied to pin 53 (C18); the nearest 100 nF is C33, 3.8 mm away along 0.15 mm track. The datasheet asks for its "increased high-frequency currents" to be accounted for. | Supply noise on the stacked flash at high QSPI clock. | Tie pins 53 and 54 together at the pin row so C18 serves both (the design guide's sharing), or add a 0201 at pin 54. | rev-3 |
| 5 | MINOR | No second 4.7 µF on VCORE at DVDD pin 23, which §6.3.8.1 recommends "for best performance". Pin 23 has only C25 (100 nF), 13 mm of VCORE track from C19. | Larger VCORE droop at pin 23 on load steps. | 4.7 µF at pin 23, away from LX / COUT. | rev-3 |
| 6 | MINOR | D2's reverse leakage floats +5V_USB on a board powered only from the harness. PMEG2005AEA leaks 15 µA typ / 40 µA max at 10 V; the only DC load on the node is R34 plus U13's internal pull-down, about 961 kΩ. A few µA holds the node near 2.9 V. | It back-drives an unpowered host's VBUS. A compliant USB-C source waits for VBUS below vSafe0V before turning VBUS on, so a harness-powered board plugged into USB for BOOTSEL may never enumerate. | A ~10 kΩ bleeder from +5V_USB to GND (0.5 mA at 5 V). | rev-3 |
| 7 | MINOR | BUS_DE (U10's DE and ~RE, tied) has no external pull-down. Undriven, the SN65HVD75's internal 3 MΩ / 1 MΩ put it at 0.83 V, between VIL and VIH. R37 on SYNC_DE is 10 kΩ, above E9's 8.2 kΩ. Both are safe at reset, when the pad pull-down holds them low. | A board whose firmware leaves GPIO10 / GPIO13 as inputs can enable its driver and hold the whole harness. | 4.7 kΩ from BUS_DE to GND and R37 → 4.7 kΩ (the R29 / R30 reel); drive both low on slaves. | rev-3 |
| 8 | MINOR | USB_ILIM (TPS2553 pin 5 to R27 / R28) is 12.8 mm with 4 vias; R27 / R28 are 3.4 mm from the pin. The datasheet §9.5.1 asks for it "as short as possible". The first rev-4 had 9.7 mm with no via. | Noise pick-up on the current-limit setting. | Pre-route it directly on F.Cu. | the re-route |
| 9 | MINOR | QSPI_SS (BOOTSEL) runs 13.7 mm with 2 vias to R25 / SW1 on the flash's live chip-select. The design guide puts the resistor at the pin. | Load and emissions on the flash CS. | Move R25 next to pin 60. | rev-3 |
| 10 | MINOR | J6 (SWDIO, SWCLK, GND, RUN) does not follow Raspberry Pi's 3-pin debug order (SC, GND, SD); no 100 Ω target resistors; no legend. | A Debug Probe needs individual leads. | Silk legend now; reorder at the next spin. | rev-3 |
| 11 | MAJOR (firmware) | `usb_power_policy.h` builds as rev-3 unless `TAXELSCAN_BOARD_REV=4` is defined. A rev-4 build without it reads the CC status on GPIO27/29, which carry ADC_A / ADC_B here. An amplifier output at rest (~0.15 V) decodes as L/L, "3 A attached". | The harness is fed at the 632 mA limit from any port, including a 500 mA one. | `#error` when the macro is undefined. | rev-4 |
| 12 | MINOR (firmware / docs) | `firmware/rev3/PLAN.md` and the rev-1 `scan.h` still carry the rev-1 / rev-3 ADC map. rev-4 needs pins 27 / 29 = AINSEL 1 / 3 (round-robin mask 0x0A) with `adc_gpio_init` on 27 / 28 / 29. PLAN §7.1's "never call adc_gpio_init() on GPIO27 or 29" is backwards here. The SDK's default stdio UART0 (GPIO0 / 1) would drive USB_ILIM_HI and ROW_LATCH_MCU; PLAN's board header already leaves it out, so it must be used. Conversion errors (CS.ERR) are never checked. | Wrong channels, a pulled-down ADC input, a wrong current limit. | A rev-4 column in PLAN §3.1; the board header with no default UART; check CS.ERR. | rev-4 / rev-3 |
| 13 | MINOR (docs) | The README said the internal ADC gives up "3×" in resolution. RP2350 Table 1685 gives ENOB 9.0 min / 9.5 typ: 1.6–2.3 LSB rms, 8.1–8.6 noise-free bits per conversion. Against rev-3's estimated 11.9 that is 10–14× more noise per conversion before oversampling. The same converter is on rev-1, the board that has shipped. | Expectations. | Corrected in README.md today. | rev-4 |
| 14 | MINOR (docs) | The 20 Mbaud figure. The RP2350 UART tops out at UARTCLK / 16 = 9.375 Mbaud; 20 Mbit/s is the SN65HVD75's own maximum; the firmware plan (D5) runs a PIO UART at 12.5 Mbaud. | — | Corrected in ROUTING_STATUS.md and pairs_rev4.py today; the rev-3 README's bandwidth table (8 full maps at 80 fps = 1.39 MB/s) does not fit 12.5 Mbaud and needs redoing. | rev-3 |

## Microcontroller (U9, RP2354A)

- **Pins.** All 61 pins match the RP2350A QFN-60 pinout (datasheet Fig. 2,
  Tables 1674–1679).
  - The supply pins on +3.3V (3.22–3.35 V with the TLV62569's ±2 % VFB) are all
    within range: QSPI_IOVDD 2.7–3.6 V for the stacked flash, USB_OTP_VDD and
    VREG_AVDD 3.135–3.63 V, VREG_VIN 2.7–5.5 V.
  - DVDD ×3 and VREG_FB are on VCORE. VREG_PGND and the exposed pad are on GND, with
    7 vias in the pad.
- **Regulator parts** match the datasheet.
  - L1 is the polarity-marked AOTA-B201610S3R3-101-T. Its pad 1 (the dot) is on VCORE,
    so the dot is on the output end as Figs. 25 / 26 / 28 require.
  - COUT and CIN are 4.7 µF.
  - VREG_AVDD has 33 Ω + 4.7 µF and its own GND via.
  - VREG_FB is taken from C19's node.
  - In1 is cut out under L1 and the LX trace (Fig. 27).
  - The layout around them is finding 1.
- **Boot.**
  - QSPI SD0–3 and SCLK have no copper.
  - QSPI_SS has its reset pull-up plus R25 1 kΩ to SW1, as the design guide's R6 / SW1.
  - SD1's reset pull-down selects USB boot.
  - RUN has R20 10 kΩ and goes to J6.
- **Crystal.** ABM8-272-T3 (the guide's part). C20 / C21 are 15 pF C0G and R36 is 1 kΩ on
  XOUT. Load is 7.5 pF + ~3 pF stray = 10.5 pF against CL 10 pF.
- **USB.** Pin 52 → R23 → D5.1 / 6 → J5 A6 / B6 is D+; pin 51 → R24 → D5.3 / 4 →
  J5 A7 / B7 is D−. No external pulls are needed (guide §5.1).
- **GPIO functions** (Table 677).
  - GPIO8 / 9 are UART1 TX / RX.
  - GPIO1–3 can be SPI0 CSn / SCK / TX or PIO.
  - GPIO4–7 are contiguous for the mux select.
  - GPIO27 / 28 / 29 are ADC1 / 2 / 3.
  - GPIO18 / 19 are also I2C1 SDA / SCL with R32 / R33 as pull-ups (see the TUSB320 note
    below).
- **Erratum E9** (input-buffer leakage with pull-downs) affects A2 silicon only. It is
  fixed in A3, and A4 is the production stepping. The address straps rely on pull-ups,
  which E9 never affected. There is no ADC erratum in Appendix D for RP2350.

## Power and USB-C

- **Rails, pinouts and diodes.** Every IC power pin is on the right rail. Nexperia's
  pin 1 = cathode matches KiCad's pad 1, and D1 / D2 / D4 point the right way.
- **TUSB320LAI.** Configured as a GPIO-mode sink: PORT = GND, ADDR open, EN_N = GND.
  - R34 866 kΩ ±0.5 % is inside the 855–920 kΩ VBUS_DET window.
  - It presents Rd of 4.1–6.1 kΩ with no VDD (§7.3.3, §8.3.1), so a USB-C charger
    turns VBUS on for an unpowered board.
  - OUT1 / OUT2 decode as firmware expects (Table 3).
  - The LAI datasheet does not promise that OUT1 / OUT2 update when the source changes
    its Rp while attached. If they don't, strap ADDR low and poll the I2C register
    instead; GPIO18 / 19 already have the pull-ups.
- **TPS2553.** Off at reset: EN has R30 4.7 kΩ plus GPIO24's reset pull-down. Its two
  limits are 321 mA (285–363) and 632 mA (576–697). FAULT has 10 kΩ and a 5–10 ms
  deglitch, inside the firmware's 50 ms grace.
- **TLV62569.** 0.6 × (1 + 180 / 40.2) = 3.287 V. L2 is 2.2 µH (Isat 5 A); about 40 µF
  sits on the output, inside the datasheet's tested matrix. EN is tied to VIN.
  Efficiency is 93–95 % at 10–100 mA.
- **Budget.**
  - 20–34 mA per board at 3.3 V, plus ~35 mA while transmitting. That is 16–28 mA per
    board at 5 V.
  - Seven downstream boards and one transmitter draw 140–225 mA, under the 285 mA low
    limit.
  - The last board's buck input is 3.68–3.99 V from a 4.75 V source, against the
    3.30 V it needs.
  - Thermal: D4 0.22 W at 0.7 A, U14 66 mW.
- Hot plug, VBUS leakage and USB_ILIM are findings 2, 6 and 8.

## Analog chain on the internal ADC

- **Topology.**
  - SENSE_A = {R1, U5.1, U7.3}, GAIN_A = {R6, R7, U7.2}, AMP_A = {U7.1, R6, R21},
    ADC_A = {R21, C31, U9.41 = ADC1}. Bank B is the same with ADC3.
  - RAIL_MON is on ADC2.
  - The muxes' E pins are on GND; the 595s' OE is on GND and SRCLR high.
  - G = 1 + 10k / 2k = 6. No input can exceed ADC_AVDD: U7's output tops out near
    3.265 V against 3.289 V.
- **Signal.** At rest a taxel reads about 185 counts and a 50 kΩ press about 3228;
  clipping starts below 38 kΩ. That is the same full scale as rev-3. Row load is 2.1 mA
  for a fully pressed row, against the 595's −24 mA.
- **Stability and noise.**
  - U7 into 51 Ω + 1 nF has 52.5° phase margin (43.5–61° over model corners) and
    ≥ 29.8 dB gain margin.
  - The front end adds 0.13 LSB rms of noise; the ADC's own 1.6–2.3 LSB rms sets the
    floor.
- **Carry-over** between ADC1 and ADC3 on the single SAR: ≤ 0.3 LSB once the sample
    window is ≥ 200 ns, and ≤ 3.7 LSB with none. The 1 nF reservoirs do this: without
    them the kick is 176 LSB.
- **Settling** after a mux switch depends on the mat and FFC column capacitance, which
  nothing on file measures (time to 0.5 LSB, simulated):

  | column capacitance | 5 pF | 20 pF | 50 pF | 100 pF | 200 pF |
  |---|---|---|---|---|---|
  | pressed → rest | 4.1 µs | 5.0 | 6.8 | 9.6 | 15.2 |
  | saturated → rest | 4.7 | 5.7 | 7.7 | 10.8 | 16.9 |
  | pressed → open column | 5.5 | 6.6 | 8.8 | 12.3 | 18.8 |
  | max frame rate, rev-1-style firmware with the discard | 98 fps | 92 | 83 | 72 | 57 |

  The firmware plan's 6 µs settle covers column capacitance up to about 20 pF. Measure
  one column (mat + FFC), or find the settle-time knee on a real mat, before fixing
  `settleUs`.
- **Ratiometric.** The rows (ROW_VCC) and the ADC reference (ADC_AVDD) are two branches
  of +3.3 V, so slow rail droop cancels: 100 mV moves a reading by 0.04 LSB. Ripple
  cancels only below about 10 kHz: 2 % residual at 10 kHz, 16 % at 100 kHz. The
  TLV62569 runs in power-save mode at this load, so scope its ripple at bring-up.
- **Dark level.** It sits at 6 × the op-amp offset (±9.6 mV) with nothing to lift it, so
  about half the channels clip at code 0 in the dark. PLAN §7.1's "dark codes a few
  hundred counts up" test will misfire. Change the criterion, or add ≥ 2.2 MΩ from
  ROW_VCC to each sense node for a ratiometric ~85-count pedestal.
- **RAIL_MON** reads 0.3197 × +5V: 1.60 V at 5 V, 2.33 V at 7.3 V. It needs
  `adc_gpio_init`: with the pad's reset pull-down left on, its 32 kΩ source reads 22–53 %
  low.

## Digital I/O, RS-485 and connectors

- **595 chain.** ROW_DATA → U1 SER, then U1 QH′ → U2 → U3 → U4 (U4's QH′ open).
  J1 pin n is ROW_(n−1), the same as rev-1. R3 / R4 (33 Ω) sit at the MCU end of
  SRCLK / RCLK.
- **Muxes.** S0–S3 are on GPIO4–7 on both. U5 channel k goes to COL_k and U6 channel k
  to COL_16+k. HC thresholds at 3.3 V are 2.36 / 0.94 V, which the MCU's
  VOH ≥ 2.62 V / VOL ≤ 0.5 V clear.
- **RS-485.**
  - U10's DE and ~RE share GPIO10 (no echo; RO is high-impedance while sending).
  - U11's DI and ~RE are on GND and DE on GPIO13 with R37.
  - Pin 6 (A, non-inverting) is P on both.
  - Failsafe makes an idle, open or shorted pair read high, and a driven SYNC reads low
    on every board.
  - Loading is 1.2 unit loads for 8 nodes, 59.6 Ω against the 54 Ω minimum.
  - R11 / R12 are only in the `-end` CPL (104 parts on a middle board, 106 on an end
    board).
- **Connectors.**
  - J1 / J2 carry the same net on every pin as rev-1 (same FH12 part and footprint).
  - J3 / J4 are wired identically (1 +5V_BUS, 2 GND, 3 / 4 BUS P / N, 5 / 6 SYNC P / N);
    the cable is finding 3.
  - J5 matches HRO's pin table.
  - The ADDR straps are bridged by default (address 000) and need the internal
    pull-ups.
  - D3 runs at 1.4 mA.
  - SW1 (B3U-1000P) has two terminals.

## Layout, measured on the board

- **Decoupling** (`verify/layout/decap.py`, against the first re-route and the first
  rev-4 with the same placement).
  - Every RP2354A supply pin except QSPI_IOVDD (finding 4) reaches its own 0201 on F.Cu
    without a via:
    - IOVDD 1.00–1.45 mm
    - DVDD 1.15 / 1.48 / 2.70 mm
    - VREG_VIN 1.23 mm
    - VREG_AVDD 1.12 mm
    - ADC_AVDD 2.62 mm
    - USB_OTP_VDD 1.23 mm
  - Every other IC's supply pin reaches its 100 nF on F.Cu at 0.9–3.9 mm. Every pin is
    the same or better than on the first re-route.
  - Two paths go through vias:
    - VREG_FB to C19 (6.2 mm, 2 vias; a sense line, as on both earlier boards);
    - D5's VBUS pin to C38 (3.3 mm, 2 vias; forced by D5.5's via under the part).
- **Power paths** (`verify/layout/powerpath.py`; DC, tracks and vias):

  | path | first rev-4 | first re-route | now |
  |---|---|---|---|
  | +5V_BUS J4.1 → J3.1 (harness pass-through) | 119 mΩ | 115 | **101** |
  | +5V_USB J5 VBUS A9 / B9 → U14 | 4.5 / 17.5 | 13.5 / 22.6 | 13.7 / 22.7 |
  | +5V D1 / D2 → U12 VIN | 5.5 / 121 | 5.3 / 110 | 5.3 / 141 |
  | VCORE L1 → DVDD 6 / 39 | 66 / 86 | 19 / 39 | 19 / 39 |

  - Load-carrying copper is ≥ 0.30 mm on outer layers, about 1 A for a 10 °C rise.
    The one 0.15 mm +5V_USB segment is D5.5's tie, which carries no DC.
  - At the harness's 0.7 A the pass-through drops 71 mV per board.
- **Noise neighbours** (`verify/layout/aggr.py`, same-layer copper within 0.30 mm; the
  inner layers carry only the planes).
  - The sense nodes have only their own gain network beside them, plus 1.3 mm of MUX_S2
    at 0.20 mm by SENSE_B (it switches only between samples).
  - SW_NODE has nothing within 0.30 mm.
  - VREG_LX passes 0.8 mm of USB_D_P at 0.17 mm.
  - XIN runs beside XOUT_MCU for 5.3 mm at 0.12 mm, as on rev-3.
- **Reference planes** (`verify/layout/refplane.py`, share of each track over solid
  plane).
  - Over In1 GND: SENSE / GAIN / ADC / RAIL_MON / XIN / XOUT 95–100 %; the RS-485 pairs
    94–96 % on F.Cu. The gaps are via antipads.
  - VREG_LX is 6 %, by design (the cut-out).
- **Impedance** (JLC04161H-7628, 0.21 mm prepreg, εr 4.4, closed-form).
  - 0.15 mm tracks are 76 Ω single-ended.
  - The pairs are 115 Ω differential at 0.30 mm pitch and 122 Ω at 0.35 mm.
  - RS-485 wants ~120 Ω. USB's 90 Ω matters little over 24 mm at 12 Mbit/s.
- **Manufacture** (`../rev3/audit-tools/dfm_measure.py`, `dfm_jlcpcb.py` on this board).
  - Gaps and holes:
    - narrowest gap 0.100 mm (inside the escape areas); tracks ≥ 0.10 mm;
    - vias 0.30 / 0.50 and 0.30 / 0.60;
    - via holes 0.25 mm apart on the same net, 0.35 mm on different nets;
    - copper 0.27 mm from the outline centre line;
    - NPTH to copper 0.28 mm, PTH to copper 0.30 mm;
    - no via within 0.45 mm of a component hole.
  - Mask webs are 0.18 mm within a part and 0.29 mm between parts.
  - All of this is inside JLCPCB's published limits.
  - 51 vias sit wholly and 74 partly in SMD pads: order epoxy-filled, capped vias, as
    `fab/ORDER-NOTES.txt` says.

## To measure at bring-up

1. VCORE at DVDD pin 23: ripple and load-step response (finding 1); 1.05–1.16 V, with
   transients within ±100 mV.
2. U12 VIN and +5V_USB on hot plug from the stiffest supply that will be used
   (finding 2).
3. One column's capacitance (mat + FFC), then `settleUs`; raw ADC σ at rest; the
   bank-to-bank carry-over (rev-1 test 0c).
4. +5V_USB's idle voltage on a harness-powered board, and whether a laptop enumerates it
   (finding 6).
5. Whether the TUSB320LAI's OUT1 / OUT2 follow a source changing its Rp while attached.
6. SN65HVD75 DE → valid output with DE and ~RE switching together; the harness eye at
   12.5 Mbaud over the full cable.
7. The RP2354A's stepping (CHIP_ID.REVISION and the bootrom version byte); crystal
   start-up; USB enumeration.
8. In JLCPCB's placement preview: L1's dot, pin 1 of U9 / U12 / U14 / D5, D3's cathode.
