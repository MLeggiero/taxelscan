/*
 * scan.h - matrix acquisition for the TaxelScan reader.
 *
 * Owns the pin map, the row shift registers, the column muxes and the ADC.
 * Everything above this layer sees only two arrays:
 *
 *   rawFrame[r][c]   raw ADC counts, exactly what the bring-up sketch produced
 *   drFrame[r][c]    the same frame with the DARK REFERENCE subtracted
 *
 * The dark reference is the whole point of this file. Twice per frame the rows
 * are all driven LOW and every mux channel is read. With no row driven there is
 * no source anywhere in the matrix, so that reading is purely ADC offset +
 * TLV9062 offset + CD74HC4067 leakage + whatever the supply is doing right now.
 * Subtracting it leaves only what the row drive actually pushed through the film.
 *
 * Two consequences worth stating plainly, because they are the reason this
 * sensor needs no recalibration pause:
 *
 *   - The dark reading is VALID UNDER LOAD. Pressing the mat cannot change it,
 *     because with no row driven there is no current to steer. So it is a live
 *     re-zero that never needs an untouched moment.
 *   - A phantom with any electronic cause reads ZERO in this domain, while a
 *     real contact reads full signal no matter how long it has been there. The
 *     reference is re-measured every frame, so a sustained grasp cannot decay.
 *
 * The reference is swept before and after the row walk and fed through a slow
 * per-channel EMA. Averaging it across frames matters: one dark reading serves
 * 32 taxels, so its own noise appears as a COMMON-MODE flicker down a whole
 * column rather than as independent per-taxel noise. The EMA costs nothing and
 * drops that contribution below the per-taxel floor while still tracking drift
 * far faster than drift actually moves.
 */
#pragma once

#include <Arduino.h>
#include <hardware/adc.h>
#include <hardware/structs/sio.h>

// ---------------------------------------------------------------- pin map
// Read out of the assembled board's netlist, not assumed.
//
//   ROW_DATA   GPIO3   (D10)  -> U1.SER
//   ROW_CLK    GPIO2   (D8)   -> SRCLK, 33R series
//   ROW_LATCH  GPIO1   (D7)   -> RCLK,  33R series
//   MUX_S0..S3 GPIO4,5,6,7    -> both muxes (contiguous: one store)
//   ADC_A      GPIO26  (ADC0) <- SENSE_A buffer, R1 3k3 pulldown
//   ADC_B      GPIO27  (ADC1) <- SENSE_B buffer, R2 3k3 pulldown
//   ADC_RAIL   GPIO28  (ADC2) <- optional ROW_VCC monitor wire
static const uint8_t PIN_ROW_DATA  = 3;
static const uint8_t PIN_ROW_CLK   = 2;
static const uint8_t PIN_ROW_LATCH = 1;
static const uint8_t PIN_MUX_S0    = 4;            // S1=5, S2=6, S3=7
static const uint8_t PIN_ADC_A     = 26;
static const uint8_t PIN_ADC_B     = 27;
static const uint8_t PIN_ADC_RAIL  = 28;
static const uint8_t ADC_RAIL      = 2;            // GPIO28 = ADC2
static const uint8_t ADC_TEMP      = 4;            // on-die temperature sensor

static const uint32_t MUX_MASK   = 0xFu << PIN_MUX_S0;
static const uint32_t DATA_MASK  = 1u   << PIN_ROW_DATA;
static const uint32_t CLK_MASK   = 1u   << PIN_ROW_CLK;
static const uint32_t LATCH_MASK = 1u   << PIN_ROW_LATCH;

#define LATCH_LOW()  (sio_hw->gpio_clr = LATCH_MASK)
#define LATCH_HIGH() (sio_hw->gpio_set = LATCH_MASK)

static const int MAX_ROWS    = 32;
static const int MAX_CHANS   = 16;
static const int MAX_BANKS   = 2;
static const int MAX_COLS    = MAX_CHANS * MAX_BANKS;
static const int MAX_TAXELS  = MAX_ROWS * MAX_COLS;     // per sensor

// One reader now drives several mats. They share the row walk and the mux
// select lines, so a frame covers all of them at once and every mat samples
// the same row at the same instant.
//
// MAX_TAXELS stays PER SENSOR - it is the geometry of one mat, and the
// conditioning pipeline is written against one mat at a time. MAX_ALL_TAXELS
// is the size of the arrays that hold every mat's state.
static const int MAX_SENSORS    = 8;
static const int MAX_ALL_TAXELS = MAX_SENSORS * MAX_TAXELS;

// ---------------------------------------------------------------- config
struct Config {
  uint8_t  rows        = 16;   // driven rows, 1..32
  uint8_t  chans       = 16;   // mux channels scanned, 1..16
  uint8_t  banks       = 2;    // 1 = ADC_A only, 2 = both banks   -> 16x32
  uint8_t  sensors     = 1;    // mats attached, 1..MAX_SENSORS
  uint8_t  sensorMask  = 0x01; // which of them to scan and condition
  uint16_t settleUs    = 15;   // sense-node settle after a mux change
  uint16_t rowSettleUs = 5;    // after latching a new row
  uint8_t  mode        = 0;    // 0 text, 1 csv, 2 binary v2

  /*
   * Conditioning bypass. 0 is the full pipeline. 1 hands back the
   * dark-referenced frame with nothing else done to it: no baseline, no
   * filters, no thresholds, no contacts. 2 also drops the dark reference, so
   * what comes out is the ADC reading and nothing else.
   *
   * Level 1 is what you usually want. The dark reference is a measurement
   * technique rather than conditioning: it removes amplifier and mux offset by
   * differential measurement, and switching it off makes the data worse without
   * making it more honest. Level 2 exists for checking the analog front end
   * itself.
   */
  uint8_t  rawLevel    = 0;

  /*
   * Standalone operation. The board is a sensor, not a peripheral: with
   * autoRun set it scans, conditions and drives the status pixel from boot with
   * no host involved at all. Nothing needs to connect for it to work.
   *
   * autoEmit is separate on purpose. Sensing and reporting are different jobs,
   * and streaming binary frames at 80 fps into a USB endpoint nobody is reading
   * is not a useful default. Turn it on if the board is wired to something that
   * always wants frames; leave it off and 'c' starts the stream on demand.
   */
  bool     autoRun     = true;
  bool     autoEmit    = false;

  // --- acquisition -------------------------------------------------------
  uint8_t  oversample  = 2;    // ADC samples per taxel
  uint8_t  spreadUs    = 2;    // gap between them: sets the boxcar null at 1/T
  bool     trim        = true; // drop the min and max before averaging
  bool     adcDiscard  = true; // throw away one conversion after a channel switch
  bool     maskIrq     = true; // interrupts off across the sample burst only
  bool     darkRef     = true; // subtract the per-column dark reference
  uint8_t  darkShift   = 4;    // dark EMA alpha = 1/2^n  (~0.3 s at 40 Hz)

  // --- frame clock -------------------------------------------------------
  uint32_t framePeriodUs = 12500;   // fixed cadence; the filters need a known dt
};
extern Config cfg;

static inline int nCols() { return cfg.chans * cfg.banks; }

// ---------------------------------------------------------------- buffers
extern uint16_t rawFrame[MAX_ROWS][MAX_COLS];   // raw ADC counts
extern int16_t  drFrame [MAX_ROWS][MAX_COLS];   // dark-referenced
extern int32_t  darkQ8  [MAX_BANKS][MAX_CHANS]; // per-channel dark EMA, Q8

// Telemetry refreshed by every scanFrame().
struct ScanTelem {
  uint32_t periodUs;      // measured wall time between frame starts
  uint32_t scanUs;        // time the scan itself took
  uint16_t dieTempRaw;    // ADC channel 4
  uint16_t railRaw;       // ADC2 - only meaningful if D2 is wired to R5
  uint32_t overruns;      // frames that missed the fixed deadline
};
extern volatile ScanTelem telem;

// ---------------------------------------------------------------- hardware
void scanInit(void);
void scanResetDark(void);   // forget the dark reference (geometry changed)

/*
 * Shift the row pattern out by hand rather than over SPI.
 *
 * SPI is deliberately NOT used. SPIClassRP2040::begin() calls
 * gpio_set_function(_RX, GPIO_FUNC_SPI) on its RX pin, and this variant defines
 * PIN_SPI0_MISO as GPIO4 - which is MUX_S0. Calling SPI.begin() therefore takes
 * the mux select line away from the SIO block and kills its output driver, even
 * though the sketch never reads a byte back. Bit-banging costs ~3 us per row
 * against a 25 ms frame, so there is nothing to win by fighting the core over
 * pin ownership. If you ever reintroduce SPI, call SPI.setRX(NOPIN) first.
 *
 * Bit 31 goes out first: it travels furthest down the chain and lands on
 * U4.QH = ROW_31, so bit n ends up on ROW_n.
 */
void selectRowMask(uint32_t pat);

static inline void selectRow(int r) {
  selectRowMask((r < 0) ? 0u : (1u << r));
}

static inline void selectChan(uint8_t k) {
  // one store: no intermediate glitch, and S0..S3 are contiguous GPIO4..7
  sio_hw->gpio_out = (sio_hw->gpio_out & ~MUX_MASK) | ((uint32_t)k << PIN_MUX_S0);
}

// Single unaveraged conversion - the bring-up diagnostics still want this one.
static inline uint16_t readAdc(uint8_t input) {
  adc_select_input(input);
  return adc_read();
}

// One conditioned reading of one bank: optional discard, oversample, trim, mean.
uint16_t sampleBank(uint8_t bank);

// Fill rawFrame + drFrame, refresh the dark EMA, update telemetry.
void scanFrame(void);

// ---------------------------------------------------------------- core 1
// The scan runs on core 1 so USB SOF and CDC transfers cannot stretch a dwell.
// Core 0 must call scanPause() before touching the matrix itself - every
// bring-up diagnostic does.
extern volatile bool     scanRun;    // core 0 requests scanning
extern volatile bool     scanIdle;   // core 1 acknowledges it is off the hardware
extern volatile uint32_t frameSeq;

void scanPause(void);        // blocks until core 1 is off the hardware
void scanResume(void);
bool scanCopyLatest(int16_t *dst);   // copy it out; false if nothing new
void scanCore1Loop(void);            // called from loop1()
