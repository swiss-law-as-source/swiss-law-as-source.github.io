"""Tests for the structured data exporter (JSON-LD + CSV)."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from legalize_ch.exporter import (
    collect_metadata,
    export_csv,
    export_jsonld,
    write_all,
    write_csv,
    write_jsonld,
)


def _create_law_file(repo: Path, sr_number: str, lang: str = "de",
                     title: str = "Test Law", abbreviation: str = "",
                     version_date: str = "2024-01-01") -> Path:
    """Helper: create a minimal law markdown file with frontmatter."""
    sr_prefix = sr_number.split(".")[0]
    dir_path = repo / "ch" / sr_prefix / lang
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{sr_number}.md"

    lines = ["---"]
    if abbreviation:
        lines.append(f"abbreviation: {abbreviation}")
    lines.append(f"language: {lang}")
    lines.append("source: https://fedlex.data.admin.ch")
    lines.append(f"sr_number: '{sr_number}'")
    lines.append(f"title: {title}")
    lines.append(f"version_date: '{version_date}'")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append("Article 1 — Test content.")
    lines.append("")

    file_path.write_text("\n".join(lines), encoding="utf-8")
    return file_path


@pytest.fixture
def law_repo(tmp_path):
    """Create a temporary repo with sample law files."""
    _create_law_file(tmp_path, "210", title="Zivilgesetzbuch", abbreviation="ZGB",
                     version_date="2024-01-01")
    _create_law_file(tmp_path, "210", lang="fr", title="Code civil", abbreviation="CC",
                     version_date="2024-01-01")
    _create_law_file(tmp_path, "311.0", title="Strafgesetzbuch", abbreviation="StGB",
                     version_date="2023-07-01")
    _create_law_file(tmp_path, "0.101", title="EMRK",
                     version_date="2022-06-01")
    # A file with no abbreviation
    _create_law_file(tmp_path, "172.010", title="Regierungs- und Verwaltungsorganisationsgesetz",
                     version_date="2023-01-01")
    return tmp_path


class TestCollectMetadata:
    def test_collects_all_languages(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de", "fr"])
        sr_numbers = {e["sr_number"] for e in entries}
        assert "210" in sr_numbers
        assert "311.0" in sr_numbers
        # French version of 210
        fr_entries = [e for e in entries if e["sr_number"] == "210" and e["language"] == "fr"]
        assert len(fr_entries) == 1
        assert fr_entries[0]["title"] == "Code civil"

    def test_sr_filter(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"], sr_filter="21")
        assert len(entries) == 1
        assert entries[0]["sr_number"] == "210"

    def test_category_assignment(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"])
        by_sr = {e["sr_number"]: e for e in entries}
        # SR 210 -> category 2 (Privatrecht)
        assert "Privatrecht" in by_sr["210"]["category"]
        # SR 311.0 -> category 3 (Strafrecht)
        assert "Strafrecht" in by_sr["311.0"]["category"]
        # SR 0.101 -> category 0 (Völkerrecht)
        assert "Völkerrecht" in by_sr["0.101"]["category"]

    def test_fedlex_uri(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"])
        by_sr = {e["sr_number"]: e for e in entries}
        assert by_sr["210"]["fedlex_uri"] == "https://fedlex.data.admin.ch/eli/cc/210"
        assert by_sr["311.0"]["fedlex_uri"] == "https://fedlex.data.admin.ch/eli/cc/311.0"

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            collect_metadata(str(tmp_path / "nonexistent"))

    def test_dedup_same_sr_language(self, law_repo):
        """Same SR + language should only appear once."""
        entries = collect_metadata(str(law_repo), languages=["de"])
        sr_lang_pairs = [(e["sr_number"], e["language"]) for e in entries]
        assert len(sr_lang_pairs) == len(set(sr_lang_pairs))

    def test_empty_repo(self, tmp_path):
        (tmp_path / "ch").mkdir()
        entries = collect_metadata(str(tmp_path), languages=["de"])
        assert entries == []


class TestExportCSV:
    def test_csv_has_header_and_rows(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"])
        csv_str = export_csv(entries)
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == len(entries)
        # Check header fields
        assert "sr_number" in reader.fieldnames
        assert "title" in reader.fieldnames
        assert "fedlex_uri" in reader.fieldnames

    def test_csv_content(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"], sr_filter="210")
        csv_str = export_csv(entries)
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["sr_number"] == "210"
        assert rows[0]["abbreviation"] == "ZGB"
        assert rows[0]["title"] == "Zivilgesetzbuch"

    def test_csv_empty_entries(self):
        assert export_csv([]) == ""

    def test_csv_special_characters(self, tmp_path):
        """Titles with commas/quotes should be properly escaped."""
        _create_law_file(tmp_path, "999", title='Law with "quotes" and, commas')
        entries = collect_metadata(str(tmp_path), languages=["de"])
        csv_str = export_csv(entries)
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert rows[0]["title"] == 'Law with "quotes" and, commas'


class TestExportJSONLD:
    def test_jsonld_structure(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"])
        doc = export_jsonld(entries)
        assert "@context" in doc
        assert "@graph" in doc
        assert doc["@type"] == "Dataset"
        assert len(doc["@graph"]) == len(entries)

    def test_jsonld_context(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"])
        doc = export_jsonld(entries)
        ctx = doc["@context"]
        assert "schema.org" in ctx["@vocab"]

    def test_jsonld_legislation_node(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"], sr_filter="210")
        doc = export_jsonld(entries)
        node = doc["@graph"][0]
        assert node["@type"] == "Legislation"
        assert node["identifier"] == "210"
        assert node["name"] == "Zivilgesetzbuch"
        assert node["alternateName"] == "ZGB"
        assert node["dateModified"] == "2024-01-01"
        assert "fedlex.data.admin.ch" in node["@id"]

    def test_jsonld_no_abbreviation(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"], sr_filter="172")
        doc = export_jsonld(entries)
        node = doc["@graph"][0]
        assert "alternateName" not in node

    def test_jsonld_serializable(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"])
        doc = export_jsonld(entries)
        # Must be valid JSON
        json_str = json.dumps(doc, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["@type"] == "Dataset"

    def test_jsonld_distribution(self, law_repo):
        entries = collect_metadata(str(law_repo), languages=["de"])
        doc = export_jsonld(entries)
        dist = doc["distribution"]
        assert len(dist) == 2
        formats = {d["encodingFormat"] for d in dist}
        assert "text/csv" in formats
        assert "application/ld+json" in formats


class TestWriteFiles:
    def test_write_csv(self, law_repo):
        path = write_csv(str(law_repo), languages=["de"])
        assert path.exists()
        assert path.name == "laws_metadata.csv"
        content = path.read_text(encoding="utf-8")
        assert "sr_number" in content
        assert "210" in content

    def test_write_jsonld(self, law_repo):
        path = write_jsonld(str(law_repo), languages=["de"])
        assert path.exists()
        assert path.name == "laws_metadata.jsonld"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["@type"] == "Dataset"

    def test_write_all(self, law_repo):
        csv_path, jsonld_path = write_all(str(law_repo), languages=["de"])
        assert csv_path.exists()
        assert jsonld_path.exists()
        # Both should be in data/ directory
        assert csv_path.parent.name == "data"
        assert jsonld_path.parent.name == "data"

    def test_write_creates_data_dir(self, tmp_path):
        (tmp_path / "ch").mkdir()
        csv_path = write_csv(str(tmp_path), languages=["de"])
        assert csv_path.parent.exists()
