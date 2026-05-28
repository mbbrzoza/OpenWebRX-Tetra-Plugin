#!/usr/bin/env python3
"""Demodulator GNU Radio działający na natywnym IQ @288kS/s (bez decimacji scipy).

Hipoteza: scipy.signal.resample_poly z domyślnym filtrem oddziaływał na phase
characteristics sygnału, dlatego pipeline na zdecymowanym 36k IQ nie syncował.
Tutaj GR robi pełen path z polyphase clock_sync który sam decymuje.

288 kS/s / 16 = 18 kbaud (TETRA symbol rate). Czyli sps=16.

Run: python3 dmo_demod_native.py <iq_288k_file> [offset_hz=0]
"""
import sys, math, time, cmath
import numpy as np

Y_BITS = np.array([1,1, 0,0, 0,0, 0,1, 1,0, 0,1, 1,1, 0,0, 1,1, 1,0, 1,0,
                   0,1, 1,1, 0,0, 0,0, 0,1, 1,0, 0,1, 1,1], dtype=np.int8)
N_BITS = np.array([1,1, 0,1, 0,0, 0,0, 1,1, 1,0, 1,0, 0,1, 1,1, 0,1, 0,0], dtype=np.int8)
P_BITS = np.array([0,1, 1,1, 1,0, 1,0, 0,1, 0,0, 0,0, 1,1, 0,1, 1,1, 1,0], dtype=np.int8)


def find_pattern(bits, pat, max_err=2, debounce=50):
    if len(bits) < len(pat): return 0
    win = np.lib.stride_tricks.sliding_window_view(bits, len(pat))
    neg = (1 - pat).astype(np.int8)
    d_min = np.minimum((win != pat).sum(axis=1), (win != neg).sum(axis=1))
    pos = np.where(d_min <= max_err)[0]
    keep = 0; last = -1000
    for p in pos:
        if p - last >= debounce:
            keep += 1; last = p
    return keep


def demod_native(iq_path, sample_rate, offset_hz, seconds):
    """Pipeline GR z polyphase clock sync który sam decymuje na 1 sps z natywnego SR."""
    from gnuradio import gr, blocks, analog, digital
    from gnuradio.filter import firdes

    class Tb(gr.top_block):
        def __init__(self):
            gr.top_block.__init__(self)
            # symbol rate = 18000, sps = sample_rate/18000
            sps = sample_rate // 18000  # 288/18 = 16
            nfilts = 32
            constel = digital.constellation_dqpsk().base()
            constel.gen_soft_dec_lut(8)
            algo = digital.adaptive_algorithm_cma(constel, 10e-3, 1).base()
            # RRC filter w nfilts banks
            rrc = firdes.root_raised_cosine(nfilts, nfilts, 1.0/sps, 0.35, 11*sps*nfilts)
            self.src = blocks.file_source(gr.sizeof_gr_complex, iq_path, False)
            self.throt = blocks.throttle(gr.sizeof_gr_complex, sample_rate)
            self.rot = blocks.rotator_cc(-2*math.pi*offset_hz/sample_rate) if offset_hz else None
            self.agc = analog.feedforward_agc_cc(64, 1)
            # FLL band-edge
            self.fll = digital.fll_band_edge_cc(sps, 0.35, 11*sps, cmath.pi/1000.0)
            # Polyphase clock sync — sam decymuje do 1 sps (de facto, output = 1 symbol per output sample × sps)
            # Tu ustawiamy output sps = 2 (czyli wstawia 2 sps żebyśmy mieli też diff_phasor)
            self.cs = digital.pfb_clock_sync_ccf(sps, 2*cmath.pi/100.0, rrc, nfilts, nfilts//2, 1.5, 2)
            self.eq = digital.linear_equalizer(15, 2, algo, True, [], 'corr_est')
            self.dp = digital.diff_phasor_cc()
            self.dec = digital.constellation_decoder_cb(constel)
            self.mp = digital.map_bb(constel.pre_diff_code())
            self.un = blocks.unpack_k_bits_bb(constel.bits_per_symbol())
            self.snk = blocks.vector_sink_b()
            for i in range(1, 4):
                self.connect((self.fll, i), blocks.null_sink(gr.sizeof_float))
            chain = [self.src, self.throt]
            if self.rot: chain.append(self.rot)
            chain += [self.agc, self.fll, self.cs, self.eq, self.dp, self.dec, self.mp, self.un, self.snk]
            self.connect(*chain)

    tb = Tb()
    tb.start()
    time.sleep(seconds + 3)
    tb.stop()
    tb.wait()
    return np.array(tb.snk.data(), dtype=np.int8)


def main():
    iq_path = sys.argv[1]
    offset = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    sample_rate = int(sys.argv[3]) if len(sys.argv) > 3 else 288000
    seconds = int(sys.argv[4]) if len(sys.argv) > 4 else 30
    print(f"demod {iq_path} @{sample_rate} offset={offset} for {seconds}s\n")
    bits = demod_native(iq_path, sample_rate, offset, seconds)
    print(f"output: {len(bits)} bits")
    y = find_pattern(bits, Y_BITS, max_err=4)
    n = find_pattern(bits, N_BITS, max_err=1)
    p = find_pattern(bits, P_BITS, max_err=1)
    n2 = find_pattern(bits, N_BITS, max_err=2)
    p2 = find_pattern(bits, P_BITS, max_err=2)
    print(f"y_bits (DSB, err<=4):       {y}")
    print(f"n_bits DNB-1 (err<=1/<=2):  {n} / {n2}")
    print(f"p_bits DNB-2 (err<=1/<=2):  {p} / {p2}")


if __name__ == '__main__':
    main()
