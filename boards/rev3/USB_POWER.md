# USB-C power for the sensor chain

The master receives USB data and 5 V from a computer. It can inject protected
5 V into pin 1 of both RS485 connectors. The same circuit is fitted on every
board; a board without a local USB attachment leaves its injection switch off.
The PCB outline remains **54 × 36 mm**, and connector positions are retained.

**1.5 A is a source capability, not a minimum load.** One sensor board draws
less than eight. This design accepts default USB current for a smaller chain
after enumeration, and raises its downstream limit when CC advertises more.
There is no USB PD negotiation, higher-voltage distribution, or USB data hub.

## Circuit

```
J5 VBUS (+5V_USB) ----- D2 ----------> local +5V -> U12 -> +3.3V
        |         |
        |         +--- R38 1R --- C46 10uF --- GND   (hot-plug damper)
        |
        +--- U14 current limiter --- D4 ---> +5V_BUS ---> J3/J4 pin 1
                                                  |
                                                  +--- D1 ---> local +5V
J5 CC1/CC2 ---> U13 sink/current detector ---> RP2354A power policy
J5 D+/D-/VBUS ---> D5 USBLC6-2P6 ESD array ---> GND
```

- **R38/C46 (added 21 September 2026):** a series RC across VBUS. A live cable's
  inductance into the ceramic input capacitance rings on hot plug; the LC
  estimate peaked at 6.2–7.3 V against the TLV62569's 6 V absolute maximum. The
  resistor is the point: it puts ~1 Ω of loss across the resonance, where a bare
  capacitor would only move it. It raises the attach capacitance to ~16 µF,
  above USB's 10 µF guideline; that trade was taken knowingly. Measure U12 VIN
  on hot plug (criterion 3 below) to confirm.
- **D5 USBLC6-2P6:** ESD on D+, D− and VBUS at the connector, on the connector
  side of R23/R24. The RS-485 transceivers carry their own ±12 kV IEC 61000-4-2
  protection; CC1/CC2 rely on the TUSB320's ±7 kV HBM.

- **U13 TUSB320LAIRWBR:** PORT and active-low enable are grounded; ADDR floats
  for GPIO mode. Its internal Rd terminations replace R13/R14. OUT1/OUT2 and
  their pullups use the local 3.3 V rail. R34 is in series with VBUS_DET: 866 kΩ
  ±0.5% thin film (861.7–870.3 kΩ, inside TI's 855–920 kΩ window), because no
  0402 thin-film 887 kΩ is stocked; 887 kΩ 1% thick film is the fallback. Use the
  LAI variant, which updates its current status while attached.
- **U14 TPS2553DBVR:** active-high enable, soft start, current limiting,
  thermal protection, and active-low fault reporting. R30 holds enable low at
  reset. The master powers itself through D2 before enabling the chain.
- **D4 PMEG2010ER, CFP3/SOD123W:** anode at U14 OUT, cathode at +5V_BUS.
  It provides a continuous reverse-current barrier. U14's reverse comparator
  alone has a millisecond delay. Schottky leakage remains finite.
- **R27/R28/Q1:** R27 is always connected to ground. Q1 adds R28 in parallel
  for the higher limit. Both resistors are 82.5 kΩ, 1%; Q1 is a 2N7002PW in
  SOT323. R29 holds the selection low at reset. R29/R30 are 4.7 kΩ.
- **C37 = 1 µF; C22 = 4.7 µF:** reduce the former 10 µF + 22 µF USB-side
  charging load. These changes do not establish attach-inrush compliance:
  the buck's startup load and its output capacitors must also be measured.

The TPS2553 DBV pin map is IN=1, GND=2, EN=3, FAULT=4, ILIM=5, OUT=6.
The WSON/DRV version has a different pin map and must not be substituted.

## Available current

These are **downstream branch** limits; the master's local current is additional.

| Condition | Branch setting | Nominal limit | Calculated min–max, including 1% resistors |
|---|---|---:|---:|
| No attachment, reset, default-current USB not configured, suspend, latched fault | Off | 0 | Leakage only |
| Default CC advertisement, successful 500 mA USB 2.0 configuration | Low | 321 mA | 282–367 mA |
| CC advertises 1.5 A or 3 A, not suspended | High | 632 mA | 570–703 mA |

Bounds use the TPS2553 datasheet's equations, R in kΩ and I in mA:
`Imin=25230/R^1.016`, `Inom=23950/R^0.977`, `Imax=22980/R^0.94`.
Apply +1% R to Imin and −1% R to Imax. High mode uses 41.25 kΩ nominal.
Q1's resistance is negligible relative to 82.5 kΩ; validate the limit on hardware.

Reserve **100 mA maximum for the master**, including its startup and all local
loads. Then the calculated low-mode maximum is below 467 mA total. This is a
design allocation, not a measured board rating. If the master exceeds it,
reduce the branch budget or revise the local power architecture.

For seven downstream boards, low mode guarantees a current-limit threshold of
about 40 mA per remote board if their loads were equal; high mode gives about
81 mA. Startup and transmitter peaks count as well as average consumption.
Eight boards on a default-current computer port are therefore **conditional
on measurement**. A 1.5 A or 3 A Type-C source offers more margin. A USB 3.x
computer port does not automatically grant this USB 2.0 device 900 mA.

The branch maximum stays below the JST GH connector's 1 A rating. Use properly
crimped **26 AWG** power/ground conductors; GH is specified for 26–30 AWG.
One USB supply per chain is the intended operating arrangement. The circuit
does not implement current sharing between multiple computers or supplies.
External harness power must be separately limited to the connector/cable rating.

## Firmware

The implementation is in `firmware/rev3_power/`. GPIO assignments:

| GPIO | Net | Function |
|---:|---|---|
| 24 | USB_BUS_EN | High enables downstream power |
| 0 | USB_ILIM_HI | High selects the larger current limit |
| 27 | USB_CC_OUT1 | U13 current-status bit 1 |
| 29 | USB_CC_OUT2 | U13 current-status bit 2 |
| 23 | USB_PWR_FAULT | Active-low U14 fault |

These moved on 10 September 2026 (they were GPIO 12–15). All four sat on the
RP2354A's bottom row, which also carries the crystal, SWD, RUN, the SPI to the
ADC and three supply pins; at 0.4 mm pitch that row could not be escaped with
every pin used. GPIO 24, 27 and 29 are on the right edge and GPIO 0 on the
left, where pins were free. `gen_rev3.py` asserts the map and
`firmware/rev3_power/usb_power_policy.h` carries the same numbers.

The Pico SDK adapter reads the actual TinyUSB configuration descriptor and
requires a configured 500 mA request for low mode. A stock 100/250 mA descriptor
does not qualify. Serial-port DTR is not used as an enumeration signal. A 1 ms
timer samples CC and controls the switch. Increases qualify for 5 ms; decreases
and suspend revoke the budget immediately on the next service call.

An initial 50 ms fault grace period allows charging the chain's capacitors;
U14 limits current throughout. A persistent fault then latches the branch off
until an explicit retry or a physical USB detach. This avoids repeated
power-cycling of an overloaded sensor chain.

The existing `firmware/taxelscan` application targets rev1. This power module
is a component for the rev3 firmware port, not a completed ADC/RS485/USB scan
application. See its README for integration and native test instructions.

## Bring-up criteria

1. Check that every injection switch remains off at MCU reset and on bus-only
   powered boards. Confirm both cable orientations and default/1.5 A/3 A sources.
2. Measure the master before USB configuration: respect the USB 2.0 100 mA
   allocation. Measure attach inrush, suspend current, and USB reset behavior.
   The switch controls the downstream branch; the master's own low-power
   suspend behavior remains the responsibility of the rev3 application.
3. Measure one through eight boards at the intended scan rate, including cold
   start, line transmission, and hot plug. Exercise a persistent overload and
   confirm fault latching. Verify CC downgrades while streaming.
4. Measure voltage at the last board and both sides of D1/D4 with the longest
   intended harness. The last regulator must retain enough input headroom to
   maintain 3.3 V; the buck's 2.5 V operating minimum does not imply that it can
   produce 3.3 V from 2.5 V.
5. Check U14/D4 temperatures and the power/ground path at maximum load and
   ambient temperature. Check backfeed leakage with the harness powered and
   USB disconnected or the computer off.

Schematic/netlist/DRC checks verify the design files. They cannot establish
these electrical results or certify USB compliance. Existing fabrication
outputs predate this change and must not be used for the revised circuit.

## Component references

- [TI TUSB320LAI datasheet](https://www.ti.com/lit/ds/symlink/tusb320lai.pdf)
- [TI TPS2553 datasheet](https://www.ti.com/lit/ds/symlink/tps2553.pdf)
- [TI TLV62569 datasheet](https://www.ti.com/lit/ds/symlink/tlv62569.pdf)
- [Nexperia PMEG2010ER datasheet](https://assets.nexperia.com/documents/data-sheet/PMEG2010ER.pdf)
- [Nexperia 2N7002PW](https://www.nexperia.com/product/2N7002PW)
- [JST GH series specification](https://www.jst-mfg.com/product/pdf/eng/eGH.pdf)
- [TinyUSB device API](https://github.com/hathach/tinyusb/blob/master/src/device/usbd.h)
