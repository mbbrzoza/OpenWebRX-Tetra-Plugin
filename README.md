# OpenWebRX+ TETRA Plugin

**Author: SP8MB (mbbrzoza)**

Digital-voice decoder plugin for [OpenWebRX+](https://github.com/luarvique/openwebrx). Adds demodulation modes — **TETRA TMO**, **TETRA DMO**, **LoRa-APRS**, **DMR EP** — plus cell identification for **LTE** and **GSM** (click a carrier on the waterfall), with signaling extraction, voice decoding and real-time panels in the OpenWebRX+ web UI.

| Tryb / Mode | Opis / Description | Instalacja / Install |
|---|---|---|
| `tetra` | TETRA TMO (trunked) — sygnalizacja L1/L2/L3, fonia ACELP, panel TETRA Monitor | `install.sh` |
| `tetra-dmo` | TETRA DMO / DM-GATE — bezpośrednia łączność radio↔radio | `install.sh` |
| `lora` | LoRa-APRS (CSS) — dekoder ramek APRS, digimode drugorzędny | `install.sh` |
| `dmr-ep` | DMR z deszyfrowaniem RC4 (Enhanced/Basic Privacy) + pozycje LRRP na mapie | `patch_dmr_ep.py` (ręcznie / manual) |
| `lte` | Identyfikacja komórki LTE: PCI, MIB, SIB1–7 (operator, eNB, TAC, sąsiedzi) | `patch_lte.py` (ręcznie / manual) |
| `gsm` | Identyfikacja komórki GSM: BSIC, CI/LAC, SI1–13, sąsiedzi + wykrywanie anomalii (IMSI-catcher) | `patch_gsm.py` (ręcznie / manual) |

---

## PL — Polski

### Opis
Wtyczka rozszerza OpenWebRX+ o dekodowanie cyfrowych trybów łączności: demodulację π/4-DQPSK, dekodowanie warstw L1/L2/L3, odtwarzanie mowy w kodeku ACELP oraz dedykowane panele WWW z informacjami o sieci, połączeniach i terminalach.

### Funkcje — TETRA TMO (`tetra`)
- Demodulacja π/4-DQPSK (GNURadio)
- Dekodowanie protokołu L1/L2/L3 (osmo-tetra / tetra-rx, fork sq5bpf)
- Dekodowanie mowy ACELP (kodek ETSI) — odtwarzanie w przeglądarce
- AFC z portu FLL (radiany/sample → Hz)
- Panel WWW w czasie rzeczywistym:
  - Sieć: MCC, MNC, LA, Color Code, DL/UL, status szyfrowania, czas sieci (ETSI EN 300 392-2 §18.5.24)
  - Aktywne połączenia (call setup / connect / release / TX grant)
  - GSSI / ISSI z rozróżnieniem prawdziwego ISSI od aliasów ESI (3 kategorie: Real / Adres / ESI)
  - Stan szczelin czasowych (Traffic / Control / Common-Ctrl / Reserved / Unalloc) z TTL
  - Lista sąsiednich komórek (cell_id, carrier, DL freq, load)
  - Aktywne SSI (5 min TTL), zdarzenia MS Register (LU Accept/Reject, attach/detach, auth)
  - Wiadomości SDS z dekodowaniem protokołu i statusu doręczenia
- Pływające okno **TETRA Monitor** z zakładkami:
  - filtry zdarzeń, edytor etykiet G/SSI, eksport CSV, tryb zdalny i kompaktowy
  - **🔒 Szyfrowana aktywność** — ruch na sieci szyfrowanej wnioskowany z jawnych nagłówków PDU (NDB1/NDB2, bez deszyfrowania)
  - **📊 Statystyki** — ranking SSI wg airtime + heatmapa godzinowa
  - **Nagrania** — nagrywanie rozmów do WAV i pobieranie z przeglądarki (opcjonalne, domyślnie wyłączone)
  - **DMO** — zdarzenia z dekodera DMO

### Funkcje — TETRA DMO (`tetra-dmo`)
- Detektor synchronizacji + ekstrakcja burstów (DNB/DSB)
- Łańcuch L1: descramble → Viterbi → CRC
- Parser MAC PDU, ekstrakcja TCH i dekodowanie fonii (end-to-end)
- Zdarzenia trafiają do zakładki **DMO** w oknie TETRA Monitor
- Uwaga: fonia potwierdzona na nagraniach o wysokim SNR; przy niskim SNR ramki bywają BFI

### Funkcje — LoRa-APRS (`lora`)
- Demodulacja CSS (chirp spread spectrum) i dekodowanie ramek LoRa
- Digimode drugorzędny w OWRX+ (typowo 433.775 MHz, LoRa-APRS)

### Funkcje — DMR EP (`dmr-ep`)
- Odsłuch DMR z deszyfrowaniem **RC4 (Enhanced / Basic Privacy)** przez `dsd-fme`
- Wpisywanie klucza z panelu WWW (box na górze panelu Receiver) → zapis `/var/lib/openwebrx/dmr_ep.key` (0600) → **hot-reload** dekodera bez restartu
- Panel „DMR EP" z logiem zdekodowanych/odszyfrowanych danych (źródło, cel, ALG/Key ID)
- **Pozycje LRRP (GPS)** — piny na mapie OpenWebRX + pływające okno „Pozycje (LRRP)" nad waterfallem
- Walidacja: 24/24 PDU odszyfrowane na 148.512 MHz (ARS), 14 fixów LRRP poprawnie przypisanych do nadawców na 168.775/169.125 MHz; ścieżka live potwierdzona on-air
- ⚠️ Klucze RC4 **nie są** trzymane w repo — `dmr_ep.key` jest w `.gitignore`
- Szczegóły: [`tetra/DMR_EP.md`](tetra/DMR_EP.md)

### Funkcje — LTE (`lte`)
- Kliknięcie nośnej LTE na wodospadzie → panel „📶 LTE — komórka”
- PSS/SSS → **PCI** i CFO (dociąganie do rastra 100 kHz), `pdsch_ue` (srsRAN 4G, łatka `PDSCH_ALL_SI`) → **MIB**
- Dla komórek 1,4 MHz także **SIB1–7** (pycrate): PLMN + operator, Cell ID → eNB/sektor, TAC, pasmo/EARFCN, moc RS, sąsiedzi LTE/UMTS/GSM z priorytetami
- Ograniczenie: IF 1,92 MS/s (6 PRB) — dla szerszych komórek tylko PCI + MIB
- Szczegóły: [`tetra/LTE.md`](tetra/LTE.md)

### Funkcje — GSM (`gsm`)
- Kliknięcie nośnej BCCH (C0) → panel „📡 GSM — komórka” (gr-gsm + pycrate, analiza ~8 s)
- **BSIC** (dekodowanie SCH), PLMN + operator, **CI**, **LAC**, parametry dostępu i reselekcji (CRO), konfiguracja CCCH, T3212, GPRS/EDGE (RAC, NMO)
- Alokacja komórki (SI1), sąsiedzi GSM 900/1800, UMTS i LTE (SI2/2bis/2ter/2quater), obciążenie (paging/s), poziom i CFO z tonu FCCH
- **Wykrywanie anomalii (IMSI-catcher)** — baza znanych komórek (`/var/lib/openwebrx/gsm_cells.json`) i heurystyki: testowy/nieznany PLMN, pusta lista sąsiadów, wysoki CRO, nieznany LAC, ARFCN spoza list sąsiadów, zmiana CI/BSIC na tym samym ARFCN, brak GPRS / sąsiadów 3G/4G
- Szczegóły: [`tetra/GSM.md`](tetra/GSM.md)

### Łańcuch sygnałów
```
TETRA TMO:
IQ (36 kS/s) → tetra_demod.py (GNURadio π/4-DQPSK)
             → tetra-rx (osmo-tetra L1/L2/L3, fork sq5bpf)
             → tetra_decoder.py (parser TETMON + kodek ACELP)
             → PCM audio (stdout) + JSON metadane (stderr)
             → WebSocket → tetra_panel.js (panel w przeglądarce)

DMR EP:
IQ → dyskryminator FM → dsd-fme (RC4 EP/BP)
   → dmr_ep_decoder.py (meta + LRRP)
   → PCM audio + JSON → panel DMR EP + mapa OWRX

LTE:
IQ (1,92 MS/s) → lte_decoder.py: migawka 3 s, PSS/SSS → PCI/CFO
               → pdsch_ue (srsRAN) → MIB + SI-RNTI (UDP) → pycrate SIB → JSON → LteMetaPanel

GSM:
IQ (500 kS/s) → gsm_decoder.py: migawka 6 s → gr-gsm (bursty C0 + BCCH/CCCH)
              → SCH (BSIC), pycrate SI1–13, ocena anomalii → JSON → GsmMetaPanel
```

### Instalacja
```bash
git clone https://github.com/mbbrzoza/OpenWebRX-Tetra-Plugin.git
cd OpenWebRX-Tetra-Plugin/tetra

# Pełna instalacja (deps, kompilacja, patchowanie OpenWebRX+, frontend)
# — instaluje tryby: tetra, tetra-dmo, lora
sudo bash install.sh

# Szybka aktualizacja (skrypty + panel, bez rekompilacji)
sudo bash install.sh --update

# Sprawdzenie statusu
sudo bash install.sh --check

# Odinstalowanie (przywraca kopie .bak.pre-tetra)
sudo bash install.sh --uninstall
```

Po instalacji w OpenWebRX+ pojawiają się nowe tryby demodulacji — wystarczy ustawić je na zakładce SDR profile albo wybrać przez bookmark.

**DMR EP instaluje się osobno** (nie jest częścią `install.sh`):
```bash
cp dmr_ep_decoder.py /opt/openwebrx-tetra/
cp csdr_chain_dmr_ep.py  {dist-packages}/csdr/chain/dmr_ep.py
cp csdr_module_dmr_ep.py {dist-packages}/csdr/module/dmr_ep.py
python3 patch_dmr_ep.py {dist-packages}      # rejestruje tryb "DMR EP"
systemctl restart openwebrx
```
Wymaga `dsd-fme` w PATH (lub `/usr/local/bin/dsd-fme`) oraz `numpy`/`scipy`.

**LTE i GSM instalują się osobno**, tym samym wzorcem (decoder → `/opt/openwebrx-tetra/`, chain/module → `{dist-packages}/csdr/`, `patch_lte.py` / `patch_gsm.py`). Pełne instrukcje: [`tetra/LTE.md`](tetra/LTE.md), [`tetra/GSM.md`](tetra/GSM.md).

### Wymagania
- OpenWebRX+ v1.2.x
- Debian/Raspberry Pi OS (aarch64 lub x86_64)
- GNURadio + osmo-tetra (instalowane przez `install.sh`)
- `dsd-fme`, `numpy`, `scipy` — tylko dla trybu DMR EP
- `numpy`, `scipy`, `pycrate`, srsRAN 4G (`build_lte_pdsch.sh`) — tryb LTE
- `gr-gsm` (apt), `pycrate` — tryb GSM
- Dostęp do internetu przy pierwszej instalacji

### Pliki
```
tetra/
  install.sh                — instalator (install/update/uninstall/check, kopie .bak.pre-tetra)

  # TETRA TMO
  tetra_decoder.py          — główny dekoder: pipeline + parser TETMON + meta events
  tetra_demod.py            — demodulator DQPSK (GNURadio) z opcjonalnym pre-FLL rotatorem
  csdr_module_tetra.py      — moduł CSDR (PopenModule)
  csdr_chain_tetra.py       — łańcuch CSDR (integracja z OpenWebRX+)
  tetra_panel.js            — frontend (TetraMetaPanel + okno TETRA Monitor)
  tetra_panel.html          — szablon HTML panelu

  # TETRA DMO
  tetra_dmo_decoder.py      — dekoder DMO (live, z fonią TCH)
  dmo_l1_chain.py           — warstwa L1: descramble / Viterbi / CRC
  dmo_dnb_detect.py         — detektor burstów DNB
  dmo_burst_extract.py      — ekstrakcja burstów
  dmo_pdu_parser.py         — parser MAC PDU
  dmo_tch_extract.py        — ekstrakcja kanału ruchowego (fonia)
  dmo_demod_native.py       — demodulator natywny (sps=16 dla IQ 288k)
  csdr_module_tetra_dmo.py / csdr_chain_tetra_dmo.py

  # LoRa-APRS
  lora_decoder.py           — dekoder CSS + ramki LoRa-APRS
  csdr_module_lora.py / csdr_chain_lora.py
  patch_lora.py             — rejestracja trybu 'lora' w OWRX+

  # DMR EP
  dmr_ep_decoder.py         — dsd-fme + RC4 + ekstrakcja meta/LRRP
  dmr_ep_panel.js           — panel „DMR EP" + okno „Pozycje (LRRP)"
  dmr_ep_keys.js            — box wpisywania klucza w panelu Receiver
  patch_dmr_ep.py           — rejestracja trybu (modes/feature/dsp)
  patch_dmr_ep_panel.py / patch_dmr_ep_ui.py
  DMR_EP.md                 — dokumentacja trybu + wyniki walidacji

  # LTE
  lte_decoder.py            — PSS/SSS → PCI, pdsch_ue → MIB/SIB, pycrate
  csdr_module_lte.py / csdr_chain_lte.py
  lte_panel.js              — panel „LTE — komórka”
  patch_lte.py              — rejestracja trybu 'lte' (modes/feature/dsp/htdocs)
  build_lte_pdsch.sh        — budowa pdsch_ue (srsRAN 4G) z łatką PDSCH_ALL_SI
  LTE.md                    — dokumentacja trybu

  # GSM
  gsm_decoder.py            — gr-gsm + SCH/BSIC + pycrate SI + baza komórek / anomalie
  csdr_module_gsm.py / csdr_chain_gsm.py
  gsm_panel.js              — panel „GSM — komórka”
  patch_gsm.py              — rejestracja trybu 'gsm' (modes/feature/dsp/htdocs)
  GSM.md                    — dokumentacja trybu

  # Narzędzia
  deploy.py                 — szybki redeploy dekodera + panelu na RPi
  update_html_css.py        — aktualizacja HTML/CSS na serwerze
  sdr_calibration.md        — notatki PPM per SDR/host
```

### Ścieżki po instalacji
- `/opt/openwebrx-tetra/` — binaria, skrypty dekodera, opcjonalny `offset.txt`
- `/var/lib/openwebrx/dmr_ep.key` — klucz RC4 dla trybu DMR EP (0600, poza repo)
- `/var/lib/openwebrx/gsm_cells.json` — baza znanych komórek GSM (wykrywanie anomalii)
- `/usr/lib/python3/dist-packages/` — patche integracyjne OpenWebRX+ (modes.py, feature.py, dsp.py, csdr/*, htdocs/*)

---

## EN — English

### Description
The plugin adds digital-mode decoding to OpenWebRX+: π/4-DQPSK demod, L1/L2/L3 decoding, ACELP voice playback and dedicated browser panels with network, call and terminal information.

### Features — TETRA TMO (`tetra`)
- π/4-DQPSK demodulation (GNURadio)
- TETRA protocol decoding L1/L2/L3 (osmo-tetra / tetra-rx, sq5bpf fork)
- ACELP speech decoding (ETSI codec) with in-browser playback
- AFC sourced from FLL port (radians/sample → Hz)
- Real-time web panel:
  - Network: MCC, MNC, LA, color code, DL/UL, encryption, network time (ETSI EN 300 392-2 §18.5.24)
  - Active calls (setup / connect / release / TX grant)
  - GSSI / ISSI with real-ISSI vs ESI-alias classification (3 categories)
  - Per-timeslot state (Traffic / Control / Common-Ctrl / Reserved / Unalloc) with TTL
  - Neighbour cell list (cell_id, carrier, DL freq, load)
  - Active SSI list (5 min TTL), MS Register events (LU Accept/Reject, attach/detach, auth)
  - SDS messages with protocol & delivery-status decoding
- Floating **TETRA Monitor** window with tabs:
  - event filters, G/SSI label editor, CSV export, remote and compact modes
  - **🔒 Encrypted Activity** — traffic on an encrypted network inferred from clear PDU headers (NDB1/NDB2; no decryption)
  - **📊 Statistics** — SSI ranking by airtime + hourly heatmap
  - **Recordings** — per-call WAV recording and download (opt-in, off by default)
  - **DMO** — events from the DMO decoder

### Features — TETRA DMO (`tetra-dmo`)
- Sync detector + burst extraction (DNB/DSB)
- L1 chain: descramble → Viterbi → CRC
- MAC PDU parser, TCH extraction and voice decoding (end-to-end)
- Events appear in the **DMO** tab of the TETRA Monitor window
- Note: voice confirmed on high-SNR recordings; low-SNR frames may still come out BFI

### Features — LoRa-APRS (`lora`)
- CSS (chirp spread spectrum) demodulation and LoRa frame decoding
- Secondary digimode in OWRX+ (typically 433.775 MHz, LoRa-APRS)

### Features — DMR EP (`dmr-ep`)
- DMR monitoring with **RC4 (Enhanced / Basic Privacy)** decryption via `dsd-fme`
- Key entry from the web UI (box at the top of the Receiver panel) → written to `/var/lib/openwebrx/dmr_ep.key` (0600) → decoder **hot-reload**, no restart
- "DMR EP" panel with a log of decoded/decrypted data (source, target, ALG/Key ID)
- **LRRP (GPS) positions** — pins on the OpenWebRX map + floating "Pozycje (LRRP)" window above the waterfall
- Validation: 24/24 PDUs decrypted on 148.512 MHz (ARS), 14 LRRP fixes correctly attributed to senders on 168.775/169.125 MHz; live path confirmed on air
- ⚠️ RC4 keys are **not** stored in the repo — `dmr_ep.key` is gitignored
- Details: [`tetra/DMR_EP.md`](tetra/DMR_EP.md)

### Features — LTE (`lte`)
- Click an LTE carrier on the waterfall → "📶 LTE — komórka" panel
- PSS/SSS → **PCI** and CFO (snaps to the 100 kHz raster), `pdsch_ue` (srsRAN 4G, `PDSCH_ALL_SI` patch) → **MIB**
- For 1.4 MHz cells also **SIB1–7** (pycrate): PLMN + operator, Cell ID → eNB/sector, TAC, band/EARFCN, RS power, LTE/UMTS/GSM neighbours with priorities
- Limitation: 1.92 MS/s IF (6 PRB) — wider cells give PCI + MIB only
- Details: [`tetra/LTE.md`](tetra/LTE.md)

### Features — GSM (`gsm`)
- Click a BCCH (C0) carrier → "📡 GSM — komórka" panel (gr-gsm + pycrate, ~8 s analysis)
- **BSIC** (SCH decoding), PLMN + operator, **CI**, **LAC**, access and reselection parameters (CRO), CCCH configuration, T3212, GPRS/EDGE (RAC, NMO)
- Cell allocation (SI1), GSM 900/1800, UMTS and LTE neighbours (SI2/2bis/2ter/2quater), load (paging/s), level and CFO from the FCCH tone
- **Anomaly detection (IMSI catcher)** — known-cell database (`/var/lib/openwebrx/gsm_cells.json`) and heuristics: test/unknown PLMN, empty neighbour list, high CRO, unknown LAC, ARFCN missing from neighbour lists, CI/BSIC change on the same ARFCN, no GPRS / no 3G/4G neighbours
- Details: [`tetra/GSM.md`](tetra/GSM.md)

### Signal chain
```
TETRA TMO:
IQ (36 kS/s) → tetra_demod.py (GNURadio π/4-DQPSK)
             → tetra-rx (osmo-tetra L1/L2/L3, sq5bpf fork)
             → tetra_decoder.py (TETMON parser + ACELP codec)
             → PCM audio (stdout) + JSON metadata (stderr)
             → WebSocket → tetra_panel.js (browser panel)

DMR EP:
IQ → FM discriminator → dsd-fme (RC4 EP/BP)
   → dmr_ep_decoder.py (meta + LRRP)
   → PCM audio + JSON → DMR EP panel + OWRX map

LTE:
IQ (1.92 MS/s) → lte_decoder.py: 3 s snapshot, PSS/SSS → PCI/CFO
               → pdsch_ue (srsRAN) → MIB + SI-RNTI (UDP) → pycrate SIB → JSON → LteMetaPanel

GSM:
IQ (500 kS/s) → gsm_decoder.py: 6 s snapshot → gr-gsm (C0 bursts + BCCH/CCCH)
              → SCH (BSIC), pycrate SI1–13, anomaly assessment → JSON → GsmMetaPanel
```

### Installation
```bash
git clone https://github.com/mbbrzoza/OpenWebRX-Tetra-Plugin.git
cd OpenWebRX-Tetra-Plugin/tetra

# Full install (deps, build, patch OpenWebRX+, frontend)
# — installs modes: tetra, tetra-dmo, lora
sudo bash install.sh

# Quick update of scripts and panel (no recompile)
sudo bash install.sh --update

# Verify installation
sudo bash install.sh --check

# Uninstall (restores .bak.pre-tetra backups)
sudo bash install.sh --uninstall
```

After installation the new demodulation modes appear in OpenWebRX+ — set them on the SDR profile or via a bookmark.

**DMR EP is installed separately** (not part of `install.sh`):
```bash
cp dmr_ep_decoder.py /opt/openwebrx-tetra/
cp csdr_chain_dmr_ep.py  {dist-packages}/csdr/chain/dmr_ep.py
cp csdr_module_dmr_ep.py {dist-packages}/csdr/module/dmr_ep.py
python3 patch_dmr_ep.py {dist-packages}      # registers the "DMR EP" mode
systemctl restart openwebrx
```
Requires `dsd-fme` in PATH (or `/usr/local/bin/dsd-fme`) plus `numpy`/`scipy`.

**LTE and GSM are installed separately**, following the same pattern (decoder → `/opt/openwebrx-tetra/`, chain/module → `{dist-packages}/csdr/`, `patch_lte.py` / `patch_gsm.py`). Full instructions: [`tetra/LTE.md`](tetra/LTE.md), [`tetra/GSM.md`](tetra/GSM.md).

### Requirements
- OpenWebRX+ v1.2.x
- Debian / Raspberry Pi OS (aarch64 or x86_64)
- GNURadio + osmo-tetra (installed by `install.sh`)
- `dsd-fme`, `numpy`, `scipy` — DMR EP mode only
- `numpy`, `scipy`, `pycrate`, srsRAN 4G (`build_lte_pdsch.sh`) — LTE mode
- `gr-gsm` (apt), `pycrate` — GSM mode
- Internet access for the first install

### Server paths after install
- `/opt/openwebrx-tetra/` — decoder binaries, scripts, optional `offset.txt`
- `/var/lib/openwebrx/dmr_ep.key` — RC4 key for DMR EP mode (0600, kept out of the repo)
- `/var/lib/openwebrx/gsm_cells.json` — known GSM cell database (anomaly detection)
- `/usr/lib/python3/dist-packages/` — OpenWebRX+ integration patches (modes.py, feature.py, dsp.py, csdr/*, htdocs/*)

---

## License
Open source for amateur radio use.

73 de SP8MB
