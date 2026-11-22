#!/usr/bin/env bash
# uninstall-systemd.sh - Uninstall SignalForge Q5.2 collector systemd service and timers
set -euo pipefail

SYSTEMD_DIR="/etc/systemd/system"

echo "=== SignalForge Q5.2 Collector Systemd Uninstaller ==="
systemctl disable --now signalforge-collector@08.timer 2>/dev/null || true
systemctl disable --now signalforge-collector@12.timer 2>/dev/null || true
systemctl disable --now signalforge-collector@primary.timer 2>/dev/null || true
systemctl disable --now signalforge-collector@retry.timer 2>/dev/null || true
systemctl stop signalforge-collector.service 2>/dev/null || true

rm -f "${SYSTEMD_DIR}/signalforge-collector.service"
rm -f "${SYSTEMD_DIR}/signalforge-collector@.timer"
rm -f "${SYSTEMD_DIR}/signalforge-collector@08.timer"
rm -f "${SYSTEMD_DIR}/signalforge-collector@12.timer"
rm -f "${SYSTEMD_DIR}/signalforge-collector@primary.timer"
rm -f "${SYSTEMD_DIR}/signalforge-collector@retry.timer"

systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true

echo "Uninstall complete. No collection data was deleted."
