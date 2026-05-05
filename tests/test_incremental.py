"""Tests for incremental update mode (task 2.3)."""
from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.legalize_ch.models import LawEntry, LawVersion, LawText
from src.legalize_ch.pipeline import Pipeline


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temp git repo with pipeline state."""
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path, capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
        },
    )
    state_dir = tmp_path / "data"
    state_dir.mkdir()
    state = {
        "processed": {
            "101@2020-01-01": True,
            "101@2021-01-01": True,
            "101@2022-01-01": True,
            "210@2019-06-01": True,
        },
        "last_run": "2024-01-01",
    }
    (state_dir / "pipeline_state.json").write_text(json.dumps(state))
    return tmp_path


def _make_law(sr="101", uri="http://example.com/101"):
    return LawEntry(sr_number=sr, uri=uri, title_de=f"Testgesetz SR {sr}")


def _make_versions(sr, dates):
    return [
        LawVersion(sr_number=sr, version_uri=f"http://example.com/{sr}/{d}", date_applicable=d)
        for d in dates
    ]


class TestIncrementalHelpers:
    """Test _get_known_version_count and _get_known_version_dates."""

    def test_known_version_count(self, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)
        assert p._get_known_version_count("101") == 3
        assert p._get_known_version_count("210") == 1
        assert p._get_known_version_count("999") == 0

    def test_known_version_dates(self, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)
        dates = p._get_known_version_dates("101")
        assert dates == {"2020-01-01", "2021-01-01", "2022-01-01"}

    def test_known_version_dates_empty(self, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)
        assert p._get_known_version_dates("nonexistent") == set()


class TestProcessLawIncremental:
    """Test that _process_law with since_date filters old versions."""

    @patch.object(Pipeline, "_save_state")
    def test_since_date_filters_old_versions(self, mock_save, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)

        law = _make_law("101")
        all_versions = _make_versions("101", [
            date(2020, 1, 1),
            date(2021, 1, 1),
            date(2022, 1, 1),
            date(2024, 6, 1),  # new version
            date(2025, 1, 1),  # new version
        ])

        with patch.object(p.fetcher, "fetch_versions", return_value=all_versions), \
             patch.object(p.fetcher, "fetch_text") as mock_fetch_text:
            mock_fetch_text.return_value = LawText(
                sr_number="101", language="de", version_date=date(2024, 6, 1),
                title="Test", xml_content="<xml>content</xml>",
            )

            commits = p._process_law(law, ["de"], latest_only=False,
                                     since_date=date(2024, 1, 1))

            # Should only call fetch_text for versions >= 2024-01-01
            # That's 2024-06-01 and 2025-01-01 (2 versions)
            # 2020, 2021, 2022 are before since_date → filtered out
            assert mock_fetch_text.call_count == 2  # 2 new versions × 1 lang

    @patch.object(Pipeline, "_save_state")
    def test_no_since_date_processes_all(self, mock_save, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)

        law = _make_law("300")
        all_versions = _make_versions("300", [
            date(2020, 1, 1),
            date(2024, 6, 1),
        ])

        with patch.object(p.fetcher, "fetch_versions", return_value=all_versions), \
             patch.object(p.fetcher, "fetch_text") as mock_fetch_text:
            mock_fetch_text.return_value = LawText(
                sr_number="300", language="de", version_date=date(2020, 1, 1),
                title="Test", xml_content="<xml>content</xml>",
            )

            commits = p._process_law(law, ["de"], latest_only=False,
                                     since_date=None)

            # Without since_date, all versions are considered
            # Both are unprocessed, so both get fetch_text
            assert mock_fetch_text.call_count == 2

    @patch.object(Pipeline, "_save_state")
    def test_already_processed_skipped_even_after_since(self, mock_save, tmp_repo):
        """Versions that pass date filter but are already processed should be skipped."""
        p = Pipeline(repo_path=tmp_repo)
        # Mark 2024-06-01 as already processed
        p._mark_processed("101", date(2024, 6, 1))

        law = _make_law("101")
        all_versions = _make_versions("101", [
            date(2024, 6, 1),  # already processed
            date(2025, 1, 1),  # new
        ])

        with patch.object(p.fetcher, "fetch_versions", return_value=all_versions), \
             patch.object(p.fetcher, "fetch_text") as mock_fetch_text:
            mock_fetch_text.return_value = LawText(
                sr_number="101", language="de", version_date=date(2025, 1, 1),
                title="Test", xml_content="<xml>content</xml>",
            )

            commits = p._process_law(law, ["de"], latest_only=False,
                                     since_date=date(2024, 1, 1))

            # Only 2025-01-01 should trigger fetch_text
            assert mock_fetch_text.call_count == 1


class TestUpdateSinceOverride:
    """Test that update() accepts since_override."""

    @patch.object(Pipeline, "_save_state")
    def test_since_override_used(self, mock_save, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)

        with patch.object(p.fetcher, "fetch_modified_since", return_value=[]) as mock_fetch:
            p.update(since_override=date(2023, 6, 15))
            mock_fetch.assert_called_once_with(date(2023, 6, 15), limit=None)

    @patch.object(Pipeline, "_save_state")
    def test_uses_last_run_when_no_override(self, mock_save, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)

        with patch.object(p.fetcher, "fetch_modified_since", return_value=[]) as mock_fetch:
            p.update()
            # Should use last_run from state: 2024-01-01
            mock_fetch.assert_called_once_with(date(2024, 1, 1), limit=None)
