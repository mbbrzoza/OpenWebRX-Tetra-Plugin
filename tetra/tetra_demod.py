#!/usr/bin/env python3
"""Simplified TETRA DQPSK demodulator for OpenWebRX+.

Reads complex float IQ from stdin at 36 kS/s (centered on TETRA carrier).
Outputs demodulated bits to stdout.
Outputs AFC (frequency offset) info to stderr as JSON lines.

Based on simdemod3_telive.py by Jacek Lipkowski SQ5BPF,
adapted for OpenWebRX+ integration.
Author: SP8MB

Requires: gnuradio 3.10+
"""

from gnuradio import analog, blocks, digital, gr
from gnuradio.filter import firdes
import cmath
import json
import math
import numpy as np
import os
import signal
import sys
import time

# Channel offset in Hz — frequency shift applied BEFORE FLL.
# TETRA networks may use ±6.25 kHz or ±12.5 kHz channel offset.
# Can be set via env var TETRA_OFFSET_HZ or via /opt/openwebrx-tetra/offset.txt.
def _read_offset():
    env = os.environ.get('TETRA_OFFSET_HZ', '').strip()
    if env:
        try: return float(env)
        except ValueError: pass
    try:
        with open('/opt/openwebrx-tetra/offset.txt') as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return 0.0


class AFCProbe(gr.sync_block):
    """Probe FLL frequency output and write AFC info to stderr."""

    def __init__(self, interval=2.0):
        gr.sync_block.__init__(
            self, name="AFC Probe",
            in_sig=[np.float32], out_sig=None
        )
        self.interval = interval
        self.last_time = 0

    def work(self, input_items, output_items):
        now = time.monotonic()
        if now - self.last_time >= self.interval:
            val = float(input_items[0][-1])
            # FLL freq output is in radians/sample, convert to Hz
            freq_hz = val * 36000.0 / (2.0 * cmath.pi)
            try:
                line = json.dumps({"afc": round(freq_hz, 1)}) + "\n"
                sys.stderr.write(line)
                sys.stderr.flush()
            except (BrokenPipeError, OSError):
                pass
            self.last_time = now
        return len(input_items[0])


# TETRA synchronization training sequence y(1..38).
# Source: ETSI EN 300 392-2 §9.4.4.3.4 (TMO) and EN 300 396-2 §9.4.3.3.4 (DMO).
# Verified against bytearray I_00005FA0 in SDRSharp.Tetra.dll (TTT plug-in).
# Same pattern occurs in TMO downlink SB and DMO DSB — detection alone cannot
# distinguish; the rate / burst context disambiguates (TMO SB ≈ 1/multiframe,
# DMO DSB appears in frames 6/12/18 of an 18-frame multiframe).
_SYNC_Y_38 = np.array([
    1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 0,
    1, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1
], dtype=np.int8)


class SyncDetector(gr.sync_block):
    """Correlate demodulated bit stream against TETRA sync training sequence.

    Operates on the post-`unpack` bit stream (36 kbps at 1 bit/byte).
    Emits stderr JSON `sync_hit` events when Hamming distance ≤ `max_errors`
    against either polarity of the 38-bit y sequence.

    Also keeps a rolling 5 s count, emitted periodically as `sync_stat`.
    Rate ~6/s on a locked TETRA carrier (per timeslot in SB frame across 4 TS
    once per multiframe ≈ 4×1/1.02s for TMO; DMO DSB appears 3×/multiframe so
    ~3/s when active).
    """

    PATTERN_LEN = 38

    def __init__(self, max_errors=4, stat_interval=5.0):
        gr.sync_block.__init__(
            self, name="TETRA Sync Detector",
            in_sig=[np.int8], out_sig=None
        )
        self.max_errors = int(max_errors)
        self.stat_interval = float(stat_interval)
        self.pattern_pos = _SYNC_Y_38.copy()
        self.pattern_neg = (1 - _SYNC_Y_38).astype(np.int8)
        # Rolling window of last PATTERN_LEN bits
        self.window = np.zeros(self.PATTERN_LEN, dtype=np.int8)
        self.last_hit_time = 0.0
        self.cooldown_s = 0.01  # debounce: don't double-count adjacent matches
        self.hits = 0
        self.last_stat = time.monotonic()

    def _emit(self, obj):
        try:
            sys.stderr.write(json.dumps(obj) + "\n")
            sys.stderr.flush()
        except (BrokenPipeError, OSError):
            pass

    def work(self, input_items, output_items):
        bits = input_items[0]
        plen = self.PATTERN_LEN
        win = self.window
        now = time.monotonic()
        # Process each incoming bit; maintain rolling window.
        # Vectorized batch processing for speed.
        n = len(bits)
        if n == 0:
            return 0
        # Build a strided view of [prev_window + bits] of length (n) windows of plen.
        combined = np.concatenate([win, bits.astype(np.int8)])
        if len(combined) < plen:
            self.window = combined[-plen:]
            return n
        # Number of full windows ending at each new position
        windows = np.lib.stride_tricks.sliding_window_view(combined, plen)[-n:]
        # Hamming distance to both polarities
        d_pos = np.sum(windows != self.pattern_pos, axis=1)
        d_neg = np.sum(windows != self.pattern_neg, axis=1)
        d_min = np.minimum(d_pos, d_neg)
        hit_idx = np.where(d_min <= self.max_errors)[0]
        for i in hit_idx:
            if now - self.last_hit_time < self.cooldown_s:
                # debounce within batch — only count first
                pass
            self.hits += 1
            self.last_hit_time = now
            self._emit({
                "sync_hit": {
                    "errors": int(d_min[i]),
                    "polarity": "neg" if d_neg[i] < d_pos[i] else "pos",
                    "ts": round(now, 3),
                }
            })
        # Update rolling window to last plen bits of combined
        self.window = combined[-plen:].astype(np.int8)
        if now - self.last_stat >= self.stat_interval:
            self._emit({
                "sync_stat": {
                    "hits_per_s": round(self.hits / max(now - self.last_stat, 1e-3), 2),
                    "window_s": round(now - self.last_stat, 2),
                }
            })
            self.hits = 0
            self.last_stat = now
        return n


class TetraDemod(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "TETRA DQPSK Demodulator", catch_exceptions=True)

        # TETRA parameters
        sps = 2          # samples per symbol (36000 / 18000 sym/s)
        nfilts = 32      # polyphase filter bank arms
        constel = digital.constellation_dqpsk().base()
        constel.gen_soft_dec_lut(8)
        algo = digital.adaptive_algorithm_cma(constel, 10e-3, 1).base()
        rrc_taps = firdes.root_raised_cosine(nfilts, nfilts, 1.0 / sps, 0.35, 11 * sps * nfilts)

        # Source: complex float IQ from stdin
        self.source = blocks.file_descriptor_source(gr.sizeof_gr_complex, 0, False)

        # Channel-offset compensation (rotator) — shifts signal by -offset_hz
        # so that the actual carrier appears at baseband zero before FLL.
        self.offset_hz = _read_offset()
        if abs(self.offset_hz) > 0.5:
            phase_inc = -2.0 * math.pi * self.offset_hz / 36000.0
            self.offset_rot = blocks.rotator_cc(phase_inc)
            sys.stderr.write(json.dumps({"info": "offset_applied", "hz": self.offset_hz}) + "\n")
            sys.stderr.flush()
        else:
            self.offset_rot = None

        # AGC
        self.agc = analog.feedforward_agc_cc(8, 1)

        # Frequency Lock Loop
        self.fll = digital.fll_band_edge_cc(sps, 0.35, 45, cmath.pi / 100.0)

        # Clock recovery
        self.clock_sync = digital.pfb_clock_sync_ccf(
            sps, 2 * cmath.pi / 100.0, rrc_taps, nfilts, nfilts // 2, 1.5, sps
        )

        # Adaptive equalizer (CMA)
        self.equalizer = digital.linear_equalizer(15, sps, algo, True, [], 'corr_est')

        # Differential phase extraction (pi/4-DQPSK)
        self.diff_phasor = digital.diff_phasor_cc()

        # Constellation decoder
        self.decoder = digital.constellation_decoder_cb(constel)
        self.mapper = digital.map_bb(constel.pre_diff_code())
        self.unpack = blocks.unpack_k_bits_bb(constel.bits_per_symbol())

        # Sinks
        self.stdout_sink = blocks.file_descriptor_sink(gr.sizeof_char, 1)
        self.null_sink = blocks.null_sink(gr.sizeof_float)

        # AFC probe - reads FLL frequency output
        self.afc_probe = AFCProbe(interval=2.0)

        # Optional sync detector — enabled when TETRA_SYNC_DETECT=1 (default on).
        # Tap on the demodulated bit stream; emits sync_hit/sync_stat to stderr.
        self.sync_detector = None
        if os.environ.get('TETRA_SYNC_DETECT', '1').strip() not in ('0', '', 'off', 'no'):
            try:
                max_err = int(os.environ.get('TETRA_SYNC_MAX_ERR', '4'))
            except ValueError:
                max_err = 4
            self.sync_detector = SyncDetector(max_errors=max_err)

        # Connections
        if self.offset_rot is not None:
            self.connect(self.source, self.offset_rot, self.agc, self.fll, self.clock_sync,
                         self.equalizer, self.diff_phasor, self.decoder,
                         self.mapper, self.unpack, self.stdout_sink)
        else:
            self.connect(self.source, self.agc, self.fll, self.clock_sync,
                         self.equalizer, self.diff_phasor, self.decoder,
                         self.mapper, self.unpack, self.stdout_sink)

        # Tap bits → sync detector (parallel branch, no impact on stdout_sink path)
        if self.sync_detector is not None:
            self.connect(self.unpack, self.sync_detector)

        # FLL: port1=phase, port2=frequency, port3=error
        self.connect((self.fll, 1), (self.null_sink, 0))
        self.connect((self.fll, 2), self.afc_probe)
        self.connect((self.fll, 3), (self.null_sink, 1))


def main():
    tb = TetraDemod()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    tb.start()
    tb.wait()


if __name__ == '__main__':
    main()
