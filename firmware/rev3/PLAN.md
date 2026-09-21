# rev-3 firmware — implementation plan

**Status: nothing of this is written yet.** This document is the specification
and the working order for it. It is meant to be picked up cold, in a new
session, with no memory of the conversations that produced the board.

There is no rev-3 acquisition firmware today. `firmware/taxelscan/` is the
rev-1 sketch for a XIAO RP2350 module and **must never be flashed to a rev-3
board** — it drives GPIO22 and GPIO23, which on rev-3 are `ADDR2` (strapped to
ground through a closed solder jumper) and the open-drain `USB_PWR_FAULT`. It
reads the RP2350's internal ADC on GPIO26/27; rev-3 has an external 16-bit
converter on SPI0 and carries `USB_CC_OUT1` on GPIO27.

What *is* reusable is most of the value: the conditioning pipeline, the frame
format discipline, the simulator, the host tools, and the USB power controller.
This plan is mostly about the new hardware layer underneath them and the
multi-board layer around them.

---

## 1. Scope

Build the firmware that turns a chain of rev-3 boards into one instrument:

- each board scans a 32 × 32 taxel mat through 4 × SN74LVC595A row drivers,
  2 × CD74HC4067 muxes, a TLV9062 ×6 gain stage and an LTC1865L 16-bit SPI ADC;
- all boards sample **the same row at the same instant**, because every board
  starts its frame on one broadcast edge on the RS-485 SYNC pair;
- one board is the master: it generates that edge, polls the others over the
  RS-485 data pair, and forwards everything to a host over USB CDC;
- the master also owns the harness power switch (`firmware/rev3_power/`);
- the conditioning pipeline (`firmware/taxelscan/condition.cpp`) runs on every
  board, unchanged, so contacts are extracted at the sensor.

**Out of scope for the first pass:** firmware update over the bus, host-side
fusion across mats, any change to the conditioning algorithm itself.

**Definition of done for the whole project:** eight boards on a 4 m harness,
1024 taxels each, streaming to a host at 60 Hz with zero CRC errors and zero
frame drops over a one-hour soak, with the harness powered from the master's
USB-C.

---

## 2. Working agreements

These are the owner's standing rules, and they apply to this work too.

1. **Experiments happen on copies.** Keep a known-good build; a board that
   streams is worth more than a board that nearly does something better.
2. **Back up before replacing anything that works**, including flashed images —
   keep the last good `.uf2`/`.elf` with its git hash.
3. **Do not commit or push without being asked.** Nothing in this repo has been
   committed for the rev-3 work so far; that is deliberate.
4. **Measure, don't assert.** Every number in this plan that is marked
   *(predicted)* is arithmetic, not a measurement. The house style is that
   claims in docs are things that were checked; keep it that way.
5. **Confirm before anything outward-facing** (ordering, publishing, sending).

---

## 3. Hardware facts

Authoritative sources, in this order: `boards/rev3/gen_rev3.py` (the netlist
generator — pin numbers are looked up by name, never typed), `boards/rev3/rev3.net`,
`boards/rev3/README.md`, `boards/rev3/AUDIT.md`, `boards/rev3/USB_POWER.md`.
If this section and those files disagree, **they are right**.

### 3.1 GPIO map

`gen_rev3.py` keeps rev-1's map on purpose, so the row and mux lines port
across unchanged. `MUX_S0..S3` must stay contiguous and in order: the scan
writes all four with a single store.

| Signal | GPIO | Direction | Notes |
|---|---:|---|---|
| `USB_ILIM_HI` | 0 | out | high = 632 mA harness limit (Q1 switches R28 in) |
| `ROW_LATCH_MCU` | 1 | out | 595 RCLK, 33 Ω series (R4) |
| `ROW_CLK_MCU` | 2 | out | 595 SRCLK, 33 Ω series (R3) |
| `ROW_DATA` | 3 | out | 595 SER |
| `MUX_S0..S3` | 4,5,6,7 | out | both muxes; one store |
| `BUS_DI` | 8 | out | U10 DI — RS-485 data TX (UART1 TX in F2, **used by PIO instead**) |
| `BUS_RO` | 9 | in | U10 RO — RS-485 data RX; **floats while this board transmits** |
| `BUS_DE` | 10 | out | U10 DE **and** ~RE tied together; high = drive |
| `SYNC_OUT` | 11 | in | U11 RO — frame-start, falling edge |
| `SYNC_DE` | 13 | out (master only) | U11 DE; R37 10 kΩ pull-down |
| `ADC_SDO` | 16 | in | SPI0 RX ← LTC1865L SDO |
| `ADC_CONV` | 17 | out | LTC1865L CONV — **GPIO, not hardware CS** (§7.1) |
| `ADC_SCK` | 18 | out | SPI0 SCK |
| `ADC_SDI` | 19 | out | SPI0 TX → LTC1865L SDI |
| `ADDR0..2` | 20,21,22 | in, pull-up | solder straps; **closed = 0** |
| `USB_PWR_FAULT` | 23 | in | active low, R31 pull-up, open drain from U14 |
| `USB_BUS_EN` | 24 | out | high enables the harness feed; R30 pull-down |
| `STATUS` | 25 | out | R17 1 kΩ → green LED to GND, **active high** |
| `USB_CC_OUT1` | 27 | in | TUSB320LAI, open drain, R32 pull-up |
| `RAIL_MON` | 28 | ADC2 | +5 V through R15 100 k / R16 47 k, C45 |
| `USB_CC_OUT2` | 29 | in | TUSB320LAI, open drain, R33 pull-up |

Free and unrouted: **GPIO12, 14, 15, 26**. Leave them as inputs with the reset
pull-down; do not call `adc_gpio_init()` on 26.

`firmware/rev3_power/usb_power_policy.h` already carries 0, 23, 24, 27, 29 and
asserts them against `gen_rev3.py`. Do not re-type them; include that header.

### 3.2 Analog chain

    mat column ──> CD74HC4067 (U5 bank A / U6 bank B)
                    └─> R1/R2 10 kΩ pulldown ──> TLV9062 non-inverting, G = 1 + R6/R7 = 6
                          └─> R21/R22 51 Ω ──> C31/C32 1 nF C0G ──> LTC1865L CH0/CH1

- Rows are driven from `ROW_VCC` (= +3.3 V through the R5 0 Ω link).
  **Unselected rows are driven LOW**, which is the design's best property: every
  sneak path terminates at ~25 Ω to ground. Never tri-state the 595s.
- `VREF` is tapped off `ROW_VCC` through R10/C10, so the excitation and the
  reference move together and the rail cancels out of the reading. There is no
  rail-sag correction to do, and `RAIL_MON` is health telemetry only.
- Sense node at rest (1 MΩ sensor): ~25 mV → ~150 mV at the ADC.
  Pressed (50 kΩ): ~435 mV → ~2.6 V. Predicted settle to 16 bits ≈ 5.9 µs with
  ~70 pF at the mux common — that is where `cfg.settleUs` starts, and §9 says
  how to measure the real knee.
- Mux off-state leakage is up to ±8 µA hot (not the ±1 µA in older notes). At
  the 7.6 kΩ effective node that is tens of mV, static — which is exactly what
  the per-frame dark reference removes.

### 3.3 Converter — LTC1865L (U8, MSOP-10)

From the datasheet (cached text used while writing this: dual 16-bit SAR,
150 ksps, V<sub>REF</sub> on its own pin):

| Parameter | Value |
|---|---|
| f<sub>SCK</sub> max | **8 MHz** |
| t<sub>CONV</sub> | 3.7 µs typ, **4.66 µs max** |
| f<sub>SMPL</sub> max | 150 kHz |
| Data transfer | 16 SCK with CONV low |
| t<sub>SMPL</sub> (acquisition) | 14 SCK for the LTC1865L, after the 2 config bits |
| t<sub>suCONV</sub> | 60 ns (CONV↓ before first SCK↑) |
| t<sub>hCONV</sub> | 26 ns (CONV low after last SCK↑) |
| SPI mode | 0 — SDO changes on SCK↓, both ends capture on SCK↑ |

Operating sequence, and the one thing to internalise: **the transfer returns the
*previous* conversion and configures *and samples* the next one.**

```
CONV ──┐ convert (≥ t_CONV) ┌── convert ──┐
       └─ 16 SCK: read B15..B0 of conversion N-1,
          shift in 2 bits (S/D, O/S) selecting the channel for conversion N,
          then the input is SAMPLED for the rest of the CONV-low window ──┘
```

Channel select (single-ended, Table 1): `S/D = 1`, `O/S = 0` → CH0 (bank A);
`O/S = 1` → CH1 (bank B). The two bits are the first two bits of the 16-bit
word you transmit; the rest are don't-care.

### 3.4 Bus

RS-485, half duplex, multidrop, 6-wire harness (J3/J4 wired identically, so
power and bus pass through a board): +5 V, GND, DATA±, SYNC±.

- **Data pair**, U10 (SN65HVD75, rated 20 Mbps): DE and ~RE tied to GPIO10, so
  one pin turns the driver around and a firmware slip cannot leave the driver
  enabled while listening.
- **Sync pair**, U11: `DI` and `~RE` strapped to GND — a driven pair can only
  ever pull SYNC low, and every board (the master included) always listens.
  `DE` is on GPIO13 with R37 holding it low, so a board in reset, a blank board
  and every slave are receive-only.
- Termination R11/R12 is fitted **only on the two end boards** (`-end` variant
  in `boards/rev3/fab/`). The line is failsafe-high when idle.
- Address: JP1–JP3 strap ADDR0..2 to ground; **boards ship with all three
  closed**, so every board is address 0 until two bridges are cut. The MCU must
  enable its internal pull-ups.

### 3.5 Power and USB

`boards/rev3/USB_POWER.md` is the contract; `firmware/rev3_power/` implements
the policy and has native tests covering all 32 CC/grant/suspend combinations.
Key points for firmware: the USB configuration descriptor must request
**500 mA**, `tud_task()` must be serviced at least every millisecond, the
policy's tick is a 1 ms timer on core 0, and the downstream feed defaults OFF
in hardware (R30).

---

## 4. What already exists

| Reuse | Where | How |
|---|---|---|
| Conditioning pipeline (10 stages, contacts, baseline guarantees) | `firmware/taxelscan/condition.cpp/.h` | **compile unmodified** |
| Geometry, `Config`, `MAX_*`, pin constants, core-1 contract | `firmware/taxelscan/scan.h` | include as-is; rev-3 supplies the *implementations* |
| Frame discipline (header CRC before length, payload CRC, `FrameTrailer`) | `firmware/taxelscan/protocol.h/.cpp` | v3 extends it (§7.4) |
| Native conditioning sim + golden digest | `firmware/sim/` | add a rev-3 profile; the digest stays the regression |
| Wire-format tests | `firmware/tools/test_protocol.py` | extend to v3 **before** writing the emitter |
| Live viewer, soak logging, guided procedures | `firmware/tools/taxelscan_live.py` | extend to multi-board |
| Interference survey | `firmware/tools/spectrum.py` | re-point at the LTC1865L sampler |
| USB chain power policy + Pico adapter | `firmware/rev3_power/` | link into the master build |

`scan.h`'s pin constants are already right for rev-3 — `PIN_ROW_DATA` 3,
`PIN_ROW_CLK` 2, `PIN_ROW_LATCH` 1, `PIN_MUX_S0` 4, `PIN_ADC_RAIL` 28 — because
the board deliberately kept rev-1's map. Only `PIN_ADC_A`/`PIN_ADC_B` (26/27)
are meaningless here and must not be used.

---

## 5. Decisions already taken

These were argued out while the board was being finished. Each is written with
its reason so a later session can overturn it on evidence rather than by taste.

### D1 — Build with the Pico SDK (CMake), not the Arduino core

**Recommended.** The rev-1 sketch is Arduino (`arduino-cli`, earlephilhower
core 6.0.0, which is installed on this machine and bundles pico-sdk 2.3,
arm-none-eabi gcc, `pioasm`, `picotool` and OpenOCD). rev-3 should still move:

1. `firmware/rev3_power/usb_power_pico.cpp` reads the **TinyUSB configuration
   descriptor** and requires a configured 500 mA request. The Arduino core owns
   that descriptor and sets it to 250 mA (`generic_rp2350.build.usbpwr=
   -DUSBD_MAX_POWER_MA=250` in `boards.txt`), so the default-current branch
   would silently never enable and the harness would stay dark on an ordinary
   PC port. It is overridable with a `--build-property`, but the firmware then
   depends on an invisible flag.
2. The master must forward up to ~17 kB per frame over USB. On rev-1 the
   Arduino CDC path cost **1.28 ms per 1.1 kB frame** (`emitUs`, measured, see
   `firmware/README.md`). The SDK lets the TX buffer, the flush policy and
   `tud_task()` cadence be set directly, and leaves a vendor-bulk interface
   open if CDC does not reach the rate.
3. PIO + DMA, a board header (2 MB flash, 12 MHz crystal, **no default UART on
   GPIO0/1**), and `pico_generate_pio_header()` are all first-class.

Cost: CMake and Ninja are not installed (arm-none-eabi-gcc, pioasm, picotool
and the SDK source are, inside the Arduino core). **Installing them needs the
owner's go-ahead** — that is step M0.1.

*Fallback if that is refused:* build with `arduino-cli` against
`rp2040:rp2040:generic_rp2350`, add
`--build-property "build.usbpwr=-DUSBD_MAX_POWER_MA=500"`, and call the SDK's
PIO/DMA/SPI APIs directly from the sketch (they are all available). Everything
else in this plan is unaffected.

### D2 — Count domain: `(code16 − dark) >> 4`

The conditioning pipeline is written in **counts**, and rev-1's count was one
12-bit LSB (806 µV at the ADC). Feeding raw 16-bit codes in would break it in
two places: `drFrame` is `int16_t` and a hard press reads ~52 000 codes, and
`varEma[]` is `uint16_t` in Q4, whose headroom is *exactly* 4095 counts.

So the scan layer right-shifts by `cfg.adcShift`, default **4**:

- 1 count = 16 LSB = 806 µV at the ADC = **134 µV at the sense node** (the ×6
  gain makes it 6× finer than rev-1 at the node, which is the point of the
  upgrade);
- full scale = 4095 counts, so every threshold, rate and cap in `CondCfg` keeps
  its rev-1 meaning and the simulator's golden digest stays the regression;
- the converter's bits below 1 count are not wasted: oversampling sums in the
  16-bit domain *before* the shift, so `o ovs 4` buys real sub-count averaging.
- the noise floor is predicted at ~17 LSB p-p (≈11.9 noise-free bits at 80 fps),
  i.e. about **1 count** after the shift — measure it with `n 30` and compare.

`adcShift` is a runtime parameter. `adcShift 1` gives a 15-bit diagnostic mode;
in that mode `varEma` saturates and the stuck-release heuristics degrade, so it
is for bench work, not for service. Say so in the console help.

### D3 — Role: master = address 0 **and** USB configured

Every board is identical, and boards ship at address 0, so the role rule has to
be safe when two address-0 boards share a harness.

- `addr != 0` → **slave**. Never drives SYNC_DE, never initiates bus traffic.
- `addr == 0` + USB configured → **master**, after a listen-before-talk check.
- `addr == 0`, no USB → **standalone**: scans, conditions, drives the LED,
  answers nothing. (This is also the single-board bring-up mode.)
- Before driving anything, a would-be master listens for 3 frame periods. Any
  SYNC edge it did not generate, or any bus traffic, means another master:
  refuse the role, latch a `MASTER_CONFLICT` error, blink it, say it on the
  console. Keep watching for foreign SYNC edges while running.

### D4 — Frame start is the hardware SYNC edge, generated by PWM

The master raises `SYNC_DE` for a 100 µs pulse once per frame; all boards take
the **falling** edge on `SYNC_OUT` (the pair idles high, and a driven pair can
only pull low). GPIO13 is **PWM6B**, so the pulse train can come straight out of
the PWM hardware with no CPU jitter and no ISR to miss: wrap = frame period,
level = 100 µs. The master receives its own edge through U11 exactly as everyone
else does, so its own scan has the same delay as theirs.

Qualify the edge: re-read the pin ~20 µs after the interrupt and require it
still low. A 4 m harness in a robot will see transients; a spurious frame start
is a scan overrun and a shifted map.

### D5 — The bus runs on a PIO UART at 12.5 Mbaud, with DMA

The hardware UART tops out at clk_peri/16 = **9.375 Mbaud** (clk_peri is
150 MHz on RP2350), and seven slaves × 2.2 kB × 60 Hz × 10 bits = **9.16 Mbit/s**
of payload alone. It does not fit, and that is what the SYNC hardware fix and
this decision are for.

PIO at 150 MHz with an **integer** clock divider (no fractional jitter):

| cycles/bit | divider | baud | 7 slaves, full 16-bit maps @60 Hz *(predicted)* |
|---:|---:|---:|---|
| 12 | 1 | **12.5 Mbaud** | 12.2 ms payload + 1.1 ms overhead = 80 % of the period |
| 10 | 1 | 15 Mbaud | 10.2 + 1.1 = 68 % |
| 8 | 1 | 18.75 Mbaud | 8.2 + 1.1 = 56 % |

Start at **3 Mbaud** for bring-up (a rate the hardware UART can also do, so a
USB-serial adapter can watch the wire), then 12.5, then raise it only if a BER
soak justifies it. The RS-485 rule of thumb (baud × metres ≤ 10⁸) allows 4 m at
all three; the SN65HVD75 is rated 20 Mbps; the harness is *not* controlled
impedance, so the wire, not the silicon, sets the ceiling.

`DE` is driven **by the PIO program** (side-set), not by software, so it rises
before the start bit and falls one bit-time after the last stop bit.

### D6 — Map payload formats are host-selectable per board

Full 16-bit maps from eight boards at 60 Hz is **1.05 MB/s** into a USB
full-speed CDC endpoint whose practical ceiling is around 1 MB/s. That is the
real bottleneck in the system — not the bus, not the scan. So the map format is
a per-board setting the host can change at run time:

Sizes below are one whole v3 frame — 30 B header, map, 4 contacts, 28 B trailer,
2 B CRC — times eight boards at 60 Hz.

| id | format | frame bytes | 8 boards @ 60 Hz *(predicted)* |
|---:|---|---:|---|
| 0 | `NONE` — contacts + telemetry only | 140 | 67 kB/s |
| 1 | `I16` — int16, row major | 2188 | 1.05 MB/s |
| 2 | `I12` — signed 12-bit packed, `>> mapShift` | 1676 | 804 kB/s |
| 3 | `I8` — int8, `>> mapShift` | 1164 | 559 kB/s |
| 4 | `SPARSE` — count + (index, value) pairs | 140 + 4 × active | load dependent |

The count domain is 0..4095 (D2) and the map is **signed**, so 12 bits do not
hold it unshifted: `I12` defaults to `mapShift 1` (±4094 counts at 2-count
resolution, just under the ~1 count noise floor), and `mapShift 0` clips
anything above 2047. `I8` defaults to `mapShift 5`. The header carries the
shift, so the host reconstructs `value ≈ v << mapShift` without being told.

**`I12` is the default for a full chain**; `I16` is for one to six boards or for
diagnostics. M1 measures the real USB ceiling before any of this is built on
top of.

---

## 6. Architecture

```
            SYNC pair (broadcast, master pulls low)      DATA pair (polled)
   ┌───────────────┴───────────────┬───────────────┬───────────────┐
master (addr 0)              slave 1          slave 2   …     slave 7
   │                              │                │               │
   ├─ core 1: scan on the SYNC edge ─────────────── same on every board ──
   ├─ core 0: condition → build frame v3 → hold it ready for the poll
   ├─ core 0: PWM SYNC generator, poll scheduler, USB CDC to host
   └─ core 0: USB power policy (1 ms timer), console, LED, watchdog
```

**Core split** (inherited from rev-1, and it is the reason the dwell timing
holds): core 1 owns the matrix and nothing else; core 0 does everything that can
block. Anything on core 0 that touches the matrix calls `scanPause()` first.

**One frame period at 60 Hz (16 667 µs), predicted:**

```
 t=0      SYNC falling edge on every board
 t≈0.1    core 1 begins: dark sweep, 32 row walk, dark sweep      ~11.2 ms
 t≈11.3   core 1 publishes frame N; core 0 conditions it          ~3.5 ms
 t≈14.8   core 0 packs frame N (v3) into the ready buffer, arms TX DMA
 t≈16.7   SYNC N+1 … and the master polls slaves for frame N during period N+1
```

The poll cycle is deliberately one period behind the scan it reports. There is
no room to do both in one period at 60 Hz with full maps, and pretending
otherwise is how a system ends up dropping frames under load. The header
carries `syncId`, so the host can align mats exactly regardless of when the
bytes arrived. End-to-end latency is then ~1.3 frame periods (~22 ms) plus USB.

---

## 7. Detailed design

### 7.1 Scan layer (`scan3.cpp`) — implements the `scan.h` contract

Implement `scanInit`, `scanResetDark`, `selectRowMask`, `sampleBank`,
`scanFrame`, `scanPause/Resume`, `scanCopyLatest`, `scanCore1Loop` with rev-3
hardware behind them. Keep the published behaviour identical — two buffers, a
mutex on the index swap, `frameSeq`, `telem` — because core 0's code and the
host both depend on it.

**Boot order matters.** The 595 outputs are undefined at power-up, so before
anything else: drive `ROW_LATCH` high, `ROW_CLK` low, then clock 32 zeros and
latch. Only then is it safe to assume no row is driving the mat.

**Row shift.** Bit 31 first, exactly as rev-1 (`selectRowMask`), with the
four-`nop` spacing for the 33 Ω series resistors. Do **not** use SPI for this —
not because of rev-1's pin conflict (that was the XIAO's MISO on GPIO4), but
because SPI0 is now the ADC's and bit-banging costs ~3 µs per row against an
11 ms frame.

**Which physical row is bit *n*, and which column is channel *k*, is not
assumed.** The board was re-placed; verify with the `e` (row output walk) and
`y` (connectivity) diagnostics on the first assembled board and record the
answer in `firmware/rev3/README.md`.

**ADC transfer.** SPI0 in mode 0, 16-bit frames, 7.5 MHz (`spi_set_baudrate`
picks 150 MHz / (2 × 10); the next step up is 8.33 MHz, over the part's 8 MHz
limit). `CONV` is a plain SIO output on GPIO17, **not** the PL022's CSn: the
PL022 pulses CSn for one SCK period between frames, and this part needs CONV
held high for ≥ 4.66 µs.

```c
// Returns conversion N-1 and starts conversion N on channel `ch`.
static inline uint16_t adcXfer(uint8_t ch) {       // ch: 0 = bank A, 1 = bank B
    gpio_put(PIN_ADC_CONV, 0);                     // t_suCONV 60 ns before SCK
    uint16_t tx = (uint16_t)(0x8000 | (ch ? 0x4000 : 0));  // S/D = 1, O/S = ch
    uint16_t rx;
    spi_write16_read16_blocking(spi0, &tx, &rx, 1);        // 16 SCK ≈ 2.13 µs
    busy_wait_us(cfg.acqUs);                       // extra acquisition, default 0
    gpio_put(PIN_ADC_CONV, 1);                     // sample held, conversion starts
    busy_wait_us_32(cfg.convUs);                   // ≥ t_CONV, default 5
    return rx;
}
```

Consequences to design around, not to discover on the bench:

- The result lags the request by one transfer. Keep a "where does the next
  result go" pointer; the first transfer after any pause returns junk — discard
  it (`cfg.adcDiscard` keeps its rev-1 meaning: one throwaway conversion).
- The mux must be settled **before** the transfer that selects its bank, because
  the sample window is inside that transfer. Order per channel:
  `selectChan(k)` → `settleUs` → `adcXfer(A)` → `adcXfer(B)` → …
- Bank A ↔ B carryover is a real risk (one S/H behind a 2:1 mux). Run rev-1's
  test 0c equivalent: press a taxel on bank A hard and watch its partner at
  `chans + k` on a *disconnected* bank B column. If it moves, add a discard
  conversion per bank change and pay the 7.1 µs.
- The op-amp cannot swing to 0 V. Dark-reference codes should sit a few hundred
  counts above zero; any channel reading ~0 in the dark sweep means the output
  is clipping and the dark subtraction for that channel is an estimate. Check
  it during bring-up and record it.

**Per-frame structure** stays rev-1's: dark sweep → 32 row walk → dark sweep →
per-channel dark EMA (`cfg.darkShift`) → `drFrame = clamp((raw − dark) >> cfg.adcShift)`.
The dark reference is the technique that makes this sensor need no
recalibration pause; do not make it optional in service, only in diagnostics.

**Telemetry** per frame: `periodUs`, `scanUs`, `dieTempRaw` (internal ADC input
4 on the RP2350A package), `railRaw` (ADC2). `+5 V = railRaw × (3.3/4096) ×
(147/47)`; ~1984 counts at 5.00 V. Only the internal ADC reads these — and
**never call `adc_gpio_init()` on GPIO27 or 29**, which would disable the digital
input buffers the CC status bits arrive on.

**New `Config` fields** (additive; rev-1 and the sim ignore them):
`adcShift` (4), `convUs` (5), `acqUs` (0), `busBaud`, `mapFormat`, `mapShift`,
`syncTimeoutFrames` (3).

### 7.2 Conditioning integration

Compile `firmware/taxelscan/condition.cpp` **unmodified**. It needs two shims,
exactly as `firmware/sim/shim/` does for the native build:

- `compat/Arduino.h` — `micros()`, `millis()`, `delayMicroseconds()`,
  `constrain()`, `byte`, and an `rp2040`-shaped object whose
  `idleOtherCore()/resumeOtherCore()` park core 1 around a flash write;
- `compat/EEPROM.h` — the same `begin/get/put/commit` surface over the last
  4 kB flash sector, using `flash_safe_execute()` (core 1 must have called
  `flash_safe_execute_core_init()`). A commit stalls XIP for tens of ms, so it
  only ever happens on an explicit command, with the scan parked.

The regression that this stayed honest is `cd firmware/sim && make regress`
(and `make multi`): byte-identical digests. Run it after any touch of the
shared headers, including adding `Config` fields.

**Parameter rescaling.** D2 keeps the numeric domain, but the *sensor* changed
(1 MΩ CNT film, not rev-1's mat) so the defaults in `CondCfg` are starting
points, not settings. Add a `rev3` profile to `firmware/sim/simtest.cpp` — rest
level, contact amplitude and noise at rev-3 scale — and re-run checks A–G
against it before trusting any threshold on hardware. Then on the bench:
`n 30` for sigma, a one-hour soak with nothing on the mat for false positives,
and `p r c` for the settle knee.

### 7.3 Board identity and role (`main.cpp`)

At boot, in this order:

1. Park the matrix (32 zeros into the 595s, all mux lines low).
2. Read the straps: pull-ups on GPIO20–22, wait 1 ms, `addr = (g22<<2)|(g21<<1)|g20`.
3. Bring up USB (TinyUSB, one configuration, **500 mA**), then
   `taxelscan_usb_power_init()`.
4. Decide the role per D3, including the listen-before-talk window.
5. Start core 1 (`scanCore1Loop`), enable the SYNC edge IRQ **on core 1**, so
   the frame start is not queued behind USB work. (The SDK's GPIO callback is
   per-core; register it from core 1.)
6. Enable the watchdog (~250 ms) fed by core 0 only after it has seen core 1's
   heartbeat advance. A watchdog reset returns GPIOs to reset state, where R30
   holds the harness feed off and R37 holds SYNC_DE low — which is the right
   failure direction.

### 7.4 Frame format v3

v2 is `firmware/taxelscan/protocol.h` and stays as it is for rev-1. v3 adds what
a chain needs: which board, which sync pulse, and how the map is packed.

```
 off size field
  0   2  'F','T'
  2   1  version = 3
  3   1  flags
  4   2  seq            u16  per-board frame counter
  6   1  rows
  7   1  cols
  8   2  payloadLen     u16  bytes after the header CRC, excluding the payload CRC
 10   4  periodUs       u32  measured
 14   2  dieTempRaw     u16
 16   2  railRaw        u16  RAIL_MON, +5 V at this board
 18   1  nContacts
 19   1  nRejected
 20   1  boardAddr      0..7
 21   1  mapFormat      0 NONE / 1 I16 / 2 I12 / 3 I8 / 4 SPARSE
 22   1  mapShift       extra right shift applied to map values
 23   1  reserved       0
 24   4  syncId         u32  the SYNC pulse this scan started on
 28   2  header CRC16   over bytes 0..27
 30   .  payload: map, then contacts (20 B each), then FrameTrailer (28 B)
        payload CRC16   u16
```

Flags keep v2's meanings (`GATED` 0x01, `COND` 0x02, `DARKREF` 0x04, `SIGMA`
0x08, `TARE_SUSPECT` 0x10, `RAW` 0x20) and add **`NOSYNC` 0x40** (free-running,
not locked to the harness) and **`STALE` 0x80** (this frame was already served
for an earlier poll, or the scan overran).

Rules that are not negotiable, because this project has already paid for them:
the header carries its own CRC and it is verified **before** `payloadLen` is
trusted; samples stay **signed**; a reader walks frame to frame by the verified
length rather than hunting for the magic.

`I12` packing, two values per three bytes, values clamped to [−2048, 2047]:

```
b0 =  v0        & 0xFF
b1 = (v0 >> 8)  & 0x0F | (v1 & 0x0F) << 4
b2 = (v1 >> 4)  & 0xFF
```

**Write the packers and the parser tests first.** `firmware/tools/test_protocol.py`
is the format's specification as much as it is a test; extend it for v3 and all
five map formats, including the four failure modes it already covers, before
the firmware emits a single v3 byte.

### 7.5 SYNC (`sync.cpp`)

*Master:* PWM slice 6, channel B on GPIO13. `wrap` = frame period, `level` =
100 µs. Count pulses in the receive path, not the transmit path (see below), so
the master's `syncId` is derived the same way as everyone else's.

*Every board:* falling-edge IRQ on GPIO11, on core 1. The handler stamps
`time_us_64()`, re-reads the pin after ~20 µs to qualify it, increments the
local pulse counter and releases core 1's frame loop. Core 1 starts the scan at
`edge + cfg.syncDelayUs` (default 0) so a deliberate stagger is available if
bus traffic turns out to couple into the analog front end (§10, risk R3).

*Loss of sync:* after `syncTimeoutFrames` (3) missed edges a slave free-runs on
its internal deadline, sets `FF_NOSYNC`, and blinks it. It must never simply
stop producing frames — a silent sensor looks like a dead sensor.

*Aligning `syncId` across boards:* every poll request carries the master's
current pulse count. A slave sets `offset = masterSyncId − localCount` on the
first poll after boot and whenever the two disagree by more than one. The master
never sends a request within ±500 µs of an edge, which is what makes that
comparison unambiguous.

### 7.6 Bus transport (`bus_pio.cpp`) and link layer (`bus_proto.cpp`)

**Split hardware from protocol.** `bus_proto.cpp` holds the framing, the address
filter, the master's poll scheduler and the slave's response state machine, with
the transport injected — so both state machines compile natively and can be run
against a simulated lossy channel in `firmware/rev3/test/`. This is the same
trick the conditioning sim uses, and it is worth as much here.

**PIO.** Two programs, both at divider 1.0 with cycles-per-bit setting the baud
(D5): `rs485_tx.pio` (8N1, `DE` on side-set, asserted one bit-time before the
start bit, released one bit-time after the final stop bit, held high while the
TX FIFO still has data) and `rs485_rx.pio` (start-bit detect, mid-bit sampling).
`pico-examples`' `uart_tx.pio`/`uart_rx.pio` are the starting point; the DE
handling and the integer divider are the changes.

Bring-up order for the PIO: scope the TX line for framing, then loop a board's
own TX into its RX through the transceivers (it cannot — DE mutes RO — so use
two boards from the start, or a USB-RS485 adapter at 3 Mbaud).

**DMA.** TX: memory → PIO TX FIFO. RX: PIO RX FIFO → a max-size buffer; the
receiver polls the DMA write pointer, parses the header once ≥ 30 bytes have
landed, and completes when the pointer reaches the total length. Timeout =
`200 µs + len × 10 / baud + 20 % + 50 µs`. Optionally let the **DMA sniffer**
compute CRC-16-CCITT for free on the RX channel — there is exactly one sniffer,
so it belongs to the master's RX path; prove it against the software `crc16()`
with test vectors before relying on it.

**Envelope.**

```
request  (master → bus)      response (slave → master)
 0  0xA5                      0  0x5A
 1  addr  (0x0F = broadcast)  1  addr
 2  cmd                       2  cmd     (echo)
 3  seq                       3  seq     (echo)
 4  len   u8                  4  status  u8
 5  payload[len]              5  len     u16
 …  crc16 u16                 7  payload[len]   ← for POLL: a whole v3 frame
                              …  crc16   u16
```

| cmd | name | payload | response |
|---:|---|---|---|
| 0x01 | `PING` | — | addr, role, firmware id, uptime, capabilities |
| 0x02 | `POLL_FRAME` | syncId u32, mapFormat u8, mapShift u8 | v3 frame |
| 0x03 | `CONSOLE` | text line | text, ≤ 512 B per response |
| 0x04 | `SET` | param text (the `o` grammar) or id/value | ack |
| 0x05 | `SYNC_INFO` | syncId u32, periodUs u32 | broadcast, no response |
| 0x06 | `CONTROL` | tare / reset / bootsel (magic-guarded) | ack |
| 0x07 | `STATUS` | — | counters: overruns, CRC errors, timeouts, rail mV, die °C, flags |

Bus rules: only the master initiates; a slave answers only a complete, CRC-valid
request carrying its own address; it starts transmitting between 20 µs and
200 µs after the request's last stop bit; broadcasts are never answered. A slave
sees every other slave's response on the wire, so its parser resynchronises on
an idle gap of ≥ 4 byte times before accepting a `0xA5`.

Two things that look like bugs and are not: `BUS_RO` floats while this board
transmits (enable the **internal pull-up on GPIO9** and flush the RX FIFO after
every TX), and `BUS_DE` has no external pull-down (the pad's reset pull-down
holds it, but set the PIO pin's value low *before* its direction).

### 7.7 Master: schedule, USB, power

**Poll schedule.** Compute it from the format: `bytes × 10 / baud` per board
plus ~160 µs of request, gap and turnaround. Refuse to start a schedule that
exceeds 85 % of the frame period; instead degrade, in this order, and say so on
the console: `I16 → I12 → I8`, then round-robin full maps (one board per frame
at full resolution, the rest on `NONE`), then drop the frame rate. A silent
degrade is worse than a slow instrument.

**USB out.** Forward each v3 frame verbatim — the master must not re-encode a
slave's frame, or a bus error becomes indistinguishable from a sensor fault.
Own frame first, then slaves in address order. Feed a ring buffer and flush from
the main loop; never block the bus state machine on USB.

**USB power.** `taxelscan_usb_power_init()` after `tusb_init()`; request the
feed only in the master role; expose `W` on the console for status / enable /
disable / **retry** (the policy latches a fault until an explicit retry or a
physical detach — rev-3's review flagged the missing retry path, so build it:
one automatic retry after 5 s, up to three, then latch and report). Surface
`fault_latched`, the CC state and the selected limit in the master's own frame
flags or the `STATUS` reply, so the host can see the chain browning out.

Before USB configuration the master must stay under 100 mA: do not enable the
harness feed, and do not start the row drive, until configured — one more reason
the scan is gated on the role decision.

### 7.8 LED, console, diagnostics

rev-3 has a single green LED on GPIO25 (active high), not rev-1's RGB pixel.
Encode state as blink patterns, written **only between scans**, and keep the
duty low:

| pattern | meaning |
|---|---|
| slow heartbeat | idle, no contact |
| solid | contact accepted (brightness is not available; use a fast flicker for peak if wanted) |
| double blink | tare looked loaded at boot (`FF_TARE_SUSPECT`) |
| triple blink | taxels pinned at the drift cap |
| fast blink | frames overrunning, or no SYNC |
| SOS-ish long/short | master conflict, or latched power fault |

Port the rev-1 console verbatim where it still applies (`f c x t z g s w m o n v
B X R T i d y q b u e k p` — see `firmware/README.md`) and add:

`A` role/address/sync state · `L` list discovered boards · `@<addr> <cmd>` route
a command to one board · `@* <cmd>` broadcast · `M <fmt> [shift]` map format ·
`P <hz>` frame rate (master) · `W` harness power · `S` bus statistics.

Diagnostics that read the matrix must be re-pointed at the LTC1865L, and the
`v` spectrum dump needs a fixed-rate sampler (a PIO-driven CONV, or a timed
loop) that is honest about its 146 ksps ceiling.

---

## 8. Timing budgets

All predicted; M2 and M7 replace them with measurements.

**Scan, per frame, 32 × 32:** one conversion = 2.13 µs (16 SCK at 7.5 MHz) +
5 µs CONV high = **7.13 µs**. Per mux channel: `settleUs` 6 µs + two conversions
(bank A, bank B) = 20.3 µs. Per row: 16 × 20.3 + 5 = 330 µs. Frame: 32 rows plus
two dark sweeps ≈ **11.2 ms** at `ovs 1` → an ~89 fps ceiling before overhead.
At `ovs 2` it is ≈ 18.9 ms, which does **not** fit 60 Hz. So 60 Hz runs at
`ovs 1`, and oversampling is what you spend the headroom on if you drop to
40–50 Hz.

**Conditioning:** 1.76 ms measured for 512 taxels on rev-1 → **~3.5 ms** for
1024. Core 0 must hold cond + frame build + bus + USB inside 16.7 ms.

**Bus at 12.5 Mbaud:** `I16` 2188 B = 1.75 ms/board → 7 slaves = 12.2 ms, plus
~1.1 ms of requests, gaps and turnarounds = **80 %** of the period. `I12`
1676 B = 1.34 ms → 9.4 + 1.1 = **63 %**.

**USB:** `I16` × 8 = 1.05 MB/s (at or over the practical CDC ceiling), `I12` × 8
= 804 kB/s, `I8` × 8 = 559 kB/s. **This is the number M1 exists to establish.**

---

## 9. Milestones

Each milestone ends with something demonstrable and a test that proves it.
M0–M4 are worth doing even if the chain never gets built: they are a complete
single-board 32 × 32 sensor.

**M0 — Toolchain and skeleton.**
0.1 Get the owner's go-ahead to install CMake + Ninja (D1), or fall back to
`arduino-cli`. 0.2 `firmware/rev3/` project, board header (RP2354A, 2 MB flash,
12 MHz XOSC, `PICO_XOSC_STARTUP_DELAY_MULTIPLIER 64`, **no default UART**),
blink GPIO25, USB CDC console, SWD via J6, `picotool` load.
*Done when:* a board boots, blinks, enumerates with a 500 mA descriptor, and a
scope shows **no activity on GPIO0/1**.

**M1 — USB throughput spike.** Before anything is built on top of it: stream
synthetic 8-board payloads and measure sustained bytes/s and jitter, with the
real host reader in the loop.
*Done when:* the achievable rate is written into this file and the default map
format for 8 boards at 60 Hz is chosen on evidence.

**M2 — Matrix bring-up.** 595 zeroing, row walk, mux, LTC1865L, dark sweep, and
the rev-1 diagnostics ported.
*Done when:* `d`, `y`, `q`, `e`, `p` all pass on an assembled board; the row and
column order is recorded; the settle knee is measured; bank A↔B carryover is
measured; `scanUs` is recorded at `ovs 1` and `ovs 2`.

**M3 — Conditioning on hardware.** Shared `condition.cpp` in the SDK build,
flash calibration store, tare, sigma, stored reference.
*Done when:* `make regress` and `make multi` are byte-identical, `n 30` returns
a sensible sigma on hardware, and a 1 h soak on an untouched mat logs zero
contacts.

**M4 — Frame v3 to a host, one board.** v3 emitter, map packers, extended
`test_protocol.py`, viewer updated.
*Done when:* `taxelscan_live.py` shows a live 32 × 32 map at 60 Hz with
`desync = badcrc = stalls = 0` over 10 minutes. **This is the first genuinely
useful deliverable.**

**M5 — SYNC between two boards.** PWM generator, qualified edge capture,
free-run fallback.
*Done when:* a scope shows both boards' row-0 dwell starting within the measured
jitter of each other, and pulling the master's power makes the slave free-run
with `FF_NOSYNC` set rather than go silent.

**M6 — Bus, two boards.** PIO UART at 3 Mbaud → 12.5 Mbaud, DE timing on a
scope, DMA both ways, the link layer with its native tests.
*Done when:* the native state-machine tests pass (including lossy-channel
cases), and a 10-minute poll soak at 12.5 Mbaud logs zero CRC errors and zero
timeouts.

**M7 — The chain.** Eight boards, 4 m harness, the poll schedule, degradation
policy, host demux.
*Done when:* 60 Hz × 8 boards to the host for an hour with zero drops; bus
utilisation, latency and per-board jitter recorded; **and** the analog noise
check: sigma with the bus idle vs the bus saturated, on the same mat, must not
move meaningfully.

**M8 — Power.** `rev3_power` integrated, retry path, fault reporting, and the
bring-up criteria in `boards/rev3/USB_POWER.md` §"Bring-up criteria" worked
through with a meter: inrush, 1→8 boards cold start, CC downgrade while
streaming, voltage at the last board, U14/D4 temperature.
*Done when:* those five criteria have measured numbers written next to them.

**M9 — Hardening and options.** Watchdog and brown-out reporting, config
persistence, `spectrum.py` port, and — only if M1 says it is needed — lossless
delta packing for full maps.

---

## 10. Risks

| # | Risk | Why it matters | What to do about it |
|---|---|---|---|
| R1 | USB CDC cannot carry 8 × full maps at 60 Hz | it is the system's narrowest pipe | M1 first; `I12` default; vendor-bulk endpoint as the escape hatch |
| R2 | The harness will not run at 12.5 Mbaud over 4 m | the whole 60 Hz full-map case depends on it | start at 3 Mbaud, BER soak at each step, be willing to ship 30 Hz full maps |
| R3 | Bus traffic couples into the sense nodes | the front end is megohm and the bus is busy during the scan | measure sigma bus-idle vs bus-busy at M7; `syncDelayUs` lets the scan be staggered away from the poll window |
| R4 | Bank A↔B charge carryover in the ADC's S/H | a press on one bank appears on the other — a phantom with no sensor involvement | test it at M2; a discard conversion costs 6.8 µs |
| R5 | Two address-0 boards on one harness | duplicate masters desynchronise everything | listen-before-talk (D3) and a latched, visible conflict state |
| R6 | Hot-plug overshoot on U12's 6 V absolute max | unmeasured, flagged in AUDIT 7.3 finding 5 | hardware measurement, not firmware — but do not hot-plug during bring-up |
| R7 | `varEma` saturation if the count domain is changed | silently degrades the stuck-contact release logic | D2's `>> 4`; document `adcShift 1` as diagnostic-only |
| R8 | rev-1 firmware flashed onto rev-3 | drives ADDR2 into a closed strap and fights U14's fault output | different USB PID and product string; say it in both READMEs |

---

## 11. Files to create

```
firmware/rev3/
  PLAN.md               this file
  README.md             written as the firmware lands: what it does, how it was measured
  CMakeLists.txt
  board/taxelscan_rev3.h
  pio/rs485_tx.pio  pio/rs485_rx.pio
  compat/Arduino.h  compat/EEPROM.h
  src/main.cpp          boot, role, core 0 loop, watchdog
  src/hw_pins.h         the map of §3.1, checked against gen_rev3.py
  src/scan3.cpp         the scan.h contract on rev-3 hardware
  src/adc_ltc1865.h     the transfer sequence of §7.1
  src/sync.cpp          PWM generator + qualified edge capture
  src/bus_pio.cpp       PIO + DMA transport
  src/bus_proto.cpp     framing, addressing, master/slave state machines (hardware-free)
  src/frame3.cpp        v3 builder + map packers (shared with the native tests)
  src/console.cpp       console, `o` table, diagnostics
  src/store.cpp         flash-backed calibration
  src/led.cpp
  test/                 native tests: bus_proto, frame3 packers, CRC vectors
```

Shared, unchanged: `firmware/taxelscan/condition.cpp/.h`, `scan.h`,
`protocol.h/.cpp`. Extended: `firmware/sim/simtest.cpp` (rev-3 profile),
`firmware/tools/test_protocol.py` (v3), `firmware/tools/taxelscan_live.py`
(multi-board). Linked in: `firmware/rev3_power/`.

---

## 12. Open questions for the owner

1. **Frame rate vs oversampling.** 60 Hz forces `ovs 1` (§8). 50 Hz would allow
   `ovs 2` and its anti-alias dwell notch. Which matters more on the robot?
2. **Full maps, or contacts plus maps on demand?** Contacts from eight boards
   are 66 kB/s and fit trivially; full maps are the entire reason the bus and
   USB budgets are tight.
3. **Does a slave's USB port need to stream?** It can (locally), but it is more
   code and more ways to confuse a chain.
4. **How many boards, really?** Everything above is sized for eight. Five or six
   makes the USB budget comfortable and `I16` the default.

---

## 13. Reference index

| Fact | Source |
|---|---|
| Net list, pin-by-pin, and the checks that assert it | `boards/rev3/gen_rev3.py`, `boards/rev3/check_faults.py` |
| Board intent, front end, bus, power | `boards/rev3/README.md` |
| Review findings, including the firmware blockers | `boards/rev3/AUDIT.md` §7.1, §7.3, §7.4 |
| USB power circuit, budgets, bring-up criteria | `boards/rev3/USB_POWER.md` |
| Power policy + native tests | `firmware/rev3_power/` |
| rev-1 firmware behaviour, console, measured timing | `firmware/README.md` |
| Conditioning contract and its guarantees | `firmware/taxelscan/condition.h` |
| Wire format and the bug that produced it | `firmware/taxelscan/protocol.h`, `firmware/tools/test_protocol.py` |
| Host tools | `firmware/tools/README.md` |
