#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="claim_arm.service"
INSTALL_DIR="$(pwd)"

echo "==========================================================================="
echo "                OCI ARM AUTO-CLAIMER STATUS & RECENT LOGS                  "
echo "==========================================================================="

if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    echo "🟢 Service Status: ACTIVE (RUNNING)"
else
    echo "🔴 Service Status: INACTIVE / STOPPED"
fi

echo ""
echo "--- [SERVICE INFO] ---"
sudo systemctl status "${SERVICE_NAME}" --no-pager || true

echo ""
echo "--- [LATEST 25 LOG ENTRIES] ---"
if [ -f "${INSTALL_DIR}/claim_arm.log" ]; then
    tail -n 25 "${INSTALL_DIR}/claim_arm.log"
else
    echo "Log file ${INSTALL_DIR}/claim_arm.log not found yet."
fi
echo "==========================================================================="
