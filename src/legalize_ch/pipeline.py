"""Pipeline orchestrator — fetch, transform, commit Swiss law."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from .committer import GitCommitter
from .fetcher import FedlexFetcher
from .models import LawEntry, LawRevision
from .transformer import law_to_markdown

logger = logging.getLogger(__name__)

STATE_FILE = "data/pipeline_state.json"


class Pipeline:
    """Orchestrates the full fetch → transform → commit pipeline."""

    def __init__(self, repo_path: str | Path, rate_limit: float = 1.5):
        self.repo_path = Path(repo_path)
        self.fetcher = FedlexFetcher(rate_limit=rate_limit)
        self.committer = GitCommitter(repo_path)
        self.state_file = self.repo_path / STATE_FILE
        self.state: dict = self._load_state()

    def _load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"processed": {}, "last_run": None}

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, default=str))

    def _is_processed(self, sr_number: str, version_date: date) -> bool:
        key = f"{sr_number}@{version_date.isoformat()}"
        return key in self.state.get("processed", {})

    def _mark_processed(self, sr_number: str, version_date: date):
        key = f"{sr_number}@{version_date.isoformat()}"
        self.state.setdefault("processed", {})[key] = True

    def _get_abbreviation(self, law: LawEntry, lang: str) -> str:
        return {
            "de": law.abbreviation_de,
            "fr": law.abbreviation_fr,
            "it": law.abbreviation_it,
        }.get(lang, "")

    def _get_title(self, law: LawEntry, lang: str) -> str:
        return {
            "de": law.title_de,
            "fr": law.title_fr,
            "it": law.title_it,
        }.get(lang, "")

    def run(self, limit: int | None = None, languages: list[str] | None = None,
            sr_filter: str | None = None, latest_only: bool = False):
        """Run the full pipeline.

        Args:
            limit: Max number of laws to process (None = all)
            languages: Languages to fetch (default: all three)
            sr_filter: Only process laws matching this SR prefix
            latest_only: If True, only fetch the most recent version per law
        """
        languages = languages or ["de", "fr", "it"]

        # Initialize repo
        self.committer.init_repo()
        self.committer.commit_initial()

        # Fetch catalog
        catalog = self.fetcher.fetch_catalog(limit=limit)
        if sr_filter:
            catalog = [e for e in catalog if e.sr_number.startswith(sr_filter)]

        logger.info("Processing %d laws in languages %s (latest_only=%s)",
                     len(catalog), languages, latest_only)
        total_commits = 0

        for i, law in enumerate(catalog):
            logger.info("[%d/%d] SR %s: %s", i + 1, len(catalog), law.sr_number,
                        law.title_de or law.title_fr or law.sr_number)

            try:
                commits = self._process_law(law, languages, latest_only)
                total_commits += commits
            except Exception as e:
                logger.error("Error processing SR %s: %s", law.sr_number, e)

            if (i + 1) % 10 == 0:
                self._save_state()

        self.state["last_run"] = date.today().isoformat()
        self._save_state()
        logger.info("Pipeline complete. Total commits: %d", total_commits)
        return total_commits

    def update(self, limit: int | None = None, languages: list[str] | None = None,
               sr_filter: str | None = None):
        """Incremental update: only fetch laws modified since last_run.

        Args:
            limit: Max number of laws to process (None = all)
            languages: Languages to fetch (default: all three)
            sr_filter: Only process laws matching this SR prefix
        """
        languages = languages or ["de", "fr", "it"]

        last_run = self.state.get("last_run")
        if not last_run:
            logger.error("No last_run date in state. Run 'bootstrap' first.")
            raise SystemExit(1)

        since = date.fromisoformat(last_run)
        logger.info("Updating laws modified since %s", since.isoformat())

        # Fetch only laws with versions since last_run
        catalog = self.fetcher.fetch_modified_since(since, limit=limit)
        if sr_filter:
            catalog = [e for e in catalog if e.sr_number.startswith(sr_filter)]

        logger.info("Found %d laws to update", len(catalog))
        total_commits = 0

        for i, law in enumerate(catalog):
            logger.info("[%d/%d] SR %s: %s", i + 1, len(catalog), law.sr_number,
                        law.title_de or law.title_fr or law.sr_number)

            try:
                commits = self._process_law(law, languages, latest_only=False)
                total_commits += commits
            except Exception as e:
                logger.error("Error processing SR %s: %s", law.sr_number, e)

            if (i + 1) % 10 == 0:
                self._save_state()

        self.state["last_run"] = date.today().isoformat()
        self._save_state()
        logger.info("Update complete. %d laws checked, %d commits created.",
                    len(catalog), total_commits)
        return total_commits

    def _process_law(self, law: LawEntry, languages: list[str],
                     latest_only: bool) -> int:
        """Process a single law. Returns number of commits."""
        versions = self.fetcher.fetch_versions(law)

        if not versions:
            revision = self._fetch_current(law, languages)
            if revision and revision.texts:
                if self.committer.commit_revision(revision, law):
                    self._mark_processed(law.sr_number, revision.date)
                    return 1
            return 0

        if latest_only:
            versions = [versions[-1]]

        commits = 0
        for version in versions:
            if self._is_processed(law.sr_number, version.date_applicable):
                continue

            texts = {}
            for lang in languages:
                text = self.fetcher.fetch_text(version, lang)
                if text and (text.xml_content or text.html_content or text.title):
                    abbr = self._get_abbreviation(law, lang)
                    md = law_to_markdown(
                        sr_number=law.sr_number,
                        title=text.title or self._get_title(law, lang),
                        xml_content=text.xml_content,
                        html_content=text.html_content,
                        language=lang,
                        version_date=version.date_applicable,
                        abbreviation=abbr,
                    )
                    texts[lang] = md

            if not texts:
                continue

            revision = LawRevision(
                sr_number=law.sr_number,
                date=version.date_applicable,
                title_de=law.title_de,
                title_fr=law.title_fr,
                title_it=law.title_it,
                texts=texts,
            )

            if self.committer.commit_revision(revision, law):
                commits += 1
                self._mark_processed(law.sr_number, version.date_applicable)

        return commits

    def _fetch_current(self, law: LawEntry, languages: list[str]) -> LawRevision | None:
        """Fetch current text when no consolidation versions exist."""
        d = law.date_in_force or law.date_document or date.today()
        texts = {}

        for lang in languages:
            title = self._get_title(law, lang)
            if title:
                abbr = self._get_abbreviation(law, lang)
                md = law_to_markdown(
                    sr_number=law.sr_number,
                    title=title,
                    xml_content="",
                    html_content="",
                    language=lang,
                    version_date=d,
                    abbreviation=abbr,
                )
                texts[lang] = md

        if not texts:
            return None

        return LawRevision(
            sr_number=law.sr_number,
            date=d,
            title_de=law.title_de,
            title_fr=law.title_fr,
            title_it=law.title_it,
            texts=texts,
        )
