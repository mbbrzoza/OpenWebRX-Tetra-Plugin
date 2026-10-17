# Tryb „GSM” — identyfikacja komórki GSM po kliknięciu nośnej BCCH

Wybierz tryb **GSM** i kliknij nośną BCCH (C0) na wodospadzie albo zakładkę „GSM &lt;ARFCN&gt;”.
Panel „📡 GSM — komórka” pokazuje: ARFCN/pasmo, sieć (PLMN + operator), CI, LAC, BSIC (NCC-BCC),
parametry dostępu (blokady, RXLEV min, moc MS), konfigurację CCCH, T3212, GPRS/EDGE (RAC, NMO),
alokację komórki (SI1), sąsiadów GSM 900/1800, UMTS i LTE (SI2/2bis/2ter/2quater), obciążenie
(paging/s, Immediate Assignment/s), poziom i CFO (z tonu FCCH).
Nośne TCH (bez BCCH) dają „brak FCCH”. Klik nie musi być dokładny — dociąganie do rastra 200 kHz.

## Wykrywanie anomalii (IMSI-catcher)

Każda zdekodowana komórka trafia do bazy `/var/lib/openwebrx/gsm_cells.json` (`--db`; zapis tylko przy
nowej/zmienionej komórce lub co 10 min). Ocena jest liczona względem komórek tej samej sieci radiowej
(T-Mobile i Orange = wspólny RAN NetWorkS!), z pominięciem komórek już uznanych za podejrzane:

| poziom | heurystyka |
|---|---|
| ⚠ | testowy PLMN (MCC 001/002/999/901) lub nieznany operator w MCC 260 |
| ⚠ | pusta lista sąsiadów BA; brak SI2 przy pełnym odczycie SI1/3/4 (inaczej ℹ „niepełny odczyt”) |
| ⚠ | CRO ≥ 40 dB (komórka „wymusza” reselekcję; sieci używają do ~30 dB, T-Mobile 26 dB) |
| ⚠ (ℹ przy < 3 znanych) | LAC niespotykany w innych komórkach sieci; ARFCN nieobecny na listach sąsiadów komórek tego samego operatora |
| ⚠ | ten sam ARFCN + BSIC z innym CI/LAC (podmiana); zmiana BSIC znanej komórki |
| ℹ | brak GPRS; brak sąsiadów 3G/4G; sieć zagraniczna |

Panel: ≥ 2 × ⚠ → „PODEJRZANA (możliwy IMSI-catcher)”. To heurystyki — pojedyncze ostrzeżenie zdarza się
w normalnej sieci (np. nowa stacja, pierwsza komórka nowego LAC).

## Pliki

| plik | cel |
|---|---|
| `gsm_decoder.py` | → `/opt/openwebrx-tetra/` — migawka 6 s IQ @ 500 kS/s, gr-gsm (bursty + BCCH/CCCH), SCH → BSIC, pycrate → JSON na stderr |
| `csdr_chain_gsm.py` | → `{OWX}/csdr/chain/gsm.py` — łańcuch `Gsm` (IF 500 kS/s, DialFrequencyReceiver) |
| `csdr_module_gsm.py` | → `{OWX}/csdr/module/gsm.py` — PopenModule; przestrojenie = plik ctl w /dev/shm + SIGUSR1 |
| `gsm_panel.js` | wstrzykiwany do `htdocs/lib/MetaPanel.js` jako `GsmMetaPanel` (types `gsm`) |
| `patch_gsm.py` | modes.py / dsp.py / feature.py / MetaPanel.js / index.html (backup `.bak.gsm`, idempotentny) |

## Instalacja

```sh
sudo apt install gr-gsm                             # Bookworm: 1.0.0~20220727
# pycrate jak dla LTE (LTE.md); lte_decoder.py obok = opis pasm sąsiadów LTE (opcjonalnie)
sudo cp gsm_decoder.py /opt/openwebrx-tetra/
sudo cp csdr_chain_gsm.py  /usr/lib/python3/dist-packages/csdr/chain/gsm.py
sudo cp csdr_module_gsm.py /usr/lib/python3/dist-packages/csdr/module/gsm.py
sudo python3 patch_gsm.py /usr/lib/python3/dist-packages
sudo systemctl restart openwebrx                    # w przeglądarce Ctrl+Shift+R
```

## Test bez radia

```sh
python3 gsm_decoder.py --once --freq 937400000 < nagranie_500k.cfile
```

Sprawdzone 2026-10-17 (nagranie RTL .110, ARFCN 12): Plus 260-01, LAC 11012, CI 11241, BSIC 2-0,
SI1/2/2ter/2quater/3/4/13, EDGE, sąsiad UMTS UARFCN 3000, CFO −1,7 kHz; analiza ~1,5 s na Pi5.
**Uwaga:** `--freq` musi być środkiem nagrania — przy złej wartości dekoder przesuwa sygnał o różnicę.
