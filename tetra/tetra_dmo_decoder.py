#!/usr/bin/env python3
"""TETRA DMO live decoder for OpenWebRX+.
Author: SP8MB

Live counterpart of dmo_burst_extract.py + dmo_l1_chain.py + dmo_pdu_parser.py:
processes IQ stream from stdin, emits PCM audio on stdout, JSON meta events
on stderr (same format jak tetra_decoder.py for TMO).

Pipeline:
  IQ stdin (cf32 @36kS/s) → tetra_demod.py subprocess (GR DQPSK demod)
    → bits → burst search → L1 chain → PDU parser → meta events on stderr
  Audio output: silence PCM (TCH live path TODO)
"""
import sys, os, time, json, threading, subprocess
from collections import deque
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from dmo_l1_chain import (tetra_descramble, block_deinterleave, rcpc_depunct_2_3,
                          viterbi_cch_decode, crc16_itut_bits,
                          BLK1_LEN_BITS, SB1_TYPE1_BITS, SB1_TYPE2_BITS,
                          SB1_INTERLEAVE_A, SB2_TYPE1_BITS, SB2_TYPE2_BITS,
                          SB2_INTERLEAVE_A, BLK2_LEN_BITS, TETRA_CRC_OK,
                          SCRAMB_INIT_BSCH)
from dmo_pdu_parser import parse_sync_pdu, format_sync_pdu

# Sync training sequences (ETSI EN 300 396-2)
Y_BITS = np.array([1,1, 0,0, 0,0, 0,1, 1,0, 0,1, 1,1, 0,0, 1,1, 1,0, 1,0,
                   0,1, 1,1, 0,0, 0,0, 0,1, 1,0, 0,1, 1,1], dtype=np.int8)
N_BITS = np.array([1,1, 0,1, 0,0, 0,0, 1,1, 1,0, 1,0, 0,1, 1,1, 0,1, 0,0], dtype=np.int8)
P_BITS = np.array([0,1, 1,1, 1,0, 1,0, 0,1, 0,0, 0,0, 1,1, 0,1, 1,1, 1,0], dtype=np.int8)

# DSB burst geometry
SYNC_Y_OFFSET_BITS = 214
BURST_TOTAL_BITS = 500
BLK1_OFFSET_BITS = 94
DMO_BLK2_OFFSET = 252

# DNB burst geometry (DM Normal Burst — TCH)
DNB_TRAIN_OFFSET = 230   # n_bits/p_bits zaczyna się na bit 230 w 470-bit DNB
DNB_BLK1_BEFORE = 216    # BLK1 = 216 bits przed training seq
DNB_BLK2_AFTER = 22      # BLK2 = za training seq (22 bits dalej)
DNB_BLK_LEN = 216

# Audio output: 60ms PCM frame @ 8 kHz int16
AUDIO_FRAME_SAMPLES = 480
AUDIO_FRAME_BYTES = AUDIO_FRAME_SAMPLES * 2

# Internal bit buffer — keep ~2 seconds of bits (72k bits)
BIT_BUFFER_MAX = 100_000

# cdecoder/sdecoder paths
CDECODER_PATH = "/opt/openwebrx-tetra/cdecoder"
SDECODER_PATH = "/opt/openwebrx-tetra/sdecoder"
PCM_PER_TCH = 480 * 2  # 1 TCH frame → 60 ms PCM (480 sampli, 960 bytes)


_LOG_PATH = "/tmp/tetra_dmo_log.json"
_log_fh = None

def emit(event_type, **kwargs):
    """JSON event line na stderr (zgodny format z tetra_decoder.py TMO)
    + duplikat do /tmp/tetra_dmo_log.json dla debug.

    KRYTYCZNE: musi zawierać 'protocol': 'TETRA' bo TetraMetaPanel.isSupported
    sprawdza data.protocol — bez tego events są drop'owane przez frontend."""
    obj = {"protocol": "TETRA", "type": event_type, "t": time.time(), **kwargs}
    line = json.dumps(obj) + "\n"
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass
    global _log_fh
    try:
        if _log_fh is None:
            _log_fh = open(_LOG_PATH, "a", buffering=1)
        _log_fh.write(line)
    except Exception:
        pass


def find_sync_positions(bits, max_errors=4, debounce=100):
    """Szukaj y_bits (DSB sync) w bit array."""
    if len(bits) < len(Y_BITS):
        return []
    win = np.lib.stride_tricks.sliding_window_view(bits, len(Y_BITS))
    neg = (1 - Y_BITS).astype(np.int8)
    d_pos = (win != Y_BITS).sum(axis=1)
    d_neg = (win != neg).sum(axis=1)
    d_min = np.minimum(d_pos, d_neg)
    pos = np.where(d_min <= max_errors)[0]
    keep, last = [], -1000
    for p in pos:
        if p - last >= debounce:
            keep.append(int(p))
            last = p
    return keep


def decode_dsb_burst(burst, summary):
    """Dekoduje SCH/S + SCH/H z 500-bit DSB burst. Zwraca PDU dict lub None."""
    blk1 = burst[BLK1_OFFSET_BITS:BLK1_OFFSET_BITS + BLK1_LEN_BITS]
    blk2 = burst[DMO_BLK2_OFFSET:DMO_BLK2_OFFSET + BLK2_LEN_BITS]
    t4 = tetra_descramble(blk1, SCRAMB_INIT_BSCH)
    t3 = block_deinterleave(BLK1_LEN_BITS, SB1_INTERLEAVE_A, t4)
    mother = rcpc_depunct_2_3(t3, BLK1_LEN_BITS)
    t2_s, _ = viterbi_cch_decode(mother[:SB1_TYPE2_BITS * 4], SB1_TYPE2_BITS)
    crc_s = crc16_itut_bits(t2_s[:SB1_TYPE1_BITS + 16])
    summary["sch_s_total"] += 1
    if crc_s != TETRA_CRC_OK:
        return None
    summary["sch_s_ok"] += 1
    t4h = tetra_descramble(blk2, SCRAMB_INIT_BSCH)
    t3h = block_deinterleave(BLK2_LEN_BITS, SB2_INTERLEAVE_A, t4h)
    mother_h = rcpc_depunct_2_3(t3h, BLK2_LEN_BITS)
    t2_h, _ = viterbi_cch_decode(mother_h[:SB2_TYPE2_BITS * 4], SB2_TYPE2_BITS)
    crc_h = crc16_itut_bits(t2_h[:SB2_TYPE1_BITS + 16])
    if crc_h == TETRA_CRC_OK:
        summary["sch_h_ok"] += 1
        rec = parse_sync_pdu(t2_s[:SB1_TYPE1_BITS], t2_h[:SB2_TYPE1_BITS])
    else:
        rec = parse_sync_pdu(t2_s[:SB1_TYPE1_BITS])
    rec["summary"] = format_sync_pdu(rec)
    return rec


def find_train_positions(bits, pattern, max_errors=1, debounce=200):
    """Szukaj n_bits/p_bits (DNB training seq) w bit array."""
    if len(bits) < len(pattern):
        return []
    win = np.lib.stride_tricks.sliding_window_view(bits, len(pattern))
    neg = (1 - pattern).astype(np.int8)
    d_pos = (win != pattern).sum(axis=1)
    d_neg = (win != neg).sum(axis=1)
    d_min = np.minimum(d_pos, d_neg)
    pos = np.where(d_min <= max_errors)[0]
    keep, last = [], -1000
    for p in pos:
        if p - last >= debounce:
            keep.append((int(p), int(d_pos[p] <= d_neg[p])))  # (pos, polarity)
            last = p
    return keep


def scramble_seq(init, length):
    """32-bit LFSR per ETSI tetra_scramb.c — taps 32-26-23-22-16-12-11-10-8-7-5-4-2-1."""
    out = np.empty(length, dtype=np.int8)
    s = int(init) & 0xFFFFFFFF
    taps = [32, 26, 23, 22, 16, 12, 11, 10, 8, 7, 5, 4, 2, 1]
    for i in range(length):
        b = 0
        for t in taps:
            b ^= (s >> (32 - t)) & 1
        s = ((s >> 1) | (b << 31)) & 0xFFFFFFFF
        out[i] = b
    return out


def dmo_scramb_init(mni, src):
    """ETSI EN 300 396-3 §8.2.4: init = ((src & 0xFFFFFF) | ((mni & 0x3F) << 24)) << 2 | 3."""
    mni6 = mni & 0x3F
    src24 = src & 0xFFFFFF
    init = src24 | (mni6 << 24)
    init = (init << 2) | 3
    return init & 0xFFFFFFFF


def pack_cdecoder_block(type4_432):
    """Pack 432 type4 bits do sq5bpf format 690×int16 z sync marks."""
    block = np.zeros(690, dtype=np.int16)
    sync_marks = [0x6b21, 0x6b22, 0x6b23, 0x6b24, 0x6b25, 0x6b26]
    for i, m in enumerate(sync_marks):
        block[115 * i] = m
    mapped = np.where(np.asarray(type4_432, dtype=np.int8) == 1, -127, 127).astype(np.int16)
    block[1:115]   = mapped[0:114]
    block[116:230] = mapped[114:228]
    block[231:345] = mapped[228:342]
    block[346:436] = mapped[342:432]
    return block


def emit_pdu(rec):
    """Emit sparsowany DMO PDU jako event 'dmo_burst' (forma zgodna z TMO meta events).
    Plus: jeśli PDU ma encrypted aspect (airint_encryption_state > 0), wyemituj
    dodatkowo 'encrypted_activity' (analogicznie do TMO _maybe_emit_encrypted)."""
    emit("dmo_burst",
         sync_type=rec.get("sync_pdu_type_name"),
         comm=rec.get("communication_type_name"),
         sys_code=rec.get("system_code"),
         tn=rec.get("slot_number"),
         fn=rec.get("frame_number"),
         enc=rec.get("airint_encryption_state"),
         msg_type=rec.get("message_type_name"),
         src=rec.get("src_address"),
         dst=rec.get("dest_address"),
         mcc=rec.get("mcc"),
         mnc=rec.get("mnc"),
         summary=rec.get("summary"))
    # Encrypted activity
    enc_state = rec.get("airint_encryption_state", 0)
    if enc_state and enc_state > 0:
        mcc = rec.get("mcc")
        mnc = rec.get("mnc")
        emit("encrypted_activity",
             action="pdu",
             la=(f"{mcc}-{mnc}" if mcc is not None else ""),
             ssi=rec.get("dest_address"),
             gssi="",
             tn=rec.get("slot_number"),
             enc_mode={1: "TEA", 2: "SCK", 3: "static"}.get(enc_state, f"enc={enc_state}"),
             source_event="dmac_sync",
             description=f"DMO DMAC-SYNC msg={rec.get('message_type_name','?')} src={rec.get('src_address')}")


# ---------- Threads ----------
class BurstProcessor:
    def __init__(self, audio_pipe):
        self.bit_buf = bytearray()
        self.summary = {"sch_s_total": 0, "sch_s_ok": 0, "sch_h_ok": 0, "n_pdus": 0,
                        "tch_sent": 0, "tch_skipped_no_ctx": 0}
        self.last_report = time.time()
        # Context dla DNB scrambling — derived z ostatniego DSB DMAC-SYNC
        self.cur_src = None
        self.cur_mni = None
        self.cur_scramb_init = None
        self.cur_scramb_seq = None  # cache scramble seq 432 bits
        # Audio pipe (do cdecoder stdin)
        self.audio_pipe = audio_pipe
        # Fallback scramble defaults (TETRA_DMO_DEFAULT_SRC + TETRA_DMO_DEFAULT_MNI env vars)
        # — gdy SCH/H zawsze fail i nie mamy dynamic ctx. Default: Tetrapack 2600824/901-9999.
        try:
            default_src = int(os.environ.get('TETRA_DMO_DEFAULT_SRC', '2600824'))
            default_mni = int(os.environ.get('TETRA_DMO_DEFAULT_MNI', str((901 << 14) | 9999)))
            self.fallback_scramb_seq = scramble_seq(dmo_scramb_init(default_mni, default_src), 432)
            self.fallback_src = default_src
            self.fallback_mni = default_mni
            emit("audio_fallback", src=default_src, mni=default_mni,
                 msg="TCH descramble fallback ready (used if no SCH/H ctx)")
        except Exception:
            self.fallback_scramb_seq = None

    def update_call_context(self, rec):
        """Z PDU DMAC-SYNC z msg_type wyciągnij src/mni dla scrambling DNB."""
        src = rec.get("src_address")
        mcc = rec.get("mcc")
        mnc = rec.get("mnc")
        if src is not None and mcc is not None and mnc is not None:
            mni = (mcc << 14) | mnc
            if src != self.cur_src or mni != self.cur_mni:
                self.cur_src = src
                self.cur_mni = mni
                self.cur_scramb_init = dmo_scramb_init(mni, src)
                self.cur_scramb_seq = scramble_seq(self.cur_scramb_init, 432)
                emit("dmo_call_ctx", src=src, mni=mni,
                     scramb_init=f"0x{self.cur_scramb_init:08x}",
                     msg="updated TCH descramble context")

    def feed(self, chunk):
        self.bit_buf.extend(chunk)
        if len(self.bit_buf) > BIT_BUFFER_MAX:
            keep = BIT_BUFFER_MAX // 2
            self.bit_buf = self.bit_buf[-keep:]

    def process(self):
        if len(self.bit_buf) < BURST_TOTAL_BITS + 200:
            return
        bits = np.frombuffer(self.bit_buf, dtype=np.uint8).astype(np.int8)

        # 1) DSB sync burst (signaling)
        dsb_positions = find_sync_positions(bits, max_errors=4)
        last_processed = 0
        for p in dsb_positions:
            burst_start = p - SYNC_Y_OFFSET_BITS
            burst_end = burst_start + BURST_TOTAL_BITS
            if burst_start < 0 or burst_end > len(bits):
                continue
            try:
                rec = decode_dsb_burst(bits[burst_start:burst_end], self.summary)
                if rec:
                    self.summary["n_pdus"] += 1
                    self.update_call_context(rec)
                    emit_pdu(rec)
                last_processed = max(last_processed, burst_end)
            except Exception as e:
                emit("error", source="decode_dsb", msg=str(e)[:200])

        # 2) DNB normal burst (TCH audio) — only if mamy call context
        if self.cur_scramb_seq is not None and self.audio_pipe is not None:
            dnb_hits = find_train_positions(bits, N_BITS, max_errors=1)
            dnb_hits += find_train_positions(bits, P_BITS, max_errors=1)
            dnb_hits.sort(key=lambda x: x[0])
            for pos, polarity in dnb_hits:
                b1s = pos - DNB_BLK1_BEFORE
                b2s = pos + DNB_BLK2_AFTER
                b2e = b2s + DNB_BLK_LEN
                if b1s < 0 or b2e > len(bits):
                    continue
                try:
                    t5 = np.concatenate([bits[b1s:b1s + DNB_BLK_LEN], bits[b2s:b2e]])
                    if polarity == 0:
                        t5 = (1 - t5).astype(np.int8)
                    t4 = (t5 ^ self.cur_scramb_seq).astype(np.int8)
                    block = pack_cdecoder_block(t4)
                    self.audio_pipe.write(block.tobytes())
                    self.audio_pipe.flush()
                    self.summary["tch_sent"] += 1
                    last_processed = max(last_processed, b2e)
                except (BrokenPipeError, OSError):
                    self.audio_pipe = None
                    emit("warning", source="tch", msg="audio pipe broken")
                    break
                except Exception as e:
                    emit("error", source="decode_dnb", msg=str(e)[:200])
        elif self.cur_scramb_seq is None:
            self.summary["tch_skipped_no_ctx"] += 1

        if last_processed > 600:
            self.bit_buf = self.bit_buf[last_processed - 600:]
        now = time.time()
        if now - self.last_report > 10.0:
            emit("dmo_stats", **self.summary)
            self.last_report = now


def demod_reader(proc, processor, stop_event):
    """Czytaj bity ze stdout subprocess tetra_demod.py, przekazuj do processora."""
    try:
        while not stop_event.is_set():
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            processor.feed(chunk)
    except Exception as e:
        emit("error", source="demod_reader", msg=str(e)[:200])


def burst_loop(processor, stop_event):
    """Co 200ms uruchamia burst processor."""
    while not stop_event.is_set():
        try:
            processor.process()
        except Exception as e:
            emit("error", source="burst_loop", msg=str(e)[:200])
        time.sleep(0.2)


def audio_loop(sdec_stdout, stop_event):
    """Pump PCM z sdecoder stdout do naszego stdout, z fallback na silence
    gdy brak danych (audio_clock pattern jak w tetra_decoder.py TMO).

    sdec_stdout: file obj sdecoder.stdout (blocking read). Może być None
    jeśli codec pipeline nie wystartowało — wtedy puste = silence.
    """
    silence = b"\x00" * AUDIO_FRAME_BYTES
    next_t = time.time()
    # Virtual playout clock — track gdzie jesteśmy w real time
    while not stop_event.is_set():
        # Czytaj jeden frame z sdecoder (60ms = 960 bytes)
        chunk = b""
        if sdec_stdout is not None:
            try:
                # non-blocking-ish read — sdecoder produkuje 60ms PCM frames
                # gdy mamy DNB sent. Inaczej blokuje.
                # Use select-like behaviour: jeśli brak danych w ~50ms, silence.
                import select
                ready, _, _ = select.select([sdec_stdout], [], [], 0.05)
                if ready:
                    chunk = sdec_stdout.read(AUDIO_FRAME_BYTES)
            except (OSError, ValueError):
                pass
        if not chunk or len(chunk) < AUDIO_FRAME_BYTES:
            chunk = silence
        try:
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            stop_event.set()
            break
        next_t += AUDIO_FRAME_SAMPLES / 8000.0
        sleep = next_t - time.time()
        if sleep > 0:
            time.sleep(sleep)
        else:
            next_t = time.time()


def iq_pump(demod_proc, stop_event):
    """Czyta IQ z naszego stdin i piszę do demod subprocess stdin."""
    bytes_total = 0
    last_report = time.time()
    try:
        while not stop_event.is_set():
            data = sys.stdin.buffer.read(8192)
            if not data:
                emit("warning", source="iq_pump", msg="stdin EOF")
                break
            bytes_total += len(data)
            try:
                demod_proc.stdin.write(data)
                demod_proc.stdin.flush()
            except (BrokenPipeError, OSError):
                emit("warning", source="iq_pump", msg="demod stdin broken")
                break
            now = time.time()
            if now - last_report > 10.0:
                # 8 bytes per complex64 sample, 36k SPS → 288k bytes/s
                rate = bytes_total / (now - last_report) / 1024
                emit("iq_rate", kb_per_s=round(rate, 1), total_mb=round(bytes_total/1e6, 2))
                bytes_total = 0
                last_report = now
    except Exception as e:
        emit("error", source="iq_pump", msg=str(e)[:200])
    finally:
        stop_event.set()


def main():
    emit("startup", version="dmo_decoder/v3",
         offset_hz=float(os.environ.get('TETRA_OFFSET_HZ', 0)))

    # Uruchom subprocess tetra_demod.py (IQ → bits)
    demod_path = os.path.join(SCRIPT_DIR, 'tetra_demod.py')
    if not os.path.isfile(demod_path):
        emit("fatal", msg=f"tetra_demod.py nie znaleziony w {SCRIPT_DIR}")
        return 1
    demod_proc = subprocess.Popen(
        ["python3", "-u", demod_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0
    )

    # Uruchom cdecoder|sdecoder pipeline dla TCH audio (jeśli binarki dostępne)
    cdec_proc = sdec_proc = None
    audio_pipe = None
    sdec_stdout = None
    if os.path.isfile(CDECODER_PATH) and os.path.isfile(SDECODER_PATH):
        try:
            pipe_r, pipe_w = os.pipe()
            cdec_proc = subprocess.Popen([CDECODER_PATH, '/dev/stdin', '/dev/stdout'],
                                          stdin=subprocess.PIPE, stdout=pipe_w,
                                          stderr=subprocess.DEVNULL)
            os.close(pipe_w)
            sdec_proc = subprocess.Popen([SDECODER_PATH, '/dev/stdin', '/dev/stdout'],
                                          stdin=pipe_r, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL)
            os.close(pipe_r)
            audio_pipe = cdec_proc.stdin
            sdec_stdout = sdec_proc.stdout
            emit("audio_pipeline", msg="cdecoder|sdecoder started")
        except Exception as e:
            emit("warning", source="audio_init", msg=f"failed to start codec: {e}")
    else:
        emit("warning", source="audio_init",
             msg=f"codec missing ({CDECODER_PATH}, {SDECODER_PATH}) — audio=silence")

    processor = BurstProcessor(audio_pipe=audio_pipe)
    stop = threading.Event()
    threads = [
        threading.Thread(target=iq_pump, args=(demod_proc, stop), daemon=True),
        threading.Thread(target=demod_reader, args=(demod_proc, processor, stop), daemon=True),
        threading.Thread(target=burst_loop, args=(processor, stop), daemon=True),
        threading.Thread(target=audio_loop, args=(sdec_stdout, stop), daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        stop.wait()
    except KeyboardInterrupt:
        stop.set()

    for proc in (demod_proc, cdec_proc, sdec_proc):
        if proc is None:
            continue
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass

    emit("shutdown", **processor.summary)
    return 0


if __name__ == '__main__':
    sys.exit(main())
