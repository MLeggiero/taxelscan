#include "scan.h"
#include <hardware/sync.h>
#include <pico/mutex.h>
#include <string.h>

Config cfg;

uint16_t rawFrame[MAX_ROWS][MAX_COLS];
int16_t  drFrame [MAX_ROWS][MAX_COLS];
int32_t  darkQ8  [MAX_BANKS][MAX_CHANS];

volatile ScanTelem telem;

static bool darkPrimed = false;   // first dark sweep is loaded, not eased in

volatile bool     scanRun  = false;
volatile bool     scanIdle = true;
volatile uint32_t frameSeq = 0;

// ---------------------------------------------------------------- handoff
// Core 1 scans; core 0 conditions and talks to USB. Two published buffers with
// a mutex on the index swap. Core 0's copy-out is ~2 us against a 25 ms frame,
// so the "busy" spin below effectively never runs - it is there for the case
// where core 0 stalls on a blocking CDC write.
static int16_t   drBuf[2][MAX_ROWS][MAX_COLS];
static volatile int8_t drLive = 0;
static volatile int8_t drRead = -1;
static mutex_t   frameMtx;
static uint32_t  lastSeen = 0;

void scanInit(void) {
  mutex_init(&frameMtx);

  pinMode(PIN_ROW_LATCH, OUTPUT);
  digitalWrite(PIN_ROW_LATCH, HIGH);
  for (uint8_t p = PIN_MUX_S0; p < PIN_MUX_S0 + 4; p++) {
    pinMode(p, OUTPUT);
    digitalWrite(p, LOW);
  }
  pinMode(PIN_ROW_DATA, OUTPUT);
  pinMode(PIN_ROW_CLK, OUTPUT);
  digitalWrite(PIN_ROW_CLK, LOW);
  digitalWrite(PIN_ROW_DATA, LOW);

  adc_init();
  adc_gpio_init(PIN_ADC_A);
  adc_gpio_init(PIN_ADC_B);
  adc_gpio_init(PIN_ADC_RAIL);      // spare ADC2 for the ROW_VCC probe
  adc_set_temp_sensor_enabled(true);

  selectRow(-1);                    // all rows low before anything else
  scanResetDark();
  memset(rawFrame, 0, sizeof(rawFrame));
  memset(drFrame, 0, sizeof(drFrame));
}

// Forget the dark reference. Must be called on any geometry change: channel k
// means a different electrode afterwards, so the old value is not just stale,
// it is about the wrong thing.
void scanResetDark(void) {
  memset(darkQ8, 0, sizeof(darkQ8));
  darkPrimed = false;
}

void selectRowMask(uint32_t pat) {
  LATCH_LOW();
  for (int i = 31; i >= 0; i--) {
    if ((pat >> i) & 1u) sio_hw->gpio_set = DATA_MASK;
    else                 sio_hw->gpio_clr = DATA_MASK;
    asm volatile("nop; nop; nop; nop");     // 33R series + trace C
    sio_hw->gpio_set = CLK_MASK;
    asm volatile("nop; nop; nop; nop");
    sio_hw->gpio_clr = CLK_MASK;
  }
  LATCH_HIGH();
}

/*
 * One conditioned reading of one bank.
 *
 * Three things happen here that the bring-up sketch did not do.
 *
 * DISCARD. The RP2350 has ONE SAR behind an input mux, so the sample-and-hold
 * cap carries charge from the previously selected channel into the first
 * conversion after a switch. The bring-up scan read ADC0 then ADC1 back to
 * back, which lets a hard press on bank A bleed into the matching bank B taxel.
 * That is a phantom with no sensor involvement at all. One thrown-away
 * conversion costs 2 us and removes it. Prove it is needed with test 0c in the
 * plan before paying for it: press a wired column and watch its floating
 * partner at index chans+k.
 *
 * SPREAD. This is the only anti-alias filtering the signal chain has - the
 * netlist confirms the TLV9062 output goes straight to the ADC pin, no series
 * resistor, no capacitor. Per-taxel sample rate is the frame rate, so motor
 * PWM, servo loops and switching supplies all fold down into the passband and
 * look like a light touch that wanders. N samples spread across T is a boxcar
 * with nulls at k/T. Back-to-back samples (the old avg) span ~8 us and null at
 * 125 kHz, which is useless; spreading the same samples across the dwell puts
 * the null where the interference actually is. If the `v` spectrum probe finds
 * a line, set spreadUs so that oversample*spreadUs == 1/f and null it exactly.
 *
 * TRIM. Dropping the min and max before averaging rejects a single impulse
 * without the cost of a full median.
 */
uint16_t sampleBank(uint8_t bank) {
  adc_select_input(bank);
  if (cfg.adcDiscard) (void)adc_read();

  uint8_t  n = cfg.oversample ? cfg.oversample : 1;
  uint32_t acc = 0;
  uint16_t mn = 0xFFFF, mx = 0;

  uint32_t irq = 0;
  if (cfg.maskIrq) irq = save_and_disable_interrupts();
  for (uint8_t i = 0; i < n; i++) {
    uint16_t v = adc_read();
    acc += v;
    if (v < mn) mn = v;
    if (v > mx) mx = v;
    if (cfg.spreadUs && i + 1 < n) delayMicroseconds(cfg.spreadUs);
  }
  if (cfg.maskIrq) restore_interrupts(irq);

  if (cfg.trim && n >= 4) { acc -= mn; acc -= mx; n -= 2; }
  return (uint16_t)((acc + n / 2) / n);
}

// All rows LOW, walk every mux channel. This is the reference that makes the
// whole design work without a recalibration pause - see the header comment.
static void sweepDark(uint16_t *outA, uint16_t *outB) {
  selectRowMask(0u);
  delayMicroseconds(cfg.rowSettleUs + 10);
  for (uint8_t k = 0; k < cfg.chans; k++) {
    selectChan(k);
    delayMicroseconds(cfg.settleUs);
    outA[k] = sampleBank(0);
    if (cfg.banks > 1) outB[k] = sampleBank(1);
  }
}

// Zeroed by scanPause(). Without that, the first frame after a pause reports a
// period of however long the pause was - and condProcess() would take that as
// dt, letting the baseline take one enormous step. A diagnostic that stops the
// scan for ten seconds must not move the baseline by ten seconds' worth.
static uint32_t lastStart = 0;

void scanFrame(void) {
  uint32_t t0 = micros();
  telem.periodUs = lastStart ? (t0 - lastStart) : 0;
  lastStart = t0;

  uint16_t d0A[MAX_CHANS] = {0}, d0B[MAX_CHANS] = {0};
  uint16_t d1A[MAX_CHANS] = {0}, d1B[MAX_CHANS] = {0};

  if (cfg.darkRef) sweepDark(d0A, d0B);

  for (uint8_t r = 0; r < cfg.rows; r++) {
    selectRow(r);
    delayMicroseconds(cfg.rowSettleUs);
    for (uint8_t k = 0; k < cfg.chans; k++) {
      selectChan(k);
      delayMicroseconds(cfg.settleUs);
      rawFrame[r][k] = sampleBank(0);
      if (cfg.banks > 1) rawFrame[r][cfg.chans + k] = sampleBank(1);
    }
  }
  selectRow(-1);          // rest with every row low: no standing matrix current

  if (cfg.darkRef) {
    sweepDark(d1A, d1B);
    // Bracketed mean, then a slow per-channel EMA. The EMA is what keeps the
    // reference's own noise from showing up as column-wide flicker.
    //
    // The FIRST sweep is loaded straight in rather than eased into from zero.
    // Ramping an EMA up from nothing takes ~70 frames to get within 1%, and
    // every frame until then is dark-referenced against a number that is mostly
    // wrong - which is exactly the window a startup tare would land in. One
    // assignment removes the whole problem.
    const uint8_t sh = cfg.darkShift;
    for (uint8_t k = 0; k < cfg.chans; k++) {
      int32_t mA = (((int32_t)d0A[k] + (int32_t)d1A[k]) / 2) << 8;
      if (!darkPrimed) darkQ8[0][k] = mA;
      else             darkQ8[0][k] += (mA - darkQ8[0][k]) >> sh;
      if (cfg.banks > 1) {
        int32_t mB = (((int32_t)d0B[k] + (int32_t)d1B[k]) / 2) << 8;
        if (!darkPrimed) darkQ8[1][k] = mB;
        else             darkQ8[1][k] += (mB - darkQ8[1][k]) >> sh;
      }
    }
    darkPrimed = true;
  }

  const int nc = nCols();
  for (uint8_t r = 0; r < cfg.rows; r++) {
    for (int c = 0; c < nc; c++) {
      int32_t d = 0;
      if (cfg.darkRef && cfg.rawLevel < 2) {
        uint8_t bank = (c >= cfg.chans) ? 1 : 0;
        uint8_t k    = (uint8_t)(c % cfg.chans);
        d = darkQ8[bank][k] >> 8;
      }
      int32_t v = (int32_t)rawFrame[r][c] - d;
      if (v >  32767) v =  32767;
      if (v < -32768) v = -32768;
      drFrame[r][c] = (int16_t)v;
    }
  }

  // Health channels. Both are cheap and both turn a future mystery into a plot:
  // die temperature is the prime suspect for slow offset drift, and ROW_VCC
  // catches a rail that sags only while the matrix is actually loaded.
  adc_select_input(ADC_TEMP);
  (void)adc_read();
  telem.dieTempRaw = adc_read();
  adc_select_input(ADC_RAIL);
  (void)adc_read();
  telem.railRaw = adc_read();

  telem.scanUs = micros() - t0;
}

// ---------------------------------------------------------------- core 1
void scanPause(void) {
  scanRun = false;
  uint32_t t0 = millis();
  while (!scanIdle && millis() - t0 < 500) { tight_loop_contents(); }
  lastStart = 0;                  // the next frame reports "no previous frame"
}

void scanResume(void) {
  scanRun = true;
}

// Copy the newest published frame out for conditioning. Returns false if
// nothing new has arrived.
bool scanCopyLatest(int16_t *dst) {
  int8_t idx;
  mutex_enter_blocking(&frameMtx);
  if (frameSeq == lastSeen) { mutex_exit(&frameMtx); return false; }
  idx = drLive;
  drRead = idx;
  lastSeen = frameSeq;
  mutex_exit(&frameMtx);

  memcpy(dst, drBuf[idx], sizeof(drBuf[0]));

  mutex_enter_blocking(&frameMtx);
  drRead = -1;
  mutex_exit(&frameMtx);
  return true;
}

/*
 * Core 1's frame loop, on a fixed deadline.
 *
 * The cadence is fixed rather than free-running for two reasons. The one-euro
 * filter needs a known dt or its cutoff wanders with serial load, and a
 * stationary sample clock makes interference stationary too - which is what
 * makes it filterable instead of a moving target. Overruns are counted rather
 * than absorbed, so a dwell setting that does not fit is visible immediately.
 */
void scanCore1Loop(void) {
  static uint64_t deadline = 0;

  if (!scanRun) {
    scanIdle = true;
    deadline = 0;
    // Sleep rather than spin. A tight loop here is core 1 hammering the bus for
    // no reason while core 0 does the conditioning and talks to USB, and the
    // contention is not free: a settle-time sweep found one dwell length where
    // the two cores beat against each other and the scan took ten times longer.
    delayMicroseconds(500);
    return;
  }
  scanIdle = false;

  uint64_t now = time_us_64();
  if (deadline == 0) deadline = now;
  if (now < deadline) {
    // Wait out the remainder in ONE call instead of cycling through loop1()
    // thousands of times. Same latency, a small fraction of the bus traffic.
    uint64_t left = deadline - now;
    if (left > 300) delayMicroseconds((uint32_t)(left - 200));
    return;
  }

  deadline += cfg.framePeriodUs;
  if (deadline < now) {                 // did not fit - resynchronise and count
    telem.overruns++;
    deadline = now + cfg.framePeriodUs;
  }

  // Pick a buffer core 0 is not reading, then scan into the canonical arrays
  // and publish a copy. The extra 2 KB memcpy costs ~2 us.
  int8_t wr;
  for (;;) {
    mutex_enter_blocking(&frameMtx);
    wr = 1 - drLive;
    bool busy = (drRead == wr);
    mutex_exit(&frameMtx);
    if (!busy) break;
    delayMicroseconds(2);
  }

  scanFrame();
  memcpy(drBuf[wr], drFrame, sizeof(drBuf[0]));

  mutex_enter_blocking(&frameMtx);
  drLive = wr;
  frameSeq++;
  mutex_exit(&frameMtx);
}
