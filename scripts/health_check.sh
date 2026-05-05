#!/usr/bin/env bash
# health_check.sh — Check if the Swiss law repo has had recent commits.
# Sends a Telegram alert if no new commits for 30+ days.
# Intended to be run daily via cron.
#
# Usage: ./scripts/health_check.sh
# Cron:  15 9 * * *  /home/ubuntu/swiss-law/scripts/health_check.sh

set -euo pipefail

REPO_DIR="/home/ubuntu/swiss-law"
VENV="${REPO_DIR}/.venv"
LOG_DIR="${REPO_DIR}/data/logs"
LOG_FILE="${LOG_DIR}/health_check_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Swiss Law Health Check ==="
echo "Started: $(date -Iseconds)"
echo ""

"${VENV}/bin/legalize-ch" health-check --repo "$REPO_DIR" --days 30
EXIT_CODE=$?

echo ""
echo "Finished: $(date -Iseconds)"
echo "Log: ${LOG_FILE}"

# Clean up old logs (keep last 30 days)
find "$LOG_DIR" -name "health_check_*.log" -mtime +30 -delete 2>/dev/null || true

exit $EXIT_CODE
