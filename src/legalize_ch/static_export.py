"""Static JSON export of the publications API for GitHub Pages.

Renders the same shape that the FastAPI `/api/v1/publications` endpoint
returns, but as flat files: one per year that has at least one
publication, plus an index and a `today.json` snapshot.

Three sources are merged into the year files, deduped by
``(sr_number, date, scope)`` so the same revision never appears twice:

1. **Git log over `ch/` + `kt/`** — works for repos bootstrapped
   chronologically (one commit per consolidation, dates set on the
   commit). See ``api._list_publications``.
2. **Markdown frontmatter `version_date`** — works for repos
   bootstrapped from a snapshot (one big commit, but each markdown
   carries its own ``version_date`` and ``sr_number`` in YAML).
3. **Markdown frontmatter `original_publication_date`** — laws whose
   original publication predates 1970 (git's epoch) carry the date in
   frontmatter; see ``api._list_early_publications``.

Lives separately from `api.py` so the FastAPI server has no runtime
dependency on the exporter, and the exporter has no Web framework
dependency.
"""
from __future__ import annotations

import json
import logging
import subprocess
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from . import api as _api

logger = logging.getLogger(__name__)

# Generous bounds for the wide git-log sweep — far past anything realistic.
_MAX_DATE = date(2200, 12, 31)
_EARLIEST_GIT_DATE = date(1970, 1, 1)
_HUGE_LIMIT = 10**9


def _publication_dict(entry: _api.PublicationEntry) -> dict:
    """Serialise a PublicationEntry as a plain dict (no Pydantic v1/v2 fork)."""
    return {
        "commit_hash": entry.commit_hash,
        "date": entry.date,
        "sr_number": entry.sr_number,
        "title": entry.title,
        "scope": entry.scope,
        "languages": list(entry.languages),
        "paths": list(entry.paths),
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _list_publications_from_frontmatter(
    repo_path: Path,
) -> list[_api.PublicationEntry]:
    """Build publication entries from working-tree markdown frontmatter.

    Each markdown carries its consolidation's ``version_date`` in YAML.
    Walking the working tree gives us the LATEST version of every law's
    text — enough to populate the API after a snapshot bootstrap (where
    the per-revision git history was discarded).

    For chronologically-bootstrapped repos the same entries also appear
    in git log; the merge step in ``export_publications`` dedupes by
    ``(sr_number, date, scope)``.
    """
    # Aggregate per (sr_number, version_date, scope), collecting all lang files.
    laws: dict[tuple[str, str, str], dict] = {}

    roots: list[tuple[str, Path]] = [
        ("federal", repo_path / "ch"),
        ("cantonal", repo_path / "kt"),
    ]
    for scope, root in roots:
        if not root.exists():
            continue
        for md_file in root.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _ = _api._parse_frontmatter(text)
            sr_number = str(meta.get("sr_number") or "").strip()
            version_date = str(meta.get("version_date") or "").strip()
            if not sr_number or not version_date:
                continue
            try:
                rel_parts = md_file.relative_to(repo_path).parts
            except ValueError:
                continue
            if len(rel_parts) < 4:
                continue
            lang = rel_parts[2]
            if lang not in ("de", "fr", "it"):
                continue
            title = str(meta.get("title") or "").strip()
            key = (sr_number, version_date, scope)
            entry = laws.setdefault(key, {
                "sr_number": sr_number,
                "title": title,
                "date": version_date,
                "scope": scope,
                "languages": set(),
                "paths": set(),
            })
            entry["languages"].add(lang)
            entry["paths"].add("/".join(rel_parts))
            # Prefer the most informative title across language files.
            if title and len(title) > len(entry["title"]):
                entry["title"] = title

    results: list[_api.PublicationEntry] = []
    for entry in laws.values():
        # Find the introducing commit for this law (one cheap git call).
        first_path = sorted(entry["paths"])[0]
        commit_hash = ""
        try:
            out = subprocess.run(
                ["git", "log", "--diff-filter=A", "--reverse",
                 "--format=%H", "-1", "--", first_path],
                cwd=repo_path, capture_output=True, text=True, check=False,
            ).stdout.strip()
            commit_hash = out
        except (OSError, subprocess.SubprocessError):
            pass
        results.append(_api.PublicationEntry(
            commit_hash=commit_hash,
            date=entry["date"],
            sr_number=entry["sr_number"],
            title=entry["title"],
            scope=entry["scope"],
            languages=sorted(entry["languages"]),
            paths=sorted(entry["paths"]),
        ))
    return results


def export_publications(repo_path: Path, output_dir: Path) -> dict:
    """Write per-year JSON exports of all publications.

    Produces under ``output_dir``:
      - ``index.json`` — metadata + list of years that have a file
      - ``today.json`` — publications committed today (often empty)
      - ``{year}.json`` — for each year with at least one publication

    Merges git-log, working-tree frontmatter, and pre-1970 frontmatter
    markers; dedupes by ``(sr_number, date, scope)``, preferring entries
    that carry a non-empty ``commit_hash``.
    """
    # Point the api helpers at the target repo.
    _api._REPO_PATH = Path(repo_path)
    repo_path = Path(repo_path)
    output_dir = Path(output_dir)

    git_pubs = _api._list_publications(
        since=_EARLIEST_GIT_DATE,
        until=_MAX_DATE,
        limit=_HUGE_LIMIT,
    )
    early_pubs = _api._list_early_publications(
        since=date(1, 1, 1),
        until=_EARLIEST_GIT_DATE,
        limit=_HUGE_LIMIT,
    )
    frontmatter_pubs = _list_publications_from_frontmatter(repo_path)

    # Dedupe by (sr_number, date, scope). Prefer the entry with a populated
    # commit_hash; among equals, prefer the one with more paths/languages.
    merged: dict[tuple[str, str, str], _api.PublicationEntry] = {}
    for source in (early_pubs, frontmatter_pubs, git_pubs):
        for entry in source:
            key = (entry.sr_number, entry.date, entry.scope)
            existing = merged.get(key)
            if existing is None:
                merged[key] = entry
                continue
            # Prefer entry with commit_hash; tie-break on richer paths/languages.
            cur_score = (
                bool(existing.commit_hash),
                len(existing.languages),
                len(existing.paths),
            )
            new_score = (
                bool(entry.commit_hash),
                len(entry.languages),
                len(entry.paths),
            )
            if new_score > cur_score:
                merged[key] = entry

    by_year: dict[int, list[_api.PublicationEntry]] = defaultdict(list)
    for entry in merged.values():
        by_year[int(entry.date[:4])].append(entry)

    written_years: list[int] = []
    for year, pubs in sorted(by_year.items()):
        pubs.sort(key=lambda p: (p.date, p.sr_number))
        payload = {
            "date_prefix": str(year),
            "count": len(pubs),
            "publications": [_publication_dict(p) for p in pubs],
        }
        _write_json(output_dir / f"{year}.json", payload)
        written_years.append(year)

    today = date.today()
    today_pubs = _api._list_publications(
        since=today, until=today, limit=_HUGE_LIMIT,
    )
    _write_json(output_dir / "today.json", {
        "date_prefix": today.isoformat(),
        "count": len(today_pubs),
        "publications": [_publication_dict(p) for p in today_pubs],
    })

    generated_at = datetime.now(timezone.utc).isoformat()
    total = sum(len(pubs) for pubs in by_year.values())
    _write_json(output_dir / "index.json", {
        "generated_at": generated_at,
        "years": written_years,
        "total_publications": total,
        "earliest_year": written_years[0] if written_years else None,
        "latest_year": written_years[-1] if written_years else None,
    })

    logger.info(
        "Exported %d publications across %d years to %s",
        total, len(written_years), output_dir,
    )
    return {
        "years": len(written_years),
        "publications": total,
        "output_dir": str(output_dir),
    }
