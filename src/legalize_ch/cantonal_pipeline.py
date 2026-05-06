"""Cantonal pipeline orchestrator — fetch, transform, commit cantonal laws."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import NamedTuple

from .cantonal import (
    CantonalFetcher,
    CantonalLawEntry,
    CantonalLawText,
    ALL_CANTONS,
    LEXWORK_CANTONS,
    LEXFIND_ONLY_CANTONS,
    DEDICATED_FETCHER_CANTONS,
    canton_to_path,
    cantonal_law_to_markdown,
)
from .committer import GitCommitter
from .models import LawRevision

logger = logging.getLogger(__name__)

CANTONAL_STATE_FILE = "data/cantonal_pipeline_state.json"


class _PendingCantonalRevision(NamedTuple):
    """A cantonal revision ready to be committed."""
    canton: str
    systematic_number: str
    title: str
    date_val: date
    texts: dict[str, str]  # lang -> markdown


class CantonalPipeline:
    """Orchestrates the cantonal fetch -> transform -> commit pipeline.

    Mirrors the federal Pipeline class but works with cantonal data sources
    (LexWork, LexFind, ZHLex).
    """

    def __init__(self, repo_path: str | Path, rate_limit: float = 1.0):
        self.repo_path = Path(repo_path)
        self.fetcher = CantonalFetcher(rate_limit=rate_limit)
        self.committer = GitCommitter(repo_path)
        self.state_file = self.repo_path / CANTONAL_STATE_FILE
        self.state: dict = self._load_state()

    def _load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"processed": {}, "last_run": None}

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, default=str))

    def _state_key(self, canton: str, number: str, lang: str) -> str:
        return f"{canton}/{number}@{lang}"

    def _is_processed(self, canton: str, number: str, lang: str) -> bool:
        key = self._state_key(canton, number, lang)
        return key in self.state.get("processed", {})

    def _mark_processed(self, canton: str, number: str, lang: str):
        key = self._state_key(canton, number, lang)
        self.state.setdefault("processed", {})[key] = True

    def run(
        self,
        cantons: list[str] | None = None,
        languages: list[str] | None = None,
        limit: int | None = None,
    ) -> int:
        """Run the full cantonal pipeline for specified cantons.

        Args:
            cantons: List of canton abbreviations (default: all 26).
            languages: Languages to fetch (default: ["de"]).
            limit: Max laws per canton (None = all).

        Returns:
            Total number of commits created.
        """
        cantons = cantons or list(ALL_CANTONS)
        languages = languages or ["de"]
        total_commits = 0

        for canton in cantons:
            canton = canton.lower()
            if canton not in ALL_CANTONS:
                logger.warning("Unknown canton '%s', skipping", canton)
                continue

            logger.info("=== Canton %s ===", canton.upper())
            commits = self._process_canton(canton, languages, limit)
            total_commits += commits
            logger.info("Canton %s: %d commits", canton.upper(), commits)

        self.state["last_run"] = date.today().isoformat()
        self._save_state()
        logger.info("Cantonal pipeline complete. Total commits: %d", total_commits)
        return total_commits

    def update(
        self,
        cantons: list[str] | None = None,
        languages: list[str] | None = None,
        limit: int | None = None,
    ) -> int:
        """Incremental cantonal update — re-fetch catalogs, skip already-processed laws.

        For cantonal laws we don't have date-based incremental detection (unlike
        Fedlex SPARQL), so we re-scan the catalog and skip entries already in state.

        Args:
            cantons: Canton abbreviations (default: all 26).
            languages: Languages to fetch (default: ["de"]).
            limit: Max laws per canton (None = all).

        Returns:
            Total number of new commits created.
        """
        cantons = cantons or list(ALL_CANTONS)
        languages = languages or ["de"]
        total_commits = 0

        for canton in cantons:
            canton = canton.lower()
            if canton not in ALL_CANTONS:
                logger.warning("Unknown canton '%s', skipping", canton)
                continue

            logger.info("=== Updating canton %s ===", canton.upper())
            commits = self._process_canton(canton, languages, limit)
            total_commits += commits
            if commits:
                logger.info("Canton %s: %d new commits", canton.upper(), commits)
            else:
                logger.debug("Canton %s: up to date", canton.upper())

        self.state["last_run"] = date.today().isoformat()
        self._save_state()
        logger.info("Cantonal update complete. Total new commits: %d", total_commits)
        return total_commits

    def _process_canton(
        self, canton: str, languages: list[str], limit: int | None
    ) -> int:
        """Fetch and commit all laws for a single canton."""
        catalog = self.fetcher.fetch_lexwork_catalog(canton, languages[0])
        if not catalog:
            logger.info("Canton %s: empty catalog, skipping", canton.upper())
            return 0

        if limit:
            catalog = catalog[:limit]

        logger.info("Canton %s: processing %d laws in %s",
                     canton.upper(), len(catalog), languages)

        commits = 0
        for i, entry in enumerate(catalog):
            for lang in languages:
                if self._is_processed(canton, entry.systematic_number, lang):
                    continue

                text = self.fetcher.fetch_law_text(
                    canton, entry.systematic_number, lang,
                    lexfind_id=entry.lexfind_id,
                )
                if not text:
                    continue

                md = cantonal_law_to_markdown(text)
                rel_path = canton_to_path(canton, entry.systematic_number, lang)
                abs_path = self.repo_path / rel_path
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(md, encoding="utf-8")

                # Commit with the version date if available, else today
                commit_date = text.version_date or date.today()
                title = text.title or entry.title or entry.systematic_number
                revision = LawRevision(
                    sr_number=f"{canton.upper()}/{entry.systematic_number}",
                    date=commit_date,
                    title_de=title if lang == "de" else "",
                    title_fr=title if lang == "fr" else "",
                    title_it=title if lang == "it" else "",
                    texts={},  # We handle file writing ourselves
                    message=(
                        f"{canton.upper()} {entry.systematic_number}: "
                        f"{title} ({commit_date.isoformat()})"
                    ),
                )

                # Stage and commit the file directly
                self.committer._run_git("add", rel_path)
                status = self.committer._run_git("diff", "--cached", "--quiet")
                if status.returncode != 0:
                    env = self.committer._date_env(commit_date)
                    env["GIT_AUTHOR_NAME"] = self.committer.author_name
                    env["GIT_AUTHOR_EMAIL"] = self.committer.author_email
                    env["GIT_COMMITTER_NAME"] = self.committer.author_name
                    env["GIT_COMMITTER_EMAIL"] = self.committer.author_email
                    result = self.committer._run_git(
                        "commit", "-m", revision.message, env=env,
                    )
                    if result.returncode == 0:
                        commits += 1
                        logger.info("Committed: %s", revision.message)
                    else:
                        logger.error("Commit failed for %s/%s: %s",
                                     canton, entry.systematic_number, result.stderr)

                self._mark_processed(canton, entry.systematic_number, lang)

            if (i + 1) % 10 == 0:
                logger.info("  [%d/%d] processed...", i + 1, len(catalog))
                self._save_state()

        self._save_state()
        return commits
