#!/usr/bin/env python3
"""Patch OpenWebRX+ dla trybu LoRa-APRS: modes.py + dsp.py + feature.py.
Backup .bak.lora + walidacja py_compile + auto-rollback. Idempotentny.
Author: SP8MB
"""
import os, sys, shutil, py_compile

# ścieżka OWRX dist-packages: argv[1] albo env LORA_OWX albo domyślna
OWX = (sys.argv[1] if len(sys.argv) > 1 else
       os.environ.get("LORA_OWX", "/usr/lib/python3/dist-packages"))


def patch(path, already, transform):
    src = open(path).read()
    if already in src:
        print(f"  {os.path.basename(path)}: już zpatchowane")
        return True
    new = transform(src)
    if new is None or new == src:
        print(f"  {os.path.basename(path)}: BŁĄD — brak punktu wstawienia")
        return False
    shutil.copy(path, path + ".bak.lora")
    open(path, "w").write(new)
    try:
        py_compile.compile(path, doraise=True)
        print(f"  {os.path.basename(path)}: OK")
        return True
    except py_compile.PyCompileError as e:
        shutil.copy(path + ".bak.lora", path)
        print(f"  {os.path.basename(path)}: SYNTAX ERROR → rollback ({e})")
        return False


def do_modes(src):
    # LoRa-APRS jako DigitalMode secondary (wzorzec ISM/packet — dane→mapa)
    lines = src.split("\n")
    for i, l in enumerate(lines):
        if 'AnalogMode("tetra-dmo"' in l:
            indent = len(l) - len(l.lstrip())
            lines.insert(i + 1, " " * indent +
                'DigitalMode("lora", "LoRa-APRS", underlying=["empty"], '
                'bandpass=Bandpass(-62500, 62500), ifRate=250000, '
                'requirements=["lora_decoder"], service=True, squelch=False),')
            return "\n".join(lines)
    return None


def do_dsp(src):
    # wpięcie w _getSecondaryDemodulator (po ism) — secondary demod
    anchor = ('        elif mod == "ism":\n'
              '            from csdr.chain.toolbox import IsmDemodulator\n'
              '            return IsmDemodulator(250000)')
    ins = ('\n        elif mod == "lora":\n'
           '            from csdr.chain.lora import LoRaAprsDemodulator\n'
           '            return LoRaAprsDemodulator()')
    if anchor in src:
        return src.replace(anchor, anchor + ins, 1)
    return None


def do_feature(src):
    a = '            "tetra_dmo_decoder": ["tetra_dmo_demod"],'
    if a not in src:
        return None
    src = src.replace(a, a + '\n            "lora_decoder": ["lora_demod"],', 1)
    method = (
        '    def has_lora_demod(self):\n'
        '        """Check if LoRa-APRS decoder is available."""\n'
        '        import os\n'
        '        if not os.path.isfile("/opt/openwebrx-tetra/lora_decoder.py"):\n'
        '            return False\n'
        '        try:\n'
        '            import numpy, scipy\n'
        '        except ImportError:\n'
        '            return False\n'
        '        return True\n\n'
    )
    return src.replace("    def has_tetra_dmo_demod(self):",
                       method + "    def has_tetra_dmo_demod(self):", 1)


def do_aprs(src):
    # bugfix OWRX: hasCompressedCoordinates pomija pozycje skompresowane z OVERLAY
    # (tabela = litera A-Z, np. LoRa-APRS iGate) → kieruje do parsera nieskompresowanego → błąd.
    bs = chr(92)
    lines = src.split("\n")
    for i, l in enumerate(lines):
        if "def hasCompressedCoordinates" in l:
            j = i + 1
            if j < len(lines) and "return raw[0]" in lines[j] and "isalpha" not in lines[j]:
                indent = len(lines[j]) - len(lines[j].lstrip())
                lines[j] = (" " * indent + 'return len(raw) > 0 and (raw[0] == "/" or raw[0] == "'
                            + bs + bs + '" or raw[0].isalpha())')
                return "\n".join(lines)
    return None


if __name__ == "__main__":
    print("Patchowanie OWRX dla LoRa-APRS:")
    ok = True
    ok &= patch(f"{OWX}/owrx/modes.py", 'DigitalMode("lora"', do_modes)
    ok &= patch(f"{OWX}/owrx/dsp.py", 'mod == "lora"', do_dsp)
    ok &= patch(f"{OWX}/owrx/feature.py", '"lora_decoder"', do_feature)
    ok &= patch(f"{OWX}/owrx/aprs/__init__.py", "raw[0].isalpha()", do_aprs)
    print("WSZYSTKO OK" if ok else "SĄ BŁĘDY")
    sys.exit(0 if ok else 1)
