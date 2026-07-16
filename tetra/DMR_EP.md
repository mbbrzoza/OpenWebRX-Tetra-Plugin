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

## Wpisanie klucza (3 sposoby, priorytet malejąco)
1. `--key A1B2C3D4E5` (CLI / do testów),
2. zmienna `DMR_RC4_KEY=A1B2C3D4E5`,
3. plik `dmr_ep.key` obok dekodera (jedna linia hex, `#` = komentarz).

Bez klucza tryb działa jak zwykły DMR (ruch zaszyfrowany zostaje oznaczony jako `encrypted`).

## Łańcuch (live w OWRX+)
```
IF IQ 48k (complex) → dyskryminator FM (numpy) → TCP 127.0.0.1:7355
   → dsd-fme -fs -1 <klucz> -i tcp -o udp:127.0.0.1:23456
   → PCM 8k na stdout  (audio OWRX)
   → zdarzenia dsd-fme → meta JSON na stderr → MetaParser → panel
```
Meta w formacie zgodnym z panelem: `{"protocol":"DMR","slot":N,"source":..,"target":..}`
plus rozszerzenie krypto `{"encrypted":true,"decrypted":true,"alg":"01","keyid":"28","cipher":"RC4","mi":..}`.

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

## Przetestowane vs do zrobienia
- ✅ Deszyfrowanie + ekstrakcja meta (rdzeń) — na nagraniu 148.512.
- ✅ Patcher OWRX (modes/dsp/feature) — na atrapach, idempotentny, walidacja py_compile.
- ⏳ Ścieżka live w OWRX (IQ→dyskryminator→TCP/UDP dsd-fme) — wymaga testu na serwerze z RTL.
- ⏳ Fonia EP (AMBE) — nagranie 148.512 to dane (ARS); ten sam klucz odszyfruje fonię, jeśli
  używa tego samego ALG 01/KID 0x28.
- ⏳ Docelowe pole klucza w ustawieniach OWRX (na razie plik/env).
