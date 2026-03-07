#!/bin/bash
# TETRA module installer for OpenWebRX+
# Installs TETRA voice decoding support on Raspberry Pi
#
# Usage: sudo bash install.sh
#
# Prerequisites on the RPi:
#   - OpenWebRX+ v1.2.x installed
#   - Internet access for apt packages
#
# This script:
#   1. Installs GNURadio and libosmocore (for DQPSK demod and tetra-rx)
#   2. Compiles tetra-rx (osmo-tetra) for ARM64
#   3. Builds ETSI ACELP codec (cdecoder/sdecoder)
#   4. Installs TETRA decoder scripts to /opt/openwebrx-tetra/
#   5. Patches OpenWebRX+ to add TETRA mode

set -e

INSTALL_DIR="/opt/openwebrx-tetra"
OWRX_PYTHON="/usr/lib/python3/dist-packages"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()   { echo -e "${GREEN}[TETRA]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Check root
[[ $EUID -ne 0 ]] && error "This script must be run as root (sudo)"

# Check OpenWebRX+
[[ -f "$OWRX_PYTHON/owrx/modes.py" ]] || error "OpenWebRX+ not found at $OWRX_PYTHON"

log "=== TETRA Module Installer for OpenWebRX+ ==="

# ─── Step 1: Install system dependencies ───
log "Step 1: Installing system dependencies..."
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

log "GNURadio $(python3 -c 'from gnuradio import gr; print(gr.version())' 2>/dev/null || echo 'check version') installed"

# ─── Step 2: Create install directory ───
log "Step 2: Setting up $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# Copy decoder scripts
cp "$SCRIPT_DIR/tetra_demod.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/tetra_decoder.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/tetra_decoder.py"

# ─── Step 3: Compile tetra-rx ───
log "Step 3: Compiling tetra-rx (osmo-tetra)..."
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

# ─── Step 4: Build ETSI ACELP codec ───
log "Step 4: Building ETSI ACELP codec..."

CODEC_DIR="$OSMO_TETRA_SRC/etsi_codec-patches"
if [[ -d "$CODEC_DIR" && -f "$CODEC_DIR/download_and_patch.sh" ]]; then
    cd "$CODEC_DIR"

    if [[ ! -f "$INSTALL_DIR/cdecoder" ]]; then
        log "Running ETSI codec download and build script..."
        bash download_and_patch.sh 2>&1 | tail -10

        # Build codec
        if [[ -d codec ]]; then
            cd codec
            make 2>&1 | tail -5
            cp cdecoder sdecoder "$INSTALL_DIR/" 2>/dev/null || true
            cd "$CODEC_DIR"
        fi

        if [[ -f "$INSTALL_DIR/cdecoder" && -f "$INSTALL_DIR/sdecoder" ]]; then
            log "ACELP codec built successfully"
        else
            warn "ACELP codec build failed. Audio decoding will not work."
            warn "You may need to build cdecoder/sdecoder manually."
            warn "See: $CODEC_DIR/README"
        fi
    else
        log "ACELP codec already installed"
    fi
else
    warn "ETSI codec patches not found. Audio decoding will not work."
    warn "Copy cdecoder and sdecoder to $INSTALL_DIR/ manually."
fi

# ─── Step 5: Install OpenWebRX+ module ───
log "Step 5: Installing OpenWebRX+ TETRA module..."

# Install csdr module
cp "$SCRIPT_DIR/csdr_module_tetra.py" "$OWRX_PYTHON/csdr/module/tetra.py"
cp "$SCRIPT_DIR/csdr_chain_tetra.py" "$OWRX_PYTHON/csdr/chain/tetra.py"

# ─── Step 6: Patch OpenWebRX+ ───
log "Step 6: Patching OpenWebRX+ for TETRA support..."

# Backup originals
for f in owrx/modes.py owrx/feature.py owrx/dsp.py; do
    cp "$OWRX_PYTHON/$f" "$OWRX_PYTHON/$f.bak.pre-tetra" 2>/dev/null || true
done

# --- Patch modes.py: Add TETRA mode ---
if ! grep -q '"tetra"' "$OWRX_PYTHON/owrx/modes.py"; then
    log "  Adding TETRA mode to modes.py..."
    # Find the line with AnalogMode("nfm" and add TETRA after it
    python3 << 'PYEOF'
import re

modes_file = "/usr/lib/python3/dist-packages/owrx/modes.py"
with open(modes_file, "r") as f:
    content = f.read()

# Add TETRA mode after NXDN (last digital voice mode)
# Find the NXDN AnalogMode line
nxdn_pattern = r'(AnalogMode\("nxdn"[^)]+\))'
match = re.search(nxdn_pattern, content)

if match:
    insert_pos = match.end()
    # Add comma if needed
    tetra_mode = ',\n            AnalogMode("tetra", "TETRA", bandpass=Bandpass(-12500, 12500), requirements=["tetra_decoder"], squelch=False)'
    content = content[:insert_pos] + tetra_mode + content[insert_pos:]
    with open(modes_file, "w") as f:
        f.write(content)
    print("  TETRA mode added to modes.py")
else:
    # Fallback: add after last AnalogMode with digital voice
    # Find the mappings list and add before the first DigitalMode
    digital_pattern = r'(\s+DigitalMode\()'
    match = re.search(digital_pattern, content)
    if match:
        insert_pos = match.start()
        tetra_mode = '\n            AnalogMode("tetra", "TETRA", bandpass=Bandpass(-12500, 12500), requirements=["tetra_decoder"], squelch=False),\n'
        content = content[:insert_pos] + tetra_mode + content[insert_pos:]
        with open(modes_file, "w") as f:
            f.write(content)
        print("  TETRA mode added to modes.py (fallback)")
    else:
        print("  WARNING: Could not find insertion point in modes.py")
PYEOF
else
    log "  TETRA mode already in modes.py"
fi

# --- Patch feature.py: Add TETRA feature detection ---
if ! grep -q 'tetra_decoder' "$OWRX_PYTHON/owrx/feature.py"; then
    log "  Adding TETRA feature detection to feature.py..."
    python3 << 'PYEOF'
import re

feature_file = "/usr/lib/python3/dist-packages/owrx/feature.py"
with open(feature_file, "r") as f:
    content = f.read()

# Add tetra_decoder feature to the features dict
# Find "digital_voice_digiham" or similar feature entry
features_pattern = r'("digital_voice_digiham"\s*:\s*\[[^\]]+\])'
match = re.search(features_pattern, content)

if match:
    insert_pos = match.end()
    tetra_feature = ',\n            "tetra_decoder": ["tetra_demod"]'
    content = content[:insert_pos] + tetra_feature + content[insert_pos:]

    # Add has_tetra_demod method before the last method or at class end
    # Find the class body - add the detection method
    method_code = '''
    def has_tetra_demod(self):
        """Check if TETRA demodulator is available."""
        import os
        import shutil
        tetra_dir = "/opt/openwebrx-tetra"
        # Check for tetra_decoder.py and tetra-rx
        has_decoder = os.path.isfile(os.path.join(tetra_dir, "tetra_decoder.py"))
        has_tetra_rx = os.path.isfile(os.path.join(tetra_dir, "tetra-rx"))
        # Check for GNURadio
        has_gnuradio = False
        try:
            import gnuradio
            has_gnuradio = True
        except ImportError:
            pass
        return has_decoder and has_tetra_rx and has_gnuradio
'''

    # Insert the method before the last class in the file
    # Find "class FeatureDetector" or similar and add method at end of class
    # Simple approach: find the last method definition and add after it
    last_def = content.rfind('\n    def has_')
    if last_def > 0:
        # Find the end of this method (next def or end of class)
        next_def = content.find('\n    def ', last_def + 1)
        if next_def < 0:
            # Add before end of file
            next_def = content.find('\nclass ', last_def + 1)
            if next_def < 0:
                next_def = len(content)
        content = content[:next_def] + '\n' + method_code + '\n' + content[next_def:]

    with open(feature_file, "w") as f:
        f.write(content)
    print("  TETRA feature detection added to feature.py")
else:
    print("  WARNING: Could not find features dict in feature.py")
PYEOF
else
    log "  TETRA feature detection already in feature.py"
fi

# --- Patch dsp.py: Add TETRA demodulator routing ---
if ! grep -q '"tetra"' "$OWRX_PYTHON/owrx/dsp.py"; then
    log "  Adding TETRA routing to dsp.py..."
    python3 << 'PYEOF'
import re

dsp_file = "/usr/lib/python3/dist-packages/owrx/dsp.py"
with open(dsp_file, "r") as f:
    content = f.read()

# Find the _getDemodulator method and add TETRA case
# Look for the last elif in _getDemodulator (e.g., nxdn or ysf)
nxdn_pattern = r'(elif demod == "nxdn":\s*\n\s*from csdr\.chain\.digiham import Nxdn\s*\n\s*return Nxdn\([^)]*\))'
match = re.search(nxdn_pattern, content)

if match:
    insert_pos = match.end()
    tetra_routing = '''
            elif demod == "tetra":
                from csdr.chain.tetra import Tetra
                return Tetra()'''
    content = content[:insert_pos] + tetra_routing + content[insert_pos:]
    with open(dsp_file, "w") as f:
        f.write(content)
    print("  TETRA routing added to dsp.py")
else:
    # Fallback: look for any digital voice elif block
    fallback_pattern = r'(elif demod == "(?:ysf|dstar|dmr)":\s*\n\s*from csdr\.chain\.\S+ import \S+\s*\n\s*return \S+\([^)]*\))'
    match = re.search(fallback_pattern, content)
    if match:
        insert_pos = match.end()
        tetra_routing = '''
            elif demod == "tetra":
                from csdr.chain.tetra import Tetra
                return Tetra()'''
        content = content[:insert_pos] + tetra_routing + content[insert_pos:]
        with open(dsp_file, "w") as f:
            f.write(content)
        print("  TETRA routing added to dsp.py (fallback)")
    else:
        print("  WARNING: Could not find insertion point in dsp.py")
        print("  You may need to add the TETRA case manually to _getDemodulator()")
PYEOF
else
    log "  TETRA routing already in dsp.py"
fi

# ─── Step 7: Clear Python cache ───
log "Step 7: Clearing Python cache..."
find "$OWRX_PYTHON" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$OWRX_PYTHON" -name "*.pyc" -delete 2>/dev/null || true

# ─── Step 8: Verify installation ───
log "Step 8: Verifying installation..."

ERRORS=0
for f in tetra_decoder.py tetra_demod.py tetra-rx; do
    if [[ -f "$INSTALL_DIR/$f" ]]; then
        log "  ✓ $f"
    else
        warn "  ✗ $f missing"
        ERRORS=$((ERRORS + 1))
    fi
done

for f in cdecoder sdecoder; do
    if [[ -f "$INSTALL_DIR/$f" ]]; then
        log "  ✓ $f"
    else
        warn "  ✗ $f missing (audio decoding won't work)"
    fi
done

if grep -q '"tetra"' "$OWRX_PYTHON/owrx/modes.py"; then
    log "  ✓ TETRA mode registered"
else
    warn "  ✗ TETRA mode not in modes.py"
    ERRORS=$((ERRORS + 1))
fi

if grep -q 'tetra_decoder' "$OWRX_PYTHON/owrx/feature.py"; then
    log "  ✓ TETRA feature detection registered"
else
    warn "  ✗ TETRA feature detection not in feature.py"
    ERRORS=$((ERRORS + 1))
fi

if grep -q '"tetra"' "$OWRX_PYTHON/owrx/dsp.py"; then
    log "  ✓ TETRA routing registered"
else
    warn "  ✗ TETRA routing not in dsp.py"
    ERRORS=$((ERRORS + 1))
fi

# ─── Done ───
echo ""
if [[ $ERRORS -eq 0 ]]; then
    log "=== Installation complete! ==="
    log ""
    log "Next steps:"
    log "  1. Restart OpenWebRX+:  sudo systemctl restart openwebrx"
    log "  2. Open the web interface"
    log "  3. Select a TETRA profile (e.g., TETRA1 at 391 MHz)"
    log "  4. Choose 'TETRA' modulation in the receiver panel"
    log ""
    log "  Note: Existing TETRA profiles use NFM modulation."
    log "  Change them to TETRA in Settings → SDR → Profiles."
else
    warn "=== Installation completed with $ERRORS error(s) ==="
    warn "Check warnings above and fix manually."
fi
