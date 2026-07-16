#!/usr/bin/env python3
"""DMR Enhanced Privacy (RC4) decoder wrapper for OpenWebRX+.
Author: SP8MB

Adds an RC4-key-capable DMR listening path. Unlike digiham (no decryption),
this routes the demodulated signal through dsd-fme, which natively decrypts
DMR Enhanced Privacy (ALG 01 / RC4-40) and Basic Privacy given the key.

Live pipeline (inside OWRX+ chain):
  stdin complex-float IQ (48 kS/s, centred on the DMR carrier)
    -> FM discriminator (numpy)               -> raw s16 48k mono
    -> TCP server 127.0.0.1:<in_port>         -> dsd-fme -i tcp
       dsd-fme -1 <RC4KEY> -fs                 (decrypt + AMBE synth + PDU decode)
    -> dsd-fme -o udp:127.0.0.1:<out_port>    -> stdout s16 8k mono PCM
    -> dsd-fme stdout events -> JSON meta lines -> stderr

The key is passed via --key (hex, e.g. A1B2C3D4E5). With no key the chain still
works as a plain DMR decoder (encrypted traffic stays muted/marked encrypted).

Standalone validation (no OWRX needed):
  dmr_ep_decoder.py --selftest <discriminator.wav> --key A1B2C3D4E5
  dmr_ep_decoder.py --selftest-iq <capture.iq> --key A1B2C3D4E5   # cu8 1.024M, -250k
Emits the same JSON meta lines it would feed OWRX, plus a human summary on stderr.
"""

import argparse
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

IF_RATE = 48000          # complex IQ sample rate expected on stdin (OWRX IF)
DISC_RATE = 48000        # discriminator audio rate handed to dsd-fme
AUDIO_RATE = 8000        # dsd-fme synthesized voice output rate
DSDFME = shutil.which("dsd-fme") or "/usr/local/bin/dsd-fme"

# ---- dsd-fme event -> OWRX meta mapping ----------------------------------
# Lines dsd-fme prints (console mode) that carry who/what + crypto state.
_ANSI = re.compile(r"\x1b\[[0-9;]*[mK]")
_RE_SYNC = re.compile(r"Sync:\s*[+-]DMR\s+(\[slot1\]|slot1)\s+(\[slot2\]|slot2).*?Color Code=(\d+)")
_RE_HDR = re.compile(r"Slot (\d) Data Header.*?Source:\s*(\d+)\s*Target:\s*(\d+)")
_RE_PRE = re.compile(r"Preamble CSBK.*?Source:\s*(\d+)\s*-\s*Target:\s*(\d+)")
_RE_VOICE = re.compile(r"Slot (\d).*?(?:VC\d|Voice).*?TGT[=:]\s*(\d+).*?SRC[=:]\s*(\d+)", re.I)
_RE_VCALL = re.compile(r"SLOT (\d) TGT=(\d+) SRC=(\d+) FLCO=0x(\w+).*?SVC=0x(\w+)\s*(.*?)\s*(?:\(CRC|$)")
_RE_ENC = re.compile(r"Slot (\d) - Encrypted PDU")
_RE_PI = re.compile(r"ALG:\s*(\w+);\s*Key ID:\s*(\w+);\s*MI\(32\):\s*(\w+);\s*(RC4|AES\d*|DES|BP)")
_RE_KEYSET = re.compile(r"Encryption Key Value set to")
# MNIS LRRP position report — a multi-line block: "Lat: .. Lon: ..", then Radius, Speed.
_RE_LATLON = re.compile(r"Lat:\s*(-?\d+\.\d+)\s+Lon:\s*(-?\d+\.\d+)")
_RE_RADIUS = re.compile(r"Radius:\s*(\d+)\s*m")
_RE_SPEED = re.compile(r"Speed:\s*[\d.]+\s*m/s\s+([\d.]+)\s*km/h")
_RE_MBPDU = re.compile(r"Slot (\d) - Multi Block PDU Message")


# Meta sink: live path -> stderr (OWRX reads meta there, audio goes to stdout);
# selftest -> stdout (JSON only, human text on stderr).
_META = sys.stdout


def emit(meta):
    """Write one OWRX-style meta JSON line to the active meta stream."""
    _META.write(json.dumps(meta) + "\n")
    _META.flush()


def _flush_lrrp(state, sink, src=None):
    """Emit any pending LRRP fix as a meta event (-> OWRX map pin + panel)."""
    p = state.pop("lrrp_pend", None)
    if p and "lat" in p:
        sink({"protocol": "DMR", "lrrp": True, "slot": p.get("slot"),
              "source": src or p.get("src"), "lat": p["lat"], "lon": p["lon"],
              "radius": p.get("radius"), "speed": p.get("speed")})


def parse_line(line, state, sink):
    """Translate a single dsd-fme console line into OWRX DMR meta events."""
    line = _ANSI.sub("", line).rstrip()
    if not line:
        return

    if _RE_KEYSET.search(line):
        state["key_active"] = True   # dsd-fme accepted an RC4/DES key -> decryption on
        return

    # PI header: dsd-fme prints it for every encrypted PDU, key or not.
    # 'decrypted' is true only when a key is actually active.
    m = _RE_PI.search(line)
    if m:
        state["alg"], state["kid"], state["mi"], state["cipher"] = m.groups()
        sink({"protocol": "DMR", "slot": state.get("slot"), "encrypted": True,
              "decrypted": bool(state.get("key_active")), "alg": m.group(1),
              "keyid": m.group(2), "cipher": m.group(4), "mi": m.group(3)})
        return

    # dsd-fme emits this only when it could NOT decode the PDU (stayed encrypted).
    m = _RE_ENC.search(line)
    if m:
        sink({"protocol": "DMR", "slot": int(m.group(1)), "encrypted": True,
              "decrypted": False})
        return

    m = _RE_HDR.search(line) or _RE_PRE.search(line)
    if m:
        if m.re is _RE_HDR:
            slot, src, tgt = int(m.group(1)), m.group(2), m.group(3)
            if src and src != "0":
                # Data Header carries the true sender of the PDU whose payload (LRRP)
                # follows; remember it per slot. Preamble CSBKs must NOT overwrite this.
                state.setdefault("hdr_src", {})[slot] = src
                state["last_hdr_src"] = src
        else:
            slot, src, tgt = state.get("slot", 1), m.group(1), m.group(2)
        state["slot"] = slot
        sink({"protocol": "DMR", "slot": slot, "source": src, "target": tgt,
              "datatype": "data"})
        return

    # LRRP position (MNIS multi-block PDU): accumulate Lat/Lon (+ Radius/Speed), then emit
    # on the "Slot N - Multi Block PDU Message" trailer, tagged with slot N's Data-Header
    # sender (Preamble CSBKs printed in between carry a different flow's source).
    m = _RE_LATLON.search(line)
    if m:
        _flush_lrrp(state, sink, state.get("last_hdr_src"))   # flush a stale fix if any
        lat, lon = float(m.group(1)), float(m.group(2))
        if abs(lat) <= 90 and abs(lon) <= 180 and (abs(lat) > 1e-4 or abs(lon) > 1e-4):
            state["lrrp_pend"] = {"lat": lat, "lon": lon}
        return
    if "lrrp_pend" in state:
        m = _RE_RADIUS.search(line)
        if m:
            state["lrrp_pend"]["radius"] = int(m.group(1))
            return
        m = _RE_SPEED.search(line)
        if m:
            state["lrrp_pend"]["speed"] = float(m.group(1))
            return
    m = _RE_MBPDU.search(line)
    if m:
        slot = int(m.group(1))
        state["slot"] = slot
        if "lrrp_pend" in state:
            state["lrrp_pend"]["slot"] = slot
            src = (state.get("hdr_src") or {}).get(slot) or state.get("last_hdr_src")
            _flush_lrrp(state, sink, src)
        return

    m = _RE_VCALL.search(line)
    if m:
        slot, tgt, src, flco, svc = int(m.group(1)), m.group(2), m.group(3), m.group(4), m.group(5)
        enc = (int(svc, 16) & 0x40) != 0     # SVC bit6 = encrypted
        sink({"protocol": "DMR", "slot": slot, "source": src, "target": tgt,
              "datatype": "voice", "grouptype": flco, "encrypted": enc})
        return

    m = _RE_SYNC.search(line)
    if m:
        _flush_lrrp(state, sink, state.get("last_hdr_src"))   # safety net for an orphan fix
        active = 1 if m.group(1).startswith("[") else (2 if m.group(2).startswith("[") else None)
        state["cc"] = int(m.group(3))
        if active:
            state["slot"] = active
        return


# ---- FM discriminator (matches demod_iq.py used to crack the 148.512 key) --
def fm_discriminator(iq):
    import numpy as np
    d = np.angle(iq[1:] * np.conj(iq[:-1])).astype("float32")
    return d


def iq_to_disc_wav(iq_path, out_wav, fs_in=1024000.0, toff=-250e3, t0=0.0, t1=None):
    """Offline helper: cu8 IQ capture -> 48k discriminator wav (selftest-iq)."""
    import numpy as np
    import scipy.signal as sig
    import wave
    off = int(t0 * fs_in) * 2
    cnt = -1 if t1 is None else int((t1 - t0) * fs_in) * 2
    raw = np.fromfile(iq_path, dtype=np.uint8, count=cnt, offset=off)
    raw = raw[: len(raw) // 2 * 2]
    x = (raw[0::2].astype("float32") - 127.5) + 1j * (raw[1::2].astype("float32") - 127.5)
    t = np.arange(len(x)) / fs_in
    xbb = (x * np.exp(-1j * 2 * np.pi * toff * t)).astype("complex64")
    xb48 = sig.resample_poly(xbb, 3, 64).astype("complex64")
    h = sig.firwin(151, 6500, fs=48000.0).astype("float32")
    xch = sig.lfilter(h, 1.0, xb48)
    d = fm_discriminator(xch)
    strong = np.abs(xch[1:]) > np.percentile(np.abs(xch[1:]), 60)
    d = d - np.median(d[strong])
    sc = np.percentile(np.abs(d[strong]), 99.5) + 1e-9
    pcm = np.clip(d / sc * 0.5, -1, 1)
    pcm = (pcm * 32767).astype("int16")
    w = wave.open(out_wav, "w")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
    w.writeframes(pcm.tobytes()); w.close()
    return out_wav


def dsdfme_cmd(key, in_spec, out_spec, extra=None):
    # console mode (NO -N: ncurses needs a TTY); -Z logs PDU payloads/PI headers
    cmd = [DSDFME, "-fs", "-Z"]
    if key:
        cmd += ["-1", key]     # RC4/DES key value (DMR EP)
    cmd += ["-i", in_spec, "-o", out_spec]
    if extra:
        cmd += extra
    return cmd


# ---- selftest: run dsd-fme on a file, parse events, print meta ------------
def run_selftest(disc_wav, key):
    state, counts = {}, {"events": 0, "encrypted": 0, "decrypted": 0, "source_target": 0, "lrrp": 0}

    def sink(meta):
        counts["events"] += 1
        if meta.get("encrypted"):
            counts["encrypted"] += 1
        if meta.get("decrypted"):
            counts["decrypted"] += 1
        if meta.get("source") and meta.get("target"):
            counts["source_target"] += 1
        if meta.get("lrrp"):
            counts["lrrp"] += 1
        emit(meta)

    cmd = dsdfme_cmd(key, disc_wav, "udp")   # -o udp: harmless sink, no audio device needed
    sys.stderr.write("[dmr_ep] " + " ".join(cmd) + "\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=1, text=True)
    for line in proc.stdout:
        parse_line(line, state, sink)
    proc.wait()
    _flush_lrrp(state, sink, state.get("last_hdr_src"))   # emit final pending fix (file has no trailing sync)
    sys.stderr.write(
        "[dmr_ep] SELFTEST done: key=%s events=%d encrypted=%d decrypted=%d src/tgt=%d lrrp=%d cc=%s\n"
        % (key or "(none)", counts["events"], counts["encrypted"],
           counts["decrypted"], counts["source_target"], counts["lrrp"], state.get("cc"))
    )
    return counts


# ---- live path: IQ stdin -> disc -> dsd-fme (TCP in / UDP out) -> PCM stdout
def run_live(key, in_port=7355, out_port=23456):
    import numpy as np
    global _META
    _META = sys.stderr    # audio -> stdout, meta JSON -> stderr (OWRX module reads stderr)
    # 1) TCP server that streams the discriminator to dsd-fme (-i tcp)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", in_port)); srv.listen(1)
    # 2) UDP socket that receives dsd-fme decoded audio (-o udp:...)
    aud = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    aud.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    aud.bind(("127.0.0.1", out_port))

    cmd = dsdfme_cmd(key, "tcp", "udp:127.0.0.1:%d" % out_port)
    sys.stderr.write("[dmr_ep] live: " + " ".join(cmd) + "\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=1, text=True)

    def feed_disc():
        conn, _ = srv.accept()
        buf = b""
        while True:
            chunk = sys.stdin.buffer.read(4096 * 8)   # complex float32 = 8 B/sample
            if not chunk:
                break
            buf += chunk
            n = len(buf) // 8
            if n == 0:
                continue
            iq = np.frombuffer(buf[: n * 8], dtype=np.complex64)
            buf = buf[n * 8:]
            d = fm_discriminator(np.concatenate([iq[:1], iq]))  # keep length
            pcm = np.clip(d * 8000.0, -32767, 32767).astype("<i2")
            try:
                conn.sendall(pcm.tobytes())
            except OSError:
                break

    def forward_audio():
        while True:
            try:
                data, _ = aud.recvfrom(65535)
            except OSError:
                break
            sys.stdout.buffer.write(data)   # already s16 mono; OWRX expects 8k SHORT
            sys.stdout.buffer.flush()

    def read_meta():
        state = {}
        for line in proc.stdout:
            parse_line(line, state, emit)

    threading.Thread(target=feed_disc, daemon=True).start()
    threading.Thread(target=forward_audio, daemon=True).start()
    read_meta()
    proc.wait()


def _key_files():
    """Kandydaci na plik klucza (kolejność = priorytet). Panel WWW zapisuje do
    /var/lib/openwebrx (własność procesu openwebrx); /opt/... to fallback dev."""
    files = []
    ev = os.environ.get("DMR_EP_KEYFILE")
    if ev:
        files.append(ev)
    files.append("/var/lib/openwebrx/dmr_ep.key")
    files.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dmr_ep.key"))
    return files


# Podstawowa ścieżka zapisu z panelu WWW (obserwowana do hot-reload).
KEY_FILE = "/var/lib/openwebrx/dmr_ep.key"


def resolve_key(cli_key):
    """Key precedence: --key > $DMR_RC4_KEY > plik klucza (hex, komentarze '#')."""
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("DMR_RC4_KEY", "").strip()
    if env:
        return env
    for kf in _key_files():
        try:
            with open(kf) as f:
                for ln in f:
                    ln = ln.split("#", 1)[0].strip()
                    if ln:
                        return ln
        except OSError:
            continue
    return ""


def watch_key_and_reload(interval=2.0):
    """Hot-reload: gdy zmieni się dmr_ep.key (np. zapisany z panelu WWW), zrestartuj
    proces (execv) — deskryptory stdin/stdout/stderr przeżywają, więc OWRX pipe trwa,
    a dsd-fme startuje z nowym kluczem bez restartu OWRX."""
    def sig():
        best = None
        for kf in _key_files():
            try:
                m = os.stat(kf).st_mtime
            except OSError:
                continue
            if best is None or m > best:
                best = m
        return best

    last = sig()
    while True:
        time.sleep(interval)
        cur = sig()
        if cur != last:
            sys.stderr.write("[dmr_ep] dmr_ep.key zmieniony -> reload dekodera\n")
            sys.stderr.flush()
            os.execv(sys.executable, [sys.executable] + sys.argv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="",
                    help="RC4 Enhanced Privacy key (hex), e.g. A1B2C3D4E5 "
                         "(else $DMR_RC4_KEY or dmr_ep.key file)")
    ap.add_argument("--selftest", metavar="DISC_WAV", help="run on a 48k discriminator wav")
    ap.add_argument("--selftest-iq", metavar="IQ", help="run on a cu8 1.024M -250k IQ capture")
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--t1", type=float, default=None)
    args = ap.parse_args()

    if not os.path.exists(DSDFME):
        sys.stderr.write("[dmr_ep] ERROR: dsd-fme not found (%s)\n" % DSDFME)
        return 2

    key = resolve_key(args.key)
    if not key:
        sys.stderr.write("[dmr_ep] no RC4 key -> plain DMR (encrypted traffic stays encrypted)\n")

    if args.selftest_iq:
        tmp = args.selftest_iq + ".disc48k.wav"
        sys.stderr.write("[dmr_ep] demod IQ -> %s\n" % tmp)
        iq_to_disc_wav(args.selftest_iq, tmp, t0=args.t0, t1=args.t1)
        run_selftest(tmp, key)
    elif args.selftest:
        run_selftest(args.selftest, key)
    else:
        # live: bez klucza z CLI śledź plik klucza (panel WWW zapisuje dmr_ep.key)
        if not args.key:
            threading.Thread(target=watch_key_and_reload, daemon=True).start()
        run_live(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
