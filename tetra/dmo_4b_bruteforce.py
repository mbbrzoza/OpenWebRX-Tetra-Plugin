#!/usr/bin/env python3
"""Brute force descramble init — szuka mni&0x3f i src które daje niezerową audio energy.

Jeśli scramble jest pominięte / źle, cdecoder traktuje wszystko jako BFI →
RMS(PCM) ~ 12. Jeśli prawidłowe, RMS powinien być znacznie wyższy (real voice).

Test variants:
  - 64 wartości mni&0x3f (6-bit colour code)
  - kilka src kandydatów (z Tetrapack history)
  - bonus: bez scramble w ogóle
"""
import sys, os, math, time, cmath, subprocess
import numpy as np

sys.path.insert(0, '.')
from dmo_tch_extract import (N_BITS, P_BITS, find_pattern, scramble_seq,
                              pack_block_690, BLK1_BEFORE_TRAIN, BLK2_AFTER_TRAIN,
                              BLK_LEN_BITS, demodulate)


def try_descramble(bits, hits, scramb_init):
    """Run cdecoder|sdecoder on given hits with given scramble init. Returns PCM bytes."""
    scramb_432 = scramble_seq(scramb_init, 432) if scramb_init is not None else np.zeros(432, dtype=np.int8)
    cdec_path = '/home/brzoza/tetra-kit/codec/cdecoder'
    sdec_path = '/home/brzoza/tetra-kit/codec/sdecoder'
    pipe_r, pipe_w = os.pipe()
    cdec = subprocess.Popen([cdec_path, '/dev/stdin', '/dev/stdout'],
                            stdin=subprocess.PIPE, stdout=pipe_w,
                            stderr=subprocess.DEVNULL)
    os.close(pipe_w)
    sdec = subprocess.Popen([sdec_path, '/dev/stdin', '/dev/stdout'],
                            stdin=pipe_r, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    os.close(pipe_r)

    n_sent = 0
    for pos, polarity in hits:
        b1s = pos - BLK1_BEFORE_TRAIN
        b2s = pos + BLK2_AFTER_TRAIN
        b2e = b2s + BLK_LEN_BITS
        if b1s < 0 or b2e > len(bits):
            continue
        t5 = np.concatenate([bits[b1s:b1s + BLK_LEN_BITS], bits[b2s:b2e]])
        if polarity == 0:
            t5 = (1 - t5).astype(np.int8)
        t4 = (t5 ^ scramb_432).astype(np.int8)
        block = pack_block_690(t4)
        try:
            cdec.stdin.write(block.tobytes())
            n_sent += 1
        except BrokenPipeError:
            break
    try:
        cdec.stdin.flush()
        cdec.stdin.close()
    except Exception:
        pass
    pcm_chunks = []
    try:
        while True:
            chunk = sdec.stdout.read(4096)
            if not chunk:
                break
            pcm_chunks.append(chunk)
    except Exception:
        pass
    try:
        cdec.wait(timeout=3); sdec.wait(timeout=3)
    except Exception:
        cdec.kill(); sdec.kill()
    return b''.join(pcm_chunks)


def pcm_energy(pcm_bytes):
    if len(pcm_bytes) < 100:
        return 0, 0
    d = np.frombuffer(pcm_bytes, dtype=np.int16)
    rms = float(np.sqrt(np.mean(d.astype(np.float64) ** 2)))
    pk = int(np.abs(d).max())
    return rms, pk


def main():
    iq_path = sys.argv[1]
    sys.stderr.write(f"[1/3] demoduluje {iq_path}\n")
    bits = demodulate(iq_path, 1000)
    sys.stderr.write(f"      {len(bits)} bits ({len(bits)/36000:.1f}s)\n")

    sys.stderr.write(f"[2/3] szukam DNB @max_err=1\n")
    hits = find_pattern(bits, N_BITS, max_errors=1) + find_pattern(bits, P_BITS, max_errors=1)
    hits.sort(key=lambda x: x[0])
    sys.stderr.write(f"      {len(hits)} hits\n")

    candidates = []
    # 64 wartości mni&0x3f × kilka src
    srcs = [2600824, 2600437, 2603835, 0, 0xFFFFFF]
    sys.stderr.write(f"[3/3] brute force ({len(srcs)} src × 64 mni + bez scramble)\n")
    sys.stderr.write(f"      bez scramble (control):\n")
    pcm = try_descramble(bits, hits, None)
    rms, pk = pcm_energy(pcm)
    sys.stderr.write(f"        RMS={rms:7.1f}  peak={pk:5d}\n")
    candidates.append(("no_scramble", rms, pk))

    for src in srcs:
        best_rms = 0
        best_mni = -1
        for mni6 in range(64):
            init = (((src & 0xFFFFFF) | (mni6 << 24)) << 2) | 3
            pcm = try_descramble(bits, hits, init)
            rms, pk = pcm_energy(pcm)
            if rms > best_rms:
                best_rms = rms
                best_mni = mni6
                best_pk = pk
        sys.stderr.write(f"      src={src:8d}: best mni&0x3f={best_mni:2d} RMS={best_rms:7.1f} pk={best_pk}\n")
        candidates.append((f"src={src} mni&0x3f={best_mni}", best_rms, best_pk))

    candidates.sort(key=lambda x: -x[1])
    print("\nTop 5 candidates (by RMS):")
    for name, rms, pk in candidates[:5]:
        print(f"  RMS={rms:7.1f}  peak={pk:5d}  {name}")


if __name__ == '__main__':
    main()
