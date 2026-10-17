#!/usr/bin/env python3
"""LTE cell identification decoder for OpenWebRX+ (tryb "LTE").
Author: SP8MB

Wejście: stdin = complex float32 IQ @ 1,92 MS/s wycentrowane na klikniętej nośnej.
Wyjście: linie JSON na stderr (protocol "LTE") -> metaWriter -> panel LteMetaPanel.
Sterowanie: plik --ctl z częstotliwością (Hz) + SIGUSR1 = przestrojenie (nowa analiza).

Cykl analizy (co --repeat s albo po przestrojeniu):
  1. migawka --snap s IQ; dociągnięcie do rastra LTE 100 kHz (klik nie musi być dokładny)
  2. PSS (3 wzorce x hipotezy CFO) -> N_ID2, położenie, CFO; SSS -> N_ID1 => PCI
  3. pdsch_ue (srsRAN, tryb plikowy, PDSCH_ALL_SI=1) na migawce wyrównanej do podramki 0
     -> MIB (PRB, porty, PHICH, SFN) + bloki SI-RNTI po UDP
  4. pycrate: BCCH-DL-SCH -> SIB1 (PLMN, TAC, Cell ID, pasmo), SIB2/3/5/6/7 (sąsiedzi)
PSS/SSS/PBCH leżą w środkowych 6 PRB każdej komórki, więc PCI+MIB działa dla każdej
szerokości kanału; SIB-y (PDSCH w całym kanale) tylko dla komórek 1,4 MHz (6 PRB).
"""
import signal

# moduł może przysłać SIGUSR1 (przestrojenie) zanim skończą się importy numpy/scipy;
# domyślna akcja SIGUSR1 to zakończenie procesu. Częstotliwość i tak czytamy z --ctl.
signal.signal(signal.SIGUSR1, signal.SIG_IGN)

import argparse
import collections
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

import numpy as np
from scipy.signal import fftconvolve

FS = 1920000
N = 128                      # FFT dla 6 PRB
HALF_FRAME = 9600            # 5 ms
SC = np.r_[-31:0, 1:32]      # 62 podnośne PSS/SSS bez DC
PSS_TO_SF_START = 832        # PSS = ostatni symbol slotu 0: 960 - 128 próbek od początku podramki
SSS_BEFORE_PSS = 137         # 128 + CP 9

# 36.101 tab. 5.7.3-1: pasmo -> (F_DL_low MHz, N_offs_DL, N_min, N_max, duplex UL-DL MHz)
BANDS = {
    1: (2110, 0, 0, 599, -190), 3: (1805, 1200, 1200, 1949, -95), 7: (2620, 2750, 2750, 3449, -120),
    8: (925, 3450, 3450, 3799, -45), 20: (791, 6150, 6150, 6449, 41), 28: (758, 9210, 9210, 9659, -55),
    31: (462.5, 9870, 9870, 9919, -10), 32: (1452, 9920, 9920, 10359, None),
    38: (2570, 37750, 37750, 38249, 0), 40: (2300, 38650, 38650, 39649, 0),
    72: (461, 68986, 68986, 69035, -10), 73: (460, 69036, 69036, 69085, -10),
    87: (420, 69788, 69788, 69837, -10), 88: (422, 69838, 69838, 69887, -10),
}
BW_MHZ = {"n6": 1.4, "n15": 3, "n25": 5, "n50": 10, "n75": 15, "n100": 20,
          "mbw6": 1.4, "mbw15": 3, "mbw25": 5, "mbw50": 10, "mbw75": 15, "mbw100": 20}


def emit(**kw):
    kw["protocol"] = "LTE"
    sys.stderr.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def log(msg):
    sys.stderr.write(f"[lte] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- EARFCN / raster
def earfcn_info(n):
    for band, (flow, noffs, nmin, nmax, dup) in BANDS.items():
        if nmin <= n <= nmax:
            f = flow + 0.1 * (n - noffs)
            return {"earfcn": n, "band": band, "dl_mhz": round(f, 1)}
    return {"earfcn": n}


def earfcn_for(freq_hz, band):
    b = BANDS.get(band)
    if not b:
        return None
    n = round(b[1] + (freq_hz / 1e6 - b[0]) * 10)
    return n if b[2] <= n <= b[3] else None


def uarfcn_mhz(u):
    if 2937 <= u <= 3088:    # UMTS 900 (band VIII)
        return round(340 + u / 5, 1)
    return round(u / 5, 1)


def gsm_arfcn_mhz(n, band_ind):
    if 512 <= n <= 885 and band_ind == "dcs1800":
        return round(1805.2 + 0.2 * (n - 512), 1)
    if 0 <= n <= 124:
        return round(935.0 + 0.2 * n, 1)
    if 975 <= n <= 1023:
        return round(935.0 + 0.2 * (n - 1024), 1)
    return None


# ---------------------------------------------------------------- PSS / SSS
def pss_freq(nid2):
    u = (25, 29, 34)[nid2]
    n = np.arange(62)
    return np.where(n < 31, np.exp(-1j * np.pi * u * n * (n + 1) / 63),
                    np.exp(-1j * np.pi * u * (n + 1) * (n + 2) / 63))


def to_time(d):
    X = np.zeros(N, complex)
    X[SC % N] = d
    return np.fft.ifft(X)


def _m_seq(taps):
    x = [0, 0, 0, 0, 1]
    while len(x) < 31:
        i = len(x) - 5
        x.append(sum(x[i + t] for t in taps) % 2)
    return 1 - 2 * np.array(x)


_S, _C, _Z = _m_seq((2, 0)), _m_seq((3, 0)), _m_seq((4, 2, 1, 0))


def sss_freq(nid1, nid2, sub5):
    """36.211 6.11.2.1."""
    q_ = nid1 // 30
    q = (nid1 + q_ * (q_ + 1) // 2) // 30
    m_ = nid1 + q * (q + 1) // 2
    m0 = m_ % 31
    m1 = (m0 + m_ // 31 + 1) % 31
    n = np.arange(31)
    s0, s1 = _S[(n + m0) % 31], _S[(n + m1) % 31]
    c0, c1 = _C[(n + nid2) % 31], _C[(n + nid2 + 3) % 31]
    z0, z1 = _Z[(n + m0 % 8) % 31], _Z[(n + m1 % 8) % 31]
    d = np.empty(62)
    if not sub5:
        d[0::2], d[1::2] = s0 * c0, s1 * c1 * z0
    else:
        d[0::2], d[1::2] = s1 * c0, s0 * c1 * z1
    return d


_SSS_TABLE = {}


def sss_table(nid2):
    if nid2 not in _SSS_TABLE:
        keys = [(a, b) for a in range(168) for b in (False, True)]
        _SSS_TABLE[nid2] = (keys, np.array([sss_freq(a, nid2, b) for a, b in keys]))
    return _SSS_TABLE[nid2]


def find_cell(x, cfo_hyps=(-10000, -5000, 0, 5000, 10000)):
    """-> dict(pci, nid1, nid2, cfo, pss_score, sss_ratio, sf0_pos) albo None."""
    srch = x[: FS // 2]
    n = np.arange(N)
    scores = []
    for nid2 in range(3):
        t = to_time(pss_freq(nid2))
        for h in cfo_hyps:
            th = t * np.exp(2j * np.pi * h * n / FS)
            cor = np.abs(fftconvolve(srch, np.conj(th[::-1]), "valid"))
            L = len(cor) // HALF_FRAME * HALF_FRAME
            acc = cor[:L].reshape(-1, HALF_FRAME).sum(0)
            k = int(np.argmax(acc))
            scores.append((acc[k] / np.median(acc), nid2, h, k))
    scores.sort(reverse=True)
    best = scores[0]
    second = next((s for s in scores if s[1] != best[1]), (1.0,))
    score, nid2, hyp, k = best
    if score < 4.0 or score < 1.4 * second[0]:
        return {"found": False, "pss_score": round(float(score), 1)}

    t = to_time(pss_freq(nid2))
    starts = np.arange(k, len(x) - N, HALF_FRAME)
    tt = np.arange(len(x))
    xr = x * np.exp(-2j * np.pi * hyp * tt / FS)
    ph = 0
    for p in starts[:200]:
        seg = xr[p:p + N] * np.conj(t)
        ph += np.sum(seg[N // 2:]) * np.conj(np.sum(seg[:N // 2]))
    cfo = hyp + np.angle(ph) / (2 * np.pi * (N / 2) / FS)
    xc = x * np.exp(-2j * np.pi * cfo * tt / FS)

    keys, table = sss_table(nid2)
    pss = pss_freq(nid2)
    votes = collections.Counter()
    first_sf0 = None
    for p in starts[:200]:
        if p - SSS_BEFORE_PSS < 0:
            continue
        P = np.fft.fft(xc[p:p + N])[SC % N]
        S = np.fft.fft(xc[p - SSS_BEFORE_PSS:p - SSS_BEFORE_PSS + N])[SC % N]
        h = P / pss
        e = S / np.where(np.abs(h) > 1e-12, h, 1e-12)
        sc = np.real(table @ e)
        key = keys[int(np.argmax(sc))]
        votes[key[0]] += 1
        if first_sf0 is None and not key[1] and p - PSS_TO_SF_START >= 0:
            first_sf0 = p
    if not votes:
        return {"found": False, "pss_score": round(float(score), 1)}
    nid1, nv = votes.most_common(1)[0]
    ratio = nv / sum(votes.values())
    if ratio < 0.5:
        return {"found": False, "pss_score": round(float(score), 1), "sss_ratio": round(ratio, 2)}
    if first_sf0 is None:
        first_sf0 = int(starts[0]) if starts[0] >= PSS_TO_SF_START else int(starts[1])
    return {"found": True, "pci": 3 * nid1 + nid2, "nid1": nid1, "nid2": nid2,
            "cfo": float(cfo), "pss_score": round(float(score), 1), "sss_ratio": round(ratio, 2),
            "sf0": int(first_sf0 - PSS_TO_SF_START), "x": xc}


def snr_estimate(x):
    """SNR komórki 1,4 MHz: PSD w paśmie zajętym vs w odstępie ochronnym 570–680 kHz."""
    seg = x[: FS // 4]
    nf = 4096
    w = np.hanning(nf)
    psd = np.zeros(nf)
    cnt = 0
    for i in range(0, len(seg) - nf, nf):
        psd += np.abs(np.fft.fft(seg[i:i + nf] * w)) ** 2
        cnt += 1
    psd = np.fft.fftshift(psd / max(cnt, 1))
    f = (np.arange(nf) - nf / 2) * FS / nf
    sig = psd[(np.abs(f) > 20e3) & (np.abs(f) < 500e3)].mean()
    noise = psd[(np.abs(f) > 570e3) & (np.abs(f) < 680e3)].mean()
    return round(float(10 * np.log10(max(sig / noise - 1, 1e-3))), 1)


# ---------------------------------------------------------------- SIB (pycrate)
def _bits(v):
    return v[0] if isinstance(v, tuple) else v


def _find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        obj = list(obj.values())
    if isinstance(obj, (list, tuple)):
        for v in obj:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def _plmn(p):
    return "".join(map(str, p.get("mcc", []))) + "-" + "".join(map(str, p["mnc"]))


class SibDecoder:
    def __init__(self):
        try:
            from pycrate_asn1dir import RRCLTE
            self.msg = RRCLTE.EUTRA_RRC_Definitions.BCCH_DL_SCH_Message
        except ImportError:
            self.msg = None
            log("brak pycrate - SIB-y nie będą dekodowane (pip install pycrate)")

    def decode(self, buf):
        if self.msg is None:
            return {}
        try:
            self.msg.from_uper(buf)
            kind, body = self.msg.get_val()["message"][1]
        except Exception:
            return {}
        if kind == "systemInformationBlockType1":
            return {"sib1": body}
        try:
            return dict(body["criticalExtensions"][1]["sib-TypeAndInfo"])
        except Exception:
            return {}


def summarize(sibs, freq_hz):
    out = {}
    s1 = sibs.get("sib1")
    if s1:
        acc = s1["cellAccessRelatedInfo"]
        cid = _bits(acc["cellIdentity"])
        band = _find(s1, "freqBandIndicator-v9e0") or s1["freqBandIndicator"]
        out["sib1"] = {
            "plmn": [_plmn(x["plmn-Identity"]) for x in acc["plmn-IdentityList"]],
            "reserved": any(x["cellReservedForOperatorUse"] == "reserved" for x in acc["plmn-IdentityList"]),
            "tac": _bits(acc["trackingAreaCode"]),
            "cell_id": cid, "enb_id": cid >> 8, "sector": cid & 0xFF,
            "barred": acc["cellBarred"] == "barred",
            "csg": bool(acc.get("csg-Indication")),
            "q_rxlevmin": s1["cellSelectionInfo"]["q-RxLevMin"] * 2,
            "band": band,
            "earfcn": earfcn_for(freq_hz, band) if freq_hz else None,
            "tdd": "tdd-Config" in s1,
            "sibs": sorted({s for x in s1["schedulingInfoList"] for s in x["sib-MappingInfo"]}),
        }
    s2 = sibs.get("sib2")
    if s2:
        fi = s2.get("freqInfo", {})
        rc = s2.get("radioResourceConfigCommon", {})
        d = {"ref_signal_power": rc.get("pdsch-ConfigCommon", {}).get("referenceSignalPower"),
             "ac_barring": "ac-BarringInfo" in s2}
        if "ul-CarrierFreq" in fi:
            d["ul"] = earfcn_info(fi["ul-CarrierFreq"])
        if "ul-Bandwidth" in fi:
            d["ul_bw_mhz"] = BW_MHZ.get(fi["ul-Bandwidth"])
        out["sib2"] = d
    s3 = sibs.get("sib3")
    if s3:
        out["serving_priority"] = _find(s3, "cellReselectionPriority")
    s5 = sibs.get("sib5")
    if s5:
        out["lte_neighbours"] = [
            dict(earfcn_info(f["dl-CarrierFreq"]), bw_mhz=BW_MHZ.get(f.get("allowedMeasBandwidth")),
                 priority=f.get("cellReselectionPriority"))
            for f in s5.get("interFreqCarrierFreqList", [])]
    s6 = sibs.get("sib6")
    if s6:
        out["umts_neighbours"] = [
            {"uarfcn": f["carrierFreq"], "dl_mhz": uarfcn_mhz(f["carrierFreq"]),
             "priority": f.get("cellReselectionPriority")}
            for f in s6.get("carrierFreqListUTRA-FDD", [])]
    s7 = sibs.get("sib7")
    if s7:
        g = []
        for info in s7.get("carrierFreqsInfoList", []):
            cf = info["carrierFreqs"]
            ar = [cf["startingARFCN"]]
            kind, val = cf["followingARFCNs"]
            if kind == "explicitListOfARFCNs":
                ar += list(val)
            elif kind == "equallySpacedARFCNs":
                ar += [cf["startingARFCN"] + val["arfcn-Spacing"] * (i + 1)
                       for i in range(val["numberOfFollowingARFCNs"])]
            ar = sorted(set(ar))
            mhz = [m for m in (gsm_arfcn_mhz(a, cf["bandIndicator"]) for a in ar) if m]
            g.append({"arfcns": ar, "band": "GSM 900" if mhz and mhz[0] < 1000 else "GSM 1800",
                      "dl_mhz": [min(mhz), max(mhz)] if mhz else None,
                      "priority": info.get("commonInfo", {}).get("cellReselectionPriority")})
        out["gsm_neighbours"] = g
    return out


# ---------------------------------------------------------------- pdsch_ue
MIB_RE = {
    "prb": re.compile(r"- PRB:\s+(\d+)"),
    "ports": re.compile(r"- Nof ports:\s+(\d+)"),
    "phich_len": re.compile(r"- PHICH Length:\s+(\w+)"),
    "phich_res": re.compile(r"- PHICH Resources:\s+([\d/]+)"),
    "sfn": re.compile(r"Decoded MIB\. SFN:\s+(\d+)"),
}


def run_pdsch(pdsch_bin, path, pci, ports, nsub, sib_dec, timeout=40):
    """-> (mib dict | None, {sib: najczęstsza treść})"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.2)
    port = sock.getsockname()[1]
    got = collections.defaultdict(collections.Counter)
    raw = {}
    done = threading.Event()

    def rx():
        while not done.is_set():
            try:
                buf = sock.recv(4096)
            except socket.timeout:
                continue
            for k, v in sib_dec.decode(buf).items():
                key = json.dumps(v, default=str, sort_keys=True)
                got[k][key] += 1
                raw[(k, key)] = v

    th = threading.Thread(target=rx, daemon=True)
    th.start()
    env = dict(os.environ, PDSCH_ALL_SI="1")
    cmd = [pdsch_bin, "-i", path, "-p", "6", "-c", str(pci), "-P", str(ports), "-d",
           "-n", str(nsub), "-u", str(port), "-U", "127.0.0.1"]
    try:
        r = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        out = r.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
    time.sleep(0.3)
    done.set()
    th.join(1)
    sock.close()
    mib = None
    if "Decoded MIB" in out:
        mib = {}
        for k, rgx in MIB_RE.items():
            m = rgx.search(out)
            if m:
                mib[k] = m.group(1)
        for k in ("prb", "ports", "sfn"):
            if k in mib:
                mib[k] = int(mib[k])
    sibs = {k: raw[(k, c.most_common(1)[0][0])] for k, c in got.items()}
    return mib, sibs


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
        self.sib_dec = SibDecoder()
        self.shm = f"/dev/shm/owrx_lte_{os.getpid()}.cf32"
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
        """Ciągle drenuje stdin (OWRX pompuje 15 MB/s), trzyma ostatnią migawkę."""
        chunk = 19200
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
        raster = None
        if snap is None:
            snap = self.take_snapshot()
        if snap is None:
            return
        x = snap.astype(np.complex128)
        shift = 0
        if freq:
            raster = int(round(freq / 1e5) * 1e5)
            shift = raster - freq
            if shift:
                x *= np.exp(-2j * np.pi * shift * np.arange(len(x)) / FS)
        emit(type="status", state="analyzing", freq=raster or freq)
        t0 = time.time()
        cell = find_cell(x)
        if not cell.get("found"):
            emit(type="cell", found=False, freq=raster or freq, pss_score=cell.get("pss_score"),
                 ts=time.time())
            return
        xc = cell.pop("x")
        cfo = cell["cfo"]
        res = dict(type="cell", found=True, freq=raster or freq, ts=time.time(),
                   pci=cell["pci"], nid1=cell["nid1"], nid2=cell["nid2"],
                   cfo_hz=round(cfo), cfo_ppm=round(-cfo / (raster or freq) * 1e6, 2) if (raster or freq) else None,
                   pss_score=cell["pss_score"], sss_ratio=cell["sss_ratio"])
        emit(**dict(res, partial=True))

        mib = None
        sibs = {}
        if self.args.pdsch and os.path.isfile(self.args.pdsch):
            for off in (cell["sf0"], cell["sf0"] + HALF_FRAME):
                seg = xc[off:].astype(np.complex64)
                seg.tofile(self.shm)
                nsub = len(seg) // 1920 - 2
                for ports in (2, 1, 4):
                    mib, sibs = run_pdsch(self.args.pdsch, self.shm, cell["pci"], ports, nsub, self.sib_dec)
                    if mib:
                        break
                if mib:
                    break
            try:
                os.unlink(self.shm)
            except OSError:
                pass
        if mib:
            res["mib"] = mib
            res["bw_mhz"] = BW_MHZ.get(f"n{mib.get('prb')}")
            if mib.get("prb") == 6:
                res["snr_db"] = snr_estimate(xc)
            else:
                res["note"] = "SIB tylko dla komórek 1,4 MHz (IF 1,92 MS/s)"
        res.update(summarize(sibs, raster or freq))
        res["took_s"] = round(time.time() - t0, 1)
        emit(**res)

    def run(self):
        signal.signal(signal.SIGUSR1, self.on_signal)
        threading.Thread(target=self.reader, daemon=True).start()
        emit(type="status", state="started", freq=self.freq, pdsch=bool(self.args.pdsch and os.path.isfile(self.args.pdsch)),
             pycrate=self.sib_dec.msg is not None)
        while True:
            self.retune.clear()
            self.read_ctl()
            try:
                self.analyze_once()
            except Exception as e:
                log(f"błąd analizy: {e!r}")
                emit(type="status", state="error", error=str(e))
            # czekaj na przestrojenie albo kolejny cykl
            if self.retune.wait(self.args.repeat):
                emit(type="status", state="retune")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctl", default="", help="plik z częstotliwością (Hz); SIGUSR1 = przeczytaj ponownie")
    ap.add_argument("--pdsch", default="/opt/openwebrx-tetra/bin/pdsch_ue")
    ap.add_argument("--snap", type=float, default=3.0, help="długość migawki IQ [s]")
    ap.add_argument("--repeat", type=float, default=60.0, help="odstęp ponownej analizy [s]")
    ap.add_argument("--once", action="store_true", help="jedna analiza i koniec (testy z pliku)")
    ap.add_argument("--freq", type=float, help="częstotliwość [Hz] dla --once")
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
