# Tryb "DMR EP" — DMR z deszyfrowaniem RC4 (Enhanced/Basic Privacy)

Prototyp dodający do odsłuchu DMR w OpenWebRX+ **wpisanie klucza RC4** i deszyfrowanie
ruchu. Domyślny dekoder DMR w OWRX+ (digiham) **nie deszyfruje**; ten tryb kieruje sygnał
przez **dsd-fme**, który natywnie odszyfrowuje DMR Enhanced Privacy (ALG 01 / RC4‑40),
Basic Privacy i inne, gdy poda się klucz.

Autor: SP8MB. Odsłuch tylko ruchu, do którego masz uprawnienia (własna sieć / posiadany klucz).

## Pliki
| plik | rola | miejsce docelowe |
|---|---|---|
| `dmr_ep_decoder.py` | wrapper dsd-fme: IQ→dyskryminator→dsd-fme(`-1 klucz`)→PCM + meta JSON | `/opt/openwebrx-tetra/` |
| `csdr_module_dmr_ep.py` | `DmrEpDecoderModule` (PopenModule) | `{dist-packages}/csdr/module/dmr_ep.py` |
| `csdr_chain_dmr_ep.py` | `DmrEp` chain (IF 48k, audio 8k, MetaProvider) | `{dist-packages}/csdr/chain/dmr_ep.py` |
| `patch_dmr_ep.py` | patch modes.py/dsp.py/feature.py (rejestracja trybu) | uruchamiany raz przy instalacji |
| `dmr_ep.key` | klucz(e) RC4 hex, **gitignored** | `/opt/openwebrx-tetra/dmr_ep.key` |

## Wpisanie klucza
- **Panel WWW** (docelowe): box „🔑 Klucze DMR EP" na górze panelu *Receiver* (pod wyborem profilu, nad „Modes") — wpisz klucze hex
  (jeden na linię) i „Zapisz". JS wysyła `ws.send({type:"dmr_ep_key",keys})`, handler w
  `connection.py` sanityzuje i zapisuje `/var/lib/openwebrx/dmr_ep.key` (0600, właściciel
  openwebrx). Dekoder **hot-reloaduje** się (`os.execv`) bez restartu OWRX. Pliki:
  `dmr_ep_keys.js` (→ `htdocs/dmr_ep_keys.js`), `patch_dmr_ep_ui.py` (patch index.html + connection.py).
- Alternatywnie (serwerowo): `--key <hex>` (CLI), env `DMR_RC4_KEY`, lub plik klucza.
  Priorytet: `--key` > `DMR_RC4_KEY` > `/var/lib/openwebrx/dmr_ep.key` > `dmr_ep.key` obok dekodera.

Bez klucza tryb działa jak zwykły DMR (ruch zaszyfrowany zostaje oznaczony jako `encrypted`).

## Okno pozycji (LRRP) — pływające okno nad waterfallem
`dmr_ep_panel.js` (`DmrEpMetaPanel`) dostaje te same zdarzenia `lrrp` co mapa i pokazuje je w
**pływającym, przeciąganym oknie** nad widmem (wzorzec jak okno TETRA: `position:fixed`,
drag za pasek tytułu, przycisk ✕, `z-index:10000`). Tabela `radio → lat,lon (link do OSM) →
prędkość → czas`, aktualizowana w miejscu (jedna linia na radio). Okno **auto-otwiera się** przy
pierwszym fixie; ponowne otwarcie przyciskiem „📍 Okno pozycji (LRRP)" w panelu DMR EP. Okno i
przycisk wstrzykiwane dynamicznie w JS (bez zmian index.html), więc deploy = re-patch
`lib/MetaPanel.js` (restore `.bak.dmrep_panel` + `patch_dmr_ep_panel.py`); `compiled/receiver.js`
generowany z `lib/*.js` → Ctrl+Shift+R w przeglądarce, **bez restartu openwebrx**.

## Panel „DMR EP" (wyświetlanie zdekodowanych danych)
Dedykowany panel meta pokazuje przewijany log zdekodowanych/odszyfrowanych ramek (source→target,
`🔓 odszyfrowano RC4 ALGxx/KIDxx`). Pliki: `dmr_ep_panel.js` (`DmrEpMetaPanel`, → wstrzyknięty do
`lib/MetaPanel.js`, `MetaPanel.types["dmrep"]`), `patch_dmr_ep_panel.py` (patch MetaPanel.js +
DemodulatorPanel.js `dmr-ep→dmrep` + panel div w index.html). Dekoder emituje `protocol="DMR"` →
wszystkie panele meta dostają `update()`; panel dmrep pokazywany dla trybu dmr-ep. `compiled/receiver.js`
budowany z `lib/*.js` → patch dociera bez restartu (Ctrl+Shift+R w przeglądarce).

## Łańcuch (live w OWRX+)
```
IF IQ 48k (complex) → dyskryminator FM (numpy) → TCP 127.0.0.1:7355
   → dsd-fme -fs -1 <klucz> -i tcp -o udp:127.0.0.1:23456
   → PCM 8k na stdout  (audio OWRX)
   → zdarzenia dsd-fme → meta JSON na stderr → MetaParser → panel
```
Meta w formacie zgodnym z panelem: `{"protocol":"DMR","slot":N,"source":..,"target":..}`
plus rozszerzenie krypto `{"encrypted":true,"decrypted":true,"alg":"01","keyid":"28","cipher":"RC4","mi":..}`
plus pozycja LRRP `{"lrrp":true,"source":"1030","lat":51.2212,"lon":22.6949,"radius":..,"speed":..}`.

## Mapa — pozycje LRRP (GPS) na mapie OpenWebRX
Ruch danych MOTOTRBO (MNIS) niesie **LRRP** — raporty pozycji GPS. dsd-fme dekoduje je do
`Lat/Lon` (jawne, jeśli sieć nieszyfrowana; albo po odszyfrowaniu kluczem RC4). Dekoder
składa blok `Lat`/`Radius`/`Speed` i emituje meta `lrrp`, wiążąc pozycję z nadawcą z
**Data Header danej szczeliny** (Preamble CSBK po drodze NIE nadpisuje źródła; emisja na
linii `Slot N - Multi Block PDU Message`). `csdr_module_dmr_ep.py._pushMap()` woła
`owrx.map.Map.getSharedInstance().updateLocation("DMR-<src>", LatLngLocation(lat,lon), "DMR")`
— pin pojawia się na mapie OWRX (klucz `DMR-<id_radia>`, tryb „DMR"), widoczny dla wszystkich
klientów mapy, z auto-wygasaniem wg `map_position_retention_time`. Działa **na żywo** podczas
odsłuchu trybu DMR EP na danym kanale; niezależne od otwarcia panelu (mapa dostaje pin nawet
bez `metaWriter`).

## Instalacja na serwerze (nietestowana na produkcji w tym prototypie)
```bash
cp dmr_ep_decoder.py /opt/openwebrx-tetra/
cp csdr_chain_dmr_ep.py  {dist-packages}/csdr/chain/dmr_ep.py
cp csdr_module_dmr_ep.py {dist-packages}/csdr/module/dmr_ep.py
echo A1B2C3D4E5 > /opt/openwebrx-tetra/dmr_ep.key
python3 patch_dmr_ep.py {dist-packages}      # rejestruje tryb "DMR EP"
systemctl restart openwebrx
```
Wymaga `dsd-fme` w PATH (lub `/usr/local/bin/dsd-fme`) oraz `numpy`/`scipy`.

## Walidacja (2026-07-16, nagranie 148.512 MHz — sieć SP8MB)
`dmr_ep_decoder.py --selftest disc_148512.wav` (dyskryminator 48k):

| | bez klucza | z kluczem `A1B2C3D4E5` |
|---|---|---|
| PDU odszyfrowane | 0 | **24 / 24** |
| PDU nieodszyfrowane (encrypted) | 48 | **0** |
| źródło/cel wyodrębnione | 76 | 76 |

Meta odszyfrowane: `ALG 01 / Key ID 0x28 / RC4`, ruch = ARS (radia 33000–56000 ↔ hub 64250).
Zgodne z wcześniejszym złamaniem klucza (projekt `dmr_crack`).

## Walidacja LRRP→mapa (2026-07-16, nagranie 168.775 + 169.125 MHz — SP8MB)
`dmr_ep_decoder.py --selftest disc_<f>_noinv.wav` (sieci **nieszyfrowane**, MNIS data):

| kanał | CC | fixy LRRP | przypisanie źródło→pozycja |
|---|---|---|---|
| 168.775 | 05 | 11 | 1020→(51.2230, 22.6162)×5, 1030→(51.2212, 22.6949)×6 |
| 169.125 | 06 | 3 | 1040→(51.2320, 22.9264)×3 |

Wszystkie fixy poprawnie przypisane do nadawcy (rejon Świdnik/Lublin — zgodny z lokalizacją RX).

## Przetestowane vs do zrobienia
- ✅ Deszyfrowanie + ekstrakcja meta (rdzeń) — na nagraniu 148.512.
- ✅ Ekstrakcja LRRP + piny na mapie OWRX — walidacja na 168.775/169.125 (14 fixów, przypisanie źródeł OK).
- ✅ Patcher OWRX (modes/dsp/feature) — na atrapach, idempotentny, walidacja py_compile.
- ⏳ Ścieżka live w OWRX (IQ→dyskryminator→TCP/UDP dsd-fme) — wymaga testu na serwerze z RTL.
- ⏳ Wizualne potwierdzenie pinów na mapie SP8MB — wymaga deploya + odsłuchu DMR EP na żywo (dongiel).
- ⏳ Fonia EP (AMBE) — nagranie 148.512 to dane (ARS); ten sam klucz odszyfruje fonię, jeśli
  używa tego samego ALG 01/KID 0x28.
- ⏳ Docelowe pole klucza w ustawieniach OWRX (na razie plik/env).
