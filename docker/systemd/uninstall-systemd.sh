#!/usr/bin/env bash
# uninstall-systemd.sh - Uninstall SignalForge Q5.2 collector systemd service and timers
#
# Usage:
#   sudo bash uninstall-systemd.sh
#
# Stops and disables timers, removes unit files.
#
set -euo pipefail

SYSTEMD_DIR="/etc/systemd/system"

echo "=== SignalForge Q5.2 Collector Systemd Uninstaller ==="
echo ""

# Stop and disable timers if they are active/enabled
echo "Stopping and disabling timers..."
systemctl disable --now signalforge-collector@primary.timer 2>/dev/null || true
systemctl disable --now signalforge-collector@retry.timer 2>/dev/null || true

# Stop any running service instance
systemctl stop signalforge-collector.service 2>/dev/null || true

# Remove unit files
echo "Removing unit files..."
rm -f "${SYSTEMD_DIR}/signalforge-collector.service"
rm -f "${SYSTEMD_DIR}/signalforge-collector@.timer"
rm -f "${SYSTEMD_DIR}/signalforge-collector@primary.timer"
rm -f "${SYSTEMD_DIR}/signalforge-collector@retry.timer"

# Reload systemd
echo "Reloading systemd daemon..."
systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true

echo ""
echo "=== Uninstall Complete ==="
echo "SignalForge Q5.2 collector systemd units have been removed."
echo ""
echo "No collection data was deleted. Persistent data remains in:"
echo "  ${SYSTEMD_DIR}/../opt/signalforge/data/prospective/ (or custom path)"
echo ""
echo "To fully remove SignalForge, also remove the project directory."
echo "Docker images and volumes are NOT removed by this script."
