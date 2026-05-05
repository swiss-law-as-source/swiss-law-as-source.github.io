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

# ─── Helper: configure git remote with current token ───
configure_remote() {
    if [ -f "$GITHUB_TOKEN_FILE" ]; then
        GITHUB_TOKEN=$(grep -oP 'GITHUB_TOKEN=\K.*' "$GITHUB_TOKEN_FILE" | tr -d '[:space:]')
        if [ -n "$GITHUB_TOKEN" ]; then
            REMOTE_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/benjamin-arfa/swiss-law.git"
            git remote set-url origin "$REMOTE_URL"
            echo "Remote URL updated from token file."
        else
            echo "WARNING: GITHUB_TOKEN is empty in ${GITHUB_TOKEN_FILE}"
        fi
    else
        echo "WARNING: Token file not found at ${GITHUB_TOKEN_FILE}"
    fi
}

# ─── Helper: push to GitHub with retry ───
push_to_github() {
    local max_retries=3
    local attempt=1

    while [ "$attempt" -le "$max_retries" ]; do
        echo "  Push attempt ${attempt}/${max_retries}..."
        # Use --porcelain for machine-readable output; filter token from stderr
        if git push origin main 2>&1 | grep -v 'x-access-token'; then
            echo "  Push successful."
            return 0
        fi
        echo "  Push attempt ${attempt} failed."
        attempt=$((attempt + 1))
        if [ "$attempt" -le "$max_retries" ]; then
            sleep $((attempt * 5))
        fi
    done

    echo "ERROR: Push failed after ${max_retries} attempts."
    return 1
}

# Count commits before
COMMITS_BEFORE=$(git rev-list --count HEAD)

# Run the incremental update
echo "[1/4] Running incremental update..."
"${VENV}/bin/legalize-ch" update --repo "$REPO_DIR" --rate-limit 1.5

# Count commits after
COMMITS_AFTER=$(git rev-list --count HEAD)
NEW_COMMITS=$((COMMITS_AFTER - COMMITS_BEFORE))

echo ""
echo "[2/4] Update complete. New commits: ${NEW_COMMITS}"

# Configure remote with latest token
echo "[3/4] Configuring GitHub remote..."
configure_remote

# Push to GitHub — push regardless of new pipeline commits, to catch any
# previously-unpushed commits (e.g. from manual runs or failed pushes)
UNPUSHED=$(git rev-list origin/main..HEAD --count 2>/dev/null || echo "unknown")
echo "  Unpushed commits: ${UNPUSHED}"

if [ "$UNPUSHED" != "0" ] && [ "$UNPUSHED" != "unknown" ]; then
    echo "[4/4] Pushing ${UNPUSHED} commits to GitHub..."
    push_to_github
    PUSH_EXIT=$?
    if [ "$PUSH_EXIT" -ne 0 ]; then
        echo "WARNING: Push failed — commits will be pushed on next run."
    fi
else
    echo "[4/4] Remote is up to date — skipping push."
fi

echo ""
echo "=== Summary ==="
echo "New commits from pipeline: ${NEW_COMMITS}"
echo "Unpushed commits at start: ${UNPUSHED}"
echo "Finished: $(date -Iseconds)"
echo "Log: ${LOG_FILE}"

# Clean up old logs (keep last 12 weeks)
find "$LOG_DIR" -name "weekly_update_*.log" -mtime +84 -delete 2>/dev/null || true
