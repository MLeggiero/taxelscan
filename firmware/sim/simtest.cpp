/*
 * simtest.cpp - run the SHIPPED conditioning pipeline against synthetic frames.
 *
 *     build.cmd && sim.exe
 *
 * condition.cpp is compiled unmodified here, against the shims in sim/shim.
 * That matters: a Python model of the baseline logic would only tell you
 * whether the model is right. This exercises the code that goes on the board.
 *
 * The question it exists to answer is the one that motivated the whole design:
 * an adaptive baseline that merely freezes while a taxel is active will
 * eventually eat a static grasp, and on a robot arm that is a safety defect.
 * Test A holds a real contact for half an hour and checks it does not decay.
 * Tests B and C check the phantoms still get removed, so the guarantees have
 * not simply been bought by disabling the cleanup.
 *
 * What this CANNOT tell you: anything about the actual mat, the actual noise,
 * or the actual interference. Velostat creep here is a modelled exponential,
 * not a measured one. Use it to check the logic and to pick starting
 * parameters; use the hardware protocol in README.md to check the sensor.
 */

#include "condition.h"
#include <EEPROM.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdarg.h>

// ---------------------------------------------------------------- shims
RP2040Shim rp2040;
EEPROMShim EEPROM;
static sio_hw_t sio_dummy;
sio_hw_t *sio_hw = &sio_dummy;
Config cfg;

// ---------------------------------------------------------------- scene
static float   rest[MAX_TAXELS];
static float   load[MAX_TAXELS];
static int16_t dr  [MAX_TAXELS];
static float   NOISE = 2.5f;          // counts rms, per the bring-up floor
static const float DT = 0.025f;
static const int   FPS = 40;

static uint32_t rng = 0x13579BDFu;
static float uniform() {
  rng = rng * 1664525u + 1013904223u;
  return (float)((rng >> 8) & 0xFFFFFF) / 16777216.0f;
}
static float gauss() {               // Box-Muller, good enough and deterministic
  float u1 = uniform() + 1e-7f, u2 = uniform();
  return sqrtf(-2.0f * logf(u1)) * cosf(6.2831853f * u2);
}

static void sceneInit() {
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nCols(); c++) {
      int i = r * MAX_COLS + c;
      // A connected but untouched mat is not at zero: ~32 sneak paths in
      // parallel put every column at tens of counts, and it varies per channel.
      rest[i] = 25.0f + (float)((c * 7 + r) % 11);
      load[i] = 0.0f;
    }
}

static void clearLoad() { memset(load, 0, sizeof(load)); }

static void addBlob(float cr, float cc, float radius, float amp) {
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nCols(); c++) {
      float d2 = (r - cr) * (r - cr) + (c - cc) * (c - cc);
      float v = amp * expf(-d2 / (2.0f * radius * radius));
      if (v > 0.5f) load[r * MAX_COLS + c] += v;
    }
}

static void makeFrame(float scale = 1.0f) {
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nCols(); c++) {
      int i = r * MAX_COLS + c;
      float v = rest[i] + load[i] * scale + gauss() * NOISE;
      if (v < 0) v = 0;
      if (v > 4095) v = 4095;
      dr[i] = (int16_t)lrintf(v);
    }
}

static void simTare(int n = 16) {
  static double acc[MAX_TAXELS];
  memset(acc, 0, sizeof(acc));
  for (int f = 0; f < n; f++) {
    makeFrame();
    for (int i = 0; i < MAX_TAXELS; i++) acc[i] += dr[i];
  }
  static int16_t avg[MAX_TAXELS];
  for (int i = 0; i < MAX_TAXELS; i++) avg[i] = (int16_t)(acc[i] / n);
  condSeedBaseline(avg);
}

// ---------------------------------------------------------------- readout
static int acceptedCount() {
  int n = 0;
  for (int i = 0; i < nContacts; i++)
    if (contacts[i].flags & CF_ACCEPTED) n++;
  return n;
}
static int32_t bestSum() {
  int32_t best = 0;
  for (int i = 0; i < nContacts; i++)
    if ((contacts[i].flags & CF_ACCEPTED) && contacts[i].sum > best)
      best = contacts[i].sum;
  return best;
}
static int bestArea() {
  int32_t best = 0; int area = 0;
  for (int i = 0; i < nContacts; i++)
    if ((contacts[i].flags & CF_ACCEPTED) && contacts[i].sum > best) {
      best = contacts[i].sum; area = contacts[i].area;
    }
  return area;
}
static int mapMax() {
  int m = 0;
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nCols(); c++) {
      int v = outMap[r * MAX_COLS + c];
      if (v > m) m = v;
    }
  return m;
}
static int mapMin() {
  int m = 0;
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nCols(); c++) {
      int v = filtMap[r * MAX_COLS + c];
      if (v < m) m = v;
    }
  return m;
}

static int failures = 0;
static void check(const char *name, bool ok, const char *fmt = 0, ...) {
  printf("  %-4s %s", ok ? "ok" : "FAIL", name);
  if (fmt) {
    va_list ap; va_start(ap, fmt);
    printf("  ->  "); vprintf(fmt, ap); va_end(ap);
  }
  printf("\n");
  if (!ok) failures++;
}

static void reset() {
  condReset();
  clearLoad();
  simTare();
}

// ---------------------------------------------------------------- tests
static void testSustained(int minutes) {
  printf("\nA. a real contact held for %d minutes must not be eroded\n", minutes);
  reset();
  addBlob(16.0f, 6.0f, 1.6f, 620.0f);       // ~16 taxels above threshold

  const int total = minutes * 60 * FPS;
  const int settle = 5 * FPS;
  int32_t sum0 = 0, minSum = INT32_MAX, minArea = 9999, dead = 0;
  int firstArea = 0;

  for (int f = 0; f < total; f++) {
    makeFrame();
    condProcess(dr, DT);
    if (f == settle) { sum0 = bestSum(); firstArea = bestArea(); }
    if (f > settle) {
      int32_t s = bestSum();
      if (s < minSum) minSum = s;
      int a = bestArea();
      if (a < minArea) minArea = a;
      if (acceptedCount() == 0) dead++;
    }
  }
  printf("     initial sum %ld over %d taxels; worst seen %ld over %d\n",
         (long)sum0, firstArea, (long)minSum, minArea);
  check("contact is reported in every frame", dead == 0, "%d frames with none", dead);
  check("total force never fell below 95 percent of initial",
        minSum > sum0 * 95 / 100, "%ld vs %ld", (long)minSum, (long)sum0 * 95 / 100);
  check("area never shrank", minArea >= firstArea, "%d vs %d", minArea, firstArea);
  check("no taxel hit the drift cap", ctel.capped == 0, "%u capped", ctel.capped);
}

// One stuck offset of a given size. Returns the area it occupied, and how many
// seconds until the map was clean again (-1 = never, within the window).
static float phantomRun(float radius, float amp, int secs, int *areaOut) {
  reset();
  addBlob(8.0f, 4.0f, radius, amp);
  int firstSeen = -1, cleared = -1, area = 0;
  for (int f = 0; f < secs * FPS; f++) {
    makeFrame();
    condProcess(dr, DT);
    if (firstSeen < 0 && ctel.activeCells > 0) {
      firstSeen = f;
    }
    if (f == firstSeen + 4 * FPS) area = (int)ctel.activeCells;
    if (firstSeen >= 0 && cleared < 0 && f > firstSeen + 5 * FPS &&
        acceptedCount() == 0 && mapMax() == 0)
      cleared = f;
  }
  *areaOut = area;
  return cleared < 0 ? -1.0f : (cleared - firstSeen) / (float)FPS;
}

/*
 * The boundary matters more than any single pass/fail: it is the line between
 * "cleaned up automatically" and "held until it physically decays", and it is
 * set by `o coherent`. Guarantee 2 refuses to walk back a blob big enough to be
 * a real contact, so this sweep is the honest statement of what that costs.
 */
static void testPhantomSizes() {
  printf("\nB. where is the line between a phantom and a contact?\n");
  printf("     (a stuck offset of each size; `coherent` is %u taxels)\n",
         cc.coherentArea);
  printf("     %-8s %-6s %s\n", "radius", "area", "cleared after");

  struct { float rad, amp; } cases[] = {
    {0.40f, 200.0f}, {0.55f, 220.0f}, {0.75f, 220.0f},
    {1.00f, 240.0f}, {1.60f, 300.0f},
  };
  int clearedBelow = 0, heldAtOrAbove = 0, tested = 0;
  for (auto &k : cases) {
    int area = 0;
    float t = phantomRun(k.rad, k.amp, 200, &area);
    char when[40];
    if (area == 0)   snprintf(when, sizeof(when), "never detected (despeckled)");
    else if (t < 0)  snprintf(when, sizeof(when), "never - held as a contact");
    else             snprintf(when, sizeof(when), "%.0f s", t);
    printf("     %-8.2f %-6d %s\n", k.rad, area, when);
    tested++;
    if (area > 0 && area < cc.coherentArea) { if (t >= 0) clearedBelow++; }
    else if (area >= cc.coherentArea)       { if (t < 0)  heldAtOrAbove++; }
  }
  check("every phantom smaller than `coherent` is walked back",
        clearedBelow > 0, "%d of the sub-coherent cases cleared", clearedBelow);
  check("every blob at or above `coherent` is held, as designed",
        heldAtOrAbove > 0, "%d held", heldAtOrAbove);
  printf("     A phantom below the line clears in about %u s of stuck timer plus\n"
         "     the walk-back at %u counts/s. Above it, nothing is walked back -\n"
         "     that is deliberate, and it is what stops a real grasp being eroded.\n"
         "     Lower `o coherent` to clean up more aggressively, at the cost of\n"
         "     eventually absorbing genuinely small sustained contacts.\n",
         cc.stuckSecs, cc.releaseRate);
}

static void testSpeck() {
  printf("\nC. a single-taxel phantom must never be reported at all\n");
  reset();
  load[10 * MAX_COLS + 5] = 400.0f;         // one taxel, hard

  int reported = 0, mapped = 0;
  for (int f = 0; f < 60 * FPS; f++) {
    makeFrame();
    condProcess(dr, DT);
    if (acceptedCount() > 0) reported++;
    if (mapMax() > 0) mapped++;
  }
  check("never becomes an accepted contact", reported == 0, "%d frames", reported);
  check("never appears in the gated map", mapped == 0, "%d frames", mapped);
  printf("     stage 9 (despeckle) removes it before it can ever form a blob\n");
}

static void testCreep() {
  printf("\nD. Velostat creep tail after a real press (tau = 20 s)\n");
  reset();
  addBlob(16.0f, 6.0f, 1.6f, 800.0f);

  int f = 0;
  for (; f < 3 * FPS; f++) { makeFrame(1.0f); condProcess(dr, DT); }
  bool pressed = acceptedCount() > 0;

  int cleared = -1;
  const int release = f;
  for (; f < 300 * FPS; f++) {
    float t = (f - release) / (float)FPS;
    makeFrame(expf(-t / 20.0f));             // the film relaxing, not a hand
    condProcess(dr, DT);
    if (cleared < 0 && acceptedCount() == 0 && mapMax() == 0) cleared = f;
  }
  check("the press itself was detected", pressed);
  check("the tail clears within 300 s", cleared > 0);
  if (cleared > 0)
    printf("     map clears %.0f s after release\n", (cleared - release) / (float)FPS);
  printf("     NOTE: a large-area residual is held until it decays below the off\n"
         "     threshold - guarantee 2 will not release a coherent blob, by design.\n"
         "     That is the price of never eroding a real grasp. Small residuals,\n"
         "     which is what the reported symptom actually was, go via B and C.\n");
}

static void testFalsePositives(int minutes) {
  printf("\nE. %d minutes of nothing but noise: false positives\n", minutes);
  reset();
  int frames = minutes * 60 * FPS, bad = 0, specks = 0;
  for (int f = 0; f < frames; f++) {
    makeFrame();
    condProcess(dr, DT);
    if (acceptedCount() > 0) bad++;
    specks += ctel.suppressed;
  }
  check("zero frames report a contact", bad == 0, "%d of %d frames", bad, frames);
  printf("     %d isolated specks were suppressed along the way\n", specks);
}

static void testBaselineTooHigh() {
  printf("\nF. baseline tared while loaded, then unloaded (the dangerous case)\n");
  condReset();
  clearLoad();
  addBlob(16.0f, 6.0f, 1.6f, 300.0f);
  simTare();                                 // tare WITH the load present
  clearLoad();                               // then take it away

  int worstNeg = 0, recovered = -1;
  for (int f = 0; f < 60 * FPS; f++) {
    makeFrame();
    condProcess(dr, DT);
    int mn = mapMin();
    if (mn < worstNeg) worstNeg = mn;
    if (recovered < 0 && f > 2 * FPS && mapMin() > -(int)cc.minOff) recovered = f;
  }
  check("the deficit is visible as negative values, not clamped away",
        worstNeg < -50, "worst %d counts", worstNeg);
  check("fast ungated fall recovers it", recovered > 0, "%.1f s",
        recovered / (float)FPS);
  printf("     %d counts at %u counts/s is the recovery bound\n",
         -worstNeg, cc.fallRate);
}

/*
 * The startup tare is blind: whatever is on the mat at boot becomes zero. These
 * are the two ways the firmware notices, and the point of testing both is that
 * they fail differently - the shape test cannot see a load spread evenly over
 * the whole mat, and the reference test can.
 */
static void testStartupReference() {
  printf("\nG. detecting a mat that was already loaded at startup\n");

  // --- no stored reference: the shape test -------------------------------
  condReset();
  clearLoad();
  addBlob(16.0f, 6.0f, 1.6f, 400.0f);
  simTare();                                  // tare WITH a hand on the mat
  int worst = 0, wi = -1, med = 0;
  int n = condTareOutliers(cc.maxDrift, &worst, &wi, &med);
  check("shape test flags a localised load", n > 0,
        "%d taxels, worst %+d over median %d at r%d c%d",
        n, worst, med, wi / MAX_COLS, wi % MAX_COLS);

  // --- clear mat: must NOT false-alarm ------------------------------------
  condReset();
  clearLoad();
  simTare();
  n = condTareOutliers(cc.maxDrift, &worst, &wi, &med);
  check("clear mat does not false-alarm", n == 0, "%d flagged, worst %+d", n, worst);

  // Record this clear state as the boot reference.
  condRefAdopt();
  check("reference stored", condRefValid());

  // --- the case the shape test CANNOT see --------------------------------
  // A load pressing evenly on everything shifts the whole array, so the median
  // shifts with it and nothing looks like an outlier.
  condReset();
  clearLoad();
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nCols(); c++) load[r * MAX_COLS + c] = 300.0f;
  simTare();

  int nShape = condTareOutliers(cc.maxDrift, &worst, &wi, &med);
  int nRef   = condRefCompare(cc.maxDrift, &worst, &wi);
  printf("     uniform 300-count load: shape test flags %d, reference test flags %d\n",
         nShape, nRef);
  check("shape test is blind to a uniform load, as expected", nShape == 0,
        "%d flagged", nShape);
  check("stored reference catches it", nRef > 0,
        "%d taxels, worst %+d counts", nRef, worst);
  printf("     This is why 'X' is worth pressing once on a clear mat: without a\n"
         "     reference, a load covering the whole pad reads as a normal tare.\n");
}

/*
 * How much does the contact extractor actually cost, and does it gate the frame
 * rate?
 *
 * It cannot gate the SCAN rate by construction: core 1 owns the matrix and runs
 * on a fixed deadline, while condProcess() - baseline, filters, despeckle,
 * connected components, the lot - runs on core 0 against the PREVIOUS frame. The
 * two overlap. Conditioning only starts to matter when it stops fitting inside
 * one frame period, at which point core 0 becomes the new limit.
 *
 * So the number that matters is milliseconds per frame, measured against the
 * period. This times it under three loads, including a deliberately absurd one:
 * thresholds on the floor so noise alone lights up the whole array, which gives
 * the component labeller far more work than any hand ever will.
 */
#include <chrono>

static double timeCond(int frames) {
  auto a = std::chrono::steady_clock::now();
  for (int i = 0; i < frames; i++) { makeFrame(); condProcess(dr, DT); }
  auto b = std::chrono::steady_clock::now();
  return std::chrono::duration<double, std::milli>(b - a).count() / frames;
}

static void bench() {
  printf("\nH. what does the contact extractor cost?\n");
  const int N = 400;

  struct { uint8_t rows, chans, banks; const char *name; } geoms[] = {
    {32, 12, 1, "32x12  384 taxels"},
    {16, 16, 2, "16x32  512 taxels"},
    {32, 16, 2, "32x32 1024 taxels"},
  };

  for (auto &g : geoms) {
    cfg.rows = g.rows; cfg.chans = g.chans; cfg.banks = g.banks;
    sceneInit();

    reset();
    double rest = timeCond(N);
    int restCells = ctel.activeCells;

    reset();
    addBlob(g.rows / 2.0f, nCols() / 2.0f, 2.2f, 700.0f);
    double press = timeCond(N);
    int pressCells = ctel.activeCells, pressCon = nContacts;

    // Worst case: drop the thresholds so the noise floor itself goes active.
    reset();
    clearLoad();
    uint16_t onSave = cc.minOn, offSave = cc.minOff; uint8_t konSave = cc.kOn, nonSave = cc.nOn;
    cc.minOn = 0; cc.minOff = 0; cc.kOn = 1; cc.nOn = 1;
    double storm = timeCond(N);
    int stormCells = ctel.activeCells, stormCon = nContacts;
    cc.minOn = onSave; cc.minOff = offSave; cc.kOn = konSave; cc.nOn = nonSave;

    reset();
    cc.enable = false;
    double bare = timeCond(N);
    cc.enable = true;

    printf("   %s\n", g.name);
    printf("     %-34s %7.3f ms\n", "baseline subtraction only", bare);
    printf("     %-34s %7.3f ms   (%d active, 0 contacts)\n", "full pipeline, mat at rest", rest, restCells);
    printf("     %-34s %7.3f ms   (%d active, %d contacts)\n", "full pipeline, firm press", press, pressCells, pressCon);
    printf("     %-34s %7.3f ms   (%d active, %d contacts)\n", "thresholds on the floor", storm, stormCells, stormCon);
    printf("     blob machinery costs %+.3f ms at a press, %+.3f ms in the storm\n\n",
           press - rest, storm - rest);
  }
  printf("     x86 timings. Ratios transfer to the RP2350; absolutes do not.\n");
}

int main(int argc, char **argv) {
  if (argc > 1 && !strcmp(argv[1], "bench")) {
    condInit();
    cfg.rows = 16; cfg.chans = 16; cfg.banks = 2;
    sceneInit();
    bench();
    return 0;
  }
  int minutes = (argc > 1) ? atoi(argv[1]) : 30;
  condInit();
  sceneInit();

  printf("TaxelScan conditioning pipeline - native simulation\n");
  printf("geometry %ux%u = %u taxels, %d fps, noise %.1f counts rms\n",
         cfg.rows, nCols(), cfg.rows * nCols(), FPS, NOISE);
  printf("fall %u rise %u release %u maxdrift %u stucksecs %u coherent %u\n",
         cc.fallRate, cc.riseRate, cc.releaseRate, cc.maxDrift,
         cc.stuckSecs, cc.coherentArea);
  printf("kon %u koff %u minon %u minoff %u minarea %u minsum %ld\n",
         cc.kOn, cc.kOff, cc.minOn, cc.minOff, cc.minArea, (long)cc.minSum);

  testSustained(minutes);
  testPhantomSizes();
  testSpeck();
  testCreep();
  testFalsePositives(10);
  testBaselineTooHigh();
  testStartupReference();

  printf("\n%s\n", failures ? "SOME CHECKS FAILED" : "all checks passed");
  return failures ? 1 : 0;
}
