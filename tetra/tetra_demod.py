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
import numpy as np
import signal
import sys
import time


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

        # Connections
        self.connect(self.source, self.agc, self.fll, self.clock_sync,
                     self.equalizer, self.diff_phasor, self.decoder,
                     self.mapper, self.unpack, self.stdout_sink)

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
