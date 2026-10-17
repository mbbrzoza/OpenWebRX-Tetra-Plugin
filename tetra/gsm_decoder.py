#!/usr/bin/env python3
"""GSM cell identification decoder for OpenWebRX+ (tryb "GSM").
Author: SP8MB

Wejście: stdin = complex float32 IQ @ 500 kS/s wycentrowane na klikniętej nośnej GSM.
Wyjście: linie JSON na stderr (protocol "GSM") -> metaWriter -> panel GsmMetaPanel.
Sterowanie: plik --ctl z częstotliwością (Hz) + SIGUSR1 = przestrojenie (nowa analiza).

Cykl analizy (co --repeat s albo po przestrojeniu):
  1. migawka --snap s IQ; dociągnięcie do rastra GSM 200 kHz
  2. gr-gsm (gnuradio.gsm): receiver -> bursty C0 (FCCH/SCH/normal) + BCCH TS0 -> komunikaty L2
  3. SCH: Viterbi K=5 + parzystość -> BSIC (NCC/BCC) i numer ramki
  4. pycrate: SI1/2/2bis/2ter/2quater/3/4/13 -> MCC/MNC, LAC, CI, alokacja, sąsiedzi GSM/UMTS/LTE, GPRS
  5. ocena anomalii (IMSI-catcher) względem bazy znanych komórek --db
Analizowany jest tylko nośnik BCCH (C0) komórki; nośne TCH (bez BCCH) dają "brak BCCH".
"""
import signal

# moduł może przysłać SIGUSR1 (przestrojenie) zanim skończą się importy gnuradio;
# domyślna akcja SIGUSR1 to zakończenie procesu. Częstotliwość i tak czytamy z --ctl.
signal.signal(signal.SIGUSR1, signal.SIG_IGN)

import argparse
import collections
import json
import os
import sys
import threading
import time

import numpy as np

FS = 500000
FCCH_HZ = 1625000 / 24          # 67,708 kHz: ton FCCH względem nośnej

OPERATORS = {"260-01": "Plus", "260-02": "T-Mobile", "260-03": "Orange", "260-06": "Play",
             "260-17": "Aero2", "260-34": "NetWorkS!"}
SI_NAMES = {0x19: "si1", 0x1a: "si2", 0x02: "si2bis", 0x03: "si2ter", 0x07: "si2quater",
            0x1b: "si3", 0x1c: "si4", 0x00: "si13"}


def emit(**kw):
    kw["protocol"] = "GSM"
    sys.stderr.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def log(msg):
    sys.stderr.write(f"[gsm] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- ARFCN
def arfcn_for(freq_hz):
    """DL częstotliwość -> (ARFCN, pasmo) albo (None, None)."""
    f = round(freq_hz / 1e5) / 10      # MHz z dokładnością 0,1
    n = round((f - 935.0) / 0.2)
    if 0 <= n <= 124:
        return n, "GSM 900"
    if -69 <= n <= -1:                 # E-GSM 975..1023, R-GSM 955..974
        return n + 1024, "E-GSM 900" if n >= -49 else "R-GSM 900"
    n = round((f - 1805.2) / 0.2)
    if 0 <= n <= 373:
        return 512 + n, "DCS 1800"
    n = round((f - 869.2) / 0.2)
    if 0 <= n <= 123:
        return 128 + n, "GSM 850"
    return None, None


def arfcn_mhz(n):
    if 0 <= n <= 124:
        return round(935.0 + 0.2 * n, 1)
    if 955 <= n <= 1023:
        return round(935.0 + 0.2 * (n - 1024), 1)
    if 512 <= n <= 885:
        return round(1805.2 + 0.2 * (n - 512), 1)
    return None


def uarfcn_mhz(u):
    if 2937 <= u <= 3088:              # UMTS 900 (band VIII)
        return round(340 + u / 5, 1)
    return round(u / 5, 1)


# ---------------------------------------------------------------- SCH (BSIC)
def _viterbi_sch(coded):
    """K=5, G0=1+D3+D4, G1=1+D+D3+D4; 78 bitów -> 39 (25 info + 10 parzystości + 4 ogon)."""
    inf = 1 << 30
    metric = [0] + [inf] * 15
    paths = [[] for _ in range(16)]
    for k in range(len(coded) // 2):
        r0, r1 = coded[2 * k], coded[2 * k + 1]
        nm = [inf] * 16
        npth = [None] * 16
        for s in range(16):
            if metric[s] >= inf:
                continue
            u1, u3, u4 = s & 1, (s >> 2) & 1, (s >> 3) & 1
            for u in (0, 1):
                m = metric[s] + ((u ^ u3 ^ u4) != r0) + ((u ^ u1 ^ u3 ^ u4) != r1)
                ns = ((s << 1) | u) & 15
                if m < nm[ns]:
                    nm[ns] = m
                    npth[ns] = paths[s] + [u]
        metric, paths = nm, npth
    return paths[0]


def decode_sch(bits):
    """148 bitów burstu SCH -> dict(ncc, bcc, fn) albo None (zła parzystość)."""
    d = _viterbi_sch(list(bits[3:42]) + list(bits[106:145]))
    r = list(d[:35])
    g = (1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1)      # D^10+D^8+D^6+D^5+D^4+D^2+1
    for i in range(25):
        if r[i]:
            for j in range(11):
                r[i + j] ^= g[j]
    if r[25:35] != [1] * 10:                   # parzystość odwrócona
        return None
    t1 = ((d[1] << 10) | (d[0] << 9) | (d[15] << 8) | (d[14] << 7) | (d[13] << 6) | (d[12] << 5) |
          (d[11] << 4) | (d[10] << 3) | (d[9] << 2) | (d[8] << 1) | d[23])
    t2 = (d[22] << 4) | (d[21] << 3) | (d[20] << 2) | (d[19] << 1) | d[18]
    t3 = 10 * ((d[17] << 2) | (d[16] << 1) | d[24]) + 1
    return {"ncc": (d[7] << 2) | (d[6] << 1) | d[5], "bcc": (d[4] << 2) | (d[3] << 1) | d[2],
            "fn": 51 * ((t3 - t2) % 26) + t3 + 51 * 26 * t1}


def fcch_offset(x):
    """CFO z tonu FCCH (+67,7 kHz): okna 256 próbek z najsilniejszym tonem -> estymata fazowa."""
    w = 256
    nwin = len(x) // 128 - 2
    if nwin < 10:
        return None
    idx = np.arange(nwin) * 128
    frames = np.stack([x[i:i + w] for i in idx])
    spec = np.abs(np.fft.fft(frames, axis=1)) ** 2
    f = np.fft.fftfreq(w, 1 / FS)
    band = (f > FCCH_HZ - 15000) & (f < FCCH_HZ + 15000)
    ratio = spec[:, band].max(1) / (spec.mean(1) + 1e-20)
    top = np.argsort(ratio)[-max(5, nwin // 400):]
    if ratio[top].min() < 20:
        return None
    est = []
    for t in top:
        seg = frames[t]
        est.append(np.angle(np.sum(seg[1:] * np.conj(seg[:-1]))) * FS / (2 * np.pi))
    return float(np.median(est) - FCCH_HZ)


# ---------------------------------------------------------------- gr-gsm
class GrGsm:
    def __init__(self):
        from gnuradio import gr, blocks
        from gnuradio import gsm as grgsm
        import pmt
        self.gr, self.blocks, self.grgsm, self.pmt = gr, blocks, grgsm, pmt

    def run(self, path, fc, arfcn):
        gr, blocks, grgsm, pmt = self.gr, self.blocks, self.grgsm, self.pmt
        tb = gr.top_block()
        src = blocks.file_source(gr.sizeof_gr_complex, path, False)
        inp = grgsm.gsm_input(ppm=0, osr=4, fc=fc, samp_rate_in=FS)
        rx = grgsm.receiver(4, [arfcn or 0], [], False)
        ctl = grgsm.clock_offset_control(fc, FS, osr=4)
        tsf = grgsm.burst_timeslot_filter(0)
        dmp = grgsm.gsm_bcch_ccch_demapper(timeslot_nr=0)
        dec = grgsm.control_channels_decoder()
        mdbg = blocks.message_debug()
        bdbg = blocks.message_debug()
        tb.connect(src, inp, rx)
        tb.msg_connect(rx, "measurements", ctl, "measurements")
        tb.msg_connect(ctl, "ctrl", inp, "ctrl_in")
        tb.msg_connect(rx, "C0", tsf, "in")
        tb.msg_connect(tsf, "out", dmp, "bursts")
        tb.msg_connect(dmp, "bursts", dec, "bursts")
        tb.msg_connect(dec, "msgs", mdbg, "store")
        tb.msg_connect(rx, "C0", bdbg, "store")
        tb.run()

        def pdus(dbg):
            for i in range(dbg.num_messages()):
                yield bytes(pmt.u8vector_elements(pmt.cdr(dbg.get_message(i))))


        # GSMTAP: [6] signal_dbm (int8), [12] sub_type (burst: 1 FCCH, 3 SCH, 6 normal, 7 dummy);
        # komunikaty: 23 bajty L2 (pseudo-długość, PD, typ) po 16-bajtowym nagłówku
        bursts = [(b[12], b[6] - 256 if b[6] > 127 else b[6], b[16:16 + 148]) for b in pdus(bdbg)]
        msgs = [b[16:] for b in pdus(mdbg)]
        return bursts, msgs


# ---------------------------------------------------------------- SI (pycrate)
def _jfind(obj, key, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            _jfind(v, key, out)
    elif isinstance(obj, list):
        for v in obj:
            _jfind(v, key, out)
    return out


def _ints(tree, key):
    return [int(v, 2) for v in _jfind(tree, key, []) if isinstance(v, str) and v and set(v) <= {"0", "1"}]


def _v(m, *path):
    try:
        for p in path:
            m = m[p]
        v = m.get_val()
        return v[0] if isinstance(v, list) and len(v) == 1 else v
    except Exception:
        return None


def _rest(m, name):
    try:
        return [x._to_jval() for x in m[name]]
    except Exception:
        return []


def _arfcns(m, name):
    try:
        return m[name][0].decode()
    except Exception:
        return []


class SiDecoder:
    def __init__(self):
        try:
            import pycrate_mobile.TS44018_RR as RR
            self.cls = RR.RRTypeMTClasses
        except ImportError:
            self.cls = None
            log("brak pycrate - SI nie będą dekodowane (pip install pycrate)")

    def parse(self, l2):
        if self.cls is None or len(l2) < 3 or (l2[1] & 0x0F) != 6:
            return None
        c = self.cls.get(l2[2])
        if c is None:
            return None
        try:
            m = c()
            m.from_bytes(l2)
            return m
        except Exception:
            return None


CCCH_CONF = {0: "1×CCCH", 1: "1×CCCH + SDCCH/4", 2: "2×CCCH", 4: "3×CCCH", 6: "4×CCCH"}
NMO = {0: "I", 1: "II", 2: "III"}
PAGING = (0x21, 0x22, 0x24)
IMM_ASS = (0x3F, 0x39)


try:
    # tabela pasm LTE z dekodera LTE (ten sam katalog); import przed instalacją handlera SIGUSR1
    from lte_decoder import earfcn_info as _earfcn_info
except Exception:
    def _earfcn_info(n):
        return {"earfcn": n}


def summarize(msgs, sid, band, snap_s):
    """Komunikaty L2 z BCCH/CCCH -> pola komórki (do JSON)."""
    out = {}
    uniq = collections.OrderedDict()
    pag = ass = 0
    for l2 in msgs:
        if len(l2) < 3 or (l2[1] & 0x0F) != 6:
            continue
        t = l2[2]
        if t in PAGING:
            pag += 1
        elif t in IMM_ASS:
            ass += 1
        elif t in SI_NAMES:
            uniq.setdefault(l2, t)
    out["load"] = {"paging_s": round(pag / snap_s, 1), "imm_ass_s": round(ass / snap_s, 1)}
    si = collections.defaultdict(list)
    for l2, t in uniq.items():
        m = sid.parse(l2)
        if m is not None:
            si[SI_NAMES[t]].append(m)
    out["si"] = sorted(si)

    s3 = (si.get("si3") or [None])[0]
    s4 = (si.get("si4") or [None])[0]
    lai = s3 or s4
    if lai is not None:
        plmn = str(_v(lai, "LAI", 0, "PLMN") and lai["LAI"][0]["PLMN"].decode() or "")
        if len(plmn) >= 5:
            out["plmn"] = plmn[:3] + "-" + plmn[3:]
            out["operator"] = OPERATORS.get(out["plmn"])
        out["lac"] = _v(lai, "LAI", 0, "LAC")
        sel = lai["CellSelParams"][0]
        rach = lai["RACHCtrl"][0]
        lvl = _v(sel, "MS-TXPWR-MAX-CCH")
        acc = int.from_bytes(_v(rach, "AccessCtrlClass") or b"\0\0", "big")
        out["access"] = {
            "rxlev_min_dbm": -110 + (_v(sel, "RXLEV-ACCESS-MIN") or 0),
            "ms_txpwr_max_dbm": (30 if band == "DCS 1800" else 43) - 2 * lvl if lvl is not None else None,
            "reselect_hyst_db": 2 * (_v(sel, "CellReselectHyst") or 0),
            "barred": bool(_v(rach, "CELL_BARR_ACCESS")),
            "reestab": not _v(rach, "CallReestab"),
            "acc_barred": [i for i in range(16) if i != 10 and acc >> i & 1],
            "emergency_barred": bool(acc >> 10 & 1),
        }
    if s3 is not None:
        out["ci"] = _v(s3, "CellId")
        cc = s3["CtrlChanDesc"][0]
        t3212 = _v(cc, "T3212")
        out["control"] = {
            "ccch": CCCH_CONF.get(_v(cc, "CCCHConf"), _v(cc, "CCCHConf")),
            "agch_res": _v(cc, "BS-AG-BLKS-RES"),
            "pa_mfrms": (_v(cc, "BS-PA-MFRMS") or 0) + 2,
            "att": bool(_v(cc, "ATT")),
            "t3212_min": t3212 * 6 if t3212 else None,
            "mscr": "R99+" if _v(cc, "MSCRel") else "R98",
            "rlt": 4 * ((_v(s3, "CellOpt", 0, "RadioLinkTimeout") or 0) + 1),
            "dtx": _v(s3, "CellOpt", 0, "DTXInd"),
        }
        r3 = _rest(s3, "SI3RestOctets")
        if _jfind(r3, "ra_colour", []):
            out["gprs"] = {"ra_colour": (_ints(r3, "ra_colour") or [None])[0]}
    # C2: CRO zachęca telefony do wyboru komórki (catchery ustawiają go wysoko)
    for m, name in ((s3, "SI3RestOctets"), (s4, "SI4RestOctets")):
        r = _rest(m, name) if m is not None else []
        cro = _ints(r, "cell_reselect_offset")
        if cro:
            tmp = (_ints(r, "temporary_offset") or [0])[0]
            pen = (_ints(r, "penalty_time") or [0])[0]
            out["reselect"] = {"cbq": (_ints(r, "cbq") or [0])[0], "cro_db": 2 * cro[0],
                               "temp_offset_db": None if tmp == 7 else 10 * tmp, "penalty_s": 20 * (pen + 1)}
            break
    s1 = (si.get("si1") or [None])[0]
    if s1 is not None:
        ca = _arfcns(s1, "CellChan")
        out["cell_alloc"] = [{"arfcn": a, "mhz": arfcn_mhz(a)} for a in ca]
    nb = set()
    for name in ("si2", "si2bis", "si2ter"):
        for m in si.get(name, []):
            nb.update(_arfcns(m, "BCCHFreqList"))
            nb.update(_arfcns(m, "ExtBCCHFreqList"))
    if nb:
        out["gsm_neighbours"] = [{"arfcn": a, "mhz": arfcn_mhz(a)} for a in sorted(nb)]
    s2 = (si.get("si2") or [None])[0]
    if s2 is not None:
        ncc = _v(s2, "NCCPermitted")
        if isinstance(ncc, bytes):
            ncc = ncc[0] if ncc else None
        if ncc is not None:
            out["ncc_permitted"] = [i for i in range(8) if ncc >> i & 1]
    umts, lte, prio = {}, {}, {}
    for m in si.get("si2quater", []):
        r = _rest(m, "SI2quaterRestOctets")
        for node in _jfind(r, "repeated_utran_fdd_neighbour_cells_struct", []):
            for u in _ints(node, "fdd_arfcn"):
                umts[u] = {"uarfcn": u, "dl_mhz": uarfcn_mhz(u),
                           "cells": (_ints(node, "nr_of_fdd_cells") or [None])[0]}
        for node in _jfind(r, "repeated_e_utran_neighbour_cells_struct", []):
            p = (_ints(node, "e_utran_priority") or [None])[0]
            for e in _ints(node, "earfcn"):
                lte[e] = dict(_earfcn_info(e), priority=p)
        for k in ("geran_priority", "utran_priority"):
            v = _ints(r, k)
            if v:
                prio[k.split("_")[0]] = v[0]
    if umts:
        out["umts_neighbours"] = sorted(umts.values(), key=lambda d: d["uarfcn"])
    if lte:
        out["lte_neighbours"] = sorted(lte.values(), key=lambda d: d["earfcn"])
    if prio:
        out["priorities"] = prio
    s13 = (si.get("si13") or [None])[0]
    if s13 is not None:
        r = _rest(s13, "SI13RestOctets")
        g = out.setdefault("gprs", {})
        rac = _ints(r, "rac")
        nmo = _ints(r, "nmo")
        g.update({"rac": rac[0] if rac else None, "nmo": NMO.get(nmo[0]) if nmo else None,
                  "egprs": bool(_jfind(r, "egprs_packet_channel_request", []))})
    return out


# ---------------------------------------------------------------- anomalie (IMSI-catcher)
# sieci dzielące RAN: listy sąsiadów i LAC wspólne (NetWorkS! = T-Mobile + Orange)
RAN_GROUP = {"260-02": "NetWorkS!", "260-03": "NetWorkS!"}
TEST_MCC = ("001", "002", "999", "901")


class CellDb:
    """Znane komórki (plik JSON): klucz "plmn|lac|ci". Zapis tylko przy nowej/zmienionej
    komórce albo co 10 min (karta SD), atomowo (tmp + rename) - kilka dekoderów naraz."""

    def __init__(self, path):
        self.path = path
        self.cells = {}
        self.saved = 0

    def load(self):
        try:
            with open(self.path) as f:
                self.cells.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save(self):
        if not self.path:
            return
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self.cells, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.saved = time.time()
        except OSError as e:
            log(f"zapis bazy komórek nieudany: {e}")

    def update(self, res):
        """Najpierw ocena (na stanie sprzed tej analizy), potem zapis komórki."""
        self.load()
        res["alerts"] = assess(res, self.cells)
        if not res.get("plmn") or res.get("ci") is None:
            return
        key = f"{res['plmn']}|{res.get('lac')}|{res['ci']}"
        now = int(time.time())
        rec = {"plmn": res["plmn"], "lac": res.get("lac"), "ci": res["ci"], "arfcn": res.get("arfcn"),
               "bsic": (res.get("bsic") or {}).get("value"), "level": res.get("level_db"),
               "nbrs": [n["arfcn"] for n in res.get("gsm_neighbours", [])],
               "warn": sum(a["level"] == "warn" for a in res["alerts"])}
        old = self.cells.get(key)
        changed = old is None or any(old.get(k) != v for k, v in rec.items() if k != "level")
        rec["first"] = old["first"] if old else now
        rec["last"] = now
        self.cells[key] = rec
        res["db_cells"] = len(self.cells)
        if changed or time.time() - self.saved > 600:
            self.save()


def _group(plmn):
    return RAN_GROUP.get(plmn, plmn)


def assess(res, cells):
    """Heurystyki IMSI-catchera -> [{level: warn|info, text}]. Pojedyncza "info" to nic
    niezwykłego; kilka "warn" naraz = komórka podejrzana."""
    out = []

    def add(level, text):
        out.append({"level": level, "text": text})

    plmn, lac, ci, arfcn = res.get("plmn"), res.get("lac"), res.get("ci"), res.get("arfcn")
    si = res.get("si", [])
    if plmn:
        if plmn[:3] in TEST_MCC:
            add("warn", f"testowy PLMN {plmn}")
        elif plmn[:3] == "260" and plmn not in OPERATORS:
            add("warn", f"nieznany operator {plmn}")
        elif plmn[:3] != "260":
            add("info", f"sieć zagraniczna {plmn}")
    elif si:
        add("warn", "brak SI3/SI4 (nie nadaje LAI)")
    if "si2" in si and not res.get("gsm_neighbours"):
        add("warn", "pusta lista sąsiadów (SI2)")
    elif si and "si2" not in si:
        add("warn", "brak SI2 (lista sąsiadów)")
    cro = (res.get("reselect") or {}).get("cro_db")
    if cro is not None and cro >= 20:
        add("warn", f"wysoki CRO {cro} dB — komórka wymusza wybór")
    if si and "gprs" not in res:
        add("info", "brak GPRS")
    if si and not res.get("umts_neighbours") and not res.get("lte_neighbours"):
        add("info", "brak sąsiadów 3G/4G (tylko 2G)")
    if lac == 0 or lac == 0xFFFE:
        add("warn", f"zarezerwowany LAC {lac}")

    if not plmn:
        return out
    me = f"{plmn}|{lac}|{ci}"
    bsic = (res.get("bsic") or {}).get("value")
    own = cells.get(me)
    if own and own.get("bsic") not in (None, bsic) and bsic is not None:
        add("warn", f"zmiana BSIC komórki CI {ci}: {own.get('bsic')} → {bsic}")
    # wzorzec sieci = komórki bez podejrzeń (catcher nie może "uprawomocnić" swojego LAC)
    same = [c for k, c in cells.items()
            if k != me and _group(c.get("plmn")) == _group(plmn) and c.get("warn", 0) < 2]
    if same:
        lacs = {c.get("lac") for c in same}
        if lac not in lacs:
            add("warn" if len(same) >= 3 else "info",
                f"LAC {lac} nie występuje w innych komórkach sieci (znane: {', '.join(map(str, sorted(lacs)))})")
        nbr = set()
        for c in same:
            nbr.update(c.get("nbrs") or [])
        if nbr and arfcn is not None and arfcn not in nbr:
            add("warn" if len(same) >= 3 else "info", f"ARFCN {arfcn} nie jest na listach sąsiadów innych komórek sieci")
        for c in same:
            # ten sam ARFCN i BSIC = ta sama stacja w zasięgu; inna tożsamość pod nimi = podmiana
            if c.get("arfcn") == arfcn and c.get("bsic") == bsic and c.get("plmn") == plmn \
                    and time.time() - c.get("last", 0) < 7 * 86400:
                add("warn", f"zmiana tożsamości na ARFCN {arfcn}: wcześniej CI {c.get('ci')} / LAC {c.get('lac')}")
                break
    return out


# ---------------------------------------------------------------- główna pętla
class Decoder:
    def __init__(self, args):
        self.args = args
        self.freq = None
        self.retune = threading.Event()
        self.lock = threading.Lock()
        self.cap = int(FS * args.snap)
        self.buf = np.zeros(self.cap, np.complex64)
        self.fill = 0
        self.sid = SiDecoder()
        try:
            self.gr = GrGsm()
        except ImportError as e:
            self.gr = None
            log(f"brak gr-gsm ({e}) - tylko FCCH/CFO (apt install gr-gsm)")
        self.shm = f"/dev/shm/owrx_gsm_{os.getpid()}.cf32"
        self.db = CellDb(args.db)
        self.read_ctl()

    def read_ctl(self):
        try:
            with open(self.args.ctl) as f:
                self.freq = int(float(f.read().strip()))
        except (OSError, ValueError, TypeError):
            pass

    def on_signal(self, *_):
        self.retune.set()

    def reader(self):
        """Ciągle drenuje stdin (OWRX pompuje 4 MB/s), trzyma ostatnią migawkę."""
        chunk = 5000
        raw = bytearray(chunk * 8)
        mv = memoryview(raw)
        stdin = sys.stdin.buffer
        while True:
            got = 0
            while got < len(raw):
                n = stdin.readinto(mv[got:])
                if not n:
                    os._exit(0)
                got += n
            s = np.frombuffer(raw, np.complex64)
            with self.lock:
                if self.fill < self.cap:
                    m = min(len(s), self.cap - self.fill)
                    self.buf[self.fill:self.fill + m] = s[:m]
                    self.fill += m

    def take_snapshot(self, settle=0.3):
        with self.lock:
            self.fill = 0
        time.sleep(settle)
        with self.lock:
            self.fill = 0
        while True:
            if self.retune.is_set():
                return None
            with self.lock:
                if self.fill >= self.cap:
                    return self.buf.copy()
            time.sleep(0.1)

    def analyze_once(self, snap=None):
        freq = self.freq
        if snap is None:
            snap = self.take_snapshot()
        if snap is None:
            return
        x = snap.astype(np.complex64)
        raster = int(round(freq / 2e5) * 2e5) if freq else None
        if raster and raster != freq:
            x = x * np.exp(-2j * np.pi * (raster - freq) * np.arange(len(x)) / FS).astype(np.complex64)
        f = raster or freq
        arfcn, band = arfcn_for(f) if f else (None, None)
        emit(type="status", state="analyzing", freq=f)
        t0 = time.time()
        cfo = fcch_offset(x[:FS].astype(np.complex128))
        res = dict(type="cell", freq=f, arfcn=arfcn, band=band, ts=time.time())
        if cfo is not None:
            res["cfo_hz"] = round(cfo)
            res["cfo_ppm"] = round(-cfo / f * 1e6, 2) if f else None
        if self.gr is None:
            res.update(found=cfo is not None, note="brak gr-gsm: tylko FCCH/CFO")
            emit(**res)
            return
        x.tofile(self.shm)
        try:
            bursts, msgs = self.gr.run(self.shm, float(f or 935e6), arfcn)
        finally:
            try:
                os.unlink(self.shm)
            except OSError:
                pass
        kinds = collections.Counter(b[0] for b in bursts)
        votes = collections.Counter()
        for k, _, bits in bursts:
            if k == 3 and len(bits) == 148:
                s = decode_sch(bits)
                if s:
                    votes[(s["ncc"], s["bcc"])] += 1
        lvl = [b[1] for b in bursts if b[0] in (1, 3)]
        res["bursts"] = {"fcch": kinds.get(1, 0), "sch": kinds.get(3, 0),
                         "normal": kinds.get(6, 0), "dummy": kinds.get(7, 0)}
        if lvl:
            res["level_db"] = int(np.median(lvl))
        if votes:
            (ncc, bcc), _ = votes.most_common(1)[0]
            res["bsic"] = {"ncc": ncc, "bcc": bcc, "value": 8 * ncc + bcc}
        res.update(summarize(msgs, self.sid, band, len(x) / FS))
        res["found"] = bool(votes or res.get("si"))
        if not res["found"]:
            res["reason"] = ("FCCH bez SCH/SI — za słaby sygnał?" if kinds.get(1) or cfo is not None
                             else "brak FCCH — to nie nośna BCCH (C0)")
        if res["found"]:
            self.db.update(res)
        res["took_s"] = round(time.time() - t0, 1)
        emit(**res)

    def run(self):
        signal.signal(signal.SIGUSR1, self.on_signal)
        threading.Thread(target=self.reader, daemon=True).start()
        emit(type="status", state="started", freq=self.freq, grgsm=self.gr is not None,
             pycrate=self.sid.cls is not None)
        while True:
            self.retune.clear()
            self.read_ctl()
            try:
                self.analyze_once()
            except Exception as e:
                log(f"błąd analizy: {e!r}")
                emit(type="status", state="error", error=str(e))
            if self.retune.wait(self.args.repeat):
                emit(type="status", state="retune")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctl", default="", help="plik z częstotliwością (Hz); SIGUSR1 = przeczytaj ponownie")
    ap.add_argument("--snap", type=float, default=6.0, help="długość migawki IQ [s]")
    ap.add_argument("--repeat", type=float, default=60.0, help="odstęp ponownej analizy [s]")
    ap.add_argument("--once", action="store_true", help="jedna analiza i koniec (testy z pliku)")
    ap.add_argument("--freq", type=float, help="częstotliwość [Hz] dla --once")
    ap.add_argument("--db", default="/var/lib/openwebrx/gsm_cells.json", help="baza znanych komórek (pusty = bez zapisu)")
    a = ap.parse_args()
    d = Decoder(a)
    if a.once:
        if a.freq:
            d.freq = int(a.freq)
        snap = np.frombuffer(sys.stdin.buffer.read(d.cap * 8), np.complex64)
        d.analyze_once(snap)
        return
    d.run()


if __name__ == "__main__":
    main()
