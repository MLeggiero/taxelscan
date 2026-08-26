/*
 * condition.h - the noise-rejection pipeline.
 *
 * Input is one dark-referenced frame from scan.cpp. Output is a conditioned map
 * plus a contact list. Ten stages, in order:
 *
 *   1  dark-referenced sampling      (scan.cpp)
 *   2  dwell oversampling            (scan.cpp)
 *   3  per-sample trim               (scan.cpp)
 *   4  temporal median-of-3          <- here
 *   5  adaptive baseline             <- here
 *   6  one-euro filter               <- here
 *   7  per-taxel sigma thresholds    <- here
 *   8  frame debounce                <- here
 *   9  isolated-speck suppression    <- here
 *   10 connected components + gate   <- here
 *
 * The median runs BEFORE the baseline rather than after it, which is a
 * deliberate departure from the stage order in the plan: feeding an
 * unmedianed value to the baseline lets a single ADC spike perturb the
 * baseline itself, and a corrupted baseline outlives the spike by minutes.
 *
 * The median is also not optional in front of the one-euro filter. One-euro
 * opens its cutoff in response to a large derivative - that is the entire point
 * of it - which means it CHASES impulses. Three frames of median in front is
 * what stops a single glitch from being tracked as a real onset.
 *
 * -------------------------------------------------------------------------
 * WHY A SUSTAINED CONTACT CANNOT BE ABSORBED
 *
 * An adaptive baseline that merely freezes while a taxel is active will
 * eventually eat a static grasp, and on a robot arm that is a safety defect,
 * not a cosmetic one. Five independent mechanisms guard against it, any one of
 * which holds on its own:
 *
 *   1  Freeze while active, plus a one-taxel halo, so a spreading contact is
 *      not nibbled at its edges.
 *   2  Spatial-coherence gate on release. The stuck-active timeout may only
 *      fire for taxels that are NOT part of a coherent blob. This maps straight
 *      onto the symptom: the thing to kill is small, the thing to keep is not.
 *   3  Edge-liveness. A real contact has a boundary of partly loaded taxels
 *      that fluctuate even when its centre is dead still. A live perimeter
 *      vetoes release regardless of area or elapsed time.
 *   4  A hard cap on cumulative upward drift. Even with every heuristic above
 *      failing at once, the worst case is a bounded, documented loss rather
 *      than an open-ended one. Taxels pinned at the cap are counted.
 *   5  It is all reported. Adapted, frozen, released and capped counts ride in
 *      every frame header, and the baseline map is readable on demand.
 *
 * On top of that the rates themselves are asymmetric, and the asymmetry is the
 * inverse of the naive one: FALLING is fast and ungated because a baseline that
 * is too high suppresses real contact and correcting it downward can never eat
 * signal; RISING is slow and heavily gated because that is the direction that
 * can. A real contact arrives in ~50 ms and is worth hundreds of counts; the
 * baseline climbs at a few counts per second.
 */
#pragma once

#include <Arduino.h>
#include "scan.h"

/*
 * One-euro arithmetic: fixed point (1) or float (0). FLOAT IS THE DEFAULT, and
 * that is a measured result rather than an assumption - it went the other way
 * from what the v2 plan expected.
 *
 * Removing the per-taxel VDIV.F32 looked like the obvious win for the
 * multi-sensor build. It is not, on any core with a hardware FPU. Counting the
 * instructions the stage actually compiles to (tools/armcycles.sh, which
 * cross-compiles this file and attributes each instruction to its source line):
 *
 *     core          float           fixed          verdict
 *     Cortex-M33    ~66 cycles      ~130 cycles    fixed is 2x WORSE
 *     Cortex-M7     ~80 cycles      ~92 cycles     fixed is ~15% worse
 *
 * VDIV.F32 costs 14 cycles and does not pipeline, but the integer replacement
 * needs three 64-bit multiply-shift chains to hold the intermediate ranges, and
 * those cost more than the divide they save. The FPU is simply the right tool
 * on these parts.
 *
 * The fixed path is kept, and kept proven equivalent, for two reasons: it is
 * the evidence for this decision, and it is ready if the target ever loses its
 * FPU. `make compare` in sim/ runs both builds against each other - they agree
 * to within one count of output rounding, with identical gating decisions on
 * every frame.
 */
#ifndef TAXEL_EURO_FIXED
#define TAXEL_EURO_FIXED 0
#endif

static const int MAX_CONTACTS = 32;

struct __attribute__((packed)) Contact {
  uint8_t  id;
  uint8_t  area;        // taxels, saturating at 255
  int32_t  sum;         // total delta - the force proxy
  int16_t  peak;
  uint8_t  peakR, peakC;
  int16_t  cenRQ8, cenCQ8;
  uint8_t  r0, c0, r1, c1;
  uint8_t  flags;       // bit0 edgeLive, bit1 accepted
  uint8_t  pad;
};                      // 20 bytes

static const uint8_t CF_EDGE_LIVE = 0x01;
static const uint8_t CF_ACCEPTED  = 0x02;

struct CondCfg {
  bool     enable      = true;   // off = baseline-subtracted passthrough

  // --- baseline (counts per second) --------------------------------------
  uint16_t fallRate    = 50;     // ungated: the safe direction
  uint16_t riseRate    = 3;      // gated: the direction that can eat contact
  uint16_t releaseRate = 10;     // for a taxel released by the stuck timeout
  uint16_t idleBand    = 40;     // only track up while the delta is below this
  uint16_t maxDrift    = 150;    // hard cap above the boot tare
  bool     haloFreeze  = true;   // also freeze the 8 neighbours of an active taxel

  // --- stuck-active release ----------------------------------------------
  bool     release     = true;
  uint16_t stuckSecs   = 60;     // continuous active time before release is considered
  uint16_t stillness   = 24;     // varEma below this (Q4) means nothing is changing
  uint8_t  coherentArea = 6;     // blobs at least this big are NEVER released

  // --- thresholds --------------------------------------------------------
  uint8_t  kOn  = 6;             // sigma multipliers
  uint8_t  kOff = 3;
  uint16_t minOn  = 25;          // absolute floors, counts
  uint16_t minOff = 12;
  uint8_t  nOn  = 3;             // debounce, frames
  uint8_t  nOff = 5;

  // --- filters -----------------------------------------------------------
  bool     median   = true;
  bool     euro     = true;
  float    fcMin    = 1.0f;      // Hz, cutoff at rest
  float    beta     = 0.05f;     // how fast the cutoff opens with motion
  float    dCutoff  = 1.0f;      // Hz, on the derivative estimate

  // --- spatial -----------------------------------------------------------
  bool     despeckle = true;
  uint8_t  minArea   = 2;        // 1 keeps thin contacts: a probe, a wire edge
  int32_t  minSum    = 120;
  bool     gateMap   = true;     // zero taxels outside an accepted contact
};
extern CondCfg cc;

/*
 * Telemetry is PER MAT.
 *
 * It was one frame-global accumulator when there was one mat, and summing
 * eight mats into it would quietly destroy the thing it is most useful for:
 * telling which mat is misbehaving. A sensor whose baseline is walking or
 * whose specks are piling up is invisible inside a total.
 *
 * It also makes the mats testable. The claim that they are independent is
 * checked by running mat 0 alone and then alongside seven busy neighbours and
 * requiring its output to be identical - and a shared counter fails that by
 * construction, whether or not anything is actually wrong.
 *
 * condUs is the exception and belongs to the frame, not to any one mat.
 */
struct CondTelem {
  uint16_t adapted;      // taxels whose baseline moved this frame
  uint16_t frozen;       // taxels held because they or a neighbour are active
  uint16_t released;     // taxels the stuck-active timeout is walking back
  uint16_t capped;       // taxels pinned at the drift cap - a phantom may return
  uint16_t suppressed;   // isolated specks removed
  uint16_t activeCells;
  int16_t  peak;
  uint32_t condUs;       // frame-level, stamped into [0]; see above
};
extern CondTelem ctel[MAX_SENSORS];

// Frame totals across every enabled mat, for the status pixel and `T`.
CondTelem condTelemAll(void);

extern int16_t  outMap [MAX_ALL_TAXELS]; // conditioned, optionally gated
extern int16_t  filtMap[MAX_ALL_TAXELS]; // conditioned, never gated
// Contacts are scoped to the mat they were found on: a blob never spans two
// surfaces, so labelling and the id space are per sensor - which is also what
// keeps the label arrays one mat's worth instead of growing with sensor count.
extern Contact  contacts [MAX_SENSORS][MAX_CONTACTS];
extern uint8_t  nContacts[MAX_SENSORS];  // entries in contacts[s], accepted first
extern uint8_t  nRejected[MAX_SENSORS];  // how many of those failed the gate
extern uint16_t sigmaQ4[MAX_ALL_TAXELS]; // per-taxel noise, counts in Q4

#if TAXEL_EURO_FIXED
// Test seam: lets the simulator sweep the shipped alphaQ16() against the float
// formula directly, rather than against a copy of it that could drift.
uint32_t condAlphaQ16Test(uint32_t xQ16);
#endif

void condInit(void);
void condReset(void);                            // clear filter and baseline state
void condSeedBaseline(int s, const int16_t *dr); // adopt this frame as the tare
void condProcess(const int16_t *dr, float dt);   // run the pipeline
int32_t condBaseline(int s, int r, int c);       // for the `B` dump
void    condSigmaDefault(uint16_t q4);

/*
 * Stored calibration: per-taxel sigma, and the REFERENCE TARE.
 *
 * The reference tare is the resting level the mat had when it was last known
 * good. It is never used as the live baseline - a tare from last week says
 * nothing about today's mounting or temperature - but comparing this boot's
 * tare against it answers a question nothing else can:
 *
 *   "was something already pressing on the mat when it powered up?"
 *
 * That matters because a startup tare is blind. Whatever is on the mat at boot
 * becomes the definition of zero, and a robot that boots with its hand against
 * a bracket will simply not see the bracket. The comparison catches it, and the
 * frame header carries a flag so the host knows the baseline is suspect.
 */
bool condCalLoad(void);       // true if a valid sigma table was found
void condCalSave(void);       // writes sigma + reference (parks core 1)
bool condRefValid(void);
void condRefAdopt(void);      // make the current tare the stored reference
// Compare this boot's tare against the stored reference. Returns the number of
// taxels more than `thresh` counts above it; fills the worst delta and index.
int  condRefCompare(int s, int thresh, int *maxDelta, int *worstIdx);
// No stored reference: fall back to the array's own shape. A mat at rest is
// fairly uniform, so taxels far above the median are the suspicious ones.
int  condTareOutliers(int s, int thresh, int *maxDelta, int *worstIdx, int *medianOut);
