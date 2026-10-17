# Tryb „LTE” — identyfikacja komórki LTE po kliknięciu nośnej

Wybierz tryb **LTE** i kliknij środek nośnej LTE na wodospadzie. Panel „📶 LTE — komórka” pokazuje:
PCI, MIB (szerokość, porty TX), a dla komórek **1,4 MHz** także SIB1–7: sieć (PLMN + nazwa operatora),
Cell ID (eNB/sektor), TAC, pasmo i EARFCN, moc RS, sąsiadów LTE/UMTS/GSM z priorytetami, SNR i CFO.
Kliknięcie innej nośnej = nowa analiza; ta sama komórka jest odświeżana co 60 s.

Ograniczenie: IF to 1,92 MS/s (6 PRB). PSS/SSS/PBCH leżą w środkowych 6 PRB każdej komórki, więc
PCI i MIB działają dla dowolnej szerokości; SIB-y (PDSCH w całym kanale) tylko dla 1,4 MHz.
Klik nie musi być dokładny — dekoder dociąga do rastra LTE 100 kHz i szuka CFO ±12,5 kHz.

## Pliki

| plik | cel |
|---|---|
| `lte_decoder.py` | → `/opt/openwebrx-tetra/` — migawka 3 s IQ, PSS/SSS → PCI/CFO, `pdsch_ue` → MIB + SIB (UDP), pycrate → JSON na stderr |
| `csdr_chain_lte.py` | → `{OWX}/csdr/chain/lte.py` — łańcuch `Lte` (IF 1,92 MS/s, DialFrequencyReceiver) |
| `csdr_module_lte.py` | → `{OWX}/csdr/module/lte.py` — PopenModule; przestrojenie = plik ctl w /dev/shm + SIGUSR1 |
| `lte_panel.js` | wstrzykiwany do `htdocs/lib/MetaPanel.js` jako `LteMetaPanel` (types `lte`) |
| `patch_lte.py` | modes.py / dsp.py / feature.py / MetaPanel.js / index.html (backup `.bak.lte`, idempotentny) |
| `build_lte_pdsch.sh` | buduje `pdsch_ue` (srsRAN 4G, tylko PHY) z łatką `PDSCH_ALL_SI` → `/opt/openwebrx-tetra/bin/pdsch_ue` |

## Instalacja (RPi z OWRX+ i pluginem TETRA)

```sh
sudo JOBS=2 bash build_lte_pdsch.sh                 # ~1 h na Pi5 przy -j2 obok OWRX
# pycrate (czysto pythonowy; na Pi bez pip wystarczy rozpakować wheel):
sudo python3 -m zipfile -e pycrate-0.8.1-py2.py3-none-any.whl /usr/local/lib/python3.11/dist-packages
sudo cp lte_decoder.py /opt/openwebrx-tetra/
sudo cp csdr_chain_lte.py  /usr/lib/python3/dist-packages/csdr/chain/lte.py
sudo cp csdr_module_lte.py /usr/lib/python3/dist-packages/csdr/module/lte.py
sudo python3 patch_lte.py /usr/lib/python3/dist-packages
sudo systemctl restart openwebrx                    # w przeglądarce Ctrl+Shift+R
```

Bez `pdsch_ue` tryb działa w okrojonej wersji (tylko PCI/CFO). Bez pycrate — PCI + MIB.
Po aktualizacji OWRX (nadpisuje owrx/ i htdocs/) powtórzyć kopiowanie chain/module i `patch_lte.py`.

## Test bez radia

```sh
head -c $((8*1920000*4)) nagranie_1920k.cf32 | python3 lte_decoder.py --once --freq 423700000
```

Sprawdzone 2026-10-17 na 423,7 MHz (RTL .110): PCI 71, Plus 260-01, band 87, EARFCN 69825,
eNB 110200/3, TAC 15200; także przy kliknięciu ±30–40 kHz od środka; szum → „brak komórki”.

## Uwagi techniczne

- `pdsch_ue` w trybie plikowym nie szuka PCI ani granicy podramki — robi to `lte_decoder.py`
  (PSS z hipotezami CFO, SSS z rozróżnieniem podramki 0/5) i podaje `-c PCI -P porty`, plik od podramki 0.
- `-P` (liczba portów) musi się zgadzać, inaczej MIB się nie dekoduje — dekoder próbuje 2, 1, 4.
- Bez łatki `PDSCH_ALL_SI` `pdsch_ue` dekoduje SI-RNTI tylko w podramce 5 (sam SIB1).
- `-r` w `pdsch_ue` jest szesnastkowe (`-r 65535` ≠ SI-RNTI 0xFFFF).
