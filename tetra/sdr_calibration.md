# SDR Calibration — PPM Offsets

## SDRplay RSP1 on brzoza@192.168.12.22 (User PC)

**PPM = +2.31** (potwierdzone na 2 nagraniach niezależnie: carrier offset +1 kHz na 433.400 i 438.375 MHz, data: 2026-05-26)

### Expected carrier offset on TETRA frequencies (Hz)

| Frequency (MHz) | Offset (Hz) |
|---|---|
| 380 | +878 |
| 410 | +947 |
| 433.400 | +1001 |
| 433.450 | +1001 |
| 433.500 | +1001 |
| 438.375 | +1013 |
| 440 | +1016 |
| 460 | +1063 |

Wartości obliczone jako `freq_hz × ppm × 1e-6`.

### Użycie

Dla OWRX+ z TETRA plugin na hoście używającym tego SDRplay:

```bash
echo "1000" > /opt/openwebrx-tetra/offset.txt
```

Lub przez env var:

```bash
export TETRA_OFFSET_HZ=1000
```

Wartość ~1000 Hz pasuje do całego pasma 380-470 MHz (PPM offset jest ~1 kHz w
tym zakresie z dokładnością ~13 Hz, co mieści się w FLL capture range ±25 kHz).

### RTL-SDR Blog V4 na 192.168.11.22 (zadeklarowany TCXO)

**PPM = -14.45** (po warmup ~6-10s, 2026-05-28 wieczór)

3 niezależne pomiary 5s na TETRA BS 438.375 MHz, gain 30:
- pomiar 1 (crystal zimny): -7.49 ppm
- pomiar 2 (~3s później): -14.45 ppm
- pomiar 3 (~3s później): -14.46 ppm

→ Po warmup crystal stabilizuje na **-14.45/-14.46** (różnica <0.01 ppm).

| Frequency (MHz) | Offset (Hz) |
|---|---|
| 380 | -5491 |
| 410 | -5925 |
| 433.400 | -6263 |
| 433.450 | -6263 |
| 438.375 | -6335 |
| 460 | -6647 |

Uwaga: -14.45 ppm to **dużo dla TCXO** (typowo ±1-2 ppm). Możliwe że klon
RTL V4 ma zwykły XO mimo deklaracji TCXO. Niezależnie — PPM stabilny po
warmup, wiarygodny do użycia.

**KRYTYCZNE**: pierwsze 10-30 sekund po starcie RTL crystal drifti silnie
(-7 → -14.5 ppm w ciągu kilku sekund). Dla precyzyjnej kalibracji odczekać
30s przed pomiarem.

Dla OWRX z tym RTL:
```bash
echo "-6263" > /opt/openwebrx-tetra/offset.txt   # dla 433.400 (po warmup)
# lub w OWRX settings: rtlsdr.ppm=-14
```

Wymagana sekwencja przed użyciem RTL:
```bash
sudo modprobe -rf dvb_usb_rtl28xxu rtl2832_sdr rtl2832
# lub permanent: /etc/modprobe.d/blacklist-rtl.conf
```

Wcześniejszy pomiar tej sesji innym RTL wykazał PPM=-74 — to był inny
exemplar bez TCXO. RTL V4 znacznie bliżej standardu mimo wciąż dużego -14.

### SDRplay RSP1 na 192.168.11.22 — niezkalibrowane

W sesji 2026-05-28 próbowano kalibrować SDRplay przez libmirisdr na 438.375 MHz:
- Driver mirisdr SIGSEGV po ~25-50 ms streamingu (przerywa nagranie)
- Z częściowych danych: spectrum zanieczyszczone artefaktami (peaki ±3 kHz
  symetryczne to harmonic szyny zasilania / crystal mirisdr, NIE carrier BS)
- Dwa niezależne pomiary dały PPM +57 i +12 (różnica 19 kHz/s = niemożliwe
  dla stabilnego SDR) → driver buggy
- `|z|max=7` (vs typical 1.4 normalized) → saturation indicator
- **Wniosek**: SDRplay z mirisdr nie nadaje się do kalibracji 240-470 MHz.
  Potrzebne SDRplay native API (proprietary, install z sdrplay.com).
  Alternatywa: użyć SDRplay na 64-108 (FM broadcast) lub 470-960 (DVB-T).

### Notatki

- Memory `[[ppm-sdrplay-12-22]]` ma timestamp 2026-05-26 — drift termiczny
  może być, sprawdzić ponownie na 438.375 BS przed produkcyjnym wpisem
- FLL bandwith ±25 kHz, czyli błąd PPM do ±60 ppm (na 433 MHz) jest tolerowany
- offset.txt zostaje stosowany przez tetra_demod.py jako pre-FLL rotator
- Dla różnych SDR per host: każdy host powinien mieć swój offset.txt
