#!/usr/bin/env python3
"""LoRa-APRS live decoder dla OpenWebRX+.
Author: SP8MB

IQ stdin (complex float32 @ IF_RATE) → kanalizacja do BW → detekcja preambuły CSS
→ sync (preambuła + SFD) → demod symboli → dekod ramki Semtech (LoRaPHY) → parse
TNC2 → zdarzenia JSON na stderr (typ "aprs"/"lora_frame"). Cisza PCM na stdout
(audio_loop), żeby utrzymać łańcuch audio OWRX — LoRa to dane, nie dźwięk.

Wzorzec I/O jak tetra_dmo_decoder.py. Rdzeń DSP/dekodu = lora_phy + lora_decode +
lora_semtech (zweryfikowane na żywym LoRa-APRS 434.855: SP8MB-9>APLRG1,WIDE1-1,...).
"""
import sys, os, time, json, threading, re
import numpy as np

try:
    from scipy.signal import decimate as _decimate, resample_poly as _resample
except Exception:
    _decimate = None
    _resample = None

# ── konfiguracja (env) ──
IF_RATE = int(os.environ.get("LORA_IF_RATE", "250000"))
BW = int(os.environ.get("LORA_BW", "125000"))
SF_LIST = [int(s) for s in os.environ.get("LORA_SF", "12,9,10,11,7,8").split(",")]
AUDIO_RATE = 8000
AUDIO_FRAME_SAMPLES = 480
AUDIO_FRAME_BYTES = AUDIO_FRAME_SAMPLES * 2

WIN_SEC = 2.6              # długość okna analizy
SLIDE_SEC = 2.0           # przesuw (overlap = WIN-SLIDE)
DEDUP_SEC = 8.0           # nie powtarzaj tej samej ramki przez N s
DEBUG = os.environ.get("LORA_DEBUG", "1") != "0"   # log do /tmp/lora_decoder.log

_DBG = None
def _dbg(msg):
    if not DEBUG:
        return
    global _DBG
    if _DBG is None:
        try:
            _DBG = open("/tmp/lora_decoder.log", "a")
        except Exception:
            _DBG = False
    if _DBG:
        try:
            _DBG.write(f"{time.time():.1f} {msg}\n"); _DBG.flush()
        except Exception:
            pass

# ── tablica whiteningu Semtech (LoRaPHY.m) ──
WHITENING = [
    0xff,0xfe,0xfc,0xf8,0xf0,0xe1,0xc2,0x85,0x0b,0x17,0x2f,0x5e,0xbc,0x78,0xf1,0xe3,
    0xc6,0x8d,0x1a,0x34,0x68,0xd0,0xa0,0x40,0x80,0x01,0x02,0x04,0x08,0x11,0x23,0x47,
    0x8e,0x1c,0x38,0x71,0xe2,0xc4,0x89,0x12,0x25,0x4b,0x97,0x2e,0x5c,0xb8,0x70,0xe0,
    0xc0,0x81,0x03,0x06,0x0c,0x19,0x32,0x64,0xc9,0x92,0x24,0x49,0x93,0x26,0x4d,0x9b,
    0x37,0x6e,0xdc,0xb9,0x72,0xe4,0xc8,0x90,0x20,0x41,0x82,0x05,0x0a,0x15,0x2b,0x56,
    0xad,0x5b,0xb6,0x6d,0xda,0xb5,0x6b,0xd6,0xac,0x59,0xb2,0x65,0xcb,0x96,0x2c,0x58,
    0xb0,0x61,0xc3,0x87,0x0f,0x1f,0x3e,0x7d,0xfb,0xf6,0xed,0xdb,0xb7,0x6f,0xde,0xbd,
    0x7a,0xf5,0xeb,0xd7,0xae,0x5d,0xba,0x74,0xe8,0xd1,0xa2,0x44,0x88,0x10,0x21,0x43,
    0x86,0x0d,0x1b,0x36,0x6c,0xd8,0xb1,0x63,0xc7,0x8f,0x1e,0x3c,0x79,0xf3,0xe7,0xce,
    0x9c,0x39,0x73,0xe6,0xcc,0x98,0x31,0x62,0xc5,0x8b,0x16,0x2d,0x5a,0xb4,0x69,0xd2,
    0xa4,0x48,0x91,0x22,0x45,0x8a,0x14,0x29,0x52,0xa5,0x4a,0x95,0x2a,0x54,0xa9,0x53,
    0xa7,0x4e,0x9d,0x3b,0x77,0xee,0xdd,0xbb,0x76,0xec,0xd9,0xb3,0x67,0xcf,0x9e,0x3d,
    0x7b,0xf7,0xef,0xdf,0xbf,0x7e,0xfd,0xfa,0xf4,0xe9,0xd3,0xa6,0x4c,0x99,0x33,0x66,
    0xcd,0x9a,0x35,0x6a,0xd4,0xa8,0x51,0xa3,0x46,0x8c,0x18,0x30,0x60,0xc1,0x83,0x07,
    0x0e,0x1d,0x3a,0x75,0xea,0xd5,0xaa,0x55,0xab,0x57,0xaf,0x5f,0xbe,0x7c,0xf9,0xf2,
    0xe5,0xca,0x94,0x28,0x50,0xa1,0x42,0x84,0x09,0x13,0x27,0x4f,0x9f,0x3f,0x7f,
]


# ───────────────────────── CSS PHY (lora_phy) ─────────────────────────
_CHIRP_CACHE = {}
def base_chirp(sf, up=True):
    key = (sf, up)
    c = _CHIRP_CACHE.get(key)
    if c is None:
        N = 1 << sf
        n = np.arange(N)
        phase = 2 * np.pi * (n * n / (2.0 * N) - n / 2.0)
        c = np.exp(1j * phase)
        if not up:
            c = np.conj(c)
        _CHIRP_CACHE[key] = c
    return c


def _spec(seg, base):
    return np.abs(np.fft.fft(seg * base))


def decim(x, dec):
    if dec <= 1:
        return x
    if _decimate is not None:
        out = x
        d = dec
        while d > 1:
            step = min(d, 8)
            while d % step:
                step -= 1
            out = _decimate(out, step, ftype="fir")
            d //= step
        return out
    return np.convolve(x, np.ones(dec) / dec, "same")[::dec]


# ───────────────────────── sync (lora_decode) ─────────────────────────
def find_preamble(xd, sf, thr=12, min_win=18):
    N = 1 << sf
    down = base_chirp(sf, up=False)
    hop = N // 4
    recs = []
    for off in range(0, len(xd) - N, hop):
        sp = _spec(xd[off:off + N], down)
        b = int(np.argmax(sp))
        recs.append((off, b, sp[b] / (np.mean(sp) + 1e-12)))
    if not recs:
        return None
    best = None
    i = 0
    while i < len(recs):
        if recs[i][2] < thr:
            i += 1; continue
        j = i
        while j + 1 < len(recs) and recs[j + 1][2] >= thr and abs(recs[j + 1][1] - recs[i][1]) <= 2:
            j += 1
        nwin = j - i + 1
        if nwin >= min_win and (best is None or nwin > best[3]):
            mb = int(np.median([recs[k][1] for k in range(i, j + 1)]))
            best = (recs[i][0], mb, float(np.mean([recs[k][2] for k in range(i, j + 1)])), nwin)
        i = j + 1
    if best is None:
        off, b, q = max(recs, key=lambda t: t[2])
        if q < thr:
            return None
        return (off, b, q)                    # fallback: pojedynczy najlepszy peak
    return best[:3]


def fine_sync(xd, sf, approx_start):
    N = 1 << sf
    down = base_chirp(sf, up=False)
    lo, hi = max(0, approx_start - N), min(len(xd) - N, approx_start + N)
    best_off, best_q = approx_start, -1
    for o in range(lo, hi):
        sp = _spec(xd[o:o + N], down)
        q = np.max(sp) / (np.mean(sp) + 1e-12)
        if q > best_q:
            best_q, best_off = q, o
    return best_off


def find_payload_start(xd, sf, pre_start, max_lookahead=16):
    N = 1 << sf
    up = base_chirp(sf, up=True)
    for k in range(0, max_lookahead):
        o = pre_start + k * N
        if o + N > len(xd):
            break
        sp = _spec(xd[o:o + N], up)
        dq = np.max(sp) / (np.mean(sp) + 1e-12)
        if k >= 6 and dq > 8:
            return pre_start + k * N + int(round(2.25 * N))
    return pre_start + int(round(12.25 * N))


def demod_payload(xd, sf, payload_start, pre_bin, nsyms):
    N = 1 << sf
    down = base_chirp(sf, up=False)
    syms = []
    for k in range(nsyms):
        o = payload_start + k * N
        if o + N > len(xd):
            break
        b = int(np.argmax(_spec(xd[o:o + N], down)))
        syms.append((b - pre_bin) % N)
    return syms


# ───────────────────────── dekod ramki Semtech (lora_semtech) ──────────
def _gray_coding(din, sf, ldr):
    N = 1 << sf
    out = []
    for i, v in enumerate(din):
        v = int(v)
        if i < 8 or ldr:
            v = v // 4
        else:
            v = (v - 1) % N
        out.append(v ^ (v >> 1))
    return out


def _circshift(row, k):
    n = len(row); k %= n
    return row[-k:] + row[:-k] if k else row[:]


def _diag_deinterleave(symbols, ppm):
    nsym = len(symbols)
    b = [[(symbols[x] >> (ppm - 1 - kk)) & 1 for kk in range(ppm)] for x in range(nsym)]
    sh = [_circshift(b[x], (1 - (x + 1))) for x in range(nsym)]
    cw = []
    for kk in range(ppm):
        val = 0
        for j in range(nsym):
            val |= (sh[j][kk] & 1) << j
        cw.append(val)
    return cw[::-1]


def _bitget(w, p):
    return (w >> (p - 1)) & 1


def _hamming_decode(cw, rdd):
    if rdd in (7, 8):
        p2 = _bitget(cw, 7) ^ _bitget(cw, 4) ^ _bitget(cw, 2) ^ _bitget(cw, 1)
        p3 = _bitget(cw, 5) ^ _bitget(cw, 3) ^ _bitget(cw, 2) ^ _bitget(cw, 1)
        p5 = _bitget(cw, 6) ^ _bitget(cw, 4) ^ _bitget(cw, 3) ^ _bitget(cw, 2)
        pf = {3: 4, 5: 8, 6: 1, 7: 2}.get(p2 * 4 + p3 * 2 + p5, 0)
        cw ^= pf
    return cw & 0xF


def semtech_decode(symbols, sf, ldr=0):
    N = 1 << sf
    g = _gray_coding(symbols, sf, ldr)
    if len(g) < 8:
        return None
    cwh = _diag_deinterleave(g[:8], sf - 2)
    nh = [_hamming_decode(c, 8) for c in cwh]
    if len(nh) < 5:
        return None
    payload_len = nh[0] * 16 + nh[1]
    crc = nh[2] & 1
    cr = nh[2] >> 1
    nibbles = nh[5:]
    header_ok = (1 <= cr <= 4) and (0 < payload_len <= 250)
    rdd = cr + 4
    ppm = sf - 2 * ldr
    ii = 8
    while ii + rdd <= len(g):
        cwp = _diag_deinterleave(g[ii:ii + rdd], ppm)
        nibbles += [_hamming_decode(c, rdd) for c in cwp]
        ii += rdd
    out = bytearray()
    for i in range(len(nibbles) // 2):
        out.append((nibbles[2 * i] & 0xF) | ((nibbles[2 * i + 1] & 0xF) << 4))
    L = min(payload_len, len(out))
    data = bytearray(out[i] ^ WHITENING[i % len(WHITENING)] for i in range(L))
    return {"payload_len": payload_len, "cr": cr, "crc": crc,
            "header_ok": header_ok, "data": bytes(data)}


# ───────────────────────── TNC2 / LoRa-APRS parse ─────────────────────
def parse_lora_aprs(data: bytes):
    """LoRa-APRS payload: [0x3C 0xFF 0x01] + 'SRC>DST,PATH:INFO' → dict dla AprsParser."""
    if len(data) >= 3 and data[0] == 0x3C:
        data = data[3:]                       # zdejmij nagłówek '<\xff\x01'
    try:
        text = data.decode("latin-1")
    except Exception:
        return None
    if ">" not in text or ":" not in text:
        return None
    src, rest = text.split(">", 1)
    hdr, info = rest.split(":", 1)
    parts = hdr.split(",")
    dest = parts[0].strip()
    path = [p.strip() for p in parts[1:] if p.strip()]
    src = src.strip()
    # walidacja regexem: znak ham na początku (toleruje przekłamany SSID/ogon)
    m = re.match(r"([A-Z]{1,2}\d[A-Z]{1,4})(?:[-/]([0-9A-Z]{1,2}))?", src.upper())
    if not m:
        return None
    call, ssid = m.group(1), m.group(2)
    src = f"{call}-{ssid}" if ssid and ssid.isalnum() else call
    info = info.split("\x00")[0]              # utnij ewentualne śmieci po NUL
    return {
        "source": src,
        "destination": dest if dest else "APRS",
        "path": path,
        "data": info.encode("latin-1", "replace"),
        "raw": data.hex(),
    }


# ───────────────────────── streaming ──────────────────────────────────
def best_frame(symbols, sf):
    """Wybierz najlepszy offset Δ + LDRO, zwróć (aprs_dict, meta) jeśli czytelny."""
    N = 1 << sf
    best = None
    for ldr in (0, 1):
        for d in range(-3, 4):
            s2 = [(s + d) % N for s in symbols]
            r = semtech_decode(s2, sf, ldr=ldr)
            if not r:
                continue
            ap = parse_lora_aprs(r["data"])
            if ap is not None:
                printable = sum(1 for c in ap["data"] if 32 <= c < 127)
                score = printable + (2 if r["header_ok"] else 0) + 5
                if best is None or score > best[0]:
                    best = (score, ap, r, d, ldr)
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


class LoRaStream:
    def __init__(self, emit):
        self.emit = emit
        self.dec = max(1, int(round(IF_RATE / BW)))
        self.fsd = IF_RATE // self.dec
        self.win_if = int(WIN_SEC * IF_RATE)       # okno liczone w próbkach IF
        self.slide_if = int(SLIDE_SEC * IF_RATE)
        self.buf_if = np.zeros(0, dtype=np.complex64)
        self.recent = {}                           # sygnatura ramki -> czas

    def feed_if(self, iq):
        self.buf_if = np.concatenate([self.buf_if, iq])
        # decymuj CAŁE okno naraz (ciągłe — bez glitchy na granicach chunków)
        while len(self.buf_if) >= self.win_if:
            w = self.buf_if[:self.win_if]
            xd = decim(w - np.mean(w), self.dec).astype(np.complex64)
            self._process(xd)
            self.buf_if = self.buf_if[self.slide_if:]

    def _process(self, xd):
        mag = np.abs(xd)
        med = np.median(mag) + 1e-12
        mx = float(np.max(mag))
        rms = float(np.sqrt(np.mean(mag ** 2)))
        passed = mx >= med * 4
        _dbg(f"okno rms={rms:.4f} max/med={mx/med:.1f} {'GATE-PASS' if passed else 'cisza'}")
        # tani gate: pomiń okna bez wyraźnej energii (brak ramki)
        if not passed:
            return
        # znajdź do kilku preambuł w oknie (kolejne frame'y), dekoduj każdą
        self._try_decode(xd, multi=True)

    def _try_decode(self, seg, multi=False):
        for sf in SF_LIST:
            N = 1 << sf
            if len(seg) < 14 * N:
                continue
            pre = find_preamble(seg, sf)
            if pre is None:
                continue
            pre_start, pre_bin, q = pre
            _dbg(f"sf{sf} preambuła q={q:.0f} @ {pre_start}")
            pre_start = fine_sync(seg, sf, pre_start)
            pre_bin = int(np.argmax(_spec(seg[pre_start:pre_start + N], base_chirp(sf, up=False))))
            payload_start = find_payload_start(seg, sf, pre_start)
            nsyms = max(8, (len(seg) - payload_start) // N)
            syms = demod_payload(seg, sf, payload_start, pre_bin, min(nsyms, 200))
            if len(syms) < 8:
                continue
            res = best_frame(syms, sf)
            _dbg(f"sf{sf} demod {len(syms)} symboli pre_bin={pre_bin} pls={payload_start} → "
                 f"best_frame={'OK' if res else 'None'}")
            if res is None:
                # diagnostyka: pokaż surowy dekod dla kilku Δ
                for dd in (1, 0, -1, 2, -2):
                    r = semtech_decode([(s + dd) % N for s in syms], sf)
                    if r:
                        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in r["data"][:36])
                        _dbg(f"   Δ={dd:+d} len={r['payload_len']} cr=4/{4+r['cr']} hdr={r['header_ok']} |{asc}|")
                continue
            ap, meta, delta, ldr = res
            sig = ap["source"] + "|" + ap["data"].decode("latin-1", "replace")[:24]
            now = time.time()
            self.recent = {k: v for k, v in self.recent.items() if now - v < DEDUP_SEC}
            if sig in self.recent:
                return
            self.recent[sig] = now
            self.emit({
                "type": "aprs", "sf": sf, "bw": BW,
                "source": ap["source"], "destination": ap["destination"],
                "path": ap["path"], "info": ap["data"].decode("latin-1", "replace"),
                "raw": ap["raw"], "crc": meta["crc"], "cr": meta["cr"],
            })
            return


# ───────────────────────── I/O ────────────────────────────────────────
def emit_frame(obj):
    """Zdekodowana ramka APRS → STDOUT (CHAR, pompowane do chainu → LoRaAprsParser → mapa)."""
    _dbg(f"★DEKOD {obj.get('source')} > {obj.get('destination')} : {obj.get('info','')[:50]}")
    try:
        sys.stdout.buffer.write((json.dumps(obj) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        pass


def emit_log(obj):
    """Status/błędy → STDERR (tylko log, nie pompowane do chainu)."""
    try:
        sys.stderr.write(json.dumps(obj) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def main():
    stream = LoRaStream(emit_frame)
    emit_log({"type": "lora_status", "msg": "LoRa-APRS decoder start",
              "if_rate": IF_RATE, "bw": BW, "sf": SF_LIST})
    try:
        while True:
            data = sys.stdin.buffer.read(16384)
            if not data:
                break
            iq = np.frombuffer(data, dtype=np.complex64)
            try:
                stream.feed_if(iq)
            except Exception as e:
                emit_log({"type": "lora_error", "msg": str(e)})
    except (KeyboardInterrupt, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
