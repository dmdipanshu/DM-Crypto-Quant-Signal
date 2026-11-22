#!/usr/bin/env bash
# install-systemd.sh - Install SignalForge Q5.2 collector systemd service and timers
#
# Usage:
#   sudo bash docker/systemd/install-systemd.sh [PROJECT_DIR] [SERVICE_USER]
#
# Defaults:
#   PROJECT_DIR=/home/ubuntu/DM-Crypto-Quant-Signal
#   SERVICE_USER=ubuntu
#
# Installs:
#   /etc/systemd/system/signalforge-collector.service
#   /etc/systemd/system/signalforge-collector@08.timer  (primary, :08 UTC)
#   /etc/systemd/system/signalforge-collector@12.timer  (retry, :12 UTC)
#
# Safety:
#   - Does NOT start or enable timers unless ENABLE_TIMERS=1 is explicitly set.
#   - Persistent=false in timer units (no catch-up/backfill).
#   - Verifies compose config and required files before install.
#
set -euo pipefail

PROJECT_DIR="${1:-/home/ubuntu/DM-Crypto-Quant-Signal}"
SERVICE_USER="${2:-ubuntu}"
SYSTEMD_DIR="/etc/systemd/system"
ENABLE_TIMERS="${ENABLE_TIMERS:-0}"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root (sudo)."
  exit 1
fi

if [ ! -d "${PROJECT_DIR}" ]; then
  echo "ERROR: project directory does not exist: ${PROJECT_DIR}"
  exit 1
fi
PROJECT_DIR="$(cd "${PROJECT_DIR}" && pwd)"

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "ERROR: service user does not exist: ${SERVICE_USER}"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed or not in PATH"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose is not available"
  exit 1
fi

COMPOSE_FILE="${PROJECT_DIR}/compose.yaml"
for required in \
  "${COMPOSE_FILE}" \
  "${PROJECT_DIR}/docker/systemd/signalforge-collector.service" \
  "${PROJECT_DIR}/docker/systemd/signalforge-collector@.timer" \
  "${PROJECT_DIR}/docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json" \
  "${PROJECT_DIR}/docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json" \
  "${PROJECT_DIR}/docs/validation/approvals/collection-epoch3-authorization.json" \
  "${PROJECT_DIR}/docs/validation/approvals/scheduler-epoch3-authorization.json"; do
  if [ ! -f "${required}" ]; then
    echo "ERROR: required file missing: ${required}"
    exit 1
  fi
done

if ! grep -q "q5-collector" "${COMPOSE_FILE}"; then
  echo "ERROR: collector profile q5-collector not found in ${COMPOSE_FILE}"
  exit 1
fi

if ! docker compose -f "${COMPOSE_FILE}" config --quiet >/dev/null 2>&1; then
  echo "ERROR: invalid compose configuration: ${COMPOSE_FILE}"
  exit 1
fi

# Ensure service user can access Docker daemon.
if getent group docker >/dev/null 2>&1; then
  usermod -aG docker "${SERVICE_USER}" || true
else
  echo "ERROR: docker group does not exist; install Docker and ensure docker group is present."
  exit 1
fi

# Ensure writable dirs exist and are owned by container app uid (10001).
install -d -m 0755 "${PROJECT_DIR}/data/prospective/binance/spot/BTC/USDT/1h"
install -d -m 0755 "${PROJECT_DIR}/logs" "${PROJECT_DIR}/reports/q5-epoch3"
chown -R 10001:10001 "${PROJECT_DIR}/data/prospective" "${PROJECT_DIR}/logs" "${PROJECT_DIR}/reports" || true

# Render service/timer with configured path/user.
SERVICE_TMP="$(mktemp)"
TIMER_TMP="$(mktemp)"
sed \
  -e "s|/home/ubuntu/DM-Crypto-Quant-Signal|${PROJECT_DIR}|g" \
  -e "s|^User=ubuntu$|User=${SERVICE_USER}|g" \
  "${PROJECT_DIR}/docker/systemd/signalforge-collector.service" > "${SERVICE_TMP}"

sed \
  -e "s|/home/ubuntu/DM-Crypto-Quant-Signal|${PROJECT_DIR}|g" \
  "${PROJECT_DIR}/docker/systemd/signalforge-collector@.timer" > "${TIMER_TMP}"

install -m 0644 "${SERVICE_TMP}" "${SYSTEMD_DIR}/signalforge-collector.service"
install -m 0644 "${TIMER_TMP}" "${SYSTEMD_DIR}/signalforge-collector@.timer"
ln -sf "${SYSTEMD_DIR}/signalforge-collector@.timer" "${SYSTEMD_DIR}/signalforge-collector@08.timer"
ln -sf "${SYSTEMD_DIR}/signalforge-collector@.timer" "${SYSTEMD_DIR}/signalforge-collector@12.timer"

rm -f "${SERVICE_TMP}" "${TIMER_TMP}"

systemctl daemon-reload

echo "Installed systemd units:"
echo "  ${SYSTEMD_DIR}/signalforge-collector.service"
echo "  ${SYSTEMD_DIR}/signalforge-collector@08.timer (primary, :08 UTC)"
echo "  ${SYSTEMD_DIR}/signalforge-collector@12.timer (retry, :12 UTC)"
echo ""
echo "Verification commands:"
echo "  systemctl cat signalforge-collector.service"
echo "  systemctl list-timers --all | grep signalforge-collector"
echo "  docker compose -f ${COMPOSE_FILE} config >/dev/null"
echo ""
if [ "${ENABLE_TIMERS}" = "1" ]; then
  systemctl enable --now signalforge-collector@08.timer
  systemctl enable --now signalforge-collector@12.timer
  echo "Timers enabled and started (ENABLE_TIMERS=1)."
else
  echo "Timers NOT enabled/started. To enable explicitly:"
  echo "  sudo systemctl enable --now signalforge-collector@08.timer"
  echo "  sudo systemctl enable --now signalforge-collector@12.timer"
fi
