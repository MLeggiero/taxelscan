/*
 * protocol.h - binary frame format v2.
 *
 * v1 was magic-only framing: 'F','T' then rows, cols, and the samples. That is
 * not robust at this data rate and this project has already paid for it. When
 * the host's serial buffer overflowed, bytes vanished mid-frame, the reader
 * locked onto a false 'F','T' inside the payload, and the NEXT frame's header
 * was decoded as pixel data. The tell was a sample reading 21574 - 'F','T'
 * little-endian, the magic itself. A misaligned decode also repeats a fixed
 * pattern down every row, which is indistinguishable from a real row-axis
 * hardware fault, and hours went into chasing one that did not exist.
 *
 * So v2 is explicitly framed:
 *
 *   offset  0   'F' 'T'                 magic
 *           2   version = 2             u8
 *           3   flags                   u8
 *           4   seq                     u16   increments every frame
 *           6   rows, cols              u8 u8
 *           8   payload length          u16   bytes after the header CRC
 *          10   frame period            u32   microseconds, measured
 *          14   die temperature raw     u16   ADC channel 4
 *          16   ROW_VCC raw             u16   ADC2, if D2 is wired to R5
 *          18   nContacts               u8
 *          19   nRejected               u8
 *          20   header CRC16            u16
 *          22   payload                       rows*cols int16, then contacts,
 *                                             then the telemetry trailer
 *               payload CRC16           u16
 *
 * The header carries its own CRC, checked BEFORE the length field is trusted.
 * A reader must never allocate or skip based on a length it has not verified -
 * that is precisely how a single dropped byte turned into a convincing fake
 * frame last time.
 *
 * Samples are SIGNED. Negative values are not an error: they are how a baseline
 * that has drifted too high announces itself, and clamping them away at the
 * source destroys the only evidence of the one drift direction that actually
 * endangers a robot.
 */
#pragma once

#include <Arduino.h>
#include "condition.h"

static const uint8_t FT_VERSION = 2;

static const uint8_t FF_GATED   = 0x01;   // map is gated to accepted contacts
static const uint8_t FF_COND    = 0x02;   // conditioning pipeline enabled
static const uint8_t FF_DARKREF = 0x04;   // dark reference subtracted
static const uint8_t FF_SIGMA   = 0x08;   // per-taxel sigma is characterised
// The startup tare looked loaded - something may have been pressing on the mat
// when it powered up, in which case that pressure is now the definition of zero
// and the sensor is blind to it. The host must be able to see this.
static const uint8_t FF_TARE_SUSPECT = 0x10;
// Conditioning is bypassed. Without this the host cannot tell a clean sensor
// from an unfiltered one, since both can look quiet.
static const uint8_t FF_RAW = 0x20;
extern bool tareSuspect;

struct __attribute__((packed)) FrameTrailer {
  uint16_t adapted;
  uint16_t frozen;
  uint16_t released;
  uint16_t capped;
  uint16_t suppressed;
  uint16_t activeCells;
  uint32_t overruns;
  uint32_t scanUs;      // core 1: the matrix scan
  uint32_t condUs;      // core 0: the conditioning pipeline
  uint32_t emitUs;      // core 0: CRC + USB write, for the PREVIOUS frame
};

uint16_t crc16(const uint8_t *p, size_t n);
void     emitBinV2(void);
extern bool sigmaCharacterised;
extern uint32_t lastEmitUs;
