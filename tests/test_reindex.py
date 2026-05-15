"""Tests for the reindex helper (seeds pipeline_state.json from frontmatter)."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from legalize_ch.reindex import reindex


def _write_md(path, sr_number, version_date, lang="de"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nsr_number: '{sr_number}'\nlanguage: {lang}\n"
        f"version_date: '{version_date}'\ntitle: Sample\n---\nBody\n",
        encoding="utf-8",
    )


class TestReindex:
    def test_walks_federal_and_cantonal(self, tmp_path):
        _write_md(tmp_path / "ch/210/de/210.md", "210", "2023-01-01")
        _write_md(tmp_path / "ch/210/fr/210.md", "210", "2023-01-01", lang="fr")
        _write_md(tmp_path / "ch/210/de/210.1.md", "210.1", "2020-06-01")
        _write_md(tmp_path / "kt/zh/de/170.4.md", "170.4", "2024-06-01")

        result = reindex(tmp_path, buffer_days=30)

        state = json.loads((tmp_path / "data/pipeline_state.json").read_text())
        # Two lang files for SR 210 (2023-01-01) collapse into one entry.
        assert state["processed"] == {
            "210@2023-01-01": True,
            "210.1@2020-06-01": True,
            "170.4@2024-06-01": True,
        }
        assert result["processed_count"] == 3

    def test_last_run_uses_buffer(self, tmp_path):
        _write_md(tmp_path / "ch/210/de/210.md", "210", "2023-01-01")
        result = reindex(tmp_path, buffer_days=30)
        expected = (date.today() - timedelta(days=30)).isoformat()
        assert result["last_run"] == expected
        state = json.loads((tmp_path / "data/pipeline_state.json").read_text())
        assert state["last_run"] == expected

    def test_skips_files_without_required_frontmatter(self, tmp_path):
        _write_md(tmp_path / "ch/210/de/210.md", "210", "2023-01-01")
        # Missing version_date
        bad = tmp_path / "ch/220/de/220.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("---\nsr_number: '220'\ntitle: x\n---\n\nbody\n", encoding="utf-8")
        # Missing frontmatter delimiters entirely
        no_fm = tmp_path / "ch/230/de/230.md"
        no_fm.parent.mkdir(parents=True, exist_ok=True)
        no_fm.write_text("just text\n", encoding="utf-8")

        result = reindex(tmp_path)
        state = json.loads((tmp_path / "data/pipeline_state.json").read_text())
        assert state["processed"] == {"210@2023-01-01": True}
        assert result["skipped"] == 2

    def test_empty_repo_writes_empty_state(self, tmp_path):
        result = reindex(tmp_path)
        state = json.loads((tmp_path / "data/pipeline_state.json").read_text())
        assert state["processed"] == {}
        assert state["last_run"]  # populated even when no laws
        assert result["processed_count"] == 0

    def test_idempotent(self, tmp_path):
        _write_md(tmp_path / "ch/210/de/210.md", "210", "2023-01-01")
        reindex(tmp_path)
        first = (tmp_path / "data/pipeline_state.json").read_text()
        reindex(tmp_path)
        second = (tmp_path / "data/pipeline_state.json").read_text()
        assert first == second
