#!/usr/bin/env python3
"""Debug kroku 4b: analiza co naprawdę jest w bit streamie.

1. Detect DSB sync positions (y_bits, CRC OK na BLK1=SCH/S → real DSB)
2. Detect DNB positions (n_bits, p_bits)
3. Timeline: czy DNB grupują się po DSB DM-OCCUPIED?
4. Histogram gapów (real TCH = regularne 56.67ms slot)
5. Test różnych max_errors dla n_bits — ile zostaje?

Run: python3 dmo_4b_debug.py test_data/dmo_433400_36k.iq
"""
import sys, math, time, cmath
import numpy as np

sys.path.insert(0, '.')
from dmo_l1_chain import (tetra_descramble, block_deinterleave, rcpc_depunct_2_3,
                          viterbi_cch_decode, crc16_itut_bits,
                          BLK1_LEN_BITS, SB1_TYPE1_BITS, SB1_TYPE2_BITS,
                          SB1_INTERLEAVE_A, SB2_TYPE1_BITS, SB2_TYPE2_BITS,
                          SB2_INTERLEAVE_A, BLK2_LEN_BITS, TETRA_CRC_OK,
                          SCRAMB_INIT_BSCH)
from dmo_pdu_parser import parse_sync_pdu

N_BITS = np.array([1,1, 0,1, 0,0, 0,0, 1,1, 1,0, 1,0, 0,1, 1,1, 0,1, 0,0], dtype=np.int8)
P_BITS = np.array([0,1, 1,1, 1,0, 1,0, 0,1, 0,0, 0,0, 1,1, 0,1, 1,1, 1,0], dtype=np.int8)
Y_BITS = np.array([1,1, 0,0, 0,0, 0,1, 1,0, 0,1, 1,1, 0,0, 1,1, 1,0, 1,0,
                   0,1, 1,1, 0,0, 0,0, 0,1, 1,0, 0,1, 1,1], dtype=np.int8)

SAMPLE_RATE_BITS = 36000  # 2 bit/sym × 18k sym/s = 36k bit/s; nasze IQ to 36k complex samples/s → 36k bits/s


def demodulate(iq_path, offset_hz, seconds=70):
    from gnuradio import gr, blocks, analog, digital
    from gnuradio.filter import firdes
    class Tb(gr.top_block):
        def __init__(self):
            gr.top_block.__init__(self)
            sps = 2; nfilts = 32
            constel = digital.constellation_dqpsk().base()
            constel.gen_soft_dec_lut(8)
            algo = digital.adaptive_algorithm_cma(constel, 10e-3, 1).base()
            rrc = firdes.root_raised_cosine(nfilts, nfilts, 1.0/sps, 0.35, 11*sps*nfilts)
            self.src = blocks.file_source(gr.sizeof_gr_complex, iq_path, False)
            self.throt = blocks.throttle(gr.sizeof_gr_complex, 36000)
            self.rot = blocks.rotator_cc(-2*math.pi*offset_hz/36000.0) if offset_hz else None
            self.agc = analog.feedforward_agc_cc(8, 1)
            self.fll = digital.fll_band_edge_cc(sps, 0.35, 45, cmath.pi/100.0)
            self.cs = digital.pfb_clock_sync_ccf(sps, 2*cmath.pi/100.0, rrc, nfilts, nfilts//2, 1.5, sps)
            self.eq = digital.linear_equalizer(15, sps, algo, True, [], 'corr_est')
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
    tb = Tb(); tb.start(); time.sleep(seconds); tb.stop(); tb.wait()
    return np.array(tb.snk.data(), dtype=np.int8)


def find_pattern(bits, pattern, max_errors, debounce=50):
    win = np.lib.stride_tricks.sliding_window_view(bits, len(pattern))
    neg = (1 - pattern).astype(np.int8)
    d_pos = (win != pattern).sum(axis=1)
    d_neg = (win != neg).sum(axis=1)
    d_min = np.minimum(d_pos, d_neg)
    pos = np.where(d_min <= max_errors)[0]
    keep = []; last = -1000
    for p in pos:
        if p - last >= debounce:
            keep.append(int(p)); last = p
    return np.array(keep, dtype=np.int64)


def decode_sb1(t5):
    """Try DSB BLK1 (SCH/S). Returns (crc_ok, parsed_record or None)."""
    t4 = tetra_descramble(t5, SCRAMB_INIT_BSCH)
    t3 = block_deinterleave(BLK1_LEN_BITS, SB1_INTERLEAVE_A, t4)
    mother = rcpc_depunct_2_3(t3, BLK1_LEN_BITS)
    t2, cost = viterbi_cch_decode(mother[:SB1_TYPE2_BITS * 4], SB1_TYPE2_BITS)
    crc = crc16_itut_bits(t2[:SB1_TYPE1_BITS + 16])
    if crc == TETRA_CRC_OK:
        rec = parse_sync_pdu(t2[:SB1_TYPE1_BITS])
        return True, rec
    return False, None


def main():
    iq_path = sys.argv[1]
    bits = demodulate(iq_path, 1000)
    print(f"demod: {len(bits)} bits ({len(bits)/SAMPLE_RATE_BITS:.1f}s)")

    # 1. DSB hits + which are real (CRC OK)
    SYNC_Y_OFFSET = (6+1+40+60)*2  # 214 z dmo_burst_extract
    BLK1_OFF = 94; BLK1_LEN = 120
    BURST_TOTAL = 500
    DMO_BLK2_OFF = 252
    y_pos = find_pattern(bits, Y_BITS, max_errors=4, debounce=100)
    real_dsb = []
    occupied_dsb = []  # DSB z DM-OCCUPIED/DM-RESERVED
    for s in y_pos:
        start = int(s) - SYNC_Y_OFFSET
        end = start + BURST_TOTAL
        if start < 0 or end > len(bits): continue
        burst = bits[start:end]
        t5 = burst[BLK1_OFF:BLK1_OFF + BLK1_LEN]
        ok, rec = decode_sb1(t5)
        if ok:
            real_dsb.append(int(s))
            if rec.get('message_type') in (13, 14, 9, 8) or 'message_type' in rec:
                occupied_dsb.append((int(s), rec.get('message_type'), rec.get('frame_number')))
    print(f"\nDSB y_pos: {len(y_pos)}  z CRC OK: {len(real_dsb)}")
    print(f"DSB OK msg_types: {[(p/SAMPLE_RATE_BITS, m, fn) for p,m,fn in occupied_dsb[:8]]}")

    # 2. DNB hits przy różnych max_errors
    print("\nDNB n_bits przy różnych progach hamming dist:")
    for me in [0, 1, 2, 3]:
        n = find_pattern(bits, N_BITS, max_errors=me, debounce=50)
        # ile z nich jest w czasach gdy DSB ostatnie pokazało DM-OCCUPIED?
        n_near_occupied = 0
        for p in n:
            for op, _, _ in occupied_dsb:
                # gdy w obrębie 1 sek po DM-OCCUPIED
                if 0 < (p - op) < SAMPLE_RATE_BITS:  # 1 sek
                    n_near_occupied += 1
                    break
        print(f"  max_err={me}: {len(n)} hits, z czego {n_near_occupied} w 1s od DM-OCCUPIED")

    # 3. Histogram gapów dla n_bits @ max_err=1
    print("\nGap histogram dla n_bits (max_err=1):")
    n1 = find_pattern(bits, N_BITS, max_errors=1, debounce=50)
    if len(n1) > 1:
        gaps_ms = np.diff(n1) / 36.0
        # buckets: 25-65 (1 slot), 50-65 (~slot 56.67), 110-120 (~2 slot), 220-240 (~4 slot=frame)
        for lo, hi, label in [(50,65,"1 slot=56.67ms"), (110,120,"2 slot=113ms"),
                              (165,180,"3 slot=170ms"), (220,240,"frame=227ms"),
                              (440,470,"2 frame=453ms"), (1000,1100,"multifr=1.02s")]:
            n = ((gaps_ms >= lo) & (gaps_ms <= hi)).sum()
            print(f"  {label:25s}: {n} gapów")
        print(f"  total gaps: {len(gaps_ms)},  min={gaps_ms.min():.1f} med={np.median(gaps_ms):.1f} max={gaps_ms.max():.1f}")

    # 4. Random baseline: szukamy random pattern w bit stream
    print("\nBaseline (random pattern 22-bit przy max_err=2):")
    rng = np.random.default_rng(42)
    for trial in range(3):
        random_pat = rng.integers(0, 2, 22).astype(np.int8)
        rp = find_pattern(bits, random_pat, max_errors=2, debounce=50)
        print(f"  trial {trial}: {len(rp)} hits (random pattern)")


if __name__ == '__main__':
    main()
