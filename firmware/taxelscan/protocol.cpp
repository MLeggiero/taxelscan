#include "protocol.h"
#include <string.h>

bool sigmaCharacterised = false;
bool tareSuspect = false;
uint32_t lastEmitUs = 0;

// CRC16-CCITT, poly 0x1021, init 0xFFFF. Nibble table: 32 bytes of flash and
// four times faster than the bitwise loop, which matters at ~2.7 kB per frame.
static const uint16_t crcTab[16] = {
  0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
  0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF
};

uint16_t crc16(const uint8_t *p, size_t n) {
  uint16_t c = 0xFFFF;
  while (n--) {
    c = (uint16_t)((c << 4) ^ crcTab[((c >> 12) ^ (*p >> 4)) & 0x0F]);
    c = (uint16_t)((c << 4) ^ crcTab[((c >> 12) ^ (*p & 0x0F)) & 0x0F]);
    p++;
  }
  return c;
}

static uint8_t txbuf[3072];

void emitBinV2(void) {
  const int nc = nCols();
  const int nr = cfg.rows;
  const size_t mapBytes = (size_t)nr * nc * 2;
  const size_t conBytes = (size_t)nContacts * sizeof(Contact);
  const size_t payload  = mapBytes + conBytes + sizeof(FrameTrailer);

  if (22 + payload + 2 > sizeof(txbuf)) return;    // cannot happen at MAX_*

  static uint16_t seq = 0;
  uint8_t f = 0;
  if (cc.gateMap) f |= FF_GATED;
  if (cc.enable)  f |= FF_COND;
  if (cfg.darkRef) f |= FF_DARKREF;
  if (sigmaCharacterised) f |= FF_SIGMA;
  if (tareSuspect) f |= FF_TARE_SUSPECT;

  uint8_t *h = txbuf;
  h[0] = 'F'; h[1] = 'T';
  h[2] = FT_VERSION;
  h[3] = f;
  h[4] = (uint8_t)seq; h[5] = (uint8_t)(seq >> 8);
  h[6] = (uint8_t)nr;  h[7] = (uint8_t)nc;
  h[8] = (uint8_t)payload; h[9] = (uint8_t)(payload >> 8);
  uint32_t per = telem.periodUs;
  h[10] = (uint8_t)per; h[11] = (uint8_t)(per >> 8);
  h[12] = (uint8_t)(per >> 16); h[13] = (uint8_t)(per >> 24);
  h[14] = (uint8_t)telem.dieTempRaw; h[15] = (uint8_t)(telem.dieTempRaw >> 8);
  h[16] = (uint8_t)telem.railRaw;    h[17] = (uint8_t)(telem.railRaw >> 8);
  h[18] = nContacts;
  h[19] = nRejected;
  uint16_t hc = crc16(h, 20);
  h[20] = (uint8_t)hc; h[21] = (uint8_t)(hc >> 8);

  uint8_t *p = txbuf + 22;

  // The map is stored 32 wide regardless of geometry, so it is copied row by
  // row rather than in one block.
  for (int r = 0; r < nr; r++) {
    memcpy(p, &outMap[r * MAX_COLS], (size_t)nc * 2);
    p += (size_t)nc * 2;
  }

  if (conBytes) { memcpy(p, contacts, conBytes); p += conBytes; }

  FrameTrailer tr;
  tr.adapted     = ctel.adapted;
  tr.frozen      = ctel.frozen;
  tr.released    = ctel.released;
  tr.capped      = ctel.capped;
  tr.suppressed  = ctel.suppressed;
  tr.activeCells = ctel.activeCells;
  tr.overruns    = telem.overruns;
  tr.scanUs      = telem.scanUs;
  tr.condUs      = ctel.condUs;
  tr.emitUs      = lastEmitUs;
  memcpy(p, &tr, sizeof(tr)); p += sizeof(tr);

  uint16_t pc = crc16(txbuf + 22, payload);
  *p++ = (uint8_t)pc; *p++ = (uint8_t)(pc >> 8);

  Serial.write(txbuf, (size_t)(p - txbuf));
  seq++;
}
