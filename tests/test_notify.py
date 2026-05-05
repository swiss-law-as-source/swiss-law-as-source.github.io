"""Tests for the Telegram notification module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from legalize_ch.notify import (
    PipelineResult,
    format_message,
    send_telegram,
    _escape_md,
    _load_telegram_config,
)


class TestEscapeMd:
    """Test MarkdownV2 escaping."""

    def test_plain_text(self):
        assert _escape_md("hello world") == "hello world"

    def test_special_chars(self):
        assert _escape_md("1.2") == "1\\.2"
        assert _escape_md("(test)") == "\\(test\\)"
        assert _escape_md("*bold*") == "\\*bold\\*"

    def test_all_special(self):
        for ch in "_*[]()~`>#+-=|{}.!":
            assert f"\\{ch}" in _escape_md(ch)


class TestFormatMessage:
    """Test message formatting."""

    def test_success_with_commits(self):
        result = PipelineResult(new_commits=5, laws_checked=100, mode="update")
        msg = format_message(result)
        assert "\u2705" in msg  # green check
        assert "5" in msg
        assert "100" in msg

    def test_no_commits_no_errors(self):
        result = PipelineResult(new_commits=0, laws_checked=50, mode="update")
        msg = format_message(result)
        assert "\u2139\ufe0f" in msg  # info icon

    def test_errors_shown(self):
        result = PipelineResult(
            new_commits=0,
            laws_checked=10,
            errors=["SR 101 fetch failed", "SR 102 timeout"],
            mode="update",
        )
        msg = format_message(result)
        assert "\u26a0\ufe0f" in msg  # warning
        assert "Errors" in msg
        assert "2" in msg

    def test_errors_capped_at_five(self):
        result = PipelineResult(
            new_commits=0,
            errors=[f"Error #{i}" for i in range(10)],
        )
        msg = format_message(result)
        assert "and 5 more" in msg

    def test_push_failed(self):
        result = PipelineResult(
            new_commits=3,
            push_ok=False,
            unpushed=15,
        )
        msg = format_message(result)
        assert "\u274c" in msg
        assert "15" in msg

    def test_duration_shown(self):
        result = PipelineResult(new_commits=1, duration_seconds=125)
        msg = format_message(result)
        assert "2m 5s" in msg


class TestLoadConfig:
    """Test env file loading."""

    def test_loads_from_file(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("TELEGRAM_BOT_TOKEN=abc123\nTELEGRAM_ADMIN_CHAT_ID=999\n")
        token, chat_id = _load_telegram_config(env)
        assert token == "abc123"
        assert chat_id == "999"

    def test_missing_file(self, tmp_path):
        token, chat_id = _load_telegram_config(tmp_path / "nope")
        assert token == ""
        assert chat_id == ""

    def test_partial_config(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("TELEGRAM_BOT_TOKEN=tok\n")
        token, chat_id = _load_telegram_config(env)
        assert token == "tok"
        assert chat_id == ""

    def test_comments_and_blanks(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# comment\n\nTELEGRAM_BOT_TOKEN=tok\n\nTELEGRAM_ADMIN_CHAT_ID=42\n")
        token, chat_id = _load_telegram_config(env)
        assert token == "tok"
        assert chat_id == "42"


class TestSendTelegram:
    """Test the send function (mocked HTTP)."""

    @patch("legalize_ch.notify.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = PipelineResult(new_commits=3, mode="update")
        ok = send_telegram(result, bot_token="tok", chat_id="123")

        assert ok is True
        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["chat_id"] == "123"
        assert body["parse_mode"] == "MarkdownV2"
        assert "3" in body["text"]

    @patch("legalize_ch.notify._load_telegram_config", return_value=("", ""))
    @patch("legalize_ch.notify.urllib.request.urlopen")
    def test_missing_config(self, mock_urlopen, mock_config):
        result = PipelineResult(new_commits=0)
        ok = send_telegram(result, bot_token="", chat_id="")
        assert ok is False
        mock_urlopen.assert_not_called()

    @patch("legalize_ch.notify.urllib.request.urlopen")
    def test_network_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("timeout")

        result = PipelineResult(new_commits=1, mode="update")
        ok = send_telegram(result, bot_token="tok", chat_id="123")
        assert ok is False
