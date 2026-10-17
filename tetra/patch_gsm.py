#!/usr/bin/env python3
"""Patch OpenWebRX+ dla trybu "GSM" (identyfikacja komórki GSM po kliknięciu nośnej BCCH):
  - owrx/modes.py, owrx/dsp.py, owrx/feature.py : rejestracja trybu, łańcuch, detekcja
  - htdocs/lib/MetaPanel.js : GsmMetaPanel (z gsm_panel.js) + MetaPanel.types["gsm"]
  - htdocs/index.html       : div openwebrx-panel-metadata-gsm
Backup .bak.gsm + walidacja py_compile + auto-rollback. Idempotentny.
Author: SP8MB

Pliki rozkładane osobno (jak dla LTE):
  csdr_chain_gsm.py  -> {OWX}/csdr/chain/gsm.py
  csdr_module_gsm.py -> {OWX}/csdr/module/gsm.py
  gsm_decoder.py     -> /opt/openwebrx-tetra/gsm_decoder.py
Zależności: gr-gsm (apt), pycrate; lte_decoder.py opcjonalnie (opis pasm sąsiadów LTE).
"""
import os, sys, shutil, py_compile

OWX = (sys.argv[1] if len(sys.argv) > 1 else
       os.environ.get("GSM_OWX", "/usr/lib/python3/dist-packages"))
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
    shutil.copy(path, path + ".bak.gsm")
    open(path, "w").write(new)
    if not compile_py:
        print(f"  {os.path.basename(path)}: OK")
        return True
    try:
        py_compile.compile(path, doraise=True)
        print(f"  {os.path.basename(path)}: OK")
        return True
    except py_compile.PyCompileError as e:
        shutil.copy(path + ".bak.gsm", path)
        print(f"  {os.path.basename(path)}: SYNTAX ERROR → rollback ({e})")
        return False


def do_modes(src):
    # pasmo ±120 kHz (GMSK 270,8 kbit/s, 99% mocy w ~250 kHz); IF 500 kS/s wymusza łańcuch
    lines = src.split("\n")
    for anchor in ('AnalogMode("lte"', 'AnalogMode("dmr-ep"', 'AnalogMode("tetra-dmo"', 'AnalogMode("tetra"'):
        for i, l in enumerate(lines):
            if anchor in l:
                indent = len(l) - len(l.lstrip())
                lines.insert(i + 1, " " * indent +
                    'AnalogMode("gsm", "GSM", bandpass=Bandpass(-120000, 120000), '
                    'requirements=["gsm_decoder"], squelch=False),')
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
                        " " * indent + 'elif demod == "gsm":',
                        " " * body + "from csdr.chain.gsm import Gsm",
                        " " * body + "return Gsm()",
                    ]
                    for k, b in enumerate(block):
                        lines.insert(j + 1 + k, b)
                    return "\n".join(lines)
    return None


def do_feature(src):
    for a in ('            "lte_decoder": ["lte_demod"],',
              '            "dmr_ep_decoder": ["dmr_ep_demod"],',
              '            "lora_decoder": ["lora_demod"],',
              '            "tetra_dmo_decoder": ["tetra_dmo_demod"],'):
        if a in src:
            src = src.replace(a, a + '\n            "gsm_decoder": ["gsm_demod"],', 1)
            break
    else:
        return None
    method = (
        '    def has_gsm_demod(self):\n'
        '        """Check if the GSM cell decoder is available (gr-gsm python module + numpy)."""\n'
        '        import os\n'
        '        if not os.path.isfile("/opt/openwebrx-tetra/gsm_decoder.py"):\n'
        '            return False\n'
        '        if not os.path.isfile("/usr/lib/python3/dist-packages/gnuradio/gsm/__init__.py"):\n'
        '            return False\n'
        '        try:\n'
        '            import numpy\n'
        '        except ImportError:\n'
        '            return False\n'
        '        return True\n\n'
    )
    for anchor in ("    def has_lte_demod(self):",
                   "    def has_dmr_ep_demod(self):",
                   "    def has_lora_demod(self):",
                   "    def has_tetra_dmo_demod(self):"):
        if anchor in src:
            return src.replace(anchor, method + anchor, 1)
    return None


def do_metapanel(src):
    js = open(os.path.join(HERE, "gsm_panel.js")).read()
    anchor = "MetaPanel.types = {"
    if anchor not in src:
        return None
    return src.replace(anchor, js + "\n\n" + anchor + "\n    gsm: GsmMetaPanel,", 1)


def refresh_metapanel(path):
    """Podmiana wstrzykniętego GsmMetaPanel na bieżący gsm_panel.js (aktualizacja panelu)."""
    src = open(path).read()
    start = src.find("// GsmMetaPanel —")
    end_mark = "GsmMetaPanel.prototype.clear = function () { };"
    end = src.find(end_mark, start)
    if start < 0 or end < 0:
        return
    js = open(os.path.join(HERE, "gsm_panel.js")).read().rstrip("\n")
    new = src[:start] + js + src[end + len(end_mark):]
    if new != src:
        shutil.copy(path, path + ".bak.gsm")
        open(path, "w").write(new)
        print("  MetaPanel.js: GsmMetaPanel zaktualizowany")


PANEL = (
    '                <div class="openwebrx-panel openwebrx-meta-panel" id="openwebrx-panel-metadata-gsm" style="display: none;" data-panel-name="metadata-gsm">\n'
    '                    <div style="font-family:monospace;font-size:11px;min-width:260px;max-width:420px;">\n'
    '                        <div style="font-weight:bold;padding:2px 4px;border-bottom:1px solid #666;">&#128225; GSM — komórka</div>\n'
    '                        <div class="openwebrx-gsm-status" style="padding:2px 4px;color:#9ab;">czekam na dane…</div>\n'
    '                        <div class="openwebrx-gsm-cell" style="padding:2px 4px;max-height:260px;overflow-y:auto;"></div>\n'
    '                        <div style="padding:2px 4px;border-top:1px solid #666;color:#8ab;">Znalezione: <span class="openwebrx-gsm-hcount">0</span>'
    '<span class="openwebrx-gsm-hclear" title="Wyczyść" style="float:right;cursor:pointer;">&#128465;</span></div>\n'
    '                        <div class="openwebrx-gsm-history" style="padding:0 4px 2px;max-height:80px;overflow-y:auto;"></div>\n'
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
    print("Patchowanie OWRX dla trybu GSM:")
    ok = True
    ok &= patch(f"{OWX}/owrx/modes.py", 'AnalogMode("gsm"', do_modes)
    ok &= patch(f"{OWX}/owrx/dsp.py", 'demod == "gsm"', do_dsp)
    ok &= patch(f"{OWX}/owrx/feature.py", '"gsm_decoder"', do_feature)
    ok &= patch(f"{OWX}/htdocs/lib/MetaPanel.js", "GsmMetaPanel", do_metapanel, compile_py=False)
    refresh_metapanel(f"{OWX}/htdocs/lib/MetaPanel.js")
    ok &= patch(f"{OWX}/htdocs/index.html", "openwebrx-panel-metadata-gsm", do_index, compile_py=False)
    print("WSZYSTKO OK" if ok else "SĄ BŁĘDY")
    sys.exit(0 if ok else 1)
