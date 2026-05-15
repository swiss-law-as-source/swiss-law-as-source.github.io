"""REST API for querying Swiss law text by SR number and date.

Provides endpoints to retrieve law texts from the git-backed repository,
including historical versions via git history lookup.
"""
from __future__ import annotations

import calendar
import logging
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Default repo path — can be overridden at startup
_REPO_PATH: Path = Path("/home/ubuntu/swiss-law")


def create_app(repo_path: str | Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    global _REPO_PATH
    if repo_path:
        _REPO_PATH = Path(repo_path)

    app = FastAPI(
        title="Swiss Law API",
        description="Query Swiss federal legislation by SR number and date",
        version="1.0.0",
    )

    app.include_router(_build_router())
    return app


class LawResponse(BaseModel):
    """Response model for a law query."""
    sr_number: str
    title: str | None = None
    language: str
    version_date: str | None = None
    content: str
    source: str | None = None


class LawVersionInfo(BaseModel):
    """Summary of an available version."""
    version_date: str
    commit_hash: str
    commit_message: str


class LawVersionsResponse(BaseModel):
    """Response listing all available versions of a law."""
    sr_number: str
    language: str
    versions: list[LawVersionInfo]


def _sr_to_path(sr_number: str, lang: str) -> Path:
    """Convert SR number to file path within the repo.

    Structure: ch/{prefix}/{lang}/{sr_number}.md
    where prefix is the integer part before the first dot.
    """
    parts = sr_number.split(".")
    prefix = parts[0]
    return Path("ch") / prefix / lang / f"{sr_number}.md"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter and body from markdown text."""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}

    body = parts[2].strip()
    return meta, body


def _read_law_file(sr_number: str, lang: str) -> tuple[dict, str] | None:
    """Read the current version of a law file from disk."""
    rel_path = _sr_to_path(sr_number, lang)
    abs_path = _REPO_PATH / rel_path

    if not abs_path.exists():
        return None

    text = abs_path.read_text(encoding="utf-8")
    return _parse_frontmatter(text)


def _read_law_at_date(sr_number: str, lang: str, target_date: date) -> tuple[dict, str] | None:
    """Read a law file as it existed at a specific date using git history.

    Finds the last commit (by author date) on or before target_date that
    modified the file, then retrieves that version.
    """
    rel_path = _sr_to_path(sr_number, lang)

    # List all commits that touched this file with their author dates
    try:
        result = subprocess.run(
            [
                "git", "log",
                "--format=%H|%aI",
                "--", str(rel_path),
            ],
            cwd=str(_REPO_PATH),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    # Find the most recent commit with author date <= target_date
    commit_hash = None
    for line in result.stdout.strip().split("\n"):
        if "|" not in line:
            continue
        h, author_date_str = line.split("|", 1)
        # Parse author date (ISO format, take date part)
        author_date = date.fromisoformat(author_date_str[:10])
        if author_date <= target_date:
            commit_hash = h
            break  # git log is reverse-chronological, first match is most recent

    if not commit_hash:
        return None

    # Get file content at that commit
    try:
        result = subprocess.run(
            ["git", "show", f"{commit_hash}:{rel_path}"],
            cwd=str(_REPO_PATH),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    return _parse_frontmatter(result.stdout)


def _list_versions(sr_number: str, lang: str) -> list[LawVersionInfo]:
    """List all available versions (commits) for a law file."""
    rel_path = _sr_to_path(sr_number, lang)

    try:
        result = subprocess.run(
            [
                "git", "log",
                "--format=%H|%aI|%s",
                "--", str(rel_path),
            ],
            cwd=str(_REPO_PATH),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode != 0:
        return []

    versions = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        commit_hash, author_date, message = parts
        # Extract just the date part from ISO datetime
        version_date = author_date[:10]
        versions.append(LawVersionInfo(
            version_date=version_date,
            commit_hash=commit_hash,
            commit_message=message,
        ))

    return versions


class PublicationEntry(BaseModel):
    """A single publication/revision committed at some date."""
    commit_hash: str
    date: str
    sr_number: str
    title: str
    scope: str  # "federal" | "cantonal"
    languages: list[str]
    paths: list[str]


class PublicationsResponse(BaseModel):
    date_prefix: str
    count: int
    publications: list[PublicationEntry]


# Commit message format from committer.py: "SR <number>: <title> (<date>)"
_COMMIT_RE = re.compile(r"^SR (\S+): (.+?) \(\d{4}-\d{2}-\d{2}\)$")
_DATE_PREFIX_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _parse_date_prefix(prefix: str) -> tuple[date, date]:
    """Expand a YYYY / YYYY-MM / YYYY-MM-DD prefix into [since, until] bounds.

    Raises ValueError on malformed input.
    """
    if not _DATE_PREFIX_RE.match(prefix):
        raise ValueError(f"Invalid date format: {prefix}. Use YYYY, YYYY-MM, or YYYY-MM-DD.")

    parts = prefix.split("-")
    year = int(parts[0])
    if len(parts) == 1:
        return date(year, 1, 1), date(year, 12, 31)
    month = int(parts[1])
    if len(parts) == 2:
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)
    day = int(parts[2])
    return date(year, month, day), date(year, month, day)


def _list_publications(
    since: date,
    until: date,
    lang_filter: str | None = None,
    scope_filter: str | None = None,
    limit: int = 1000,
) -> list[PublicationEntry]:
    """Run `git log --since --until --name-only` and parse into publications.

    Author dates are used (they reflect the legislative timeline; committer
    dates may diverge after rebases). `--diff-filter=AM` keeps only adds and
    modifications — file deletions in a revision would otherwise show up as
    "paths" without a meaningful publication.
    """
    # Explicit ISO timestamps (with Z) bypass git's "approxidate" parser,
    # which for bare YYYY-MM-DD silently applies a fuzz factor and excludes
    # boundary commits. `--until` (and `--since`) both use exclusive
    # comparison against the timestamp, so we anchor at midnight UTC and
    # pad `until` by one day to make the window inclusive on both ends.
    since_iso = f"{since.isoformat()}T00:00:00Z"
    until_iso = f"{(until + timedelta(days=1)).isoformat()}T00:00:00Z"
    # No `--diff-filter`: it compares each commit to its parent, which
    # silently excludes the root commit (no parent). All commits the
    # pipeline writes add or modify markdown — listing every name is fine.
    cmd = [
        "git", "log",
        f"--since={since_iso}",
        f"--until={until_iso}",
        "--format=__COMMIT__%x00%H%x00%aI%x00%s",
        "--name-only",
        "--", "ch/", "kt/",
    ]
    try:
        out = subprocess.run(
            cmd, cwd=_REPO_PATH, capture_output=True, text=True, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("git log failed: %s", e)
        return []

    publications: list[PublicationEntry] = []
    current_hash: str | None = None
    current_date: str | None = None
    current_subject: str | None = None
    current_paths: list[str] = []

    def flush():
        if not current_hash or not current_subject:
            return
        m = _COMMIT_RE.match(current_subject)
        if not m:
            return
        sr_number, title = m.group(1), m.group(2)
        scope = "federal"
        languages: set[str] = set()
        for p in current_paths:
            parts = p.split("/", 3)
            if not parts:
                continue
            if parts[0] == "kt":
                scope = "cantonal"
            if len(parts) >= 3 and parts[2] in ("de", "fr", "it"):
                languages.add(parts[2])
            elif len(parts) >= 3 and parts[0] == "kt" and parts[2] in ("de", "fr", "it"):
                languages.add(parts[2])
        if scope_filter and scope != scope_filter:
            return
        if lang_filter and lang_filter not in languages:
            return
        publications.append(PublicationEntry(
            commit_hash=current_hash,
            date=current_date.split("T", 1)[0] if current_date else "",
            sr_number=sr_number,
            title=title,
            scope=scope,
            languages=sorted(languages),
            paths=list(current_paths),
        ))

    for line in out.splitlines():
        if line.startswith("__COMMIT__\x00"):
            flush()
            _, h, d, s = line.split("\x00", 3)
            current_hash, current_date, current_subject = h, d, s
            current_paths = []
            if len(publications) >= limit:
                break
        elif line.strip():
            current_paths.append(line)
    else:
        flush()

    return publications[:limit]


def _list_early_publications(
    since: date,
    until: date,
    lang_filter: str | None = None,
    scope_filter: str | None = None,
    limit: int = 1000,
) -> list[PublicationEntry]:
    """Find laws whose `original_publication_date` (frontmatter) falls in window.

    Pre-1970 publication dates can't be represented in git commit timestamps,
    so the pipeline records them in the law's YAML frontmatter as a
    permanent `original_publication_date: YYYY-MM-DD` marker. This walker
    scans the working tree for that marker and surfaces each matching law
    as a synthetic publication entry. The reported `commit_hash` is the
    git commit that first introduced the file (the earliest consolidation
    we actually have).
    """
    laws: dict[tuple[str, str], dict] = {}

    roots: list[tuple[str, Path]] = []
    if scope_filter in (None, "federal"):
        roots.append(("federal", _REPO_PATH / "ch"))
    if scope_filter in (None, "cantonal"):
        roots.append(("cantonal", _REPO_PATH / "kt"))

    for scope, root in roots:
        if not root.exists():
            continue
        for md_file in root.rglob("*.md"):
            # Cheap byte-level prefilter before YAML parsing.
            try:
                head = md_file.read_text(encoding="utf-8")[:2048]
            except OSError:
                continue
            if "original_publication_date" not in head:
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _ = _parse_frontmatter(text)
            opd_raw = meta.get("original_publication_date")
            if not opd_raw:
                continue
            try:
                opd = date.fromisoformat(str(opd_raw))
            except ValueError:
                continue
            if not (since <= opd <= until):
                continue
            try:
                rel_parts = md_file.relative_to(_REPO_PATH).parts
            except ValueError:
                continue
            if len(rel_parts) < 4:
                continue
            lang = rel_parts[2]
            if lang not in ("de", "fr", "it"):
                continue
            if lang_filter and lang_filter != lang:
                continue
            sr_number = str(meta.get("sr_number") or md_file.stem)
            key = (sr_number, scope)
            entry = laws.setdefault(key, {
                "sr_number": sr_number,
                "title": str(meta.get("title") or ""),
                "scope": scope,
                "date": opd.isoformat(),
                "languages": set(),
                "paths": set(),
            })
            entry["languages"].add(lang)
            entry["paths"].add("/".join(rel_parts))

    results: list[PublicationEntry] = []
    for entry in laws.values():
        if len(results) >= limit:
            break
        # The earliest git commit that introduced any file of this law —
        # that's where the actual text body lives, even though the
        # publication date predates it.
        first_path = sorted(entry["paths"])[0]
        commit_hash = ""
        try:
            out = subprocess.run(
                ["git", "log", "--diff-filter=A", "--reverse",
                 "--format=%H", "-1", "--", first_path],
                cwd=_REPO_PATH, capture_output=True, text=True, check=False,
            ).stdout.strip()
            commit_hash = out
        except (OSError, subprocess.SubprocessError):
            pass
        results.append(PublicationEntry(
            commit_hash=commit_hash,
            date=entry["date"],
            sr_number=entry["sr_number"],
            title=entry["title"],
            scope=entry["scope"],
            languages=sorted(entry["languages"]),
            paths=sorted(entry["paths"]),
        ))

    results.sort(key=lambda p: (p.date, p.sr_number))
    return results


def _build_router():
    """Build the API router with all endpoints."""
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/v1", tags=["laws"])

    @router.get("/laws/{sr_number}", response_model=LawResponse)
    async def get_law(
        sr_number: str,
        lang: str = Query(default="de", pattern="^(de|fr|it|en)$",
                          description="Language code"),
        date_str: Optional[str] = Query(
            default=None, alias="date",
            description="Version date (YYYY-MM-DD). Returns version valid at this date.",
        ),
    ):
        """Retrieve law text by SR number, optionally at a specific date.

        - Without `date`: returns the latest version on disk.
        - With `date`: finds the version from git history valid at that date.
        """
        if date_str:
            try:
                target_date = date.fromisoformat(date_str)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid date format: {date_str}. Use YYYY-MM-DD.",
                )
            result = _read_law_at_date(sr_number, lang, target_date)
            if result is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No version of SR {sr_number} ({lang}) found at or before {date_str}.",
                )
        else:
            result = _read_law_file(sr_number, lang)
            if result is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Law SR {sr_number} ({lang}) not found.",
                )

        meta, body = result
        return LawResponse(
            sr_number=sr_number,
            title=meta.get("title"),
            language=lang,
            version_date=str(meta.get("version_date", "")),
            content=body,
            source=meta.get("source"),
        )

    @router.get("/laws/{sr_number}/versions", response_model=LawVersionsResponse)
    async def get_law_versions(
        sr_number: str,
        lang: str = Query(default="de", pattern="^(de|fr|it|en)$",
                          description="Language code"),
    ):
        """List all available versions (from git history) for a law."""
        # Verify the file exists
        rel_path = _sr_to_path(sr_number, lang)
        if not (_REPO_PATH / rel_path).exists():
            raise HTTPException(
                status_code=404,
                detail=f"Law SR {sr_number} ({lang}) not found.",
            )

        versions = _list_versions(sr_number, lang)
        return LawVersionsResponse(
            sr_number=sr_number,
            language=lang,
            versions=versions,
        )

    @router.get("/publications", response_model=PublicationsResponse)
    async def get_publications(
        date_prefix: str = Query(
            ..., alias="date",
            description="YYYY, YYYY-MM, or YYYY-MM-DD",
        ),
        lang: Optional[str] = Query(
            default=None, pattern="^(de|fr|it)$",
            description="Filter to publications touching this language",
        ),
        scope: Optional[str] = Query(
            default=None, pattern="^(federal|cantonal)$",
            description="Filter by federal (ch/) or cantonal (kt/)",
        ),
        limit: int = Query(default=1000, ge=1, le=10000),
    ):
        """List publications/revisions committed in a date window.

        Backed by `git log` over `ch/` and `kt/`. One entry per commit;
        languages and scope are inferred from the paths the commit touched.
        """
        try:
            since, until = _parse_date_prefix(date_prefix)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        publications = _list_publications(
            since=since, until=until,
            lang_filter=lang, scope_filter=scope,
            limit=limit,
        )

        # Pre-1970 publication dates live only in markdown frontmatter
        # (git can't store earlier timestamps). Merge them in when the
        # window overlaps that era.
        epoch = date(1970, 1, 1)
        if since < epoch:
            early_until = min(until, epoch - timedelta(days=1))
            early = _list_early_publications(
                since=since, until=early_until,
                lang_filter=lang, scope_filter=scope,
                limit=limit,
            )
            if early:
                publications = early + publications
                publications.sort(key=lambda p: p.date, reverse=True)
                publications = publications[:limit]

        return PublicationsResponse(
            date_prefix=date_prefix,
            count=len(publications),
            publications=publications,
        )

    @router.get("/publications/today", response_model=PublicationsResponse)
    async def get_publications_today(
        lang: Optional[str] = Query(default=None, pattern="^(de|fr|it)$"),
        scope: Optional[str] = Query(default=None, pattern="^(federal|cantonal)$"),
        limit: int = Query(default=1000, ge=1, le=10000),
    ):
        """Shortcut for publications committed today."""
        today = date.today()
        publications = _list_publications(
            since=today, until=today,
            lang_filter=lang, scope_filter=scope,
            limit=limit,
        )
        return PublicationsResponse(
            date_prefix=today.isoformat(),
            count=len(publications),
            publications=publications,
        )

    @router.get("/search")
    async def search_laws(
        q: str = Query(description="Search query (SR number prefix or title keyword)"),
        lang: str = Query(default="de", pattern="^(de|fr|it)$"),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        """Search federal + cantonal laws by SR/number prefix or title keyword."""
        results = []
        roots = [r for r in (_REPO_PATH / "ch", _REPO_PATH / "kt") if r.exists()]
        if not roots:
            return {"results": [], "total": 0}

        for root in roots:
            for prefix_dir in sorted(root.iterdir()):
                if not prefix_dir.is_dir():
                    continue
                lang_dir = prefix_dir / lang
                if not lang_dir.is_dir():
                    continue
                for md_file in sorted(lang_dir.glob("*.md")):
                    sr = md_file.stem
                    if q and not sr.startswith(q):
                        text = md_file.read_text(encoding="utf-8")
                        meta, _ = _parse_frontmatter(text)
                        title = meta.get("title", "")
                        if q.lower() not in title.lower():
                            continue
                        results.append({"sr_number": sr, "title": title, "language": lang})
                    else:
                        text = md_file.read_text(encoding="utf-8")
                        meta, _ = _parse_frontmatter(text)
                        results.append({
                            "sr_number": sr,
                            "title": meta.get("title", ""),
                            "language": lang,
                        })

                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        return {"results": results, "total": len(results)}

    @router.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "ok", "repo": str(_REPO_PATH)}

    return router


# Default app instance for uvicorn
app = create_app()
