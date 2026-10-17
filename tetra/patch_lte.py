#!/usr/bin/env python3
"""Patch OpenWebRX+ dla trybu "LTE" (identyfikacja komórki LTE po kliknięciu nośnej):
  - owrx/modes.py, owrx/dsp.py, owrx/feature.py : rejestracja trybu, łańcuch, detekcja
  - htdocs/lib/MetaPanel.js : LteMetaPanel (z lte_panel.js) + MetaPanel.types["lte"]
  - htdocs/index.html       : div openwebrx-panel-metadata-lte
Backup .bak.lte + walidacja py_compile + auto-rollback. Idempotentny.
Author: SP8MB

Pliki rozkładane osobno (jak dla DMR EP):
  csdr_chain_lte.py  -> {OWX}/csdr/chain/lte.py
  csdr_module_lte.py -> {OWX}/csdr/module/lte.py
  lte_decoder.py     -> /opt/openwebrx-tetra/lte_decoder.py
  pdsch_ue           -> /opt/openwebrx-tetra/bin/pdsch_ue   (build_lte_pdsch.sh)
"""
import os, sys, shutil, py_compile

OWX = (sys.argv[1] if len(sys.argv) > 1 else
       os.environ.get("LTE_OWX", "/usr/lib/python3/dist-packages"))
HERE = os.path.dirname(os.path.abspath(__file__))


def patch(path, already, transform, compile_py=True):
    src = open(path).read()
    if already in src:
        print(f"  {os.path.basename(path)}: już zpatchowane")
        return True
    new = transform(src)
    if new is None or new == src:
        print(f"  {os.path.basename(path)}: BŁĄD — brak punktu wstawienia")
        return False
    shutil.copy(path, path + ".bak.lte")
    open(path, "w").write(new)
    if not compile_py:
        print(f"  {os.path.basename(path)}: OK")
        return True
    try:
        py_compile.compile(path, doraise=True)
        print(f"  {os.path.basename(path)}: OK")
        return True
    except py_compile.PyCompileError as e:
        shutil.copy(path + ".bak.lte", path)
        print(f"  {os.path.basename(path)}: SYNTAX ERROR → rollback ({e})")
        return False


def do_modes(src):
    # pasmo ±700 kHz (kanał 1,4 MHz = 1,08 MHz zajęte); IF 1,92 MS/s wymusza łańcuch
    lines = src.split("\n")
    for anchor in ('AnalogMode("dmr-ep"', 'AnalogMode("tetra-dmo"', 'AnalogMode("tetra"'):
        for i, l in enumerate(lines):
            if anchor in l:
                indent = len(l) - len(l.lstrip())
                lines.insert(i + 1, " " * indent +
                    'AnalogMode("lte", "LTE", bandpass=Bandpass(-700000, 700000), '
                    'requirements=["lte_decoder"], squelch=False),')
                return "\n".join(lines)
    return None


def do_dsp(src):
    lines = src.split("\n")
    for i, l in enumerate(lines):
        if 'demod == "tetra"' in l and 'elif' in l:
            indent = len(l) - len(l.lstrip())
            for j in range(i + 1, min(i + 8, len(lines))):
                if "return Tetra()" in lines[j]:
                    body = len(lines[j]) - len(lines[j].lstrip())
                    block = [
                        " " * indent + 'elif demod == "lte":',
                        " " * body + "from csdr.chain.lte import Lte",
                        " " * body + "return Lte()",
                    ]
                    for k, b in enumerate(block):
                        lines.insert(j + 1 + k, b)
                    return "\n".join(lines)
    return None


def do_feature(src):
    for a in ('            "dmr_ep_decoder": ["dmr_ep_demod"],',
              '            "lora_decoder": ["lora_demod"],',
              '            "tetra_dmo_decoder": ["tetra_dmo_demod"],'):
        if a in src:
            src = src.replace(a, a + '\n            "lte_decoder": ["lte_demod"],', 1)
            break
    else:
        return None
    method = (
        '    def has_lte_demod(self):\n'
        '        """Check if the LTE cell decoder is available (pdsch_ue optional: without it PCI only)."""\n'
        '        import os\n'
        '        if not os.path.isfile("/opt/openwebrx-tetra/lte_decoder.py"):\n'
        '            return False\n'
        '        try:\n'
        '            import numpy, scipy\n'
        '        except ImportError:\n'
        '            return False\n'
        '        return True\n\n'
    )
    for anchor in ("    def has_dmr_ep_demod(self):",
                   "    def has_lora_demod(self):",
                   "    def has_tetra_dmo_demod(self):"):
        if anchor in src:
            return src.replace(anchor, method + anchor, 1)
    return None


def do_metapanel(src):
    js = open(os.path.join(HERE, "lte_panel.js")).read()
    anchor = "MetaPanel.types = {"
    if anchor not in src:
        return None
    return src.replace(anchor, js + "\n\n" + anchor + "\n    lte: LteMetaPanel,", 1)


PANEL = (
    '                <div class="openwebrx-panel openwebrx-meta-panel" id="openwebrx-panel-metadata-lte" style="display: none;" data-panel-name="metadata-lte">\n'
    '                    <div style="font-family:monospace;font-size:11px;min-width:260px;max-width:420px;">\n'
    '                        <div style="font-weight:bold;padding:2px 4px;border-bottom:1px solid #666;">&#128246; LTE — komórka</div>\n'
    '                        <div class="openwebrx-lte-status" style="padding:2px 4px;color:#9ab;">czekam na dane…</div>\n'
    '                        <div class="openwebrx-lte-cell" style="padding:2px 4px;max-height:260px;overflow-y:auto;"></div>\n'
    '                        <div style="padding:2px 4px;border-top:1px solid #666;color:#8ab;">Znalezione: <span class="openwebrx-lte-hcount">0</span>'
    '<span class="openwebrx-lte-hclear" title="Wyczyść" style="float:right;cursor:pointer;">&#128465;</span></div>\n'
    '                        <div class="openwebrx-lte-history" style="padding:0 4px 2px;max-height:80px;overflow-y:auto;"></div>\n'
    '                    </div>\n'
    '                </div>\n'
)


def do_index(src):
    lines = src.split("\n")
    mi = next((k for k, l in enumerate(lines) if 'id="openwebrx-panel-metadata-wfm"' in l), None)
    if mi is None:
        return None
    lines.insert(mi, PANEL.rstrip("\n"))
    return "\n".join(lines)


if __name__ == "__main__":
    print("Patchowanie OWRX dla trybu LTE:")
    ok = True
    ok &= patch(f"{OWX}/owrx/modes.py", 'AnalogMode("lte"', do_modes)
    ok &= patch(f"{OWX}/owrx/dsp.py", 'demod == "lte"', do_dsp)
    ok &= patch(f"{OWX}/owrx/feature.py", '"lte_decoder"', do_feature)
    ok &= patch(f"{OWX}/htdocs/lib/MetaPanel.js", "LteMetaPanel", do_metapanel, compile_py=False)
    ok &= patch(f"{OWX}/htdocs/index.html", "openwebrx-panel-metadata-lte", do_index, compile_py=False)
    print("WSZYSTKO OK" if ok else "SĄ BŁĘDY")
    sys.exit(0 if ok else 1)
