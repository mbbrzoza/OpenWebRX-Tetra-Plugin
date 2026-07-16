#!/usr/bin/env python3
"""Patch OpenWebRX+ dla PANELU "DMR EP" (wyswietlanie zdekodowanych/odszyfrowanych danych):
  - htdocs/lib/MetaPanel.js       : DmrEpMetaPanel (z dmr_ep_panel.js) + rejestracja types["dmrep"]
  - htdocs/lib/DemodulatorPanel.js : mapowanie trybu dmr-ep -> panel dmrep
  - htdocs/index.html              : div panelu openwebrx-panel-metadata-dmrep
Backup .bak.dmrep_panel + auto-rollback (marker check). Idempotentny.
Author: SP8MB

compiled/receiver.js budowany na biezaco z lib/*.js -> patch lib/ dociera do przegladarki
(user: Ctrl+Shift+R po deployu).
"""
import os, sys, shutil

OWX = (sys.argv[1] if len(sys.argv) > 1 else
       os.environ.get("DMREP_OWX", "/usr/lib/python3/dist-packages"))
HERE = os.path.dirname(os.path.abspath(__file__))


def patch(path, already, transform):
    src = open(path).read()
    if already in src:
        print(f"  {os.path.basename(path)}: juz zpatchowane")
        return True
    new = transform(src)
    if new is None or new == src:
        print(f"  {os.path.basename(path)}: BLAD — brak punktu wstawienia")
        return False
    shutil.copy(path, path + ".bak.dmrep_panel")
    open(path, "w").write(new)
    print(f"  {os.path.basename(path)}: OK")
    return True


def do_metapanel(src):
    js = open(os.path.join(HERE, "dmr_ep_panel.js")).read()
    anchor = "MetaPanel.types = {"
    if anchor not in src:
        return None
    # wstaw definicje przed obiektem types i zarejestruj dmrep
    return src.replace(anchor, js + "\n\n" + anchor + "\n    dmrep: DmrEpMetaPanel,", 1)


def do_demod(src):
    anchor = "modulation === 'tetra-dmo' ? 'tetra' : modulation"
    if anchor not in src:
        return None
    repl = "modulation === 'tetra-dmo' ? 'tetra' : modulation === 'dmr-ep' ? 'dmrep' : modulation"
    return src.replace(anchor, repl, 1)


PANEL = (
    '                <div class="openwebrx-panel openwebrx-meta-panel" id="openwebrx-panel-metadata-dmrep" style="display: none;" data-panel-name="metadata-dmrep">\n'
    '                    <div class="openwebrx-dmrep-panel" style="font-family:monospace;font-size:11px;min-width:240px;">\n'
    '                        <div style="font-weight:bold;padding:2px 4px;border-bottom:1px solid #666;">&#128275; DMR EP — zdekodowane dane'
    '<span style="float:right;"><span class="openwebrx-dmrep-count">0</span> <span class="openwebrx-dmrep-clear" title="Wyczysc" style="cursor:pointer;">&#128465;</span></span></div>\n'
    '                        <div class="openwebrx-dmrep-list" style="max-height:220px;overflow-y:auto;padding:2px 4px;"></div>\n'
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
    print("Patchowanie OWRX dla panelu DMR EP:")
    ok = True
    ok &= patch(f"{OWX}/htdocs/lib/MetaPanel.js", "DmrEpMetaPanel", do_metapanel)
    ok &= patch(f"{OWX}/htdocs/lib/DemodulatorPanel.js", "'dmr-ep' ? 'dmrep'", do_demod)
    ok &= patch(f"{OWX}/htdocs/index.html", "openwebrx-panel-metadata-dmrep", do_index)
    print("WSZYSTKO OK" if ok else "SA BLEDY")
    sys.exit(0 if ok else 1)
