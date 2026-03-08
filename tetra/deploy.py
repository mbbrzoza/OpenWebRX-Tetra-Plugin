#!/usr/bin/env python3
"""Deploy TETRA files on RPi.
Author: SP8MB
"""
import shutil
import os
import subprocess

# 1. Deploy decoder
shutil.copy("/tmp/tetra_decoder.py", "/opt/openwebrx-tetra/tetra_decoder.py")
os.chmod("/opt/openwebrx-tetra/tetra_decoder.py", 0o755)
print("tetra_decoder.py deployed")

# 2. Update MetaPanel.js
js_file = "/usr/lib/python3/dist-packages/htdocs/lib/MetaPanel.js"
with open(js_file, "r") as f:
    content = f.read()
start = content.find("function TetraMetaPanel(el)")
end = content.find("MetaPanel.types = {")
if start > 0 and end > start:
    with open("/tmp/tetra_panel.js", "r") as f:
        new_js = f.read()
    content = content[:start] + new_js + "\n" + content[end:]
    with open(js_file, "w") as f:
        f.write(content)
    print("MetaPanel.js updated")

# 3. Update HTML
html_file = "/usr/lib/python3/dist-packages/htdocs/index.html"
with open(html_file, "r") as f:
    html = f.read()
tetra_start = html.find('id="openwebrx-panel-metadata-tetra"')
if tetra_start > 0:
    div_start = html.rfind("<div", 0, tetra_start)
    pos = tetra_start
    depth = 1
    while depth > 0 and pos < len(html):
        next_open = html.find("<div", pos + 1)
        next_close = html.find("</div>", pos + 1)
        if next_close < 0:
            break
        if next_open >= 0 and next_open < next_close:
            depth += 1
            pos = next_open
        else:
            depth -= 1
            pos = next_close
    div_end = pos + len("</div>")
    with open("/tmp/tetra_panel.html", "r") as f:
        new_html = f.read().strip()
    html = html[:div_start] + new_html + html[div_end:]
    with open(html_file, "w") as f:
        f.write(html)
    print("index.html updated")

# 4. Clear cache
import glob
for d in glob.glob("/usr/lib/python3/dist-packages/**/__pycache__", recursive=True):
    shutil.rmtree(d, ignore_errors=True)
print("Cache cleared")
