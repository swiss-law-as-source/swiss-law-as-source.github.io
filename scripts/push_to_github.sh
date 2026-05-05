#!/usr/bin/env bash
# push_to_github.sh — Push any unpushed commits to GitHub.
# Can be run standalone or called from other scripts.
#
# Usage: ./scripts/push_to_github.sh

set -euo pipefail

REPO_DIR="/home/ubuntu/swiss-law"
GITHUB_TOKEN_FILE="/home/ubuntu/.env"

cd "$REPO_DIR"

# Update remote URL with current token
if [ -f "$GITHUB_TOKEN_FILE" ]; then
    GITHUB_TOKEN=$(grep -oP 'GITHUB_TOKEN=\K.*' "$GITHUB_TOKEN_FILE" | tr -d '[:space:]')
    if [ -n "$GITHUB_TOKEN" ]; then
        REMOTE_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/benjamin-arfa/swiss-law.git"
        git remote set-url origin "$REMOTE_URL"
    else
        echo "ERROR: GITHUB_TOKEN is empty" >&2
        exit 1
    fi
else
    echo "ERROR: Token file not found at ${GITHUB_TOKEN_FILE}" >&2
    exit 1
fi

# Fetch remote to get accurate count
git fetch origin --quiet 2>/dev/null || true

UNPUSHED=$(git rev-list origin/main..HEAD --count 2>/dev/null)
if [ "$UNPUSHED" -eq 0 ]; then
    echo "Remote is up to date — nothing to push."
    exit 0
fi

echo "Pushing ${UNPUSHED} commits to GitHub..."
git push origin main 2>&1 | grep -v 'x-access-token'
echo "Push complete."
