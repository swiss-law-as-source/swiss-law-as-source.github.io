"""Pipeline orchestrator — fetch, transform, commit Swiss law."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import NamedTuple

from .committer import GitCommitter
from .fetcher import FedlexFetcher
from .models import LawEntry, LawRevision
from .transformer import law_to_markdown

logger = logging.getLogger(__name__)

STATE_FILE = "data/pipeline_state.json"


class _PendingRevision(NamedTuple):
    """A revision ready to be committed, used for chronological sorting."""
    revision: LawRevision
    law: LawEntry


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

    def _get_known_version_count(self, sr_number: str) -> int:
        """Return the number of known processed versions for an SR number."""
        prefix = f"{sr_number}@"
        return sum(1 for k in self.state.get("processed", {}) if k.startswith(prefix))

    def _get_known_version_dates(self, sr_number: str) -> set[str]:
        """Return the set of known version date strings for an SR number."""
        prefix = f"{sr_number}@"
        return {k.split("@", 1)[1] for k in self.state.get("processed", {}) if k.startswith(prefix)}

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
            sr_filter: str | None = None, latest_only: bool = False,
            chronological: bool = True):
        """Run the full pipeline.

        Args:
            limit: Max number of laws to process (None = all)
            languages: Languages to fetch (default: all three)
            sr_filter: Only process laws matching this SR prefix
            latest_only: If True, only fetch the most recent version per law
            chronological: If True (default), sort all revisions by date before
                          committing so git history is in chronological order
        """
        languages = languages or ["de", "fr", "it"]

        # Initialize repo
        self.committer.init_repo()
        self.committer.commit_initial()

        # Fetch catalog
        catalog = self.fetcher.fetch_catalog(limit=limit)
        if sr_filter:
            catalog = [e for e in catalog if e.sr_number.startswith(sr_filter)]

        logger.info("Processing %d laws in languages %s (latest_only=%s, chronological=%s)",
                     len(catalog), languages, latest_only, chronological)

        if chronological:
            total_commits = self._run_chronological(catalog, languages, latest_only)
        else:
            total_commits = self._run_sequential(catalog, languages, latest_only)

        self.state["last_run"] = date.today().isoformat()
        self._save_state()
        logger.info("Pipeline complete. Total commits: %d", total_commits)
        return total_commits

    def _run_sequential(self, catalog: list[LawEntry], languages: list[str],
                        latest_only: bool) -> int:
        """Process laws sequentially (original behavior, no date sorting).

        State is saved after every law to allow recovery from mid-run failures.
        """
        total_commits = 0
        for i, law in enumerate(catalog):
            logger.info("[%d/%d] SR %s: %s", i + 1, len(catalog), law.sr_number,
                        law.title_de or law.title_fr or law.sr_number)
            try:
                commits = self._process_law(law, languages, latest_only)
                total_commits += commits
            except Exception as e:
                logger.error("Error processing SR %s: %s", law.sr_number, e)
            self._save_state()
        return total_commits

    def _run_chronological(self, catalog: list[LawEntry], languages: list[str],
                           latest_only: bool) -> int:
        """Collect all revisions, sort by date, then commit in chronological order.

        This ensures git history reflects actual legal timeline rather than
        processing order.
        """
        pending: list[_PendingRevision] = []

        # Phase 1: Fetch and transform all laws (collect revisions)
        for i, law in enumerate(catalog):
            logger.info("[%d/%d] Fetching SR %s: %s", i + 1, len(catalog),
                        law.sr_number, law.title_de or law.title_fr or law.sr_number)
            try:
                revisions = self._collect_revisions(law, languages, latest_only)
                pending.extend(revisions)
            except Exception as e:
                logger.error("Error fetching SR %s: %s", law.sr_number, e)

        # Phase 2: Sort by date (stable sort preserves SR order for same-date entries)
        pending.sort(key=lambda p: p.revision.date)
        logger.info("Collected %d revisions, committing in chronological order...", len(pending))

        # Phase 3: Commit in chronological order, saving state after each
        # successful commit so progress is never lost on failure.
        total_commits = 0
        for i, item in enumerate(pending):
            try:
                if self.committer.commit_revision(item.revision, item.law):
                    total_commits += 1
                    self._mark_processed(item.revision.sr_number, item.revision.date)
                    self._save_state()
            except Exception as e:
                logger.error("Error committing SR %s @ %s: %s",
                             item.revision.sr_number, item.revision.date, e)

            if (i + 1) % 100 == 0:
                logger.info("Committed %d/%d revisions...", total_commits, len(pending))

        return total_commits

    def update(self, limit: int | None = None, languages: list[str] | None = None,
               sr_filter: str | None = None, since_override: date | None = None,
               chronological: bool = True):
        """Incremental update: only fetch laws modified since last_run.

        Uses date comparison to detect new consolidation versions — only
        versions with dateApplicability >= since are considered, and each
        is checked against the processed-state so already-known versions
        are skipped without fetching their text.

        Args:
            limit: Max number of laws to process (None = all)
            languages: Languages to fetch (default: all three)
            sr_filter: Only process laws matching this SR prefix
            since_override: Explicit cutoff date (overrides last_run)
            chronological: If True (default), sort revisions by date before committing
        """
        languages = languages or ["de", "fr", "it"]

        if since_override:
            since = since_override
        else:
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

        logger.info("Found %d laws with new versions since %s", len(catalog), since.isoformat())

        if chronological:
            # Collect all revisions, sort by date, then commit
            pending: list[_PendingRevision] = []
            for i, law in enumerate(catalog):
                logger.info("[%d/%d] Fetching SR %s: %s", i + 1, len(catalog),
                            law.sr_number, law.title_de or law.title_fr or law.sr_number)
                try:
                    revisions = self._collect_revisions(law, languages,
                                                       latest_only=False,
                                                       since_date=since)
                    pending.extend(revisions)
                except Exception as e:
                    logger.error("Error fetching SR %s: %s", law.sr_number, e)

            pending.sort(key=lambda p: p.revision.date)
            logger.info("Collected %d new revisions, committing chronologically...", len(pending))

            total_commits = 0
            for i, item in enumerate(pending):
                try:
                    if self.committer.commit_revision(item.revision, item.law):
                        total_commits += 1
                        self._mark_processed(item.revision.sr_number, item.revision.date)
                        self._save_state()
                except Exception as e:
                    logger.error("Error committing SR %s @ %s: %s",
                                 item.revision.sr_number, item.revision.date, e)

                if (i + 1) % 100 == 0:
                    logger.info("Committed %d/%d revisions...", total_commits, len(pending))
        else:
            total_commits = 0
            skipped_laws = 0
            for i, law in enumerate(catalog):
                logger.info("[%d/%d] SR %s: %s", i + 1, len(catalog), law.sr_number,
                            law.title_de or law.title_fr or law.sr_number)
                try:
                    commits = self._process_law(law, languages, latest_only=False,
                                                since_date=since)
                    total_commits += commits
                    if commits == 0:
                        skipped_laws += 1
                except Exception as e:
                    logger.error("Error processing SR %s: %s", law.sr_number, e)
                self._save_state()

        self.state["last_run"] = date.today().isoformat()
        self._save_state()
        logger.info("Update complete. %d laws checked, %d commits created.",
                    len(catalog), total_commits)
        return total_commits

    def _collect_revisions(self, law: LawEntry, languages: list[str],
                           latest_only: bool,
                           since_date: date | None = None) -> list[_PendingRevision]:
        """Fetch and transform a law's versions without committing.

        Returns a list of _PendingRevision items ready for chronological sorting.
        """
        versions = self.fetcher.fetch_versions(law)

        if not versions:
            revision = self._fetch_current(law, languages)
            if revision and revision.texts:
                if not self._is_processed(law.sr_number, revision.date):
                    return [_PendingRevision(revision=revision, law=law)]
            return []

        if latest_only:
            versions = [versions[-1]]

        if since_date is not None:
            versions = [v for v in versions if v.date_applicable >= since_date]

        pending: list[_PendingRevision] = []
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
            pending.append(_PendingRevision(revision=revision, law=law))

        return pending

    def _process_law(self, law: LawEntry, languages: list[str],
                     latest_only: bool, since_date: date | None = None) -> int:
        """Process a single law. Returns number of commits.

        Args:
            law: The law entry to process
            languages: Languages to fetch
            latest_only: If True, only process the most recent version
            since_date: If set, only process versions with dates >= this date
                        (incremental mode — avoids re-checking old versions)
        """
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

        # Incremental mode: filter to only versions newer than since_date
        if since_date is not None:
            total_before = len(versions)
            versions = [v for v in versions if v.date_applicable >= since_date]
            skipped = total_before - len(versions)
            if skipped:
                logger.debug("Incremental: skipped %d old versions for SR %s (before %s)",
                             skipped, law.sr_number, since_date.isoformat())

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
