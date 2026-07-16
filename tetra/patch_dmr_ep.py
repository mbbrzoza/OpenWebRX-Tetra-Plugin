#!/usr/bin/env python3
"""Patch OpenWebRX+ dla trybu "DMR EP" (DMR z deszyfrowaniem RC4 Enhanced/Basic
Privacy przez dsd-fme): modes.py + dsp.py + feature.py.
Backup .bak.dmrep + walidacja py_compile + auto-rollback. Idempotentny.
Author: SP8MB

Uwaga: pliki dekodera muszą być rozłożone osobno (jak dla TETRA/LoRa):
  csdr_chain_dmr_ep.py  -> {OWX}/csdr/chain/dmr_ep.py
  csdr_module_dmr_ep.py -> {OWX}/csdr/module/dmr_ep.py
  dmr_ep_decoder.py     -> /opt/openwebrx-tetra/dmr_ep_decoder.py
Klucz RC4: /opt/openwebrx-tetra/dmr_ep.key  (albo env DMR_RC4_KEY).
"""
import os, sys, shutil, py_compile

OWX = (sys.argv[1] if len(sys.argv) > 1 else
       os.environ.get("DMREP_OWX", "/usr/lib/python3/dist-packages"))


def patch(path, already, transform):
    src = open(path).read()
    if already in src:
        print(f"  {os.path.basename(path)}: już zpatchowane")
        return True
    new = transform(src)
    if new is None or new == src:
        print(f"  {os.path.basename(path)}: BŁĄD — brak punktu wstawienia")
        return False
    shutil.copy(path, path + ".bak.dmrep")
    open(path, "w").write(new)
    try:
        py_compile.compile(path, doraise=True)
        print(f"  {os.path.basename(path)}: OK")
        return True
    except py_compile.PyCompileError as e:
        shutil.copy(path + ".bak.dmrep", path)
        print(f"  {os.path.basename(path)}: SYNTAX ERROR → rollback ({e})")
        return False


def do_modes(src):
    # DMR EP jako AnalogMode (fonia/dane; demod robi chain, IF 48k) — wstaw po
    # pierwszej znanej kotwicy: lora, potem tetra-dmo, potem tetra.
    lines = src.split("\n")
    for anchor in ('DigitalMode("lora"', 'AnalogMode("tetra-dmo"', 'AnalogMode("tetra"'):
        for i, l in enumerate(lines):
            if anchor in l:
                indent = len(l) - len(l.lstrip())
                lines.insert(i + 1, " " * indent +
                    'AnalogMode("dmr-ep", "DMR EP", bandpass=Bandpass(-6250, 6250), '
                    'requirements=["dmr_ep_decoder"], squelch=False),')
                return "\n".join(lines)
    return None


def do_dsp(src):
    # primary demod: wstaw blok dmr-ep zaraz po bloku tetra w _getDemodulator,
    # kopiując jego wcięcie (odporne na różne poziomy indentacji).
    lines = src.split("\n")
    for i, l in enumerate(lines):
        if 'demod == "tetra"' in l and 'elif' in l:
            indent = len(l) - len(l.lstrip())
            # znajdź 'return Tetra()' zamykające ten blok
            for j in range(i + 1, min(i + 8, len(lines))):
                if "return Tetra()" in lines[j]:
                    body = len(lines[j]) - len(lines[j].lstrip())
                    block = [
                        " " * indent + 'elif demod == "dmr-ep":',
                        " " * body + "from csdr.chain.dmr_ep import DmrEp",
                        " " * body + "return DmrEp()",
                    ]
                    for k, b in enumerate(block):
                        lines.insert(j + 1 + k, b)
                    return "\n".join(lines)
    return None


def do_feature(src):
    # mapowanie wymagań + metoda detekcji; kotwicz na lora, potem tetra_dmo.
    for a in ('            "lora_decoder": ["lora_demod"],',
              '            "tetra_dmo_decoder": ["tetra_dmo_demod"],'):
        if a in src:
            src = src.replace(a, a + '\n            "dmr_ep_decoder": ["dmr_ep_demod"],', 1)
            break
    else:
        return None
    method = (
        '    def has_dmr_ep_demod(self):\n'
        '        """Check if DMR-EP (dsd-fme RC4) decoder is available."""\n'
        '        import os, shutil\n'
        '        if not os.path.isfile("/opt/openwebrx-tetra/dmr_ep_decoder.py"):\n'
        '            return False\n'
        '        if not (shutil.which("dsd-fme") or os.path.isfile("/usr/local/bin/dsd-fme")):\n'
        '            return False\n'
        '        try:\n'
        '            import numpy, scipy\n'
        '        except ImportError:\n'
        '            return False\n'
        '        return True\n\n'
    )
    # wstaw metodę przed pierwszą istniejącą has_* z naszych
    for anchor in ("    def has_lora_demod(self):",
                   "    def has_tetra_dmo_demod(self):"):
        if anchor in src:
            return src.replace(anchor, method + anchor, 1)
    return None


if __name__ == "__main__":
    print("Patchowanie OWRX dla trybu DMR EP:")
    ok = True
    ok &= patch(f"{OWX}/owrx/modes.py", 'AnalogMode("dmr-ep"', do_modes)
    ok &= patch(f"{OWX}/owrx/dsp.py", 'demod == "dmr-ep"', do_dsp)
    ok &= patch(f"{OWX}/owrx/feature.py", '"dmr_ep_decoder"', do_feature)
    print("WSZYSTKO OK" if ok else "SĄ BŁĘDY")
    sys.exit(0 if ok else 1)
