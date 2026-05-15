"""Tests for the static publications export (static_export.py)."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date

import pytest

from legalize_ch.static_export import export_publications


@pytest.fixture
def populated_repo(tmp_path):
    """A repo with two consolidation commits and one pre-1970 marker file."""
    cwd = str(tmp_path)
    subprocess.run(["git", "init"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=cwd, capture_output=True)

    def commit(paths, msg, iso):
        for rel, content in paths.items():
            f = tmp_path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=cwd, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", msg, f"--date={iso}"],
            cwd=cwd, capture_output=True,
            env={**os.environ, "GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso},
        )

    # Pre-1970 marker in frontmatter (file is committed in 1985 but the
    # publication date predates git's epoch boundary).
    commit(
        {"ch/0/de/0.742.140.313.61.md":
            "---\nsr_number: '0.742.140.313.61'\nlanguage: de\n"
            "title: Vertrag vom 27. Juli 1852\nversion_date: '1985-10-07'\n"
            "original_publication_date: '1852-07-27'\n---\nBody\n"},
        "SR 0.742.140.313.61: Treaty (1985-10-07)",
        "1985-10-07T12:00:00+01:00",
    )
    commit(
        {"ch/210/de/210.md":
            "---\nsr_number: '210'\nlanguage: de\ntitle: ZGB\nversion_date: '2023-01-01'\n---\nBody\n"},
        "SR 210: ZGB (2023-01-01)",
        "2023-01-01T12:00:00+01:00",
    )
    commit(
        {"kt/zh/de/170.4.md":
            "---\nsr_number: '170.4'\nlanguage: de\ntitle: Gemeindegesetz\nversion_date: '2024-06-01'\n---\nBody\n"},
        "SR 170.4: Gemeindegesetz (2024-06-01)",
        "2024-06-01T12:00:00+01:00",
    )
    return tmp_path


class TestExportPublications:
    def test_creates_per_year_files(self, populated_repo, tmp_path):
        out = tmp_path / "out"
        result = export_publications(populated_repo, out)

        assert result["years"] == 4  # 1852, 1985, 2023, 2024
        assert (out / "1852.json").exists()
        assert (out / "1985.json").exists()
        assert (out / "2023.json").exists()
        assert (out / "2024.json").exists()
        assert (out / "index.json").exists()
        assert (out / "today.json").exists()

    def test_year_file_shape_matches_api_response(self, populated_repo, tmp_path):
        out = tmp_path / "out"
        export_publications(populated_repo, out)

        data = json.loads((out / "2023.json").read_text())
        assert data["date_prefix"] == "2023"
        assert data["count"] == 1
        pub = data["publications"][0]
        assert pub["sr_number"] == "210"
        assert pub["scope"] == "federal"
        assert pub["date"] == "2023-01-01"
        assert "de" in pub["languages"]

    def test_pre_1970_year_uses_frontmatter(self, populated_repo, tmp_path):
        out = tmp_path / "out"
        export_publications(populated_repo, out)

        data = json.loads((out / "1852.json").read_text())
        assert data["count"] == 1
        pub = data["publications"][0]
        assert pub["sr_number"] == "0.742.140.313.61"
        assert pub["date"] == "1852-07-27"

    def test_cantonal_scope_propagates(self, populated_repo, tmp_path):
        out = tmp_path / "out"
        export_publications(populated_repo, out)

        data = json.loads((out / "2024.json").read_text())
        assert data["publications"][0]["scope"] == "cantonal"
        assert data["publications"][0]["sr_number"] == "170.4"

    def test_index_lists_all_years(self, populated_repo, tmp_path):
        out = tmp_path / "out"
        export_publications(populated_repo, out)

        index = json.loads((out / "index.json").read_text())
        assert index["years"] == [1852, 1985, 2023, 2024]
        assert index["earliest_year"] == 1852
        assert index["latest_year"] == 2024
        assert index["total_publications"] == 4
        assert "generated_at" in index

    def test_today_file_exists_even_when_empty(self, populated_repo, tmp_path):
        out = tmp_path / "out"
        export_publications(populated_repo, out)

        today = json.loads((out / "today.json").read_text())
        assert today["date_prefix"] == date.today().isoformat()
        assert "publications" in today

    def test_snapshot_mode_via_frontmatter(self, tmp_path):
        """When all data lives in a single bootstrap commit and per-revision
        history is absent, export must still emit per-year files derived
        from each markdown's `version_date` frontmatter."""
        cwd = str(tmp_path)
        subprocess.run(["git", "init"], cwd=cwd, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=cwd, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=cwd, capture_output=True)

        for sr, vd, scope_dir in [
            ("210", "2023-01-01", "ch/210"),
            ("220", "2021-07-15", "ch/220"),
            ("170.4", "2024-06-01", "kt/zh"),
        ]:
            for lang in ("de", "fr", "it"):
                p = tmp_path / scope_dir / lang / f"{sr}.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    f"---\nsr_number: '{sr}'\nlanguage: {lang}\n"
                    f"version_date: '{vd}'\ntitle: Sample {sr} {lang}\n---\nBody\n",
                    encoding="utf-8",
                )

        subprocess.run(["git", "add", "."], cwd=cwd, capture_output=True)
        # ONE big snapshot commit — no per-revision history.
        iso = "2026-05-15T12:00:00+01:00"
        subprocess.run(
            ["git", "commit", "-m", "Bootstrap snapshot (not in SR-message format)",
             f"--date={iso}"],
            cwd=cwd, capture_output=True,
            env={**os.environ, "GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso},
        )

        out = tmp_path / "out"
        result = export_publications(tmp_path, out)

        assert result["years"] == 3  # 2021, 2023, 2024
        for y in (2021, 2023, 2024):
            assert (out / f"{y}.json").exists()

        data_2023 = json.loads((out / "2023.json").read_text())
        assert data_2023["count"] == 1
        pub = data_2023["publications"][0]
        assert pub["sr_number"] == "210"
        assert pub["date"] == "2023-01-01"
        assert sorted(pub["languages"]) == ["de", "fr", "it"]
        # The commit_hash is the snapshot commit (it introduced the file).
        assert pub["commit_hash"]

        data_2024 = json.loads((out / "2024.json").read_text())
        assert data_2024["publications"][0]["scope"] == "cantonal"

    def test_empty_repo_writes_index(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), capture_output=True)

        out = tmp_path / "out"
        result = export_publications(repo, out)

        assert result["years"] == 0
        assert result["publications"] == 0
        index = json.loads((out / "index.json").read_text())
        assert index["years"] == []
        assert index["earliest_year"] is None
