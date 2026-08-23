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
CondTelem ctel;

int16_t  outMap [MAX_TAXELS];
int16_t  filtMap[MAX_TAXELS];
Contact  contacts[MAX_CONTACTS];
uint8_t  nContacts = 0;
uint8_t  nRejected = 0;
uint16_t sigmaQ4[MAX_TAXELS];

// ---------------------------------------------------------------- state
static int32_t  baseQ8 [MAX_TAXELS];     // baseline, Q8
static int16_t  tare0  [MAX_TAXELS];     // boot tare - the drift cap hangs off this
static int16_t  hist   [3][MAX_TAXELS];  // temporal median ring
static uint8_t  histPos = 0;
static bool     histFull = false;
static float    xhat   [MAX_TAXELS];     // one-euro state
static float    dxhat  [MAX_TAXELS];
static int16_t  prevIn [MAX_TAXELS];     // raw derivative reference
static int16_t  prevFilt[MAX_TAXELS];
static uint16_t varEma [MAX_TAXELS];     // EMA of |frame-to-frame|, Q4
static uint8_t  onCnt  [MAX_TAXELS];
static uint8_t  offCnt [MAX_TAXELS];
static uint8_t  flags  [MAX_TAXELS];     // bit0 active, bit1 releasable, bit2 capped
static uint32_t stuckN [MAX_TAXELS];     // consecutive active frames
static uint16_t label  [MAX_TAXELS];
static bool     primed = false;

static const uint8_t TF_ACTIVE    = 0x01;
static const uint8_t TF_RELEASABLE = 0x02;
static const uint8_t TF_CAPPED    = 0x04;

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

static int16_t refTare[MAX_TAXELS];
static bool    refValid = false;

// ---------------------------------------------------------------- helpers
static inline int IDX(int r, int c) { return r * MAX_COLS + c; }

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
  memset(xhat,   0, sizeof(xhat));
  memset(dxhat,  0, sizeof(dxhat));
  memset(prevIn, 0, sizeof(prevIn));
  memset(prevFilt, 0, sizeof(prevFilt));
  memset(varEma, 0, sizeof(varEma));
  memset(onCnt,  0, sizeof(onCnt));
  memset(offCnt, 0, sizeof(offCnt));
  memset(flags,  0, sizeof(flags));
  memset(stuckN, 0, sizeof(stuckN));
  memset(label,  0, sizeof(label));
  memset(outMap, 0, sizeof(outMap));
  memset(filtMap, 0, sizeof(filtMap));
  histPos = 0; histFull = false; primed = false;
  nContacts = 0; nRejected = 0;
  memset(&ctel, 0, sizeof(ctel));
}

void condSeedBaseline(const int16_t *dr) {
  const int nc = nCols();
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nc; c++) {
      int i = IDX(r, c);
      baseQ8[i] = (int32_t)dr[i] << 8;
      tare0[i]  = dr[i];
      stuckN[i] = 0;
      flags[i]  = 0;
      onCnt[i] = offCnt[i] = 0;
      xhat[i] = 0.0f; dxhat[i] = 0.0f;
      prevIn[i] = dr[i]; prevFilt[i] = 0;
      varEma[i] = 0;
    }
  primed = true;
}

int32_t condBaseline(int r, int c) { return baseQ8[IDX(r, c)] >> 8; }

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
void condProcess(const int16_t *dr, float dt) {
  const uint32_t t_enter = micros();
  const int nc   = nCols();
  const int nrow = cfg.rows;
  // Clamped, not merely defaulted. dt scales every baseline step and sets every
  // filter cutoff, so a wild value - a resumed pause, a stalled host, a frame
  // that overran badly - must not be allowed to move the baseline by seconds'
  // worth in one go. A 4x error is survivable; a 400x one is not.
  const float nominal = cfg.framePeriodUs / 1e6f;
  if (dt <= 0.0f) dt = nominal;
  if (dt < 0.002f) dt = 0.002f;
  if (dt > 0.100f) dt = 0.100f;

  if (!primed) condSeedBaseline(dr);

  // Passthrough mode: baseline subtraction only. Kept because A/B against the
  // full pipeline is the only way to attribute an improvement to it.
  if (!cc.enable) {
    ctel.peak = 0; ctel.activeCells = 0;
    for (int r = 0; r < nrow; r++)
      for (int c = 0; c < nc; c++) {
        int i = IDX(r, c);
        int32_t v = dr[i] - (baseQ8[i] >> 8);
        if (v >  32767) v =  32767;
        if (v < -32768) v = -32768;
        filtMap[i] = outMap[i] = (int16_t)v;
        if (v > ctel.peak) ctel.peak = (int16_t)v;
      }
    nContacts = nRejected = 0;
    ctel.condUs = micros() - t_enter;
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
  const float invDt = 1.0f / dt;      // hoisted: was a divide per taxel

  ctel.adapted = ctel.frozen = ctel.released = ctel.capped = 0;
  ctel.suppressed = 0; ctel.activeCells = 0; ctel.peak = 0;

  // Snapshot the latched state before anything moves. The halo test below has
  // to see one consistent frame, not a mix of this row's updated neighbours
  // and the next row's stale ones.
  static uint8_t actPrev[MAX_TAXELS];
  for (int r = 0; r < nrow; r++)
    for (int c = 0; c < nc; c++)
      actPrev[IDX(r, c)] = (flags[IDX(r, c)] & TF_ACTIVE) ? 1 : 0;

  for (int r = 0; r < nrow; r++) {
    for (int c = 0; c < nc; c++) {
      const int i = IDX(r, c);

      int16_t med = dr[i];
      if (cc.median && histFull)
        med = median3(hist[0][i], hist[1][i], hist[2][i]);

      // ---- stage 5: adaptive baseline ---------------------------------
      int32_t bq = baseQ8[i];
      int32_t d  = (int32_t)med - (bq >> 8);

      bool actNow = actPrev[i] != 0;
      bool frozen = actNow;
      if (cc.haloFreeze && !frozen) {
        // Guarantee 1: a one-taxel halo, so a spreading contact is not
        // nibbled at its edges while it grows.
        for (int dr2 = -1; dr2 <= 1 && !frozen; dr2++)
          for (int dc = -1; dc <= 1; dc++) {
            int rr = r + dr2, ccx = c + dc;
            if (rr < 0 || ccx < 0 || rr >= nrow || ccx >= nc) continue;
            if (actPrev[IDX(rr, ccx)]) { frozen = true; break; }
          }
      }

      if (d < 0) {
        // Falling: fast and ungated. Too-high baselines suppress real contact,
        // which is the dangerous direction, and coming down can never eat signal.
        int32_t want = (-d) << 8;
        bq -= (want < fallStep) ? want : fallStep;
        ctel.adapted++;
      } else if (cc.release && (flags[i] & TF_RELEASABLE)) {
        int32_t want = d << 8;
        bq += (want < relStep) ? want : relStep;
        ctel.adapted++; ctel.released++;
      } else if (!frozen && d < (int32_t)cc.idleBand) {
        int32_t want = d << 8;
        bq += (want < riseStep) ? want : riseStep;
        ctel.adapted++;
      } else if (frozen) {
        ctel.frozen++;
      }

      // Guarantee 4: hard cap on cumulative upward drift. A taxel pinned here
      // is counted, so a phantom that comes back is visible in telemetry
      // rather than being rediscovered by staring at a heatmap.
      const int32_t capQ8 = ((int32_t)tare0[i] + (int32_t)cc.maxDrift) << 8;
      if (bq > capQ8) bq = capQ8;
      // "Pinned" means it is at the cap AND still wants to climb - a taxel that
      // merely sits there is not interesting. Flagged with >= rather than >
      // because the clamp above makes the strict test true for one frame only.
      if (bq >= capQ8 && d > 0) { flags[i] |= TF_CAPPED; ctel.capped++; }
      else flags[i] &= (uint8_t)~TF_CAPPED;
      baseQ8[i] = bq;

      int32_t delta = (int32_t)med - (bq >> 8);

      // ---- stage 6: one-euro ------------------------------------------
      float out;
      if (cc.euro) {
        float x  = (float)delta;
        float dx = ((float)delta - (float)prevIn[i]) * invDt;
        dxhat[i] += ad * (dx - dxhat[i]);
        float cutoff = cc.fcMin + cc.beta * fabsf(dxhat[i]);
        float a = alphaFor(cutoff, dt);
        xhat[i] += a * (x - xhat[i]);
        out = xhat[i];
      } else {
        out = (float)delta;
      }
      prevIn[i] = (int16_t)delta;

      // Not lrintf(): that is an out-of-line veneer call, once per taxel.
      int32_t fv = (int32_t)(out >= 0.0f ? out + 0.5f : out - 0.5f);
      if (fv >  32767) fv =  32767;
      if (fv < -32768) fv = -32768;
      filtMap[i] = (int16_t)fv;
      if (fv > ctel.peak) ctel.peak = (int16_t)fv;

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
          if (++onCnt[i] >= cc.nOn) { flags[i] |= TF_ACTIVE; onCnt[i] = 0; offCnt[i] = 0; }
        } else onCnt[i] = 0;
      } else {
        if (fv < sOff) {
          if (++offCnt[i] >= cc.nOff) { flags[i] &= (uint8_t)~TF_ACTIVE; offCnt[i] = 0; onCnt[i] = 0; }
        } else offCnt[i] = 0;
      }

      if (flags[i] & TF_ACTIVE) { stuckN[i]++; ctel.activeCells++; }
      else stuckN[i] = 0;
    }
  }

  // ---- stage 9: isolated-speck suppression -----------------------------
  // A real contact at 2 mm pitch spans several taxels. A lone active cell with
  // no active 4-neighbour is noise. This is deliberately NOT a 3x3 median,
  // which would also erase a genuinely thin contact - a probe tip, the edge of
  // a bracket. Done on a snapshot so the test is simultaneous.
  static uint8_t actSnap[MAX_TAXELS];
  if (cc.despeckle) {
    for (int r = 0; r < nrow; r++)
      for (int c = 0; c < nc; c++)
        actSnap[IDX(r, c)] = (flags[IDX(r, c)] & TF_ACTIVE) ? 1 : 0;
    for (int r = 0; r < nrow; r++)
      for (int c = 0; c < nc; c++) {
        int i = IDX(r, c);
        if (!actSnap[i]) continue;
        int n = 0;
        if (r > 0        && actSnap[i - MAX_COLS]) n++;
        if (r < nrow - 1 && actSnap[i + MAX_COLS]) n++;
        if (c > 0        && actSnap[i - 1]) n++;
        if (c < nc - 1   && actSnap[i + 1]) n++;
        if (n == 0) { flags[i] &= (uint8_t)~TF_ACTIVE; ctel.suppressed++; ctel.activeCells--; }
      }
  }

  // ---- stage 10: connected components ----------------------------------
  uint16_t nextLabel = 1;
  for (int r = 0; r < nrow; r++) {
    for (int c = 0; c < nc; c++) {
      int i = IDX(r, c);
      if (!(flags[i] & TF_ACTIVE)) { label[i] = 0; continue; }
      uint16_t up   = (r > 0) ? label[i - MAX_COLS] : 0;
      uint16_t left = (c > 0) ? label[i - 1] : 0;
      if (!up && !left) {
        if (nextLabel < MAX_LABELS) {
          lparent[nextLabel] = nextLabel;
          label[i] = nextLabel++;
        } else {
          label[i] = 0;               // pathological frame; treat as unlabelled
        }
      } else if (up && left) {
        label[i] = (up < left) ? up : left;
        if (up != left) lunion(up, left);
      } else {
        label[i] = up ? up : left;
      }
    }
  }

  for (uint16_t l = 1; l < nextLabel; l++) {
    larea[l] = 0; lsum[l] = 0; lpeak[l] = INT16_MIN; lpeakI[l] = 0;
    lsumR[l] = lsumC[l] = lwsum[l] = 0; ledge[l] = 0; laccept[l] = 0;
    lr0[l] = 255; lc0[l] = 255; lr1[l] = 0; lc1[l] = 0;
  }

  for (int r = 0; r < nrow; r++) {
    for (int c = 0; c < nc; c++) {
      int i = IDX(r, c);
      if (!label[i]) continue;
      uint16_t l = lfind(label[i]);
      label[i] = l;
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
      bool perim = (r == 0 || c == 0 || r == nrow - 1 || c == nc - 1) ||
                   !(flags[i - MAX_COLS] & TF_ACTIVE) ||
                   !(flags[i + MAX_COLS] & TF_ACTIVE) ||
                   !(flags[i - 1] & TF_ACTIVE) ||
                   !(flags[i + 1] & TF_ACTIVE);
      if (perim && varEma[i] > ledge[l]) ledge[l] = varEma[i];
    }
  }

  // ---- accept / reject, and build the contact list ---------------------
  nContacts = 0; nRejected = 0;
  for (uint16_t l = 1; l < nextLabel; l++) {
    if (lfind(l) != l || larea[l] == 0) continue;
    if (nContacts >= MAX_CONTACTS) {
      // More blobs than the wire format carries - a pathological frame. They
      // stay unaccepted so the map gates them out, and they are counted into
      // `suppressed` rather than into `nRejected`, which has to keep meaning
      // "how many of the transmitted records were rejected".
      laccept[l] = 2;
      ctel.suppressed++;
      continue;
    }
    bool accept = (larea[l] >= cc.minArea) && (lsum[l] >= cc.minSum);
    laccept[l] = accept ? 1 : 2;              // 1 accepted, 2 rejected

    Contact &k = contacts[nContacts++];
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
    if (accept) k.flags |= CF_ACCEPTED; else nRejected++;
    k.pad = 0;
  }

  // ---- release eligibility for the NEXT frame --------------------------
  // Guarantees 2 and 3 live here. A taxel may only be walked back if it has
  // been continuously active for a long time, nothing about it is moving, it
  // is not part of a blob big enough to be a real contact, and that blob's
  // perimeter is not alive. A 20-taxel grasp fails three of those four tests
  // no matter how long it sits there.
  for (int r = 0; r < nrow; r++) {
    for (int c = 0; c < nc; c++) {
      int i = IDX(r, c);
      flags[i] &= (uint8_t)~TF_RELEASABLE;
      if (!cc.release) continue;
      if (!(flags[i] & TF_ACTIVE)) continue;
      if (stuckN[i] < stuckLimit) continue;
      if (varEma[i] >= cc.stillness) continue;
      uint16_t l = label[i];
      if (l) {
        if (larea[l] >= cc.coherentArea) continue;     // coherent blob: never
        if (ledge[l] > sigmaQ4[i]) continue;           // live perimeter: never
      }
      flags[i] |= TF_RELEASABLE;
    }
  }

  // ---- output map ------------------------------------------------------
  for (int r = 0; r < nrow; r++)
    for (int c = 0; c < nc; c++) {
      int i = IDX(r, c);
      if (!cc.gateMap) { outMap[i] = filtMap[i]; continue; }
      uint16_t l = label[i];
      outMap[i] = (l && laccept[l] == 1) ? filtMap[i] : 0;
    }

  ctel.condUs = micros() - t_enter;
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

int condRefCompare(int thresh, int *maxDelta, int *worstIdx) {
  int n = 0, worst = -32768, wi = -1;
  if (!refValid) { if (maxDelta) *maxDelta = 0; if (worstIdx) *worstIdx = -1; return 0; }
  const int nc = nCols();
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nc; c++) {
      int i = IDX(r, c);
      int d = (int)tare0[i] - (int)refTare[i];
      if (d > worst) { worst = d; wi = i; }
      if (d > thresh) n++;
    }
  if (maxDelta) *maxDelta = worst;
  if (worstIdx) *worstIdx = wi;
  return n;
}

int condTareOutliers(int thresh, int *maxDelta, int *worstIdx, int *medianOut) {
  const int nc = nCols();
  const int total = cfg.rows * nc;
  if (total <= 0) return 0;

  static int16_t sorted[MAX_TAXELS];
  int k = 0;
  for (int r = 0; r < cfg.rows; r++)
    for (int c = 0; c < nc; c++) sorted[k++] = tare0[IDX(r, c)];
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
      int i = IDX(r, c);
      int d = (int)tare0[i] - med;
      if (d > worst) { worst = d; wi = i; }
      if (d > thresh) n++;
    }
  if (maxDelta) *maxDelta = worst;
  if (worstIdx) *worstIdx = wi;
  return n;
}
