#!/usr/bin/env python3
"""DMO TCH audio path — krok 4b.

Z bit-streamu wyciąga DNB ramki, descramble'uje BLK1+BLK2 (432 bity type4
po pre-diff DQPSK) i karmi nimi cdecoder|sdecoder w formacie 690×int16 z
sync marks 0x6b21..0x6b24 (sq5bpf-format). Wyjście PCM 8 kHz int16 zapisuje
do WAV.

Walidacja kroku 4b: po uruchomieniu na dmo_433400_36k.iq powinien powstać
WAV z DMO call audio z Tetrapack 901-9999, ISSI 2600824.

Usage: python3 dmo_tch_extract.py <iq_file> <out_wav> [src=2600824] [mni=14771983] [offset_hz=1000]

Format bloku do cdecoder (per sq5bpf tetra_lower_mac.c:307-327):
  block[690] int16, każdy bit jako ±127:
    block[0]   = 0x6b21
    block[1..114]   = type4[  0..113] mapped (-127/+127)
    block[115] = 0x6b22
    block[116..229] = type4[114..227]
    block[230] = 0x6b23
    block[231..344] = type4[228..341]
    block[345] = 0x6b24
    block[346..435] = type4[342..431]
    block[460..]   = keystream params (zerowane = clear voice)

DMO scrambling (ETSI EN 300 396-3 §8.2.4, osmo-tetra-dmo tetra_scramb.c):
   mni    &= 0x3f         # dolne 6 bitów MNI = colour code
   srcaddr &= 0xffffff    # 24-bit SSI
   init   = (srcaddr) | (mni << 24)
   init   = (init << 2) | 3
"""
import sys, math, time, cmath, struct, subprocess, wave
import numpy as np

# Training sequences (ETSI EN 300 396-2 §9.4.3.3.3)
N_BITS = np.array([1,1, 0,1, 0,0, 0,0, 1,1, 1,0, 1,0, 0,1, 1,1, 0,1, 0,0], dtype=np.int8)
P_BITS = np.array([0,1, 1,1, 1,0, 1,0, 0,1, 0,0, 0,0, 1,1, 0,1, 1,1, 1,0], dtype=np.int8)

# Bit offsets w DNB (470 bitów totalnie, anchored on training-seq start at offset 230)
TRAIN_OFFSET = 230
BLK1_BEFORE_TRAIN = 216  # BLK1 zaraz przed training seq
BLK2_AFTER_TRAIN = 22    # BLK2 zaraz za training seq
BLK_LEN_BITS = 216


def demodulate(iq_path, offset_hz, seconds=70):
    from gnuradio import gr, blocks, analog, digital
    from gnuradio.filter import firdes

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
            for i in range(1, 4):
                self.connect((self.fll, i), blocks.null_sink(gr.sizeof_float))
            chain = [self.src, self.throt]
            if self.rot: chain.append(self.rot)
            chain += [self.agc, self.fll, self.cs, self.eq, self.dp, self.dec, self.mp, self.un, self.snk]
            self.connect(*chain)

    tb = Tb()
    tb.start()
    time.sleep(seconds)
    tb.stop()
    tb.wait()
    return np.array(tb.snk.data(), dtype=np.int8)


def find_pattern(bits, pattern, max_errors=2, debounce=200):
    """Find positions where pattern matches (or its bit-inverted form)."""
    win = np.lib.stride_tricks.sliding_window_view(bits, len(pattern))
    neg = (1 - pattern).astype(np.int8)
    d_pos = (win != pattern).sum(axis=1)
    d_neg = (win != neg).sum(axis=1)
    d_min = np.minimum(d_pos, d_neg)
    pos = np.where(d_min <= max_errors)[0]
    keep = []
    last = -debounce
    for p in pos:
        if p - last >= debounce:
            keep.append((p, int(d_pos[p] < d_neg[p])))  # (pos, polarity: 1=normal, 0=inverted)
            last = p
    return keep  # list of (pos, polarity)


def scramble_seq(init, length):
    """32-bit LFSR per ETSI/osmo-tetra tetra_scramb.c (taps 32-26-23-22-16-12-11-10-8-7-5-4-2-1)."""
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
    """Per ETSI EN 300 396-3 §8.2.4 / osmo-tetra-dmo tetra_dmo_scramb_get_init."""
    mni6 = mni & 0x3F
    src24 = src & 0xFFFFFF
    init = src24 | (mni6 << 24)
    init = (init << 2) | 3
    return init & 0xFFFFFFFF


def pack_block_690(type4_432, decrypted=0):
    """Pack 432 type4 bits into sq5bpf-style int16[690] block z sync marks."""
    block = np.zeros(690, dtype=np.int16)
    sync_marks = [0x6b21, 0x6b22, 0x6b23, 0x6b24, 0x6b25, 0x6b26]
    for i, m in enumerate(sync_marks):
        block[115 * i] = m
    # Mapowanie: bit=1 → -127, bit=0 → +127 (per sq5bpf code)
    mapped = np.where(np.asarray(type4_432, dtype=np.int8) == 1, -127, 127).astype(np.int16)
    block[1:115]     = mapped[0:114]
    block[116:230]   = mapped[114:228]
    block[231:345]   = mapped[228:342]
    block[346:436]   = mapped[342:432]
    # block[460..] zostawiamy zerami (clear voice, brak keystream)
    return block


def main():
    iq_path = sys.argv[1]
    out_wav = sys.argv[2]
    src = int(sys.argv[3]) if len(sys.argv) > 3 else 2600824
    mni = int(sys.argv[4]) if len(sys.argv) > 4 else (901 << 14) | 9999  # = 14771983
    offset_hz = int(sys.argv[5]) if len(sys.argv) > 5 else 1000

    sys.stderr.write(f"[1/5] demoduluje {iq_path}\n")
    bits = demodulate(iq_path, offset_hz)
    sec = len(bits) / 36000.0
    sys.stderr.write(f"      {len(bits)} bitów ({sec:.1f}s)\n")

    sys.stderr.write(f"[2/5] szukam DNB (n_bits + p_bits)\n")
    # max_err=1 zamiast 2: random baseline (max_err=2) daje ~40 fake'ów na 60s.
    # @max_err=1 mamy 76 hits z których ~26 ma regularny gap=slot.
    hits = find_pattern(bits, N_BITS, max_errors=1)
    hits += find_pattern(bits, P_BITS, max_errors=1)
    hits.sort(key=lambda x: x[0])
    sys.stderr.write(f"      {len(hits)} kandydat. DNB pozycji\n")

    sys.stderr.write(f"[3/5] descramble (src={src}, mni={mni}, init=0x{dmo_scramb_init(mni, src):08x})\n")
    init = dmo_scramb_init(mni, src)
    scramb_432 = scramble_seq(init, 432)

    sys.stderr.write(f"[4/5] pakuje TCH ramki i karmię cdecoder|sdecoder\n")
    cdec_path = '/home/brzoza/tetra-kit/codec/cdecoder'
    sdec_path = '/home/brzoza/tetra-kit/codec/sdecoder'
    pipe_r, pipe_w = subprocess.os.pipe()
    cdec = subprocess.Popen([cdec_path, '/dev/stdin', '/dev/stdout'],
                            stdin=subprocess.PIPE, stdout=pipe_w,
                            stderr=subprocess.DEVNULL)
    subprocess.os.close(pipe_w)
    sdec = subprocess.Popen([sdec_path, '/dev/stdin', '/dev/stdout'],
                            stdin=pipe_r, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    subprocess.os.close(pipe_r)

    n_sent = 0
    n_inverted = 0
    for pos, polarity in hits:
        blk1_start = pos - BLK1_BEFORE_TRAIN
        blk2_start = pos + BLK2_AFTER_TRAIN
        blk2_end = blk2_start + BLK_LEN_BITS
        if blk1_start < 0 or blk2_end > len(bits):
            continue
        blk1 = bits[blk1_start:blk1_start + BLK_LEN_BITS]
        blk2 = bits[blk2_start:blk2_end]
        t5 = np.concatenate([blk1, blk2])
        # Jeśli training seq pasował tylko w odwróconej polarności (DQPSK 180°),
        # cały burst też jest zanegowany — odwracamy bity przed descramble.
        if polarity == 0:
            t5 = (1 - t5).astype(np.int8)
            n_inverted += 1
        t4 = (t5 ^ scramb_432).astype(np.int8)
        block = pack_block_690(t4)
        try:
            cdec.stdin.write(block.tobytes())
            cdec.stdin.flush()
            n_sent += 1
        except BrokenPipeError:
            break

    sys.stderr.write(f"      wysłano {n_sent} ramek TCH do cdecoder (z czego {n_inverted} odwróconych)\n")
    try:
        cdec.stdin.close()
    except Exception:
        pass

    sys.stderr.write(f"[5/5] czytam PCM ze sdecoder i piszę WAV\n")
    # sdecoder output: każda ramka = 2 speech frames × (137+1)/8 ≈ but pcm output to int16 240 sampli per 30ms ramka
    pcm_chunks = []
    try:
        while True:
            chunk = sdec.stdout.read(4096)
            if not chunk:
                break
            pcm_chunks.append(chunk)
    except Exception:
        pass

    cdec.wait(timeout=5)
    sdec.wait(timeout=5)
    pcm_data = b''.join(pcm_chunks)
    sys.stderr.write(f"      PCM bytes: {len(pcm_data)}\n")

    # Zapisz jako WAV 8 kHz mono int16
    with wave.open(out_wav, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(pcm_data)
    sys.stderr.write(f"      zapisano {out_wav} ({len(pcm_data)/16000:.2f}s @ 8kHz)\n")


if __name__ == '__main__':
    main()
