"""Tests for exponential backoff retry logic in FedlexFetcher."""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest
import requests

from legalize_ch.fetcher import (
    FedlexFetcher,
    MAX_RETRIES,
    INITIAL_BACKOFF,
    BACKOFF_FACTOR,
)


@pytest.fixture
def fetcher():
    """Create a fetcher with minimal rate limit for fast tests."""
    f = FedlexFetcher(rate_limit=0.0)
    return f


class TestQueryRetry:
    """Test SPARQL _query method retry behavior."""

    def test_succeeds_on_first_try(self, fetcher):
        mock_results = {"results": {"bindings": [{"x": {"value": "hello"}}]}}
        fetcher.sparql = MagicMock()
        fetcher.sparql.query.return_value.convert.return_value = mock_results
        fetcher.sparql.setQuery = MagicMock()

        result = fetcher._query("SELECT ?x WHERE { ?x ?y ?z }")
        assert result == [{"x": {"value": "hello"}}]
        assert fetcher.sparql.query.call_count == 1

    @patch("legalize_ch.fetcher.time.sleep")
    def test_retries_on_429_error(self, mock_sleep, fetcher):
        """Should retry when exception message contains '429'."""
        mock_results = {"results": {"bindings": [{"x": {"value": "ok"}}]}}
        fetcher.sparql = MagicMock()
        fetcher.sparql.setQuery = MagicMock()

        # Fail twice with 429, then succeed
        fetcher.sparql.query.return_value.convert.side_effect = [
            Exception("HTTP Error 429: Too Many Requests"),
            Exception("HTTP Error 429: Too Many Requests"),
            mock_results,
        ]

        result = fetcher._query("SELECT ?x WHERE { ?x ?y ?z }")
        assert result == [{"x": {"value": "ok"}}]
        assert fetcher.sparql.query.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("legalize_ch.fetcher.time.sleep")
    def test_retries_on_timeout_error(self, mock_sleep, fetcher):
        """Should retry on timeout errors."""
        mock_results = {"results": {"bindings": []}}
        fetcher.sparql = MagicMock()
        fetcher.sparql.setQuery = MagicMock()

        fetcher.sparql.query.return_value.convert.side_effect = [
            Exception("Connection timeout"),
            mock_results,
        ]

        result = fetcher._query("SELECT ?x WHERE { ?x ?y ?z }")
        assert result == []
        assert fetcher.sparql.query.call_count == 2
        assert mock_sleep.call_count == 1

    @patch("legalize_ch.fetcher.time.sleep")
    def test_gives_up_after_max_retries(self, mock_sleep, fetcher):
        """Should stop retrying after MAX_RETRIES attempts."""
        fetcher.sparql = MagicMock()
        fetcher.sparql.setQuery = MagicMock()

        fetcher.sparql.query.return_value.convert.side_effect = Exception(
            "HTTP Error 503: Service Unavailable"
        )

        result = fetcher._query("SELECT ?x WHERE { ?x ?y ?z }")
        assert result == []
        assert fetcher.sparql.query.call_count == MAX_RETRIES
        assert mock_sleep.call_count == MAX_RETRIES - 1

    @patch("legalize_ch.fetcher.time.sleep")
    def test_no_retry_on_non_retryable_error(self, mock_sleep, fetcher):
        """Should not retry on non-retryable errors (e.g. syntax error)."""
        fetcher.sparql = MagicMock()
        fetcher.sparql.setQuery = MagicMock()

        fetcher.sparql.query.return_value.convert.side_effect = Exception(
            "SPARQL syntax error: unexpected token"
        )

        result = fetcher._query("INVALID QUERY")
        assert result == []
        assert fetcher.sparql.query.call_count == 1
        assert mock_sleep.call_count == 0

    @patch("legalize_ch.fetcher.time.sleep")
    def test_exponential_backoff_timing(self, mock_sleep, fetcher):
        """Should use exponential backoff between retries."""
        fetcher.sparql = MagicMock()
        fetcher.sparql.setQuery = MagicMock()

        mock_results = {"results": {"bindings": [{"x": {"value": "ok"}}]}}
        fetcher.sparql.query.return_value.convert.side_effect = [
            Exception("HTTP Error 429: Too Many Requests"),
            Exception("HTTP Error 429: Too Many Requests"),
            Exception("HTTP Error 429: Too Many Requests"),
            mock_results,
        ]

        fetcher._query("SELECT ?x WHERE { ?x ?y ?z }")

        # Check backoff progression: 2.0, 4.0, 8.0
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert sleep_calls[0] == INITIAL_BACKOFF
        assert sleep_calls[1] == INITIAL_BACKOFF * BACKOFF_FACTOR
        assert sleep_calls[2] == INITIAL_BACKOFF * BACKOFF_FACTOR ** 2


class TestFetchUrlRetry:
    """Test _fetch_url method retry behavior."""

    @patch("legalize_ch.fetcher.time.sleep")
    def test_succeeds_on_first_try(self, mock_sleep, fetcher):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>content</html>"
        mock_resp.raise_for_status = MagicMock()
        fetcher.session.get = MagicMock(return_value=mock_resp)

        result = fetcher._fetch_url("https://example.com/law.xml")
        assert result == "<html>content</html>"
        assert fetcher.session.get.call_count == 1

    @patch("legalize_ch.fetcher.time.sleep")
    def test_retries_on_429_status(self, mock_sleep, fetcher):
        """Should retry when server returns HTTP 429."""
        mock_429 = MagicMock()
        mock_429.status_code = 429

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.text = "success"
        mock_200.raise_for_status = MagicMock()

        fetcher.session.get = MagicMock(side_effect=[mock_429, mock_200])

        result = fetcher._fetch_url("https://example.com/law.xml")
        assert result == "success"
        assert fetcher.session.get.call_count == 2

    @patch("legalize_ch.fetcher.time.sleep")
    def test_retries_on_connection_error(self, mock_sleep, fetcher):
        """Should retry on connection errors."""
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.text = "ok"
        mock_200.raise_for_status = MagicMock()

        fetcher.session.get = MagicMock(
            side_effect=[
                requests.exceptions.ConnectionError("Connection reset"),
                mock_200,
            ]
        )

        result = fetcher._fetch_url("https://example.com/law.xml")
        assert result == "ok"
        assert fetcher.session.get.call_count == 2

    @patch("legalize_ch.fetcher.time.sleep")
    def test_returns_empty_after_max_retries(self, mock_sleep, fetcher):
        """Should return empty string after exhausting retries."""
        fetcher.session.get = MagicMock(
            side_effect=requests.exceptions.Timeout("timed out")
        )

        result = fetcher._fetch_url("https://example.com/law.xml")
        assert result == ""
        assert fetcher.session.get.call_count == MAX_RETRIES
