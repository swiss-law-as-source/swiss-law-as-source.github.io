"""Tests for cantonal law fetcher and helpers."""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from legalize_ch.cantonal import (
    ALL_CANTONS,
    LEXFIND_ONLY_CANTONS,
    LEXWORK_CANTONS,
    CantonalFetcher,
    CantonalLawEntry,
    CantonalLawText,
    CantonalLawVersion,
    canton_to_path,
    cantonal_law_to_markdown,
)


# ─── Registry tests ──────────────────────────────────────────────────────────


class TestCantonRegistry:
    """Verify canton registry completeness and consistency."""

    def test_all_26_cantons(self):
        """All 26 Swiss cantons must be represented."""
        assert len(ALL_CANTONS) == 26

    def test_no_overlap(self):
        """LexWork and LexFind-only lists must not overlap."""
        overlap = set(LEXWORK_CANTONS.keys()) & set(LEXFIND_ONLY_CANTONS)
        assert overlap == set(), f"Cantons in both lists: {overlap}"

    def test_all_cantons_is_union(self):
        """ALL_CANTONS must be the union of LexWork + LexFind-only."""
        expected = sorted(list(LEXWORK_CANTONS.keys()) + LEXFIND_ONLY_CANTONS)
        assert ALL_CANTONS == expected

    def test_lexwork_count(self):
        """14 cantons have LexWork portals."""
        assert len(LEXWORK_CANTONS) == 14

    def test_lexfind_only_count(self):
        """12 cantons are LexFind-only."""
        assert len(LEXFIND_ONLY_CANTONS) == 12

    def test_canton_codes_are_lowercase(self):
        """All canton abbreviations must be lowercase."""
        for c in ALL_CANTONS:
            assert c == c.lower(), f"Canton code not lowercase: {c}"
            assert len(c) == 2, f"Canton code not 2 chars: {c}"

    def test_known_cantons_present(self):
        """Key cantons must be in the registry."""
        for canton in ["zh", "be", "ge", "ti", "vd", "bs", "ag", "lu"]:
            assert canton in ALL_CANTONS, f"Missing canton: {canton}"

    def test_lexwork_hosts_are_valid(self):
        """LexWork hosts should be proper domain names."""
        for canton, host in LEXWORK_CANTONS.items():
            assert "." in host, f"Invalid host for {canton}: {host}"
            assert not host.startswith("http"), f"Host should not include protocol: {host}"


# ─── Path helper tests ───────────────────────────────────────────────────────


class TestCantonToPath:
    def test_basic_path(self):
        assert canton_to_path("bs", "300.100", "de") == "ch/bs/de/300.100.md"

    def test_different_canton(self):
        assert canton_to_path("ag", "100.200", "de") == "ch/ag/de/100.200.md"

    def test_french_language(self):
        assert canton_to_path("ge", "A.1.1", "fr") == "ch/ge/fr/A.1.1.md"

    def test_italian_language(self):
        assert canton_to_path("ti", "1.1.1.1", "it") == "ch/ti/it/1.1.1.1.md"


# ─── Markdown conversion tests ───────────────────────────────────────────────


class TestCantonalLawToMarkdown:
    def test_basic_conversion(self):
        text = CantonalLawText(
            canton="bs",
            systematic_number="300.100",
            title="Gemeindegesetz",
            html_content="<p>Art. 1 Test</p>",
            language="de",
            version_date=date(2024, 1, 1),
            abbreviation="GemG",
        )
        md = cantonal_law_to_markdown(text)
        assert "---" in md
        assert "canton: BS" in md
        assert "systematic_number: '300.100'" in md or "systematic_number: 300.100" in md
        assert "Gemeindegesetz" in md
        assert "version_date: '2024-01-01'" in md or "version_date: 2024-01-01" in md
        assert "abbreviation: GemG" in md
        assert "source: LexWork" in md  # BS is a LexWork canton

    def test_lexfind_source(self):
        text = CantonalLawText(
            canton="zh",
            systematic_number="100.1",
            title="Verfassung",
            html_content="<p>Test</p>",
            language="de",
        )
        md = cantonal_law_to_markdown(text)
        assert "source: LexFind" in md  # ZH is not in LexWork

    def test_no_content_placeholder(self):
        text = CantonalLawText(
            canton="ag",
            systematic_number="100.100",
            title="Test Law",
            html_content="",
            language="de",
        )
        md = cantonal_law_to_markdown(text)
        assert "No text content available" in md

    def test_no_version_date(self):
        text = CantonalLawText(
            canton="ag",
            systematic_number="100.100",
            title="Test",
            html_content="<p>Content</p>",
            language="de",
        )
        md = cantonal_law_to_markdown(text)
        assert "version_date" not in md


# ─── Fetcher tests ────────────────────────────────────────────────────────────


class TestCantonalFetcher:
    """Test CantonalFetcher with mocked HTTP responses."""

    def _make_lexwork_response(self):
        return {
            "text_of_law": {
                "title": "Gemeindegesetz",
                "abbreviation": "GemG",
                "systematic_number": "300.100",
                "enactment": "2005-06-01",
                "publication_enactment": "2024-01-01",
                "selected_version": {
                    "xhtml_tol": "<div><p>Art. 1 Geltungsbereich</p></div>",
                    "version_dates_str": "In Kraft seit: 01.01.2024",
                },
                "current_version": {"id": 42, "title": "Gemeindegesetz"},
                "old_versions": [
                    {
                        "id": 41,
                        "title": "Gemeindegesetz",
                        "version_dates_str": "In Kraft seit: 01.06.2020",
                    }
                ],
            }
        }

    @patch.object(CantonalFetcher, "_get_json")
    def test_fetch_from_lexwork(self, mock_json):
        mock_json.return_value = self._make_lexwork_response()
        fetcher = CantonalFetcher(rate_limit=0)
        result = fetcher.fetch_law_text("bs", "300.100", "de")

        assert result is not None
        assert result.canton == "bs"
        assert result.systematic_number == "300.100"
        assert result.title == "Gemeindegesetz"
        assert "Art. 1" in result.html_content
        assert result.version_date == date(2024, 1, 1)

    @patch.object(CantonalFetcher, "_get_json")
    def test_fetch_versions(self, mock_json):
        mock_json.return_value = self._make_lexwork_response()
        fetcher = CantonalFetcher(rate_limit=0)
        versions = fetcher.fetch_versions("bs", "300.100")

        assert len(versions) == 2
        # Current version
        assert versions[0].version_id == 42
        # Old version
        assert versions[1].version_id == 41
        assert versions[1].date_in_force == date(2020, 6, 1)

    @patch.object(CantonalFetcher, "_get_json")
    def test_fetch_lexwork_returns_none_on_404(self, mock_json):
        mock_json.return_value = None
        fetcher = CantonalFetcher(rate_limit=0)
        result = fetcher.fetch_law_text("bs", "999.999", "de")
        assert result is None

    @patch.object(CantonalFetcher, "_get_json")
    def test_fetch_versions_non_lexwork_canton(self, mock_json):
        """Non-LexWork cantons return empty version list."""
        fetcher = CantonalFetcher(rate_limit=0)
        versions = fetcher.fetch_versions("zh", "100.1")
        assert versions == []
        mock_json.assert_not_called()

    @patch.object(CantonalFetcher, "_get_json")
    def test_lexwork_base_url(self, mock_json):
        fetcher = CantonalFetcher(rate_limit=0)
        base = fetcher._lexwork_base("bs")
        assert base == "https://www.gesetzessammlung.bs.ch/api"

    @patch.object(CantonalFetcher, "_get_json")
    def test_lexfind_fallback_for_non_lexwork(self, mock_json):
        """For non-LexWork cantons, should try LexFind if lexfind_id provided."""
        mock_json.return_value = None
        fetcher = CantonalFetcher(rate_limit=0)

        with patch.object(fetcher, "_fetch_from_lexfind", return_value=None) as mock_lf:
            result = fetcher.fetch_law_text("zh", "100.1", "de", lexfind_id="12345")
            mock_lf.assert_called_once_with("zh", "100.1", "12345", "de")


# ─── Model tests ─────────────────────────────────────────────────────────────


class TestCantonalModels:
    def test_law_entry_defaults(self):
        entry = CantonalLawEntry(canton="bs", systematic_number="300.100", title="Test")
        assert entry.abbreviation == ""
        assert entry.enactment_date is None
        assert entry.is_active is True
        assert entry.lexfind_id == ""

    def test_law_version_fields(self):
        v = CantonalLawVersion(
            canton="ag",
            systematic_number="100.1",
            version_id=5,
            title="Test",
            date_in_force=date(2023, 1, 1),
        )
        assert v.version_id == 5
        assert v.date_in_force == date(2023, 1, 1)

    def test_law_text_defaults(self):
        t = CantonalLawText(canton="zh", systematic_number="100.1", title="Test")
        assert t.html_content == ""
        assert t.language == "de"
        assert t.version_date is None
