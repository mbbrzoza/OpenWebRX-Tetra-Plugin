#!/bin/bash
# Buduje pdsch_ue (srsRAN 4G) dla trybu LTE w OpenWebRX+ i instaluje go do /opt/openwebrx-tetra/bin.
# Author: SP8MB
#
# Budowany jest tylko przykład pdsch_ue (biblioteka PHY), bez srsue/srsenb/srsepc/5G/GUI.
# Łatka: zmienna PDSCH_ALL_SI=1 każe dekodować SI-RNTI także w oknach SI (SIB2..SIBn),
# a nie wyłącznie SIB1 w podramce 5 (zachowanie oryginalne).
#
#   sudo bash build_lte_pdsch.sh            # deps + clone + build + install
#   JOBS=2 sudo -E bash build_lte_pdsch.sh  # ograniczenie rdzeni (OWRX działa obok)
set -euo pipefail

SRSRAN_REPO="https://github.com/srsran/srsRAN_4G.git"
SRSRAN_COMMIT="${SRSRAN_COMMIT:-bef8680}"
SRC_DIR="${SRC_DIR:-/usr/local/src/srsRAN_4G}"
INSTALL_DIR="${INSTALL_DIR:-/opt/openwebrx-tetra}"
JOBS="${JOBS:-$(nproc)}"

log() { echo -e "\033[1;32m[lte-build]\033[0m $*"; }

log "Zależności"
apt-get install -y -q cmake g++ git libfftw3-dev libmbedtls-dev libboost-program-options-dev \
    libconfig++-dev libsctp-dev >/dev/null

if [[ ! -d "$SRC_DIR/.git" ]]; then
    log "Klon srsRAN_4G -> $SRC_DIR"
    git clone -q "$SRSRAN_REPO" "$SRC_DIR"
fi
git -C "$SRC_DIR" fetch -q --depth 50 origin master 2>/dev/null || true
git -C "$SRC_DIR" checkout -q -f "$SRSRAN_COMMIT"

log "Łatka PDSCH_ALL_SI"
F="$SRC_DIR/lib/examples/pdsch_ue.c"
if ! grep -q PDSCH_ALL_SI "$F"; then
    sed -i 's|if ((sf_idx == 5 \&\& (sfn % 2) == 0) \|\| mch_table\[sf_idx\] == 1) {|if ((sf_idx == 5 \&\& (sfn % 2) == 0) \|\| mch_table[sf_idx] == 1 \|\| getenv("PDSCH_ALL_SI")) {|' "$F"
    grep -q PDSCH_ALL_SI "$F" || { echo "łatka nie weszła (zmiana w źródłach srsRAN?)"; exit 1; }
    grep -q "#include <stdlib.h>" "$F" || sed -i '0,/#include/s//#include <stdlib.h>\n#include/' "$F"
fi

log "cmake + make pdsch_ue (-j$JOBS)"
mkdir -p "$SRC_DIR/build"
cd "$SRC_DIR/build"
cmake .. -DCMAKE_BUILD_TYPE=Release -DENABLE_GUI=OFF -DENABLE_UHD=OFF -DENABLE_BLADERF=OFF \
    -DENABLE_SOAPYSDR=OFF -DENABLE_ZEROMQ=OFF -DENABLE_SRSUE=OFF -DENABLE_SRSENB=OFF \
    -DENABLE_SRSEPC=OFF -DENABLE_5GNR=OFF >cmake.log 2>&1
make -j"$JOBS" pdsch_ue >make.log 2>&1

install -D -m 755 lib/examples/pdsch_ue "$INSTALL_DIR/bin/pdsch_ue"
log "Zainstalowano $INSTALL_DIR/bin/pdsch_ue"
