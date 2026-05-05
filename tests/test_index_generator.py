"""Tests for the INDEX.md generator."""
import tempfile
from pathlib import Path

import pytest

from legalize_ch.index_generator import (
    _extract_frontmatter,
    _sr_sort_key,
    generate_index,
    write_index,
)


@pytest.fixture
def sample_repo(tmp_path):
    """Create a minimal repo structure with sample markdown files."""
    # Create ch/1/de/ directory
    de_dir = tmp_path / "ch" / "1" / "de"
    de_dir.mkdir(parents=True)

    # Create sample law files
    (de_dir / "1.001.md").write_text(
        "---\n"
        "language: de\n"
        "source: https://fedlex.data.admin.ch\n"
        "sr_number: 1.001\n"
        "title: Bundesverfassung Test\n"
        "version_date: '2024-01-01'\n"
        "---\n\n# Test content\n",
        encoding="utf-8",
    )
    (de_dir / "1.002.md").write_text(
        "---\n"
        "language: de\n"
        "source: https://fedlex.data.admin.ch\n"
        "sr_number: 1.002\n"
        "title: Zweites Gesetz\n"
        "version_date: '2024-02-01'\n"
        "---\n\n# Content\n",
        encoding="utf-8",
    )

    # Create ch/0/de/ directory with one entry
    de_dir_0 = tmp_path / "ch" / "0" / "de"
    de_dir_0.mkdir(parents=True)
    (de_dir_0 / "0.101.md").write_text(
        "---\n"
        "language: de\n"
        "source: https://fedlex.data.admin.ch\n"
        "sr_number: 0.101\n"
        "title: Konvention zum Schutze der Menschenrechte\n"
        "version_date: '2022-01-01'\n"
        "---\n\n# EMRK\n",
        encoding="utf-8",
    )

    return tmp_path


def test_extract_frontmatter(sample_repo):
    """Test frontmatter extraction from markdown files."""
    path = sample_repo / "ch" / "1" / "de" / "1.001.md"
    fm = _extract_frontmatter(path)
    assert fm is not None
    assert fm["sr_number"] == "1.001"
    assert fm["title"] == "Bundesverfassung Test"
    assert fm["language"] == "de"


def test_extract_frontmatter_no_frontmatter(tmp_path):
    """Test with a file that has no frontmatter."""
    path = tmp_path / "test.md"
    path.write_text("# Just a heading\nNo frontmatter here.\n")
    assert _extract_frontmatter(path) is None


def test_extract_frontmatter_missing_sr(tmp_path):
    """Test with frontmatter but no sr_number."""
    path = tmp_path / "test.md"
    path.write_text("---\ntitle: Test\nlanguage: de\n---\n")
    assert _extract_frontmatter(path) is None


def test_sr_sort_key():
    """Test SR number sort key generation."""
    assert _sr_sort_key("0.101") < _sr_sort_key("0.102")
    assert _sr_sort_key("1.001") < _sr_sort_key("1.002")
    assert _sr_sort_key("0.101.1") < _sr_sort_key("0.101.2")
    assert _sr_sort_key("0.101.1") < _sr_sort_key("0.101.02")
    assert _sr_sort_key("1.001") < _sr_sort_key("2.001")


def test_generate_index(sample_repo):
    """Test full index generation."""
    content = generate_index(repo_path=str(sample_repo), lang="de")

    assert "# Index of Swiss Federal Law" in content
    assert "**3** laws indexed" in content
    assert "[0.101]" in content
    assert "[1.001]" in content
    assert "[1.002]" in content
    assert "Bundesverfassung Test" in content
    assert "Konvention zum Schutze der Menschenrechte" in content
    # Check category headers
    assert "0 – Systematische Sammlung des Bundesrechts" in content
    assert "1 – Staat – Volk – Behörden" in content


def test_generate_index_links(sample_repo):
    """Test that links are properly formatted."""
    content = generate_index(repo_path=str(sample_repo), lang="de")
    assert "ch/1/de/1.001.md" in content
    assert "ch/0/de/0.101.md" in content


def test_write_index(sample_repo):
    """Test that INDEX.md is written to disk."""
    out = write_index(repo_path=str(sample_repo), lang="de")
    assert out.exists()
    assert out.name == "INDEX.md"
    content = out.read_text(encoding="utf-8")
    assert "# Index of Swiss Federal Law" in content


def test_generate_index_missing_dir(tmp_path):
    """Test error when ch/ directory doesn't exist."""
    with pytest.raises(FileNotFoundError):
        generate_index(repo_path=str(tmp_path))
