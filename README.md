# OpenWebRX+ TETRA Plugin

**Author: SP8MB (mbbrzoza)**

TETRA (Terrestrial Trunked Radio) decoder plugin for [OpenWebRX+](https://github.com/luarvique/openwebrx). Adds full signaling extraction, voice decoding and a real-time control panel to the OpenWebRX+ web UI.

---

## PL — Polski

### Opis
Wtyczka rozszerza OpenWebRX+ o tryb **TETRA**: demodulację π/4-DQPSK, dekodowanie warstw L1/L2/L3, odtwarzanie mowy w kodeku ACELP oraz dedykowany panel WWW z informacjami o sieci, połączeniach i terminalach.

### Funkcje
- Demodulacja π/4-DQPSK (GNURadio)
- Dekodowanie protokołu L1/L2/L3 (osmo-tetra / tetra-rx)
- Dekodowanie mowy ACELP (kodek ETSI) — odtwarzanie w przeglądarce
- AFC z portu FLL (radiany/sample → Hz)
- Panel WWW w czasie rzeczywistym:
  - Sieć: MCC, MNC, LA, Color Code, DL/UL, status szyfrowania, czas sieci
  - Aktywne połączenia (call setup / connect / release / TX grant)
  - GSSI / ISSI z rozróżnieniem prawdziwego ISSI od aliasów ESI
  - Stan szczelin czasowych (Traffic / Control / Common-Ctrl / Reserved / Unalloc) z TTL
  - Lista sąsiednich komórek (cell_id, carrier, DL freq, load)
  - Aktywne SSI (5 min TTL), zdarzenia MS Register (LU Accept/Reject, attach/detach, auth)
  - Wiadomości SDS z dekodowaniem protokołu i statusu doręczenia
  - Pływające okno typu TTT z filtrami zdarzeń, edytorem etykiet G/SSI, eksportem CSV, trybem zdalnym i kompaktowym

### Łańcuch sygnałów
```
IQ (36 kS/s) → tetra_demod.py (GNURadio π/4-DQPSK)
             → tetra-rx (osmo-tetra L1/L2/L3, fork sq5bpf)
             → tetra_decoder.py (parser TETMON + kodek ACELP)
             → PCM audio (stdout) + JSON metadane (stderr)
             → WebSocket → tetra_panel.js (panel w przeglądarce)
```

### Instalacja
```bash
git clone https://github.com/mbbrzoza/OpenWebRX-Tetra-Plugin.git
cd OpenWebRX-Tetra-Plugin/tetra

# Pełna instalacja (deps, kompilacja, patchowanie OpenWebRX+, frontend)
sudo bash install.sh

# Szybka aktualizacja (skrypty + panel, bez rekompilacji)
sudo bash install.sh --update

# Sprawdzenie statusu
sudo bash install.sh --check

# Odinstalowanie (przywraca kopie .bak.pre-tetra)
sudo bash install.sh --uninstall
```

Po instalacji w OpenWebRX+ pojawia się nowy tryb demodulacji **TETRA** — wystarczy ustawić go na zakładce SDR profile albo wybrać przez bookmark.

### Wymagania
- OpenWebRX+ v1.2.x
- Debian/Raspberry Pi OS (aarch64 lub x86_64)
- GNURadio + osmo-tetra (instalowane przez `install.sh`)
- Dostęp do internetu przy pierwszej instalacji

### Pliki
```
tetra/
  install.sh              — instalator (install/update/uninstall/check, kopie .bak.pre-tetra)
  tetra_decoder.py        — główny dekoder: orkiestracja pipeline'u + parser TETMON + meta events
  tetra_demod.py          — demodulator DQPSK (GNURadio) z opcjonalnym pre-FLL rotatorem
  csdr_module_tetra.py    — moduł CSDR (PopenModule)
  csdr_chain_tetra.py     — łańcuch CSDR (integracja z OpenWebRX+)
  tetra_panel.js          — frontend (TetraMetaPanel + okno TTT-style)
  tetra_panel.html        — szablon HTML panelu
  deploy.py               — szybki redeploy dekodera + panelu na RPi
  update_html_css.py      — aktualizacja HTML/CSS na serwerze
```

### Ścieżki po instalacji
- `/opt/openwebrx-tetra/` — binaria, skrypty dekodera, opcjonalny `offset.txt`
- `/usr/lib/python3/dist-packages/` — patche integracyjne OpenWebRX+ (modes.py, feature.py, dsp.py, csdr/*, htdocs/*)

---

## EN — English

### Description
Plugin adds a **TETRA** demodulation mode to OpenWebRX+: π/4-DQPSK demod, L1/L2/L3 decoding, ACELP voice playback and a dedicated browser panel with network, call and terminal information.

### Features
- π/4-DQPSK demodulation (GNURadio)
- TETRA protocol decoding L1/L2/L3 (osmo-tetra / tetra-rx, sq5bpf fork)
- ACELP speech decoding (ETSI codec) with in-browser playback
- AFC sourced from FLL port (radians/sample → Hz)
- Real-time web panel:
  - Network: MCC, MNC, LA, color code, DL/UL, encryption, network time
  - Active calls (setup / connect / release / TX grant)
  - GSSI / ISSI with real-ISSI vs ESI-alias classification
  - Per-timeslot state (Traffic / Control / Common-Ctrl / Reserved / Unalloc) with TTL
  - Neighbour cell list (cell_id, carrier, DL freq, load)
  - Active SSI list (5 min TTL), MS Register events (LU Accept/Reject, attach/detach, auth)
  - SDS messages with protocol & delivery-status decoding
  - Floating TTT-style window with event filters, G/SSI label editor, CSV export, remote and compact modes

### Signal chain
```
IQ (36 kS/s) → tetra_demod.py (GNURadio π/4-DQPSK)
             → tetra-rx (osmo-tetra L1/L2/L3, sq5bpf fork)
             → tetra_decoder.py (TETMON parser + ACELP codec)
             → PCM audio (stdout) + JSON metadata (stderr)
             → WebSocket → tetra_panel.js (browser panel)
```

### Installation
```bash
git clone https://github.com/mbbrzoza/OpenWebRX-Tetra-Plugin.git
cd OpenWebRX-Tetra-Plugin/tetra

# Full install (deps, build, patch OpenWebRX+, frontend)
sudo bash install.sh

# Quick update of scripts and panel (no recompile)
sudo bash install.sh --update

# Verify installation
sudo bash install.sh --check

# Uninstall (restores .bak.pre-tetra backups)
sudo bash install.sh --uninstall
```

After installation a new **TETRA** demodulation mode appears in OpenWebRX+ — set it on the SDR profile or via a bookmark.

### Requirements
- OpenWebRX+ v1.2.x
- Debian / Raspberry Pi OS (aarch64 or x86_64)
- GNURadio + osmo-tetra (installed by `install.sh`)
- Internet access for the first install

### Files
```
tetra/
  install.sh              — installer (install/update/uninstall/check, .bak.pre-tetra backups)
  tetra_decoder.py        — main decoder: pipeline orchestrator + TETMON parser + meta events
  tetra_demod.py          — DQPSK demodulator (GNURadio) with optional pre-FLL rotator
  csdr_module_tetra.py    — CSDR module (PopenModule)
  csdr_chain_tetra.py     — CSDR chain (OpenWebRX+ integration)
  tetra_panel.js          — frontend (TetraMetaPanel + TTT-style window)
  tetra_panel.html        — panel HTML template
  deploy.py               — fast redeploy of decoder + panel to RPi
  update_html_css.py      — server-side HTML/CSS update
```

### Server paths after install
- `/opt/openwebrx-tetra/` — decoder binaries, scripts, optional `offset.txt`
- `/usr/lib/python3/dist-packages/` — OpenWebRX+ integration patches (modes.py, feature.py, dsp.py, csdr/*, htdocs/*)

---

## License
Open source for amateur radio use.

73 de SP8MB
