# TETRA Decoder Module for OpenWebRX+
**Author: SP8MB**

---

## PL - Polski

### Opis
Modul dekodera TETRA dla OpenWebRX+ umozliwiajacy odbiur i dekodowanie sygnalow cyfrowej lacznosci radiowej TETRA (Terrestrial Trunked Radio) w przegladarce internetowej.

### Funkcje
- Demodulacja pi/4-DQPSK (GNURadio)
- Dekodowanie protokolu TETRA L1/L2/L3 (osmo-tetra / tetra-rx)
- Dekodowanie mowy ACELP (kodek ETSI)
- Panel informacyjny w czasie rzeczywistym:
  - Informacje o sieci (MCC, MNC, LA, Color Code)
  - Czestotliwosci DL/UL
  - Status szyfrowania (TEA1/2/3)
  - Aktywne polaczenia (GSSI, ISSI, typ polaczenia, Call ID)
  - Korekta czestotliwosci AFC
  - Szybkosc burstow (burst/s)
  - Status szczelin czasowych (timeslots 1-4)

### Lancuch sygnalow
```
IQ (36 kS/s) -> tetra_demod.py (GNURadio DQPSK)
             -> tetra-rx (osmo-tetra L1/L2/L3)
             -> tetra_decoder.py (parser TETMON + kodek ACELP)
             -> PCM audio (stdout) + JSON metadane (stderr)
             -> WebSocket -> tetra_panel.js (panel w przegladarce)
```

### Instalacja
```bash
# Pelna instalacja (kompilacja + patchowanie OpenWebRX+)
sudo bash install.sh

# Szybka aktualizacja skryptow i panelu (bez rekompilacji)
sudo bash install.sh --update

# Sprawdzenie statusu instalacji
sudo bash install.sh --check

# Odinstalowanie modulu
sudo bash install.sh --uninstall
```

### Wymagania
- OpenWebRX+ v1.2.x
- Raspberry Pi / Debian (ARM64 lub x86_64)
- Dostep do internetu (przy pierwszej instalacji)

### Struktura plikow
```
tetra/
  install.sh              - Skrypt instalacyjny (install/update/uninstall/check)
  tetra_decoder.py        - Glowny dekoder - orkiestracja pipeline'u
  tetra_demod.py          - Demodulator DQPSK (GNURadio)
  csdr_module_tetra.py    - Modul CSDR (PopenModule wrapper)
  csdr_chain_tetra.py     - Lancuch CSDR (integracja z OpenWebRX+)
  tetra_panel.js          - Panel frontendowy (TetraMetaPanel)
  tetra_panel.html        - Szablon HTML panelu
  deploy.py               - Skrypt szybkiego wdrozenia na RPi
  update_html_css.py      - Aktualizacja HTML/CSS na serwerze
```

### Sciezki na serwerze
- `/opt/openwebrx-tetra/` - binaria i skrypty dekodera
- `/usr/lib/python3/dist-packages/` - pliki integracyjne OpenWebRX+

---

## EN - English

### Description
TETRA decoder module for OpenWebRX+ enabling reception and decoding of TETRA (Terrestrial Trunked Radio) digital radio signals in a web browser.

### Features
- pi/4-DQPSK demodulation (GNURadio)
- TETRA protocol decoding L1/L2/L3 (osmo-tetra / tetra-rx)
- ACELP speech decoding (ETSI codec)
- Real-time information panel:
  - Network info (MCC, MNC, LA, Color Code)
  - DL/UL frequencies
  - Encryption status (TEA1/2/3)
  - Active calls (GSSI, ISSI, call type, Call ID)
  - AFC frequency correction
  - Burst rate (burst/s)
  - Timeslot status (slots 1-4)

### Signal chain
```
IQ (36 kS/s) -> tetra_demod.py (GNURadio DQPSK)
             -> tetra-rx (osmo-tetra L1/L2/L3)
             -> tetra_decoder.py (TETMON parser + ACELP codec)
             -> PCM audio (stdout) + JSON metadata (stderr)
             -> WebSocket -> tetra_panel.js (browser panel)
```

### Installation
```bash
# Full install (compile + patch OpenWebRX+)
sudo bash install.sh

# Quick update of scripts and panel (no recompilation)
sudo bash install.sh --update

# Check installation status
sudo bash install.sh --check

# Uninstall module
sudo bash install.sh --uninstall
```

### Requirements
- OpenWebRX+ v1.2.x
- Raspberry Pi / Debian (ARM64 or x86_64)
- Internet access (first install only)

### File structure
```
tetra/
  install.sh              - Installer script (install/update/uninstall/check)
  tetra_decoder.py        - Main decoder - pipeline orchestrator
  tetra_demod.py          - DQPSK demodulator (GNURadio)
  csdr_module_tetra.py    - CSDR module (PopenModule wrapper)
  csdr_chain_tetra.py     - CSDR chain (OpenWebRX+ integration)
  tetra_panel.js          - Frontend panel (TetraMetaPanel)
  tetra_panel.html        - Panel HTML template
  deploy.py               - Quick deployment script for RPi
  update_html_css.py      - HTML/CSS update on server
```

### Server paths
- `/opt/openwebrx-tetra/` - decoder binaries and scripts
- `/usr/lib/python3/dist-packages/` - OpenWebRX+ integration files

---

## License
Open source for amateur radio use.

73 de SP8MB
