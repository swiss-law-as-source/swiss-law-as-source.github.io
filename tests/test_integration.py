"""Integration test: small end-to-end pipeline run with 2-3 known SR numbers.

This test exercises the full pipeline path (fetch → transform → commit) using
mocked Fedlex responses so it runs offline and fast, but validates that all
components wire together correctly end-to-end.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from legalize_ch.fetcher import FedlexFetcher
from legalize_ch.models import LawEntry, LawVersion, LawText
from legalize_ch.pipeline import Pipeline


# ---------------------------------------------------------------------------
# Fixtures: realistic mock data for 2 well-known Swiss federal laws
# ---------------------------------------------------------------------------

BV_ENTRY = LawEntry(
    sr_number="101",
    uri="https://fedlex.data.admin.ch/eli/cc/1999/404",
    title_de="Bundesverfassung der Schweizerischen Eidgenossenschaft",
    title_fr="Constitution fédérale de la Confédération suisse",
    title_it="Costituzione federale della Confederazione Svizzera",
    date_document=date(1999, 4, 18),
    date_in_force=date(2000, 1, 1),
    abbreviation_de="BV",
    abbreviation_fr="Cst.",
    abbreviation_it="Cost.",
)

OR_ENTRY = LawEntry(
    sr_number="220",
    uri="https://fedlex.data.admin.ch/eli/cc/27/317_321_377",
    title_de="Bundesgesetz betreffend die Ergänzung des Schweizerischen Zivilgesetzbuches (Fünfter Teil: Obligationenrecht)",
    title_fr="Loi fédérale complétant le code civil suisse (Livre cinquième: Droit des obligations)",
    title_it="Legge federale di complemento del Codice civile svizzero (Libro quinto: Diritto delle obbligazioni)",
    date_document=date(1911, 3, 30),
    date_in_force=date(1912, 1, 1),
    abbreviation_de="OR",
    abbreviation_fr="CO",
    abbreviation_it="CO",
)

BV_VERSIONS = [
    LawVersion(
        sr_number="101",
        version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20000101",
        date_applicable=date(2000, 1, 1),
    ),
    LawVersion(
        sr_number="101",
        version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20140101",
        date_applicable=date(2014, 1, 1),
    ),
]

OR_VERSIONS = [
    LawVersion(
        sr_number="220",
        version_uri="https://fedlex.data.admin.ch/eli/cc/27/317_321_377/19710101",
        date_applicable=date(1971, 1, 1),
    ),
]

SAMPLE_AKN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <article eId="art_1">
        <num>Art. 1</num>
        <heading>Grundsatz</heading>
        <paragraph eId="art_1__para_1">
          <num>1</num>
          <content><p>Die Schweiz ist ein Bundesstaat.</p></content>
        </paragraph>
      </article>
    </body>
  </act>
</akomaNtoso>"""

SAMPLE_HTML = """<html><body>
<h1>Obligationenrecht</h1>
<p>Art. 1 – Zum Abschlusse eines Vertrages ist die übereinstimmende
gegenseitige Willensäusserung der Parteien erforderlich.</p>
</body></html>"""


def _make_law_text(version: LawVersion, lang: str, *, xml: str = "", html: str = "",
                   title: str = "") -> LawText:
    return LawText(
        sr_number=version.sr_number,
        language=lang,
        version_date=version.date_applicable,
        title=title,
        xml_content=xml,
        html_content=html,
        content_url=f"https://example.com/{version.sr_number}/{lang}",
    )


# ---------------------------------------------------------------------------
# Mock wiring: intercept fetcher calls and return test data
# ---------------------------------------------------------------------------

def _mock_fetch_catalog(limit=None):
    entries = [BV_ENTRY, OR_ENTRY]
    if limit:
        entries = entries[:limit]
    return entries


def _mock_fetch_versions(law: LawEntry):
    if law.sr_number == "101":
        return BV_VERSIONS
    elif law.sr_number == "220":
        return OR_VERSIONS
    return []


def _mock_fetch_text(version: LawVersion, lang: str):
    if version.sr_number == "101":
        return _make_law_text(
            version, lang,
            xml=SAMPLE_AKN_XML,
            title="Bundesverfassung" if lang == "de" else "Constitution" if lang == "fr" else "Costituzione",
        )
    elif version.sr_number == "220":
        return _make_law_text(
            version, lang,
            html=SAMPLE_HTML,
            title="Obligationenrecht" if lang == "de" else "Droit des obligations" if lang == "fr" else "Diritto delle obbligazioni",
        )
    return None


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline_repo(tmp_path):
    """Create a temporary git repo and return a Pipeline instance pointing at it."""
    # Initialise a bare git repo
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.ch"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )

    pipeline = Pipeline(repo_path=tmp_path, rate_limit=0.0)
    return pipeline, tmp_path


class TestEndToEndPipeline:
    """Full pipeline run with mocked Fedlex data for SR 101 (BV) and SR 220 (OR)."""

    def test_full_run_creates_commits_and_files(self, pipeline_repo):
        """Pipeline.run() should create markdown files and git commits for all versions."""
        pipeline, repo = pipeline_repo

        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            total_commits = pipeline.run(languages=["de", "fr"])

        # 4 commits: BV publication@1999-04-18 + BV@2000 + BV@2014 + OR@1971.
        # OR's publication commit (date_document=1911-03-30) is skipped because
        # git rejects pre-1970 timestamps; the consolidation at 1971-01-01 is
        # the earliest representable.
        assert total_commits == 4

        # Verify markdown files exist for both languages
        assert (repo / "ch/101/de/101.md").exists()
        assert (repo / "ch/101/fr/101.md").exists()
        assert (repo / "ch/220/de/220.md").exists()
        assert (repo / "ch/220/fr/220.md").exists()

        # Verify file content has YAML frontmatter
        bv_de = (repo / "ch/101/de/101.md").read_text()
        assert bv_de.startswith("---\n")
        assert "sr_number: '101'" in bv_de or "sr_number: \"101\"" in bv_de or "sr_number: 101" in bv_de
        assert "language: de" in bv_de
        assert "fedlex.data.admin.ch" in bv_de

        # Verify AKN XML was transformed to markdown body
        assert "Art. 1" in bv_de
        assert "Bundesstaat" in bv_de

        # Verify HTML was transformed for OR
        or_de = (repo / "ch/220/de/220.md").read_text()
        assert "Obligationenrecht" in or_de

        # Verify git log has the correct number of commits
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=repo, capture_output=True, text=True,
        )
        # 4 version commits + 1 initial commit = 5
        commit_lines = [l for l in log.stdout.strip().split("\n") if l]
        assert len(commit_lines) == 5

    def test_chronological_commit_order(self, pipeline_repo):
        """Commits should be ordered chronologically by version date."""
        pipeline, repo = pipeline_repo

        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            pipeline.run(languages=["de"], chronological=True)

        # Get commit dates in order (oldest first)
        log = subprocess.run(
            ["git", "log", "--format=%aI", "--reverse"],
            cwd=repo, capture_output=True, text=True,
        )
        dates = [l.strip() for l in log.stdout.strip().split("\n") if l.strip()]

        # Skip initial commit; remaining should be chronological
        version_dates = dates[1:]
        # 4: BV pub@1999 + BV@2000 + BV@2014 + OR@1971 (OR pub skipped, pre-1970)
        assert len(version_dates) == 4

        # OR 1971 < BV pub 1999 < BV 2000 < BV 2014
        assert version_dates == sorted(version_dates)

    def test_pipeline_state_tracking(self, pipeline_repo):
        """Pipeline should track processed versions in state file."""
        pipeline, repo = pipeline_repo

        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            pipeline.run(languages=["de"])

        # State file should exist and contain processed versions
        state_file = repo / "data" / "pipeline_state.json"
        assert state_file.exists()

        state = json.loads(state_file.read_text())
        assert "processed" in state
        assert "101@2000-01-01" in state["processed"]
        assert "101@2014-01-01" in state["processed"]
        assert "220@1971-01-01" in state["processed"]
        assert state.get("last_run") is not None

    def test_idempotent_rerun(self, pipeline_repo):
        """Running the pipeline twice should not create duplicate commits."""
        pipeline, repo = pipeline_repo

        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            first_run = pipeline.run(languages=["de"])

        # Re-load state (as a fresh Pipeline would)
        pipeline2 = Pipeline(repo_path=repo, rate_limit=0.0)
        with patch.object(pipeline2.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline2.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline2.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            second_run = pipeline2.run(languages=["de"])

        assert first_run == 4
        assert second_run == 0  # all already processed

    def test_sr_filter(self, pipeline_repo):
        """SR filter should restrict which laws are processed."""
        pipeline, repo = pipeline_repo

        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            total = pipeline.run(languages=["de"], sr_filter="101")

        # BV (SR 101): 1 publication commit + 2 consolidations = 3
        assert total == 3
        assert (repo / "ch/101/de/101.md").exists()
        assert not (repo / "ch/220/de/220.md").exists()

    def test_latest_only_mode(self, pipeline_repo):
        """latest_only should only process the most recent version per law."""
        pipeline, repo = pipeline_repo

        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            total = pipeline.run(languages=["de"], latest_only=True)

        # 1 version per law = 2 total
        assert total == 2

    def test_commit_messages_contain_sr_number(self, pipeline_repo):
        """Each commit message should reference the SR number and date."""
        pipeline, repo = pipeline_repo

        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            pipeline.run(languages=["de"])

        log = subprocess.run(
            ["git", "log", "--format=%s"],
            cwd=repo, capture_output=True, text=True,
        )
        messages = log.stdout.strip().split("\n")
        # Filter out the initial commit
        law_msgs = [m for m in messages if m.startswith("SR ")]

        # 4 commits: BV pub@1999 + BV@2000 + BV@2014 + OR@1971
        assert len(law_msgs) == 4
        assert any("SR 101" in m for m in law_msgs)
        assert any("SR 220" in m for m in law_msgs)
        # Messages should contain the version date for each commit
        assert any("1999-04-18" in m for m in law_msgs)
        assert any("2000-01-01" in m for m in law_msgs)
        assert any("1971-01-01" in m for m in law_msgs)

    def test_pre_1970_publication_date_in_frontmatter(self, pipeline_repo):
        """Pre-1970 publication dates can't go in git timestamps, so they
        must live in the markdown frontmatter for API discoverability."""
        pipeline, repo = pipeline_repo

        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            pipeline.run(languages=["de", "fr"])

        # OR (220) has date_document=1911-03-30 — pre-1970, so its earliest
        # commit (1971-01-01) should carry the marker in YAML frontmatter.
        or_de = (repo / "ch/220/de/220.md").read_text()
        assert "original_publication_date: '1911-03-30'" in or_de
        or_fr = (repo / "ch/220/fr/220.md").read_text()
        assert "original_publication_date: '1911-03-30'" in or_fr

        # BV (101) has date_document=1999-04-18 — post-1970. The prepended
        # publication commit handles it; consolidation files still get
        # the marker for consistency / API queryability.
        bv_de = (repo / "ch/101/de/101.md").read_text()
        assert "original_publication_date: '1999-04-18'" in bv_de

    def test_frontmatter_fields_valid(self, pipeline_repo):
        """All generated markdown files should have valid YAML frontmatter."""
        pipeline, repo = pipeline_repo

        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            pipeline.run(languages=["de", "fr"])

        import yaml

        for md_file in repo.rglob("*.md"):
            if md_file.name == "README.md":
                continue
            content = md_file.read_text()
            assert content.startswith("---\n"), f"Missing frontmatter in {md_file}"
            # Extract YAML between --- markers
            parts = content.split("---\n", 2)
            assert len(parts) >= 3, f"Malformed frontmatter in {md_file}"
            meta = yaml.safe_load(parts[1])
            assert "sr_number" in meta, f"Missing sr_number in {md_file}"
            assert "language" in meta, f"Missing language in {md_file}"
            assert "version_date" in meta, f"Missing version_date in {md_file}"
            assert "source" in meta, f"Missing source in {md_file}"
            assert meta["language"] in ("de", "fr", "it")


class TestEndToEndUpdate:
    """Incremental update mode with mocked data."""

    def test_update_processes_only_new_versions(self, pipeline_repo):
        """update() should only process versions newer than since_date."""
        pipeline, repo = pipeline_repo

        # First: run full pipeline to populate state
        with patch.object(pipeline.fetcher, "fetch_catalog", side_effect=_mock_fetch_catalog), \
             patch.object(pipeline.fetcher, "fetch_versions", side_effect=_mock_fetch_versions), \
             patch.object(pipeline.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            pipeline.run(languages=["de"])

        # Now simulate an update with a new BV version
        new_version = LawVersion(
            sr_number="101",
            version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20260101",
            date_applicable=date(2026, 1, 1),
        )

        def mock_modified_since(since_date, limit=None):
            return [BV_ENTRY]

        def mock_versions_update(law):
            if law.sr_number == "101":
                return BV_VERSIONS + [new_version]
            return OR_VERSIONS

        pipeline2 = Pipeline(repo_path=repo, rate_limit=0.0)
        with patch.object(pipeline2.fetcher, "fetch_modified_since", side_effect=mock_modified_since), \
             patch.object(pipeline2.fetcher, "fetch_versions", side_effect=mock_versions_update), \
             patch.object(pipeline2.fetcher, "fetch_text", side_effect=_mock_fetch_text):
            update_commits = pipeline2.update(
                languages=["de"],
                since_override=date(2025, 1, 1),
            )

        # Only the new 2026 version should be committed
        assert update_commits == 1

        # Verify state updated
        state = json.loads((repo / "data" / "pipeline_state.json").read_text())
        assert "101@2026-01-01" in state["processed"]
