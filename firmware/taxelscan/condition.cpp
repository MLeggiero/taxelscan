// This file is the per-frame hot loop: every stage below runs once per taxel,
// 512 times a frame, and at 200 Hz that is 100k iterations a second. The core
// builds everything at -Os, which is the right default for a sketch and the
// wrong one here - size is not the constraint, the frame period is.
#pragma GCC optimize("O2")

#include "condition.h"
#include <EEPROM.h>
#include <string.h>
#include <math.h>

CondCfg   cc;
CondTelem ctel[MAX_SENSORS];

int16_t  outMap [MAX_ALL_TAXELS];
int16_t  filtMap[MAX_ALL_TAXELS];
Contact  contacts [MAX_SENSORS][MAX_CONTACTS];
uint8_t  nContacts[MAX_SENSORS] = {0};
uint8_t  nRejected[MAX_SENSORS] = {0};
uint16_t sigmaQ4[MAX_ALL_TAXELS];

// ---------------------------------------------------------------- state
static int32_t  baseQ8 [MAX_ALL_TAXELS];     // baseline, Q8
static int16_t  tare0  [MAX_ALL_TAXELS];     // boot tare - the drift cap hangs off this
static int16_t  hist   [3][MAX_ALL_TAXELS];  // temporal median ring
static uint8_t  histPos = 0;
static bool     histFull = false;
#if TAXEL_EURO_FIXED
static int32_t  xhatQ8 [MAX_ALL_TAXELS];     // one-euro state, Q8 counts
static int32_t  dhatQ8 [MAX_ALL_TAXELS];     // EMA of frame-to-frame delta, Q8 counts
#else
static float    xhat   [MAX_ALL_TAXELS];     // one-euro state
static float    dxhat  [MAX_ALL_TAXELS];
#endif
static int16_t  prevIn [MAX_ALL_TAXELS];     // raw derivative reference
static int16_t  prevFilt[MAX_ALL_TAXELS];
static uint16_t varEma [MAX_ALL_TAXELS];     // EMA of |frame-to-frame|, Q4
static uint8_t  onCnt  [MAX_ALL_TAXELS];
static uint8_t  offCnt [MAX_ALL_TAXELS];
/*
 * Activity as one bit per taxel, one uint32 per row.
 *
 * MAX_COLS is exactly 32, which is what makes this worth doing: connected
 * components, despeckle and release eligibility were 53.8% of the per-taxel
 * budget between them (tools/armstages.py), plus the halo inside the baseline
 * stage, and all four spent it on bounds-checked per-taxel neighbour tests. In
 * word form a neighbourhood is a shift and an OR.
 *
 * The bounds checks do not get reimplemented, they DISAPPEAR: shifting brings
 * in zeros at the ends, which is exactly "outside the array is not active".
 * Bit c is column c, so (a << 1) at bit c means "column c-1 was active" and
 * (a >> 1) at bit c means "column c+1 was active".
 */
static uint32_t actBits [MAX_SENSORS][MAX_ROWS];     // live active state
static uint32_t prevBits[MAX_ROWS];     // scratch, per sensor: frame-entry snapshot
static uint32_t haloBits[MAX_ROWS];     // scratch, per sensor: prevBits dilated 3x3
static uint32_t snapBits[MAX_ROWS];     // scratch, per sensor: despeckle snapshot
static uint32_t relBits [MAX_SENSORS][MAX_ROWS];     // releasable
static uint32_t capBits [MAX_SENSORS][MAX_ROWS];     // pinned at the drift cap
static uint32_t stuckN [MAX_ALL_TAXELS];     // consecutive active frames
// Scratch, rebuilt from nothing every frame, so one mat's worth is enough -
// which is also why it is indexed by LI() below and not by IDX(): it has no
// sensor base, and giving it one would run off the end on mat 1.
static uint16_t label  [MAX_TAXELS];
static bool     primed = false;

static_assert(MAX_COLS == 32, "activity bitmaps assume one uint32 per row");

static inline bool bitAt(const uint32_t *w, int r, int c) {
  return (w[r] >> c) & 1u;
}

// MSVC builds sim/ too, and neither of these is standard before C++20.
#if defined(_MSC_VER)
#include <intrin.h>
static inline int ctz32(uint32_t v) { unsigned long i; _BitScanForward(&i, v); return (int)i; }
static inline int pop32(uint32_t v) { return (int)__popcnt(v); }
#else
static inline int ctz32(uint32_t v) { return __builtin_ctz(v); }
static inline int pop32(uint32_t v) { return __builtin_popcount(v); }
#endif

static const int MAX_LABELS = 512;
static uint16_t lparent[MAX_LABELS];
static uint16_t larea  [MAX_LABELS];
static int32_t  lsum   [MAX_LABELS];
static int16_t  lpeak  [MAX_LABELS];
static uint16_t lpeakI [MAX_LABELS];
static int32_t  lsumR  [MAX_LABELS], lsumC[MAX_LABELS], lwsum[MAX_LABELS];
static uint8_t  lr0[MAX_LABELS], lc0[MAX_LABELS], lr1[MAX_LABELS], lc1[MAX_LABELS];
static uint16_t ledge  [MAX_LABELS];
static uint8_t  laccept[MAX_LABELS];

/*
 * Emulated-EEPROM calibration block. One 4 KB flash sector is all the core
 * gives us, and sigma at u16 plus a reference tare at int16 would need 4100
 * bytes - just over. Sigma is stored as u8 in Q2 instead (0.25 count steps up
 * to 63.75), which is far finer than the film deserves and leaves room.
 *
 *    0  magic u32
 *    4  flags u8      bit0 sigma valid, bit1 reference valid
 *    5  rows, chans, banks u8      geometry the calibration was taken at
 *    8  sigma    MAX_TAXELS u8     Q2
 * 1032  refTare  MAX_TAXELS int16
 */
static const uint32_t CAL_MAGIC   = 0x46544332;   // "FTC2"
static const int CAL_OFF_FLAGS = 4;
static const int CAL_OFF_GEOM  = 5;
static const int CAL_OFF_SIGMA = 8;
static const int CAL_OFF_REF   = CAL_OFF_SIGMA + MAX_TAXELS;        // 1032
static const int CAL_END       = CAL_OFF_REF + MAX_TAXELS * 2;      // 3080
static const uint8_t CALF_SIGMA = 0x01;
static const uint8_t CALF_REF   = 0x02;

static int16_t refTare[MAX_ALL_TAXELS];
static bool    refValid = false;

// ---------------------------------------------------------------- helpers
/*
 * Every per-taxel array holds all the mats end to end, so an index is the
 * sensor's base plus the position within that mat. Relative arithmetic on the
 * result - i - MAX_COLS for the row above, i - 1 for the column to the left -
 * is unaffected by the base, which is what lets the pipeline below stay
 * written against a single mat.
 */
static inline int SBASE(int s) { return s * MAX_TAXELS; }
static inline int IDX(int base, int r, int c) { return base + r * MAX_COLS + c; }
static inline int LI(int r, int c)             { return r * MAX_COLS + c; }

static inline int16_t median3(int16_t a, int16_t b, int16_t c) {
  if (a > b) { int16_t t = a; a = b; b = t; }
  if (b > c) { int16_t t = b; b = c; c = t; }
  if (a > b) { int16_t t = a; a = b; b = t; }
  return b;
}

/*
 * One-euro smoothing factor, in ONE division instead of three.
 *
 * The textbook form is tau = 1/(2*pi*fc); alpha = 1/(1 + tau/dt), which costs
 * three divides per taxel. Substituting and rearranging:
 *
 *     alpha = 1/(1 + 1/(2*pi*fc*dt)) = x/(x + 1/(2*pi))   where x = fc*dt
 *
 * VDIV.F32 is ~14 cycles on this M33 and does not pipeline, so dropping two of
 * them per taxel is worth roughly 30 cycles x 512 taxels x the frame rate.
 */
static const float INV_TWO_PI = 0.15915494f;

static inline float alphaFor(float cutoff, float dt) {
  if (cutoff <= 0.0f) return 1.0f;
  float x = cutoff * dt;
  return x / (x + INV_TWO_PI);
}

#if TAXEL_EURO_FIXED
/*
 * The same filter in integers, because at 8192 taxels the float form does not
 * fit the frame budget. VDIV.F32 is ~14 cycles and does not pipeline, and it is
 * paid once per taxel on top of the int<->float conversions around it.
 *
 * Two changes remove it, and the first is the one that matters.
 *
 * TRACK THE DERIVATIVE IN COUNTS, NOT COUNTS PER SECOND. The cutoff reaches the
 * filter only through x = cutoff*dt, so with dxhat = EMA(ddelta)/dt:
 *
 *     x = (fcMin + beta*|dxhat|) * dt
 *       = fcMin*dt + beta*dt*|EMA(ddelta)|/dt
 *       = fcMin*dt + beta*|EMA(ddelta)|
 *
 * dt cancels out of the derivative term entirely. That drops the invDt multiply
 * per taxel, and it keeps the state in counts (+-8190) rather than counts per
 * second (+-4e6), which is what lets it sit in Q8 without crowding int32.
 *
 * The two forms are algebraically identical while dt is constant, which is what
 * the fixed frame cadence exists to guarantee. They differ only across a dt
 * change - a counted overrun - where the float form's stored counts/s stays
 * meaningful and this one's stored counts lag by one frame. Given that a dt
 * excursion is already an exceptional event that condProcess() clamps hard, a
 * one-frame lag in the cutoff is the cheaper of the two costs.
 *
 * DIVIDE IN INTEGERS. alpha = x/(x + 1/2pi) becomes one 32-bit UDIV (2-12
 * cycles, early-terminating), computed in the complement form
 *
 *     alpha = 1 - k/(x + k)
 *
 * rather than directly. That is worth a word, because the direct form does not
 * work in 32 bits: x/(x+k) needs the numerator pre-shifted by 16, which
 * overflows once x exceeds 2^16, so it forces either a 64-bit divide (a helper
 * call, ~40 cycles - the whole point was to avoid that) or a scale-and-clamp
 * that puts a visible step in alpha where the clamp bites. In the complement
 * form the numerator is the CONSTANT k<<16 = 683540480, which cannot overflow
 * whatever x does, so one 32-bit divide covers the entire input range exactly
 * and there is no clamp to place.
 */
static const uint32_t ONE_Q16 = 65536u;
static const uint32_t K_Q16   = 10430u;          // 1/(2*pi) in Q16
static const uint32_t K_NUM   = K_Q16 << 16;     // fits 32 bits: 6.8e8 < 4.3e9
static const uint32_t X_CAP_Q16 = 1u << 28;      // x = 4096; alpha = 0.99996

static inline uint32_t alphaQ16(uint32_t xQ16) {
  // Matches alphaFor()'s guard: a non-positive cutoff means "do not smooth".
  if (xQ16 == 0) return ONE_Q16;
  return ONE_Q16 - (K_NUM / (xQ16 + K_Q16));
}

uint32_t condAlphaQ16Test(uint32_t xQ16) { return alphaQ16(xQ16); }
#endif

void condSigmaDefault(uint16_t q4) {
  for (int i = 0; i < MAX_TAXELS; i++) sigmaQ4[i] = q4;
}

void condInit(void) {
  EEPROM.begin(4096);
  condSigmaDefault(48);            // 3.0 counts until characterised
  condReset();
}

void condReset(void) {
  memset(baseQ8, 0, sizeof(baseQ8));
  memset(tare0,  0, sizeof(tare0));
  memset(hist,   0, sizeof(hist));
#if TAXEL_EURO_FIXED
  memset(xhatQ8, 0, sizeof(xhatQ8));
  memset(dhatQ8, 0, sizeof(dhatQ8));
#else
  memset(xhat,   0, sizeof(xhat));
  memset(dxhat,  0, sizeof(dxhat));
#endif
  memset(prevIn, 0, sizeof(prevIn));
  memset(prevFilt, 0, sizeof(prevFilt));
  memset(varEma, 0, sizeof(varEma));
  memset(onCnt,  0, sizeof(onCnt));
  memset(offCnt, 0, sizeof(offCnt));
  memset(actBits,  0, sizeof(actBits));
  memset(prevBits, 0, sizeof(prevBits));
  memset(haloBits, 0, sizeof(haloBits));
  memset(snapBits, 0, sizeof(snapBits));
  memset(relBits,  0, sizeof(relBits));
  memset(capBits,  0, sizeof(capBits));
  memset(stuckN, 0, sizeof(stuckN));
  memset(label,  0, sizeof(label));
  memset(outMap, 0, sizeof(outMap));
  memset(filtMap, 0, sizeof(filtMap));
  histPos = 0; histFull = false; primed = false;
  memset(nContacts, 0, sizeof(nContacts));
  memset(nRejected, 0, sizeof(nRejected));
  memset(&ctel, 0, sizeof(ctel));
}

void condSeedBaseline(int s, const int16_t *dr) {
  const int base = SBASE(s);
  const int nc = nCols();
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nc; c++) {
      int i = IDX(base, r, c);
      baseQ8[i] = (int32_t)dr[i] << 8;
      tare0[i]  = dr[i];
      stuckN[i] = 0;
      const uint32_t m = 1u << c;
      actBits[s][r] &= ~m; relBits[s][r] &= ~m; capBits[s][r] &= ~m;
      onCnt[i] = offCnt[i] = 0;
#if TAXEL_EURO_FIXED
      xhatQ8[i] = 0; dhatQ8[i] = 0;
#else
      xhat[i] = 0.0f; dxhat[i] = 0.0f;
#endif
      prevIn[i] = dr[i]; prevFilt[i] = 0;
      varEma[i] = 0;
    }
  primed = true;
}

int32_t condBaseline(int s, int r, int c) { return baseQ8[IDX(SBASE(s), r, c)] >> 8; }

// ---------------------------------------------------------------- union-find
static uint16_t lfind(uint16_t x) {
  while (lparent[x] != x) { lparent[x] = lparent[lparent[x]]; x = lparent[x]; }
  return x;
}
static void lunion(uint16_t a, uint16_t b) {
  a = lfind(a); b = lfind(b);
  if (a != b) { if (a < b) lparent[b] = a; else lparent[a] = b; }
}

// ---------------------------------------------------------------- pipeline
/*
 * Per-frame constants, computed once and handed to every mat.
 *
 * These depend only on dt and the option table, so recomputing them per sensor
 * would be eight times the float work for eight identical answers - and worse,
 * would invite the eight mats to drift apart if one of them ever saw a
 * different dt.
 */
struct FrameConst {
  float    dt;
  int32_t  fallStep, riseStep, relStep;
  uint32_t stuckLimit;
  float    ad;
  uint32_t colMask;
#if TAXEL_EURO_FIXED
  uint32_t adQ16, betaQ16, fcMinDtQ16;
#else
  float    invDt;
#endif
};

static void condProcessOne(int s, const int16_t *dr, const FrameConst &fc);

void condProcess(const int16_t *dr, float dt) {
  const uint32_t t_enter = micros();
  const int nc   = nCols();
  const int nrow = cfg.rows;
  (void)nrow;
  // Clamped, not merely defaulted. dt scales every baseline step and sets every
  // filter cutoff, so a wild value - a resumed pause, a stalled host, a frame
  // that overran badly - must not be allowed to move the baseline by seconds'
  // worth in one go. A 4x error is survivable; a 400x one is not.
  const float nominal = cfg.framePeriodUs / 1e6f;
  if (dt <= 0.0f) dt = nominal;
  if (dt < 0.002f) dt = 0.002f;
  if (dt > 0.100f) dt = 0.100f;

  if (!primed)
    for (int s = 0; s < cfg.sensors; s++) condSeedBaseline(s, dr);

  memset(ctel, 0, sizeof(ctel));

  // Passthrough mode: baseline subtraction only. Kept because A/B against the
  // full pipeline is the only way to attribute an improvement to it.
  //
  // It returns before the median history is written, which is deliberate: a
  // frame that skipped the pipeline has no business seeding the filter that
  // the next one will run.
  if (!cc.enable) {
    for (int s = 0; s < cfg.sensors; s++) {
      if (!(cfg.sensorMask & (1u << s))) continue;
      const int base = SBASE(s);
      for (int r = 0; r < nrow; r++)
        for (int c = 0; c < nc; c++) {
          int i = IDX(base, r, c);
          int32_t v = dr[i] - (baseQ8[i] >> 8);
          if (v >  32767) v =  32767;
          if (v < -32768) v = -32768;
          filtMap[i] = outMap[i] = (int16_t)v;
          if (v > ctel[s].peak) ctel[s].peak = (int16_t)v;
        }
      nContacts[s] = nRejected[s] = 0;
    }
    ctel[0].condUs = micros() - t_enter;
    return;
  }

  // ---- stage 4: temporal median-of-3 -----------------------------------
  memcpy(hist[histPos], dr, sizeof(hist[0]));
  histPos = (uint8_t)((histPos + 1) % 3);
  if (histPos == 0) histFull = true;

  // ---- per-frame rate budgets ------------------------------------------
  const int32_t fallStep = (int32_t)(cc.fallRate    * dt * 256.0f) + 1;
  const int32_t riseStep = (int32_t)(cc.riseRate    * dt * 256.0f) + 1;
  const int32_t relStep  = (int32_t)(cc.releaseRate * dt * 256.0f) + 1;
  const uint32_t stuckLimit = (uint32_t)(cc.stuckSecs / (dt > 0 ? dt : 0.025f));
  const float ad = alphaFor(cc.dCutoff, dt);
#if TAXEL_EURO_FIXED
  // Hoisted once per frame, so the float arithmetic here costs nothing per
  // taxel. ad comes from alphaFor() unchanged, which keeps the derivative
  // EMA's rate bit-comparable with the float build.
  const uint32_t adQ16       = (uint32_t)(ad * 65536.0f);
  const uint32_t betaQ16     = (uint32_t)(cc.beta * 65536.0f);
  const uint32_t fcMinDtQ16  = (uint32_t)(cc.fcMin * dt * 65536.0f);
#else
  const float invDt = 1.0f / dt;      // hoisted: was a divide per taxel
#endif

  FrameConst fc;
  fc.dt = dt; fc.fallStep = fallStep; fc.riseStep = riseStep; fc.relStep = relStep;
  fc.stuckLimit = stuckLimit; fc.ad = ad;
  // Bits above nc-1 are never set, but masking anyway keeps a geometry change
  // that shrinks nc from leaving stale activity in the high bits.
  fc.colMask = (nc >= 32) ? 0xFFFFFFFFu : ((1u << nc) - 1u);
#if TAXEL_EURO_FIXED
  fc.adQ16 = adQ16; fc.betaQ16 = betaQ16; fc.fcMinDtQ16 = fcMinDtQ16;
#else
  fc.invDt = invDt;
#endif

  // Every mat walks the same rows at the same instant, so they are conditioned
  // in the same frame - but each is an independent surface and nothing crosses
  // between them. A mat with nothing on it costs one test and a `continue`,
  // which skips connected components, the most expensive stage there is.
  for (int s = 0; s < cfg.sensors; s++) {
    if (!(cfg.sensorMask & (1u << s))) continue;
    condProcessOne(s, dr, fc);
  }
  ctel[0].condUs = micros() - t_enter;
}

static void condProcessOne(int s, const int16_t *dr, const FrameConst &fc) {
  const int base = SBASE(s);
  const int nc   = nCols();
  const int nrow = cfg.rows;
  const int32_t fallStep = fc.fallStep, riseStep = fc.riseStep, relStep = fc.relStep;
  const uint32_t stuckLimit = fc.stuckLimit, colMask = fc.colMask;
#if TAXEL_EURO_FIXED
  const uint32_t adQ16 = fc.adQ16, betaQ16 = fc.betaQ16, fcMinDtQ16 = fc.fcMinDtQ16;
#else
  const float dt = fc.dt, ad = fc.ad, invDt = fc.invDt;
#endif
  // This mat's rows of persistent activity state. Everything below is written
  // against one mat, which is what keeps the pipeline the same code it was.
  uint32_t *const actBits = ::actBits[s];
  uint32_t *const relBits = ::relBits[s];
  uint32_t *const capBits = ::capBits[s];

  // Snapshot the latched state before anything moves. The halo test below has
  // to see one consistent frame, not a mix of this row's updated neighbours
  // and the next row's stale ones. As words that is a 128-byte copy.
  memcpy(prevBits, actBits, sizeof(prevBits));

  // Dilate it 3x3 ONCE for the whole frame, so the per-taxel halo test below
  // is a single bit test instead of a nine-way bounds-checked scan. Rows
  // outside the array contribute nothing, and the column shifts bring in
  // zeros, so neither direction needs a bounds check.
  if (cc.haloFreeze) {
    uint32_t wide[MAX_ROWS];
    for (int r = 0; r < nrow; r++) {
      uint32_t a = prevBits[r] & colMask;
      wide[r] = (a | (a << 1) | (a >> 1)) & colMask;
    }
    for (int r = 0; r < nrow; r++) {
      uint32_t h = wide[r];
      if (r > 0)        h |= wide[r - 1];
      if (r < nrow - 1) h |= wide[r + 1];
      haloBits[r] = h;
    }
  } else {
    // No halo: a taxel freezes only on its own account.
    memcpy(haloBits, prevBits, sizeof(haloBits));
  }

  for (int r = 0; r < nrow; r++) {
    for (int c = 0; c < nc; c++) {
      const int i = IDX(base, r, c);

      int16_t med = dr[i];
      if (cc.median && histFull)
        med = median3(hist[0][i], hist[1][i], hist[2][i]);

      // ---- stage 5: adaptive baseline ---------------------------------
      int32_t bq = baseQ8[i];
      int32_t d  = (int32_t)med - (bq >> 8);

      const uint32_t m = 1u << c;
      bool actNow = bitAt(prevBits, r, c);
      // Guarantee 1: a one-taxel halo, so a spreading contact is not nibbled
      // at its edges while it grows. haloBits already carries the dilation
      // (and equals prevBits when the halo is off), so this is one bit test.
      bool frozen = bitAt(haloBits, r, c);

      if (d < 0) {
        // Falling: fast and ungated. Too-high baselines suppress real contact,
        // which is the dangerous direction, and coming down can never eat signal.
        int32_t want = (-d) << 8;
        bq -= (want < fallStep) ? want : fallStep;
        ctel[s].adapted++;
      } else if (cc.release && (relBits[r] & m)) {
        int32_t want = d << 8;
        bq += (want < relStep) ? want : relStep;
        ctel[s].adapted++; ctel[s].released++;
      } else if (!frozen && d < (int32_t)cc.idleBand) {
        int32_t want = d << 8;
        bq += (want < riseStep) ? want : riseStep;
        ctel[s].adapted++;
      } else if (frozen) {
        ctel[s].frozen++;
      }

      // Guarantee 4: hard cap on cumulative upward drift. A taxel pinned here
      // is counted, so a phantom that comes back is visible in telemetry
      // rather than being rediscovered by staring at a heatmap.
      const int32_t capQ8 = ((int32_t)tare0[i] + (int32_t)cc.maxDrift) << 8;
      if (bq > capQ8) bq = capQ8;
      // "Pinned" means it is at the cap AND still wants to climb - a taxel that
      // merely sits there is not interesting. Flagged with >= rather than >
      // because the clamp above makes the strict test true for one frame only.
      if (bq >= capQ8 && d > 0) { capBits[r] |= m; ctel[s].capped++; }
      else capBits[r] &= ~m;
      baseQ8[i] = bq;

      int32_t delta = (int32_t)med - (bq >> 8);

      // ---- stage 6: one-euro ------------------------------------------
      int32_t fv;
      if (cc.euro) {
#if TAXEL_EURO_FIXED
        // Derivative EMA, in counts rather than counts/s - see alphaQ16().
        int32_t dd = (delta - (int32_t)prevIn[i]) << 8;
        dhatQ8[i] += (int32_t)(((int64_t)adQ16 * (dd - dhatQ8[i])) >> 16);

        // x = fcMin*dt + beta*|dhat|. The 64-bit product is one UMULL; only a
        // 64-bit DIVIDE would have cost anything, and there is not one here.
        uint32_t absD = (uint32_t)(dhatQ8[i] < 0 ? -dhatQ8[i] : dhatQ8[i]);
        uint64_t xw = (uint64_t)fcMinDtQ16 + ((((uint64_t)betaQ16 * absD)) >> 8);
        // The ceiling only exists to keep xQ16 inside uint32 for any beta the
        // option table allows. alpha(4096) = 0.99996, so nothing is lost.
        uint32_t xQ16 = (xw > X_CAP_Q16) ? X_CAP_Q16 : (uint32_t)xw;

        int32_t dq = (delta << 8) - xhatQ8[i];
        xhatQ8[i] += (int32_t)(((int64_t)alphaQ16(xQ16) * dq) >> 16);

        // Round half away from zero, matching the float path's +-0.5f.
        int32_t q = xhatQ8[i];
        fv = (q >= 0) ? ((q + 128) >> 8) : -(((-q) + 128) >> 8);
#else
        float x  = (float)delta;
        float dx = ((float)delta - (float)prevIn[i]) * invDt;
        dxhat[i] += ad * (dx - dxhat[i]);
        float cutoff = cc.fcMin + cc.beta * fabsf(dxhat[i]);
        float a = alphaFor(cutoff, dt);
        xhat[i] += a * (x - xhat[i]);
        // Not lrintf(): that is an out-of-line veneer call, once per taxel.
        fv = (int32_t)(xhat[i] >= 0.0f ? xhat[i] + 0.5f : xhat[i] - 0.5f);
#endif
      } else {
        fv = delta;
      }
      prevIn[i] = (int16_t)delta;

      if (fv >  32767) fv =  32767;
      if (fv < -32768) fv = -32768;
      filtMap[i] = (int16_t)fv;
      if (fv > ctel[s].peak) ctel[s].peak = (int16_t)fv;

      // Frame-to-frame movement, for the edge-liveness veto on release.
      int32_t mv = fv - prevFilt[i];
      if (mv < 0) mv = -mv;
      if (mv > 4095) mv = 4095;
      // Signed, deliberately: an unsigned EMA wraps when the target is below
      // the current value and pins varEma high forever, which would veto every
      // release and quietly disable guarantee 3.
      int32_t vtgt = mv << 4;
      varEma[i] = (uint16_t)((int32_t)varEma[i] + ((vtgt - (int32_t)varEma[i]) >> 4));
      prevFilt[i] = (int16_t)fv;

      // ---- stage 7 + 8: sigma thresholds, hysteresis, debounce ---------
      int32_t sOn  = ((int32_t)cc.kOn  * (int32_t)sigmaQ4[i]) >> 4;
      int32_t sOff = ((int32_t)cc.kOff * (int32_t)sigmaQ4[i]) >> 4;
      if (sOn  < (int32_t)cc.minOn)  sOn  = cc.minOn;
      if (sOff < (int32_t)cc.minOff) sOff = cc.minOff;

      if (!actNow) {
        if (fv >= sOn) {
          if (++onCnt[i] >= cc.nOn) { actBits[r] |= m; onCnt[i] = 0; offCnt[i] = 0; }
        } else onCnt[i] = 0;
      } else {
        if (fv < sOff) {
          if (++offCnt[i] >= cc.nOff) { actBits[r] &= ~m; offCnt[i] = 0; onCnt[i] = 0; }
        } else offCnt[i] = 0;
      }

      // activeCells is counted by popcount after despeckle rather than
      // incremented here and decremented there - one place to be wrong
      // instead of two.
      if (actBits[r] & m) stuckN[i]++;
      else stuckN[i] = 0;
    }
  }

  // ---- stage 9: isolated-speck suppression -----------------------------
  // A real contact at 2 mm pitch spans several taxels. A lone active cell with
  // no active 4-neighbour is noise. This is deliberately NOT a 3x3 median,
  // which would also erase a genuinely thin contact - a probe tip, the edge of
  // a bracket. Done on a snapshot so the test is simultaneous.
  if (cc.despeckle) {
    memcpy(snapBits, actBits, sizeof(snapBits));
    for (int r = 0; r < nrow; r++) {
      uint32_t a = snapBits[r] & colMask;
      if (!a) continue;                       // whole row: nothing to test
      uint32_t nb = (a << 1) | (a >> 1);      // left and right neighbours
      if (r > 0)        nb |= snapBits[r - 1];
      if (r < nrow - 1) nb |= snapBits[r + 1];
      uint32_t lonely = a & ~nb & colMask;    // active with no active neighbour
      if (lonely) {
        actBits[r] &= ~lonely;
        ctel[s].suppressed += (uint16_t)pop32(lonely);
      }
    }
  }

  for (int r = 0; r < nrow; r++) ctel[s].activeCells += (uint16_t)pop32(actBits[r] & colMask);

  // ---- stage 10: connected components ----------------------------------
  uint16_t nextLabel = 1;
  for (int r = 0; r < nrow; r++) {
    // A row with nothing active still needs its labels cleared, but it does
    // not need the body. At rest that is every row.
    if (!(actBits[r] & colMask)) {
      memset(&label[LI(r, 0)], 0, (size_t)nc * sizeof(label[0]));
      continue;
    }
    for (int c = 0; c < nc; c++) {
      const int li = LI(r, c);
      if (!(actBits[r] & (1u << c))) { label[li] = 0; continue; }
      uint16_t up   = (r > 0) ? label[li - MAX_COLS] : 0;
      uint16_t left = (c > 0) ? label[li - 1] : 0;
      if (!up && !left) {
        if (nextLabel < MAX_LABELS) {
          lparent[nextLabel] = nextLabel;
          label[li] = nextLabel++;
        } else {
          label[li] = 0;              // pathological frame; treat as unlabelled
        }
      } else if (up && left) {
        label[li] = (up < left) ? up : left;
        if (up != left) lunion(up, left);
      } else {
        label[li] = up ? up : left;
      }
    }
  }

  for (uint16_t l = 1; l < nextLabel; l++) {
    larea[l] = 0; lsum[l] = 0; lpeak[l] = INT16_MIN; lpeakI[l] = 0;
    lsumR[l] = lsumC[l] = lwsum[l] = 0; ledge[l] = 0; laccept[l] = 0;
    lr0[l] = 255; lc0[l] = 255; lr1[l] = 0; lc1[l] = 0;
  }

  for (int r = 0; r < nrow; r++) {
    // Guarantee 3 needs to know which taxels are on a blob's perimeter. A
    // taxel is interior when all four neighbours are active, so one word of
    // ANDs settles the whole row at once - and the shifts treat the array
    // edge as inactive for free, which is what the old four-way bounds test
    // was spelling out by hand.
    uint32_t interior;
    {
      uint32_t a  = actBits[r] & colMask;
      uint32_t up = (r > 0)        ? actBits[r - 1] : 0u;
      uint32_t dn = (r < nrow - 1) ? actBits[r + 1] : 0u;
      interior = a & up & dn & (a << 1) & (a >> 1) & colMask;
    }
    for (int c = 0; c < nc; c++) {
      int i = IDX(base, r, c);
      const int li = LI(r, c);
      if (!label[li]) continue;
      uint16_t l = lfind(label[li]);
      label[li] = l;
      int32_t w = filtMap[i] > 0 ? filtMap[i] : 0;
      if (larea[l] < 65535) larea[l]++;
      lsum[l]  += filtMap[i];
      lsumR[l] += w * r;
      lsumC[l] += w * c;
      lwsum[l] += w;
      if (filtMap[i] > lpeak[l]) { lpeak[l] = filtMap[i]; lpeakI[l] = (uint16_t)i; }
      if (r < lr0[l]) lr0[l] = (uint8_t)r;
      if (c < lc0[l]) lc0[l] = (uint8_t)c;
      if (r > lr1[l]) lr1[l] = (uint8_t)r;
      if (c > lc1[l]) lc1[l] = (uint8_t)c;

      // Guarantee 3: perimeter liveness. A real contact has a boundary of
      // partly loaded taxels that keep moving even when its centre is still.
      bool perim = !((interior >> c) & 1u);
      if (perim && varEma[i] > ledge[l]) ledge[l] = varEma[i];
    }
  }

  // ---- accept / reject, and build the contact list ---------------------
  nContacts[s] = 0; nRejected[s] = 0;
  for (uint16_t l = 1; l < nextLabel; l++) {
    if (lfind(l) != l || larea[l] == 0) continue;
    if (nContacts[s] >= MAX_CONTACTS) {
      // More blobs than the wire format carries - a pathological frame. They
      // stay unaccepted so the map gates them out, and they are counted into
      // `suppressed` rather than into `nRejected`, which has to keep meaning
      // "how many of the transmitted records were rejected".
      laccept[l] = 2;
      ctel[s].suppressed++;
      continue;
    }
    bool accept = (larea[l] >= cc.minArea) && (lsum[l] >= cc.minSum);
    laccept[l] = accept ? 1 : 2;              // 1 accepted, 2 rejected

    Contact &k = contacts[s][nContacts[s]++];
    k.id    = (uint8_t)l;
    k.area  = (uint8_t)(larea[l] > 255 ? 255 : larea[l]);
    k.sum   = lsum[l];
    k.peak  = lpeak[l];
    k.peakR = (uint8_t)(lpeakI[l] / MAX_COLS);
    k.peakC = (uint8_t)(lpeakI[l] % MAX_COLS);
    // 64-bit on purpose: lsumR can reach ~1.3e8 and shifting that left by 8
    // overflows int32 long before the divide brings it back into range.
    const int32_t wsum = lwsum[l];
    k.cenRQ8 = wsum ? (int16_t)(((int64_t)lsumR[l] << 8) / wsum)
                    : (int16_t)((int16_t)k.peakR << 8);
    k.cenCQ8 = wsum ? (int16_t)(((int64_t)lsumC[l] << 8) / wsum)
                    : (int16_t)((int16_t)k.peakC << 8);
    k.r0 = lr0[l]; k.c0 = lc0[l]; k.r1 = lr1[l]; k.c1 = lc1[l];
    k.flags = 0;
    if (ledge[l] > sigmaQ4[lpeakI[l]]) k.flags |= CF_EDGE_LIVE;
    if (accept) k.flags |= CF_ACCEPTED; else nRejected[s]++;
    k.pad = 0;
  }

  // ---- release eligibility for the NEXT frame --------------------------
  // Guarantees 2 and 3 live here. A taxel may only be walked back if it has
  // been continuously active for a long time, nothing about it is moving, it
  // is not part of a blob big enough to be a real contact, and that blob's
  // perimeter is not alive. A 20-taxel grasp fails three of those four tests
  // no matter how long it sits there.
  for (int r = 0; r < nrow; r++) {
    relBits[r] = 0;
    if (!cc.release) continue;
    // Only active taxels can be released, so walk the set bits rather than
    // every column. At rest there are none and the row costs one test.
    uint32_t a = actBits[r] & colMask;
    while (a) {
      int c = ctz32(a);
      a &= a - 1;
      int i = IDX(base, r, c);
      if (stuckN[i] < stuckLimit) continue;
      if (varEma[i] >= cc.stillness) continue;
      uint16_t l = label[LI(r, c)];
      if (l) {
        if (larea[l] >= cc.coherentArea) continue;     // coherent blob: never
        if (ledge[l] > sigmaQ4[i]) continue;           // live perimeter: never
      }
      relBits[r] |= 1u << c;
    }
  }

  // ---- output map ------------------------------------------------------
  for (int r = 0; r < nrow; r++)
    for (int c = 0; c < nc; c++) {
      int i = IDX(base, r, c);
      if (!cc.gateMap) { outMap[i] = filtMap[i]; continue; }
      uint16_t l = label[LI(r, c)];
      outMap[i] = (l && laccept[l] == 1) ? filtMap[i] : 0;
    }

}

CondTelem condTelemAll(void) {
  CondTelem t;
  memset(&t, 0, sizeof(t));
  for (int s = 0; s < cfg.sensors; s++) {
    if (!(cfg.sensorMask & (1u << s))) continue;
    t.adapted    += ctel[s].adapted;
    t.frozen     += ctel[s].frozen;
    t.released   += ctel[s].released;
    t.capped     += ctel[s].capped;
    t.suppressed += ctel[s].suppressed;
    t.activeCells+= ctel[s].activeCells;
    if (ctel[s].peak > t.peak) t.peak = ctel[s].peak;
  }
  t.condUs = ctel[0].condUs;
  return t;
}

// ---------------------------------------------------------------- calibration
bool    condRefValid(void)     { return refValid; }

bool condCalLoad(void) {
  uint32_t magic = 0;
  EEPROM.get(0, magic);
  if (magic != CAL_MAGIC) return false;
  uint8_t fl = 0;
  EEPROM.get(CAL_OFF_FLAGS, fl);

  if (fl & CALF_SIGMA) {
    for (int i = 0; i < MAX_TAXELS; i++) {
      uint8_t v = 0;              // EEPROM.get() writes it; the compiler cannot see that
      EEPROM.get(CAL_OFF_SIGMA + i, v);
      sigmaQ4[i] = v ? (uint16_t)(v * 4) : 48;   // Q2 -> Q4
    }
  }
  if (fl & CALF_REF) {
    for (int i = 0; i < MAX_TAXELS; i++) {
      int16_t v = 0;
      EEPROM.get(CAL_OFF_REF + i * 2, v);
      refTare[i] = v;
    }
    refValid = true;
  }
  return (fl & CALF_SIGMA) != 0;
}

void condCalSave(void) {
  uint8_t fl = CALF_SIGMA | (refValid ? CALF_REF : 0);
  EEPROM.put(0, CAL_MAGIC);
  EEPROM.put(CAL_OFF_FLAGS, fl);
  EEPROM.put(CAL_OFF_GEOM,     cfg.rows);
  EEPROM.put(CAL_OFF_GEOM + 1, cfg.chans);
  EEPROM.put(CAL_OFF_GEOM + 2, cfg.banks);
  for (int i = 0; i < MAX_TAXELS; i++) {
    uint32_t q2 = (sigmaQ4[i] + 2) / 4;
    if (q2 > 255) q2 = 255;
    EEPROM.put(CAL_OFF_SIGMA + i, (uint8_t)q2);
  }
  if (refValid)
    for (int i = 0; i < MAX_TAXELS; i++) EEPROM.put(CAL_OFF_REF + i * 2, refTare[i]);
  // EEPROM.commit() in the Arduino-Pico core already parks core 1 around the
  // flash erase/program operation. Nesting another idleOtherCore() here
  // deadlocks the second core-idle handshake before the calibration is saved.
  EEPROM.commit();
}

void condRefAdopt(void) {
  memcpy(refTare, tare0, sizeof(refTare));
  refValid = true;
  condCalSave();
}

int condRefCompare(int s, int thresh, int *maxDelta, int *worstIdx) {
  const int base = SBASE(s);
  int n = 0, worst = -32768, wi = -1;
  if (!refValid) { if (maxDelta) *maxDelta = 0; if (worstIdx) *worstIdx = -1; return 0; }
  const int nc = nCols();
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nc; c++) {
      int i = IDX(base, r, c);
      int d = (int)tare0[i] - (int)refTare[i];
      if (d > worst) { worst = d; wi = i; }
      if (d > thresh) n++;
    }
  if (maxDelta) *maxDelta = worst;
  if (worstIdx) *worstIdx = wi;
  return n;
}

int condTareOutliers(int s, int thresh, int *maxDelta, int *worstIdx, int *medianOut) {
  const int base = SBASE(s);
  const int nc = nCols();
  const int total = cfg.rows * nc;
  if (total <= 0) return 0;

  static int16_t sorted[MAX_TAXELS];
  int k = 0;
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nc; c++) sorted[k++] = tare0[IDX(base, r, c)];
  // Insertion sort: k is at most 1024 and this runs once, at boot.
  for (int a = 1; a < k; a++) {
    int16_t v = sorted[a];
    int b = a - 1;
    while (b >= 0 && sorted[b] > v) { sorted[b + 1] = sorted[b]; b--; }
    sorted[b + 1] = v;
  }
  int med = sorted[k / 2];
  if (medianOut) *medianOut = med;

  int n = 0, worst = -32768, wi = -1;
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nc; c++) {
      int i = IDX(base, r, c);
      int d = (int)tare0[i] - med;
      if (d > worst) { worst = d; wi = i; }
      if (d > thresh) n++;
    }
  if (maxDelta) *maxDelta = worst;
  if (worstIdx) *worstIdx = wi;
  return n;
}
