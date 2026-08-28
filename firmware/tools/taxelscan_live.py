#!/usr/bin/env python3
"""
TaxelScan live viewer - protocol v2.

    python taxelscan_live.py --port COM10
    python taxelscan_live.py --port COM10 --log soak.csv

Opens the scanner's USB CDC port, streams binary frames, and serves a live
heatmap plus the contact list at http://localhost:8000 .

WHAT CHANGED FROM v1, AND WHY IT MATTERS

v1 framing was magic-only: find 'F','T' and trust what follows. When the OS
serial buffer overflowed, bytes vanished mid-frame, the reader locked onto a
false 'F','T' inside the payload, and the NEXT frame's header decoded as pixel
data. A misaligned decode repeats a fixed pattern down every row, which is
exactly what a row-axis hardware fault looks like - and hours went into chasing
one that did not exist. The tell, when it was finally spotted, was a sample
reading 21574: 'F','T' little-endian, the magic itself.

v2 carries a header CRC that is verified BEFORE the length field is used, and a
payload CRC over everything after it. A reader must never skip or allocate on a
length it has not checked. Frames that fail are counted and shown; a desync is
now impossible to mistake for sensor data.

Samples are SIGNED here. A negative value is not a glitch - it means that
taxel's baseline has drifted above the true resting level, which SUPPRESSES real
contact. That is the dangerous drift direction on a robot, so the page renders
it in its own colour rather than clamping it away.
"""

import argparse
import csv
import json
import re
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial

FT_VERSION = 2
HDR_LEN = 22
CONTACT_FMT = "<BBihBBhhBBBBBB"
CONTACT_LEN = struct.calcsize(CONTACT_FMT)     # 20
TRAILER_FMT = "<HHHHHHIIII"
TRAILER_LEN = struct.calcsize(TRAILER_FMT)     # 28

CF_EDGE_LIVE = 0x01
CF_ACCEPTED = 0x02

FF_GATED = 0x01
FF_COND = 0x02
FF_DARKREF = 0x04
FF_SIGMA = 0x08
FF_TARE_SUSPECT = 0x10
FF_RAW = 0x20

_CRC_TAB = [0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
            0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF]


def crc16(data):
    """CRC-16/CCITT-FALSE. Same nibble table as protocol.cpp."""
    c = 0xFFFF
    for b in data:
        c = ((c << 4) ^ _CRC_TAB[((c >> 12) ^ (b >> 4)) & 0x0F]) & 0xFFFF
        c = ((c << 4) ^ _CRC_TAB[((c >> 12) ^ (b & 0x0F)) & 0x0F]) & 0xFFFF
    return c


def die_temp_c(raw):
    v = raw * 3.3 / 4095.0
    return 27.0 - (v - 0.706) / 0.001721


def decode_frame(buf, i):
    """Decode one verified frame at offset i. Header CRC must already pass."""
    rows, cols = buf[i + 6], buf[i + 7]
    ncon, nrej = buf[i + 18], buf[i + 19]
    n = rows * cols
    vals = struct.unpack_from("<%dh" % n, buf, i + HDR_LEN)
    off = i + HDR_LEN + n * 2
    contacts = []
    for k in range(ncon):
        (cid, area, csum, peak, pr, pc, cr, cc_, r0, c0, r1, c1, fl,
         _pad) = struct.unpack_from(CONTACT_FMT, buf, off + k * CONTACT_LEN)
        contacts.append(dict(
            id=cid, area=area, sum=csum, peak=peak, peak_r=pr, peak_c=pc,
            cen_r=cr / 256.0, cen_c=cc_ / 256.0,
            r0=r0, c0=c0, r1=r1, c1=c1,
            accepted=bool(fl & CF_ACCEPTED),
            edge_live=bool(fl & CF_EDGE_LIVE)))
    off += ncon * CONTACT_LEN
    (adapted, frozen, released, capped, suppressed, active,
     overruns, scan_us, cond_us, emit_us) = struct.unpack_from(TRAILER_FMT, buf, off)
    tel = dict(adapted=adapted, frozen=frozen, released=released,
               capped=capped, suppressed=suppressed, active=active,
               overruns=overruns, scan_us=scan_us,
               cond_us=cond_us, emit_us=emit_us)
    return dict(rows=rows, cols=cols,
                seq=struct.unpack_from("<H", buf, i + 4)[0],
                flags=buf[i + 3],
                us=struct.unpack_from("<I", buf, i + 10)[0],
                temp_raw=struct.unpack_from("<H", buf, i + 14)[0],
                rail_raw=struct.unpack_from("<H", buf, i + 16)[0],
                data=vals, contacts=contacts, rejected=nrej, tel=tel)


def scan_stream(buf, expect_at=None):
    """Find every complete, CRC-valid frame in `buf`.

    Returns (newest, consumed, next_expect, desync, badcrc). Kept at module
    level rather than inside Scanner so it can be tested without a serial port -
    this is the code that silently corrupted a whole session's data last time,
    so it gets to be exercised on demand.
    """
    newest_off, consumed, pos = None, 0, 0
    desync = badcrc = 0
    while True:
        i = buf.find(b"FT", pos)
        if i < 0 or i + HDR_LEN > len(buf):
            break
        if buf[i + 2] != FT_VERSION:
            pos = i + 1
            continue
        # The header CRC is checked BEFORE the length is trusted. Never skip or
        # allocate on a length that has not been verified.
        if crc16(buf[i:i + 20]) != struct.unpack_from("<H", buf, i + 20)[0]:
            pos = i + 1
            continue
        plen = struct.unpack_from("<H", buf, i + 8)[0]
        end = i + HDR_LEN + plen + 2
        if end > len(buf):
            break                              # incomplete; wait for more bytes
        if expect_at is not None and i != expect_at:
            desync += 1
        newest_off = i
        pos = end
        expect_at = end
        consumed = end

    # Only the NEWEST frame gets its payload CRC checked, and only it is decoded.
    #
    # crc16 here is a pure-Python byte loop, and at 80 fps a full payload check on
    # every frame is ~200 kB/s through it - enough to starve the SSE thread of the
    # GIL and drag the display down to a third of its rate. The frames in between
    # are dropped for display anyway, so paying for them was pure waste.
    #
    # This does not weaken the framing guarantee that matters. Every frame is
    # still walked via its own verified 20-byte header, so a dropped byte still
    # breaks the chain and still shows up as a desync; what is skipped is only
    # payload verification on frames nobody will ever look at.
    newest = None
    if newest_off is not None:
        i = newest_off
        plen = struct.unpack_from("<H", buf, i + 8)[0]
        if crc16(buf[i + HDR_LEN:i + HDR_LEN + plen]) == \
                struct.unpack_from("<H", buf, i + HDR_LEN + plen)[0]:
            try:
                newest = decode_frame(buf, i)
            except struct.error:
                badcrc += 1
        else:
            badcrc += 1
    return newest, consumed, expect_at, desync, badcrc


# ----------------------------------------------------------------- serial side


class Scanner:
    """Owns the serial port. One reader thread keeps `latest` fresh."""

    def __init__(self, port, baud=115200, logpath=None):
        # 115200 only - opening an RP2350 CDC at 1200 baud triggers the
        # UF2 bootloader and drops the sketch.
        self.ser = serial.Serial(port, baud, timeout=0.2, write_timeout=1.0)
        try:
            self.ser.set_buffer_size(rx_size=1 << 18)   # Windows only
        except Exception:
            pass
        self.lock = threading.Lock()      # guards writes to the port
        self.latest = None
        self.seq = 0
        self.running = True
        self.capturing = False       # divert the reader to raw text for a command
        self.desync = 0              # valid frames that did not start where expected
        self.badcrc = 0              # frames rejected by the payload CRC
        self.stalls = 0              # times the watchdog had to restart the stream
        # Streamers wait on this instead of polling. Two independent pollers -
        # this reader on one cadence and each SSE loop on another - alias against
        # each other, and the measured result was ~11 events/s out of a possible
        # 30 with the board running at 80 fps. Waiting on the actual event
        # removes the beat entirely.
        #
        # A Condition rather than an Event on purpose: an Event has to be
        # cleared, and with two browser tabs open each one would clear wakeups
        # the other was waiting for. notify_all wakes every viewer, and each
        # tests its own sequence number.
        self.frame_cv = threading.Condition()
        self.last_rx = time.time()
        self.text_buf = bytearray()
        self.log = None
        self.logf = None
        if logpath:
            self.logf = open(logpath, "w", newline="")
            self.log = csv.writer(self.logf)
            self.log.writerow([
                "t", "seq", "period_us", "scan_us", "peak", "mean",
                "active", "adapted", "frozen", "released", "capped",
                "suppressed", "overruns", "cond_us", "emit_us", "contacts", "rejected",
                "die_c", "rail_v", "desync", "badcrc", "stalls"])
        time.sleep(2.0)
        self.ser.reset_input_buffer()

    def send(self, line):
        with self.lock:
            self.ser.write((line + "\n").encode())
            self.ser.flush()

    def start(self):
        self.send("x")
        time.sleep(0.3)
        with self.lock:
            self.ser.reset_input_buffer()
        self.send("m 2")          # binary frames
        time.sleep(0.2)
        self.send("c")            # continuous
        threading.Thread(target=self._reader, daemon=True).start()

    def stop(self):
        self.running = False
        time.sleep(0.3)
        try:
            self.send("x")
            time.sleep(0.2)
            self.send("m 0")
            self.ser.close()
        except Exception:
            pass
        if self.logf:
            self.logf.close()

    def run_text(self, command, secs=2.0, until=None):
        """Run a console command that answers in text, then resume streaming.

        The reader thread normally discards anything that is not a binary
        frame, so text replies are invisible to it. This hands the stream over
        for the duration of one command.
        """
        self.send("x")
        time.sleep(0.25)
        self.send("m 0")
        time.sleep(0.25)
        self.text_buf = bytearray()
        self.capturing = True
        time.sleep(0.1)
        token = until.encode() if until else None
        deadline = time.time() + secs
        try:
            self.send(command)
            while time.time() < deadline:
                if token and token in self.text_buf:
                    break
                time.sleep(0.05)
            out = bytes(self.text_buf)
            if token and token not in out:
                raise TimeoutError(
                    f"{command!r} did not finish within {secs:.0f} seconds")
            return out.decode("utf-8", "replace")
        finally:
            self.capturing = False
            self.send("m 2")
            time.sleep(0.15)
            self.send("c")

    def _reader(self):
        """Parse the stream, always keeping only the newest complete frame.

        Falling behind is the normal failure mode here, so the buffer is drained
        to the LAST valid frame each pass rather than the first. That keeps
        displayed latency flat.
        """
        buf = bytearray()
        expect_at = None
        while self.running:
            n = self.ser.in_waiting
            chunk = self.ser.read(n if n else 1)
            if self.capturing:
                if chunk:
                    self.text_buf.extend(chunk)
                else:
                    time.sleep(0.005)
                expect_at = None
                continue
            if chunk:
                buf.extend(chunk)
            if len(buf) > 1 << 20:
                del buf[: len(buf) - (1 << 18)]
                expect_at = None

            newest, consumed, expect_at, dsy, bad = scan_stream(buf, expect_at)
            self.desync += dsy
            self.badcrc += bad

            if newest is not None:
                del buf[:consumed]
                if expect_at is not None:
                    expect_at -= consumed
                vals = newest["data"]
                self.latest = newest
                self.last_rx = time.time()
                with self.frame_cv:
                    self.seq += 1
                    self.frame_cv.notify_all()
                if self.log:
                    self._logrow(newest)
            elif not chunk:
                time.sleep(0.005)

            # Watchdog. Any console command that needs the matrix stops the scan,
            # and a fire-and-forget /cmd never turns it back on - so one click on
            # "tare" used to silence the board permanently and look exactly like a
            # dead sensor. The firmware now resumes by itself; this is the second
            # line of defence, and it counts every kick so a stall is visible
            # instead of being quietly papered over.
            if not self.capturing and time.time() - self.last_rx > 1.5:
                self.stalls += 1
                self.send("m 2")
                time.sleep(0.05)
                self.send("c")
                self.last_rx = time.time()
                expect_at = None

    def _logrow(self, f):
        v = f["data"]
        t = f["tel"]
        self.log.writerow([
            "%.3f" % time.time(), f["seq"], f["us"], t["scan_us"],
            max(v), "%.2f" % (sum(v) / len(v)), t["active"], t["adapted"],
            t["frozen"], t["released"], t["capped"], t["suppressed"],
            t["overruns"], t["cond_us"], t["emit_us"],
            len(f["contacts"]) - f["rejected"], f["rejected"],
            "%.1f" % die_temp_c(f["temp_raw"]),
            "%.3f" % (f["rail_raw"] * 3.3 / 4095.0),
            self.desync, self.badcrc, self.stalls])
        self.logf.flush()


# ------------------------------------------------------------------- web side

PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>TaxelScan live</title>
<style>
  :root{
    --bg:#0f1115; --panel:#171a21; --line:#272c36;
    --fg:#e6e9ef; --muted:#9aa3b2; --accent:#5ac8fa; --warn:#ff6b57;
    --ok:#3fbf6f; --amber:#d8a13c;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  header{padding:14px 18px;border-bottom:1px solid var(--line);
         display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
  h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.02em}
  .stat{color:var(--muted)}
  .stat b{color:var(--fg);font-weight:600}
  .stat b.bad{color:var(--warn)}
  .stat b.warn{color:var(--amber)}
  #tel{padding:8px 18px;border-bottom:1px solid var(--line);color:var(--muted);
       display:flex;gap:16px;flex-wrap:wrap;font-size:12px}
  #tel b{color:var(--fg);font-weight:600}
  main{display:flex;gap:18px;padding:18px;align-items:flex-start;flex-wrap:wrap}
  #wrap{position:relative;background:var(--panel);border:1px solid var(--line);
        border-radius:10px;padding:12px}
  canvas{display:block;border-radius:4px;cursor:crosshair}
  aside{background:var(--panel);border:1px solid var(--line);border-radius:10px;
        padding:14px 16px;min-width:270px;max-width:340px}
  aside h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;
           color:var(--muted);margin:0 0 10px}
  .row{display:flex;justify-content:space-between;gap:12px;padding:3px 0}
  .row span:last-child{color:var(--fg);font-variant-numeric:tabular-nums}
  .row span:first-child{color:var(--muted)}
  button{background:#222733;color:var(--fg);border:1px solid var(--line);
         border-radius:6px;padding:7px 11px;font:inherit;font-size:13px;
         cursor:pointer;margin:3px 3px 0 0}
  button:hover{border-color:var(--accent);color:var(--accent)}
  button.on{background:var(--accent);color:#08131a;border-color:var(--accent)}
  label{display:flex;align-items:center;gap:8px;color:var(--muted);padding:5px 0}
  .hint{color:var(--muted);font-size:11px;line-height:1.4;margin:7px 0 0}
  input[type=range]{flex:1;accent-color:var(--accent)}
  #banner{display:none;margin:0 18px 0;padding:11px 14px;border-radius:8px;
          background:rgba(255,107,87,.12);border:1px solid var(--warn);
          color:var(--warn);font-size:13px}
  #banner.show{display:block}
  #tip{position:absolute;pointer-events:none;background:#000d;border:1px solid var(--line);
       border-radius:5px;padding:3px 7px;font-size:12px;display:none;white-space:nowrap}
  #bar{height:12px;border-radius:3px;margin:8px 0 4px;
       background:linear-gradient(90deg,#0b0d12,#1b3a6b,#1f7a8c,#3fbf6f,#d8d84a,#f08a3c,#e33d2e)}
  .ticks{display:flex;justify-content:space-between;color:var(--muted);font-size:11px}
  #ov{position:fixed;inset:0;background:#0b0d12f2;z-index:50;display:none;
      flex-direction:column;align-items:center;justify-content:center;gap:18px;text-align:center}
  #ovStep{color:var(--muted);font-size:15px;letter-spacing:.1em}
  #ovLabel{font-size:40px;font-weight:600}
  #ovAct{font-size:26px;color:var(--muted)}
  #ovAct.rec{color:#3fbf6f}
  #ovGrid{display:grid;grid-template-columns:repeat(3,42px);gap:8px;margin-top:6px}
  #ovGrid div{width:42px;height:42px;border-radius:7px;border:1px solid var(--line);background:#161a22}
  #ovGrid div.done{background:#1f7a8c}
  #ovGrid div.now{background:#3fbf6f;border-color:#3fbf6f}
  #ovBar{width:340px;height:6px;border-radius:3px;background:#222733;overflow:hidden}
  #ovBar i{display:block;height:100%;width:0;background:#3fbf6f}
  #ovPeak{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
  table.t{margin-top:10px;font-size:11.5px;border-collapse:collapse;width:100%}
  table.t th,table.t td{text-align:left;padding:3px 5px;border-bottom:1px solid var(--line)}
  table.t th{color:var(--muted);font-weight:400}
  table.t td.bad{color:var(--warn)}
  table.t td.ok{color:var(--ok)}
</style></head><body>
<header>
  <h1>TaxelScan live</h1>
  <span class="stat">geom <b id="geom">-</b></span>
  <span class="stat">peak <b id="peak">-</b></span>
  <span class="stat">mean <b id="mean">-</b></span>
  <span class="stat">frame <b id="ft">-</b></span>
  <span class="stat">ui <b id="fps">-</b></span>
  <span class="stat">contacts <b id="ncon">-</b></span>
  <span class="stat">desync <b id="desync">0</b></span>
  <span class="stat">badcrc <b id="badcrc">0</b></span>
  <span class="stat">stalls <b id="stalls">0</b></span>
</header>
<div id="tel">
  <span>pipeline <b id="tCond">-</b></span>
  <span>gate <b id="tGate">-</b></span>
  <span>darkref <b id="tDark">-</b></span>
  <span>sigma <b id="tSig">-</b></span>
  <span>tare <b id="tTare">-</b></span>
  <span>active <b id="tAct">-</b></span>
  <span>adapted <b id="tAdp">-</b></span>
  <span>frozen <b id="tFrz">-</b></span>
  <span>released <b id="tRel">-</b></span>
  <span>capped <b id="tCap">-</b></span>
  <span>specks <b id="tSup">-</b></span>
  <span>overruns <b id="tOvr">-</b></span>
  <span>scan <b id="tScan">-</b></span>
  <span>cond <b id="tCond0">-</b></span>
  <span>core0 <b id="tCore0">-</b></span>
  <span>die <b id="tTemp">-</b></span>
  <span>ROW_VCC <b id="tRail">-</b></span>
</div>
<div id="banner"></div>
<div id="ov">
  <div id="ovStep">1 / 9</div>
  <div id="ovLabel">-</div>
  <div id="ovAct">-</div>
  <div id="ovBar"><i></i></div>
  <div id="ovPeak">-</div>
  <div id="ovGrid"></div>
  <button id="ovCancel" style="margin-top:14px">cancel</button>
</div>
<main>
  <div id="wrap"><canvas id="cv"></canvas><div id="tip"></div></div>
  <aside>
    <h2>Scale</h2>
    <div id="bar"></div>
    <div class="ticks"><span>0</span><span id="smid">-</span><span id="smax">-</span></div>
    <label>max <input type="range" id="scale" min="20" max="4095" value="400"></label>
    <button id="auto">autoscale</button>
    <button id="hold">peak hold</button>
    <button id="rst">reset peak</button>
    <button id="boxes" class="on">contact boxes</button>
    <div class="hint">Purple cells read NEGATIVE: that taxel's baseline has
      drifted above its true resting level, which suppresses real contact.
      That is the dangerous drift direction, so it is never clamped away.</div>

    <h2 style="margin-top:16px">Contacts</h2>
    <table class="t" id="con"></table>
    <div class="hint">Rejected blobs are still reported, never silently dropped.
      A blob is rejected when it is smaller than <code>minarea</code> or weaker
      than <code>minsum</code> - that is what removes the leftover specks.</div>

    <h2 style="margin-top:16px">Pipeline</h2>
    <button data-cmd="t">tare</button>
    <button data-cmd="z">toggle gate</button>
    <button data-cmd="R">reset state</button>
    <button data-cmd="T">time a frame</button>
    <button data-cmd="X">save boot reference</button>
    <div class="hint">Clear the mat first. Records the resting level so future       startups can tell whether something was already pressing on the pad - the       one check a startup tare cannot make on its own.</div>
    <div style="margin-top:6px">
      <button data-cmd="o cond 1">pipeline on</button>
      <button data-cmd="o cond 0">pipeline off</button>
    <button data-cmd="o raw 1">raw (no conditioning)</button>
    <button data-cmd="o raw 0">raw off</button>
    <h2 style="margin-top:16px">Sensitivity</h2>
    <button data-cmd="o sens 0">0 strict</button>
    <button data-cmd="o sens 1">1 default</button>
    <button data-cmd="o sens 2">2 sensitive</button>
    <button data-cmd="o sens 3">3 max</button>
    <div class="hint">Moves minon, minoff, minarea, minsum and the debounce as a
      group. Level 3 accepts single taxels and turns speck removal off, so it
      shows everything the sensor can see. Measured false positive rate on an
      untouched mat was zero at all four levels on the bench, but that was a
      bench. Re-check with the arm powered before running at 2 or 3.</div>
      <button data-cmd="o darkref 1">darkref on</button>
      <button data-cmd="o darkref 0">darkref off</button>
    </div>
    <div class="hint">A/B is the only way to attribute an improvement. Turn the
      pipeline off to see the raw dark-referenced frame; turn darkref off too to
      see what the bring-up sketch saw.</div>
    <button id="charac">characterise noise (30 s)</button>
    <div class="hint">Mat at rest, arm powered and moving if you can. Measures
      per-taxel sigma and saves it to flash - thresholds then adapt to each
      taxel instead of one global guess.</div>
    <div id="characOut"></div>

    <h2 style="margin-top:16px">Row axis</h2>
    <button id="rowtest">test row independence</button>
    <div class="hint">Holds each row high on its own and compares against
      all-rows-low. Press and hold when told.</div>
    <div id="rowverdict"></div>

    <h2 style="margin-top:16px">9-point scan</h2>
    <button id="scan">start guided scan</button>
    <table class="t" id="res"></table>

    <h2 style="margin-top:16px">Geometry</h2>
    <button data-cmd="g 32 12 1">32 x 12</button>
    <button data-cmd="g 32 16 2">32 x 32</button>
    <button data-cmd="g 32 16 1">32 x 16 (bank A)</button>
    <button data-cmd="g 16 16 2">16 x 32</button>
    <div class="hint">changing geometry clears the pipeline state - re-tare after</div>

    <h2 style="margin-top:16px">Cursor</h2>
    <div class="row"><span>taxel</span><span id="cur">-</span></div>
    <div class="row"><span>value</span><span id="curv">-</span></div>
    <div class="row"><span>peak</span><span id="curp">-</span></div>
  </aside>
</main>
<script>
const cv=document.getElementById('cv'), cx=cv.getContext('2d');
const CELL=18;
let rows=0,cols=0,data=null,peakArr=null,autoS=false,holdMode=false,smax=400;
let frames=0,lastT=performance.now(),hoverIdx=-1,contacts=[],showBoxes=true;

// perceptual-ish ramp: dark -> blue -> teal -> green -> yellow -> orange -> red
const STOPS=[[11,13,18],[27,58,107],[31,122,140],[63,191,111],[216,216,74],[240,138,60],[227,61,46]];
function ramp(t){
  t=Math.max(0,Math.min(1,t))*(STOPS.length-1);
  const i=Math.min(STOPS.length-2,Math.floor(t)), f=t-i;
  const a=STOPS[i], b=STOPS[i+1];
  return `rgb(${a[0]+(b[0]-a[0])*f|0},${a[1]+(b[1]-a[1])*f|0},${a[2]+(b[2]-a[2])*f|0})`;
}
// Negative gets its own ramp. It is not noise and it is not zero: it is a
// baseline sitting above the true rest level, i.e. lost sensitivity.
function negRamp(t){
  t=Math.max(0,Math.min(1,t));
  return `rgb(${60+120*t|0},${20+10*t|0},${80+140*t|0})`;
}
function draw(){
  if(!data) return;
  const src = holdMode && peakArr ? peakArr : data;
  let hi=0,sum=0,lo=0;
  for(const v of src){ if(v>hi)hi=v; if(v<lo)lo=v; sum+=v; }
  const top = autoS ? Math.max(20,hi) : smax;
  document.getElementById('smax').textContent=top;
  document.getElementById('smid').textContent=(top/2)|0;
  for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){
    const v=src[r*cols+c];
    cx.fillStyle = v<0 ? negRamp(-v/Math.max(20,-lo)) : ramp(v/top);
    cx.fillRect(c*CELL,r*CELL,CELL-1,CELL-1);
  }
  if(showBoxes) for(const k of contacts){
    cx.lineWidth=1.5;
    cx.strokeStyle=k.accepted?'#3fbf6f':'#ff6b57';
    cx.setLineDash(k.accepted?[]:[3,3]);
    cx.strokeRect(k.c0*CELL-1.5,k.r0*CELL-1.5,
                  (k.c1-k.c0+1)*CELL,(k.r1-k.r0+1)*CELL);
    cx.setLineDash([]);
    cx.fillStyle=k.accepted?'#3fbf6f':'#ff6b57';
    cx.beginPath();
    cx.arc((k.cen_c+0.5)*CELL-0.5,(k.cen_r+0.5)*CELL-0.5,2.5,0,6.284);
    cx.fill();
  }
  document.getElementById('peak').textContent=hi;
  document.getElementById('mean').textContent=(sum/src.length).toFixed(1);
  if(hoverIdx>=0){
    document.getElementById('curv').textContent=data[hoverIdx];
    document.getElementById('curp').textContent=peakArr?peakArr[hoverIdx]:'-';
  }
}
let _conSig='';
function renderContacts(){
  const sig=contacts.map(k=>k.id+','+k.area+','+k.sum+','+k.accepted).join(';');
  if(sig===_conSig) return;
  _conSig=sig;
  let h='<tr><th>#</th><th>area</th><th>sum</th><th>peak</th><th>at</th><th></th></tr>';
  for(const k of contacts){
    h+='<tr><td>'+k.id+'</td><td>'+k.area+'</td><td>'+k.sum+'</td><td>'+k.peak+'</td>'+
       '<td>r'+k.cen_r.toFixed(1)+' c'+k.cen_c.toFixed(1)+'</td>'+
       '<td class="'+(k.accepted?'ok':'bad')+'">'+(k.accepted?'ok':'rejected')+
       (k.edge_live?' &middot; live':'')+'</td></tr>';
  }
  if(!contacts.length) h+='<tr><td colspan="6" style="color:#9aa3b2">none</td></tr>';
  document.getElementById('con').innerHTML=h;
}
// The telemetry strip and the contact table used to be rebuilt on every single
// frame. At 30 Hz that is a few hundred DOM writes a second for values that
// almost never change, and it was enough to make the page feel like treacle.
// Everything below only touches the DOM when the value it shows actually moved.
const _statCache={};
function setStat(id,val,cls){
  const k=id+'|'+val+'|'+(cls||'');
  if(_statCache[id]===k) return;
  _statCache[id]=k;
  const e=document.getElementById(id); e.textContent=val; e.className=cls||'';
}
// ---- guided 9-point scan -------------------------------------------------
const POINTS=[["top-left","TOP-LEFT corner"],["top-center","TOP edge, middle"],
  ["top-right","TOP-RIGHT corner"],["mid-left","LEFT edge, middle"],
  ["center","CENTRE of the pad"],["mid-right","RIGHT edge, middle"],
  ["bottom-left","BOTTOM-LEFT corner"],["bottom-center","BOTTOM edge, middle"],
  ["bottom-right","BOTTOM-RIGHT corner"]];
const READY=3, HOLD=4, ACTIVE=60;
let scanning=false, recording=false, acc=null, results=[], cancelled=false;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

const ovGrid=document.getElementById('ovGrid');
POINTS.forEach(()=>ovGrid.appendChild(document.createElement('div')));

function ovSet(i,label,act,rec,frac,peak){
  document.getElementById('ovStep').textContent=(i+1)+' / 9';
  document.getElementById('ovLabel').textContent=label;
  const a=document.getElementById('ovAct'); a.textContent=act; a.className=rec?'rec':'';
  document.getElementById('ovBar').firstElementChild.style.width=(frac*100)+'%';
  document.getElementById('ovPeak').textContent=rec?('live peak '+peak):'';
  [...ovGrid.children].forEach((d,k)=>d.className=k<i?'done':(k===i?'now':''));
}
function summarize(key,label,a){
  if(!a) return {key,label,peak:0,r:-1,c:-1,rows:[],cols:[]};
  let peak=0,idx=0;
  for(let j=0;j<a.length;j++) if(a[j]>peak){peak=a[j];idx=j;}
  const rs=new Set(), cs=new Set();
  for(let j=0;j<a.length;j++) if(a[j]>=ACTIVE){rs.add((j/cols)|0); cs.add(j%cols);}
  return {key,label,peak,r:(idx/cols)|0,c:idx%cols,
          rows:[...rs].sort((x,y)=>x-y),cols:[...cs].sort((x,y)=>x-y)};
}
async function runScan(){
  if(scanning) return;
  scanning=true; cancelled=false; results=[];
  document.getElementById('ov').style.display='flex';
  for(let i=0;i<POINTS.length && !cancelled;i++){
    const point=POINTS[i], label=point[1];
    for(let t=READY;t>0 && !cancelled;t--){
      ovSet(i,label,'get ready... '+t,false,1-t/READY,0); await sleep(1000);
    }
    if(cancelled) break;
    acc=null; recording=true;
    const t0=performance.now();
    while(performance.now()-t0 < HOLD*1000 && !cancelled){
      const el=performance.now()-t0;
      ovSet(i,label,'PRESS AND HOLD  '+Math.ceil((HOLD*1000-el)/1000),true,
            el/(HOLD*1000), acc?Math.max.apply(null,acc):0);
      await sleep(100);
    }
    recording=false;
    results.push(summarize(point[0],label,acc));
  }
  document.getElementById('ov').style.display='none';
  scanning=false;
  if(!cancelled){
    renderResults();
    fetch('/results',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({rows:rows,cols:cols,points:results})});
  }
}
function renderResults(){
  let h='<tr><th>point</th><th>peak</th><th>at</th><th>rows</th><th>cols</th></tr>';
  for(const p of results){
    const dead=p.peak<ACTIVE;
    h+='<tr><td>'+p.label.split(',')[0]+'</td>'+
       '<td class="'+(dead?'bad':'')+'">'+p.peak+'</td>'+
       '<td>'+(dead?'-':'r'+p.r+' c'+p.c)+'</td>'+
       '<td>'+p.rows.length+'</td><td>'+p.cols.length+'</td></tr>';
  }
  document.getElementById('res').innerHTML=h;
}
async function runRowTest(){
  if(scanning) return;
  scanning=true; cancelled=false;
  const ov=document.getElementById('ov'); ov.style.display='flex';
  let res=null, err=null;
  const fire=(async()=>{ try{ res=await (await fetch('/rowtest')).json(); }
                         catch(e){ err=e; } })();
  const TOTAL=9000, t0=performance.now();
  while(performance.now()-t0 < TOTAL && !cancelled){
    const el=performance.now()-t0;
    ovSet(0,'PRESS AND HOLD','testing rows  '+Math.ceil((TOTAL-el)/1000),true,
          el/TOTAL, data?Math.max.apply(null,data):0);
    await sleep(100);
  }
  await fire;
  ov.style.display='none'; scanning=false;
  const box=document.getElementById('rowverdict');
  if(err||!res){ box.innerHTML='<div class="hint">test failed</div>'; return; }
  const colour={'rows independent':'#3fbf6f','no conduction':'#ff6b57',
                'press drifted':'#d8d84a','rows respond, shape unclear':'#d8d84a',
                'probe failed':'#ff6b57'}[res.verdict]||'#9aa3b2';
  box.innerHTML='<div style="margin-top:10px;padding:9px 11px;border-radius:7px;'+
    'border:1px solid '+colour+';color:'+colour+';font-size:12px;line-height:1.5">'+
    '<b>'+res.verdict.toUpperCase()+'</b><br>'+res.detail+'</div>';
}
async function runCharac(){
  if(scanning) return;
  scanning=true; cancelled=false;
  const ov=document.getElementById('ov'); ov.style.display='flex';
  let res=null, err=null, finished=false;
  const fire=(async()=>{
    try{
      const reply=await fetch('/characterise');
      const body=await reply.text();
      if(!reply.ok) throw new Error(body||('HTTP '+reply.status));
      res=body;
    }catch(e){ err=e; }
    finally{ finished=true; }
  })();
  const TOTAL=50000, t0=performance.now();
  try{
    while(!finished && performance.now()-t0 < TOTAL && !cancelled){
      const el=performance.now()-t0;
      ovSet(0,'DO NOT TOUCH THE MAT','measuring noise  '+
            Math.max(0,Math.ceil((TOTAL-el)/1000))+'s',false,
            Math.min(1,el/TOTAL),0);
      await sleep(200);
    }
    await fire;
  }finally{
    ov.style.display='none'; scanning=false;
  }
  if(err){
    document.getElementById('characOut').innerHTML=
      '<pre class="hint" style="white-space:pre-wrap;color:#ff6b57">'+
      'noise measurement failed: '+err.message+'</pre>';
    return;
  }
  const lines=(res||'').split('\n').filter(l=>l.startsWith('# sigma')||l.startsWith('#   r'));
  document.getElementById('characOut').innerHTML=
    '<pre class="hint" style="white-space:pre-wrap">'+lines.join('\n')+'</pre>';
}
document.getElementById('rowtest').addEventListener('click',runRowTest);
document.getElementById('scan').addEventListener('click',runScan);
document.getElementById('charac').addEventListener('click',runCharac);
document.getElementById('ovCancel').addEventListener('click',function(){cancelled=true;});

const es=new EventSource('/stream');
es.onmessage=e=>{
  const m=JSON.parse(e.data);
  if(m.rows!==rows||m.cols!==cols){
    rows=m.rows;cols=m.cols;cv.width=cols*CELL;cv.height=rows*CELL;
    document.getElementById('geom').textContent=rows+'x'+cols;
  }
  data=m.data; contacts=m.contacts;
  if(!peakArr || peakArr.length!==data.length) peakArr=data.slice();
  else for(let j=0;j<data.length;j++) if(data[j]>peakArr[j]) peakArr[j]=data[j];
  if(recording){
    if(!acc || acc.length!==data.length) acc=data.slice();
    else for(let j=0;j<data.length;j++) if(data[j]>acc[j]) acc[j]=data[j];
  }
  const t=m.tel;
  document.getElementById('ft').textContent=(m.us/1000).toFixed(1)+' ms';
  setStat('ncon',(m.contacts.length-m.rejected)+' / '+m.rejected+' rej');
  setStat('desync',m.desync,m.desync>0?'bad':'');
  setStat('badcrc',m.badcrc,m.badcrc>0?'bad':'');
  setStat('stalls',m.stalls||0,(m.stalls>0)?'warn':'');
  setStat('tCond',(m.flags&32)?'RAW':((m.flags&2)?'on':'OFF'),(m.flags&34)?((m.flags&32)?'bad':''):'warn');
  setStat('tGate',(m.flags&1)?'on':'off');
  setStat('tDark',(m.flags&4)?'on':'OFF',(m.flags&4)?'':'warn');
  setStat('tSig',(m.flags&8)?'measured':'default',(m.flags&8)?'':'warn');
  setStat('tTare',(m.flags&16)?'SUSPECT':'clear',(m.flags&16)?'bad':'');
  setStat('tAct',t.active);
  setStat('tAdp',t.adapted);
  setStat('tFrz',t.frozen);
  setStat('tRel',t.released,t.released>0?'warn':'');
  setStat('tCap',t.capped,t.capped>0?'bad':'');
  setStat('tSup',t.suppressed);
  setStat('tOvr',t.overruns,t.overruns>0?'bad':'');
  setStat('tScan',(t.scan_us/1000).toFixed(1)+' ms');
  setStat('tCond0',(t.cond_us/1000).toFixed(2)+' ms');
  {const c0=(t.cond_us+t.emit_us)/10/m.us*1000;
   setStat('tCore0',c0.toFixed(0)+'%',c0>80?'bad':(c0>60?'warn':''));}
  setStat('tTemp',m.die_c.toFixed(1)+' C');
  setStat('tRail',m.rail_v.toFixed(2)+' V');

  const b=document.getElementById('banner');
  let msg='';
  if(m.flags&32)
    msg='Conditioning is bypassed (o raw). This is the unfiltered signal: no baseline, '+
        'no filtering, no contact detection. Set "o raw 0" to return to normal.';
  else if(m.flags&16)
    msg='The startup tare looked loaded - something may have been pressing on the '+
        'mat when it powered up. That pressure is now the definition of zero, so the '+
        'sensor is BLIND to it until it is removed. Clear the mat and press tare.';
  else if(t.overruns>0)
    msg='The scan does not fit the frame period: '+t.overruns+' overruns. dt is '+
        'wrong for every filter downstream. Click "time a frame" and follow what it says.';
  else if(t.capped>0)
    msg=t.capped+' taxels are pinned at the drift cap. Their baseline cannot follow '+
        'any further, so a phantom will reappear there. Re-tare between tasks, or raise '+
        'maxdrift once you know how far it actually needs to go.';
  else if(!(m.flags&8))
    msg='Per-taxel sigma has never been measured, so every threshold is a single global '+
        'guess. Run "characterise noise" with the mat at rest.';
  if(msg){ b.className='show'; b.textContent=msg; } else b.className='';
  frames++; draw(); renderContacts();
};
setInterval(()=>{const n=performance.now();
  document.getElementById('fps').textContent=(frames*1000/(n-lastT)).toFixed(0)+' fps';
  frames=0;lastT=n;},1000);

cv.addEventListener('mousemove',e=>{
  const b=cv.getBoundingClientRect();
  const c=Math.floor((e.clientX-b.left)/CELL), r=Math.floor((e.clientY-b.top)/CELL);
  if(r<0||c<0||r>=rows||c>=cols){hoverIdx=-1;return;}
  hoverIdx=r*cols+c;
  document.getElementById('cur').textContent='r'+r+' c'+c;
  const t=document.getElementById('tip');
  t.style.display='block'; t.style.left=(c*CELL+24)+'px'; t.style.top=(r*CELL+4)+'px';
  t.textContent='r'+r+' c'+c+' = '+(data?data[hoverIdx]:'-');
});
cv.addEventListener('mouseleave',()=>{hoverIdx=-1;document.getElementById('tip').style.display='none';});

document.getElementById('scale').addEventListener('input',e=>{smax=+e.target.value;draw();});
document.getElementById('auto').addEventListener('click',e=>{
  autoS=!autoS;e.target.classList.toggle('on',autoS);draw();});
document.getElementById('hold').addEventListener('click',e=>{
  holdMode=!holdMode;e.target.classList.toggle('on',holdMode);draw();});
document.getElementById('rst').addEventListener('click',()=>{peakArr=null;draw();});
document.getElementById('boxes').addEventListener('click',e=>{
  showBoxes=!showBoxes;e.target.classList.toggle('on',showBoxes);draw();});
document.querySelectorAll('button[data-cmd]').forEach(b=>
  b.addEventListener('click',()=>fetch('/cmd?c='+encodeURIComponent(b.dataset.cmd))));
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    scanner = None
    results = None          # last completed 9-point scan, readable at /results
    lastrow = None          # last row probe, readable at /rowtest/last
    rowlock = threading.Lock()   # one probe at a time - it owns the serial port

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/results":
            n = int(self.headers.get("Content-Length", 0))
            try:
                Handler.results = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                Handler.results = None
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/cmd"):
            from urllib.parse import urlparse, parse_qs
            c = parse_qs(urlparse(self.path).query).get("c", [""])[0]
            if c:
                self.scanner.send(c)
            self.send_response(204)
            self.end_headers()
            return

        if self.path == "/characterise":
            if not Handler.rowlock.acquire(blocking=False):
                self.send_response(409)
                self.end_headers()
                return
            try:
                raw = self.scanner.run_text(
                    "n 30", 50.0, until="# thresholds now")
            except Exception as exc:
                body = ("noise measurement failed: " + str(exc)).encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            finally:
                Handler.rowlock.release()
            body = raw.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/rowtest/last":
            self._json(Handler.lastrow or {"verdict": "not run yet"})
            return

        if self.path == "/rowtest":
            if not Handler.rowlock.acquire(blocking=False):
                self.send_response(409)
                self.end_headers()
                return
            try:
                raw = self.scanner.run_text("q", 5.0)
            finally:
                Handler.rowlock.release()
            self._json(self._rowverdict(raw))
            return

        if self.path == "/results":
            self._json(Handler.results or {})
            return

        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = -1
            nextsend = 0.0
            try:
                while True:
                    # Block until a frame actually lands rather than sleeping a
                    # fixed slice and hoping. The timeout is only a liveness
                    # check so a dead board still lets the handler exit.
                    cv = self.scanner.frame_cv
                    with cv:
                        cv.wait_for(lambda: self.scanner.seq != last, timeout=0.5)
                        s = self.scanner.seq
                        f = self.scanner.latest
                    now = time.perf_counter()
                    if now < nextsend:
                        continue
                    if s != last and f:
                        last = s
                        nextsend = now + 1.0 / 30.0     # cap the push rate
                        payload = {
                            "rows": f["rows"], "cols": f["cols"], "us": f["us"],
                            "flags": f["flags"], "seq": f["seq"],
                            "data": list(f["data"]),
                            "contacts": f["contacts"], "rejected": f["rejected"],
                            "tel": f["tel"],
                            "die_c": die_temp_c(f["temp_raw"]),
                            "rail_v": f["rail_raw"] * 3.3 / 4095.0,
                            "desync": self.scanner.desync,
                            "badcrc": self.scanner.badcrc,
                            "stalls": self.scanner.stalls,
                        }
                        self.wfile.write(
                            b"data: " + json.dumps(payload).encode() + b"\n\n")
                        self.wfile.flush()
                    time.sleep(1 / 30)      # cap the push rate
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- row probe interpretation -----------------------------------------
    @staticmethod
    def _rowverdict(raw):
        refs, rows = {}, []
        for line in raw.splitlines():
            m = re.match(r"#\s*ref\s+(\w+)\s*:\s*((?:\s*\d+)+)", line)
            if m:
                refs[m.group(1)] = [int(x) for x in m.group(2).split()]
                continue
            m = re.match(r"#\s*(\d+)\s+(-?\d+)\s+(-?\d+)\s+(\d+)\s*$", line)
            if m:
                rows.append(dict(row=int(m.group(1)), delta=int(m.group(2)),
                                 col=int(m.group(3)), seq=int(m.group(4))))

        none_s = refs.get("none_s", [])
        ncol = len(none_s)
        # Columns whose baseline sits well above floor regardless of drive.
        # These must not be folded into a max-over-columns summary, which is
        # exactly the mistake that produced a wrong verdict before.
        stuck = [(c, none_s[c]) for c in range(ncol) if none_s[c] >= 60]

        best = max((r["delta"] for r in rows), default=0)
        live = [r["row"] for r in rows if r["delta"] >= 40]

        # Does the signal follow row INDEX (spatial) or probe SEQ (the
        # operator's hand drifting)? Whichever ordering the deltas are
        # smoother along is the axis the signal really lives on.
        def roughness(key):
            d = [r["delta"] for r in sorted(rows, key=lambda x: x[key])]
            if len(d) < 3:
                return 0.0
            return sum(abs(d[i] - d[i - 1]) for i in range(1, len(d))) / (len(d) - 1)

        rough_row, rough_seq = roughness("row"), roughness("seq")
        spatial = rough_row < rough_seq * 0.7
        temporal = rough_seq < rough_row * 0.7

        if not refs or not rows:
            verdict = "probe failed"
            detail = "The board did not answer the row probe. Check the firmware is current."
        elif best < 40:
            verdict = "no conduction"
            detail = ("No row moves any column by more than %d counts against its own "
                      "bracketing baseline. Nothing connects the driven rows to the "
                      "sense columns: suspect the row tail at J1, the column tail at "
                      "J2, or the mat itself." % best)
        elif temporal:
            verdict = "press drifted"
            detail = ("The response is smooth in probe ORDER (%.0f) but jagged by row "
                      "index (%.0f), so it tracks your hand over time rather than "
                      "position. Hold a steady, constant press and run it again."
                      % (rough_seq, rough_row))
        elif spatial:
            verdict = "rows independent"
            detail = ("%d of %d rows respond, peak delta %d counts, and the profile is "
                      "smooth by row index (%.0f) not by probe order (%.0f) - so it is "
                      "spatial, not your hand drifting. The row axis works."
                      % (len(live), len(rows), best, rough_row, rough_seq))
        else:
            verdict = "rows respond, shape unclear"
            detail = ("%d rows respond with peak delta %d, but the profile is not clearly "
                      "smoother by row index (%.0f) than by probe order (%.0f). Press one "
                      "spot firmly and steadily, then re-run to confirm."
                      % (len(live), best, rough_row, rough_seq))

        if stuck:
            detail += ("  Separately: column%s %s read %s counts with ALL rows low and "
                       "do not follow the row drive - a standing offset independent of "
                       "the matrix." %
                       ("s" if len(stuck) > 1 else "",
                        ", ".join(str(c) for c, _ in stuck),
                        ", ".join(str(v) for _, v in stuck)))

        payload = {"verdict": verdict, "detail": detail, "stuck_cols": stuck,
                   "live_rows": live, "best_delta": best, "rows": rows,
                   "refs": refs, "raw": raw, "at": time.strftime("%H:%M:%S")}
        Handler.lastrow = payload
        return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--http", type=int, default=8000)
    ap.add_argument("--log", help="append per-frame telemetry to this CSV "
                                  "(this is what a soak test reads)")
    args = ap.parse_args()

    sc = Scanner(args.port, logpath=args.log)
    sc.start()
    Handler.scanner = sc
    srv = ThreadingHTTPServer(("127.0.0.1", args.http), Handler)
    print(f"TaxelScan live viewer -> http://localhost:{args.http}   (ctrl-c to quit)")
    if args.log:
        print(f"logging telemetry to {args.log}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sc.stop()


if __name__ == "__main__":
    main()
