"""Incremental canton rollout — prioritized by data availability.

Manages the progressive rollout of cantonal law fetching across all 26 Swiss
cantons. Cantons are grouped into priority tiers based on data source quality:

  Tier 1 (DEDICATED): Cantons with dedicated API fetchers (e.g. Zürich/ZHLex)
  Tier 2 (LEXWORK):   Cantons with LexWork JSON API (direct, structured access)
  Tier 3 (LEXFIND):   Cantons accessible only via LexFind (less structured)

Within each tier, cantons are ordered by estimated catalog size (larger cantons
first) to maximize coverage quickly.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .cantonal import (
    ALL_CANTONS,
    DEDICATED_FETCHER_CANTONS,
    LEXFIND_ONLY_CANTONS,
    LEXWORK_CANTONS,
)

logger = logging.getLogger(__name__)

# ─── Priority Tiers ──────────────────────────────────────────────────────────

# Tier 1: Dedicated fetchers — best data quality, most structured
TIER_1_DEDICATED = sorted(DEDICATED_FETCHER_CANTONS)

# Tier 2: LexWork cantons — direct JSON API access, ordered by population/catalog size
TIER_2_LEXWORK = [
    "be",  # Bern — largest German-speaking canton
    "ag",  # Aargau — large, well-structured
    "sg",  # St. Gallen — mid-large
    "lu",  # Luzern — mid-large
    "bs",  # Basel-Stadt — already has data
    "bl",  # Basel-Landschaft
    "so",  # Solothurn
    "tg",  # Thurgau
    "gr",  # Graubünden — trilingual
    "fr",  # Fribourg — bilingual (de/fr)
    "vs",  # Valais — bilingual (de/fr)
    "zg",  # Zug — small but well-structured
    "gl",  # Glarus — small
    "ar",  # Appenzell Ausserrhoden — small
]

# Tier 3: LexFind-only cantons — fallback source, less structured
TIER_3_LEXFIND = [
    "ge",  # Genève — large, French-speaking
    "vd",  # Vaud — large, French-speaking
    "ti",  # Ticino — Italian-speaking
    "ne",  # Neuchâtel — French-speaking
    "ju",  # Jura — French-speaking
    "sh",  # Schaffhausen — small German
    "sz",  # Schwyz — small German
    "nw",  # Nidwalden — small German
    "ow",  # Obwalden — small German
    "ur",  # Uri — small German
    "ai",  # Appenzell Innerrhoden — smallest canton
]

# Full priority order (all 26 cantons)
ROLLOUT_ORDER = TIER_1_DEDICATED + TIER_2_LEXWORK + TIER_3_LEXFIND

ROLLOUT_STATE_FILE = "data/canton_rollout_state.json"


@dataclass
class CantonRolloutStatus:
    """Status of a single canton's rollout."""
    canton: str
    tier: int
    status: str = "pending"  # pending | in_progress | completed | failed
    laws_fetched: int = 0
    last_attempt: str | None = None
    error: str | None = None
    completed_at: str | None = None


@dataclass
class RolloutState:
    """Full rollout state across all cantons."""
    cantons: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_batch: list[str] = field(default_factory=list)
    last_run: str | None = None
    total_laws_fetched: int = 0

    def get_status(self, canton: str) -> str:
        return self.cantons.get(canton, {}).get("status", "pending")

    def set_status(self, canton: str, status: str, **kwargs):
        if canton not in self.cantons:
            self.cantons[canton] = {"canton": canton, "tier": get_tier(canton)}
        self.cantons[canton]["status"] = status
        self.cantons[canton].update(kwargs)

    def pending_cantons(self) -> list[str]:
        """Return cantons not yet completed, in priority order."""
        return [
            c for c in ROLLOUT_ORDER
            if self.get_status(c) not in ("completed",)
        ]

    def completed_cantons(self) -> list[str]:
        """Return cantons that have been fully rolled out."""
        return [c for c in ROLLOUT_ORDER if self.get_status(c) == "completed"]

    def next_batch(self, batch_size: int = 3) -> list[str]:
        """Get the next batch of cantons to roll out.

        Prioritizes:
        1. Cantons that were in_progress (resume interrupted rollouts)
        2. Next pending cantons in priority order
        """
        # First, resume any in-progress cantons
        in_progress = [
            c for c in ROLLOUT_ORDER
            if self.get_status(c) == "in_progress"
        ]

        # Then add pending cantons to fill the batch
        pending = [
            c for c in ROLLOUT_ORDER
            if self.get_status(c) == "pending"
        ]

        batch = in_progress + pending
        return batch[:batch_size]

    def summary(self) -> dict[str, Any]:
        """Return a summary of rollout progress."""
        completed = self.completed_cantons()
        pending = self.pending_cantons()
        in_progress = [
            c for c in ROLLOUT_ORDER if self.get_status(c) == "in_progress"
        ]
        failed = [
            c for c in ROLLOUT_ORDER if self.get_status(c) == "failed"
        ]

        return {
            "total_cantons": len(ROLLOUT_ORDER),
            "completed": len(completed),
            "in_progress": len(in_progress),
            "pending": len([c for c in ROLLOUT_ORDER if self.get_status(c) == "pending"]),
            "failed": len(failed),
            "completed_list": completed,
            "in_progress_list": in_progress,
            "failed_list": failed,
            "next_up": pending[:3],
            "progress_pct": round(len(completed) / len(ROLLOUT_ORDER) * 100, 1),
            "total_laws_fetched": self.total_laws_fetched,
        }


def get_tier(canton: str) -> int:
    """Get the priority tier for a canton."""
    if canton in TIER_1_DEDICATED:
        return 1
    if canton in TIER_2_LEXWORK:
        return 2
    if canton in TIER_3_LEXFIND:
        return 3
    return 0  # unknown


def tier_label(tier: int) -> str:
    """Human-readable tier label."""
    return {
        1: "Dedicated API",
        2: "LexWork API",
        3: "LexFind (fallback)",
    }.get(tier, "Unknown")


# ─── State persistence ────────────────────────────────────────────────────────

def load_rollout_state(repo_path: str | Path) -> RolloutState:
    """Load rollout state from disk."""
    state_path = Path(repo_path) / ROLLOUT_STATE_FILE
    if state_path.exists():
        data = json.loads(state_path.read_text())
        state = RolloutState(
            cantons=data.get("cantons", {}),
            current_batch=data.get("current_batch", []),
            last_run=data.get("last_run"),
            total_laws_fetched=data.get("total_laws_fetched", 0),
        )
        return state
    return RolloutState()


def save_rollout_state(repo_path: str | Path, state: RolloutState):
    """Persist rollout state to disk."""
    state_path = Path(repo_path) / ROLLOUT_STATE_FILE
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cantons": state.cantons,
        "current_batch": state.current_batch,
        "last_run": state.last_run,
        "total_laws_fetched": state.total_laws_fetched,
    }
    state_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ─── Rollout runner ───────────────────────────────────────────────────────────

def run_rollout(
    repo_path: str | Path,
    batch_size: int = 3,
    limit_per_canton: int | None = None,
    languages: list[str] | None = None,
    rate_limit: float = 1.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the next batch of canton rollouts.

    Args:
        repo_path: Path to the git repository.
        batch_size: Number of cantons to process in this batch.
        limit_per_canton: Max laws to fetch per canton (None = all).
        languages: Languages to fetch (default: ["de"]).
        rate_limit: Seconds between API requests.
        dry_run: If True, only report what would be done.

    Returns:
        Summary dict with results per canton.
    """
    from .cantonal_pipeline import CantonalPipeline

    repo_path = Path(repo_path)
    languages = languages or ["de"]
    state = load_rollout_state(repo_path)

    batch = state.next_batch(batch_size)
    if not batch:
        logger.info("All cantons have been rolled out!")
        return {"batch": [], "total_commits": 0, "status": "all_complete"}

    logger.info(
        "Rolling out batch of %d cantons: %s",
        len(batch),
        ", ".join(c.upper() for c in batch),
    )

    if dry_run:
        results = {}
        for canton in batch:
            tier = get_tier(canton)
            results[canton] = {
                "tier": tier,
                "tier_label": tier_label(tier),
                "action": "would process",
                "status": state.get_status(canton),
            }
        return {"batch": batch, "results": results, "dry_run": True}

    # Run the cantonal pipeline for this batch
    pipeline = CantonalPipeline(repo_path=repo_path, rate_limit=rate_limit)
    results = {}
    total_commits = 0

    for canton in batch:
        tier = get_tier(canton)
        state.set_status(canton, "in_progress", last_attempt=date.today().isoformat())
        save_rollout_state(repo_path, state)

        logger.info(
            "Processing %s (Tier %d: %s)...",
            canton.upper(), tier, tier_label(tier),
        )

        try:
            commits = pipeline._process_canton(canton, languages, limit_per_canton)
            state.set_status(
                canton, "completed",
                laws_fetched=commits,
                completed_at=date.today().isoformat(),
            )
            state.total_laws_fetched += commits
            total_commits += commits
            results[canton] = {
                "status": "completed",
                "commits": commits,
                "tier": tier,
            }
            logger.info(
                "%s: completed with %d commits (Tier %d)",
                canton.upper(), commits, tier,
            )
        except Exception as e:
            error_msg = str(e)
            state.set_status(canton, "failed", error=error_msg)
            results[canton] = {
                "status": "failed",
                "error": error_msg,
                "tier": tier,
            }
            logger.error("%s: failed — %s", canton.upper(), error_msg)

    state.current_batch = batch
    state.last_run = date.today().isoformat()
    save_rollout_state(repo_path, state)

    return {
        "batch": batch,
        "total_commits": total_commits,
        "results": results,
        "summary": state.summary(),
    }


def reset_canton(repo_path: str | Path, canton: str):
    """Reset a canton's rollout status to pending (for retry)."""
    state = load_rollout_state(repo_path)
    if canton in state.cantons:
        state.cantons[canton]["status"] = "pending"
        state.cantons[canton]["error"] = None
        save_rollout_state(repo_path, state)
        logger.info("Reset %s to pending", canton.upper())
