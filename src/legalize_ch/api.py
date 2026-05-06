"""REST API for querying Swiss law text by SR number and date.

Provides endpoints to retrieve law texts from the git-backed repository,
including historical versions via git history lookup.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
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

    @router.get("/search")
    async def search_laws(
        q: str = Query(description="Search query (SR number prefix or title keyword)"),
        lang: str = Query(default="de", pattern="^(de|fr|it|en)$"),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        """Search laws by SR number prefix or title keyword."""
        results = []
        ch_dir = _REPO_PATH / "ch"

        if not ch_dir.exists():
            return {"results": [], "total": 0}

        # Walk the directory structure
        for prefix_dir in sorted(ch_dir.iterdir()):
            if not prefix_dir.is_dir():
                continue
            lang_dir = prefix_dir / lang
            if not lang_dir.is_dir():
                continue
            for md_file in sorted(lang_dir.glob("*.md")):
                sr = md_file.stem
                # Match by SR prefix or read title for keyword match
                if q and not sr.startswith(q):
                    # Check title
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

        return {"results": results, "total": len(results)}

    @router.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "ok", "repo": str(_REPO_PATH)}

    return router


# Default app instance for uvicorn
app = create_app()
