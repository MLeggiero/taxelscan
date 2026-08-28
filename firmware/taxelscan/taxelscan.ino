/*
 * TaxelScan  -  XIAO RP2350 firmware
 * ------------------------------------------------
 * Board: TaxelScan reader (4x SN74LVC595A rows, 2x CD74HC4067 columns,
 *        TLV9062 dual unity-gain buffer into ADC0/ADC1).
 *
 * This sketch is the console, the bring-up diagnostics and the frame loop.
 * The interesting parts live next door:
 *
 *   scan.h/.cpp        pin map, row drive, mux, ADC, DARK REFERENCE, core-1 loop
 *   condition.h/.cpp   the noise-rejection pipeline and the contact extractor
 *   protocol.h/.cpp    binary frame format v2, with a length field and CRCs
 *
 * WHY THERE IS A PIPELINE AT ALL
 *
 * The complaint this was built for: after a press is released, small patches
 * keep reading as though something is still there. Five different faults
 * produce that, they need different fixes, and they are separable in about
 * five minutes with the diagnostics already in this file:
 *
 *   decays over 5-60 s            Velostat creep      -> adaptive baseline
 *   rock steady indefinitely      offset/leakage drift-> dark reference
 *   mirrors a press onto the      ADC channel bleed   -> cfg.adcDiscard
 *     floating partner column
 *   wanders with the arm live     aliased interference-> dwell spread, filters
 *   a whole row or column lifts   595 Vol under sneak -> measure, then decide
 *     under a hard press               current
 *
 * Matrix ghosting is deliberately absent from that list. Unselected rows are
 * driven LOW by the 595s at ~25 ohm, so every sneak path terminates at ground:
 * a pressed taxel adds a parallel path to ground on its column, which makes
 * other readings LOWER, not higher. Crosstalk on this topology is negative and
 * modest, and it cannot manufacture a phantom. Do not go looking for it.
 *
 * CORE SPLIT. Core 1 owns the matrix and scans on a fixed deadline. Core 0
 * conditions and talks to USB. USB SOF and CDC transfers used to preempt the
 * dwell delays mid-sample; now they cannot. Anything on core 0 that touches the
 * hardware directly - every diagnostic below - must call needIdle() first.
 */

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include "scan.h"
#include "condition.h"
#include "protocol.h"
#include "options.h"

/*
 * On-board RGB pixel. Traced on Seeed's own board file, because the variant
 * header does not define these pins:
 *
 *   GPIO22 -> U8 (UM3301DA level shifter) -> LED1 DI   ... WS2812 data
 *   GPIO23 -> U7 (SX1801CCR load switch)  -> LED1 VDD  ... +5V power enable,
 *             and it also enables the level shifter, so it must go HIGH first.
 *
 * The pixel runs off +5V, so the level shifter is not optional - driving GPIO22
 * straight at a 5V WS2812 would be marginal. The yellow LED on GPIO25 is active
 * low and is parked OFF, so the RGB pixel is the only indicator.
 */
static const uint8_t PIN_RGB_DATA = 22;
static const uint8_t PIN_RGB_PWR  = 23;
static const uint32_t LED_MASK = 1u << LED_BUILTIN;
#define LED_OFF()  (sio_hw->gpio_set = LED_MASK)

static Adafruit_NeoPixel pixel(1, PIN_RGB_DATA, NEO_GRB + NEO_KHZ800);

/*
 * The pixel is a pressure gauge, not a decoration.
 *
 * Brightness is fixed and low; only the HUE follows the peak of the GATED map,
 * so a rejected phantom cannot light it. Colour alone carries the reading,
 * which keeps the pixel unobtrusive on a robot that is running all day.
 *
 * The write policy matters as much as the colours. The bring-up sketch swept a
 * rainbow every 20 ms, which is a WS2812 burst plus a step in +5V load at ~50 Hz
 * - right next to the frame rate, beating against it, and showing up in the data
 * as a slowly moving pattern. So the pixel is written ONLY when the value it
 * would display actually changes, quantised to 32 levels, and never more than
 * 20 times a second. Sitting at rest it is not written at all.
 *
 * `o ledbright` is the single brightness dial, raw 0..255. `o led 0` turns the
 * pixel off entirely.
 */
enum LedState { LED_BOOT, LED_IDLE, LED_CONTACT, LED_CAPPED, LED_OVERRUN, LED_SUSPECT };
static LedState ledState  = LED_BOOT;
static bool     ledEnable = true;
static uint8_t  ledBright = 36;     // the ONE brightness, 0..255
static uint16_t ledFull   = 800;    // counts that map to full scale
static uint8_t  ledLevel  = 255;    // quantised, out of range so the first call writes

/*
 * ONE brightness for everything; only the hue carries information.
 *
 * Adafruit's hue space is red 0, green 21845, blue 43690, wrapping to red at
 * 65535. Pressure walks UPWARD from blue, so it runs blue -> violet -> magenta
 * -> red and never passes through green or yellow. That leaves those two free
 * to mean something else, which is the whole reason for going up rather than
 * down: idle can be green and a fault can be yellow with no ambiguity against
 * any pressure reading.
 *
 *   green    idle, powered, nothing in contact
 *   blue     lightest accepted contact
 *   magenta  about half of `ledfull`
 *   red      `ledfull` and above
 *   yellow   taxels pinned at the drift cap
 *   orange   the startup tare looked loaded
 *   white    frames are overrunning the fixed cadence
 */
static void ledWrite(LedState s, uint8_t lvl) {
  ledState = s;
  ledLevel = lvl;
  uint16_t hue;
  uint8_t  sat = 255;
  if      (s == LED_OVERRUN) { hue = 0;     sat = 0; }      // white
  else if (s == LED_CAPPED)    hue = 10922;                 // yellow
  else if (s == LED_SUSPECT)   hue = 5461;                  // orange
  else if (lvl == 0)           hue = 21845;                 // green: idle
  else {
    float t = lvl / 31.0f;
    hue = (uint16_t)(43690.0f + 21845.0f * t);              // blue -> red
  }
  // No gamma32: it is a perceptual curve for full-range colour, and at these
  // deliberately low values it crushes everything to near black.
  pixel.setPixelColor(0, Adafruit_NeoPixel::ColorHSV(hue, sat, ledBright));
  pixel.show();
}

static void ledUpdate(int peak) {
  if (!ledEnable) return;
  static uint32_t last = 0, lastOvr = 0, lastOvrAt = 0;
  uint32_t now = millis();
  if (now - last < 50u) return;                 // 20 Hz ceiling
  last = now;

  // telem.overruns only ever climbs, so latching on the count itself would mean
  // one bad frame turned the board red forever. Show it for five seconds after
  // it last moved instead.
  if (telem.overruns != lastOvr) { lastOvr = telem.overruns; lastOvrAt = now; }
  bool ovrRecent = lastOvrAt && (now - lastOvrAt < 5000u);

  if (peak < 0) peak = 0;
  uint32_t f = ledFull ? ledFull : 1;
  uint8_t lvl = (uint8_t)((peak >= (int)f) ? 31 : ((uint32_t)peak * 32u) / f);

  LedState s;
  if      (ovrRecent)     s = LED_OVERRUN;
  else if (ctel[0].capped)   s = LED_CAPPED;
  else if (tareSuspect)   s = LED_SUSPECT;
  else                    s = lvl ? LED_CONTACT : LED_IDLE;

  if (s != ledState || lvl != ledLevel) ledWrite(s, lvl);
}

// Peak of the gated map: only contacts that survived the area/force gate count,
// so a leftover speck can never light the pixel.
static int gatedPeak() {
  int p = 0;
  for (int i = 0; i < nContacts[0]; i++)
    if ((contacts[0][i].flags & CF_ACCEPTED) && contacts[0][i].peak > p)
      p = contacts[0][i].peak;
  return p;
}

static bool     streaming = false;
static volatile bool sysReady = false;
static int16_t  work[MAX_ROWS][MAX_COLS];

static inline int16_t valueAt(int r, int c) { return outMap[r * MAX_COLS + c]; }
static inline float toVolts(uint16_t raw) { return raw * 3.3f / 4095.0f; }

/*
 * Everything that drives the matrix from core 0 must own it exclusively first.
 *
 * It must also GIVE IT BACK. An earlier version of this just cleared `streaming`
 * and left it cleared, so any diagnostic - or a tare, or a geometry change -
 * silently killed the stream for good. The host had no idea: the viewer sends
 * these through a fire-and-forget command endpoint and never re-sends 'c', so
 * one click on "tare" made the sensor look dead. The pixel froze at the same
 * moment, because it only updates on the streaming path. Two symptoms, one
 * cause, and nothing wrong with the sensor at all.
 */
static bool resumeAfterCmd = false;

static void needIdle() {
  if (streaming) { resumeAfterCmd = true; streaming = false; }
  scanPause();
}

// Called at the end of every command. `k` and `b` are excluded on purpose: their
// whole job is to leave the matrix parked so it can be probed with a meter, and
// resuming the scan would immediately undo that.
static void resumeIfPaused(char cmd) {
  bool wasStreaming = resumeAfterCmd;
  resumeAfterCmd = false;
  if (cmd == 'k' || cmd == 'b') {
    if (wasStreaming || cfg.autoRun)
      Serial.println(F("# rows left parked - press 'c' when you are done probing"));
    return;
  }
  if (wasStreaming) streaming = true;
  // Sensing comes back even if nothing was streaming, because with autoRun the
  // board is supposed to be live whenever it is powered.
  if (wasStreaming || cfg.autoRun) scanResume();
}

// ---------------------------------------------------------------- output
static void emitText(uint32_t us) {
  Serial.printf("# frame %ux%u  %lu us  %.1f fps  peak %d  active %u  contacts %u (%u rejected)\n",
                cfg.rows, nCols(), (unsigned long)us, 1e6f / (float)us,
                ctel[0].peak, ctel[0].activeCells, nContacts[0] - nRejected[0], nRejected[0]);
  Serial.print("     ");
  for (int c = 0; c < nCols(); c++) Serial.printf("%5d", c);
  Serial.println();
  for (int r = 0; r < cfg.rows; r++) {
    Serial.printf("r%-3d ", r);
    for (int c = 0; c < nCols(); c++) Serial.printf("%5d", valueAt(r, c));
    Serial.println();
  }
  for (int i = 0; i < nContacts[0]; i++) {
    const Contact &k = contacts[0][i];
    Serial.printf("# contact %-2u %s area %-3u sum %-7ld peak %-5d at r%u c%u "
                  "centroid r%.2f c%.2f  bbox r%u-%u c%u-%u%s\n",
                  k.id, (k.flags & CF_ACCEPTED) ? "OK      " : "REJECTED",
                  k.area, (long)k.sum, k.peak, k.peakR, k.peakC,
                  k.cenRQ8 / 256.0f, k.cenCQ8 / 256.0f,
                  k.r0, k.r1, k.c0, k.c1,
                  (k.flags & CF_EDGE_LIVE) ? "  edge-live" : "");
  }
  if (ctel[0].capped)
    Serial.printf("# WARNING %u taxels pinned at the drift cap - baseline cannot "
                  "follow any further, a phantom may return there\n", ctel[0].capped);
}

static void emitCsv() {
  for (int r = 0; r < cfg.rows; r++) {
    for (int c = 0; c < nCols(); c++)
      Serial.printf("%d%s", valueAt(r, c), c == nCols() - 1 ? "" : ",");
    Serial.println();
  }
}

static void emit(uint32_t us) {
  if      (cfg.mode == 0) emitText(us);
  else if (cfg.mode == 1) emitCsv();
  else                    emitBinV2();
}

// ---------------------------------------------------------------- tare
/*
 * Capture the resting level of every taxel and make it the reference.
 *
 * `warm` frames are scanned and thrown away first. The analog front end needs a
 * moment after the row drivers start moving, and the median history wants to be
 * full before anything is averaged.
 *
 * This is not what keeps the sensor honest during a run - the dark reference
 * does that, and it needs no untouched mat. What the tare establishes is
 * `tare0`, which the drift cap hangs off, and the definition of zero force.
 */
static void tare(uint8_t n = 32, uint8_t warm = 8) {
  static int32_t acc[MAX_ROWS][MAX_COLS];
  needIdle();
  for (uint8_t i = 0; i < warm; i++) scanFrame();
  memset(acc, 0, sizeof(acc));
  for (uint8_t i = 0; i < n; i++) {
    scanFrame();
    for (int r = 0; r < cfg.rows; r++)
      for (int c = 0; c < nCols(); c++) acc[r][c] += drFrame[r][c];
  }
  static int16_t avg[MAX_ROWS][MAX_COLS];
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nCols(); c++) avg[r][c] = (int16_t)(acc[r][c] / n);
  condSeedBaseline(0, &avg[0][0]);
  Serial.printf("# tared over %u frames; drift cap set at tare + %u counts\n",
                n, cc.maxDrift);
}

/*
 * Was something already pressing on the mat when it powered up?
 *
 * A startup tare is blind by construction: whatever is on the mat at boot
 * becomes the definition of zero, and a humanoid that powers up with its palm
 * against a bracket will simply not see the bracket. It self-heals the moment
 * the load is removed - the ungated fast fall walks the baseline down in a few
 * seconds - but until then the sensor is quietly deaf in that patch, and on a
 * robot that is worth an explicit warning rather than a surprise.
 *
 * Two ways to notice, best first:
 *
 *   - against the STORED REFERENCE, if one has been saved with `X`. This is a
 *     direct per-taxel comparison against a mat that was known to be clear, so
 *     it catches an evenly distributed load that a shape test would miss.
 *   - against the array's own MEDIAN otherwise. A resting mat is fairly
 *     uniform, so taxels far above the middle are the suspicious ones. Weaker:
 *     it cannot see a load pressing on everything at once.
 *
 * The verdict is reported, flagged in every frame header, and shown on the
 * pixel. It never blocks: a suspect baseline is still far better than none.
 */
static void checkTareLoaded() {
  const int thresh = (int)cc.maxDrift;      // same yardstick as the drift cap
  int worst = 0, wi = -1, med = 0, n = 0;
  bool usedRef = condRefValid();

  if (usedRef) n = condRefCompare(0, thresh, &worst, &wi);
  else         n = condTareOutliers(0, thresh, &worst, &wi, &med);

  tareSuspect = (n > 0);
  if (!tareSuspect) {
    if (usedRef)
      Serial.printf("# tare matches the stored reference (worst taxel %+d counts)\n", worst);
    else
      Serial.printf("# tare looks clear: median %d counts, worst taxel %+d above it\n",
                    med, worst);
    return;
  }

  Serial.println(F("# ****************************************************************"));
  Serial.printf("# WARNING: %d taxels read more than %d counts above %s.\n",
                n, thresh, usedRef ? "the stored reference" : "the array median");
  if (wi >= 0)
    Serial.printf("# Worst is r%d c%d at %+d counts.\n",
                  wi / MAX_COLS, wi % MAX_COLS, worst);
  Serial.println(F("# Something was probably pressing on the mat when it powered up.\n"
                   "# That pressure is now the definition of zero, so the sensor is\n"
                   "# BLIND to it until it is removed. Clear the mat and press 't'."));
  if (!usedRef)
    Serial.println(F("# No stored reference exists, so this is only a shape test - it\n"
                     "# cannot see a load spread evenly over the whole mat. With the mat\n"
                     "# clear, press 'X' once to record a reference for future boots."));
  Serial.println(F("# ****************************************************************"));
}

// Measure one frame and say whether it fits the fixed cadence. Worth running
// after any change to geometry, dwell or oversampling: a period that does not
// fit does not fail loudly, it just accumulates overruns and makes dt wrong for
// every filter downstream.
static void reportTiming() {
  needIdle();
  scanFrame();
  uint32_t us = telem.scanUs;
  Serial.printf("# scan takes %lu us; frame period is %lu us\n",
                (unsigned long)us, (unsigned long)cfg.framePeriodUs);
  if (us + us / 5 > cfg.framePeriodUs) {
    uint32_t want = (us + us / 5 + 999) / 1000 * 1000;
    Serial.printf("# TOO TIGHT - every frame will overrun. Either 'o period %lu'\n"
                  "#   or cut the dwell: 'o ovs 2' / 'o spread 2' / 's 10'\n",
                  (unsigned long)want);
  } else {
    Serial.printf("# fits, %lu us of headroom (%.0f fps)\n",
                  (unsigned long)(cfg.framePeriodUs - us),
                  1e6f / (float)cfg.framePeriodUs);
  }
}

// ---------------------------------------------------------------- diagnostics
// After assembly this is the fast way to find a cold joint: a dead row reads
// flat across every column, a dead column reads flat down every row.
static void diagnose() {
  needIdle();
  scanFrame();
  Serial.println("# --- bring-up diagnostics ---");

  selectRow(-1);
  delayMicroseconds(200);
  uint32_t mn0 = 4095, mx0 = 0, s0 = 0;
  for (int i = 0; i < 64; i++) {
    uint16_t v = readAdc(0);
    s0 += v; if (v < mn0) mn0 = v; if (v > mx0) mx0 = v;
  }
  uint32_t mn1 = 4095, mx1 = 0, s1 = 0;
  for (int i = 0; i < 64; i++) {
    uint16_t v = readAdc(1);
    s1 += v; if (v < mn1) mn1 = v; if (v > mx1) mx1 = v;
  }
  Serial.printf("# rows off: ADC_A mean %lu (min %lu max %lu)   ADC_B mean %lu (min %lu max %lu)\n",
                s0 / 64, mn0, mx0, s1 / 64, mn1, mx1);
  Serial.println("#   expect near 0 - the 3k3 pulldowns hold both sense nodes at GND.");
  Serial.println("#   a large or noisy value means an unconnected buffer or a solder fault.");
  Serial.print("# dark reference now: ");
  for (int k = 0; k < cfg.chans; k++) Serial.printf("%d ", (int)(darkQ8[0][k] >> 8));
  Serial.println();

  Serial.println("# per-row max (flat/zero row = dead 595 output or row electrode):");
  for (int r = 0; r < cfg.rows; r++) {
    int m = 0;
    for (int c = 0; c < nCols(); c++) if (rawFrame[r][c] > m) m = rawFrame[r][c];
    Serial.printf("#   row %-2d max %4d\n", r, m);
  }
  Serial.println("# per-col max (flat/zero column = dead mux channel or column electrode):");
  for (int c = 0; c < nCols(); c++) {
    int m = 0;
    for (int r = 0; r < cfg.rows; r++) if (rawFrame[r][c] > m) m = rawFrame[r][c];
    Serial.printf("#   col %-2d max %4d  (bank %s ch %d)\n", c, m,
                  c < cfg.chans ? "A" : "B", c % cfg.chans);
  }
}

// ---------------------------------------------------------------- connectivity
// Read every column once for a given row drive pattern, heavily averaged.
static void probeColumns(uint32_t rowMask, uint16_t *out,
                         uint16_t navg, uint16_t settleUs) {
  selectRowMask(rowMask);
  delayMicroseconds(500);                 // generous: this is not a timed path
  for (uint8_t k = 0; k < cfg.chans; k++) {
    selectChan(k);
    delayMicroseconds(settleUs);
    uint32_t a = 0, b = 0;
    for (uint16_t n = 0; n < navg; n++) {
      a += readAdc(0);
      if (cfg.banks > 1) b += readAdc(1);
    }
    out[k] = (uint16_t)(a / navg);
    if (cfg.banks > 1) out[cfg.chans + k] = (uint16_t)(b / navg);
  }
}

/*
 * Find open electrodes WITHOUT needing anyone to press the mat.
 *
 * Driving one row and reading one unpressed taxel is hopeless - a resting
 * taxel is megohms, so against the 3k3 pulldown it is worth a couple of LSB.
 * Driving ALL rows at once instead puts ~32 taxels in PARALLEL as the pull-up
 * for each column, dropping the source impedance by about 30x. A connected
 * column then reads tens of counts at rest while an open one sits at the ADC
 * offset floor, and the two are trivially separable.
 *
 * The per-row pass is the weak half by construction: one row alone is back to
 * one taxel per column, so it is summed across all columns to claw back SNR.
 * Treat its verdict as a hint, and confirm a suspect row with 'k' plus a meter.
 */
static void connectivity() {
  static uint16_t lo[MAX_COLS], hi[MAX_COLS], one[MAX_COLS];
  needIdle();

  Serial.println(F("# --- connectivity sweep (no pressure needed) ---"));
  Serial.println(F("# COLUMNS: all rows LOW vs all rows HIGH."));

  probeColumns(0u, lo, 64, 500);
  probeColumns(0xFFFFFFFFu, hi, 64, 500);

  int nOpen = 0, nConn = 0;
  Serial.println(F("# col bank ch   allLow  allHigh    delta  verdict"));
  for (int c = 0; c < nCols(); c++) {
    int d = (int)hi[c] - (int)lo[c];
    const char *v = (d >= 25) ? "CONNECTED" : (d >= 8) ? "weak" : "OPEN";
    if (d >= 25) nConn++; else if (d < 8) nOpen++;
    Serial.printf("#  %-3d   %s  %-3d  %7u  %7u  %7d  %s\n",
                  c, c < cfg.chans ? "A" : "B", c % cfg.chans, lo[c], hi[c], d, v);
  }
  Serial.printf("# columns: %d connected, %d open, %d weak\n",
                nConn, nOpen, nCols() - nConn - nOpen);

  Serial.println(F("# ROWS: each row alone, summed over every column."));
  Serial.println(F("# row   sum(delta)   max   verdict"));
  for (int r = 0; r < cfg.rows; r++) {
    probeColumns(1u << r, one, 64, 500);
    long sum = 0; int mx = 0;
    for (int c = 0; c < nCols(); c++) {
      int d = (int)one[c] - (int)lo[c];
      sum += d;
      if (d > mx) mx = d;
    }
    Serial.printf("#  %-3d  %10ld  %5d   %s\n", r, sum, mx,
                  (sum >= 40) ? "live" : (sum >= 12) ? "faint" : "no signal");
  }
  selectRowMask(0u);
  Serial.println(F("# all rows returned LOW."));
}

/*
 * Park the row drivers so the board can be probed with a meter. This is the
 * only way to separate a dead 595 output from an open connector: the readout
 * is sense-side only, so both faults look identical from the ADC.
 *
 *   k a    all rows HIGH   - every 595 output should sit at ~3.3 V
 *   k n    all rows LOW
 *   k <n>  row n HIGH only - walk it to map 595 output to FFC pin
 */
static void parkRows(char *cmd) {
  int a;
  needIdle();
  const char *p = cmd + 1;
  while (*p == ' ' || *p == '\t') p++;        // accept "ka" and "k a" alike
  if (*p == 'a') {
    selectRowMask(0xFFFFFFFFu);
    Serial.println(F("# all 32 rows parked HIGH - expect ~3.3 V on every 595 output"));
  } else if (*p == 'n') {
    selectRowMask(0u);
    Serial.println(F("# all rows parked LOW"));
  } else if (sscanf(p, "%d", &a) == 1) {
    a = constrain(a, 0, 31);
    selectRowMask(1u << a);
    Serial.printf("# row %d parked HIGH, all others LOW\n", a);
  }
}

/*
 * Row probe. Reports measurements, never a pass/fail on how hard someone
 * pressed - "I pressed and nothing read" is itself a finding.
 *
 * Two things make the result trustworthy that a naive sweep gets wrong:
 *
 * SHUFFLED ORDER. Sweeping rows 0..31 in order over ~250 ms cannot tell a
 * spatial profile from the time profile of a press being applied and released:
 * both look like a hump. Rows are probed in bit-reversed order instead, so a
 * signal that follows the row INDEX is spatial while one that follows the
 * probe SEQUENCE is the operator's hand. The seq column is reported so the
 * host can check which axis the data is smooth along.
 *
 * BRACKETED BASELINE. Each row is measured between two all-rows-low readings
 * and compared against their mean, so a press that drifts during the sweep
 * cancels instead of being counted as row response.
 */
static uint8_t bitrev5(uint8_t v) {
  uint8_t r = 0;
  for (uint8_t i = 0; i < 5; i++) if (v & (1u << i)) r |= 1u << (4 - i);
  return r;
}

static void rowIndependence() {
  static uint16_t lo_s[MAX_COLS], hi_s[MAX_COLS];
  static uint16_t lo_l[MAX_COLS], hi_l[MAX_COLS];
  static uint16_t b1[MAX_COLS], b2[MAX_COLS], v[MAX_COLS];
  needIdle();
  const uint16_t SH = cfg.settleUs, LG = 300;
  const int n = nCols();

  probeColumns(0u,          lo_s, 32, SH);
  probeColumns(0xFFFFFFFFu, hi_s, 32, SH);
  probeColumns(0u,          lo_l, 32, LG);
  probeColumns(0xFFFFFFFFu, hi_l, 32, LG);

  Serial.println(F("# --- row probe ---"));
  Serial.printf("# settle short=%uus long=%uus avg=32 cols=%d order=bitrev\n", SH, LG, n);
  const char *names[4] = { "none_s", "all_s ", "none_l", "all_l " };
  uint16_t *refs[4] = { lo_s, hi_s, lo_l, hi_l };
  for (int i = 0; i < 4; i++) {
    Serial.printf("# ref %s:", names[i]);
    for (int c = 0; c < n; c++) Serial.printf("%5u", refs[i][c]);
    Serial.println();
  }

  Serial.println(F("# row delta col seq   (delta vs bracketing all-low mean)"));
  uint8_t seq = 0;
  for (uint8_t i = 0; i < 32; i++) {
    uint8_t r = bitrev5(i);
    if (r >= cfg.rows) continue;
    probeColumns(0u,       b1, 16, SH);
    probeColumns(1u << r,  v,  32, SH);
    probeColumns(0u,       b2, 16, SH);
    int best = -32768, bestc = -1;
    for (int c = 0; c < n; c++) {
      int base = ((int)b1[c] + (int)b2[c]) / 2;
      int d = (int)v[c] - base;
      if (d > best) { best = d; bestc = c; }
    }
    Serial.printf("# %3d %5d %3d %3d\n", r, best, bestc, seq);
    seq++;
  }
  selectRowMask(0u);
  Serial.println(F("# --- end row probe ---"));
}

/*
 * ROW_VCC rail monitor on the spare ADC (GPIO28 / D2).
 *
 * Run a wire from D2 to ROW_VCC - either end of R5, or the non-ground pad of
 * C1..C4. Safe: the rail is 3V3 and the ADC spans 0..3V3.
 *
 * A DMM is not good enough here. Scanning is intermittent, so a meter averages
 * over it and can read a comfortable 3.3 V while the rail is actually
 * collapsing every time an output has to source current. This samples the rail
 * DURING a scan and reports the worst dip.
 */
static void railMonitor() {
  needIdle();
  uint32_t acc = 0;
  adc_select_input(ADC_RAIL);
  for (int i = 0; i < 64; i++) acc += adc_read();
  uint16_t idle = (uint16_t)(acc / 64);

  uint16_t mn = 4095, mx = 0;
  for (uint8_t r = 0; r < cfg.rows; r++) {
    selectRow(r);
    delayMicroseconds(cfg.rowSettleUs);
    for (uint8_t k = 0; k < cfg.chans; k++) {
      selectChan(k);
      delayMicroseconds(cfg.settleUs);
      adc_select_input(ADC_RAIL);
      uint16_t v = adc_read();
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
  }
  selectRow(-1);

  uint32_t after = 0;
  adc_select_input(ADC_RAIL);
  for (int i = 0; i < 16; i++) after += adc_read();
  after /= 16;

  Serial.println(F("# --- ROW_VCC rail (wire D2 to either end of R5) ---"));
  Serial.printf("# idle  %4u  %.3f V\n", idle, toVolts(idle));
  Serial.printf("# scan  min %4u  %.3f V   max %4u  %.3f V\n",
                mn, toVolts(mn), mx, toVolts(mx));
  Serial.printf("# after %4lu  %.3f V\n", (unsigned long)after, toVolts((uint16_t)after));
  Serial.printf("# sag   %.3f V\n", toVolts(idle) - toVolts(mn));
  Serial.println(F("# if idle is ~3.3V but min collapses, the rail cannot source current"));
  Serial.println(F("# --- end rail ---"));
}

/*
 * Row output walk - measures what the 595 outputs ACTUALLY do.
 *
 * Everything else in this sketch watches the row drive through the mat, which
 * cannot distinguish a drive fault from a sensor fault. This looks straight at
 * an output pin instead. No sensor and no press needed.
 *
 * Wire D2 (GPIO28 / ADC2) to ONE row pin: either a J1 pin, or the matching
 * 595 output. Then `e <pin>` walks all 32 row patterns and reports the voltage
 * that pin sits at for each.
 */
static void rowOutputWalk(char *cmd) {
  int pin = -1;
  sscanf(cmd + 1, "%d", &pin);
  needIdle();

  Serial.println(F("# --- row output walk (wire D2 to one row pin) ---"));
  if (pin >= 0) Serial.printf("# D2 is on ROW_%d\n", pin);
  Serial.println(F("# row  raw   volts"));

  uint16_t lo = 4095, hi = 0;
  int hiRow = -1;
  for (int r = 0; r < 32; r++) {
    selectRowMask(1u << r);
    delayMicroseconds(200);
    adc_select_input(ADC_RAIL);
    uint32_t acc = 0;
    for (int i = 0; i < 16; i++) acc += adc_read();
    uint16_t v = (uint16_t)(acc / 16);
    if (v < lo) lo = v;
    if (v > hi) { hi = v; hiRow = r; }
    Serial.printf("# %3d %5u  %.2f\n", r, v, toVolts(v));
  }

  uint16_t pat[2];
  const uint32_t pats[2] = { 0xAAAAAAAAu, 0x55555555u };
  for (int i = 0; i < 2; i++) {
    selectRowMask(pats[i]);
    delayMicroseconds(200);
    adc_select_input(ADC_RAIL);
    uint32_t acc = 0;
    for (int k = 0; k < 16; k++) acc += adc_read();
    pat[i] = (uint16_t)(acc / 16);
  }
  selectRowMask(0u);

  Serial.printf("# 0xAAAAAAAA %4u %.2f V    0x55555555 %4u %.2f V\n",
                pat[0], toVolts(pat[0]), pat[1], toVolts(pat[1]));
  Serial.printf("# walk spread: min %u (%.2f V)  max %u (%.2f V) at row %d\n",
                lo, toVolts(lo), hi, toVolts(hi), hiRow);
  int spread = (int)hi - (int)lo;
  int patdiff = (int)pat[0] - (int)pat[1];
  if (patdiff < 0) patdiff = -patdiff;
  if (spread < 200 && patdiff < 200)
    Serial.println(F("# VERDICT: outputs do not follow the shifted data -> SER path dead"));
  else if (spread >= 2000)
    Serial.println(F("# VERDICT: one row drives this pin -> row selection works"));
  else
    Serial.println(F("# VERDICT: partial swing - suspect contention or a weak driver"));
  Serial.println(F("# --- end walk ---"));
}

/*
 * Bit-bang the 595 chain with plain GPIO, bypassing the fast path entirely.
 *
 *   b n / b a differ  -> the hardware is fine
 *   b n / b a same    -> the path from GPIO to the 595 outputs is broken
 *                        (probe R3/R4, the 33R series parts, and GPIO1/2/3)
 *
 * Press and hold while running both, or neither will show anything.
 */
static void readColumnsOnly(uint16_t *out, uint16_t navg, uint16_t settleUs) {
  for (uint8_t k = 0; k < cfg.chans; k++) {
    selectChan(k);
    delayMicroseconds(settleUs);
    uint32_t a = 0, b = 0;
    for (uint16_t n = 0; n < navg; n++) {
      a += readAdc(0);
      if (cfg.banks > 1) b += readAdc(1);
    }
    out[k] = (uint16_t)(a / navg);
    if (cfg.banks > 1) out[cfg.chans + k] = (uint16_t)(b / navg);
  }
}

static void bitbangRows(char *cmd) {
  static uint16_t v[MAX_COLS];
  int a;
  uint32_t pat;
  const char *p = cmd + 1;
  while (*p == ' ' || *p == '\t') p++;
  if      (*p == 'a') pat = 0xFFFFFFFFu;
  else if (*p == 'n') pat = 0u;
  else if (sscanf(p, "%d", &a) == 1) pat = 1u << constrain(a, 0, 31);
  else { Serial.println(F("# usage: b a | b n | b <row>")); return; }

  needIdle();
  LATCH_LOW();
  for (int i = 31; i >= 0; i--) {     // bit 31 first: it travels to U4.QH = ROW_31
    digitalWrite(PIN_ROW_DATA, (pat >> i) & 1u);
    delayMicroseconds(3);
    digitalWrite(PIN_ROW_CLK, HIGH);
    delayMicroseconds(3);
    digitalWrite(PIN_ROW_CLK, LOW);
    delayMicroseconds(3);
  }
  LATCH_HIGH();
  delayMicroseconds(500);

  static uint16_t vf[MAX_COLS];
  readColumnsOnly(vf, 1, 20);
  readColumnsOnly(v, 32, 300);
  Serial.printf("# bitbang pattern 0x%08lX\n", (unsigned long)pat);
  Serial.print(F("# fast:"));
  for (int c = 0; c < nCols(); c++) Serial.printf("%5u", vf[c]);
  Serial.println();
  Serial.print(F("# cols:"));
  for (int c = 0; c < nCols(); c++) Serial.printf("%5u", v[c]);
  Serial.println();
}

// Sweep the settle delay on one taxel. Readings climb then plateau; the knee is
// the true settle time.
static void sweepSettle(int r, int c) {
  needIdle();
  Serial.printf("# settle sweep at row %d col %d\n# us,value\n", r, c);
  uint8_t bank = (c >= cfg.chans) ? 1 : 0;
  uint8_t k    = c % cfg.chans;
  for (uint16_t us = 1; us <= 2000; us = (us < 20) ? (uint16_t)(us + 1)
                                                   : (uint16_t)(us * 1.4f)) {
    selectRow(r);
    delayMicroseconds(cfg.rowSettleUs);
    selectChan(k);
    delayMicroseconds(us);
    uint32_t acc = 0;
    for (int i = 0; i < 16; i++) acc += readAdc(bank);
    Serial.printf("%u,%lu\n", us, (unsigned long)(acc / 16));
  }
  selectRow(-1);
}

/*
 * Per-taxel noise characterisation.  n <secs>
 *
 * A single global threshold is either deaf in one corner of the array or
 * chattering in another: mux on-resistance, trace length and electrode
 * geometry all differ across it. This measures sigma where it actually
 * matters - on the filtered output, with the pipeline configured exactly as it
 * will run - and stores it so stage 7 can threshold per taxel.
 *
 * Run it with the mat at rest and, ideally, with the arm powered and moving.
 * The noise that matters is the noise the sensor will actually see in service,
 * not the noise it has on a quiet bench.
 */
static void characterise(char *cmd) {
  static double sum[MAX_TAXELS], sumsq[MAX_TAXELS];
  int secs = 10;
  sscanf(cmd + 1, "%d", &secs);
  secs = constrain(secs, 1, 300);
  needIdle();

  const float dt = cfg.framePeriodUs / 1e6f;
  const uint32_t n = (uint32_t)(secs / dt);
  Serial.printf("# characterising noise over %d s (%lu frames). Do not touch the mat.\n",
                secs, (unsigned long)n);

  memset(sum, 0, sizeof(sum));
  memset(sumsq, 0, sizeof(sumsq));
  const int nc = nCols();

  // Held to the real frame period, not run flat out. Every filter cutoff scales
  // with dt, so sigma measured at a different cadence is sigma for a sensor that
  // does not exist.
  uint64_t deadline = time_us_64();
  for (uint32_t f = 0; f < n; f++) {
    while (time_us_64() < deadline) tight_loop_contents();
    deadline += cfg.framePeriodUs;
    scanFrame();
    condProcess(&drFrame[0][0], dt);
    for (int r = 0; r < cfg.rows; r++)
      for (int c = 0; c < nc; c++) {
        int i = r * MAX_COLS + c;
        double v = (double)filtMap[i];
        sum[i] += v; sumsq[i] += v * v;
      }
    if ((f % 200) == 0) Serial.printf("# %lu/%lu\n", (unsigned long)f, (unsigned long)n);
  }

  double worst = 0, tot = 0; int wr = 0, wc = 0, cnt = 0;
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nc; c++) {
      int i = r * MAX_COLS + c;
      double mean = sum[i] / n;
      double var  = sumsq[i] / n - mean * mean;
      if (var < 0) var = 0;
      double sd = sqrt(var);
      uint32_t q4 = (uint32_t)(sd * 16.0 + 0.5);
      if (q4 < 8)    q4 = 8;          // floor: never let a threshold collapse
      if (q4 > 4095) q4 = 4095;
      sigmaQ4[i] = (uint16_t)q4;
      tot += sd; cnt++;
      if (sd > worst) { worst = sd; wr = r; wc = c; }
    }

  Serial.printf("# sigma: mean %.2f counts, worst %.2f at r%d c%d\n",
                tot / cnt, worst, wr, wc);

  // The worst offenders by name, because "mean sigma 2.1" hides the one column
  // that is actually causing the trouble. Selection sort over a scratch copy so
  // the stored table is untouched.
  static uint16_t scratch[MAX_TAXELS];
  memcpy(scratch, sigmaQ4, sizeof(scratch));
  Serial.println(F("# worst 16 taxels:"));
  for (int pass = 0; pass < 16; pass++) {
    int bi = -1; uint16_t bv = 0;
    for (int r = 0; r < cfg.rows; r++)
      for (int c = 0; c < nc; c++) {
        int i = r * MAX_COLS + c;
        if (scratch[i] > bv) { bv = scratch[i]; bi = i; }
      }
    if (bi < 0) break;
    Serial.printf("#   r%-2d c%-2d  sigma %.2f\n",
                  bi / MAX_COLS, bi % MAX_COLS, bv / 16.0f);
    scratch[bi] = 0;
  }

  sigmaCharacterised = true;
  condCalSave();
  Serial.printf("# thresholds now on %ux sigma / %ux sigma, floors %u / %u. Saved to flash.\n",
                cc.kOn, cc.kOff, cc.minOn, cc.minOff);
}

/*
 * Raw sample dump for spectrum analysis.  v <r> <c> [n] [kHz]
 *
 * There is NO anti-alias filter anywhere in this signal chain - the netlist
 * confirms the TLV9062 output goes straight to the ADC pin, no series resistor,
 * no capacitor. Per-taxel sample rate is the frame rate, so motor PWM, servo
 * loops and switching supplies all fold down into the passband and look like a
 * light touch that wanders around.
 *
 * Guessing which one is doing it is a waste of a day. This parks one taxel,
 * samples the ADC at a known fixed rate through the FIFO, and dumps the raw
 * series for tools/spectrum.py to transform. Run it with the arm powered and
 * moving; the interference lines are simply visible.
 *
 * Once a line is identified, set oversample * spreadUs = 1/f and the dwell
 * boxcar nulls it exactly.
 */
static uint16_t dumpBuf[4096];

static void spectrumDump(char *cmd) {
  int r = 0, c = 0, n = 2048, khz = 48;
  sscanf(cmd + 1, "%d %d %d %d", &r, &c, &n, &khz);
  needIdle();
  r = constrain(r, 0, cfg.rows - 1);
  c = constrain(c, 0, nCols() - 1);
  n = constrain(n, 64, (int)(sizeof(dumpBuf) / 2));
  khz = constrain(khz, 1, 500);

  uint8_t bank = (c >= cfg.chans) ? 1 : 0;
  uint8_t k    = (uint8_t)(c % cfg.chans);

  selectRow(r);
  delayMicroseconds(cfg.rowSettleUs);
  selectChan(k);
  delayMicroseconds(500);

  // The ADC block runs from a 48 MHz clock; fs = 48e6 / (div + 1).
  uint32_t div = (uint32_t)(48000.0f / (float)khz) - 1;
  adc_select_input(bank);
  adc_fifo_setup(true, false, 0, false, false);
  adc_set_clkdiv((float)div);
  adc_fifo_drain();
  adc_run(true);
  for (int i = 0; i < n; i++) {
    while (adc_fifo_is_empty()) tight_loop_contents();
    dumpBuf[i] = adc_fifo_get();
  }
  adc_run(false);
  adc_fifo_drain();
  adc_fifo_setup(false, false, 0, false, false);
  adc_set_clkdiv(0);
  selectRow(-1);

  float fs = 48000000.0f / (float)(div + 1);
  Serial.printf("# spectrum r%d c%d bank %u ch %u  n=%d  fs=%.1f Hz\n",
                r, c, bank, k, n, fs);
  for (int i = 0; i < n; i++) {
    Serial.print(dumpBuf[i]);
    Serial.print((i % 16 == 15 || i == n - 1) ? '\n' : ',');
  }
  Serial.println(F("# --- end spectrum ---"));
}

static void baselineDump() {
  needIdle();
  Serial.println(F("# --- baseline map (counts) ---"));
  Serial.print("     ");
  for (int c = 0; c < nCols(); c++) Serial.printf("%6d", c);
  Serial.println();
  for (int r = 0; r < cfg.rows; r++) {
    Serial.printf("r%-3d ", r);
    for (int c = 0; c < nCols(); c++) Serial.printf("%6ld", (long)condBaseline(0, r, c));
    Serial.println();
  }
  Serial.println(F("# --- sigma map (counts) ---"));
  for (int r = 0; r < cfg.rows; r++) {
    Serial.printf("r%-3d ", r);
    for (int c = 0; c < nCols(); c++)
      Serial.printf("%6.2f", sigmaQ4[r * MAX_COLS + c] / 16.0f);
    Serial.println();
  }
}

// ---------------------------------------------------------------- options
static const Param params[] = {
  { "period",   P_U32,  &cfg.framePeriodUs, 2000, 200000, "frame period us (fixed cadence)" },
  { "ovs",      P_U8,   &cfg.oversample,    1,    32,     "ADC samples per taxel" },
  { "spread",   P_U8,   &cfg.spreadUs,      0,    200,    "us between them: null at 1/(ovs*spread)" },
  { "trim",     P_BOOL, &cfg.trim,          0,    1,      "drop min+max before averaging" },
  { "discard",  P_BOOL, &cfg.adcDiscard,    0,    1,      "throw a conversion after a channel switch" },
  { "maskirq",  P_BOOL, &cfg.maskIrq,       0,    1,      "IRQs off across the sample burst" },
  { "darkref",  P_BOOL, &cfg.darkRef,       0,    1,      "subtract the all-rows-low reference" },
  { "darkshift",P_U8,   &cfg.darkShift,     0,    8,      "dark EMA alpha = 1/2^n" },

  { "raw",      P_U8,   &cfg.rawLevel,      0,    2,      "0 pipeline, 1 no conditioning, 2 also no dark ref" },
  { "autorun",  P_BOOL, &cfg.autoRun,       0,    1,      "sense + pixel from boot, no host needed" },
  { "autoemit", P_BOOL, &cfg.autoEmit,      0,    1,      "also stream frames from boot" },
  { "cond",     P_BOOL, &cc.enable,         0,    1,      "master: pipeline on/off" },
  { "fall",     P_U16,  &cc.fallRate,       0,    2000,   "baseline fall, counts/s (ungated)" },
  { "rise",     P_U16,  &cc.riseRate,       0,    2000,   "baseline rise, counts/s (gated)" },
  { "release",  P_U16,  &cc.releaseRate,    0,    2000,   "baseline rise for a released taxel" },
  { "idleband", P_U16,  &cc.idleBand,       0,    4095,   "only track up below this delta" },
  { "maxdrift", P_U16,  &cc.maxDrift,       0,    4095,   "hard cap above the boot tare" },
  { "halo",     P_BOOL, &cc.haloFreeze,     0,    1,      "freeze neighbours of active taxels" },
  { "relen",    P_BOOL, &cc.release,        0,    1,      "stuck-active release enabled" },
  { "stucksecs",P_U16,  &cc.stuckSecs,      1,    3600,   "active+static time before release" },
  { "stillness",P_U16,  &cc.stillness,      0,    4095,   "varEma below this = nothing moving" },
  { "coherent", P_U8,   &cc.coherentArea,   1,    255,    "blobs this big are NEVER released" },

  { "kon",      P_U8,   &cc.kOn,            1,    64,     "on threshold, sigma multiples" },
  { "koff",     P_U8,   &cc.kOff,           1,    64,     "off threshold, sigma multiples" },
  { "minon",    P_U16,  &cc.minOn,          0,    4095,   "on threshold floor, counts" },
  { "minoff",   P_U16,  &cc.minOff,         0,    4095,   "off threshold floor, counts" },
  { "non",      P_U8,   &cc.nOn,            1,    64,     "frames above on before latching" },
  { "noff",     P_U8,   &cc.nOff,           1,    64,     "frames below off before releasing" },

  { "median",   P_BOOL, &cc.median,         0,    1,      "temporal median-of-3" },
  { "euro",     P_BOOL, &cc.euro,           0,    1,      "one-euro filter" },
  { "fcmin",    P_F32,  &cc.fcMin,          0.01f,50.0f,  "one-euro cutoff at rest, Hz" },
  { "beta",     P_F32,  &cc.beta,           0.0f, 10.0f,  "one-euro speed coefficient" },
  { "dcut",     P_F32,  &cc.dCutoff,        0.01f,50.0f,  "one-euro derivative cutoff, Hz" },

  { "despeckle",P_BOOL, &cc.despeckle,      0,    1,      "remove isolated active taxels" },
  { "minarea",  P_U8,   &cc.minArea,        1,    255,    "min taxels for a real contact" },
  { "minsum",   P_U32,  &cc.minSum,         0,    1000000,"min summed delta for a real contact" },
  { "gate",     P_BOOL, &cc.gateMap,        0,    1,      "zero the map outside accepted contacts" },
  { "led",      P_BOOL, &ledEnable,         0,    1,      "pixel enabled at all" },
  { "ledbright",P_U8,   &ledBright,         0,    255,    "pixel brightness, raw 0..255" },
  { "ledfull",  P_U16,  &ledFull,           10,   4095,   "counts that map to full red" },
};
static const int NPARAM = sizeof(params) / sizeof(params[0]);

static void paramPrint(const Param &p) {
  switch (p.t) {
    case P_U8:   Serial.printf("%-10s %-10u  %s\n", p.name, *(uint8_t*)p.p, p.help); break;
    case P_U16:  Serial.printf("%-10s %-10u  %s\n", p.name, *(uint16_t*)p.p, p.help); break;
    case P_U32:  Serial.printf("%-10s %-10ld  %s\n", p.name, (long)*(int32_t*)p.p, p.help); break;
    case P_F32:  Serial.printf("%-10s %-10.3f  %s\n", p.name, *(float*)p.p, p.help); break;
    case P_BOOL: Serial.printf("%-10s %-10s  %s\n", p.name, *(bool*)p.p ? "on" : "off", p.help); break;
  }
}

/*
 * Sensitivity presets. These move the acceptance floors as a group, because
 * moving one of them alone usually does not do what you expect: `minon` decides
 * which taxels count as active, while `minarea` and `minsum` decide what counts
 * as a contact, and a light touch can fail either test.
 *
 * For scale, measured noise on a quiet bench is about 0.5 counts of sigma after
 * filtering, so even level 3 sits several sigma clear of it. The real limit is
 * not electrical noise, it is drift and creep residue, which is why the higher
 * levels get riskier in service rather than on the bench.
 */
static void sensPreset(int lvl) {
  switch (lvl) {
    case 0:   // conservative, the original shipping values
      cc.minOn = 25; cc.minOff = 12; cc.minSum = 120; cc.minArea = 2;
      cc.nOn = 3; cc.despeckle = true; break;
    case 1:   // default
      cc.minOn = 10; cc.minOff = 5;  cc.minSum = 40;  cc.minArea = 2;
      cc.nOn = 3; cc.despeckle = true; break;
    case 2:   // sensitive
      cc.minOn = 5;  cc.minOff = 2;  cc.minSum = 15;  cc.minArea = 2;
      cc.nOn = 2; cc.despeckle = true; break;
    case 3:   // maximum: single taxels accepted, speck removal off
      cc.minOn = 3;  cc.minOff = 1;  cc.minSum = 5;   cc.minArea = 1;
      cc.nOn = 1; cc.despeckle = false; break;
    default: Serial.println(F("# usage: o sens 0|1|2|3")); return;
  }
  Serial.printf("# sensitivity %d: minon %u minoff %u minsum %ld minarea %u "
                "non %u despeckle %s\n", lvl, cc.minOn, cc.minOff,
                (long)cc.minSum, cc.minArea, cc.nOn, cc.despeckle ? "on" : "off");
  if (lvl >= 3)
    Serial.println(F("# level 3 accepts single taxels and disables speck removal.\n"
                     "# Expect phantoms to return. Soak with logging before trusting it."));
}

static void options(char *cmd) {
  char name[24] = {0};
  float val = 0;
  int got = sscanf(cmd + 1, "%23s %f", name, &val);
  if (got < 1) {
    Serial.println(F("# name       value       meaning"));
    for (int i = 0; i < NPARAM; i++) { Serial.print("# "); paramPrint(params[i]); }
    Serial.println(F("# set with: o <name> <value>"));
    Serial.println(F("# o sens 0|1|2|3   acceptance preset, 0 strictest, 3 most sensitive"));
    return;
  }
  if (!strcasecmp(name, "sens")) { sensPreset(got >= 2 ? (int)val : -1); return; }

  for (int i = 0; i < NPARAM; i++) {
    if (strcasecmp(name, params[i].name)) continue;
    const Param &p = params[i];
    if (got < 2) { Serial.print("# "); paramPrint(p); return; }
    if (val < p.lo) val = p.lo;
    if (val > p.hi) val = p.hi;
    switch (p.t) {
      case P_U8:   *(uint8_t*)p.p  = (uint8_t)val;  break;
      case P_U16:  *(uint16_t*)p.p = (uint16_t)val; break;
      case P_U32:  *(int32_t*)p.p  = (int32_t)val;  break;
      case P_F32:  *(float*)p.p    = val;           break;
      case P_BOOL: *(bool*)p.p     = (val != 0);    break;
    }
    Serial.print("# "); paramPrint(p);
    return;
  }
  Serial.printf("# no such option: %s  (bare 'o' lists them)\n", name);
}

// ---------------------------------------------------------------- console
static void help() {
  Serial.println(F(
    "TaxelScan\n"
    "  f            one frame\n"
    "  c / x        start / stop continuous\n"
    "  t            tare (sets the baseline and the drift cap)\n"
    "  z            toggle map gating (off = see everything the filter rejected)\n"
    "  g R C B      geometry: rows, mux channels, banks(1|2)\n"
    "  s <us>       sense settle time\n"
    "  w <us>       row settle time\n"
    "  m <0|1|2>    output: 0 text, 1 csv, 2 binary v2\n"
    "  o [nm] [v]   list or set a pipeline option\n"
    "  n <secs>     characterise per-taxel noise, save sigma to flash\n"
    "  v r c [n] [kHz]  raw ADC dump on one taxel, for spectrum.py\n"
    "  B            dump the baseline and sigma maps\n"
    "  X            save the current tare as the boot reference (clear the mat first)\n"
    "  R            reset the pipeline state (filters, baseline, contacts)\n"
    "  --- bring-up, all of these stop streaming first ---\n"
    "  d            diagnostics\n"
    "  y            connectivity sweep - finds open electrodes, no press\n"
    "  q            row independence probe\n"
    "  b a|n|<row>  bit-bang the 595s\n"
    "  u            ROW_VCC rail monitor (wire D2 to R5)\n"
    "  e <row>      row output walk (wire D2 to one row pin)\n"
    "  k a|n|<row>  park row drive for probing with a meter\n"
    "  p <r> <c>    settle-time sweep on one taxel\n"
    "  T            time one frame against the fixed cadence\n"
    "  i            show config"));
}

static void showCfg() {
  Serial.printf("# rows=%u chans=%u banks=%u -> %u taxels, %s\n",
                cfg.rows, cfg.chans, cfg.banks, cfg.rows * nCols(),
                cfg.darkRef ? "dark-referenced" : "RAW (no dark reference)");
  Serial.printf("# settle=%uus rowSettle=%uus ovs=%u spread=%uus period=%luus\n",
                cfg.settleUs, cfg.rowSettleUs, cfg.oversample, cfg.spreadUs,
                (unsigned long)cfg.framePeriodUs);
  // Easy to forget you left it bypassed, and a raw stream can look plausible.
  if (cfg.rawLevel)
    Serial.printf("# *** RAW MODE %u: conditioning bypassed%s. 'o raw 0' to restore ***\n",
                  cfg.rawLevel, cfg.rawLevel >= 2 ? ", dark reference off" : "");
  Serial.printf("# pipeline %s, gate %s, sigma %s, mode %u\n",
                cc.enable ? "on" : "OFF", cc.gateMap ? "on" : "off",
                sigmaCharacterised ? "characterised" : "DEFAULT (run 'n')", cfg.mode);
  Serial.printf("# acceptance: minon %u minoff %u minarea %u minsum %ld (o sens 0-3)\n",
                cc.minOn, cc.minOff, cc.minArea, (long)cc.minSum);
  Serial.printf("# last scan %lu us, period %lu us, overruns %lu\n",
                (unsigned long)telem.scanUs, (unsigned long)telem.periodUs,
                (unsigned long)telem.overruns);
}

static void handle(char *s) {
  int a, b, c;
  switch (s[0]) {
    case 'f': {
      needIdle();
      uint32_t t0 = micros();
      scanFrame();
      condProcess(&drFrame[0][0], cfg.framePeriodUs / 1e6f);
      emit(micros() - t0);
      ledUpdate(gatedPeak());
    } break;
    case 'c': streaming = true;  scanResume(); break;
    case 'x':
      streaming = false; resumeAfterCmd = false;
      if (!cfg.autoRun) scanPause();
      else Serial.println(F("# emission stopped; sensing and the pixel keep running "
                            "(o autorun 0 to stop those too)"));
      break;
    case 't': tare(); break;
    case 'z': cc.gateMap = !cc.gateMap; showCfg(); break;
    case 'R': needIdle(); condReset(); Serial.println(F("# pipeline state cleared")); break;
    case 'd': diagnose(); break;
    case 'y': connectivity(); break;
    case 'q': rowIndependence(); break;
    case 'b': bitbangRows(s); break;
    case 'u': railMonitor(); break;
    case 'e': rowOutputWalk(s); break;
    case 'k': parkRows(s); break;
    case 'n': characterise(s); break;
    case 'v': spectrumDump(s); break;
    case 'B': baselineDump(); break;
    case 'X':
      needIdle();
      tare(32, 8);
      condRefAdopt();
      tareSuspect = false;
      Serial.println(F("# current tare saved as the boot reference; future startups "
                       "compare against it"));
      break;
    case 'o': options(s); break;
    case 'i': showCfg(); break;
    case 'T': reportTiming(); break;
    case '?':
    case 'h': help(); break;
    case 'g':
      if (sscanf(s + 1, "%d %d %d", &a, &b, &c) == 3) {
        needIdle();
        cfg.rows  = constrain(a, 1, MAX_ROWS);
        cfg.chans = constrain(b, 1, MAX_CHANS);
        cfg.banks = constrain(c, 1, MAX_BANKS);
        scanResetDark();          // channel k is a different electrode now
        condReset();
        Serial.println(F("# geometry changed - pipeline state cleared, re-tare with 't'"));
        showCfg();
        reportTiming();
      }
      break;
    case 's': if (sscanf(s + 1, "%d", &a) == 1) { cfg.settleUs    = constrain(a, 0, 20000); showCfg(); } break;
    case 'w': if (sscanf(s + 1, "%d", &a) == 1) { cfg.rowSettleUs = constrain(a, 0, 20000); showCfg(); } break;
    case 'm': if (sscanf(s + 1, "%d", &a) == 1) { cfg.mode        = constrain(a, 0, 2);     showCfg(); } break;
    case 'p':
      if (sscanf(s + 1, "%d %d", &a, &b) == 2)
        sweepSettle(constrain(a, 0, cfg.rows - 1), constrain(b, 0, nCols() - 1));
      break;
    default: break;
  }
  resumeIfPaused(s[0]);
}

// ---------------------------------------------------------------- setup/loop
void setup() {
  Serial.begin(115200);

  pinMode(LED_BUILTIN, OUTPUT);
  LED_OFF();                       // park the yellow LED; RGB is the indicator

  pinMode(PIN_RGB_PWR, OUTPUT);
  digitalWrite(PIN_RGB_PWR, HIGH); // powers the pixel AND the level shifter
  delay(2);
  pixel.begin();
  pixel.setBrightness(255);   // scaling happens in ledWrite, not here
  pixel.clear();
  pixel.show();

  scanInit();
  condInit();
  sigmaCharacterised = condCalLoad();     // survives a reboot; the flags must too

  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 2000) { }
  Serial.println(F("# TaxelScan ready (pipeline build)"));
  showCfg();
  if (!sigmaCharacterised)
    Serial.println(F("# sigma not characterised: run 'n 30' with the mat at rest"));
  Serial.println(F("# '?' for help, 'd' for bring-up diagnostics"));

  // Take the resting level of every taxel as the reference, every boot. Without
  // this the baseline was seeded lazily from a SINGLE frame, and that frame
  // landed before the dark reference had converged - so zero was defined against
  // a number that was mostly the electronics' own offset.
  Serial.println(F("# taking startup reference..."));
  tare(32, 8);
  checkTareLoaded();
  if (condRefValid()) Serial.println(F("# stored reference in use ('X' re-records it)"));
  else Serial.println(F("# no stored reference yet: clear the mat and press 'X' once"));

  sysReady = true;
  ledWrite(tareSuspect ? LED_SUSPECT : LED_IDLE, 0);

  if (cfg.autoRun) {
    scanResume();
    streaming = cfg.autoEmit;
    Serial.printf("# running standalone: sensing and status pixel live, "
                  "emission %s ('c' starts it)\n", cfg.autoEmit ? "on" : "off");
  }
}

void setup1() {
  while (!sysReady) delay(1);
}

void loop1() {
  scanCore1Loop();
}

void loop() {
  static char buf[64];
  static uint8_t n = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') { buf[n] = 0; if (n) handle(buf); n = 0; }
    else if (n < sizeof(buf) - 1)  buf[n++] = ch;
  }

  /*
   * Sensing runs whenever core 1 is scanning, which with cfg.autoRun is from
   * boot onward with no host involved. Emission is a separate decision. That
   * split is what lets the board be a standalone sensor: the pipeline runs, the
   * contact list is current and the status pixel tracks pressure whether or not
   * anything is listening on USB.
   */
  if (scanRun && scanCopyLatest(&work[0][0])) {
    float dt = telem.periodUs ? telem.periodUs / 1e6f : cfg.framePeriodUs / 1e6f;
    condProcess(&work[0][0], dt);
    if (streaming) {
      uint32_t e0 = micros();
      emit(telem.periodUs);
      lastEmitUs = micros() - e0;
    } else {
      lastEmitUs = 0;
    }
    ledUpdate(gatedPeak());
  }
}
