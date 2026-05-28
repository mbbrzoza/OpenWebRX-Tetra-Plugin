#!/usr/bin/env python3
"""Extract TETRA bursts from IQ file by sync-anchored alignment.

For every sync_y match in the demodulated bit stream, extracts the 510-bit
burst around it (offsets per ETSI EN 300 392-2 §9.4.4 / EN 300 396-2 §9.4.3.2).
Saves all bursts to a single file (one row per burst).

Usage: python3 dmo_burst_extract.py <iq_file> <out_bursts_file> [offset_hz]
"""
import sys, time, math, cmath
import numpy as np
from gnuradio import gr, blocks, analog, digital
from gnuradio.filter import firdes

# y(1..38) — TETRA sync training sequence (same for TMO SB and DMO DSB).
# Source: ETSI EN 300 392-2 §9.4.4.3.4 / EN 300 396-2 §9.4.3.3.4.
SYNC_Y = np.array([
    1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 0,
    1, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1
], dtype=np.int8)

# Burst layout in BITS (anchored to start of sync_y).
# Geometry from osmo-tetra/src/phy/tetra_burst.c SB_*_OFFSET constants:
#   (6 tail + 1 phase + 40 fcorr + 60 blk1 + 19 sync_y + 15 bbk + 108 blk2) * 2 bits
SYNC_Y_OFFSET_BITS    = (6 + 1 + 40 + 60) * 2   # = 214
SYNC_Y_LEN_BITS       = 19 * 2                   # = 38
BURST_TOTAL_SYMS      = 6 + 1 + 40 + 60 + 19 + 15 + 108 + 1  # ≈255
BURST_TOTAL_BITS      = BURST_TOTAL_SYMS * 2     # 510
BLK1_OFFSET_BITS      = (6 + 1 + 40) * 2         # 94
BLK1_LEN_BITS         = 60 * 2                   # 120
BBK_OFFSET_BITS       = (6 + 1 + 40 + 60 + 19) * 2   # 252
BBK_LEN_BITS          = 15 * 2                   # 30
BLK2_OFFSET_BITS      = (6 + 1 + 40 + 60 + 19 + 15) * 2  # 282
BLK2_LEN_BITS         = 108 * 2                  # 216
FCORR_OFFSET_BITS     = (6 + 1) * 2              # 14
FCORR_LEN_BITS        = 40 * 2                   # 80


def demodulate(iq_path, offset_hz, max_seconds=70):
    """Run IQ through GNURadio DQPSK chain (throttled), return bit array."""
    class Tb(gr.top_block):
        def __init__(self):
            gr.top_block.__init__(self)
            sps = 2
            nfilts = 32
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
            self.null1 = blocks.null_sink(gr.sizeof_float)
            self.null2 = blocks.null_sink(gr.sizeof_float)
            self.null3 = blocks.null_sink(gr.sizeof_float)
            chain = [self.src, self.throt]
            if self.rot: chain.append(self.rot)
            chain += [self.agc, self.fll, self.cs, self.eq, self.dp, self.dec, self.mp, self.un, self.snk]
            self.connect(*chain)
            self.connect((self.fll, 1), self.null1)
            self.connect((self.fll, 2), self.null2)
            self.connect((self.fll, 3), self.null3)

    tb = Tb()
    tb.start()
    time.sleep(max_seconds)
    tb.stop()
    tb.wait()
    return np.array(tb.snk.data(), dtype=np.int8)


def find_sync_positions(bits, max_errors=4):
    """Return positions where sync_y starts in `bits`, both polarities."""
    if len(bits) < len(SYNC_Y):
        return np.array([], dtype=np.int64)
    win = np.lib.stride_tricks.sliding_window_view(bits, len(SYNC_Y))
    neg = (1 - SYNC_Y).astype(np.int8)
    d_pos = (win != SYNC_Y).sum(axis=1)
    d_neg = (win != neg).sum(axis=1)
    d_min = np.minimum(d_pos, d_neg)
    pos = np.where(d_min <= max_errors)[0]
    # De-bounce — drop hits within 5 bits of a previous hit
    keep = []
    last = -10
    for p in pos:
        if p - last >= 5:
            keep.append(p)
            last = p
    return np.array(keep, dtype=np.int64)


def extract_bursts(bits, sync_positions):
    """For each sync position, extract the 510-bit burst centered around it.
    Drops any burst that would extend beyond bit-array bounds.
    Returns a 2-D int8 array of shape (n_bursts, BURST_TOTAL_BITS)."""
    bursts = []
    for s in sync_positions:
        start = int(s) - SYNC_Y_OFFSET_BITS
        end = start + BURST_TOTAL_BITS
        if start < 0 or end > len(bits):
            continue
        bursts.append(bits[start:end])
    if not bursts:
        return np.empty((0, BURST_TOTAL_BITS), dtype=np.int8)
    return np.stack(bursts)


def main():
    iq_path = sys.argv[1]
    out_path = sys.argv[2]
    offset_hz = int(sys.argv[3]) if len(sys.argv) > 3 else 1000

    sys.stderr.write(f"[1/4] demodulating {iq_path} (offset={offset_hz} Hz)\n")
    bits = demodulate(iq_path, offset_hz)
    sec = len(bits) / 36000.0
    sys.stderr.write(f"      got {len(bits)} bits ({sec:.1f} s)\n")

    sys.stderr.write(f"[2/4] sync-pattern search\n")
    pos = find_sync_positions(bits, max_errors=4)
    sys.stderr.write(f"      {len(pos)} sync hits (rate {len(pos)/sec:.2f}/s)\n")
    if len(pos) > 1:
        gaps_ms = np.diff(pos) / 36.0
        sys.stderr.write(f"      gaps: min={gaps_ms.min():.1f} median={np.median(gaps_ms):.1f} "
                        f"max={gaps_ms.max():.1f} ms\n")
        n340 = int(((gaps_ms >= 320) & (gaps_ms <= 360)).sum())
        sys.stderr.write(f"      gaps in 340 ms ±20 (DMO DSB pattern): {n340}/{len(gaps_ms)}\n")

    sys.stderr.write(f"[3/4] burst extraction (510 bits anchored on sync_y)\n")
    bursts = extract_bursts(bits, pos)
    sys.stderr.write(f"      {len(bursts)} full bursts kept\n")

    sys.stderr.write(f"[4/4] field stability analysis\n")
    if len(bursts) > 1:
        # Per-bit agreement across bursts — bit positions where ALL bursts agree
        # are likely fixed fields (tail, freq corr); positions with random bits
        # are SCH payload.
        majority = np.round(bursts.mean(axis=0)).astype(np.int8)
        agreement = (bursts == majority).mean(axis=0)
        regions = [
            ("tail/phase pre",   0,               FCORR_OFFSET_BITS),
            ("freq correction",  FCORR_OFFSET_BITS, FCORR_OFFSET_BITS + FCORR_LEN_BITS),
            ("SCH/S BLK1",       BLK1_OFFSET_BITS,  BLK1_OFFSET_BITS  + BLK1_LEN_BITS),
            ("sync y (38)",      SYNC_Y_OFFSET_BITS, SYNC_Y_OFFSET_BITS + SYNC_Y_LEN_BITS),
            ("BBK (30)",         BBK_OFFSET_BITS,    BBK_OFFSET_BITS    + BBK_LEN_BITS),
            ("SCH/S BLK2",       BLK2_OFFSET_BITS,   BLK2_OFFSET_BITS   + BLK2_LEN_BITS),
            ("tail post",        BLK2_OFFSET_BITS + BLK2_LEN_BITS, BURST_TOTAL_BITS),
        ]
        for label, a, b in regions:
            mean_agree = agreement[a:b].mean()
            sys.stderr.write(f"      [{a:3d}..{b:3d}] {label:<18}  mean agreement = {mean_agree:.3f}\n")

    bursts.astype(np.uint8).tofile(out_path)
    sys.stderr.write(f"saved {len(bursts)} × {BURST_TOTAL_BITS} bits → {out_path}\n")


if __name__ == '__main__':
    main()
