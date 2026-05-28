#!/usr/bin/env python3
"""TETRA L1 channel-coding chain — krok 2b/c/d/e.

Pipeline (per ETSI EN 300 392-2 §8 and §9.4, EN 300 396-2 §8):
  type-5 (received)  → de-scramble → type-4
  type-4             → block deinterleave → type-3
  type-3             → RCPC depuncture → type-3dp (mother code)
  type-3dp           → 16-state Viterbi → type-2 (information + tail + CRC)
  type-2             → CRC-16 ITU verify → type-1 (logical bits, validated)

Ports of:
  - tetra_scramb.c (scrambler LFSR)
  - tetra_interleave.c (block_deinterleave)
  - tetra_conv_enc.c (RCPC depuncture)
  - viterbi_cch.c (16-state TETRA control-channel Viterbi)
  - crc_simple.c (CRC-16 ITU)

Validation: process 47 DSB bursts from /tmp/dmo_bursts.bin and count CRC OK.
If TETRA L1 correct and DMO uses same params as TMO SB1, CRC should pass on
the majority of clean bursts (>30%).

Usage: python3 dmo_l1_chain.py <bursts_file>
"""
import sys
import numpy as np

# ---------- Burst layout (anchored on sync_y, from dmo_burst_extract.py) ----------
BURST_TOTAL_BITS = 500
BLK1_OFFSET_BITS = 94
BLK1_LEN_BITS    = 120   # type-5 bits for SB1
BBK_OFFSET_BITS  = 252
BBK_LEN_BITS     = 30
BLK2_OFFSET_BITS = 282
BLK2_LEN_BITS    = 216   # type-5 bits for SB2 / NDB

# SB1 (BSCH carrier) parameters per ETSI §8.3.1.1
SB1_TYPE2_BITS  = 80     # after Viterbi
SB1_TYPE1_BITS  = 60     # content
SB1_INTERLEAVE_A = 11
SB1_MOTHER_RATE  = 4     # 1/4 mother code rate

# SB2 parameters per ETSI §8.3.1.3 (SCH/F=BNCH=STCH)
SB2_TYPE2_BITS  = 144
SB2_TYPE1_BITS  = 124
SB2_INTERLEAVE_A = 101
SB2_MOTHER_RATE  = 4

SCRAMB_INIT_BSCH = 3     # ETSI §8.2.5.2 BSCH pre-defined seed
TETRA_CRC_OK = 0x1d0f    # ETSI/osmo magic remainder for CRC-16-CCITT over (info+CRC).


# ---------- 2b: Scrambler ----------
def tetra_scramble_seq(init, length):
    """32-bit LFSR (Fibonacci form), taps 32-26-23-22-16-12-11-10-8-7-5-4-2-1.
    Port of tetra_scramb.c:next_lfsr_bit. Output bit = XOR of MSB-aligned taps."""
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


def tetra_descramble(bits, init):
    """XOR bits with scrambling sequence — self-inverse, descramble = scramble."""
    return (np.asarray(bits, dtype=np.int8) ^ tetra_scramble_seq(init, len(bits))).astype(np.int8)


# ---------- 2c: Block deinterleave ----------
def block_deinterleave(K, a, bits_in):
    """ETSI §8.2.4.1: out[i-1] = in[((1 + a*i) mod K) - 1] for i in 1..K.
    Port of tetra_interleave.c:block_deinterleave."""
    out = np.empty(K, dtype=np.int8)
    for i in range(1, K + 1):
        k = 1 + ((a * i) % K)
        out[i - 1] = bits_in[k - 1]
    return out


# ---------- 2d: RCPC depuncture ----------
# Rate 2/3 pattern (ETSI §8.2.3.1.3): puncturing coefficients P(1)=1, P(2)=2, P(3)=5
# t=3 (kept per period), period=8 (mother bits per period). i_func = identity.
# NOTE: spec/osmo use 1-based indexing P(1..3); we store 0-based [1,2,5] and access
# P[(i-1) % t]. Previously had P=(0,1,2,5) and accessed P[(i-1)%3] which produced
# k offsets [0,1,2,8,9,10,...] instead of correct [1,2,5,9,10,13,...].
P_RATE_2_3 = (1, 2, 5)
T_RATE_2_3 = 3
PERIOD = 8


def rcpc_depunct_2_3(type3_bits, type3_len):
    """De-puncture rate 2/3 → mother rate 1/4, leaving erasures (-1) at dropped positions.
    For each input position j (1..type3_len):
        i = j  (i_func_equals)
        k = period * ((i-1) div t) + P((i-1) mod t + 1)
        out[k-1] = in[j-1]
    Mother length grows in proportion: type3_len/t * period (when divisible)."""
    mother_len = ((type3_len + T_RATE_2_3 - 1) // T_RATE_2_3) * PERIOD + PERIOD
    out = np.full(mother_len, -1, dtype=np.int8)
    for j in range(1, type3_len + 1):
        i = j  # i_func_equals
        k = PERIOD * ((i - 1) // T_RATE_2_3) + P_RATE_2_3[(i - 1) % T_RATE_2_3]
        if 1 <= k <= mother_len:
            out[k - 1] = type3_bits[j - 1]
    return out


# ---------- 2d: 16-state Viterbi (TETRA control-channel) ----------
# Tables from osmo-tetra viterbi_cch.c (port of EN 300 392-2 §8.2.3.1.1 mother code).
# G1 = 1 + D + D4
# G2 = 1 + D2 + D3 + D4
# G3 = 1 + D + D2 + D4
# G4 = 1 + D + D3 + D4
CONV_NEXT_OUTPUT = (
    ( 0, 15), (11,  4), ( 6,  9), (13,  2),
    ( 5, 10), (14,  1), ( 3, 12), ( 8,  7),
    (15,  0), ( 4, 11), ( 9,  6), ( 2, 13),
    (10,  5), ( 1, 14), (12,  3), ( 7,  8),
)
CONV_NEXT_STATE = (
    ( 0,  1), ( 2,  3), ( 4,  5), ( 6,  7),
    ( 8,  9), (10, 11), (12, 13), (14, 15),
    ( 0,  1), ( 2,  3), ( 4,  5), ( 6,  7),
    ( 8,  9), (10, 11), (12, 13), (14, 15),
)


def viterbi_cch_decode(mother_bits, n_info):
    """Decode n_info information bits from `mother_bits` (length 4*n_info).
    `mother_bits` are 0/1 with -1 indicating erasure (depunctured holes).
    Returns decoded bits as np.int8 array of length n_info.

    Uses Hamming-distance metric (hard decision); erasures contribute 0 cost.
    State = 4 delay registers (16 states). Trellis stops at any state (tail-biting
    is NOT used; TETRA terminates with 4 zero tail bits, so we expect the final
    state to be 0)."""
    N_STATES = 16
    INF = 10**9
    # Forward pass: cost[state] at each time step
    # Initialize: start in state 0 (TETRA convention — encoder starts at 0)
    cost = [INF] * N_STATES
    cost[0] = 0
    # Trace back: predecessor[step][state] = (prev_state, bit)
    trace_state = np.zeros((n_info, N_STATES), dtype=np.uint8)
    trace_bit   = np.zeros((n_info, N_STATES), dtype=np.uint8)

    for step in range(n_info):
        rx = mother_bits[step * 4 : step * 4 + 4]  # 4 received symbols (with -1 = erasure)
        new_cost = [INF] * N_STATES
        for prev_state in range(N_STATES):
            if cost[prev_state] >= INF:
                continue
            for bit in (0, 1):
                out_pattern = CONV_NEXT_OUTPUT[prev_state][bit]
                next_state = CONV_NEXT_STATE[prev_state][bit]
                # Hamming distance between rx and 4-bit out_pattern.
                # CONV_NEXT_OUTPUT table packs g1 as MSB (bit 3), g4 as LSB (bit 0)
                # — matches libosmocore convention. Encoder emits [g1,g2,g3,g4]
                # in stream order, so rx[0]=g1 ↔ pattern bit 3.
                d = 0
                for b in range(4):
                    expected = (out_pattern >> (3 - b)) & 1
                    if rx[b] < 0:
                        continue  # erasure
                    if rx[b] != expected:
                        d += 1
                total = cost[prev_state] + d
                if total < new_cost[next_state]:
                    new_cost[next_state] = total
                    trace_state[step][next_state] = prev_state
                    trace_bit[step][next_state] = bit
        cost = new_cost

    # Traceback from the best final state (prefer state 0 due to tail bits;
    # but if signal is noisy we accept best overall).
    best_state = 0 if cost[0] < INF else int(np.argmin(cost))
    bits = np.zeros(n_info, dtype=np.int8)
    state = best_state
    for step in range(n_info - 1, -1, -1):
        bits[step] = trace_bit[step][state]
        state = trace_state[step][state]
    return bits, min(cost)


# ---------- 2e: CRC-16 ITU ----------
def crc16_itut_bits(bits, init=0xFFFF, poly=0x1021):
    """Port of crc16_itut_bits from crc_simple.c.
    For each bit i: XOR bit into MSB; if MSB now 1, shift left + XOR poly; else just shift."""
    crc = init & 0xFFFF
    for b in bits:
        bit = int(b) & 1
        crc ^= (bit << 15)
        if crc & 0x8000:
            crc = ((crc << 1) ^ poly) & 0xFFFF
        else:
            crc = (crc << 1) & 0xFFFF
    return crc


# ---------- Self-tests ----------
def selftest_scrambler():
    """Test scrambler matches osmo-tetra reference output for init=3.
    Known property: after exactly 32 zero-input cycles with init=3, state
    should match a specific deterministic pattern. We just check determinism
    + length and that all-zeros input gives non-trivial output."""
    seq = tetra_scramble_seq(3, 32)
    # Sanity: not all zeros, not all ones
    assert seq.sum() > 0 and seq.sum() < 32, f"scrambler degenerate: sum={seq.sum()}"
    # Determinism: re-run gives same result
    assert (seq == tetra_scramble_seq(3, 32)).all()
    return True


def selftest_deinterleave():
    """K=120, a=11 is a valid permutation: applying twice (forward then back)
    should restore the input. Test: interleave then deinterleave is identity."""
    K = 120
    a = 11
    rng = np.random.default_rng(0)
    src = rng.integers(0, 2, K).astype(np.int8)
    # Interleave: inverse of deinterleave (out[k-1] = in[i-1]) — manual
    inter = np.empty(K, dtype=np.int8)
    for i in range(1, K + 1):
        k = 1 + ((a * i) % K)
        inter[k - 1] = src[i - 1]
    deinter = block_deinterleave(K, a, inter)
    assert (deinter == src).all(), "deinterleave round-trip failed"
    return True


def selftest_viterbi():
    """Encode a known bit sequence with the TETRA mother code, decode, compare.
    Reference encoder (matches G1..G4 generators):"""
    def encode(bits):
        d0 = d1 = d2 = d3 = 0
        out = []
        for b in bits:
            g1 = (b + d0 + d3) % 2  # 1 + D + D4
            g2 = (b + d1 + d2 + d3) % 2
            g3 = (b + d0 + d1 + d3) % 2
            g4 = (b + d0 + d2 + d3) % 2
            out += [g1, g2, g3, g4]
            d3, d2, d1, d0 = d2, d1, d0, b
        return out

    info = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 0, 0, 0, 0]  # 12 info + 4 tail zeros
    coded = np.array(encode(info), dtype=np.int8)
    decoded, cost = viterbi_cch_decode(coded, len(info))
    if (decoded == info).all():
        return True
    print(f"  Viterbi self-test FAILED: cost={cost}")
    print(f"  expected: {info}")
    print(f"  got     : {decoded.tolist()}")
    return False


def main():
    src_path = sys.argv[1]
    bursts = np.fromfile(src_path, dtype=np.uint8).reshape(-1, BURST_TOTAL_BITS)
    n = len(bursts)
    print(f"loaded {n} bursts × {BURST_TOTAL_BITS} bits from {src_path}")

    print("\n=== Self-tests ===")
    print(f"  scrambler:    {'PASS' if selftest_scrambler() else 'FAIL'}")
    print(f"  deinterleave: {'PASS' if selftest_deinterleave() else 'FAIL'}")
    vit_ok = selftest_viterbi()
    print(f"  viterbi:      {'PASS' if vit_ok else 'FAIL'}")
    if not vit_ok:
        print("  → Viterbi self-test failure means tables/decoder algorithm is wrong.")
        print("    BSCH decode will not work; aborting CRC sweep.")
        return

    # Process BLK1 of each burst as SB1 (BSCH)
    print(f"\n=== Decoding {n} bursts as SB1 (BSCH) ===")
    blk1_t5 = bursts[:, BLK1_OFFSET_BITS:BLK1_OFFSET_BITS + BLK1_LEN_BITS]
    n_crc_ok = 0
    decoded_t2 = []
    for idx, t5 in enumerate(blk1_t5):
        # 2b descramble
        t4 = tetra_descramble(t5, SCRAMB_INIT_BSCH)
        # 2c deinterleave
        t3 = block_deinterleave(BLK1_LEN_BITS, SB1_INTERLEAVE_A, t4)
        # 2d depuncture + Viterbi
        mother = rcpc_depunct_2_3(t3, BLK1_LEN_BITS)
        # Viterbi expects exactly type2_bits * 4 mother symbols
        mother_used = mother[:SB1_TYPE2_BITS * SB1_MOTHER_RATE]
        t2, cost = viterbi_cch_decode(mother_used, SB1_TYPE2_BITS)
        # 2e CRC verify: crc16 over type1_bits+16 = 60+16 = 76 bits (info + appended CRC).
        # The trailing 4 tail bits in type2 are NOT included.
        crc = crc16_itut_bits(t2[:SB1_TYPE1_BITS + 16])
        if crc == TETRA_CRC_OK:
            n_crc_ok += 1
            decoded_t2.append(t2)
        if idx < 5:
            print(f"  burst {idx:2d}: vit_cost={cost:3d}  crc=0x{crc:04x}  "
                  f"{'OK' if crc == TETRA_CRC_OK else 'BAD'}")

    print(f"\nCRC OK: {n_crc_ok}/{n}  ({100*n_crc_ok/n:.1f}%)")

    if n_crc_ok > 0:
        # Show decoded fields from first OK burst (SYNC PDU structure)
        # Per tetra_lower_mac.c SB1 layout in type2:
        #   bits [4..10) = colour code (6 bits)
        #   bits [10..12) = TN
        #   bits [12..17) = FN
        #   bits [17..23) = MN
        #   bits [31..41) = MCC (10 bits)
        #   bits [41..55) = MNC (14 bits)
        t2 = decoded_t2[0]
        def bits_to_int(b, o, n):
            v = 0
            for i in range(n):
                v = (v << 1) | int(b[o + i])
            return v
        cc = bits_to_int(t2, 4, 6)
        tn = bits_to_int(t2, 10, 2) + 1
        fn = bits_to_int(t2, 12, 5)
        mn = bits_to_int(t2, 17, 6)
        mcc = bits_to_int(t2, 31, 10)
        mnc = bits_to_int(t2, 41, 14)
        print(f"\nFirst OK burst decoded SYNC PDU:")
        print(f"  ColourCode={cc}  TN={tn}  FN={fn}  MN={mn}  MCC={mcc}  MNC={mnc}")


if __name__ == '__main__':
    main()
