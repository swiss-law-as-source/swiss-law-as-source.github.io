"""Tests for commit_initial — verify README is not overwritten on subsequent runs (TD.2)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from legalize_ch.committer import GitCommitter


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a bare git repo in a temp directory."""
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.ch"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    return tmp_path


def test_commit_initial_creates_readme(git_repo: Path):
    """commit_initial should create README.md when it doesn't exist."""
    committer = GitCommitter(git_repo)
    readme = git_repo / "README.md"
    assert not readme.exists()

    committer.commit_initial()

    assert readme.exists()
    assert "Swiss Federal Law" in readme.read_text()


def test_commit_initial_skips_when_readme_exists(git_repo: Path):
    """commit_initial must NOT overwrite an existing README.md (TD.2 fix)."""
    committer = GitCommitter(git_repo)
    readme = git_repo / "README.md"

    # Write a custom README first
    custom_content = "# My Custom README\n\nDetailed stats and usage info.\n"
    readme.write_text(custom_content, encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=git_repo, capture_output=True)

    # Now call commit_initial — it should be a no-op
    committer.commit_initial()

    assert readme.read_text() == custom_content, "commit_initial overwrote existing README.md!"


def test_commit_initial_idempotent(git_repo: Path):
    """Calling commit_initial twice should not fail or change the README."""
    committer = GitCommitter(git_repo)

    committer.commit_initial()
    content_after_first = (git_repo / "README.md").read_text()

    committer.commit_initial()
    content_after_second = (git_repo / "README.md").read_text()

    assert content_after_first == content_after_second
