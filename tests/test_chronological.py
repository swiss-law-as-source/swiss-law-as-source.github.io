"""Tests for chronological commit ordering (task 2.4)."""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.legalize_ch.models import LawEntry, LawVersion, LawText
from src.legalize_ch.pipeline import Pipeline, _PendingRevision


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temp git repo with pipeline state."""
    import os
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path, capture_output=True, env=env,
    )
    state_dir = tmp_path / "data"
    state_dir.mkdir()
    state = {"processed": {}, "last_run": "2024-01-01"}
    (state_dir / "pipeline_state.json").write_text(json.dumps(state))
    return tmp_path


def _make_law(sr="101", title="Testgesetz"):
    return LawEntry(sr_number=sr, uri=f"http://example.com/{sr}", title_de=f"{title} SR {sr}")


def _make_versions(sr, dates):
    return [
        LawVersion(sr_number=sr, version_uri=f"http://example.com/{sr}/{d}", date_applicable=d)
        for d in dates
    ]


class TestCollectRevisions:
    """Test _collect_revisions returns pending revisions without committing."""

    @patch.object(Pipeline, "_save_state")
    def test_collects_without_committing(self, mock_save, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)
        law = _make_law("300")
        versions = _make_versions("300", [date(2020, 1, 1), date(2021, 6, 1)])

        with patch.object(p.fetcher, "fetch_versions", return_value=versions), \
             patch.object(p.fetcher, "fetch_text") as mock_text:
            mock_text.return_value = LawText(
                sr_number="300", language="de", version_date=date(2020, 1, 1),
                title="Test", xml_content="<xml>test</xml>",
            )

            result = p._collect_revisions(law, ["de"], latest_only=False)

            assert len(result) == 2
            assert all(isinstance(r, _PendingRevision) for r in result)
            assert result[0].revision.date == date(2020, 1, 1)
            assert result[1].revision.date == date(2021, 6, 1)

    @patch.object(Pipeline, "_save_state")
    def test_skips_already_processed(self, mock_save, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)
        p._mark_processed("300", date(2020, 1, 1))

        law = _make_law("300")
        versions = _make_versions("300", [date(2020, 1, 1), date(2021, 6, 1)])

        with patch.object(p.fetcher, "fetch_versions", return_value=versions), \
             patch.object(p.fetcher, "fetch_text") as mock_text:
            mock_text.return_value = LawText(
                sr_number="300", language="de", version_date=date(2021, 6, 1),
                title="Test", xml_content="<xml>test</xml>",
            )

            result = p._collect_revisions(law, ["de"], latest_only=False)

            assert len(result) == 1
            assert result[0].revision.date == date(2021, 6, 1)

    @patch.object(Pipeline, "_save_state")
    def test_since_date_filter(self, mock_save, tmp_repo):
        p = Pipeline(repo_path=tmp_repo)
        law = _make_law("300")
        versions = _make_versions("300", [
            date(2019, 1, 1), date(2020, 1, 1), date(2024, 6, 1)
        ])

        with patch.object(p.fetcher, "fetch_versions", return_value=versions), \
             patch.object(p.fetcher, "fetch_text") as mock_text:
            mock_text.return_value = LawText(
                sr_number="300", language="de", version_date=date(2024, 6, 1),
                title="Test", xml_content="<xml>test</xml>",
            )

            result = p._collect_revisions(law, ["de"], latest_only=False,
                                          since_date=date(2024, 1, 1))

            # Only 2024-06-01 passes the since_date filter
            assert len(result) == 1
            assert result[0].revision.date == date(2024, 6, 1)


class TestRunChronological:
    """Test that _run_chronological sorts commits by date across laws."""

    @patch.object(Pipeline, "_save_state")
    def test_sorts_across_laws(self, mock_save, tmp_repo):
        """Revisions from different laws should be interleaved by date."""
        p = Pipeline(repo_path=tmp_repo)

        law_a = _make_law("100", "Law A")
        law_b = _make_law("200", "Law B")

        # Law A has versions in 2020, 2022
        # Law B has versions in 2019, 2021
        # Chronological order should be: B@2019, A@2020, B@2021, A@2022
        versions_a = _make_versions("100", [date(2020, 1, 1), date(2022, 1, 1)])
        versions_b = _make_versions("200", [date(2019, 1, 1), date(2021, 1, 1)])

        committed_dates = []

        def mock_commit(revision, law):
            committed_dates.append(revision.date)
            return True

        with patch.object(p.fetcher, "fetch_versions") as mock_versions, \
             patch.object(p.fetcher, "fetch_text") as mock_text, \
             patch.object(p.committer, "commit_revision", side_effect=mock_commit):

            def return_versions(law):
                if law.sr_number == "100":
                    return versions_a
                return versions_b

            mock_versions.side_effect = return_versions
            mock_text.return_value = LawText(
                sr_number="100", language="de", version_date=date(2020, 1, 1),
                title="Test", xml_content="<xml>test</xml>",
            )

            total = p._run_chronological([law_a, law_b], ["de"], latest_only=False)

            assert total == 4
            # Verify chronological order
            assert committed_dates == [
                date(2019, 1, 1),
                date(2020, 1, 1),
                date(2021, 1, 1),
                date(2022, 1, 1),
            ]

    @patch.object(Pipeline, "_save_state")
    def test_stable_sort_preserves_sr_order_for_same_date(self, mock_save, tmp_repo):
        """When multiple laws have the same date, preserve catalog order."""
        p = Pipeline(repo_path=tmp_repo)

        law_a = _make_law("100", "Law A")
        law_b = _make_law("200", "Law B")

        # Both laws have a version on the same date
        versions_a = _make_versions("100", [date(2020, 1, 1)])
        versions_b = _make_versions("200", [date(2020, 1, 1)])

        committed_srs = []

        def mock_commit(revision, law):
            committed_srs.append(revision.sr_number)
            return True

        with patch.object(p.fetcher, "fetch_versions") as mock_versions, \
             patch.object(p.fetcher, "fetch_text") as mock_text, \
             patch.object(p.committer, "commit_revision", side_effect=mock_commit):

            mock_versions.side_effect = lambda law: versions_a if law.sr_number == "100" else versions_b
            mock_text.return_value = LawText(
                sr_number="100", language="de", version_date=date(2020, 1, 1),
                title="Test", xml_content="<xml>test</xml>",
            )

            total = p._run_chronological([law_a, law_b], ["de"], latest_only=False)

            assert total == 2
            # Stable sort: law_a comes first in catalog, so it stays first
            assert committed_srs == ["100", "200"]
