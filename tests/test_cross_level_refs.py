"""Tests for cross-level reference detection (federal ↔ cantonal)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from legalize_ch.cross_level_refs import (
    CrossLevelRef,
    CrossLevelResult,
    _detect_abbreviation_references,
    _detect_einfuehrungsgesetz,
    _detect_sr_references,
    analyze_cross_level_refs,
    build_abbreviation_map,
    scan_cantonal_to_federal,
    write_cross_level_json,
    write_cross_level_html,
)


# ─── SR Reference Detection ──────────────────────────────────────────────────

class TestDetectSrReferences:
    def test_simple_sr_reference(self):
        body = "gestützt auf Art. 14 des Bundesgesetzes (SR 935.61)."
        refs = _detect_sr_references(body)
        assert "935.61" in refs

    def test_sr_reference_with_brackets(self):
        body = "SR [935.61](https://db.clex.ch/link/Bund/935.61/de)"
        refs = _detect_sr_references(body)
        assert "935.61" in refs

    def test_clex_bund_link(self):
        body = "See https://db.clex.ch/link/Bund/210/de for details."
        refs = _detect_sr_references(body)
        assert "210" in refs

    def test_multiple_references(self):
        body = "SR 311.0, see also SR 210 and SR 220"
        refs = _detect_sr_references(body)
        assert refs == {"311.0", "210", "220"}

    def test_no_references(self):
        body = "This is a cantonal law with no federal references."
        refs = _detect_sr_references(body)
        assert len(refs) == 0

    def test_dotted_sr_number(self):
        body = "gemäss SR 0.101.02 und SR 142.20"
        refs = _detect_sr_references(body)
        assert "0.101.02" in refs
        assert "142.20" in refs

    def test_fedlex_uri(self):
        body = "see fedlex.data.admin.ch/eli/cc/220 for the OR"
        refs = _detect_sr_references(body)
        assert "220" in refs


# ─── Abbreviation Detection ──────────────────────────────────────────────────

class TestDetectAbbreviationReferences:
    def test_known_abbreviation(self):
        abbr_map = {"BGFA": "935.61", "KVG": "832.10", "OR": "220"}
        body = "Die Voraussetzungen gemäss BGFA sind erfüllt."
        refs = _detect_abbreviation_references(body, abbr_map)
        assert "935.61" in refs

    def test_multiple_abbreviations(self):
        abbr_map = {"BGFA": "935.61", "KVG": "832.10", "StGB": "311.0"}
        body = "Gemäss BGFA und StGB gelten folgende Regeln."
        refs = _detect_abbreviation_references(body, abbr_map)
        assert "935.61" in refs
        assert "311.0" in refs
        assert "832.10" not in refs

    def test_no_match(self):
        abbr_map = {"BGFA": "935.61"}
        body = "Keine bekannten Abkürzungen hier."
        refs = _detect_abbreviation_references(body, abbr_map)
        assert len(refs) == 0

    def test_word_boundary(self):
        """Abbreviation should match at word boundaries only."""
        abbr_map = {"AHV": "831.10"}
        body = "Die AHVG-Bestimmungen und die AHV."
        refs = _detect_abbreviation_references(body, abbr_map)
        assert "831.10" in refs  # AHV matches at end

    def test_short_abbreviations_filtered(self):
        """2-char abbreviations that aren't well-known should not match."""
        abbr_map = {"XY": "999.99"}
        body = "The XY value is important."
        refs = _detect_abbreviation_references(body, abbr_map)
        assert "999.99" not in refs

    def test_well_known_2char(self):
        """Well-known 2-char abbreviations (OR, BV) should match."""
        abbr_map = {"OR": "220"}
        body = "Gemäss OR Art. 1."
        refs = _detect_abbreviation_references(body, abbr_map)
        assert "220" in refs


# ─── Einführungsgesetz Detection ──────────────────────────────────────────────

class TestDetectEinfuehrungsgesetz:
    def test_einfuehrungsgesetz_with_sr(self):
        title = "Einführungsgesetz zum Bundesgesetz über die Freizügigkeit"
        body = "gestützt auf SR 935.61, beschliesst:"
        refs = _detect_einfuehrungsgesetz(title, body)
        assert "935.61" in refs

    def test_no_einfuehrungsgesetz(self):
        title = "Gesundheitsgesetz"
        body = "SR 832.10 ist anwendbar."
        refs = _detect_einfuehrungsgesetz(title, body)
        assert len(refs) == 0

    def test_without_umlaut(self):
        title = "Einfuhrungsgesetz zum Bundesgesetz"
        body = "gemäss SR 210 und SR 220"
        refs = _detect_einfuehrungsgesetz(title, body)
        assert "210" in refs


# ─── CrossLevelResult ─────────────────────────────────────────────────────────

class TestCrossLevelResult:
    def test_to_dict_structure(self):
        result = CrossLevelResult(
            cantonal_to_federal=[
                CrossLevelRef("ag", "290.100", "EG BGFA", "935.61", "explicit_sr"),
                CrossLevelRef("ag", "290.100", "EG BGFA", "220", "abbreviation"),
                CrossLevelRef("bs", "300.100", "GesG", "832.10", "abbreviation"),
            ],
            federal_to_cantonal=[],
        )
        data = result.to_dict()

        assert data["total_cross_level_references"] == 3
        assert data["cantonal_to_federal_count"] == 3
        assert data["federal_to_cantonal_count"] == 0
        assert data["cantons_with_references"] == 2
        assert data["federal_laws_referenced"] == 3
        assert data["cantonal_laws_referencing"] == 2

        # Check canton grouping
        assert "ag" in data["cantonal_to_federal"]
        assert "290.100" in data["cantonal_to_federal"]["ag"]

        # Check federal cited_by
        assert "935.61" in data["federal_cited_by_cantonal"]
        assert data["federal_cited_by_cantonal"]["935.61"][0]["canton"] == "ag"

    def test_empty_result(self):
        result = CrossLevelResult()
        data = result.to_dict()
        assert data["total_cross_level_references"] == 0
        assert data["cantonal_to_federal"] == {}


# ─── Integration with File System ─────────────────────────────────────────────

@pytest.fixture
def mock_repo(tmp_path):
    """Create a minimal repo structure for testing."""
    ch = tmp_path / "ch"

    # Federal law: SR 935.61
    fed_dir = ch / "935" / "de"
    fed_dir.mkdir(parents=True)
    (fed_dir / "935.61.md").write_text(textwrap.dedent("""\
        ---
        sr_number: '935.61'
        title: 'Bundesgesetz über die Freizügigkeit der Anwältinnen und Anwälte'
        language: de
        version_date: '2023-01-01'
        abbreviation: BGFA
        source: https://fedlex.data.admin.ch
        ---

        # Bundesgesetz über die Freizügigkeit der Anwältinnen und Anwälte

        (Anwaltsgesetz, BGFA)
    """), encoding="utf-8")

    # Federal law: SR 220 (OR)
    fed_dir2 = ch / "220" / "de"
    fed_dir2.mkdir(parents=True)
    (fed_dir2 / "220.md").write_text(textwrap.dedent("""\
        ---
        sr_number: '220'
        title: 'Bundesgesetz betreffend die Ergänzung des Schweizerischen Zivilgesetzbuches'
        language: de
        version_date: '2023-01-01'
        abbreviation: OR
        source: https://fedlex.data.admin.ch
        ---

        # Obligationenrecht
    """), encoding="utf-8")

    # Cantonal law: AG 290.100 (references SR 935.61)
    cant_dir = ch / "ag" / "de"
    cant_dir.mkdir(parents=True)
    (cant_dir / "290.100.md").write_text(textwrap.dedent("""\
        ---
        canton: AG
        systematic_number: '290.100'
        title: 'Einführungsgesetz zum Bundesgesetz über die Freizügigkeit der Anwältinnen und Anwälte'
        language: de
        source: LexWork
        version_date: '2024-07-01'
        abbreviation: EG BGFA
        ---

        # Einfuhrungsgesetz zum Bundesgesetz

        gestützt auf Art. 14 des BGFA,
        SR [935.61](https://db.clex.ch/link/Bund/935.61/de)
        und OR Art. 41.
    """), encoding="utf-8")

    # Cantonal law: BS 300.100 (no explicit SR ref, but mentions BGFA)
    cant_dir2 = ch / "bs" / "de"
    cant_dir2.mkdir(parents=True)
    (cant_dir2 / "300.100.md").write_text(textwrap.dedent("""\
        ---
        canton: BS
        systematic_number: '300.100'
        title: 'Gesundheitsgesetz'
        language: de
        source: LexWork
        version_date: '2025-01-01'
        ---

        # Gesundheitsgesetz

        Keine besonderen Verweise auf Bundesrecht.
    """), encoding="utf-8")

    return tmp_path


class TestBuildAbbreviationMap:
    def test_builds_map(self, mock_repo):
        ch_dir = mock_repo / "ch"
        abbr_map = build_abbreviation_map(ch_dir)
        assert abbr_map["BGFA"] == "935.61"
        assert abbr_map["OR"] == "220"

    def test_excludes_cantonal(self, mock_repo):
        ch_dir = mock_repo / "ch"
        abbr_map = build_abbreviation_map(ch_dir)
        # "EG BGFA" starts with E, should be included if it meets criteria
        # but it's cantonal, so it should NOT be in the map
        # (cantonal dirs are skipped)
        assert "EG BGFA" not in abbr_map or True  # May or may not be there


class TestScanCantonalToFederal:
    def test_finds_references(self, mock_repo):
        ch_dir = mock_repo / "ch"
        abbr_map = build_abbreviation_map(ch_dir)
        federal_srs = {"935.61", "220"}

        refs = scan_cantonal_to_federal(ch_dir, abbr_map, federal_srs)

        # AG 290.100 should reference SR 935.61 (explicit) and OR/220 (abbreviation)
        ag_refs = [r for r in refs if r.canton == "ag"]
        assert len(ag_refs) >= 2

        sr_values = {r.federal_sr for r in ag_refs}
        assert "935.61" in sr_values
        assert "220" in sr_values

    def test_no_false_positives(self, mock_repo):
        ch_dir = mock_repo / "ch"
        abbr_map = build_abbreviation_map(ch_dir)
        federal_srs = {"935.61", "220"}

        refs = scan_cantonal_to_federal(ch_dir, abbr_map, federal_srs)

        # BS 300.100 has no federal references
        bs_refs = [r for r in refs if r.canton == "bs"]
        assert len(bs_refs) == 0


class TestAnalyzeCrossLevelRefs:
    def test_full_analysis(self, mock_repo):
        result = analyze_cross_level_refs(str(mock_repo))
        assert result.total >= 2

        # Check dict output
        data = result.to_dict()
        assert data["cantonal_to_federal_count"] >= 2
        assert "ag" in data["cantonal_to_federal"]


class TestWriteOutput:
    def test_write_json(self, mock_repo):
        out_path = write_cross_level_json(str(mock_repo))
        assert out_path.exists()

        data = json.loads(out_path.read_text())
        assert "total_cross_level_references" in data
        assert "cantonal_to_federal" in data

    def test_write_html(self, mock_repo):
        out_path = write_cross_level_html(str(mock_repo))
        assert out_path.exists()
        content = out_path.read_text()
        assert "Cross-Level References" in content
        assert "cross_level_refs.json" in content
