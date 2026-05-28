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

# DSB burst geometry
SYNC_Y_OFFSET_BITS = 214
BURST_TOTAL_BITS = 500
BLK1_OFFSET_BITS = 94
DMO_BLK2_OFFSET = 252

# Audio output: silence MVP
AUDIO_FRAME_SAMPLES = 480  # 60ms @ 8kHz
AUDIO_FRAME_BYTES = AUDIO_FRAME_SAMPLES * 2

# Internal bit buffer — keep ~2 seconds of bits (72k bits)
BIT_BUFFER_MAX = 100_000


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


def emit_pdu(rec):
    """Emit sparsowany DMO PDU jako event 'dmo_burst' (forma zgodna z TMO meta events)."""
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


# ---------- Threads ----------
class BurstProcessor:
    def __init__(self):
        self.bit_buf = bytearray()
        self.summary = {"sch_s_total": 0, "sch_s_ok": 0, "sch_h_ok": 0, "n_pdus": 0}
        self.last_report = time.time()

    def feed(self, chunk):
        self.bit_buf.extend(chunk)
        # Limituj rozmiar bufora
        if len(self.bit_buf) > BIT_BUFFER_MAX:
            # Zachowaj ostatnie 50% (overlap dla burstów na granicy)
            keep = BIT_BUFFER_MAX // 2
            self.bit_buf = self.bit_buf[-keep:]

    def process(self):
        if len(self.bit_buf) < BURST_TOTAL_BITS + 200:
            return
        bits = np.frombuffer(self.bit_buf, dtype=np.uint8).astype(np.int8)
        positions = find_sync_positions(bits, max_errors=4)
        # Przetwórz znalezione bursty
        last_processed = 0
        for p in positions:
            burst_start = p - SYNC_Y_OFFSET_BITS
            burst_end = burst_start + BURST_TOTAL_BITS
            if burst_start < 0 or burst_end > len(bits):
                continue
            try:
                rec = decode_dsb_burst(bits[burst_start:burst_end], self.summary)
                if rec:
                    self.summary["n_pdus"] += 1
                    emit_pdu(rec)
                last_processed = burst_end
            except Exception as e:
                emit("error", source="decode", msg=str(e)[:200])
        # Trim — zostaw ostatnie 600 bitów (overlap)
        if last_processed > 600:
            self.bit_buf = self.bit_buf[last_processed - 600:]
        # Periodic stats
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


def audio_loop(stop_event):
    """Wypluwa silence PCM @ 8 kHz int16. TCH live integration TODO."""
    silence = b"\x00" * AUDIO_FRAME_BYTES
    next_t = time.time()
    while not stop_event.is_set():
        try:
            sys.stdout.buffer.write(silence)
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
    emit("startup", version="dmo_decoder/v2",
         offset_hz=float(os.environ.get('TETRA_OFFSET_HZ', 0)))

    # Uruchom subprocess tetra_demod.py (IQ → bits)
    demod_path = os.path.join(SCRIPT_DIR, 'tetra_demod.py')
    if not os.path.isfile(demod_path):
        emit("fatal", msg=f"tetra_demod.py nie znaleziony w {SCRIPT_DIR}")
        return 1
    demod_proc = subprocess.Popen(
        ["python3", "-u", demod_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,  # zignoruj AFC events z tetra_demod
        bufsize=0
    )

    processor = BurstProcessor()
    stop = threading.Event()
    threads = [
        threading.Thread(target=iq_pump, args=(demod_proc, stop), daemon=True),
        threading.Thread(target=demod_reader, args=(demod_proc, processor, stop), daemon=True),
        threading.Thread(target=burst_loop, args=(processor, stop), daemon=True),
        threading.Thread(target=audio_loop, args=(stop,), daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        stop.wait()
    except KeyboardInterrupt:
        stop.set()

    demod_proc.terminate()
    try:
        demod_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        demod_proc.kill()

    emit("shutdown", **processor.summary)
    return 0


if __name__ == '__main__':
    sys.exit(main())
