"""Seed `data/pipeline_state.json` from existing markdown frontmatter.

Used after bootstrapping the repo from a snapshot of prior data: walks
every `ch/**/{de,fr,it}/*.md` and `kt/**/{de,fr,it}/*.md`, parses the
YAML frontmatter, and records each `(sr_number, version_date)` pair as
already processed. The next `legalize-ch update` then only fetches
versions Fedlex added after the seeded `last_run`.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

STATE_FILE = "data/pipeline_state.json"


def reindex(repo_path: Path, buffer_days: int = 30) -> dict:
    """Walk markdown frontmatter and write pipeline state.

    Args:
        repo_path: Path to the repo root containing `ch/` and/or `kt/`.
        buffer_days: How many days before today to set `last_run`. The
            buffer lets the first `legalize-ch update` re-check the
            recent window, catching any consolidations Fedlex published
            after we took the snapshot.

    Returns a small dict summarising what was written.
    """
    repo_path = Path(repo_path)
    processed: dict[str, bool] = {}
    skipped = 0

    for root_name in ("ch", "kt"):
        root = repo_path / root_name
        if not root.exists():
            continue
        for md_file in root.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                skipped += 1
                continue
            if not text.startswith("---"):
                skipped += 1
                continue
            parts = text.split("---", 2)
            if len(parts) < 3:
                skipped += 1
                continue
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                skipped += 1
                continue
            sr_number = str(meta.get("sr_number") or "").strip()
            version_date = str(meta.get("version_date") or "").strip()
            if not sr_number or not version_date:
                skipped += 1
                continue
            processed[f"{sr_number}@{version_date}"] = True

    last_run = (date.today() - timedelta(days=buffer_days)).isoformat()
    state = {"processed": processed, "last_run": last_run}

    state_path = repo_path / STATE_FILE
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))

    logger.info(
        "Reindexed %d (sr, version_date) entries (%d skipped); last_run=%s",
        len(processed), skipped, last_run,
    )
    return {
        "processed_count": len(processed),
        "skipped": skipped,
        "last_run": last_run,
        "state_file": str(state_path),
    }
