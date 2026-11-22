#!/usr/bin/env bash
# install-systemd.sh - Install SignalForge Q5.2 collector systemd service and timers
#
# Usage:
#   sudo bash install-systemd.sh [/opt/signalforge]
#
# Installs:
#   /etc/systemd/system/signalforge-collector.service
#   /etc/systemd/system/signalforge-collector@.timer
#   Primary timer: signalforge-collector@primary.timer (runs at :08 UTC)
#   Retry timer:   signalforge-collector@retry.timer   (runs at :12 UTC)
#
# Requirements:
#   - Docker Compose installed
#   - Project directory at /opt/signalforge (or custom path)
#   - compose.yaml with collector service
#
# Security:
#   - Service runs as unprivileged signalforge user
#   - NoNewPrivileges=true, ProtectSystem=strict
#   - Timers do NOT enable persistent catch-up (no historical backfill)
#
set -euo pipefail

INSTALL_DIR="${1:-/opt/signalforge}"

echo "=== SignalForge Q5.2 Collector Systemd Installer ==="
echo "Install directory: ${INSTALL_DIR}"
echo ""

# Verify prerequisites
if ! command -v docker &>/dev/null; then
    echo "ERROR: docker is not installed or not in PATH"
    exit 1
fi
if ! command -v docker compose &>/dev/null && ! docker compose version &>/dev/null; then
    echo "ERROR: docker compose is not available"
    exit 1
fi

# Create signalforge user if not exists
if ! id "signalforge" &>/dev/null; then
    echo "Creating system user 'signalforge'..."
    useradd --system --create-home --shell /usr/sbin/nologin signalforge
fi

# Verify compose.yaml exists and has collector service
COMPOSE_FILE="${INSTALL_DIR}/compose.yaml"
if [ ! -f "${COMPOSE_FILE}" ]; then
    echo "ERROR: ${COMPOSE_FILE} not found"
    exit 1
fi
if ! grep -q "signalforge-collector:" "${COMPOSE_FILE}"; then
    echo "ERROR: 'signalforge-collector' service not found in ${COMPOSE_FILE}"
    exit 1
fi

# Copy unit files
SYSTEMD_DIR="/etc/systemd/system"
echo "Installing systemd units to ${SYSTEMD_DIR}..."

cp "${INSTALL_DIR}/docker/systemd/signalforge-collector.service" "${SYSTEMD_DIR}/"
cp "${INSTALL_DIR}/docker/systemd/signalforge-collector@.timer" "${SYSTEMD_DIR}/"

# Create timer instances
# Primary timer: runs at minute 08 of every hour (UTC)
ln -sf "${SYSTEMD_DIR}/signalforge-collector@.timer" "${SYSTEMD_DIR}/signalforge-collector@primary.timer"
# Retry timer: runs at minute 12 of every hour (UTC)
ln -sf "${SYSTEMD_DIR}/signalforge-collector@.timer" "${SYSTEMD_DIR}/signalforge-collector@retry.timer"

# Reload systemd
echo "Reloading systemd daemon..."
systemctl daemon-reload

# Enable timers (do NOT start them; collection must be explicitly authorized)
echo "Enabling primary timer (runs at :08 UTC)..."
systemctl enable signalforge-collector@primary.timer

echo "Enabling retry timer (runs at :12 UTC)..."
systemctl enable signalforge-collector@retry.timer

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Timer schedules (all times UTC):"
echo "  Primary:   signalforge-collector@primary.timer  -> every hour at :08"
echo "  Retry:     signalforge-collector@retry.timer    -> every hour at :12"
echo ""
echo "To start collection (after authorization):"
echo "  sudo systemctl start signalforge-collector@primary.timer"
echo ""
echo "To check status:"
echo "  sudo systemctl status signalforge-collector@primary.timer"
echo "  sudo systemctl status signalforge-collector@retry.timer"
echo "  sudo journalctl -u signalforge-collector@primary.timer -n 50"
echo ""
echo "To disable timers:"
echo "  sudo systemctl disable signalforge-collector@primary.timer"
echo "  sudo systemctl disable signalforge-collector@retry.timer"
echo ""
echo "WARNING: Collection does NOT start automatically. Timers must be"
echo "enabled and started only after appropriate authorization is in place."
echo "No catch-up is performed for missed runs (Persistent=true with no"
echo "RandomizedDelaySec prevents backfill on timer restart)."
