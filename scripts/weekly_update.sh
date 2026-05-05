#!/usr/bin/env bash
# weekly_update.sh — Run the legalize-ch incremental update pipeline weekly.
# Intended to be invoked via cron. Logs output and pushes new commits to GitHub.
# Sends a Telegram notification on completion (commit count, errors).
#
# Usage: ./scripts/weekly_update.sh
# Cron:  43 3 * * 1  /home/ubuntu/swiss-law/scripts/weekly_update.sh

set -euo pipefail

REPO_DIR="/home/ubuntu/swiss-law"
VENV="${REPO_DIR}/.venv"
LOG_DIR="${REPO_DIR}/data/logs"
LOG_FILE="${LOG_DIR}/weekly_update_$(date +%Y%m%d_%H%M%S).log"
GITHUB_TOKEN_FILE="/home/ubuntu/.env"
START_TIME=$(date +%s)
ERRORS=""
PUSH_OK="true"

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

# ─── Helper: send Telegram notification ───
send_notification() {
    local new_commits="$1"
    local unpushed="$2"
    local push_ok="$3"
    local errors="$4"

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - START_TIME))

    echo ""
    echo "[5/5] Sending Telegram notification..."

    # Build a Python one-liner that uses the notify module
    "${VENV}/bin/python" -c "
import sys
sys.path.insert(0, '${REPO_DIR}/src')
from legalize_ch.notify import PipelineResult, send_telegram

errors = [e for e in '''${errors}'''.split('|||') if e.strip()]
result = PipelineResult(
    new_commits=${new_commits},
    laws_checked=0,
    errors=errors,
    push_ok=$([[ "$push_ok" == "true" ]] && echo "True" || echo "False"),
    unpushed=${unpushed},
    duration_seconds=${duration},
    mode='update',
)
ok = send_telegram(result)
sys.exit(0 if ok else 1)
" && echo "  Notification sent." || echo "  WARNING: Notification failed."
}

# Count commits before
COMMITS_BEFORE=$(git rev-list --count HEAD)

# Run the incremental update
echo "[1/5] Running incremental update..."
if ! "${VENV}/bin/legalize-ch" update --repo "$REPO_DIR" --rate-limit 1.5 2>&1; then
    ERRORS="Pipeline update command failed"
    echo "WARNING: Pipeline update encountered errors."
fi

# Count commits after
COMMITS_AFTER=$(git rev-list --count HEAD)
NEW_COMMITS=$((COMMITS_AFTER - COMMITS_BEFORE))

echo ""
echo "[2/5] Update complete. New commits: ${NEW_COMMITS}"

# Configure remote with latest token
echo "[3/5] Configuring GitHub remote..."
configure_remote

# Push to GitHub — push regardless of new pipeline commits, to catch any
# previously-unpushed commits (e.g. from manual runs or failed pushes)
UNPUSHED=$(git rev-list origin/main..HEAD --count 2>/dev/null || echo "unknown")
echo "  Unpushed commits: ${UNPUSHED}"

if [ "$UNPUSHED" != "0" ] && [ "$UNPUSHED" != "unknown" ]; then
    echo "[4/5] Pushing ${UNPUSHED} commits to GitHub..."
    if ! push_to_github; then
        PUSH_OK="false"
        if [ -n "$ERRORS" ]; then
            ERRORS="${ERRORS}|||Push failed after retries"
        else
            ERRORS="Push failed after retries"
        fi
        echo "WARNING: Push failed — commits will be pushed on next run."
    fi
else
    echo "[4/5] Remote is up to date — skipping push."
fi

# Send Telegram notification
FINAL_UNPUSHED=$(git rev-list origin/main..HEAD --count 2>/dev/null || echo "0")
send_notification "$NEW_COMMITS" "$FINAL_UNPUSHED" "$PUSH_OK" "$ERRORS"

echo ""
echo "=== Summary ==="
echo "New commits from pipeline: ${NEW_COMMITS}"
echo "Unpushed commits at start: ${UNPUSHED}"
echo "Finished: $(date -Iseconds)"
echo "Log: ${LOG_FILE}"

# Clean up old logs (keep last 12 weeks)
find "$LOG_DIR" -name "weekly_update_*.log" -mtime +84 -delete 2>/dev/null || true
