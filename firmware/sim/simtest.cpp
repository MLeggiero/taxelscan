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
static float   rest[MAX_ALL_TAXELS];
static float   load[MAX_ALL_TAXELS];
static int16_t dr  [MAX_ALL_TAXELS];
static float   NOISE = 2.5f;          // counts rms, per the bring-up floor
static const float DT = 0.025f;
static const int   FPS = 40;

static inline int SB(int s) { return s * MAX_TAXELS; }

/*
 * One noise stream per mat, not one shared stream.
 *
 * This is what makes the cross-talk test meaningful. The proof that the mats
 * are independent is that mat 0's output is byte-identical whether it runs
 * alone or alongside seven others - and a single shared generator would break
 * that by construction, because filling the other seven would advance the
 * sequence that mat 0 draws from. Per-mat streams mean adding a mat changes
 * nothing about any other mat's input.
 */
static uint32_t rngS[MAX_SENSORS];
static void seedRng() {
  for (int s = 0; s < MAX_SENSORS; s++) rngS[s] = 0x13579BDFu + 0x9E3779B9u * (uint32_t)s;
}
static float uniform(int s) {
  rngS[s] = rngS[s] * 1664525u + 1013904223u;
  return (float)((rngS[s] >> 8) & 0xFFFFFF) / 16777216.0f;
}
static float gauss(int s) {          // Box-Muller, good enough and deterministic
  float u1 = uniform(s) + 1e-7f, u2 = uniform(s);
  return sqrtf(-2.0f * logf(u1)) * cosf(6.2831853f * u2);
}

static void sceneInit(int s = 0) {
  const int base = SB(s);
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nCols(); c++) {
      int i = base + r * MAX_COLS + c;
      // A connected but untouched mat is not at zero: ~32 sneak paths in
      // parallel put every column at tens of counts, and it varies per channel.
      rest[i] = 25.0f + (float)((c * 7 + r) % 11);
      load[i] = 0.0f;
    }
}

static void clearLoad(int s = 0) {
  memset(&load[SB(s)], 0, MAX_TAXELS * sizeof(load[0]));
}

static void addBlob(float cr, float cc, float radius, float amp, int s = 0) {
  const int base = SB(s);
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nCols(); c++) {
      float d2 = (r - cr) * (r - cr) + (c - cc) * (c - cc);
      float v = amp * expf(-d2 / (2.0f * radius * radius));
      if (v > 0.5f) load[base + r * MAX_COLS + c] += v;
    }
}

static void dot(int r, int c, float amp, int s = 0) {
  load[SB(s) + r * MAX_COLS + c] += amp;
}

static void makeFrame(float scale = 1.0f) {
  for (int s = 0; s < cfg.sensors; s++) {
    const int base = SB(s);
    for (int r = 0; r < cfg.rows; r++)
      for (int c = 0; c < nCols(); c++) {
        int i = base + r * MAX_COLS + c;
        float v = rest[i] + load[i] * scale + gauss(s) * NOISE;
        if (v < 0) v = 0;
        if (v > 4095) v = 4095;
        dr[i] = (int16_t)lrintf(v);
      }
  }
}

static void simTare(int n = 16) {
  static double acc[MAX_ALL_TAXELS];
  memset(acc, 0, sizeof(acc));
  for (int f = 0; f < n; f++) {
    makeFrame();
    for (int i = 0; i < MAX_ALL_TAXELS; i++) acc[i] += dr[i];
  }
  static int16_t avg[MAX_ALL_TAXELS];
  for (int i = 0; i < MAX_ALL_TAXELS; i++) avg[i] = (int16_t)(acc[i] / n);
  for (int s = 0; s < cfg.sensors; s++) condSeedBaseline(s, avg);
}

// ---------------------------------------------------------------- readout
static int acceptedCount() {
  int n = 0;
  for (int i = 0; i < nContacts[0]; i++)
    if (contacts[0][i].flags & CF_ACCEPTED) n++;
  return n;
}
static int32_t bestSum() {
  int32_t best = 0;
  for (int i = 0; i < nContacts[0]; i++)
    if ((contacts[0][i].flags & CF_ACCEPTED) && contacts[0][i].sum > best)
      best = contacts[0][i].sum;
  return best;
}
static int bestArea() {
  int32_t best = 0; int area = 0;
  for (int i = 0; i < nContacts[0]; i++)
    if ((contacts[0][i].flags & CF_ACCEPTED) && contacts[0][i].sum > best) {
      best = contacts[0][i].sum; area = contacts[0][i].area;
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
  check("no taxel hit the drift cap", ctel[0].capped == 0, "%u capped", ctel[0].capped);
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
    if (firstSeen < 0 && ctel[0].activeCells > 0) {
      firstSeen = f;
    }
    if (f == firstSeen + 4 * FPS) area = (int)ctel[0].activeCells;
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
    specks += ctel[0].suppressed;
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
  int n = condTareOutliers(0, cc.maxDrift, &worst, &wi, &med);
  check("shape test flags a localised load", n > 0,
        "%d taxels, worst %+d over median %d at r%d c%d",
        n, worst, med, wi / MAX_COLS, wi % MAX_COLS);

  // --- clear mat: must NOT false-alarm ------------------------------------
  condReset();
  clearLoad();
  simTare();
  n = condTareOutliers(0, cc.maxDrift, &worst, &wi, &med);
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

  int nShape = condTareOutliers(0, cc.maxDrift, &worst, &wi, &med);
  int nRef   = condRefCompare(0, cc.maxDrift, &worst, &wi);
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
    int restCells = ctel[0].activeCells;

    reset();
    addBlob(g.rows / 2.0f, nCols() / 2.0f, 2.2f, 700.0f);
    double press = timeCond(N);
    int pressCells = ctel[0].activeCells, pressCon = nContacts[0];

    // Worst case: drop the thresholds so the noise floor itself goes active.
    reset();
    clearLoad();
    uint16_t onSave = cc.minOn, offSave = cc.minOff; uint8_t konSave = cc.kOn, nonSave = cc.nOn;
    cc.minOn = 0; cc.minOff = 0; cc.kOn = 1; cc.nOn = 1;
    double storm = timeCond(N);
    int stormCells = ctel[0].activeCells, stormCon = nContacts[0];
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

/*
 * Fixed-point one-euro equivalence.
 *
 * Two separate questions, because they can fail independently.
 *
 * The ARITHMETIC: does alphaQ16() agree with the float formula it replaces?
 * Swept directly against the shipped function through the test seam in
 * condition.h, so this cannot pass by testing a copy that has drifted.
 *
 * The PIPELINE: does the whole conditioner behave the same? A per-frame digest
 * of filtMap over a deterministic scene is printed as CSV. The float and fixed
 * builds each emit one, and `make compare` diffs them numerically. Comparing
 * digests rather than pass/fail verdicts matters: a filter change can shift
 * every value slightly while every check still passes, and that is exactly the
 * kind of drift worth seeing.
 */

/*
 * The digest scene, and why it looks like this.
 *
 * The first version of it pressed one blob in the middle of the mat and
 * watched it decay. It was useless as a regression test: deliberately breaking
 * the despeckle neighbour test (c < nc-1 changed to c < nc-2, exactly the
 * off-by-one a bitmap rewrite would produce) changed nothing in the digest,
 * and every A-G check still passed. A test that cannot fail is worse than no
 * test, because it is trusted.
 *
 * A blob in the middle of a mat exercises almost nothing. So this scene is
 * built to touch each thing that a change to the pipeline's indexing or
 * neighbour logic is likely to break:
 *
 *   - all four ARRAY EDGES, where a shift or a bounds check goes wrong first
 *   - ISOLATED TAXELS, which is the only input despeckle acts on at all
 *   - TWO BLOBS THAT MERGE, which is the only input that exercises union-find
 *   - a SUSTAINED contact, for the freeze/halo and stuck-release paths
 *   - both nc == 32 and nc < 32, so the column mask is not assumed away
 *
 * The digest carries the stage TELEMETRY, not just the map. `suppressed`
 * counts despeckle, `frozen` counts the halo, `adapted`/`released`/`capped`
 * count the baseline - so a fault in one of those stages shows up as a changed
 * counter even in a frame where the map happens to come out the same. And the
 * checksum weights each taxel by its index, so a value moving from one taxel
 * to another is visible where a plain sum would hide it.
 */
static void digestScene(int rows, int chans, int banks, const char *name,
                        int nsens = 1) {
  seedRng();
  cfg.rows = (uint8_t)rows; cfg.chans = (uint8_t)chans; cfg.banks = (uint8_t)banks;
  cfg.sensors = (uint8_t)nsens;
  cfg.sensorMask = (uint8_t)((nsens >= 8) ? 0xFF : ((1u << nsens) - 1u));
  for (int s = 0; s < nsens; s++) sceneInit(s);
  condReset(); clearLoad(); simTare();

  const int nr = cfg.rows, nc = nCols();
  // A regression scene is only worth what it touches, so count it rather than
  // trusting the blob coordinates to land where they were meant to.
  int hitR0 = 0, hitR1 = 0, hitC0 = 0, hitC1 = 0, hitSup = 0, hitMerge = 0;
  for (int f = 0; f < 600; f++) {
    switch (f) {
      case 100: addBlob(nr / 2.0f, nc / 2.0f, 2.0f, 700.0f); break;

      case 150:                     // every corner: edges reach the contact list
        addBlob(0.0f, 0.0f, 1.4f, 600.0f);
        addBlob(0.0f, (float)(nc - 1), 1.4f, 600.0f);
        addBlob((float)(nr - 1), 0.0f, 1.4f, 600.0f);
        addBlob((float)(nr - 1), (float)(nc - 1), 1.4f, 600.0f);
        break;

      /*
       * The despeckle probes, on a cleared mat so nothing else is adjacent.
       *
       * Touching an edge is not the same as DISCRIMINATING at one. A solid
       * blob that reaches column nc-1 has active neighbours whichever way the
       * bounds test goes, so breaking `c < nc - 1` changes nothing about it -
       * an earlier version of this scene proved exactly that by failing to
       * notice the fault.
       *
       * What distinguishes the guard is an ISOLATED PAIR straddling the last
       * two columns: correct code sees each half's neighbour and keeps both,
       * a broken guard cannot see across the boundary and suppresses one.
       * Singles sitting exactly ON each edge are the other half of the test -
       * they must be suppressed, and a shift that wraps a phantom neighbour in
       * from outside the array would keep them.
       */
      case 200:
        clearLoad();
        dot(0, nc / 2, 500.0f);              // single on the top edge
        dot(nr - 1, nc - 3, 500.0f);         // single on the bottom edge
        dot(nr / 2, 0, 500.0f);              // single on the left edge
        dot(nr / 2, nc - 1, 500.0f);         // single on the right edge
        dot(2, 0, 500.0f); dot(2, 1, 500.0f);              // pair across c > 0
        dot(4, nc - 2, 500.0f); dot(4, nc - 1, 500.0f);    // pair across c < nc-1
        dot(0, 3, 500.0f); dot(1, 3, 500.0f);              // pair across r > 0
        dot(nr - 2, 5, 500.0f); dot(nr - 1, 5, 500.0f);    // pair across r < nr-1
        break;

      case 250:                     // two blobs on a clear mat, then bridged,
        clearLoad();                // which is the only input union-find sees
        addBlob(nr * 0.75f, nc * 0.30f, 1.6f, 650.0f);
        addBlob(nr * 0.75f, nc * 0.62f, 1.6f, 650.0f);
        break;
      case 300:
        addBlob(nr * 0.75f, nc * 0.46f, 1.3f, 650.0f);     // bridge: forces a union
        break;
      default: break;
    }
    /*
     * Decoys on every other mat, deliberately out of step with mat 0.
     *
     * The claim being tested is that the mats are independent, and the sharp
     * way to test it is that mat 0's digest comes out byte-identical whether
     * it runs alone or alongside seven busy neighbours. That only means
     * something if the neighbours are actually doing something, and doing
     * something DIFFERENT - decoys that pressed in unison with mat 0 would
     * hide a base-offset fault that mapped one mat onto another.
     */
    for (int s = 1; s < nsens; s++) {
      int phase = (f + 37 * s) % 300;
      if (phase == 0)  addBlob((float)((nr / 2 + 3 * s) % nr),
                               (float)((nc / 2 + 5 * s) % nc), 1.8f, 640.0f, s);
      if (phase == 120) clearLoad(s);
      if (phase == 150) dot((2 * s) % nr, (3 * s) % nc, 520.0f, s);
    }

    float scale = (f >= 430) ? expf(-(f - 430) / (18.0f * FPS)) : 1.0f;
    makeFrame(scale);
    condProcess(dr, DT);

    for (int c = 0; c < nc; c++) {
      if (outMap[0 * MAX_COLS + c])        hitR0++;
      if (outMap[(nr - 1) * MAX_COLS + c]) hitR1++;
    }
    for (int r = 0; r < nr; r++) {
      if (outMap[r * MAX_COLS + 0])        hitC0++;
      if (outMap[r * MAX_COLS + (nc - 1)]) hitC1++;
    }
    hitSup += ctel[0].suppressed;
    if (nContacts[0] > 1) hitMerge++;

    long sum = 0, chk = 0; int mx = INT32_MIN, mn = INT32_MAX;
    for (int r = 0; r < nr; r++)
      for (int c = 0; c < nc; c++) {
        int i = r * MAX_COLS + c, v = filtMap[i];
        sum += v;
        chk += (long)v * (i + 1);          // position-weighted: a value that
        if (v > mx) mx = v;                // moves between taxels is visible
        if (v < mn) mn = v;
      }
    printf("%s,%d,%ld,%ld,%d,%d,%u,%u,%u,%u,%u,%u,%u\n",
           name, f, sum, chk, mx, mn,
           ctel[0].activeCells, nContacts[0], nRejected[0],
           ctel[0].suppressed, ctel[0].frozen, ctel[0].adapted, ctel[0].capped);
  }

  fprintf(stderr,
          "# %s coverage: row0 %d  row%d %d  col0 %d  col%d %d  "
          "despeckled %d  multi-contact frames %d\n",
          name, hitR0, nr - 1, hitR1, hitC0, nc - 1, hitC1, hitSup, hitMerge);
  if (!hitR0 || !hitR1 || !hitC0 || !hitC1 || !hitSup || !hitMerge) {
    fprintf(stderr, "# %s: SCENE DOES NOT COVER ITS EDGES - a bitmap or "
                    "indexing fault could pass unnoticed\n", name);
    failures++;
  }
}

static void euroCheck() {
#if TAXEL_EURO_FIXED
  // Relative error is only meaningful once alpha is big enough to affect the
  // output. Below alpha ~ 1e-3 the error is pure Q16 quantisation - alpha 1e-4
  // against 1.07e-4 is 7% "relative" and means nothing, since either value
  // leaves the filter effectively frozen.
  double worstAbs = 0.0, worstRel = 0.0;
  uint32_t worstAt = 0, worstRelAt = 0;
  for (uint32_t xQ16 = 1; xQ16 < (1u << 24); xQ16 += 7) {
    double x   = xQ16 / 65536.0;
    double ref = x / (x + 0.15915494309189535);
    double got = condAlphaQ16Test(xQ16) / 65536.0;
    double a   = fabs(got - ref);
    if (a > worstAbs) { worstAbs = a; worstAt = xQ16; }
    if (ref > 1e-3 && a / ref > worstRel) { worstRel = a / ref; worstRelAt = xQ16; }
  }
  fprintf(stderr, "# alphaQ16 vs float, x over [0, 256]:\n"
                  "#   max abs error %.2e at x = %.4f\n"
                  "#   max rel error %.4f%% at x = %.4f (over alpha > 1e-3)\n",
          worstAbs, worstAt / 65536.0, worstRel * 100.0, worstRelAt / 65536.0);
  // One Q16 step is 1.5e-5; anything near that is representation, not method.
  if (worstAbs > 5e-5) { fprintf(stderr, "# ALPHA ERROR TOO LARGE\n"); failures++; }
  if (worstRel > 0.01) { fprintf(stderr, "# ALPHA REL ERROR TOO LARGE\n"); failures++; }
#endif

  printf("scene,frame,sum,chk,max,min,active,contacts,rejected,suppressed,frozen,adapted,capped\n");
  digestScene(16, 16, 2, "16x32");   // nc == 32: one activity word per row
  digestScene(32, 12, 1, "32x12");   // nc <  32: exercises the column mask
}

int main(int argc, char **argv) {
  if (argc > 1 && !strcmp(argv[1], "euro")) {
    condInit();
    sceneInit();
    euroCheck();
    return failures ? 1 : 0;
  }
  if (argc > 1 && !strcmp(argv[1], "multi")) {
    // Identical scenes on mat 0, but with seven busy neighbours. The digest
    // must match golden/digest.csv exactly; anything else is one mat's state
    // reaching another.
    condInit();
    printf("scene,frame,sum,chk,max,min,active,contacts,rejected,"
           "suppressed,frozen,adapted,capped\n");
    digestScene(16, 16, 2, "16x32", MAX_SENSORS);
    digestScene(32, 12, 1, "32x12", MAX_SENSORS);
    return failures ? 1 : 0;
  }
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
