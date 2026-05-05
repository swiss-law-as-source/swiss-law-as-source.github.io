"""Telegram notification for pipeline completion."""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_FILE = Path("/home/ubuntu/remote-cbas/.env")


@dataclass
class PipelineResult:
    """Summary of a pipeline run for notification purposes."""
    new_commits: int = 0
    laws_checked: int = 0
    errors: list[str] = field(default_factory=list)
    push_ok: bool = True
    unpushed: int = 0
    duration_seconds: float = 0.0
    mode: str = "update"  # "update" or "bootstrap"


def _load_telegram_config(env_file: Path = ENV_FILE) -> tuple[str, str]:
    """Load bot token and chat ID from the rcbas .env file.

    Returns:
        (bot_token, chat_id) tuple. Either may be empty if not found.
    """
    token = ""
    chat_id = ""
    if not env_file.exists():
        return token, chat_id
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "TELEGRAM_BOT_TOKEN":
            token = value
        elif key == "TELEGRAM_ADMIN_CHAT_ID":
            chat_id = value
    return token, chat_id


def format_message(result: PipelineResult) -> str:
    """Format a pipeline result into a Telegram message (MarkdownV2)."""
    if result.new_commits > 0:
        icon = "\u2705"  # green check
    elif result.errors:
        icon = "\u26a0\ufe0f"  # warning
    else:
        icon = "\u2139\ufe0f"  # info

    lines = [
        f"{icon} *Swiss Law Pipeline*",
        "",
        f"Mode: {_escape_md(result.mode)}",
        f"New commits: {result.new_commits}",
        f"Laws checked: {result.laws_checked}",
    ]

    if result.duration_seconds > 0:
        mins = int(result.duration_seconds // 60)
        secs = int(result.duration_seconds % 60)
        lines.append(f"Duration: {mins}m {secs}s")

    if not result.push_ok:
        lines.append(f"\u274c Push failed \\({result.unpushed} unpushed\\)")
    elif result.unpushed > 0:
        lines.append(f"Pushed: {result.unpushed} commits")

    if result.errors:
        lines.append("")
        lines.append(f"*Errors \\({len(result.errors)}\\):*")
        for err in result.errors[:5]:
            lines.append(f"\\- {_escape_md(err[:120])}")
        if len(result.errors) > 5:
            lines.append(f"_\\.\\.\\.and {len(result.errors) - 5} more_")

    return "\n".join(lines)


def _escape_md(text: str) -> str:
    """Escape special chars for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in text:
        if ch in special:
            result.append("\\")
        result.append(ch)
    return "".join(result)


def send_telegram(result: PipelineResult, bot_token: str = "", chat_id: str = "") -> bool:
    """Send pipeline result notification via Telegram.

    Args:
        result: Pipeline run summary.
        bot_token: Telegram bot token. If empty, loaded from env file.
        chat_id: Telegram chat ID. If empty, loaded from env file.

    Returns:
        True if message was sent successfully, False otherwise.
    """
    if not bot_token or not chat_id:
        env_token, env_chat = _load_telegram_config()
        bot_token = bot_token or env_token
        chat_id = chat_id or env_chat

    if not bot_token or not chat_id:
        logger.warning("Telegram not configured (missing token or chat_id). Skipping notification.")
        return False

    message = format_message(result)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
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
                logger.info("Telegram notification sent successfully.")
                return True
            else:
                logger.warning("Telegram API returned status %d", resp.status)
                return False
    except urllib.error.URLError as e:
        logger.error("Failed to send Telegram notification: %s", e)
        return False
    except Exception as e:
        logger.error("Unexpected error sending Telegram notification: %s", e)
        return False
