#!/usr/bin/env python3
"""
Round-trip tests for the v2 frame format.

    python test_protocol.py

This exists because the v1 reader silently corrupted a whole session's data and
nobody noticed for hours: a dropped byte made it lock onto a false 'F','T'
inside the payload, after which the next frame's header decoded as pixel data.
Misaligned decoding repeats a fixed pattern down every row, which is exactly
what a row-axis hardware fault looks like - so the reader's bug was diagnosed as
a board fault and chased around the hardware for most of a day.

The frame builder below mirrors emitBinV2() in protocol.cpp byte for byte, so
these tests are also the format's specification. If the firmware layout changes
and this file is not updated, the tests fail - which is the point.

No serial port and no hardware required.
"""

import struct
import sys

from taxelscan_live import (crc16, scan_stream, HDR_LEN, CONTACT_LEN,
                           TRAILER_LEN, FT_VERSION, CF_ACCEPTED, CF_EDGE_LIVE)


def build(rows=4, cols=3, seq=7, flags=0x0F, us=25000, temp=800, rail=4000,
          vals=None, contacts=(), tel=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)):
    """Mirror of emitBinV2() in protocol.cpp."""
    if vals is None:
        vals = [(-1) ** i * (i * 37 % 900) for i in range(rows * cols)]
    body = struct.pack("<%dh" % (rows * cols), *vals)
    for k in contacts:
        body += struct.pack("<BBihBBhhBBBBBB", *k)
    body += struct.pack("<HHHHHHIIII", *tel)

    h = bytearray(20)
    h[0:2] = b"FT"
    h[2] = FT_VERSION
    h[3] = flags
    struct.pack_into("<H", h, 4, seq)
    h[6], h[7] = rows, cols
    struct.pack_into("<H", h, 8, len(body))
    struct.pack_into("<I", h, 10, us)
    struct.pack_into("<H", h, 14, temp)
    struct.pack_into("<H", h, 16, rail)
    h[18] = len(contacts)
    h[19] = sum(1 for k in contacts if not (k[12] & CF_ACCEPTED))
    frame = bytes(h) + struct.pack("<H", crc16(bytes(h)))
    frame += body + struct.pack("<H", crc16(body))
    assert len(frame) == HDR_LEN + len(body) + 2
    return frame, vals


FAILED = []


def check(name, cond, extra=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, extra))
        FAILED.append(name)


def main():
    print("frame layout")
    con = (3, 9, 12345, 640, 2, 1, 2 * 256 + 128, 1 * 256, 1, 0, 3, 2,
           CF_ACCEPTED | CF_EDGE_LIVE, 0)
    rej = (4, 1, 30, 30, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    f, vals = build(contacts=(con, rej))
    check("CONTACT_LEN is 20", CONTACT_LEN == 20, CONTACT_LEN)
    check("TRAILER_LEN is 28", TRAILER_LEN == 28, TRAILER_LEN)

    print("single frame round-trip")
    newest, consumed, nxt, dsy, bad = scan_stream(f)
    check("decodes", newest is not None)
    check("consumes exactly one frame", consumed == len(f), consumed)
    check("no desync", dsy == 0)
    check("no bad crc", bad == 0)
    check("geometry", (newest["rows"], newest["cols"]) == (4, 3))
    check("seq", newest["seq"] == 7)
    check("period", newest["us"] == 25000)
    check("map matches, negatives included",
          list(newest["data"]) == vals and min(vals) < 0)
    check("two contacts", len(newest["contacts"]) == 2)
    check("accepted flag", newest["contacts"][0]["accepted"] is True)
    check("edge-live flag", newest["contacts"][0]["edge_live"] is True)
    check("rejected flag", newest["contacts"][1]["accepted"] is False)
    check("rejected count in header", newest["rejected"] == 1)
    check("centroid q8 decode",
          abs(newest["contacts"][0]["cen_r"] - 2.5) < 1e-9,
          newest["contacts"][0]["cen_r"])
    check("telemetry", newest["tel"]["adapted"] == 1 and
          newest["tel"]["scan_us"] == 8)
    check("core-0 timings ride along", newest["tel"]["cond_us"] == 9 and
          newest["tel"]["emit_us"] == 10)

    print("back-to-back stream")
    a, _ = build(seq=1)
    b, _ = build(seq=2)
    c, _ = build(seq=3)
    newest, consumed, nxt, dsy, bad = scan_stream(a + b + c)
    check("keeps the newest", newest["seq"] == 3)
    check("consumes all three", consumed == len(a) + len(b) + len(c))
    check("no desync across a clean stream", dsy == 0)

    print("the v1 failure mode: 'FT' inside the payload")
    # 0x5446 little-endian is 'F','T' - the exact value that appeared as a
    # sample (21574) when the old reader lost sync.
    poison = [0x5446] * 12
    p, _ = build(seq=11, vals=poison)
    newest, consumed, nxt, dsy, bad = scan_stream(p + p)
    check("payload magic does not create a false frame",
          newest is not None and newest["seq"] == 11 and consumed == 2 * len(p),
          "consumed=%d of %d" % (consumed, 2 * len(p)))

    print("dropped byte mid-stream")
    stream = a + b[:20] + b[21:] + c          # one byte gone from frame b
    newest, consumed, nxt, dsy, bad = scan_stream(stream)
    check("still lands on a real frame", newest is not None)
    check("newest is the intact one after the gap", newest["seq"] == 3)
    check("the corrupted frame is never decoded as data", newest["seq"] != 2)
    check("desync is reported", dsy >= 1, "dsy=%d" % dsy)

    print("bit flip in the payload")
    bad_frame = bytearray(a)
    bad_frame[HDR_LEN + 4] ^= 0x01
    newest, consumed, nxt, dsy, bad = scan_stream(bytes(bad_frame))
    check("payload CRC rejects it", newest is None, newest)
    check("counted as bad crc", bad >= 1, "bad=%d" % bad)

    print("bit flip in the header")
    bad_frame = bytearray(a)
    bad_frame[8] ^= 0x40                       # corrupt the LENGTH field itself
    newest, consumed, nxt, dsy, bad = scan_stream(bytes(bad_frame))
    check("header CRC rejects a bad length before it is used", newest is None)

    print("partial frame at the tail")
    newest, consumed, nxt, dsy, bad = scan_stream(a + b[:30])
    check("waits for the rest instead of guessing",
          newest["seq"] == 1 and consumed == len(a))

    print("full-size frame fits the firmware tx buffer")
    big_con = tuple((i, 255, 100000, 4095, 31, 31, 0, 0, 0, 0, 31, 31, 2, 0)
                    for i in range(32))
    big, _ = build(rows=32, cols=32, vals=[0] * 1024, contacts=big_con)
    check("max frame <= 3072 bytes (txbuf in protocol.cpp)",
          len(big) <= 3072, len(big))
    newest, consumed, nxt, dsy, bad = scan_stream(big)
    check("max frame decodes", newest is not None and
          len(newest["contacts"]) == 32 and consumed == len(big))

    print()
    if FAILED:
        print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("all protocol tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
