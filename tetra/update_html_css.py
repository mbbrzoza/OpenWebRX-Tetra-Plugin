#!/usr/bin/env python3
"""Update index.html and CSS for TETRA panel.
Author: SP8MB
"""

# Replace TETRA panel in index.html
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
else:
    print("TETRA panel not found in index.html")

# Update CSS
css_file = "/usr/lib/python3/dist-packages/htdocs/css/openwebrx.css"
with open(css_file, "r") as f:
    css = f.read()
if "tetra-ts.busy" not in css:
    css += "\n.openwebrx-tetra-panel .tetra-ts.busy {\n    background: #e67700;\n    color: #fff;\n}\n.openwebrx-tetra-panel .tetra-ts.idle {\n    background: #2b8a3e;\n    color: #fff;\n}\n"
    with open(css_file, "w") as f:
        f.write(css)
    print("CSS updated")
