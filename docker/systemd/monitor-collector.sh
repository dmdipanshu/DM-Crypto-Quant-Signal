#!/usr/bin/env bash
# monitor-collector.sh - Monitor SignalForge Q5.2 collector status
#
# Usage:
#   ./monitor-collector.sh
#
# Checks:
#   - systemd timer status (primary and retry)
#   - Docker collector container status
#   - Q5 governance endpoint
#   - Prospective collection state
#
set -euo pipefail

PROJECT_DIR="${1:-$(pwd)}"
COMPOSE_FILE="${PROJECT_DIR}/compose.yaml"

echo "=== SignalForge Q5.2 Collector Monitor ==="
echo ""

# 1. systemd timer status
echo "--- Systemd Timers ---"
if command -v systemctl &>/dev/null; then
    echo "Primary timer (@primary):"
    systemctl status signalforge-collector@primary.timer --no-pager 2>/dev/null || echo "  (not installed or not found)"
    echo ""
    echo "Retry timer (@retry):"
    systemctl status signalforge-collector@retry.timer --no-pager 2>/dev/null || echo "  (not installed or not found)"
else
    echo "systemctl not available (not running on systemd host)"
fi
echo ""

# 2. Docker container status
echo "--- Docker Collector Container ---"
if command -v docker &>/dev/null; then
    docker compose -f "${COMPOSE_FILE}" ps collector 2>/dev/null || echo "  (compose not available or service not found)"
else
    echo "docker not available"
fi
echo ""

# 3. Q5 governance endpoint
echo "--- Q5 Governance Endpoint ---"
if command -v curl &>/dev/null; then
    TOKEN="${SIGNALFORGE_API_TOKEN:-}"
    if curl -sf -H "Authorization: Bearer ${TOKEN}" \
        http://127.0.0.1:8000/api/v1/q5/prospective/governance 2>/dev/null; then
        echo ""
    else
        echo "  (endpoint not reachable or auth required)"
    fi
else
    echo "curl not available"
fi
echo ""

# 4. Prospective collection state (via CLI)
echo "--- Prospective Collection Status ---"
if [ -f "${PROJECT_DIR}/scripts/run_q5_prospective_collection.py" ]; then
    uv run python "${PROJECT_DIR}/scripts/run_q5_prospective_collection.py" \
        --collect-status \
        --freeze-manifest "${PROJECT_DIR}/docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH2.json" \
        --collection-manifest "${PROJECT_DIR}/docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH2.json" \
        --storage-root "${PROJECT_DIR}/data/prospective" \
        --approvals-dir "${PROJECT_DIR}/docs/validation/approvals" \
        --output-dir "${PROJECT_DIR}/docs/validation/results" \
        2>/dev/null || echo "  (collector status check failed)"
else
    echo "  (collector script not found)"
fi
