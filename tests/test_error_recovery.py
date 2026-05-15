"""Tests for per-law error recovery and state persistence.

Verifies that the pipeline saves state after every law/commit so that
mid-run failures don't lose progress.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from legalize_ch.models import LawEntry, LawVersion, LawText
from legalize_ch.pipeline import Pipeline


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

LAW_A = LawEntry(
    sr_number="100",
    uri="https://fedlex.data.admin.ch/eli/cc/test/100",
    title_de="Gesetz A",
    title_fr="Loi A",
    title_it="Legge A",
    date_document=date(2000, 1, 1),
    date_in_force=date(2000, 6, 1),
    abbreviation_de="GA",
    abbreviation_fr="LA",
    abbreviation_it="LA",
)

LAW_B = LawEntry(
    sr_number="200",
    uri="https://fedlex.data.admin.ch/eli/cc/test/200",
    title_de="Gesetz B",
    title_fr="Loi B",
    title_it="Legge B",
    date_document=date(2001, 1, 1),
    date_in_force=date(2001, 6, 1),
    abbreviation_de="GB",
    abbreviation_fr="LB",
    abbreviation_it="LB",
)

LAW_C = LawEntry(
    sr_number="300",
    uri="https://fedlex.data.admin.ch/eli/cc/test/300",
    title_de="Gesetz C",
    title_fr="Loi C",
    title_it="Legge C",
    date_document=date(2002, 1, 1),
    date_in_force=date(2002, 6, 1),
    abbreviation_de="GC",
    abbreviation_fr="LC",
    abbreviation_it="LC",
)

VERSIONS = {
    "100": [
        LawVersion(sr_number="100",
                   version_uri="https://fedlex.data.admin.ch/eli/cc/test/100/20000601",
                   date_applicable=date(2000, 6, 1)),
    ],
    "200": [
        LawVersion(sr_number="200",
                   version_uri="https://fedlex.data.admin.ch/eli/cc/test/200/20010601",
                   date_applicable=date(2001, 6, 1)),
    ],
    "300": [
        LawVersion(sr_number="300",
                   version_uri="https://fedlex.data.admin.ch/eli/cc/test/300/20020601",
                   date_applicable=date(2002, 6, 1)),
    ],
}

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act><body>
    <article eId="art_1"><num>Art. 1</num>
      <paragraph eId="art_1__para_1"><content><p>Test content.</p></content></paragraph>
    </article>
  </body></act>
</akomaNtoso>"""


def _mock_fetch_versions(law):
    return VERSIONS.get(law.sr_number, [])


def _mock_fetch_text(version, lang):
    return LawText(
        sr_number=version.sr_number,
        language=lang,
        version_date=version.date_applicable,
        title=f"Title {version.sr_number}",
        xml_content=SAMPLE_XML,
        html_content="",
        content_url=f"https://example.com/{version.sr_number}/{lang}",
    )


def _mock_fetch_text_fail_on_200(version, lang):
    """Raise an error when fetching SR 200."""
    if version.sr_number == "200":
        raise RuntimeError("Simulated fetch failure for SR 200")
    return _mock_fetch_text(version, lang)


@pytest.fixture
def pipeline_repo(tmp_path):
    """Create a temporary git repo and return a Pipeline instance."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.ch"],
                   cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=tmp_path, capture_output=True, check=True)
    pipeline = Pipeline(repo_path=tmp_path, rate_limit=0.0)
    return pipeline, tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStatePersistedAfterEachLaw:
    """State must be saved after every law so crashes don't lose progress."""

    def test_sequential_saves_state_after_each_law(self, pipeline_repo):
        """In sequential mode, state is saved after each law, not every 10."""
        pipeline, repo = pipeline_repo
        save_call_count = []

        original_save = pipeline._save_state

        def counting_save():
            original_save()
            save_call_count.append(1)

        catalog = [LAW_A, LAW_B, LAW_C]

        with patch.object(pipeline.fetcher, "fetch_catalog", return_value=catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text), \
             patch.object(pipeline, "_save_state", side_effect=counting_save):
            pipeline.run(languages=["de"], chronological=False)

        # 3 laws + 1 final save at end of run() = 4 saves minimum
        # (each law gets 1 save + final save for last_run)
        assert len(save_call_count) >= 4

    def test_chronological_saves_state_after_each_commit(self, pipeline_repo):
        """In chronological mode, state is saved after each successful commit."""
        pipeline, repo = pipeline_repo
        save_call_count = []

        original_save = pipeline._save_state

        def counting_save():
            original_save()
            save_call_count.append(1)

        catalog = [LAW_A, LAW_B, LAW_C]

        with patch.object(pipeline.fetcher, "fetch_catalog", return_value=catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text), \
             patch.object(pipeline, "_save_state", side_effect=counting_save):
            pipeline.run(languages=["de"], chronological=True)

        # 3 commits + 1 final save = 4 saves minimum
        assert len(save_call_count) >= 4


class TestErrorRecoveryMidRun:
    """If a law fails, other laws are still processed and state is persisted."""

    def test_sequential_continues_after_error(self, pipeline_repo):
        """Sequential mode: failure on one law doesn't prevent others."""
        pipeline, repo = pipeline_repo
        catalog = [LAW_A, LAW_B, LAW_C]

        def fail_on_b(law):
            if law.sr_number == "200":
                raise RuntimeError("Simulated failure")
            return _mock_fetch_versions(law)

        with patch.object(pipeline.fetcher, "fetch_catalog", return_value=catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=fail_on_b), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            total = pipeline.run(languages=["de"], chronological=False)

        # Laws A and C should succeed, B should fail
        assert total == 2

        state = json.loads((repo / "data" / "pipeline_state.json").read_text())
        assert "100@2000-06-01" in state["processed"]
        assert "200@2001-06-01" not in state["processed"]
        assert "300@2002-06-01" in state["processed"]

    def test_chronological_continues_after_commit_error(self, pipeline_repo):
        """Chronological mode: failure on one commit doesn't prevent others."""
        pipeline, repo = pipeline_repo
        catalog = [LAW_A, LAW_B, LAW_C]

        original_commit = pipeline.committer.commit_revision

        def fail_on_b_commit(revision, law=None):
            if revision.sr_number == "200":
                raise RuntimeError("Simulated commit failure")
            return original_commit(revision, law)

        with patch.object(pipeline.fetcher, "fetch_catalog", return_value=catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text), \
             patch.object(pipeline.committer, "commit_revision", side_effect=fail_on_b_commit):
            total = pipeline.run(languages=["de"], chronological=True)

        # A and C committed (publication + consolidation each), B's commits failed.
        assert total == 4

        state = json.loads((repo / "data" / "pipeline_state.json").read_text())
        assert "100@2000-06-01" in state["processed"]
        assert "200@2001-06-01" not in state["processed"]
        assert "300@2002-06-01" in state["processed"]

    def test_update_sequential_continues_after_error(self, pipeline_repo):
        """Update (sequential mode): failure on one law doesn't prevent others."""
        pipeline, repo = pipeline_repo

        # Pre-populate state with last_run
        pipeline.state["last_run"] = "2000-01-01"
        pipeline._save_state()

        catalog = [LAW_A, LAW_B, LAW_C]

        def fail_on_b(law):
            if law.sr_number == "200":
                raise RuntimeError("Simulated failure")
            return _mock_fetch_versions(law)

        with patch.object(pipeline.fetcher, "fetch_modified_since", return_value=catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=fail_on_b), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            total = pipeline.update(languages=["de"], chronological=False)

        assert total == 2

        state = json.loads((repo / "data" / "pipeline_state.json").read_text())
        assert "100@2000-06-01" in state["processed"]
        assert "200@2001-06-01" not in state["processed"]
        assert "300@2002-06-01" in state["processed"]

    def test_update_chronological_continues_after_commit_error(self, pipeline_repo):
        """Update (chronological mode): commit failure doesn't prevent others."""
        pipeline, repo = pipeline_repo

        # Pre-populate state
        pipeline.state["last_run"] = "2000-01-01"
        pipeline._save_state()

        catalog = [LAW_A, LAW_B, LAW_C]

        original_commit = pipeline.committer.commit_revision

        def fail_on_b_commit(revision, law=None):
            if revision.sr_number == "200":
                raise RuntimeError("Simulated commit failure")
            return original_commit(revision, law)

        with patch.object(pipeline.fetcher, "fetch_modified_since", return_value=catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text), \
             patch.object(pipeline.committer, "commit_revision", side_effect=fail_on_b_commit):
            total = pipeline.update(languages=["de"], chronological=True)

        # A and C committed (publication + consolidation each), B's commits failed.
        assert total == 4

        state = json.loads((repo / "data" / "pipeline_state.json").read_text())
        assert "100@2000-06-01" in state["processed"]
        assert "200@2001-06-01" not in state["processed"]
        assert "300@2002-06-01" in state["processed"]


class TestStateRecoveryAfterCrash:
    """Verify that after a simulated crash, re-running picks up where it left off."""

    def test_resume_after_partial_run(self, pipeline_repo):
        """If pipeline crashes after law A, re-running only processes B and C."""
        pipeline, repo = pipeline_repo
        catalog = [LAW_A, LAW_B, LAW_C]

        call_count = [0]

        def fail_after_first(law):
            call_count[0] += 1
            if call_count[0] > 1:
                raise KeyboardInterrupt("Simulated crash")
            return _mock_fetch_versions(law)

        # First run: only law A completes, then crash
        with patch.object(pipeline.fetcher, "fetch_catalog", return_value=catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=fail_after_first), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            try:
                pipeline.run(languages=["de"], chronological=False)
            except KeyboardInterrupt:
                pass

        # Verify A was saved
        state = json.loads((repo / "data" / "pipeline_state.json").read_text())
        assert "100@2000-06-01" in state["processed"]
        assert "200@2001-06-01" not in state["processed"]

        # Second run: should skip A and process B and C
        pipeline2 = Pipeline(repo_path=repo, rate_limit=0.0)
        with patch.object(pipeline2.fetcher, "fetch_catalog", return_value=catalog), \
             patch.object(pipeline2.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline2.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            total = pipeline2.run(languages=["de"], chronological=False)

        # Only B and C should create new commits
        assert total == 2

        state = json.loads((repo / "data" / "pipeline_state.json").read_text())
        assert "100@2000-06-01" in state["processed"]
        assert "200@2001-06-01" in state["processed"]
        assert "300@2002-06-01" in state["processed"]
