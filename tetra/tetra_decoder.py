#!/usr/bin/env python3
"""TETRA decoder wrapper for OpenWebRX+.
Author: SP8MB

Reads complex float IQ from stdin (36 kS/s, centered on TETRA carrier).
Writes PCM audio to stdout (8 kHz, 16-bit signed LE, mono).
Writes JSON metadata to stderr (TETMON signaling: network info, calls, etc.).

Pipeline:
  stdin IQ -> GNURadio DQPSK demod -> tetra-rx -> UDP TETMON -> ACELP codec -> stdout PCM
                                                             -> JSON meta -> stderr
"""

import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

TETRA_DIR = os.path.dirname(os.path.abspath(__file__))

# Audio constants (from TETRA ACELP codec)
ACELP_FRAME_SIZE = 1380   # 2 speech frames, 690 int16 values
PCM_OUTPUT_BYTES = 960     # 480 samples x 2 bytes (2 frames x 240 samples)

# osmo-tetra-sq5bpf writes header "TRA:XX RX:XX\0" (13 bytes) followed by 1380 bytes ACELP.
# Some older forks emit "TRA:XX RX:XX DECR:X\0" (20 bytes). Match both; locate body via NUL.
AUDIO_PATTERN = re.compile(
    rb"TRA:([0-9a-fA-F]+)\s+RX:([0-9a-fA-F]+)(?:\s+DECR:([0-9a-fA-F]+))?\x00"
)

# DRELEASEDEC payload contains optional "[reason text]" between NID and RX
RELEASE_REASON_PATTERN = re.compile(rb'\[([^\]]+)\]')
# SDSDEC payload: FUNC:SDSDEC [description with content] RX:N
SDS_DESC_PATTERN = re.compile(rb'FUNC:SDSDEC\s+\[(.+)\]\s+RX:', re.DOTALL)

# Generic TETMON key:value parser
def parse_tetmon_fields(data):
    """Parse TETMON 'KEY:VALUE KEY:VALUE ...' into dict."""
    fields = {}
    for m in re.finditer(rb'([A-Z_]+):([^\s]+)', data):
        fields[m.group(1).decode()] = m.group(2).decode()
    return fields


class CodecPipeline:
    """Persistent cdecoder|sdecoder subprocess pipeline."""

    def __init__(self):
        self._cdecoder = None
        self._sdecoder = None
        self._lock = threading.Lock()
        self._started = False

    def start(self):
        cdecoder_path = os.path.join(TETRA_DIR, 'cdecoder')
        sdecoder_path = os.path.join(TETRA_DIR, 'sdecoder')

        if not os.path.isfile(cdecoder_path) or not os.path.isfile(sdecoder_path):
            for p in ['/tetra/bin', '/usr/local/bin']:
                if os.path.isfile(os.path.join(p, 'cdecoder')):
                    cdecoder_path = os.path.join(p, 'cdecoder')
                    sdecoder_path = os.path.join(p, 'sdecoder')
                    break

        pipe_r, pipe_w = os.pipe()

        self._cdecoder = subprocess.Popen(
            [cdecoder_path, '/dev/stdin', '/dev/stdout'],
            stdin=subprocess.PIPE, stdout=pipe_w, stderr=subprocess.DEVNULL
        )
        os.close(pipe_w)

        self._sdecoder = subprocess.Popen(
            [sdecoder_path, '/dev/stdin', '/dev/stdout'],
            stdin=pipe_r, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        os.close(pipe_r)

        self._started = True

    def decode(self, acelp_data):
        """Decode ACELP frame to PCM. Returns bytes or None."""
        with self._lock:
            if not self._started:
                try:
                    self.start()
                except Exception:
                    return None

            try:
                if (self._cdecoder.poll() is not None or
                        self._sdecoder.poll() is not None):
                    self.stop()
                    self.start()

                self._cdecoder.stdin.write(acelp_data)
                self._cdecoder.stdin.flush()
                pcm = self._sdecoder.stdout.read(PCM_OUTPUT_BYTES)
                if pcm and len(pcm) == PCM_OUTPUT_BYTES:
                    return pcm
            except (BrokenPipeError, OSError):
                self.stop()
            return None

    def stop(self):
        self._started = False
        for proc in (self._cdecoder, self._sdecoder):
            if proc:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
        self._cdecoder = None
        self._sdecoder = None


def find_free_port():
    """Find a free UDP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def parse_audio_from_udp(data):
    """Extract ACELP audio data from a TETMON UDP packet.

    Returns ACELP bytes (1380) or None.

    osmo-tetra packs the packet as: sprintf(tmp,"TRA:XX RX:XX\\0"); memcpy(tmp+13, acelp, 1380).
    Header is the matched part including the NUL terminator; ACELP body starts at match.end().
    """
    tra_pos = data.find(b'TRA:')
    if tra_pos < 0:
        return None

    payload = data[tra_pos:]
    match = AUDIO_PATTERN.match(payload)
    if not match:
        return None

    body_start = match.end()
    if len(payload) < body_start + ACELP_FRAME_SIZE:
        return None

    return payload[body_start:body_start + ACELP_FRAME_SIZE]


def parse_metadata_from_udp(data):
    """Extract metadata from TETMON UDP packet. Returns dict or None."""
    # Find TETMON_begin marker
    begin = data.find(b'TETMON_begin')
    if begin < 0:
        func_pos = data.find(b'FUNC:')
        if func_pos < 0:
            return None
        payload = data[func_pos:]
    else:
        end = data.find(b'TETMON_end', begin)
        if end < 0:
            payload = data[begin + len(b'TETMON_begin'):]
        else:
            payload = data[begin + len(b'TETMON_begin'):end]
    payload = payload.strip()

    fields = parse_tetmon_fields(payload)
    func = fields.get('FUNC', '')
    # Handle multi-word FUNC names like "D-TX GRANTED", "D-CONNECT ACK"
    # The generic parser catches FUNC:D-TX, but "GRANTED"/"ACK" are lost
    # Try to reconstruct from raw payload
    func_match = re.search(rb'FUNC:(\S+(?:\s+(?!SSI:|IDX:|IDT:|ENCR:|RX:|CID:|NID:|CCODE:|MCC:|MNC:)\S+)*)', payload)
    if func_match:
        func = func_match.group(1).decode()

    if func == 'NETINFO1':
        mcc_raw = fields.get('MCC', '0')
        mnc_raw = fields.get('MNC', '0')
        try:
            mcc = int(mcc_raw, 16)
            mnc = int(mnc_raw, 16)
        except ValueError:
            mcc = int(mcc_raw) if mcc_raw.isdigit() else 0
            mnc = int(mnc_raw) if mnc_raw.isdigit() else 0
        ccode_raw = fields.get('CCODE', '0')
        try:
            color_code = int(ccode_raw, 16)
        except ValueError:
            color_code = int(ccode_raw) if ccode_raw.isdigit() else 0
        # tetra-rx CRYPT values: 0=unknown, 1=disabled(clear), 2=enabled
        crypt = int(fields.get('CRYPT', '0'))
        return {
            "protocol": "TETRA",
            "type": "netinfo",
            "mcc": mcc,
            "mnc": mnc,
            "dl_freq": int(fields.get('DLF', '0')),
            "ul_freq": int(fields.get('ULF', '0')),
            "color_code": color_code,
            "encrypted": crypt == 2,
            "crypt": crypt,
            "la": fields.get('LA', ''),
        }

    if func == 'FREQINFO1':
        return {
            "protocol": "TETRA",
            "type": "freqinfo",
            "dl_freq": int(fields.get('DLF', '0')),
            "ul_freq": int(fields.get('ULF', '0')),
        }

    # FREQINFO2 = neighbour cell frequency (one event per NCI from D-NWRK-BROADCAST)
    if func == 'FREQINFO2':
        return {
            "protocol": "TETRA",
            "type": "neighbour_freq",
            "dl_freq": int(fields.get('DLF', '0')),
        }

    if func == 'DSETUPDEC':
        return {
            "protocol": "TETRA",
            "type": "call_setup",
            "ssi": int(fields.get('SSI', '0')),
            "ssi2": int(fields.get('SSI2', '0')),
            "call_id": int(fields.get('CID', '0')),
            "idx": int(fields.get('IDX', '0')),
            "nid": int(fields.get('NID', '0')),
        }

    if func in ('DRELEASEDEC', 'D-RELEASE'):
        result = {
            "protocol": "TETRA",
            "type": "call_release",
            "ssi": int(fields.get('SSI', '0')),
            "call_id": int(fields.get('CID', '0')),
            "nid": int(fields.get('NID', '0')),
        }
        m = RELEASE_REASON_PATTERN.search(payload)
        if m:
            result["reason"] = m.group(1).decode(errors='replace').strip()
        return result

    if func == 'DCONNECTDEC':
        result = {
            "protocol": "TETRA",
            "type": "call_connect",
            "ssi": int(fields.get('SSI', '0')),
            "call_id": int(fields.get('CID', '0')),
            "call_ownership": int(fields.get('CALLOWN', '0')),
            "idx": int(fields.get('IDX', '0')),
        }
        if 'SSI2' in fields:
            result["ssi2"] = int(fields['SSI2'])
        return result

    if func == 'DTXGRANTDEC':
        result = {
            "protocol": "TETRA",
            "type": "tx_grant",
            "ssi": int(fields.get('SSI', '0')),
            "call_id": int(fields.get('CID', '0')),
            "idx": int(fields.get('IDX', '0')),
            "tx_grant": int(fields.get('TXGRANT', '0')),
            "tx_perm": int(fields.get('TXPERM', '0')),
            "enc_control": int(fields.get('ENCC', '0')),
        }
        if 'SSI2' in fields:
            result["ssi2"] = int(fields['SSI2'])
        return result

    if func == 'ENCINFO1':
        # tetra-rx CRYPT values: 0=unknown, 1=disabled(clear), 2=enabled
        crypt = int(fields.get('CRYPT', '0'))
        return {
            "protocol": "TETRA",
            "type": "encinfo",
            "encrypted": crypt == 2,
            "crypt": crypt,
            "enc_mode": fields.get('ENC', '00'),
        }

    if func == 'DSTATUSDEC':
        return {
            "protocol": "TETRA",
            "type": "status",
            "ssi": int(fields.get('SSI', '0')),
            "ssi2": int(fields.get('SSI2', '0')),
            "status": fields.get('STATUS', ''),
        }

    if func == 'BURST':
        return {
            "protocol": "TETRA",
            "type": "burst",
        }

    if func == 'SDSDEC':
        # SDSDEC has no SSI fields — content is inside [description]
        result = {
            "protocol": "TETRA",
            "type": "sds",
        }
        m = SDS_DESC_PATTERN.search(payload)
        if m:
            descr = m.group(1).decode(errors='replace').strip()
            result["descr"] = descr
            sm = re.search(r'STATUS:0x([0-9a-fA-F]+)', descr)
            if sm:
                result["status_code"] = int(sm.group(1), 16)
            # Protocol identifier (SDS PDU)
            pm = re.search(r'(?:PROT|PI|ProtocolIdent)[:\s=]+(\d+)', descr, re.I)
            if pm:
                result["protocol_ident"] = int(pm.group(1))
            # Delivery status (SDS-REPORT)
            dm = re.search(r'(?:DELIVERY[_ ]?STATUS|DeliveryStatus|delivery_status)[:\s=]+(\d+)', descr, re.I)
            if dm:
                result["delivery_status"] = int(dm.group(1))
            # SDS short text (simple text PDU often has TEXT:"..." or quoted)
            tm = re.search(r'TEXT[:\s=]+"([^"]+)"', descr)
            if tm:
                result["text"] = tm.group(1)
            # Source/dest SSI for SDS
            ssm = re.search(r'(?:CallingSSI|SSI):\s*(\d+)', descr)
            if ssm:
                result["src_ssi"] = int(ssm.group(1))
            dsm = re.search(r'(?:CalledSSI|DestSSI|DSSI):\s*(\d+)', descr)
            if dsm:
                result["dest_ssi"] = int(dsm.group(1))
        return result

    # Generic CMCE PDU with RESOURCE address (IDT = address type)
    # These carry SSI from MAC RESOURCE header (= GSSI for group calls)
    # SSI2 (if present) = ISSI of individual subscriber
    if func.startswith('D-') and 'IDT' in fields:
        ssi = int(fields.get('SSI', '0'))
        ssi2 = int(fields.get('SSI2', '0')) if 'SSI2' in fields else 0
        if ssi > 0 or ssi2 > 0:
            result = {
                "protocol": "TETRA",
                "type": "resource",
                "func": func,
                "ssi": ssi,
                "idt": int(fields.get('IDT', '0')),
            }
            if ssi2 > 0:
                result["ssi2"] = ssi2
            return result

    return None


# Regex patterns for tetra-rx stdout parsing
STDOUT_SYNC_PATTERN = re.compile(
    r'TMB-SAP SYNC CC \S+\(\S+\) TN (\d+)\((\d+)\) FN \S+\(\s*(\d+)\)'
)
STDOUT_ACCESS_PATTERN = re.compile(
    r'ACCESS-ASSIGN PDU:.*?DL_USAGE:\s*(\S+(?:\s+\S+)?)\s+UL_USAGE:\s*(\S+(?:\s+\S+)?)'
)
STDOUT_RESOURCE_PATTERN = re.compile(
    r'RESOURCE\s+Encr=(\d+).*?Addr=(\w+)\((\d+)\)'
)
STDOUT_TRAFFIC_PATTERN = re.compile(
    r'Traffic TMV-UNITDATA.*?(\d+)/(\d+)/(\d+)/(\d+)'
)

# NCI:[cell_id:N cell_resel:N neigh_synced:N cell_load:N carrier:N NNNNHz
NCI_PATTERN = re.compile(
    r'NCI:\[cell_id:(\d+)\s+cell_resel:(\d+)\s+neigh_synced:(\d+)\s+'
    r'cell_load:(\d+)\s+carrier:(\d+)\s+(\d+)Hz'
)
# D_NWRK_BROADCAST:[ cell_reselect:0xXXXX cell_load:N
NWRK_PATTERN = re.compile(r'D_NWRK_BROADCAST:\[\s*cell_reselect:0x([0-9a-fA-F]+)\s+cell_load:(\d+)')
# time[secs:N offset:±Nmin year:N
TETRA_TIME_PATTERN = re.compile(r'time\[secs:(\d+)\s+offset:([+-])(\d+)min\s+year:(\d+)')
# BNCH SYSINFO (DL N Hz, UL N Hz), service_details 0xXXXX [optional LA:N] [CCK ID N | Hyperframe N]
BNCH_PATTERN = re.compile(
    r'BNCH SYSINFO \(DL\s+(\d+)\s+Hz,\s+UL\s+(\d+)\s+Hz\),\s+'
    r'service_details\s+0x([0-9a-fA-F]+)(?:\s+LA:(\d+))?'
)
BNCH_CCK_PATTERN = re.compile(r'CCK ID\s+(\d+)')
BNCH_HYPERFRAME_PATTERN = re.compile(r'Hyperframe\s+(\d+)')
# Call identifier:N  Call timeout:N  Hookmethod:N  Duplex:N
CALL_PARAMS_PATTERN = re.compile(
    r'Call identifier:(\d+)\s+Call timeout:(\d+)\s+Hookmethod:(\d+)\s+Duplex:(\d+)'
)
# NotificationID:N  Tempaddr:N  CPTI:N  CallingSSI:N  CallingExt:N
CALLER_PATTERN = re.compile(
    r'NotificationID:(\d+)\s+Tempaddr:(\d+)\s+CPTI:(\d+)\s+CallingSSI:(\d+)\s+CallingExt:(\d+)'
)
# RESOURCE Encr=N ... Addr=SSI(N)
RESOURCE_SSI_PATTERN = re.compile(r'RESOURCE\s+Encr=(\d+)[^\n]*?Addr=SSI\((\d+)\)')

# MM PDUs (Mobility Management — radio registration/auth) — from tetra-rx stdout
# D-LOCATION UPDATE ACCEPT: type:N addr_type:N SSI:N subscr_class:0xXXXX
MM_LOC_UPDATE_ACCEPT_PATTERN = re.compile(
    r'D-LOCATION UPDATE ACCEPT:\s+type:(\d+)\s+addr_type:(\d+)\s+SSI:(\d+)'
    r'(?:\s+subscr_class:0x([0-9a-fA-F]+))?'
)
# D-ATTACH/DETACH GROUP: attach/detach:N report:N type:N GSSI:N
MM_ATTACH_GROUP_PATTERN = re.compile(
    r'D-ATTACH/DETACH GROUP:\s+attach/detach:(\d+)\s+report:(\d+)\s+type:(\d+)\s+GSSI:(\d+)'
)

# Update types per ETSI EN 300 392-2 §16.10.27
MM_UPDATE_TYPE_NAMES = {
    0: 'Roaming location updating',
    1: 'Migrating location updating',
    2: 'Periodic location updating',
    3: 'ITSI attach',
    4: 'Call restoration roaming',
    5: 'Call restoration migrating',
    6: 'Demand location updating',
    7: 'Disabled MS updating',
}


_DIAG_PATH = '/tmp/tetra_diag.log'
_DIAG_STATE = {
    'counts': {},          # type -> int
    'first_sample': {},    # type -> JSON line
    'last_sample': {},     # type -> JSON line
    'last_dump': 0.0,
    'interval': 10.0,      # dump snapshot every N seconds
    'stdout_counters': {}, # keyword -> int (raw stdout matches)
    'stdout_samples': {},  # keyword -> sample line
    # ssi (int) -> {first_iso, last_iso, count, encr, sources: set of strings}
    'ssi_history': {},
    'gssi_history': {},    # gssi -> {first_iso, last_iso, count}
}


def _diag_track_ssi(ssi, encr=None, source='?'):
    """Track first/last seen times for an SSI."""
    if not ssi or ssi == 0xFFFFFF:
        return
    h = _DIAG_STATE['ssi_history']
    now_iso = time.strftime('%H:%M:%S')
    if ssi not in h:
        h[ssi] = {'first': now_iso, 'last': now_iso, 'count': 1,
                  'encr': encr, 'sources': {source}}
    else:
        e = h[ssi]
        e['last'] = now_iso
        e['count'] += 1
        if encr is not None:
            e['encr'] = encr
        e['sources'].add(source)


def _diag_track_gssi(gssi, source='?'):
    if not gssi:
        return
    h = _DIAG_STATE['gssi_history']
    now_iso = time.strftime('%H:%M:%S')
    if gssi not in h:
        h[gssi] = {'first': now_iso, 'last': now_iso, 'count': 1, 'sources': {source}}
    else:
        e = h[gssi]
        e['last'] = now_iso
        e['count'] += 1
        e['sources'].add(source)


def _diag_stdout_count(line):
    """Count occurrences of interesting keywords in raw tetra-rx stdout."""
    s = _DIAG_STATE
    for k in ('D_NWRK_BROADCAST', 'NCI:', 'BNCH SYSINFO', 'Basicinfo',
              'ACCESS-ASSIGN', 'D-NWRK', 'TL-SDU', 'CMCE', 'D-SETUP',
              'D-RELEASE', 'D-CONNECT', 'D-TX', 'SDS', 'MM',
              'Call identifier', 'Call timeout', 'NotificationID',
              'RESOURCE', 'Encr=', 'Addr=', 'NCH',
              'BSI', 'BNCH', 'TMV-UNITDATA', 'Hyperframe',
              'cck_id', 'CCK ID'):
        if k in line:
            s['stdout_counters'][k] = s['stdout_counters'].get(k, 0) + 1
            if k not in s['stdout_samples']:
                s['stdout_samples'][k] = line.rstrip()[:400]


def _diag_record(meta_dict, line):
    t = meta_dict.get('type', '?')
    s = _DIAG_STATE
    s['counts'][t] = s['counts'].get(t, 0) + 1
    if t not in s['first_sample']:
        s['first_sample'][t] = line.rstrip()
    s['last_sample'][t] = line.rstrip()
    # Track SSI/GSSI history from any meta event that carries identities
    if t == 'active_ssi':
        for r in meta_dict.get('ssis', []):
            _diag_track_ssi(r.get('ssi'), r.get('encr'), 'active_ssi')
    elif t == 'call_setup':
        _diag_track_gssi(meta_dict.get('ssi'), 'call_setup')
        if meta_dict.get('calling_ssi'):
            _diag_track_ssi(meta_dict['calling_ssi'], source='calling_ssi')
        if meta_dict.get('ssi2'):
            _diag_track_ssi(meta_dict['ssi2'], source='ssi2')
    elif t == 'call_release':
        _diag_track_gssi(meta_dict.get('ssi'), 'call_release')
    elif t == 'tx_grant':
        _diag_track_gssi(meta_dict.get('ssi'), 'tx_grant')
        if meta_dict.get('calling_ssi'):
            _diag_track_ssi(meta_dict['calling_ssi'], source='tx_grant')
    elif t == 'ms_register':
        if meta_dict.get('ssi'):
            _diag_track_ssi(meta_dict['ssi'], source='ms_register:' + meta_dict.get('action', ''))
        if meta_dict.get('gssi'):
            _diag_track_gssi(meta_dict['gssi'], 'ms_register:' + meta_dict.get('action', ''))
    now = time.monotonic()
    if now - s['last_dump'] >= s['interval']:
        s['last_dump'] = now
        try:
            with open(_DIAG_PATH, 'w') as f:
                f.write('# TETRA diag — types seen since process start\n')
                f.write('# generated: ' + time.strftime('%Y-%m-%d %H:%M:%S') + '\n\n')
                f.write('## EMITTED EVENTS (meta JSON sent to OpenWebRX)\n\n')
                for tt in sorted(s['counts'].keys()):
                    f.write('=== {} (count={}) ===\n'.format(tt, s['counts'][tt]))
                    f.write('first: ' + s['first_sample'][tt] + '\n')
                    f.write(' last: ' + s['last_sample'][tt] + '\n\n')
                f.write('\n## SSI HISTORY (every ISSI ever seen in this run)\n\n')
                if not s['ssi_history']:
                    f.write('(no ISSI tracked yet)\n')
                else:
                    f.write('# {:>10}  {:>8}  {:>8}  {:>5}  {:>4}  sources\n'.format(
                        'SSI', 'first', 'last', 'count', 'encr'))
                    for ssi in sorted(s['ssi_history'].keys()):
                        e = s['ssi_history'][ssi]
                        f.write('  {:>10}  {:>8}  {:>8}  {:>5}  {:>4}  {}\n'.format(
                            ssi, e['first'], e['last'], e['count'],
                            e['encr'] if e['encr'] is not None else '-',
                            ','.join(sorted(e['sources']))))

                f.write('\n## GSSI HISTORY (every group seen)\n\n')
                if not s['gssi_history']:
                    f.write('(no GSSI tracked yet)\n')
                else:
                    f.write('# {:>10}  {:>8}  {:>8}  {:>5}  sources\n'.format(
                        'GSSI', 'first', 'last', 'count'))
                    for gssi in sorted(s['gssi_history'].keys()):
                        e = s['gssi_history'][gssi]
                        f.write('  {:>10}  {:>8}  {:>8}  {:>5}  {}\n'.format(
                            gssi, e['first'], e['last'], e['count'],
                            ','.join(sorted(e['sources']))))

                f.write('\n## RAW STDOUT KEYWORDS (lines from tetra-rx)\n\n')
                if not s['stdout_counters']:
                    f.write('(no keywords detected yet)\n')
                for kk in sorted(s['stdout_counters'].keys()):
                    f.write('=== {} (lines={}) ===\n'.format(kk, s['stdout_counters'][kk]))
                    f.write('sample: ' + s['stdout_samples'].get(kk, '') + '\n\n')
        except Exception:
            pass


_encr_state = {"current_la": "", "network_encrypted": False, "active_ts_encr": {}}
_encr_traffic_last = {}  # per-slot last emit time dla tch_active rate limit


def emit_meta(meta_dict):
    """Write metadata as JSON line to stderr."""
    try:
        line = json.dumps(meta_dict) + '\n'
        try:
            _diag_record(meta_dict, line)
        except Exception:
            pass
        sys.stderr.write(line)
        sys.stderr.flush()
        # Sprawdź czy event ma encrypted aspect → emit "encrypted_activity"
        try:
            _maybe_emit_encrypted(meta_dict)
        except Exception:
            pass
    except (BrokenPipeError, OSError):
        pass


def _maybe_emit_encrypted(evt):
    """Dla każdego eventu, jeśli ma encrypted aspect, wyemituj `encrypted_activity`.

    Bazuje na clear-text PDU headers (jak TTT "Show encrypted call details"):
    - netinfo (encrypted=True) ustawia network_encrypted flag
    - call_setup, tx_grant, call_release z enc_control > 0 (TEA1/2/3) — emit
    - lub gdy network_encrypted == True i call leci po tej sieci — emit
    Identyfikator SSI/GSSI/timeslot z clear-text addressing fields.
    """
    t = evt.get("type")
    if t == "netinfo":
        _encr_state["current_la"] = evt.get("la", "")
        _encr_state["network_encrypted"] = bool(evt.get("encrypted", False))
        return
    # Czy event ma encrypted aspect?
    enc_ctrl = evt.get("enc_control", 0)
    net_enc = _encr_state["network_encrypted"]
    has_encr = (enc_ctrl and enc_ctrl > 0) or net_enc
    if not has_encr:
        return
    action_map = {
        "call_setup": "call_setup",
        "call_connect": "call_setup",
        "tx_grant": "tx_grant",
        "tx_state": "tx_grant",  # może mieć subtype
        "call_release": "call_release",
        "resource": "pdu",
        "sds": "sds",
    }
    if t not in action_map:
        return
    action = action_map[t]
    encrypted_event = {
        "protocol": "TETRA",
        "type": "encrypted_activity",
        "action": action,
        "la": _encr_state["current_la"],
        "ssi": evt.get("ssi2") or evt.get("calling_ssi") or evt.get("target_ssi") or evt.get("ssi"),
        "gssi": evt.get("ssi") if evt.get("ssi") and evt.get("ssi2") else "",
        "tn": evt.get("timeslot"),
        "call_id": evt.get("call_id"),
        "enc_mode": "TEA" + str(enc_ctrl) if enc_ctrl else ("network" if net_enc else ""),
        "source_event": t,
    }
    # Brak duplikatów dla GSSI same
    if encrypted_event["gssi"] == encrypted_event["ssi"]:
        encrypted_event["gssi"] = ""
    try:
        line = json.dumps(encrypted_event) + '\n'
        sys.stderr.write(line)
        sys.stderr.flush()
    except (BrokenPipeError, OSError):
        pass


def main():
    running = True

    def shutdown(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Find free port for tetra-rx TETMON output
    udp_port = find_free_port()

    # Environment for tetra-rx
    env = os.environ.copy()
    env['TETRA_HACK_PORT'] = str(udp_port)
    env['TETRA_HACK_IP'] = '127.0.0.1'
    env['TETRA_HACK_RXID'] = '1'

    # Keyfile path (optional)
    keyfile = os.path.join(TETRA_DIR, 'keyfile')
    tetra_rx_path = os.path.join(TETRA_DIR, 'tetra-rx')

    # Start DQPSK demodulator: reads IQ from our stdin, outputs bits to pipe
    # stderr carries AFC JSON lines from AFCProbe
    demod = subprocess.Popen(
        ['python3', os.path.join(TETRA_DIR, 'tetra_demod.py')],
        stdin=0,  # inherit our stdin (IQ from OpenWebRX)
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )

    # Start tetra-rx: reads bits from demod, sends TETMON to UDP
    tetra_rx_cmd = [tetra_rx_path, '-r', '-s', '/dev/stdin']
    if os.path.isfile(keyfile):
        tetra_rx_cmd.extend(['-k', keyfile])

    tetra_rx = subprocess.Popen(
        tetra_rx_cmd,
        stdin=demod.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env
    )

    # Close demod stdout in parent (tetra-rx owns it now)
    demod.stdout.close()

    # Start codec pipeline
    codec = CodecPipeline()

    # Shared state protected by lock
    state_lock = threading.Lock()
    # Timeslot usage: {tn: "unallocated"/"control"/"common_control"/"reserved"/"traffic"/"stale"/"unknown"}
    ts_usage = {1: "unknown", 2: "unknown", 3: "unknown", 4: "unknown"}
    # Per-slot last ACCESS-ASSIGN timestamp (monotonic) for TTL-based aging
    ts_seen = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    TS_TTL_SEC = 2.0
    current_tn = [0]
    # AFC value from demodulator (Hz offset)
    afc_value = [0.0]
    # Burst counter for signal quality
    burst_count = [0]
    burst_rate = [0.0]  # bursts/sec
    burst_window_start = [time.monotonic()]
    # Call type from basicinfo
    call_type_info = [""]  # "group", "individual", "broadcast", etc.

    # Neighbour cells learned from D_NWRK_BROADCAST stdout
    # key = cell_id; value = dict with carrier/dlf/load/synced/last_seen
    neighbour_cells = {}
    NEIGHBOUR_TTL = 60.0  # seconds; cells not seen for that long are dropped
    network_state = {
        "cell_reselect": None,
        "cell_load": None,
        "tetra_time": None,      # {"secs": int, "offset_min": int, "year": int}
        "service_details": None, # int from BNCH SYSINFO
        "cck_id": None,          # Common Cipher Key ID
        "hyperframe": None,      # current hyperframe counter
    }
    # Extra call fields scraped from stdout (CPTI=1 paths in tetra-rx)
    # Reset on call_release. Latest-wins, attached to next call_setup/connect/grant.
    call_extras = {}

    # Current LA (string) — tracked from netinfo emits, attached to ms_register events
    current_la = [None]
    # Track last network (MCC, MNC) — reset session state on change (user retuned to different cell)
    current_network = [None]  # tuple (mcc, mnc) or None

    # Active ISSIs seen in RESOURCE stdout lines (any radio communicating on cell)
    # ssi -> {"last_seen": mono, "encr": int}
    active_ssi = {}
    ACTIVE_SSI_TTL = 300.0   # 5 min
    ACTIVE_SSI_EMIT = 10.0   # event every 10s
    last_active_ssi_emit = [0.0]

    # Compiled regex for stdout parsing
    re_sync = re.compile(r'TN \d+\((\d+)\)')
    re_access_dl = re.compile(r'DL_USAGE:\s*(Unallocated|Assigned control|Common control|Reserved|Traffic|\S+)')
    re_access_a1 = re.compile(r'ACCESS1:\s*A/(\d+)')
    re_basicinfo = re.compile(r'Basicinfo:0x([0-9A-Fa-f]{2})')
    re_duplex = re.compile(r'Duplex:(\d+)')
    re_hookmethod = re.compile(r'[Hh]ook(?:method|_method)?:(\d+)')

    def decode_call_type(basicinfo_byte):
        """Decode TETRA Basic Service Information byte to call type string."""
        # Bits 7-5: circuit mode type
        cmt = (basicinfo_byte >> 5) & 0x07
        # Bit 4: encryption
        # Bits 3-0: communication type
        comm = basicinfo_byte & 0x0F
        types = {0: "individual", 1: "group", 2: "broadcast",
                 3: "acknowledged group"}
        cmt_str = types.get(cmt, "other")
        if comm == 1:
            cmt_str += " TEA1"
        elif comm == 2:
            cmt_str += " TEA2"
        elif comm == 3:
            cmt_str += " TEA3"
        return cmt_str

    def parse_tetra_rx_stdout():
        """Read tetra-rx stdout and extract timeslot/call info."""
        fd = tetra_rx.stdout.fileno()
        carry = ''  # incomplete line tail from previous read
        try:
            while True:
                chunk = os.read(fd, 16384)
                if not chunk:
                    break
                text = carry + chunk.decode(errors='replace')
                # Split off the last (possibly incomplete) segment as new carry
                if text.endswith('\n'):
                    carry = ''
                else:
                    nl = text.rfind('\n')
                    if nl < 0:
                        carry = text
                        continue
                    carry = text[nl + 1:]
                    text = text[:nl + 1]

                # Find all TN mentions (timeslot numbers)
                for m in re_sync.finditer(text):
                    tn = int(m.group(1))
                    if tn == 0:
                        tn = 1
                    current_tn[0] = tn

                for line in text.split('\n'):
                    _diag_stdout_count(line)
                    # ACCESS-ASSIGN → timeslot usage (full DL_USAGE categories)
                    if 'ACCESS-ASSIGN' in line:
                        tn = current_tn[0]
                        if not (1 <= tn <= 4):
                            continue
                        with state_lock:
                            m = re_access_dl.search(line)
                            if m:
                                usage = m.group(1)
                                if usage == 'Unallocated':
                                    cat = 'unallocated'
                                elif usage == 'Assigned control':
                                    cat = 'control'
                                elif usage == 'Common control':
                                    cat = 'common_control'
                                elif usage == 'Reserved':
                                    cat = 'reserved'
                                else:
                                    cat = 'traffic'
                                ts_usage[tn] = cat
                                ts_seen[tn] = time.monotonic()
                                # Encrypted TCH tracking: gdy slot=traffic + network encrypted
                                # → emit encrypted_activity co 5s (per-slot rate limit)
                                if cat == 'traffic' and _encr_state.get("network_encrypted"):
                                    last = _encr_traffic_last.get(tn, 0)
                                    now_t = time.time()
                                    if now_t - last >= 5.0:
                                        _encr_traffic_last[tn] = now_t
                                        emit_meta({
                                            "protocol": "TETRA",
                                            "type": "encrypted_activity",
                                            "action": "tch_active",
                                            "la": _encr_state.get("current_la", ""),
                                            "tn": tn,
                                            "enc_mode": "network",
                                            "source_event": "aach_dl_usage_traffic",
                                            "description": f"NDB1/NDB2 traffic na TS{tn} (network encrypted)",
                                        })
                                continue
                            m = re_access_a1.search(line)
                            if m:
                                val = int(m.group(1))
                                if val == 0:
                                    cat = 'unallocated'
                                elif val == 1:
                                    cat = 'control'
                                elif val == 2:
                                    cat = 'common_control'
                                elif val == 3:
                                    cat = 'reserved'
                                else:
                                    cat = 'traffic'
                                ts_usage[tn] = cat
                                ts_seen[tn] = time.monotonic()

                    # Basicinfo → call type (group/individual/etc.)
                    if 'Basicinfo' in line:
                        m = re_basicinfo.search(line)
                        if m:
                            bi = int(m.group(1), 16)
                            with state_lock:
                                call_type_info[0] = decode_call_type(bi)

                    # D_NWRK_BROADCAST → network params + neighbour cells
                    if 'D_NWRK_BROADCAST' in line:
                        nm = NWRK_PATTERN.search(line)
                        if nm:
                            with state_lock:
                                network_state["cell_reselect"] = int(nm.group(1), 16)
                                network_state["cell_load"] = int(nm.group(2))
                        tm = TETRA_TIME_PATTERN.search(line)
                        if tm:
                            off = int(tm.group(3))
                            if tm.group(2) == '-':
                                off = -off
                            with state_lock:
                                network_state["tetra_time"] = {
                                    "secs": int(tm.group(1)),
                                    "offset_min": off,
                                    # tetra-rx already prints 2000+year, don't add again
                                    "year": int(tm.group(4)),
                                }
                        # NCI entries embedded in the same line (one D_NWRK_BROADCAST → 0..N NCI)
                        now_mono = time.monotonic()
                        for nci in NCI_PATTERN.finditer(line):
                            cid = int(nci.group(1))
                            with state_lock:
                                neighbour_cells[cid] = {
                                    "cell_id": cid,
                                    "cell_resel": int(nci.group(2)),
                                    "synced": bool(int(nci.group(3))),
                                    "load": int(nci.group(4)),
                                    "carrier": int(nci.group(5)),
                                    "dlf": int(nci.group(6)),
                                    "last_seen": now_mono,
                                }

                    # BNCH SYSINFO → service_details + CCK ID / Hyperframe
                    if 'BNCH SYSINFO' in line:
                        bm = BNCH_PATTERN.search(line)
                        if bm:
                            with state_lock:
                                network_state["service_details"] = int(bm.group(3), 16)
                        cm = BNCH_CCK_PATTERN.search(line)
                        if cm:
                            with state_lock:
                                network_state["cck_id"] = int(cm.group(1))
                        hm = BNCH_HYPERFRAME_PATTERN.search(line)
                        if hm:
                            with state_lock:
                                network_state["hyperframe"] = int(hm.group(1))

                    # Call identifier:N Call timeout:N Hookmethod:N Duplex:N
                    if 'Call identifier:' in line:
                        cp = CALL_PARAMS_PATTERN.search(line)
                        if cp:
                            with state_lock:
                                call_extras["call_id_stdout"] = int(cp.group(1))
                                call_extras["call_timeout"] = int(cp.group(2))
                                call_extras["hook_method"] = int(cp.group(3))
                                call_extras["duplex"] = int(cp.group(4))

                    # NotificationID:N Tempaddr:N CPTI:N CallingSSI:N CallingExt:N
                    if 'CallingSSI:' in line:
                        cm = CALLER_PATTERN.search(line)
                        if cm:
                            with state_lock:
                                call_extras["notification_id"] = int(cm.group(1))
                                call_extras["temp_addr"] = int(cm.group(2))
                                call_extras["cpti"] = int(cm.group(3))
                                call_extras["calling_ssi"] = int(cm.group(4))
                                call_extras["calling_ext"] = int(cm.group(5))

                    # RESOURCE ... Addr=SSI(N) — destination address (MAY be GSSI/USSI not ISSI)
                    if 'RESOURCE' in line and 'Addr=SSI' in line:
                        rm = RESOURCE_SSI_PATTERN.search(line)
                        if rm:
                            ssi = int(rm.group(2))
                            if ssi != 0 and ssi != 0xFFFFFF:
                                encr = int(rm.group(1))
                                with state_lock:
                                    entry = active_ssi.setdefault(ssi, {"encr": encr, "sources": set()})
                                    entry["last_seen"] = time.monotonic()
                                    entry["encr"] = encr
                                    entry["sources"].add("resource_addr")

                    # NotificationID — calling_ssi is confirmed ISSI (individual radio TX'ing)
                    if 'NotificationID:' in line:
                        nm = CALLER_PATTERN.search(line)
                        if nm:
                            issi = int(nm.group(4))
                            if issi != 0:
                                with state_lock:
                                    entry = active_ssi.setdefault(issi, {"encr": 0, "sources": set()})
                                    entry["last_seen"] = time.monotonic()
                                    entry["sources"].add("calling_ssi")

                    def _emit_ms(evt):
                        if current_la[0]:
                            evt.setdefault("la", current_la[0])
                        # Friendly summary string mirroring TTT format
                        act = evt.get("action", "")
                        ssi = evt.get("ssi")
                        gssi = evt.get("gssi")
                        utn = evt.get("update_type_name")
                        result = evt.get("auth_result")
                        if act == "authentication_demand":
                            evt.setdefault("summary",
                                "BS demands authentication" + (f". SSI: {ssi}" if ssi else ""))
                        elif act == "authentication_result":
                            evt.setdefault("summary",
                                f"BS result to MS authentication: {result or 'unknown'}" +
                                (f" SSI: {ssi}" if ssi else ""))
                        elif act == "location_update_accept":
                            parts = ["MS request for registration/authentication ACCEPTED"]
                            if ssi: parts.append(f"for SSI: {ssi}")
                            if gssi: parts.append(f"GSSI: {gssi}")
                            extra = []
                            if result: extra.append(result)
                            if utn: extra.append(utn)
                            tail = (" - " + " - ".join(extra)) if extra else ""
                            evt.setdefault("summary", " ".join(parts) + tail)
                        elif act == "location_update_reject":
                            evt.setdefault("summary", "MS registration REJECTED" + (f" SSI: {ssi}" if ssi else ""))
                        elif act == "location_update_command":
                            evt.setdefault("summary", "BS sent location update COMMAND")
                        elif act in ("group_attach", "group_detach"):
                            verb = "attached" if act == "group_attach" else "detached"
                            evt.setdefault("summary", f"MS {verb} from group GSSI: {gssi}")
                        emit_meta(evt)

                    # MM PDU: D-LOCATION UPDATE ACCEPT → radio registered
                    if 'D-LOCATION UPDATE ACCEPT' in line:
                        mm = MM_LOC_UPDATE_ACCEPT_PATTERN.search(line)
                        if mm:
                            ut = int(mm.group(1))
                            _emit_ms({
                                "protocol": "TETRA",
                                "type": "ms_register",
                                "action": "location_update_accept",
                                "ssi": int(mm.group(3)),
                                "update_type": ut,
                                "update_type_name": MM_UPDATE_TYPE_NAMES.get(ut, 'unknown'),
                                "addr_type": int(mm.group(2)),
                                "subscr_class": int(mm.group(4), 16) if mm.group(4) else None,
                                "auth_result": "Authentication successful or no authentication currently in progress",
                            })

                    # MM PDU: D-ATTACH/DETACH GROUP → group attach/detach
                    if 'D-ATTACH/DETACH GROUP:' in line:
                        am = MM_ATTACH_GROUP_PATTERN.search(line)
                        if am:
                            _emit_ms({
                                "protocol": "TETRA",
                                "type": "ms_register",
                                "action": "group_attach" if int(am.group(1)) == 0 else "group_detach",
                                "gssi": int(am.group(4)),
                                "report": int(am.group(2)),
                                "attach_type": int(am.group(3)),
                            })

                    # D-AUTHENTICATION-DEMAND / D-AUTHENTICATION-RESULT (TETRA EN 300 392-7)
                    if 'D-AUTHENTICATION' in line:
                        lstripped = line.lstrip()[:40]
                        ssi_m = re.search(r'\b(?:SSI|ISSI):(\d+)', line)
                        ssi_val = int(ssi_m.group(1)) if ssi_m else None
                        if 'D-AUTHENTICATION RESULT' in lstripped or 'D-AUTH-RESULT' in lstripped:
                            # tetra-rx may print result code; default to "success"
                            res_m = re.search(r'result[:\s=]+([A-Za-z _]+)', line, re.I)
                            res = res_m.group(1).strip() if res_m else 'Authentication successful or no authentication currently in progress'
                            _emit_ms({
                                "protocol": "TETRA", "type": "ms_register",
                                "action": "authentication_result", "ssi": ssi_val, "auth_result": res,
                            })
                        elif 'D-AUTH' in lstripped:
                            _emit_ms({
                                "protocol": "TETRA", "type": "ms_register",
                                "action": "authentication_demand", "ssi": ssi_val,
                            })
                    if 'D-LOCATION UPDATE REJECT' in line:
                        ssi_m = re.search(r'\bSSI:(\d+)', line)
                        _emit_ms({
                            "protocol": "TETRA", "type": "ms_register",
                            "action": "location_update_reject",
                            "ssi": int(ssi_m.group(1)) if ssi_m else None,
                        })
                    if 'D-LOCATION UPDATE COMMAND' in line:
                        _emit_ms({
                            "protocol": "TETRA", "type": "ms_register",
                            "action": "location_update_command",
                        })
                    if 'D-LOCATION UPDATE PROCEEDING' in line:
                        _emit_ms({
                            "protocol": "TETRA", "type": "ms_register",
                            "action": "location_update_proceeding",
                        })
                    if 'D-ATTACH/DETACH GROUP ID ACK' in line:
                        _emit_ms({
                            "protocol": "TETRA", "type": "ms_register",
                            "action": "attach_detach_ack",
                        })

                    # CMCE PDUs with fields — Disconnect with cause is most important
                    if 'D-DISCONNECT:' in line:
                        dm = re.search(r'D-DISCONNECT:\s+Call Identifier:(\d+)\s+Disconnect cause:(\d+)', line)
                        if dm:
                            emit_meta({
                                "protocol": "TETRA", "type": "call_disconnect",
                                "call_id": int(dm.group(1)),
                                "disconnect_cause": int(dm.group(2)),
                            })
                    if 'D-CALL PROCEEDING:' in line:
                        cm = re.search(r'D-CALL PROCEEDING:\s+Call Identifier:(\d+)\s+Timeout:(\d+)\s+Hook:(\d+)\s+Duplex:(\d+)\s+TX_Grant:(\d+)\s+TX_Perm:(\d+)', line)
                        if cm:
                            emit_meta({
                                "protocol": "TETRA", "type": "call_proceeding",
                                "call_id": int(cm.group(1)),
                                "call_timeout": int(cm.group(2)),
                                "hook_method": int(cm.group(3)),
                                "duplex": int(cm.group(4)),
                                "tx_grant": int(cm.group(5)),
                                "tx_perm": int(cm.group(6)),
                            })
                    if 'D-ALERT:' in line:
                        am = re.search(r'D-ALERT:\s+Call Identifier:(\d+)\s+Timeout:(\d+)\s+Hook:(\d+)\s+Duplex:(\d+)\s+TX_Grant:(\d+)\s+TX_Perm:(\d+)', line)
                        if am:
                            emit_meta({
                                "protocol": "TETRA", "type": "call_alert",
                                "call_id": int(am.group(1)),
                                "call_timeout": int(am.group(2)),
                                "hook_method": int(am.group(3)),
                                "duplex": int(am.group(4)),
                                "tx_grant": int(am.group(5)),
                                "tx_perm": int(am.group(6)),
                            })
                    if 'D-CONNECT ACK:' in line:
                        cm = re.search(r'D-CONNECT ACK:\s+Call Identifier:(\d+)\s+Timeout:(\d+)\s+TX_Grant:(\d+)\s+TX_Perm:(\d+)\s+NID:(\d+)', line)
                        if cm:
                            emit_meta({
                                "protocol": "TETRA", "type": "connect_ack",
                                "call_id": int(cm.group(1)),
                                "call_timeout": int(cm.group(2)),
                                "tx_grant": int(cm.group(3)),
                                "tx_perm": int(cm.group(4)),
                                "nid": int(cm.group(5)),
                            })
                    if 'D-INFO:' in line:
                        im = re.search(r'D-INFO:\s+Call Identifier:(\d+)\s+Reset_timer:(\d+)\s+TX_Perm:(\d+)', line)
                        if im:
                            emit_meta({
                                "protocol": "TETRA", "type": "call_info",
                                "call_id": int(im.group(1)),
                                "reset_timer": int(im.group(2)),
                                "tx_perm": int(im.group(3)),
                            })
                    if 'D-TX CEASED:' in line:
                        m = re.search(r'D-TX CEASED:\s+Call Identifier:(\d+)\s+TX_Request_permission:(\d+)', line)
                        if m:
                            emit_meta({
                                "protocol": "TETRA", "type": "tx_state", "subtype": "ceased",
                                "call_id": int(m.group(1)), "tx_request_perm": int(m.group(2)),
                            })
                    if 'D-TX CONTINUE:' in line:
                        m = re.search(r'D-TX CONTINUE:\s+Call Identifier:(\d+)\s+Continue:(\d+)\s+TX_Perm:(\d+)', line)
                        if m:
                            emit_meta({
                                "protocol": "TETRA", "type": "tx_state", "subtype": "continue",
                                "call_id": int(m.group(1)), "continue": int(m.group(2)), "tx_perm": int(m.group(3)),
                            })
                    if 'D-TX INTERRUPT:' in line:
                        m = re.search(r'D-TX INTERRUPT:\s+Call Identifier:(\d+)\s+TX_Perm:(\d+)', line)
                        if m:
                            emit_meta({
                                "protocol": "TETRA", "type": "tx_state", "subtype": "interrupt",
                                "call_id": int(m.group(1)), "tx_perm": int(m.group(2)),
                            })
                    if 'D-TX WAIT:' in line:
                        m = re.search(r'D-TX WAIT:\s+Call Identifier:(\d+)', line)
                        if m:
                            emit_meta({
                                "protocol": "TETRA", "type": "tx_state", "subtype": "wait",
                                "call_id": int(m.group(1)),
                            })
                    if 'D-CALL RESTORE:' in line:
                        m = re.search(r'D-CALL RESTORE:\s+Call Identifier:(\d+)\s+TX_Grant:(\d+)\s+TX_Perm:(\d+)', line)
                        if m:
                            emit_meta({
                                "protocol": "TETRA", "type": "call_restore",
                                "call_id": int(m.group(1)), "tx_grant": int(m.group(2)), "tx_perm": int(m.group(3)),
                            })
                    if 'D-FACILITY:' in line:
                        m = re.search(r'D-FACILITY:\s+SSI:(\d+)\s+IDX:(\d+)', line)
                        if m:
                            emit_meta({
                                "protocol": "TETRA", "type": "facility",
                                "ssi": int(m.group(1)), "idx": int(m.group(2)),
                            })

                    # MM / security PDUs — emit as ms_register with new actions
                    if 'D-OTAR' in line:
                        _emit_ms({"protocol": "TETRA", "type": "ms_register", "action": "otar"})
                    if 'D-CK CHANGE DEMAND' in line:
                        _emit_ms({"protocol": "TETRA", "type": "ms_register", "action": "ck_change_demand"})
                    if 'D-DISABLE' in line:
                        _emit_ms({"protocol": "TETRA", "type": "ms_register", "action": "ms_disable"})
                    if 'D-ENABLE' in line:
                        _emit_ms({"protocol": "TETRA", "type": "ms_register", "action": "ms_enable"})
                    if 'D-MM STATUS' in line:
                        sm = re.search(r'D-MM STATUS[^\n]*?(?:status|code)[:\s=]+(\d+)', line, re.I)
                        evt = {"protocol": "TETRA", "type": "ms_register", "action": "mm_status"}
                        if sm:
                            evt["mm_status_code"] = int(sm.group(1))
                        _emit_ms(evt)

                    # MLE PDUs — handover / restoration
                    if 'D-NEW CELL' in line:
                        emit_meta({"protocol": "TETRA", "type": "cell_change", "action": "new_cell"})
                    if 'D-PREPARE FAIL' in line:
                        emit_meta({"protocol": "TETRA", "type": "cell_change", "action": "prepare_fail"})
                    if 'D-RESTORE ACK' in line:
                        emit_meta({"protocol": "TETRA", "type": "cell_change", "action": "restore_ack"})
                    if 'D-RESTORE FAIL' in line:
                        emit_meta({"protocol": "TETRA", "type": "cell_change", "action": "restore_fail"})
                    if 'D-CHANNEL RESPONSE' in line:
                        emit_meta({"protocol": "TETRA", "type": "cell_change", "action": "channel_response"})
                    if 'D-NWRK BROADCAST EXT' in line:
                        emit_meta({"protocol": "TETRA", "type": "cell_change", "action": "nwrk_broadcast_ext"})

        except (ValueError, OSError):
            pass

    def read_demod_stderr():
        """Read JSON events from demodulator stderr: AFC + sync_hit/sync_stat."""
        try:
            for line in demod.stderr:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if 'afc' in data:
                        with state_lock:
                            afc_value[0] = data['afc']
                    elif 'sync_stat' in data:
                        # Forward periodic sync rate to frontend.
                        # Used by UI to flag "TETRA sync present here" — when
                        # rate > 0 on a frequency that is NOT a known TMO DL,
                        # this strongly suggests DMO activity.
                        stat = data['sync_stat']
                        write_meta({
                            'protocol': 'tetra',
                            'type': 'sync_stat',
                            'hits_per_s': stat.get('hits_per_s', 0.0),
                            'window_s': stat.get('window_s', 0.0),
                        })
                    elif 'sync_hit' in data:
                        # Individual sync hits are bursty; keep low overhead
                        # by relying on sync_stat for UI. Hits forwarded only
                        # for diag log.
                        pass
                except (json.JSONDecodeError, Exception):
                    pass
        except (ValueError, OSError):
            pass

    stdout_thread = threading.Thread(target=parse_tetra_rx_stdout, daemon=True)
    stdout_thread.start()
    demod_stderr_thread = threading.Thread(target=read_demod_stderr, daemon=True)
    demod_stderr_thread.start()

    # UDP listener for TETMON audio frames
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', udp_port))
    sock.settimeout(0.1)  # Short timeout for responsive silence output

    # Silence frame for continuous audio output (20ms at 8kHz = 160 samples = 320 bytes)
    silence_20ms = b'\x00' * 320

    # Virtual playout clock — represents wallclock time at which the last queued
    # PCM sample will play. Inject silence only when this falls BEHIND wallclock
    # (true gap), never between consecutive ACELP frames. Each ACELP packet
    # decodes to 60ms PCM, so injecting silence every 20ms used to chop voice
    # into pieces causing crackle.
    audio_clock = time.monotonic()
    PCM_FRAME_SEC = PCM_OUTPUT_BYTES / 2 / 8000   # 960 bytes / 2 / 8000 = 0.060s
    SILENCE_FRAME_SEC = 0.020
    SILENCE_MARGIN = 0.005   # allow up to 5ms underrun before injecting

    # Rate limiting per message type
    last_emit_time = {}  # {type: timestamp}
    RATE_LIMITS = {
        "burst": 0.5,
        "netinfo": 5.0,
        "freqinfo": 10.0,
        "neighbour_freq": 10.0,
        "encinfo": 5.0,
    }
    NEIGHBOURS_EMIT_INTERVAL = 5.0
    last_neighbours_emit = [0.0]

    while running:
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            # Output silence only if virtual playout clock is behind wallclock
            now = time.monotonic()
            if audio_clock < now + SILENCE_MARGIN:
                try:
                    sys.stdout.buffer.write(silence_20ms)
                    sys.stdout.buffer.flush()
                    audio_clock = max(audio_clock, now) + SILENCE_FRAME_SEC
                except (BrokenPipeError, OSError):
                    running = False
            continue
        except Exception:
            break

        # Check if tetra-rx is still alive
        if tetra_rx.poll() is not None:
            break

        # Periodic active_ssi emit (radios seen on this cell)
        now_mono = time.monotonic()
        if now_mono - last_active_ssi_emit[0] >= ACTIVE_SSI_EMIT:
            with state_lock:
                live = [
                    {
                        "ssi": s,
                        "encr": v["encr"],
                        "age": round(now_mono - v["last_seen"], 1),
                        "sources": sorted(v.get("sources", [])),
                        # confirmed = seen as calling_ssi (real ISSI), else may be GSSI/addr
                        "confirmed": "calling_ssi" in v.get("sources", []),
                    }
                    for s, v in active_ssi.items()
                    if now_mono - v["last_seen"] < ACTIVE_SSI_TTL
                ]
            if live:
                live.sort(key=lambda x: x["age"])
                emit_meta({
                    "protocol": "TETRA",
                    "type": "active_ssi",
                    "ssis": live[:50],   # cap to 50 most recent
                    "total": len(live),
                })
                last_active_ssi_emit[0] = now_mono

        # Periodic neighbours emit (independent of TETMON traffic)
        if now_mono - last_neighbours_emit[0] >= NEIGHBOURS_EMIT_INTERVAL:
            with state_lock:
                active = [
                    nc for nc in neighbour_cells.values()
                    if now_mono - nc["last_seen"] < NEIGHBOUR_TTL
                ]
                net_snapshot = dict(network_state)
            if active:
                ev = {
                    "protocol": "TETRA",
                    "type": "neighbours",
                    "cells": [
                        {
                            "cell_id": nc["cell_id"],
                            "carrier": nc["carrier"],
                            "dlf": nc["dlf"],
                            "load": nc["load"],
                            "synced": nc["synced"],
                            "age": round(now_mono - nc["last_seen"], 1),
                        }
                        for nc in sorted(active, key=lambda x: x["cell_id"])
                    ],
                }
                if net_snapshot.get("cell_reselect") is not None:
                    ev["cell_reselect"] = net_snapshot["cell_reselect"]
                if net_snapshot.get("tetra_time"):
                    ev["tetra_time"] = net_snapshot["tetra_time"]
                emit_meta(ev)
                last_neighbours_emit[0] = now_mono

        # Parse and emit metadata (non-audio TETMON messages)
        meta = parse_metadata_from_udp(data)
        if meta is not None:
            now = time.monotonic()
            msg_type = meta.get("type")
            # Count ALL bursts for rate calculation (before throttle)
            if msg_type == "burst":
                burst_count[0] += 1
                elapsed = now - burst_window_start[0]
                if elapsed >= 2.0:
                    burst_rate[0] = burst_count[0] / elapsed
                    burst_count[0] = 0
                    burst_window_start[0] = now

            rate_limit = RATE_LIMITS.get(msg_type, 0)
            last_t = last_emit_time.get(msg_type, 0)

            if now - last_t >= rate_limit:
                if msg_type == "burst":

                    with state_lock:
                        now_m = time.monotonic()
                        ts_payload = {}
                        for k, v in ts_usage.items():
                            age = now_m - ts_seen[k] if ts_seen[k] > 0 else None
                            usage_eff = v if (age is not None and age <= TS_TTL_SEC) else ('stale' if ts_seen[k] > 0 else 'unknown')
                            ts_payload[str(k)] = {
                                "usage": usage_eff,
                                "age": round(age, 2) if age is not None else None,
                            }
                        meta["timeslots"] = ts_payload
                        meta["afc"] = afc_value[0]
                        meta["burst_rate"] = round(burst_rate[0], 1)
                        if call_type_info[0]:
                            meta["call_type"] = call_type_info[0]

                # Enrich netinfo with stdout-scraped fields
                if msg_type == "netinfo":
                    with state_lock:
                        if network_state["cck_id"] is not None:
                            meta["cck_id"] = network_state["cck_id"]
                        if network_state["service_details"] is not None:
                            meta["service_details"] = network_state["service_details"]
                        if network_state["hyperframe"] is not None:
                            meta["hyperframe"] = network_state["hyperframe"]
                        if network_state.get("tetra_time"):
                            meta["tetra_time"] = network_state["tetra_time"]
                    # Track current LA for ms_register events
                    la_val = meta.get("la")
                    if la_val:
                        current_la[0] = str(la_val)
                    # Detect MCC/MNC change → reset session state (active_ssi, neighbours, etc.)
                    mcc_v = meta.get("mcc")
                    mnc_v = meta.get("mnc")
                    if mcc_v and mnc_v and meta.get("dl_freq"):
                        net_key = (mcc_v, mnc_v)
                        if current_network[0] is not None and current_network[0] != net_key:
                            with state_lock:
                                active_ssi.clear()
                                neighbour_cells.clear()
                                call_extras.clear()
                            emit_meta({
                                "protocol": "TETRA", "type": "session_reset",
                                "old_network": "%d-%d" % current_network[0],
                                "new_network": "%d-%d" % net_key,
                            })
                        current_network[0] = net_key

                # Add call_type to call_setup messages
                if msg_type == "call_setup":
                    with state_lock:
                        if call_type_info[0]:
                            meta["call_type"] = call_type_info[0]

                # Attach stdout-scraped extras to call lifecycle events.
                # Don't overwrite fields the UDP payload already set.
                if msg_type in ("call_setup", "call_connect", "tx_grant"):
                    with state_lock:
                        for k, v in call_extras.items():
                            if k == "call_id_stdout":
                                continue
                            if k not in meta:
                                meta[k] = v

                # Clear extras on release so they don't bleed into the next call
                if msg_type == "call_release":
                    with state_lock:
                        call_extras.clear()

                emit_meta(meta)
                last_emit_time[msg_type] = now

        # Try to extract audio
        acelp_data = parse_audio_from_udp(data)
        if acelp_data is not None:
            pcm = codec.decode(acelp_data)
            if pcm:
                try:
                    sys.stdout.buffer.write(pcm)
                    sys.stdout.buffer.flush()
                    # Advance virtual playout clock by the audio duration we
                    # just queued. If the clock was behind, snap to now first.
                    now = time.monotonic()
                    audio_clock = max(audio_clock, now) + PCM_FRAME_SEC
                except (BrokenPipeError, OSError):
                    running = False
        else:
            # Non-audio packet — only inject silence if playout clock is behind
            now = time.monotonic()
            if audio_clock < now + SILENCE_MARGIN:
                try:
                    sys.stdout.buffer.write(silence_20ms)
                    sys.stdout.buffer.flush()
                    audio_clock = max(audio_clock, now) + SILENCE_FRAME_SEC
                except (BrokenPipeError, OSError):
                    running = False

    # Cleanup
    sock.close()
    codec.stop()
    for proc in (tetra_rx, demod):
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == '__main__':
    main()
