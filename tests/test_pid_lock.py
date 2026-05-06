"""Tests for PID-file locking mechanism (TD.5)."""
from __future__ import annotations

import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from legalize_ch.pipeline import Pipeline, PipelineAlreadyRunningError, _is_pid_alive


@pytest.fixture
def repo(tmp_path):
    """Create a minimal git repo with pipeline state."""
    (tmp_path / "data").mkdir()
    state = {"processed": {}, "last_run": "2026-05-01"}
    (tmp_path / "data" / "pipeline_state.json").write_text(json.dumps(state))
    return tmp_path


@pytest.fixture
def pipeline(repo):
    """Create a Pipeline with mocked git/fetcher dependencies."""
    with patch("legalize_ch.pipeline.GitCommitter"):
        with patch("legalize_ch.pipeline.FedlexFetcher"):
            p = Pipeline(repo_path=repo, rate_limit=0.0)
    return p


# ---------------------------------------------------------------------------
# _is_pid_alive
# ---------------------------------------------------------------------------

class TestIsPidAlive:
    def test_own_pid_is_alive(self):
        assert _is_pid_alive(os.getpid()) is True

    def test_nonexistent_pid(self):
        # PID 99999999 should not exist
        assert _is_pid_alive(99999999) is False

    def test_permission_error_treated_as_alive(self):
        with patch("os.kill", side_effect=PermissionError):
            assert _is_pid_alive(1) is True


# ---------------------------------------------------------------------------
# _acquire_lock / _release_lock
# ---------------------------------------------------------------------------

class TestAcquireLock:
    def test_creates_pid_file(self, pipeline, repo):
        pipeline._acquire_lock()
        pid_file = repo / "pipeline.pid"
        assert pid_file.exists()
        assert int(pid_file.read_text().strip()) == os.getpid()
        pipeline._release_lock()

    def test_release_removes_pid_file(self, pipeline, repo):
        pipeline._acquire_lock()
        pipeline._release_lock()
        assert not (repo / "pipeline.pid").exists()

    def test_blocks_second_instance(self, pipeline, repo):
        """A second acquire with a live PID raises an error."""
        pipeline._acquire_lock()
        try:
            # Simulate another instance trying to acquire
            with patch("legalize_ch.pipeline.GitCommitter"):
                with patch("legalize_ch.pipeline.FedlexFetcher"):
                    p2 = Pipeline(repo_path=repo, rate_limit=0.0)
            with pytest.raises(PipelineAlreadyRunningError, match="already running"):
                p2._acquire_lock()
        finally:
            pipeline._release_lock()

    def test_stale_pid_file_is_overwritten(self, pipeline, repo):
        """A PID file for a dead process is treated as stale and overwritten."""
        pid_file = repo / "pipeline.pid"
        pid_file.write_text("99999999")  # non-existent PID

        pipeline._acquire_lock()  # should succeed
        assert int(pid_file.read_text().strip()) == os.getpid()
        pipeline._release_lock()

    def test_corrupted_pid_file_is_overwritten(self, pipeline, repo):
        """A PID file with invalid content is treated as stale."""
        pid_file = repo / "pipeline.pid"
        pid_file.write_text("not-a-number")

        pipeline._acquire_lock()  # should succeed
        assert int(pid_file.read_text().strip()) == os.getpid()
        pipeline._release_lock()

    def test_release_only_removes_own_pid(self, pipeline, repo):
        """Release does not remove the PID file if it was overwritten by another process."""
        pipeline._acquire_lock()
        pid_file = repo / "pipeline.pid"
        # Simulate another process overwriting the PID file
        pid_file.write_text("12345")
        pipeline._release_lock()
        # File should still exist with the other PID
        assert pid_file.exists()
        assert pid_file.read_text().strip() == "12345"

    def test_release_without_acquire_is_noop(self, pipeline, repo):
        """Calling release without acquire does nothing."""
        pipeline._release_lock()  # should not raise


# ---------------------------------------------------------------------------
# Integration: run/update acquire and release the lock
# ---------------------------------------------------------------------------

class TestLockIntegration:
    def test_run_acquires_and_releases_lock(self, pipeline, repo):
        """Pipeline.run acquires the lock and releases it on completion."""
        pipeline.committer.init_repo = MagicMock()
        pipeline.committer.commit_initial = MagicMock()
        pipeline.fetcher.fetch_catalog = MagicMock(return_value=[])

        pipeline.run(limit=0)

        # Lock should be released
        assert not (repo / "pipeline.pid").exists()

    def test_run_releases_lock_on_error(self, pipeline, repo):
        """Pipeline.run releases the lock even if an exception occurs."""
        pipeline.committer.init_repo = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            pipeline.run()

        # Lock should still be released
        assert not (repo / "pipeline.pid").exists()

    def test_update_acquires_and_releases_lock(self, pipeline, repo):
        """Pipeline.update acquires the lock and releases it on completion."""
        pipeline.fetcher.fetch_modified_since = MagicMock(return_value=[])

        pipeline.update()

        assert not (repo / "pipeline.pid").exists()

    def test_update_releases_lock_on_error(self, pipeline, repo):
        """Pipeline.update releases the lock even if an exception occurs."""
        pipeline.fetcher.fetch_modified_since = MagicMock(
            side_effect=RuntimeError("network error")
        )

        with pytest.raises(RuntimeError, match="network error"):
            pipeline.update()

        assert not (repo / "pipeline.pid").exists()
