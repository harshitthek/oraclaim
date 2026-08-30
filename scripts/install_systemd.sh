#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# 1-Click Systemd Service Installer for OCI ARM Smart Auto-Claimer
# ==============================================================================

SERVICE_NAME="claim_arm.service"
INSTALL_DIR="$(pwd)"
CURRENT_USER="$(whoami)"

echo "=== Installing ${SERVICE_NAME} ==="
echo "Install Directory: ${INSTALL_DIR}"
echo "Running User:      ${CURRENT_USER}"

# 1. Disable heavy DNF timers on Oracle Linux if present to save RAM
if systemctl list-unit-files | grep -q "dnf-makecache.timer"; then
    echo "[+] Disabling dnf-makecache.timer to preserve RAM..."
    sudo systemctl stop dnf-makecache.timer dnf-makecache.service 2>/dev/null || true
    sudo systemctl disable dnf-makecache.timer 2>/dev/null || true
fi

# 2. Ensure dependencies are installed
echo "[+] Checking Python OCI SDK..."
if ! python3 -c "import oci" 2>/dev/null; then
    echo "[+] Installing oci SDK..."
    python3 -m pip install --user oci || sudo pip3 install oci
fi

# 3. Create log file with correct permissions
touch "${INSTALL_DIR}/claim_arm.log"
chmod 664 "${INSTALL_DIR}/claim_arm.log"

# 4. Write systemd unit file
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

echo "[+] Generating ${SERVICE_PATH}..."
sudo tee "${SERVICE_PATH}" > /dev/null <<EOF
[Unit]
Description=Oracle Cloud ARM Smart Auto-Claimer
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=/bin/sh -c "exec /usr/bin/python3 -u -m src.cli >> ${INSTALL_DIR}/claim_arm.log 2>&1"
Restart=always
RestartSec=5
MemoryMax=200M

[Install]
WantedBy=multi-user.target
EOF

# 5. Enable and start service
echo "[+] Reloading systemd daemon and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

sleep 2
echo "=== Installation Complete ==="
sudo systemctl status "${SERVICE_NAME}" --no-pager
echo ""
echo "To view live logs: tail -f ${INSTALL_DIR}/claim_arm.log"
