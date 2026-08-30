#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="claim_arm.service"

echo "=== Stopping and removing ${SERVICE_NAME} ==="
sudo systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
sudo systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
sudo rm -f "/etc/systemd/system/${SERVICE_NAME}"
sudo systemctl daemon-reload
echo "[✔] Service uninstalled cleanly."
