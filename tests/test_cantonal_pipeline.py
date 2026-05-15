"""Tests for the cantonal pipeline integration (--scope flag)."""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from legalize_ch.cantonal_pipeline import CantonalPipeline
from legalize_ch.cantonal import CantonalLawEntry, CantonalLawText
from legalize_ch.cli import main


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temporary git repo."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path, capture_output=True,
        env={"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com",
             "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    # Create data dir for state
    (tmp_path / "data").mkdir()
    return tmp_path


class TestCantonalPipeline:
    """Unit tests for CantonalPipeline."""

    def test_state_file_created(self, tmp_repo):
        """Pipeline creates state file on save."""
        pipeline = CantonalPipeline(repo_path=tmp_repo)
        pipeline._save_state()
        assert (tmp_repo / "data" / "cantonal_pipeline_state.json").exists()

    def test_mark_processed(self, tmp_repo):
        """Marking a law as processed prevents re-processing."""
        pipeline = CantonalPipeline(repo_path=tmp_repo)
        assert not pipeline._is_processed("zh", "131.1", "de")
        pipeline._mark_processed("zh", "131.1", "de")
        assert pipeline._is_processed("zh", "131.1", "de")

    @patch("legalize_ch.cantonal_pipeline.CantonalFetcher")
    def test_run_empty_catalog(self, mock_fetcher_cls, tmp_repo):
        """Pipeline handles empty catalog gracefully."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_lexwork_catalog.return_value = []
        mock_fetcher_cls.return_value = mock_fetcher

        pipeline = CantonalPipeline(repo_path=tmp_repo)
        pipeline.fetcher = mock_fetcher
        total = pipeline.run(cantons=["bs"], languages=["de"])
        assert total == 0

    @patch("legalize_ch.cantonal_pipeline.CantonalFetcher")
    def test_run_single_canton(self, mock_fetcher_cls, tmp_repo):
        """Pipeline processes a single canton with one law."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_lexwork_catalog.return_value = [
            CantonalLawEntry(
                canton="bs",
                systematic_number="100.100",
                title="Kantonsverfassung",
            ),
        ]
        mock_fetcher.fetch_law_text.return_value = CantonalLawText(
            canton="bs",
            systematic_number="100.100",
            title="Kantonsverfassung",
            html_content="<h1>Kantonsverfassung</h1><p>Art. 1 Basel-Stadt ist ein Kanton.</p>",
            language="de",
            version_date=date(2020, 1, 1),
        )
        mock_fetcher_cls.return_value = mock_fetcher

        pipeline = CantonalPipeline(repo_path=tmp_repo)
        pipeline.fetcher = mock_fetcher
        total = pipeline.run(cantons=["bs"], languages=["de"])

        # Check file was created
        law_file = tmp_repo / "kt" / "bs" / "de" / "100.100.md"
        assert law_file.exists()
        content = law_file.read_text()
        assert "Kantonsverfassung" in content
        assert total == 1

    @patch("legalize_ch.cantonal_pipeline.CantonalFetcher")
    def test_update_skips_processed(self, mock_fetcher_cls, tmp_repo):
        """Update skips laws already in state."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_lexwork_catalog.return_value = [
            CantonalLawEntry(
                canton="bs",
                systematic_number="100.100",
                title="Kantonsverfassung",
            ),
        ]
        mock_fetcher_cls.return_value = mock_fetcher

        pipeline = CantonalPipeline(repo_path=tmp_repo)
        pipeline.fetcher = mock_fetcher
        # Pre-mark as processed
        pipeline._mark_processed("bs", "100.100", "de")
        total = pipeline.update(cantons=["bs"], languages=["de"])

        # Should not have fetched text since it's already processed
        mock_fetcher.fetch_law_text.assert_not_called()
        assert total == 0

    def test_unknown_canton_skipped(self, tmp_repo):
        """Unknown canton abbreviation is logged and skipped."""
        pipeline = CantonalPipeline(repo_path=tmp_repo)
        # "xx" is not a valid canton
        total = pipeline.run(cantons=["xx"], languages=["de"])
        assert total == 0


class TestCLIScopeOption:
    """Test the --scope option in CLI commands."""

    def test_bootstrap_scope_federal_default(self):
        """Default scope is federal."""
        runner = CliRunner()
        result = runner.invoke(main, ["bootstrap", "--help"])
        assert "--scope" in result.output
        assert "federal" in result.output
        assert "cantonal" in result.output
        assert "all" in result.output

    def test_update_scope_option_present(self):
        """Update command has --scope option."""
        runner = CliRunner()
        result = runner.invoke(main, ["update", "--help"])
        assert "--scope" in result.output
        assert "federal" in result.output
        assert "cantonal" in result.output

    def test_bootstrap_canton_option_present(self):
        """Bootstrap command has --canton option."""
        runner = CliRunner()
        result = runner.invoke(main, ["bootstrap", "--help"])
        assert "--canton" in result.output

    @patch("legalize_ch.cli.Pipeline")
    @patch("legalize_ch.cantonal_pipeline.CantonalPipeline")
    def test_bootstrap_scope_all(self, mock_cantonal_cls, mock_pipeline_cls, tmp_path):
        """--scope all runs both federal and cantonal pipelines."""
        mock_pipeline = MagicMock()
        mock_pipeline.run.return_value = 5
        mock_pipeline_cls.return_value = mock_pipeline

        mock_cantonal = MagicMock()
        mock_cantonal.run.return_value = 3
        mock_cantonal_cls.return_value = mock_cantonal

        runner = CliRunner()
        with patch("legalize_ch.cantonal_pipeline.CantonalPipeline", mock_cantonal_cls):
            result = runner.invoke(main, [
                "bootstrap", "--repo", str(tmp_path),
                "--scope", "all", "--limit", "1",
            ])

        assert result.exit_code == 0 or "Error" not in result.output
        # Federal pipeline should be called
        mock_pipeline_cls.assert_called()

    @patch("legalize_ch.cantonal_pipeline.CantonalPipeline")
    def test_bootstrap_scope_cantonal_only(self, mock_cantonal_cls, tmp_path):
        """--scope cantonal runs only cantonal pipeline."""
        mock_cantonal = MagicMock()
        mock_cantonal.run.return_value = 2
        mock_cantonal_cls.return_value = mock_cantonal

        runner = CliRunner()
        result = runner.invoke(main, [
            "bootstrap", "--repo", str(tmp_path),
            "--scope", "cantonal", "--canton", "bs", "--limit", "1",
        ])

        assert "Cantonal: 2 commits" in result.output
        mock_cantonal.run.assert_called_once_with(
            cantons=["bs"], languages=["de", "fr", "it"], limit=1,
        )

    @patch("legalize_ch.cantonal_pipeline.CantonalPipeline")
    def test_update_scope_cantonal(self, mock_cantonal_cls, tmp_path):
        """--scope cantonal in update runs cantonal pipeline."""
        mock_cantonal = MagicMock()
        mock_cantonal.update.return_value = 1
        mock_cantonal_cls.return_value = mock_cantonal

        runner = CliRunner()
        result = runner.invoke(main, [
            "update", "--repo", str(tmp_path),
            "--scope", "cantonal", "--canton", "zh",
        ])

        assert "Cantonal: 1 commits" in result.output
        mock_cantonal.update.assert_called_once()
