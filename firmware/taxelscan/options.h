/*
 * options.h - the `o` console command's parameter table types.
 *
 * These live in a header rather than in the .ino because the Arduino
 * preprocessor injects generated prototypes above the first function
 * definition, which lands them before any type declared mid-sketch. A
 * function taking `const Param&` therefore fails to compile if Param is
 * declared in the .ino itself.
 *
 * Every tunable in the pipeline is reachable through this table on purpose.
 * The only honest way to pick a threshold, a dwell or a baseline rate is to
 * measure it on this mat, on this arm - so none of them are compile-time
 * constants that need a reflash to try.
 */
#pragma once

#include <Arduino.h>

enum PType { P_U8, P_U16, P_U32, P_F32, P_BOOL };

struct Param {
  const char *name;
  PType       t;
  void       *p;
  float       lo, hi;
  const char *help;
};
