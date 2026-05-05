"""Tests for the health check module."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from legalize_ch.health_check import (
    get_last_commit_date,
    get_commit_count,
    check_health,
    format_health_message,
    send_health_alert,
    DEFAULT_STALE_DAYS,
)


class TestGetLastCommitDate:
    """Test git commit date retrieval."""

    @patch("legalize_ch.health_check.subprocess.run")
    def test_returns_datetime(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2026-04-01T10:30:00+02:00\n",
            stderr="",
        )
        dt = get_last_commit_date("/some/repo")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 1

    @patch("legalize_ch.health_check.subprocess.run")
    def test_git_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=128,
            stdout="",
            stderr="fatal: not a git repo",
        )
        assert get_last_commit_date("/bad/path") is None

    @patch("legalize_ch.health_check.subprocess.run")
    def test_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        assert get_last_commit_date("/empty/repo") is None

    @patch("legalize_ch.health_check.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        assert get_last_commit_date("/slow/repo") is None

    @patch("legalize_ch.health_check.subprocess.run")
    def test_git_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("git not found")
        assert get_last_commit_date("/no/git") is None


class TestGetCommitCount:
    """Test commit count retrieval."""

    @patch("legalize_ch.health_check.subprocess.run")
    def test_returns_count(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="19127\n")
        assert get_commit_count("/repo") == 19127

    @patch("legalize_ch.health_check.subprocess.run")
    def test_failure_returns_zero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        assert get_commit_count("/bad") == 0


class TestCheckHealth:
    """Test health check logic."""

    @patch("legalize_ch.health_check.get_last_commit_date")
    def test_healthy_repo(self, mock_date):
        # Last commit 5 days ago
        mock_date.return_value = datetime.now(timezone.utc) - timedelta(days=5)
        is_healthy, msg = check_health("/repo", stale_days=30)
        assert is_healthy is True
        assert "OK" in msg
        assert "5 days ago" in msg

    @patch("legalize_ch.health_check.get_last_commit_date")
    def test_stale_repo(self, mock_date):
        # Last commit 45 days ago
        mock_date.return_value = datetime.now(timezone.utc) - timedelta(days=45)
        is_healthy, msg = check_health("/repo", stale_days=30)
        assert is_healthy is False
        assert "STALE" in msg
        assert "45 days ago" in msg

    @patch("legalize_ch.health_check.get_last_commit_date")
    def test_exactly_at_threshold(self, mock_date):
        # Last commit exactly 30 days ago
        mock_date.return_value = datetime.now(timezone.utc) - timedelta(days=30)
        is_healthy, msg = check_health("/repo", stale_days=30)
        assert is_healthy is False
        assert "STALE" in msg

    @patch("legalize_ch.health_check.get_last_commit_date")
    def test_just_under_threshold(self, mock_date):
        # Last commit 29 days ago
        mock_date.return_value = datetime.now(timezone.utc) - timedelta(days=29)
        is_healthy, msg = check_health("/repo", stale_days=30)
        assert is_healthy is True

    @patch("legalize_ch.health_check.get_last_commit_date")
    def test_no_commit_date(self, mock_date):
        mock_date.return_value = None
        is_healthy, msg = check_health("/repo")
        assert is_healthy is False
        assert "Could not determine" in msg

    @patch("legalize_ch.health_check.get_last_commit_date")
    def test_custom_threshold(self, mock_date):
        mock_date.return_value = datetime.now(timezone.utc) - timedelta(days=10)
        is_healthy, msg = check_health("/repo", stale_days=7)
        assert is_healthy is False
        assert "STALE" in msg

    @patch("legalize_ch.health_check.get_last_commit_date")
    def test_naive_datetime_handled(self, mock_date):
        # Ensure naive datetimes (no tzinfo) don't cause errors
        mock_date.return_value = datetime.utcnow() - timedelta(days=5)
        is_healthy, msg = check_health("/repo", stale_days=30)
        assert is_healthy is True


class TestFormatHealthMessage:
    """Test Telegram message formatting."""

    @patch("legalize_ch.health_check.get_commit_count", return_value=19127)
    def test_healthy_format(self, _mock):
        msg = format_health_message(True, "OK: Last commit was 5 days ago.")
        assert "\u2705" in msg  # green check
        assert "Healthy" in msg
        assert "19127" in msg

    @patch("legalize_ch.health_check.get_commit_count", return_value=19127)
    def test_stale_format(self, _mock):
        msg = format_health_message(False, "STALE: Last commit was 45 days ago.")
        assert "\U0001f6a8" in msg  # rotating light
        assert "STALE" in msg


class TestSendHealthAlert:
    """Test alert sending."""

    @patch("legalize_ch.health_check.urllib.request.urlopen")
    @patch("legalize_ch.health_check.check_health")
    def test_stale_sends_alert(self, mock_check, mock_urlopen):
        mock_check.return_value = (False, "STALE: Last commit was 45 days ago.")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok = send_health_alert(
            repo_path="/repo",
            stale_days=30,
            bot_token="tok",
            chat_id="123",
        )
        assert ok is True
        mock_urlopen.assert_called_once()

        # Verify the request payload
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["chat_id"] == "123"
        assert "STALE" in body["text"]

    @patch("legalize_ch.health_check.check_health")
    def test_healthy_no_alert(self, mock_check):
        mock_check.return_value = (True, "OK: Last commit was 5 days ago.")
        ok = send_health_alert(
            repo_path="/repo",
            stale_days=30,
            bot_token="tok",
            chat_id="123",
        )
        assert ok is True  # healthy = no alert needed = success

    @patch("legalize_ch.health_check.urllib.request.urlopen")
    @patch("legalize_ch.health_check.check_health")
    def test_always_notify_sends_when_healthy(self, mock_check, mock_urlopen):
        mock_check.return_value = (True, "OK: Last commit was 2 days ago.")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok = send_health_alert(
            repo_path="/repo",
            stale_days=30,
            always_notify=True,
            bot_token="tok",
            chat_id="123",
        )
        assert ok is True
        mock_urlopen.assert_called_once()

    @patch("legalize_ch.health_check._load_telegram_config", return_value=("", ""))
    @patch("legalize_ch.health_check.check_health")
    def test_missing_telegram_config(self, mock_check, mock_config):
        mock_check.return_value = (False, "STALE")
        ok = send_health_alert(repo_path="/repo", stale_days=30)
        assert ok is False

    @patch("legalize_ch.health_check.urllib.request.urlopen")
    @patch("legalize_ch.health_check.check_health")
    def test_network_error(self, mock_check, mock_urlopen):
        import urllib.error
        mock_check.return_value = (False, "STALE: 45 days")
        mock_urlopen.side_effect = urllib.error.URLError("timeout")

        ok = send_health_alert(
            repo_path="/repo",
            stale_days=30,
            bot_token="tok",
            chat_id="123",
        )
        assert ok is False
