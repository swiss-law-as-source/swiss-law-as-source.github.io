"""Tests for incremental canton rollout (task 7.7)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from legalize_ch.canton_rollout import (
    ROLLOUT_ORDER,
    TIER_1_DEDICATED,
    TIER_2_LEXWORK,
    TIER_3_LEXFIND,
    RolloutState,
    get_tier,
    load_rollout_state,
    reset_canton,
    run_rollout,
    save_rollout_state,
    tier_label,
)
from legalize_ch.cli import main


class TestRolloutOrder:
    """Verify rollout priority ordering."""

    def test_all_26_cantons_included(self):
        """All 26 Swiss cantons are in the rollout order."""
        assert len(ROLLOUT_ORDER) == 26

    def test_no_duplicates(self):
        """No canton appears twice in the order."""
        assert len(set(ROLLOUT_ORDER)) == len(ROLLOUT_ORDER)

    def test_tier_1_first(self):
        """Tier 1 (dedicated) cantons come first."""
        for i, canton in enumerate(TIER_1_DEDICATED):
            assert ROLLOUT_ORDER.index(canton) < len(TIER_1_DEDICATED)

    def test_tier_2_before_tier_3(self):
        """Tier 2 (LexWork) cantons come before Tier 3 (LexFind)."""
        max_t2_idx = max(ROLLOUT_ORDER.index(c) for c in TIER_2_LEXWORK)
        min_t3_idx = min(ROLLOUT_ORDER.index(c) for c in TIER_3_LEXFIND)
        assert max_t2_idx < min_t3_idx

    def test_get_tier(self):
        """get_tier returns correct tier for each canton."""
        assert get_tier("zh") == 1
        assert get_tier("be") == 2
        assert get_tier("ag") == 2
        assert get_tier("ge") == 3
        assert get_tier("vd") == 3
        assert get_tier("xx") == 0  # unknown

    def test_tier_label(self):
        """tier_label returns human-readable labels."""
        assert "Dedicated" in tier_label(1)
        assert "LexWork" in tier_label(2)
        assert "LexFind" in tier_label(3)


class TestRolloutState:
    """Test RolloutState logic."""

    def test_initial_state_all_pending(self):
        """Fresh state has all cantons pending."""
        state = RolloutState()
        assert len(state.pending_cantons()) == 26
        assert len(state.completed_cantons()) == 0

    def test_set_and_get_status(self):
        """Can set and retrieve canton status."""
        state = RolloutState()
        state.set_status("zh", "completed", laws_fetched=100)
        assert state.get_status("zh") == "completed"
        assert state.cantons["zh"]["laws_fetched"] == 100

    def test_pending_excludes_completed(self):
        """Completed cantons are not in pending list."""
        state = RolloutState()
        state.set_status("zh", "completed")
        pending = state.pending_cantons()
        assert "zh" not in pending
        assert len(pending) == 25

    def test_next_batch_default(self):
        """next_batch returns first 3 pending cantons."""
        state = RolloutState()
        batch = state.next_batch(3)
        assert len(batch) == 3
        assert batch == ROLLOUT_ORDER[:3]

    def test_next_batch_resumes_in_progress(self):
        """In-progress cantons are prioritized in next batch."""
        state = RolloutState()
        state.set_status("ag", "in_progress")
        batch = state.next_batch(3)
        assert "ag" in batch
        # ag should be first since it's in_progress
        assert batch[0] == "ag"

    def test_next_batch_after_some_completed(self):
        """Batch skips completed cantons."""
        state = RolloutState()
        state.set_status("zh", "completed")
        state.set_status("be", "completed")
        batch = state.next_batch(3)
        assert "zh" not in batch
        assert "be" not in batch
        assert len(batch) == 3

    def test_next_batch_empty_when_all_done(self):
        """Returns empty batch when all cantons completed."""
        state = RolloutState()
        for canton in ROLLOUT_ORDER:
            state.set_status(canton, "completed")
        batch = state.next_batch(3)
        assert batch == []

    def test_summary(self):
        """Summary returns correct counts."""
        state = RolloutState()
        state.set_status("zh", "completed", laws_fetched=50)
        state.set_status("be", "in_progress")
        state.set_status("ge", "failed", error="timeout")
        state.total_laws_fetched = 50

        summary = state.summary()
        assert summary["total_cantons"] == 26
        assert summary["completed"] == 1
        assert summary["in_progress"] == 1
        assert summary["failed"] == 1
        assert summary["pending"] == 23
        assert summary["progress_pct"] == pytest.approx(3.8, abs=0.1)
        assert summary["total_laws_fetched"] == 50
        assert "zh" in summary["completed_list"]
        assert "ge" in summary["failed_list"]


class TestStatePersistence:
    """Test state load/save."""

    def test_save_and_load(self, tmp_path):
        """State survives save/load cycle."""
        state = RolloutState()
        state.set_status("zh", "completed", laws_fetched=42)
        state.total_laws_fetched = 42
        state.last_run = "2026-05-06"
        save_rollout_state(tmp_path, state)

        loaded = load_rollout_state(tmp_path)
        assert loaded.get_status("zh") == "completed"
        assert loaded.cantons["zh"]["laws_fetched"] == 42
        assert loaded.total_laws_fetched == 42
        assert loaded.last_run == "2026-05-06"

    def test_load_missing_file(self, tmp_path):
        """Loading from non-existent file returns fresh state."""
        state = load_rollout_state(tmp_path)
        assert len(state.pending_cantons()) == 26

    def test_reset_canton(self, tmp_path):
        """reset_canton changes failed status back to pending."""
        state = RolloutState()
        state.set_status("ge", "failed", error="timeout")
        save_rollout_state(tmp_path, state)

        reset_canton(tmp_path, "ge")

        loaded = load_rollout_state(tmp_path)
        assert loaded.get_status("ge") == "pending"
        assert loaded.cantons["ge"].get("error") is None


class TestRunRollout:
    """Test the run_rollout orchestrator."""

    def test_dry_run(self, tmp_path):
        """Dry run reports batch without processing."""
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        result = run_rollout(tmp_path, batch_size=2, dry_run=True)
        assert result["dry_run"] is True
        assert len(result["batch"]) == 2
        # Should be first 2 in priority order
        assert result["batch"] == ROLLOUT_ORDER[:2]

    @patch("legalize_ch.cantonal_pipeline.CantonalPipeline._process_canton")
    def test_processes_batch(self, mock_process, tmp_path):
        """Rollout processes the batch and updates state."""
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        mock_process.return_value = 5

        result = run_rollout(tmp_path, batch_size=2, languages=["de"])

        assert result["total_commits"] == 10  # 5 per canton x 2
        assert len(result["batch"]) == 2
        # Check state was updated
        state = load_rollout_state(tmp_path)
        for canton in result["batch"]:
            assert state.get_status(canton) == "completed"

    @patch("legalize_ch.cantonal_pipeline.CantonalPipeline._process_canton")
    def test_handles_failure(self, mock_process, tmp_path):
        """Rollout marks canton as failed on exception."""
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        mock_process.side_effect = RuntimeError("API down")

        result = run_rollout(tmp_path, batch_size=1, languages=["de"])

        assert result["total_commits"] == 0
        canton = result["batch"][0]
        assert result["results"][canton]["status"] == "failed"
        # State persisted
        state = load_rollout_state(tmp_path)
        assert state.get_status(canton) == "failed"

    def test_all_complete(self, tmp_path):
        """Returns all_complete when no cantons remain."""
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        # Pre-mark all as completed
        state = RolloutState()
        for canton in ROLLOUT_ORDER:
            state.set_status(canton, "completed")
        save_rollout_state(tmp_path, state)

        result = run_rollout(tmp_path, batch_size=3)
        assert result["status"] == "all_complete"


class TestCLICantRollout:
    """Test CLI cantonal-rollout command."""

    def test_status_flag(self, tmp_path):
        """--status shows progress."""
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(main, [
            "cantonal-rollout", "--repo", str(tmp_path), "--status"
        ])
        assert result.exit_code == 0
        assert "Canton Rollout Progress" in result.output
        assert "0/26" in result.output

    def test_dry_run_flag(self, tmp_path):
        """--dry-run shows what would be done."""
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(main, [
            "cantonal-rollout", "--repo", str(tmp_path), "--dry-run"
        ])
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "Tier" in result.output

    def test_reset_flag(self, tmp_path):
        """--reset resets a canton."""
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        # Create a failed state
        state = RolloutState()
        state.set_status("ge", "failed", error="timeout")
        save_rollout_state(tmp_path, state)

        runner = CliRunner()
        result = runner.invoke(main, [
            "cantonal-rollout", "--repo", str(tmp_path), "--reset", "ge"
        ])
        assert result.exit_code == 0
        assert "Reset GE" in result.output

    @patch("legalize_ch.cantonal_pipeline.CantonalPipeline._process_canton")
    def test_full_run(self, mock_process, tmp_path):
        """Full rollout run via CLI."""
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        mock_process.return_value = 3

        runner = CliRunner()
        result = runner.invoke(main, [
            "cantonal-rollout", "--repo", str(tmp_path),
            "--batch-size", "2", "--limit", "5",
        ])
        assert result.exit_code == 0
        assert "Batch complete" in result.output
        assert "commits" in result.output
