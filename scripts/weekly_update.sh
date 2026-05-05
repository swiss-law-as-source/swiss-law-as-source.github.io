#!/usr/bin/env bash
# weekly_update.sh — Run the legalize-ch incremental update pipeline weekly.
# Intended to be invoked via cron. Logs output and pushes new commits to GitHub.
#
# Usage: ./scripts/weekly_update.sh
# Cron:  43 3 * * 1  /home/ubuntu/swiss-law/scripts/weekly_update.sh

set -euo pipefail

REPO_DIR="/home/ubuntu/swiss-law"
VENV="${REPO_DIR}/.venv"
LOG_DIR="${REPO_DIR}/data/logs"
LOG_FILE="${LOG_DIR}/weekly_update_$(date +%Y%m%d_%H%M%S).log"
GITHUB_TOKEN_FILE="/home/ubuntu/.env"

mkdir -p "$LOG_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Swiss Law Weekly Update ==="
echo "Started: $(date -Iseconds)"
echo "Repo: ${REPO_DIR}"
echo ""

cd "$REPO_DIR"

# Count commits before
COMMITS_BEFORE=$(git rev-list --count HEAD)

# Run the incremental update
echo "[1/3] Running incremental update..."
"${VENV}/bin/legalize-ch" update --repo "$REPO_DIR" --rate-limit 1.5

# Count commits after
COMMITS_AFTER=$(git rev-list --count HEAD)
NEW_COMMITS=$((COMMITS_AFTER - COMMITS_BEFORE))

echo ""
echo "[2/3] Update complete. New commits: ${NEW_COMMITS}"

# Push to GitHub if there are new commits
if [ "$NEW_COMMITS" -gt 0 ]; then
    echo "[3/3] Pushing to GitHub..."
    # Load GitHub token
    if [ -f "$GITHUB_TOKEN_FILE" ]; then
        export $(grep GITHUB_TOKEN "$GITHUB_TOKEN_FILE" | xargs)
    fi
    REMOTE_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/benjamin-arfa/swiss-law.git"
    git push "$REMOTE_URL" HEAD:main 2>&1 | grep -v "$GITHUB_TOKEN" || true
    echo "Push complete."
else
    echo "[3/3] No new commits — skipping push."
fi

echo ""
echo "Finished: $(date -Iseconds)"
echo "Log: ${LOG_FILE}"

# Clean up old logs (keep last 12 weeks)
find "$LOG_DIR" -name "weekly_update_*.log" -mtime +84 -delete 2>/dev/null || true
