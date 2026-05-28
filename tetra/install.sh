#!/bin/bash
# TETRA module installer for OpenWebRX+
# Author: SP8MB
# Installs TETRA voice decoding support on Raspberry Pi / Debian
#
# Usage:
#   sudo bash install.sh              # Full install (build + patch)
#   sudo bash install.sh --update     # Update scripts/panel only (no rebuild)
#   sudo bash install.sh --uninstall  # Remove TETRA module
#   sudo bash install.sh --check      # Verify installation
#
# Prerequisites:
#   - OpenWebRX+ v1.2.x installed
#   - Internet access for apt packages (full install only)

set -e

INSTALL_DIR="/opt/openwebrx-tetra"
OWRX_PYTHON="/usr/lib/python3/dist-packages"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()   { echo -e "${GREEN}[TETRA]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
info()  { echo -e "${CYAN}[INFO]${NC} $1"; }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Argument parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MODE="install"
NO_RESTART=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --update)    MODE="update"; shift ;;
        --uninstall) MODE="uninstall"; shift ;;
        --check)     MODE="check"; shift ;;
        --no-restart) NO_RESTART=1; shift ;;
        -h|--help)
            echo "Usage: sudo bash install.sh [--update|--uninstall|--check] [--no-restart]"
            echo ""
            echo "Modes:"
            echo "  (default)    Full install: dependencies, compile, patch"
            echo "  --update     Update decoder scripts and panel only"
            echo "  --uninstall  Remove TETRA module completely"
            echo "  --check      Verify installation status"
            echo ""
            echo "Options:"
            echo "  --no-restart  Don't restart openwebrx service"
            exit 0
            ;;
        *) error "Unknown option: $1" ;;
    esac
done

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Prechecks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[[ $EUID -ne 0 ]] && error "This script must be run as root (sudo)"
[[ -f "$OWRX_PYTHON/owrx/modes.py" ]] || error "OpenWebRX+ not found at $OWRX_PYTHON"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helper: verify installation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
verify_installation() {
    local errors=0

    log "Verifying installation..."

    # Core binaries/scripts
    for f in tetra_decoder.py tetra_demod.py tetra-rx; do
        if [[ -f "$INSTALL_DIR/$f" ]]; then
            log "  OK  $f"
        else
            warn "  MISSING  $f"
            errors=$((errors + 1))
        fi
    done

    # Codec (optional but needed for audio)
    for f in cdecoder sdecoder; do
        if [[ -f "$INSTALL_DIR/$f" ]]; then
            log "  OK  $f"
        else
            warn "  MISSING  $f (audio decoding won't work)"
        fi
    done

    # CSDR modules
    for f in csdr/module/tetra.py csdr/chain/tetra.py; do
        if [[ -f "$OWRX_PYTHON/$f" ]]; then
            log "  OK  $f"
        else
            warn "  MISSING  $f"
            errors=$((errors + 1))
        fi
    done

    # Patches in OpenWebRX+
    if grep -q '"tetra"' "$OWRX_PYTHON/owrx/modes.py" 2>/dev/null; then
        log "  OK  TETRA mode in modes.py"
    else
        warn "  MISSING  TETRA mode in modes.py"
        errors=$((errors + 1))
    fi

    if grep -q 'tetra_decoder' "$OWRX_PYTHON/owrx/feature.py" 2>/dev/null; then
        log "  OK  TETRA feature in feature.py"
    else
        warn "  MISSING  TETRA feature in feature.py"
        errors=$((errors + 1))
    fi

    if grep -q '"tetra"' "$OWRX_PYTHON/owrx/dsp.py" 2>/dev/null; then
        log "  OK  TETRA routing in dsp.py"
    else
        warn "  MISSING  TETRA routing in dsp.py"
        errors=$((errors + 1))
    fi

    # Frontend
    if grep -q 'openwebrx-panel-metadata-tetra' "$OWRX_PYTHON/htdocs/index.html" 2>/dev/null; then
        log "  OK  TETRA panel in index.html"
    else
        warn "  MISSING  TETRA panel in index.html"
        errors=$((errors + 1))
    fi

    if grep -q 'TetraMetaPanel' "$OWRX_PYTHON/htdocs/lib/MetaPanel.js" 2>/dev/null; then
        log "  OK  TetraMetaPanel in MetaPanel.js"
    else
        warn "  MISSING  TetraMetaPanel in MetaPanel.js"
        errors=$((errors + 1))
    fi

    if grep -q 'tetra-ts.busy' "$OWRX_PYTHON/htdocs/css/openwebrx.css" 2>/dev/null; then
        log "  OK  TETRA CSS styles"
    else
        warn "  MISSING  TETRA CSS styles"
        errors=$((errors + 1))
    fi

    # GNURadio
    if python3 -c 'from gnuradio import gr' 2>/dev/null; then
        log "  OK  GNURadio $(python3 -c 'from gnuradio import gr; print(gr.version())' 2>/dev/null)"
    else
        warn "  MISSING  GNURadio"
        errors=$((errors + 1))
    fi

    echo ""
    if [[ $errors -eq 0 ]]; then
        log "Installation OK - all components present"
    else
        warn "Installation has $errors issue(s)"
    fi
    return $errors
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CHECK mode
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if [[ "$MODE" == "check" ]]; then
    verify_installation
    exit $?
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UNINSTALL mode
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if [[ "$MODE" == "uninstall" ]]; then
    log "=== Uninstalling TETRA module ==="

    # Remove CSDR modules
    rm -f "$OWRX_PYTHON/csdr/module/tetra.py"
    rm -f "$OWRX_PYTHON/csdr/chain/tetra.py"
    rm -f "$OWRX_PYTHON/csdr/module/tetra_dmo.py"
    rm -f "$OWRX_PYTHON/csdr/chain/tetra_dmo.py"
    log "Removed CSDR modules"

    # Restore backed-up OpenWebRX+ files
    for f in owrx/modes.py owrx/feature.py owrx/dsp.py; do
        if [[ -f "$OWRX_PYTHON/$f.bak.pre-tetra" ]]; then
            cp "$OWRX_PYTHON/$f.bak.pre-tetra" "$OWRX_PYTHON/$f"
            log "Restored $f from backup"
        else
            warn "No backup for $f - manual cleanup may be needed"
        fi
    done

    # Remove TETRA panel from index.html
    python3 << 'PYEOF'
html_file = "/usr/lib/python3/dist-packages/htdocs/index.html"
with open(html_file, "r") as f:
    html = f.read()

marker = 'id="openwebrx-panel-metadata-tetra"'
if marker in html:
    start = html.find(marker)
    div_start = html.rfind("<div", 0, start)
    pos = start
    depth = 1
    while depth > 0 and pos < len(html):
        next_open = html.find("<div", pos + 1)
        next_close = html.find("</div>", pos + 1)
        if next_close < 0:
            break
        if 0 <= next_open < next_close:
            depth += 1
            pos = next_open
        else:
            depth -= 1
            pos = next_close
    div_end = pos + len("</div>")
    # Also remove trailing newline
    if div_end < len(html) and html[div_end] == '\n':
        div_end += 1
    html = html[:div_start] + html[div_end:]
    with open(html_file, "w") as f:
        f.write(html)
    print("  Removed TETRA panel from index.html")
PYEOF

    # Remove TetraMetaPanel from MetaPanel.js
    python3 << 'PYEOF'
js_file = "/usr/lib/python3/dist-packages/htdocs/lib/MetaPanel.js"
with open(js_file, "r") as f:
    content = f.read()

start = content.find("function TetraMetaPanel(el)")
if start >= 0:
    # Find the MetaPanel.types registration
    types_pos = content.find("MetaPanel.types", start)
    if types_pos >= 0:
        content = content[:start] + content[types_pos:]
    with open(js_file, "w") as f:
        f.write(content)
    print("  Removed TetraMetaPanel from MetaPanel.js")

# Remove tetra from MetaPanel.types
with open(js_file, "r") as f:
    content = f.read()
import re
content = re.sub(r',?\s*"tetra"\s*:\s*TetraMetaPanel', '', content)
with open(js_file, "w") as f:
    f.write(content)
print("  Removed tetra from MetaPanel.types")
PYEOF

    # Remove TETRA CSS
    python3 << 'PYEOF'
import re
css_file = "/usr/lib/python3/dist-packages/htdocs/css/openwebrx.css"
with open(css_file, "r") as f:
    css = f.read()
# Remove all TETRA-related CSS blocks
css = re.sub(r'/\*\s*TETRA\s*\*/.*?(?=\n/\*|\Z)', '', css, flags=re.DOTALL)
css = re.sub(r'\.openwebrx-tetra-panel\s*\{[^}]*\}', '', css)
css = re.sub(r'\.openwebrx-tetra-panel\s+[^{]*\{[^}]*\}', '', css)
css = re.sub(r'\.tetra-[a-z-]+[^{]*\{[^}]*\}', '', css)
css = re.sub(r'\n{3,}', '\n\n', css)
with open(css_file, "w") as f:
    f.write(css)
print("  Removed TETRA CSS styles")
PYEOF

    # Clear Python cache
    find "$OWRX_PYTHON" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    find "$OWRX_PYTHON" -name "*.pyc" -delete 2>/dev/null || true

    # Optionally remove install dir
    if [[ -d "$INSTALL_DIR" ]]; then
        info "TETRA binaries remain in $INSTALL_DIR"
        info "To remove completely: rm -rf $INSTALL_DIR"
    fi

    if [[ -z "$NO_RESTART" ]]; then
        log "Restarting OpenWebRX+..."
        systemctl restart openwebrx 2>/dev/null || warn "Could not restart openwebrx service"
    fi

    log "=== TETRA module uninstalled ==="
    exit 0
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Source file checks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
for f in tetra_decoder.py tetra_demod.py csdr_module_tetra.py csdr_chain_tetra.py tetra_panel.js tetra_panel.html \
         tetra_dmo_decoder.py csdr_module_tetra_dmo.py csdr_chain_tetra_dmo.py \
         dmo_l1_chain.py dmo_pdu_parser.py; do
    [[ -f "$SCRIPT_DIR/$f" ]] || error "Source file missing: $SCRIPT_DIR/$f"
done

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INSTALL / UPDATE mode
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if [[ "$MODE" == "install" ]]; then
    log "=== TETRA Module Installer for OpenWebRX+ ==="

    # ── Step 1: System dependencies ──
    log "Step 1/8: Installing system dependencies..."
    apt-get update -qq
    apt-get install -y -qq \
        gnuradio \
        libosmocore-dev \
        build-essential \
        pkg-config \
        git \
        wget \
        unzip \
        python3-dev \
        2>/dev/null
    log "GNURadio $(python3 -c 'from gnuradio import gr; print(gr.version())' 2>/dev/null || echo '?') installed"

    # ── Step 2: Create install directory ──
    log "Step 2/8: Setting up $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"

    # ── Step 3: Compile tetra-rx ──
    log "Step 3/8: Compiling tetra-rx (osmo-tetra)..."
    OSMO_TETRA_SRC="$INSTALL_DIR/osmo-tetra-sq5bpf"

    if [[ ! -d "$OSMO_TETRA_SRC" ]]; then
        git clone --depth 1 https://github.com/sq5bpf/osmo-tetra-sq5bpf "$OSMO_TETRA_SRC" 2>/dev/null || true
    fi

    if [[ -d "$OSMO_TETRA_SRC/src" ]]; then
        cd "$OSMO_TETRA_SRC/src"
        make clean 2>/dev/null || true
        make tetra-rx float_to_bits 2>&1 | tail -5
        cp tetra-rx "$INSTALL_DIR/"
        cp float_to_bits "$INSTALL_DIR/" 2>/dev/null || true
        log "tetra-rx compiled successfully"
    else
        error "Failed to clone osmo-tetra"
    fi

    # ── Step 4: Build ETSI ACELP codec ──
    log "Step 4/8: Building ETSI ACELP codec..."
    CODEC_DIR="$OSMO_TETRA_SRC/etsi_codec-patches"
    if [[ -d "$CODEC_DIR" && -f "$CODEC_DIR/download_and_patch.sh" ]]; then
        cd "$CODEC_DIR"
        if [[ ! -f "$INSTALL_DIR/cdecoder" ]]; then
            log "Running ETSI codec download and build..."
            bash download_and_patch.sh 2>&1 | tail -10
            if [[ -d codec ]]; then
                cd codec
                make 2>&1 | tail -5
                cp cdecoder sdecoder "$INSTALL_DIR/" 2>/dev/null || true
                cd "$CODEC_DIR"
            fi
            if [[ -f "$INSTALL_DIR/cdecoder" && -f "$INSTALL_DIR/sdecoder" ]]; then
                log "ACELP codec built successfully"
            else
                warn "ACELP codec build failed - audio decoding will not work"
                warn "See: $CODEC_DIR/README"
            fi
        else
            log "ACELP codec already installed"
        fi
    else
        warn "ETSI codec patches not found"
        warn "Copy cdecoder and sdecoder to $INSTALL_DIR/ manually"
    fi
else
    log "=== TETRA Module Update ==="
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Steps common to install and update
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP_BASE=5
[[ "$MODE" == "update" ]] && STEP_BASE=1
STEP_TOTAL=8
[[ "$MODE" == "update" ]] && STEP_TOTAL=4

# ── Deploy decoder scripts ──
log "Step $STEP_BASE/$STEP_TOTAL: Deploying decoder scripts..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/tetra_decoder.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/tetra_demod.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/tetra_dmo_decoder.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/dmo_l1_chain.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/dmo_pdu_parser.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/tetra_decoder.py" "$INSTALL_DIR/tetra_dmo_decoder.py"
log "Deployed tetra_decoder.py + tetra_demod.py + DMO modules"

# ── Install CSDR module/chain ──
STEP=$((STEP_BASE + 1))
log "Step $STEP/$STEP_TOTAL: Installing CSDR module..."
cp "$SCRIPT_DIR/csdr_module_tetra.py" "$OWRX_PYTHON/csdr/module/tetra.py"
cp "$SCRIPT_DIR/csdr_chain_tetra.py" "$OWRX_PYTHON/csdr/chain/tetra.py"
cp "$SCRIPT_DIR/csdr_module_tetra_dmo.py" "$OWRX_PYTHON/csdr/module/tetra_dmo.py"
cp "$SCRIPT_DIR/csdr_chain_tetra_dmo.py" "$OWRX_PYTHON/csdr/chain/tetra_dmo.py"
log "Installed csdr/module/tetra{,_dmo}.py and csdr/chain/tetra{,_dmo}.py"

# ── Patch OpenWebRX+ (modes.py, feature.py, dsp.py) ──
STEP=$((STEP_BASE + 2))
log "Step $STEP/$STEP_TOTAL: Patching OpenWebRX+..."

# Backup originals (only on first install)
if [[ "$MODE" == "install" ]]; then
    for f in owrx/modes.py owrx/feature.py owrx/dsp.py htdocs/index.html htdocs/lib/MetaPanel.js htdocs/css/openwebrx.css; do
        if [[ -f "$OWRX_PYTHON/$f" && ! -f "$OWRX_PYTHON/$f.bak.pre-tetra" ]]; then
            cp "$OWRX_PYTHON/$f" "$OWRX_PYTHON/$f.bak.pre-tetra"
        fi
    done
    log "  Backups created (.bak.pre-tetra)"
fi

# --- Patch modes.py ---
if ! grep -q '"tetra"' "$OWRX_PYTHON/owrx/modes.py"; then
    log "  Patching modes.py..."
    python3 << 'PYEOF'
modes_file = "/usr/lib/python3/dist-packages/owrx/modes.py"
with open(modes_file, "r") as f:
    lines = f.readlines()

inserted = False
for i, line in enumerate(lines):
    if '"nxdn"' in line and 'AnalogMode' in line:
        # Find the end of this entry (line ending with ),)
        j = i
        while j < len(lines) and not lines[j].rstrip().endswith('),'):
            j += 1
        # Match indentation of the nxdn line
        indent = len(line) - len(line.lstrip())
        tetra_line = ' ' * indent + 'AnalogMode("tetra", "TETRA", bandpass=Bandpass(-12500, 12500), requirements=["tetra_decoder"], squelch=False),\n'
        lines.insert(j + 1, tetra_line)
        inserted = True
        break

if not inserted:
    # Fallback: add before first DigitalMode
    for i, line in enumerate(lines):
        if 'DigitalMode(' in line:
            indent = len(line) - len(line.lstrip())
            tetra_line = ' ' * indent + 'AnalogMode("tetra", "TETRA", bandpass=Bandpass(-12500, 12500), requirements=["tetra_decoder"], squelch=False),\n'
            lines.insert(i, tetra_line)
            inserted = True
            break

if inserted:
    with open(modes_file, "w") as f:
        f.writelines(lines)
    # Verify syntax after patching
    import py_compile, shutil, os
    try:
        py_compile.compile(modes_file, doraise=True)
        print("    TETRA mode added to modes.py")
    except py_compile.PyCompileError as e:
        print("    ERROR: modes.py has syntax error after patching!")
        print("    " + str(e))
        backup = modes_file + ".bak.pre-tetra"
        if os.path.isfile(backup):
            shutil.copy2(backup, modes_file)
            print("    Restored modes.py from backup")
        raise SystemExit(1)
else:
    print("    WARNING: Could not find insertion point in modes.py")
PYEOF
else
    log "  modes.py already patched"
fi

# --- Patch feature.py ---
if ! grep -q 'tetra_decoder' "$OWRX_PYTHON/owrx/feature.py"; then
    log "  Patching feature.py..."
    python3 << 'PYEOF'
import re

feature_file = "/usr/lib/python3/dist-packages/owrx/feature.py"
with open(feature_file, "r") as f:
    content = f.read()

# Add tetra_decoder feature to the features dict
features_pattern = r'("digital_voice_digiham"\s*:\s*\[[^\]]+\])'
match = re.search(features_pattern, content)
if match:
    insert_pos = match.end()
    tetra_feature = ',\n            "tetra_decoder": ["tetra_demod"]'
    content = content[:insert_pos] + tetra_feature + content[insert_pos:]

    # Add has_tetra_demod method
    method_code = '''
    def has_tetra_demod(self):
        """Check if TETRA demodulator is available."""
        import os
        tetra_dir = "/opt/openwebrx-tetra"
        has_decoder = os.path.isfile(os.path.join(tetra_dir, "tetra_decoder.py"))
        has_tetra_rx = os.path.isfile(os.path.join(tetra_dir, "tetra-rx"))
        has_gnuradio = False
        try:
            import gnuradio
            has_gnuradio = True
        except ImportError:
            pass
        return has_decoder and has_tetra_rx and has_gnuradio
'''
    # Insert method before the last class or at end of FeatureDetector
    last_def = content.rfind('\n    def has_')
    if last_def > 0:
        next_def = content.find('\n    def ', last_def + 1)
        if next_def < 0:
            next_def = content.find('\nclass ', last_def + 1)
            if next_def < 0:
                next_def = len(content)
        content = content[:next_def] + '\n' + method_code + '\n' + content[next_def:]

    with open(feature_file, "w") as f:
        f.write(content)
    # Verify syntax after patching
    import py_compile, shutil, os
    try:
        py_compile.compile(feature_file, doraise=True)
        print("    TETRA feature detection added")
    except py_compile.PyCompileError as e:
        print("    ERROR: feature.py has syntax error after patching!")
        print("    " + str(e))
        backup = feature_file + ".bak.pre-tetra"
        if os.path.isfile(backup):
            shutil.copy2(backup, feature_file)
            print("    Restored feature.py from backup")
        raise SystemExit(1)
else:
    print("    WARNING: Could not find features dict in feature.py")
PYEOF
else
    log "  feature.py already patched"
fi

# --- Patch modes.py for TETRA DMO ---
if ! grep -q '"tetra_dmo"' "$OWRX_PYTHON/owrx/modes.py"; then
    log "  Patching modes.py for TETRA DMO..."
    python3 << 'PYEOF'
modes_file = "/usr/lib/python3/dist-packages/owrx/modes.py"
with open(modes_file, "r") as f:
    lines = f.readlines()

inserted = False
for i, line in enumerate(lines):
    if '"tetra"' in line and 'AnalogMode' in line:
        indent = len(line) - len(line.lstrip())
        dmo_line = ' ' * indent + 'AnalogMode("tetra_dmo", "TETRA DMO", bandpass=Bandpass(-12500, 12500), requirements=["tetra_dmo_decoder"], squelch=False),\n'
        lines.insert(i + 1, dmo_line)
        inserted = True
        break

if inserted:
    with open(modes_file, "w") as f:
        f.writelines(lines)
    import py_compile, shutil, os
    try:
        py_compile.compile(modes_file, doraise=True)
        print("    TETRA DMO mode added to modes.py")
    except py_compile.PyCompileError as e:
        backup = modes_file + ".bak.pre-tetra"
        if os.path.isfile(backup):
            shutil.copy2(backup, modes_file)
            print("    Restored modes.py from backup; DMO patch failed")
        raise SystemExit(1)
else:
    print("    WARNING: TETRA TMO entry not found, can't add DMO")
PYEOF
else
    log "  modes.py already has tetra_dmo"
fi

# --- Patch feature.py for TETRA DMO ---
if ! grep -q 'tetra_dmo_decoder' "$OWRX_PYTHON/owrx/feature.py"; then
    log "  Patching feature.py for TETRA DMO..."
    python3 << 'PYEOF'
feature_file = "/usr/lib/python3/dist-packages/owrx/feature.py"
with open(feature_file, "r") as f:
    content = f.read()

# Dodaj tetra_dmo_decoder do features dict — po istniejącym tetra_decoder
import re
m = re.search(r'("tetra_decoder"\s*:\s*\[[^\]]+\])', content)
if m:
    insert_pos = m.end()
    addition = ',\n            "tetra_dmo_decoder": ["tetra_dmo_demod"]'
    content = content[:insert_pos] + addition + content[insert_pos:]
    # Dodaj has_tetra_dmo_demod method — po has_tetra_demod
    method_code = '''
    def has_tetra_dmo_demod(self):
        """Check if TETRA DMO live decoder is available."""
        import os
        tetra_dir = "/opt/openwebrx-tetra"
        for fn in ("tetra_dmo_decoder.py", "dmo_l1_chain.py", "dmo_pdu_parser.py", "tetra_demod.py"):
            if not os.path.isfile(os.path.join(tetra_dir, fn)):
                return False
        try:
            import gnuradio
        except ImportError:
            return False
        return True
'''
    # Wstaw method po has_tetra_demod
    tm = re.search(r'(def has_tetra_demod\(self\):.*?return has_decoder and has_tetra_rx and has_gnuradio\n)',
                   content, re.DOTALL)
    if tm:
        content = content[:tm.end()] + method_code + content[tm.end():]
    with open(feature_file, "w") as f:
        f.write(content)
    import py_compile, shutil, os
    try:
        py_compile.compile(feature_file, doraise=True)
        print("    TETRA DMO feature detection added")
    except py_compile.PyCompileError as e:
        backup = feature_file + ".bak.pre-tetra"
        if os.path.isfile(backup):
            shutil.copy2(backup, feature_file)
            print("    Restored feature.py; DMO patch failed")
        raise SystemExit(1)
else:
    print("    WARNING: tetra_decoder mapping not found, can't add DMO")
PYEOF
else
    log "  feature.py already has tetra_dmo_decoder"
fi

# --- Patch dsp.py ---
if ! grep -q '"tetra"' "$OWRX_PYTHON/owrx/dsp.py"; then
    log "  Patching dsp.py..."
    python3 << 'PYEOF'
dsp_file = "/usr/lib/python3/dist-packages/owrx/dsp.py"
with open(dsp_file, "r") as f:
    lines = f.readlines()

# Find nxdn elif block and insert tetra after it
inserted = False
search_modes = ['"nxdn"', '"ysf"', '"dmr"', '"dstar"']
for mode in search_modes:
    for i, line in enumerate(lines):
        if mode in line and 'elif demod ==' in line:
            # Find end of this block (next elif/else at same indentation)
            indent = len(line) - len(line.lstrip())
            j = i + 1
            while j < len(lines):
                stripped = lines[j].lstrip()
                cur_indent = len(lines[j]) - len(stripped)
                if cur_indent <= indent and stripped and (stripped.startswith('elif ') or stripped.startswith('else:')):
                    break
                j += 1
            # Insert tetra block before the next elif/else
            body_indent = ' ' * (indent + 4)
            tetra_lines = [
                ' ' * indent + 'elif demod == "tetra":\n',
                body_indent + 'from csdr.chain.tetra import Tetra\n',
                body_indent + 'return Tetra()\n',
            ]
            for k, tl in enumerate(tetra_lines):
                lines.insert(j + k, tl)
            inserted = True
            break
    if inserted:
        break

if inserted:
    with open(dsp_file, "w") as f:
        f.writelines(lines)
    # Verify syntax after patching
    import py_compile, shutil, os
    try:
        py_compile.compile(dsp_file, doraise=True)
        print("    TETRA routing added to dsp.py")
    except py_compile.PyCompileError as e:
        print("    ERROR: dsp.py has syntax error after patching!")
        print("    " + str(e))
        backup = dsp_file + ".bak.pre-tetra"
        if os.path.isfile(backup):
            shutil.copy2(backup, dsp_file)
            print("    Restored dsp.py from backup")
        raise SystemExit(1)
else:
    print("    WARNING: Could not find insertion point in dsp.py")
    print("    Add manually to _getDemodulator() method")
PYEOF
else
    log "  dsp.py already patched"
fi

# --- Patch dsp.py for TETRA DMO ---
if ! grep -q '"tetra_dmo"' "$OWRX_PYTHON/owrx/dsp.py"; then
    log "  Patching dsp.py for TETRA DMO..."
    python3 << 'PYEOF'
dsp_file = "/usr/lib/python3/dist-packages/owrx/dsp.py"
with open(dsp_file, "r") as f:
    lines = f.readlines()

inserted = False
for i, line in enumerate(lines):
    if 'elif demod == "tetra"' in line:
        indent = len(line) - len(line.lstrip())
        body_indent = ' ' * (indent + 4)
        # Znajdź koniec bloku tetra (linia z "return Tetra()" + następna)
        j = i + 1
        while j < len(lines):
            stripped = lines[j].lstrip()
            cur_indent = len(lines[j]) - len(stripped)
            if cur_indent <= indent and stripped and (stripped.startswith('elif ') or stripped.startswith('else:')):
                break
            j += 1
        dmo_block = [
            ' ' * indent + 'elif demod == "tetra_dmo":\n',
            body_indent + 'from csdr.chain.tetra_dmo import TetraDmo\n',
            body_indent + 'return TetraDmo()\n',
        ]
        for k, dl in enumerate(dmo_block):
            lines.insert(j + k, dl)
        inserted = True
        break

if inserted:
    with open(dsp_file, "w") as f:
        f.writelines(lines)
    import py_compile, shutil, os
    try:
        py_compile.compile(dsp_file, doraise=True)
        print("    TETRA DMO routing added to dsp.py")
    except py_compile.PyCompileError as e:
        backup = dsp_file + ".bak.pre-tetra"
        if os.path.isfile(backup):
            shutil.copy2(backup, dsp_file)
            print("    Restored dsp.py; DMO patch failed")
        raise SystemExit(1)
else:
    print("    WARNING: 'elif demod == \"tetra\"' not found, can't add DMO")
PYEOF
else
    log "  dsp.py already has tetra_dmo"
fi

# ── Install frontend (HTML, JS, CSS) ──
STEP=$((STEP_BASE + 3))
log "Step $STEP/$STEP_TOTAL: Installing frontend panel..."

# --- Install TETRA panel HTML ---
python3 << PYEOF
html_file = "$OWRX_PYTHON/htdocs/index.html"
panel_file = "$SCRIPT_DIR/tetra_panel.html"

with open(html_file, "r") as f:
    html = f.read()
with open(panel_file, "r") as f:
    new_panel = f.read().strip()

marker = 'id="openwebrx-panel-metadata-tetra"'
if marker in html:
    # Replace existing panel
    start = html.find(marker)
    div_start = html.rfind("<div", 0, start)
    pos = start
    depth = 1
    while depth > 0 and pos < len(html):
        next_open = html.find("<div", pos + 1)
        next_close = html.find("</div>", pos + 1)
        if next_close < 0:
            break
        if 0 <= next_open < next_close:
            depth += 1
            pos = next_open
        else:
            depth -= 1
            pos = next_close
    div_end = pos + len("</div>")
    html = html[:div_start] + new_panel + html[div_end:]
    print("    Updated TETRA panel in index.html")
else:
    # Insert before the DMR meta panel (or before closing body)
    insert_markers = [
        'id="openwebrx-panel-metadata-dmr"',
        'id="openwebrx-panel-metadata-ysf"',
        'id="openwebrx-panel-metadata-dstar"',
    ]
    inserted = False
    for m in insert_markers:
        pos = html.find(m)
        if pos >= 0:
            div_start = html.rfind("<div", 0, pos)
            # Find proper indentation
            line_start = html.rfind("\n", 0, div_start) + 1
            indent = html[line_start:div_start]
            html = html[:div_start] + new_panel + "\n" + indent + html[div_start:]
            inserted = True
            break
    if not inserted:
        # Last resort: insert before </body>
        body_end = html.find("</body>")
        if body_end >= 0:
            html = html[:body_end] + new_panel + "\n" + html[body_end:]
            inserted = True
    if inserted:
        print("    Added TETRA panel to index.html")
    else:
        print("    WARNING: Could not insert TETRA panel into index.html")

with open(html_file, "w") as f:
    f.write(html)
PYEOF

# --- Install TetraMetaPanel JS ---
python3 << PYEOF
js_file = "$OWRX_PYTHON/htdocs/lib/MetaPanel.js"
panel_js_file = "$SCRIPT_DIR/tetra_panel.js"

with open(js_file, "r") as f:
    content = f.read()
with open(panel_js_file, "r") as f:
    new_js = f.read().strip()

# Check if TetraMetaPanel already exists
if "function TetraMetaPanel(el)" in content:
    # Replace existing
    start = content.find("function TetraMetaPanel(el)")
    types_pos = content.find("MetaPanel.types", start)
    if types_pos >= 0:
        content = content[:start] + new_js + "\n\n" + content[types_pos:]
        print("    Updated TetraMetaPanel in MetaPanel.js")
else:
    # Insert before MetaPanel.types
    types_pos = content.find("MetaPanel.types")
    if types_pos >= 0:
        content = content[:types_pos] + new_js + "\n\n" + content[types_pos:]
        print("    Added TetraMetaPanel to MetaPanel.js")
    else:
        # Append at end
        content += "\n\n" + new_js + "\n"
        print("    Appended TetraMetaPanel to MetaPanel.js")

# Register in MetaPanel.types
import re
if '"tetra"' not in content or 'TetraMetaPanel' not in content.split("MetaPanel.types")[-1]:
    # Add tetra to MetaPanel.types = { ... }
    types_match = re.search(r'(MetaPanel\.types\s*=\s*\{)', content)
    if types_match:
        insert_pos = types_match.end()
        # Check if there's already content after the brace
        after = content[insert_pos:insert_pos+1]
        if after == '\n' or after == ' ':
            content = content[:insert_pos] + '\n    "tetra": TetraMetaPanel,' + content[insert_pos:]
        else:
            content = content[:insert_pos] + '\n    "tetra": TetraMetaPanel,' + content[insert_pos:]
        print("    Registered tetra in MetaPanel.types")

with open(js_file, "w") as f:
    f.write(content)
PYEOF

# --- Install TETRA CSS ---
python3 << 'PYEOF'
css_file = "/usr/lib/python3/dist-packages/htdocs/css/openwebrx.css"
with open(css_file, "r") as f:
    css = f.read()

tetra_css = """
/* TETRA panel styles */
.openwebrx-tetra-panel {
    padding: 5px 10px;
    font-size: 0.85em;
}
.openwebrx-tetra-panel .tetra-header {
    font-weight: bold;
    font-size: 1.1em;
    margin-bottom: 3px;
    color: #74c0fc;
}
.openwebrx-tetra-panel .tetra-label {
    color: #868e96;
    margin-right: 3px;
}
.openwebrx-tetra-panel .tetra-row {
    margin: 1px 0;
}
.openwebrx-tetra-panel .tetra-timeslots {
    margin-top: 3px;
}
.openwebrx-tetra-panel .tetra-ts {
    display: inline-block;
    width: 20px;
    text-align: center;
    margin: 0 2px;
    padding: 1px 4px;
    border: 1px solid #495057;
    border-radius: 3px;
    font-size: 0.9em;
}
.openwebrx-tetra-panel .tetra-ts.busy {
    background: #e67700;
    color: #fff;
}
.openwebrx-tetra-panel .tetra-ts.idle {
    background: #2b8a3e;
    color: #fff;
}
"""

if "tetra-ts.busy" not in css:
    css += tetra_css
    with open(css_file, "w") as f:
        f.write(css)
    print("    Added TETRA CSS styles")
else:
    # Replace existing TETRA CSS
    import re
    css = re.sub(r'/\* TETRA panel styles \*/.*?(?=\n/\*|\Z)', tetra_css.strip() + '\n', css, flags=re.DOTALL)
    with open(css_file, "w") as f:
        f.write(css)
    print("    Updated TETRA CSS styles")
PYEOF

# ── Clear cache and restart ──
log "Clearing Python cache..."
find "$OWRX_PYTHON" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$OWRX_PYTHON" -name "*.pyc" -delete 2>/dev/null || true

# ── Verify ──
echo ""
verify_installation

# ── Restart service ──
if [[ -z "$NO_RESTART" ]]; then
    echo ""
    log "Restarting OpenWebRX+..."
    systemctl restart openwebrx 2>/dev/null || warn "Could not restart openwebrx service"
fi

echo ""
if [[ "$MODE" == "install" ]]; then
    log "=== Installation complete! ==="
    log ""
    log "Next steps:"
    log "  1. Open the web interface"
    log "  2. Go to Settings -> SDR -> Profiles"
    log "  3. Create a profile for your TETRA frequency"
    log "  4. Set modulation to 'TETRA'"
else
    log "=== Update complete! ==="
fi
