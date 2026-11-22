#!/usr/bin/env bash
# monitor-collector.sh - Monitor SignalForge Q5.2 collector status
set -euo pipefail

PROJECT_DIR="${1:-/home/ubuntu/DM-Crypto-Quant-Signal}"
COMPOSE_FILE="${PROJECT_DIR}/compose.yaml"

echo "=== SignalForge Q5.2 Collector Monitor ==="
echo "Project: ${PROJECT_DIR}"
echo ""

echo "--- Systemd Timers ---"
systemctl list-timers --all | grep signalforge-collector || echo "(no signalforge-collector timers found)"
echo ""
systemctl status signalforge-collector@08.timer --no-pager 2>/dev/null || true
systemctl status signalforge-collector@12.timer --no-pager 2>/dev/null || true
echo ""

echo "--- Recent Collector Logs ---"
journalctl -u signalforge-collector.service -n 50 --no-pager 2>/dev/null || echo "(no journal entries)"
echo ""

echo "--- Docker Compose Collector Status ---"
docker compose -f "${COMPOSE_FILE}" ps 2>/dev/null || echo "(compose ps failed)"
echo ""

echo "--- Q5 Governance Endpoint ---"
TOKEN="${SIGNALFORGE_API_TOKEN:-}"
if [ -n "${TOKEN}" ]; then
  curl -sf -H "Authorization: Bearer ${TOKEN}" \
    http://127.0.0.1:8000/api/v1/q5/prospective/governance || echo "(endpoint unavailable)"
else
  echo "SIGNALFORGE_API_TOKEN not set; skipping authenticated governance check."
fi
echo ""

echo "--- Prospective Collection Status ---"
cd "${PROJECT_DIR}"
uv run python scripts/run_q5_prospective_collection.py \
  --collect-status \
  --freeze-manifest docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json \
  --collection-manifest docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json \
  --storage-root data/prospective \
  --approvals-dir docs/validation/approvals \
  --output-dir reports/q5-epoch3 || true
