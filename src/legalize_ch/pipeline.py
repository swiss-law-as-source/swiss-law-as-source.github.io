"""Pipeline orchestrator — fetch, transform, commit Swiss law."""
from __future__ import annotations

import json
import logging
import os
import signal
from datetime import date
from pathlib import Path
from typing import NamedTuple

from .committer import GitCommitter
from .fetcher import FedlexFetcher
from .models import LawEntry, LawRevision, LawVersion
from .transformer import law_to_markdown

logger = logging.getLogger(__name__)

STATE_FILE = "data/pipeline_state.json"
PID_FILE = "pipeline.pid"


class PipelineAlreadyRunningError(RuntimeError):
    """Raised when another pipeline instance is already running."""


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is still running."""
    try:
        os.kill(pid, 0)  # signal 0 = existence check, no signal sent
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack permission to signal it
        return True


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
        self.pid_file = self.repo_path / PID_FILE
        self.state: dict = self._load_state()
        self._owns_pid = False

    def _load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"processed": {}, "last_run": None}

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, default=str))

    # ------------------------------------------------------------------
    # PID-file locking
    # ------------------------------------------------------------------

    def _acquire_lock(self) -> None:
        """Write the current PID to the lock file.

        Raises ``PipelineAlreadyRunningError`` if another live pipeline
        process already holds the lock.  Stale PID files (where the
        recorded process is no longer running) are automatically cleaned up.
        """
        if self.pid_file.exists():
            try:
                existing_pid = int(self.pid_file.read_text().strip())
            except (ValueError, OSError):
                existing_pid = None

            if existing_pid is not None and _is_pid_alive(existing_pid):
                raise PipelineAlreadyRunningError(
                    f"Another pipeline is already running (PID {existing_pid}). "
                    f"If this is stale, remove {self.pid_file}"
                )
            else:
                logger.warning(
                    "Stale PID file found (PID %s is not running) — overwriting.",
                    existing_pid,
                )

        self.pid_file.write_text(str(os.getpid()))
        self._owns_pid = True
        logger.debug("Acquired pipeline lock (PID %d)", os.getpid())

    def _release_lock(self) -> None:
        """Remove the PID lock file if we own it."""
        if self._owns_pid and self.pid_file.exists():
            try:
                # Only remove if it's still our PID (guard against races)
                current = int(self.pid_file.read_text().strip())
                if current == os.getpid():
                    self.pid_file.unlink()
                    logger.debug("Released pipeline lock (PID %d)", os.getpid())
            except (ValueError, OSError):
                self.pid_file.unlink(missing_ok=True)
            self._owns_pid = False

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

        self._acquire_lock()
        try:
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
        finally:
            self._release_lock()

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

        self._acquire_lock()
        try:
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
        finally:
            self._release_lock()

    @staticmethod
    def _version_date(version: LawVersion) -> date:
        """Pick the most representative date for a consolidation.

        Prefers `dateDocument` (when the consolidation was published) over
        `dateApplicability` (when it took legal effect) if the former is
        earlier — the publication date is closer to "version date" as it
        appears in the law's own text.
        """
        if version.date_document and version.date_document < version.date_applicable:
            return version.date_document
        return version.date_applicable

    def _collect_revisions(self, law: LawEntry, languages: list[str],
                           latest_only: bool,
                           since_date: date | None = None) -> list[_PendingRevision]:
        """Fetch and transform a law's versions without committing.

        Returns a list of _PendingRevision items ready for chronological sorting.

        When the law's original document date predates the earliest Fedlex
        consolidation, two things happen:

        1. **Every revision's markdown frontmatter** gets an
           ``original_publication_date: YYYY-MM-DD`` field. This makes the
           original publication queryable via the REST API even for pre-1970
           dates that git itself cannot represent as a commit timestamp.

        2. **For post-1970 publication dates**, a synthetic publication
           revision is *also* prepended so the git log starts at the law's
           actual beginning. Pre-1970 dates skip this — git's ``time_t``
           cannot go negative — but the frontmatter marker still surfaces
           them through the API.
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
            versions = [v for v in versions
                        if self._version_date(v) >= since_date]

        # If the law's abstract dateDocument is earlier than its earliest
        # consolidation, propagate that date into every revision's
        # frontmatter as `original_publication_date`. This is the durable
        # marker the API scans for pre-1970 lookups, and it also documents
        # post-1970 publications consistently across all revisions.
        earliest_v_date = self._version_date(versions[0]) if versions else None
        early_pub_marker: date | None = (
            law.date_document
            if (law.date_document
                and earliest_v_date
                and law.date_document < earliest_v_date)
            else None
        )

        pending: list[_PendingRevision] = []
        first_consolidation_texts: dict[str, str] | None = None
        first_consolidation_date: date | None = None

        for version in versions:
            v_date = self._version_date(version)
            if self._is_processed(law.sr_number, v_date):
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
                        version_date=v_date,
                        abbreviation=abbr,
                    )
                    if early_pub_marker:
                        md = self._inject_original_publication_date(
                            md, early_pub_marker,
                        )
                    texts[lang] = md

            if not texts:
                continue

            if first_consolidation_texts is None:
                first_consolidation_texts = texts
                first_consolidation_date = v_date

            revision = LawRevision(
                sr_number=law.sr_number,
                date=v_date,
                title_de=law.title_de,
                title_fr=law.title_fr,
                title_it=law.title_it,
                texts=texts,
            )
            pending.append(_PendingRevision(revision=revision, law=law))

        # Prepend a synthetic publication commit when the date is post-1970
        # (git can't store earlier commit timestamps). The frontmatter
        # marker added above already makes pre-1970 publications queryable
        # via the API.
        if (
            not latest_only
            and early_pub_marker
            and first_consolidation_texts
            and first_consolidation_date
            and early_pub_marker >= date(1970, 1, 1)
            and not self._is_processed(law.sr_number, early_pub_marker)
            and (since_date is None or early_pub_marker >= since_date)
        ):
            pub_texts = {
                lang: self._mark_original_publication(md, early_pub_marker)
                for lang, md in first_consolidation_texts.items()
            }
            pub_revision = LawRevision(
                sr_number=law.sr_number,
                date=early_pub_marker,
                title_de=law.title_de,
                title_fr=law.title_fr,
                title_it=law.title_it,
                texts=pub_texts,
            )
            pending.insert(0, _PendingRevision(revision=pub_revision, law=law))

        return pending

    @staticmethod
    def _inject_original_publication_date(markdown: str, pub_date: date) -> str:
        """Add `original_publication_date: YYYY-MM-DD` to YAML frontmatter.

        Idempotent — does nothing if the field is already present. Preserves
        all other frontmatter fields, including `version_date` (which still
        refers to this consolidation's date, not the original publication).
        Returns the original markdown unchanged if parsing fails — losing
        the marker is preferable to corrupting the file.
        """
        if not markdown.startswith("---\n"):
            return markdown
        end = markdown.find("\n---\n", 4)
        if end == -1:
            return markdown
        frontmatter = markdown[4:end]
        body = markdown[end + 5:]
        lines = frontmatter.splitlines()
        if any(line.startswith("original_publication_date:") for line in lines):
            return markdown
        lines.append(f"original_publication_date: '{pub_date.isoformat()}'")
        return "---\n" + "\n".join(lines) + "\n---\n" + body

    @staticmethod
    def _mark_original_publication(markdown: str, pub_date: date) -> str:
        """Rewrite a markdown's YAML frontmatter for the publication commit.

        Sets `version_date` to `pub_date` and adds `original_publication: true`
        so the synthetic commit is auditable. If parsing fails the original
        text is returned unchanged — losing the marker is preferable to
        corrupting the file.
        """
        if not markdown.startswith("---\n"):
            return markdown
        end = markdown.find("\n---\n", 4)
        if end == -1:
            return markdown
        frontmatter = markdown[4:end]
        body = markdown[end + 5:]
        iso = pub_date.isoformat()
        new_lines: list[str] = []
        saw_version_date = False
        for line in frontmatter.splitlines():
            if line.startswith("version_date:"):
                new_lines.append(f"version_date: '{iso}'")
                saw_version_date = True
            else:
                new_lines.append(line)
        if not saw_version_date:
            new_lines.append(f"version_date: '{iso}'")
        new_lines.append("original_publication: true")
        return "---\n" + "\n".join(new_lines) + "\n---\n" + body

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
            versions = [v for v in versions if self._version_date(v) >= since_date]
            skipped = total_before - len(versions)
            if skipped:
                logger.debug("Incremental: skipped %d old versions for SR %s (before %s)",
                             skipped, law.sr_number, since_date.isoformat())

        earliest_v_date = self._version_date(versions[0]) if versions else None
        early_pub_marker: date | None = (
            law.date_document
            if (law.date_document
                and earliest_v_date
                and law.date_document < earliest_v_date)
            else None
        )

        commits = 0
        for version in versions:
            v_date = self._version_date(version)
            if self._is_processed(law.sr_number, v_date):
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
                        version_date=v_date,
                        abbreviation=abbr,
                    )
                    if early_pub_marker:
                        md = self._inject_original_publication_date(
                            md, early_pub_marker,
                        )
                    texts[lang] = md

            if not texts:
                continue

            revision = LawRevision(
                sr_number=law.sr_number,
                date=v_date,
                title_de=law.title_de,
                title_fr=law.title_fr,
                title_it=law.title_it,
                texts=texts,
            )

            if self.committer.commit_revision(revision, law):
                commits += 1
                self._mark_processed(law.sr_number, v_date)

        return commits

    def _fetch_current(self, law: LawEntry, languages: list[str]) -> LawRevision | None:
        """Fetch current text when no consolidation versions exist.

        Attempts to retrieve real content from the abstract URI before
        falling back to a clearly-marked stub (``stub: true`` in frontmatter).
        """
        d = law.date_in_force or law.date_document or date.today()
        texts = {}

        for lang in languages:
            title = self._get_title(law, lang)
            abbr = self._get_abbreviation(law, lang)

            # Try to fetch real content from the abstract URI
            abstract_text = self.fetcher.fetch_abstract_text(law, lang)
            xml_content = ""
            html_content = ""
            if abstract_text:
                xml_content = abstract_text.xml_content
                html_content = abstract_text.html_content
                if abstract_text.title and not title:
                    title = abstract_text.title

            if not title:
                continue

            md = law_to_markdown(
                sr_number=law.sr_number,
                title=title,
                xml_content=xml_content,
                html_content=html_content,
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
