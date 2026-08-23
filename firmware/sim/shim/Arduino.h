/*
 * Arduino.h shim - just enough of it to compile condition.cpp on a PC.
 *
 * The point of the sim is to exercise the SHIPPED conditioning code, not a
 * reimplementation of it. A Python twin of the baseline logic would test
 * whether the twin is correct, which is not the question. So condition.cpp is
 * compiled unmodified against these headers instead.
 */
#pragma once

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <math.h>
#include <stdlib.h>

#if defined(_MSC_VER)
// GCC attribute syntax is used for the packed wire structs. Layout does not
// matter in the sim - only the field values do - so it is simply dropped.
#define __attribute__(x)
#endif

typedef uint8_t byte;

// condProcess() times itself now; the sim just needs a monotonic microsecond
// clock for that to compile and return something sane.
#include <chrono>
static inline uint32_t micros() {
  using namespace std::chrono;
  return (uint32_t)duration_cast<microseconds>(
      steady_clock::now().time_since_epoch()).count();
}

#ifndef constrain
#define constrain(a, lo, hi) ((a) < (lo) ? (lo) : ((a) > (hi) ? (hi) : (a)))
#endif

// condSigmaSave() parks the other core before writing flash. There is no other
// core here.
struct RP2040Shim {
  void idleOtherCore() {}
  void resumeOtherCore() {}
};
extern RP2040Shim rp2040;
