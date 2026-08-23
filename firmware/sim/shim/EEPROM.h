// EEPROM shim: a plain RAM array. The sim never needs sigma to persist.
#pragma once
#include <Arduino.h>

class EEPROMShim {
 public:
  void begin(size_t n) { if (n > sizeof(buf)) n = sizeof(buf); size = n; }
  void commit() {}
  template <typename T> void get(int a, T &v) { memcpy(&v, buf + a, sizeof(T)); }
  template <typename T> void put(int a, const T &v) { memcpy(buf + a, &v, sizeof(T)); }
 private:
  uint8_t buf[4096] = {0};
  size_t  size = 0;
};
extern EEPROMShim EEPROM;
