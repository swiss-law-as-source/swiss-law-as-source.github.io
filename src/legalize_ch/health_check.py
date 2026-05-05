"""Health check for the Swiss law pipeline.

Alerts via Telegram if no new commits have been made in the repo
for a configurable number of days (default: 30).
"""
from __future__ import annotations

import json
import logging
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .notify import PipelineResult, send_telegram, _escape_md, _load_telegram_config

logger = logging.getLogger(__name__)

DEFAULT_STALE_DAYS = 30


def get_last_commit_date(repo_path: str = ".") -> datetime | None:
    """Return the author date of the most recent commit in the repo.

    Returns:
        A timezone-aware datetime, or None if the repo has no commits
        or git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "log", "-1", "--format=%aI"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning("git log failed: %s", result.stderr.strip())
            return None
        return datetime.fromisoformat(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error("Failed to get last commit date: %s", e)
        return None


def get_commit_count(repo_path: str = ".") -> int:
    """Return the total number of commits in the repo."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return 0


def check_health(
    repo_path: str = ".",
    stale_days: int = DEFAULT_STALE_DAYS,
) -> tuple[bool, str]:
    """Check whether the repo has had recent activity.

    Args:
        repo_path: Path to the git repository.
        stale_days: Number of days without commits before considering stale.

    Returns:
        (is_healthy, message) — True if the repo has recent commits.
    """
    last_date = get_last_commit_date(repo_path)
    if last_date is None:
        return False, "Could not determine last commit date."

    now = datetime.now(timezone.utc)
    # Ensure last_date is timezone-aware
    if last_date.tzinfo is None:
        last_date = last_date.replace(tzinfo=timezone.utc)

    age = now - last_date
    age_days = age.days

    if age_days >= stale_days:
        return False, (
            f"STALE: Last commit was {age_days} days ago "
            f"({last_date.strftime('%Y-%m-%d')}). "
            f"Threshold: {stale_days} days."
        )

    return True, (
        f"OK: Last commit was {age_days} days ago "
        f"({last_date.strftime('%Y-%m-%d')}). "
        f"Threshold: {stale_days} days."
    )


def format_health_message(
    is_healthy: bool,
    message: str,
    repo_path: str = ".",
) -> str:
    """Format a health check result as a Telegram MarkdownV2 message."""
    icon = "\u2705" if is_healthy else "\U0001f6a8"  # green check or rotating light
    status = "Healthy" if is_healthy else "STALE"
    commit_count = get_commit_count(repo_path)

    lines = [
        f"{icon} *Swiss Law Health Check*",
        "",
        f"Status: {_escape_md(status)}",
        f"Total commits: {commit_count}",
        f"{_escape_md(message)}",
    ]
    return "\n".join(lines)


def send_health_alert(
    repo_path: str = ".",
    stale_days: int = DEFAULT_STALE_DAYS,
    always_notify: bool = False,
    bot_token: str = "",
    chat_id: str = "",
) -> bool:
    """Run health check and send Telegram alert if repo is stale.

    Args:
        repo_path: Path to the git repository.
        stale_days: Days without commits before alerting.
        always_notify: If True, send notification even when healthy.
        bot_token: Telegram bot token (loaded from env if empty).
        chat_id: Telegram chat ID (loaded from env if empty).

    Returns:
        True if notification was sent (or not needed), False on failure.
    """
    is_healthy, message = check_health(repo_path, stale_days)
    logger.info("Health check: %s", message)

    if is_healthy and not always_notify:
        logger.info("Repo is healthy — no alert needed.")
        return True

    # Load Telegram config if not provided
    if not bot_token or not chat_id:
        env_token, env_chat = _load_telegram_config()
        bot_token = bot_token or env_token
        chat_id = chat_id or env_chat

    if not bot_token or not chat_id:
        logger.warning("Telegram not configured. Cannot send health alert.")
        return False

    text = format_health_message(is_healthy, message, repo_path)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                logger.info("Health alert sent via Telegram.")
                return True
            else:
                logger.warning("Telegram API returned status %d", resp.status)
                return False
    except urllib.error.URLError as e:
        logger.error("Failed to send health alert: %s", e)
        return False
    except Exception as e:
        logger.error("Unexpected error sending health alert: %s", e)
        return False
