#!/usr/bin/env python3
"""Parsuje bursts file (output z dmo_burst_extract.py) → JSON z sparowanymi
DMAC-SYNC/DPRES-SYNC PDU. Używane do statycznego DMO panelu w UI.

Usage: python3 dmo_parse_to_json.py <bursts_file> <out_json>
"""
import sys, json
import numpy as np

sys.path.insert(0, '.')
from dmo_l1_chain import (tetra_descramble, block_deinterleave, rcpc_depunct_2_3,
                          viterbi_cch_decode, crc16_itut_bits,
                          BLK1_OFFSET_BITS, BLK1_LEN_BITS,
                          BLK2_LEN_BITS, BURST_TOTAL_BITS,
                          SB1_TYPE1_BITS, SB1_TYPE2_BITS, SB1_INTERLEAVE_A,
                          SB2_TYPE1_BITS, SB2_TYPE2_BITS, SB2_INTERLEAVE_A,
                          TETRA_CRC_OK, SCRAMB_INIT_BSCH)
from dmo_pdu_parser import parse_sync_pdu, format_sync_pdu

DMO_BLK2_OFFSET = 252  # DMO DSB layout (bez BBK)


def decode_block(t5, type1_bits, type2_bits, interleave_a, len_t5):
    t4 = tetra_descramble(t5, SCRAMB_INIT_BSCH)
    t3 = block_deinterleave(len_t5, interleave_a, t4)
    mother = rcpc_depunct_2_3(t3, len_t5)
    t2, _ = viterbi_cch_decode(mother[:type2_bits * 4], type2_bits)
    crc = crc16_itut_bits(t2[:type1_bits + 16])
    return t2, crc


def main():
    src = sys.argv[1]
    dst = sys.argv[2]
    bursts = np.fromfile(src, dtype=np.uint8).reshape(-1, BURST_TOTAL_BITS)
    n = len(bursts)

    parsed = []
    n_s = n_h = n_both = 0
    src_ssis = {}
    dst_ssis = {}
    msg_types = {}
    mni_seen = {}

    for idx in range(n):
        blk1 = bursts[idx, BLK1_OFFSET_BITS:BLK1_OFFSET_BITS + BLK1_LEN_BITS]
        blk2 = bursts[idx, DMO_BLK2_OFFSET:DMO_BLK2_OFFSET + BLK2_LEN_BITS]
        t2_s, crc_s = decode_block(blk1, SB1_TYPE1_BITS, SB1_TYPE2_BITS, SB1_INTERLEAVE_A, BLK1_LEN_BITS)
        t2_h, crc_h = decode_block(blk2, SB2_TYPE1_BITS, SB2_TYPE2_BITS, SB2_INTERLEAVE_A, BLK2_LEN_BITS)
        s_ok = crc_s == TETRA_CRC_OK
        h_ok = crc_h == TETRA_CRC_OK
        n_s += s_ok; n_h += h_ok
        if not s_ok:
            continue
        if h_ok:
            n_both += 1
            rec = parse_sync_pdu(t2_s[:SB1_TYPE1_BITS], t2_h[:SB2_TYPE1_BITS])
        else:
            rec = parse_sync_pdu(t2_s[:SB1_TYPE1_BITS])
        rec['burst_idx'] = idx
        rec['both_ok'] = h_ok
        rec['summary'] = format_sync_pdu(rec)
        parsed.append(rec)
        if 'src_address' in rec:
            src_ssis[rec['src_address']] = src_ssis.get(rec['src_address'], 0) + 1
        if 'dest_address' in rec:
            dst_ssis[rec['dest_address']] = dst_ssis.get(rec['dest_address'], 0) + 1
        if 'message_type_name' in rec:
            msg_types[rec['message_type_name']] = msg_types.get(rec['message_type_name'], 0) + 1
        if 'mcc' in rec:
            key = f"{rec['mcc']}-{rec['mnc']}"
            mni_seen[key] = mni_seen.get(key, 0) + 1

    # JSON-friendly: serializuj numpy → int, usuń bytes arrays które nie są przydatne
    for rec in parsed:
        for k in list(rec.keys()):
            if k.endswith('_bits'):
                del rec[k]
            elif isinstance(rec[k], (np.integer, np.bool_)):
                rec[k] = int(rec[k])

    out = {
        'meta': {
            'source': src,
            'total_bursts': int(n),
            'sch_s_ok': int(n_s),
            'sch_h_ok': int(n_h),
            'both_ok': int(n_both),
        },
        'stats': {
            'unique_src_ssi': {str(k): v for k, v in src_ssis.items()},
            'unique_dst_ssi': {str(k): v for k, v in dst_ssis.items()},
            'message_types': msg_types,
            'mni_seen': mni_seen,
        },
        'pdus': parsed,
    }

    with open(dst, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"saved {dst}: {len(parsed)} parsed PDU "
          f"({n_s} S/{n_h} H/{n_both} both of {n} total)")
    print(f"unique SSIs: src={list(src_ssis)} dst={list(dst_ssis)}")
    print(f"msg_types: {msg_types}")
    print(f"MNI: {mni_seen}")


if __name__ == '__main__':
    main()
