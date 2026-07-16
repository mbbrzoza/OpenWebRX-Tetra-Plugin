#!/usr/bin/env python3
"""Patch OpenWebRX+ dla WPISYWANIA KLUCZY DMR EP z panelu WWW:
  - htdocs/index.html : formularz kluczy w sekcji Settings + <script> dmr_ep_keys.js
  - owrx/connection.py: handler wiadomości {type:"dmr_ep_key"} -> zapis dmr_ep.key
Backup .bak.dmrep_ui + walidacja (py_compile dla .py) + auto-rollback. Idempotentny.
Author: SP8MB

Plik JS wgrywany osobno: dmr_ep_keys.js -> {OWX}/htdocs/static/dmr_ep_keys.js
"""
import os, sys, shutil, py_compile

OWX = (sys.argv[1] if len(sys.argv) > 1 else
       os.environ.get("DMREP_OWX", "/usr/lib/python3/dist-packages"))


def patch(path, already, transform, compile_py=False):
    src = open(path).read()
    if already in src:
        print(f"  {os.path.basename(path)}: już zpatchowane")
        return True
    new = transform(src)
    if new is None or new == src:
        print(f"  {os.path.basename(path)}: BŁĄD — brak punktu wstawienia")
        return False
    shutil.copy(path, path + ".bak.dmrep_ui")
    open(path, "w").write(new)
    if compile_py:
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as e:
            shutil.copy(path + ".bak.dmrep_ui", path)
            print(f"  {os.path.basename(path)}: SYNTAX ERROR → rollback ({e})")
            return False
    print(f"  {os.path.basename(path)}: OK")
    return True


FORM = (
    '                        <div class="openwebrx-panel-line openwebrx-dmr-ep-keys" style="flex-direction:column;align-items:stretch;">\n'
    '                            <div style="font-size:11px;margin-bottom:2px;">&#128273; Klucze DMR EP (RC4 hex, jeden na linie)</div>\n'
    '                            <textarea id="dmr-ep-key-input" rows="2" spellcheck="false" style="width:100%;box-sizing:border-box;font-family:monospace;font-size:11px;"></textarea>\n'
    '                            <div style="display:flex;align-items:center;margin-top:3px;">\n'
    '                                <div class="openwebrx-button" style="flex:0 0 auto;" onclick="DmrEpKeys.save();">Zapisz klucze</div>\n'
    '                                <span id="dmr-ep-key-status" style="font-size:11px;margin-left:8px;"></span>\n'
    '                            </div>\n'
    '                        </div>\n'
)


def do_index(src):
    lines = src.split("\n")
    # 1) <script> po plugins.js
    for i, l in enumerate(lines):
        if 'src="static/plugins.js"' in l:
            indent = l[: len(l) - len(l.lstrip())]
            lines.insert(i + 1, indent + '<script src="static/dmr_ep_keys.js"></script>')
            break
    else:
        return None
    # 2) formularz zaraz po otwarciu sekcji Settings
    si = next((k for k, l in enumerate(lines) if 'id="openwebrx-section-settings"' in l), None)
    if si is None:
        return None
    for k in range(si, min(si + 4, len(lines))):
        if '<div class="openwebrx-section">' in lines[k]:
            lines.insert(k + 1, FORM.rstrip("\n"))
            return "\n".join(lines)
    return None


HANDLER = (
    '                elif message["type"] == "dmr_ep_key":\n'
    '                    try:\n'
    '                        import os as _os, re as _re\n'
    '                        _raw = message.get("keys", message.get("key", "")) or ""\n'
    '                        _lines = []\n'
    '                        for _ln in str(_raw).splitlines():\n'
    '                            _c = _ln.split("#", 1)[0].strip()\n'
    '                            if _c and _re.fullmatch(r"[0-9A-Fa-f]{2,64}", _c):\n'
    '                                _lines.append(_c.upper())\n'
    '                        _p = "/var/lib/openwebrx/dmr_ep.key"\n'
    '                        _os.makedirs(_os.path.dirname(_p), exist_ok=True)\n'
    '                        with open(_p, "w") as _f:\n'
    '                            _f.write("# DMR EP RC4 keys (hex, one per line) - via web panel\\n")\n'
    '                            _f.write("\\n".join(_lines) + ("\\n" if _lines else ""))\n'
    '                        try:\n'
    '                            _os.chmod(_p, 0o600)\n'
    '                        except OSError:\n'
    '                            pass\n'
    '                        self.write_log_message("DMR EP: zapisano %d klucz(y)" % len(_lines))\n'
    '                    except Exception as _e:\n'
    '                        logger.warning("dmr_ep_key handler: %s", _e)\n'
)


def do_connection(src):
    anchor = '                elif message["type"] == "sendmessage":'
    if anchor not in src:
        return None
    return src.replace(anchor, HANDLER + anchor, 1)


if __name__ == "__main__":
    print("Patchowanie OWRX dla wpisywania kluczy DMR EP z panelu WWW:")
    ok = True
    ok &= patch(f"{OWX}/htdocs/index.html", 'dmr_ep_keys.js', do_index, compile_py=False)
    ok &= patch(f"{OWX}/owrx/connection.py", '"dmr_ep_key"', do_connection, compile_py=True)
    print("WSZYSTKO OK" if ok else "SĄ BŁĘDY")
    sys.exit(0 if ok else 1)
