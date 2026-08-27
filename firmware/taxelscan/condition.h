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
  // Absolute floors, counts. These dominate until sigma is characterised under
  // real service noise: measured sigma on a quiet bench is about 0.5 counts, so
  // kOn*sigma is ~3 and never reaches these.
  //
  // These are the `o sens 2` values. A soak on an untouched mat measured zero
  // false positives here, and zero again with the gate fully open, so the
  // conservative floors were only ever discarding real contacts. That result
  // came from a bench with no motors running; `o sens 0` restores the strict
  // values in one command if a noisier environment needs them.
  uint16_t minOn  = 5;
  uint16_t minOff = 2;
  uint8_t  nOn  = 2;             // debounce, frames
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
  int32_t  minSum    = 15;
  bool     gateMap   = true;     // zero taxels outside an accepted contact
};
extern CondCfg cc;

struct CondTelem {
  uint16_t adapted;      // taxels whose baseline moved this frame
  uint16_t frozen;       // taxels held because they or a neighbour are active
  uint16_t released;     // taxels the stuck-active timeout is walking back
  uint16_t capped;       // taxels pinned at the drift cap - a phantom may return
  uint16_t suppressed;   // isolated specks removed
  uint16_t activeCells;
  int16_t  peak;
  uint32_t condUs;       // how long this pipeline took - core 0's share
};
extern CondTelem ctel;

extern int16_t  outMap[MAX_TAXELS];      // conditioned, optionally gated
extern int16_t  filtMap[MAX_TAXELS];     // conditioned, never gated
extern Contact  contacts[MAX_CONTACTS];
extern uint8_t  nContacts;               // entries in contacts[], accepted first
extern uint8_t  nRejected;               // how many of those failed the gate
extern uint16_t sigmaQ4[MAX_TAXELS];     // per-taxel noise, counts in Q4

void condInit(void);
void condReset(void);                            // clear filter and baseline state
void condSeedBaseline(const int16_t *dr);        // adopt this frame as the tare
void condProcess(const int16_t *dr, float dt);   // run the pipeline
int32_t condBaseline(int r, int c);              // for the `B` dump
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
int  condRefCompare(int thresh, int *maxDelta, int *worstIdx);
// No stored reference: fall back to the array's own shape. A mat at rest is
// fairly uniform, so taxels far above the median are the suspicious ones.
int  condTareOutliers(int thresh, int *maxDelta, int *worstIdx, int *medianOut);
