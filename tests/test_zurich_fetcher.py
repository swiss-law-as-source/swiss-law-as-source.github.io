"""Tests for the Zürich cantonal law fetcher (ZHLex API)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from legalize_ch.zurich_fetcher import (
    CATALOG_URL,
    ERLASS_URL,
    VERSIONS_URL,
    ZHLEX_API,
    ZurichFetcher,
    _extract_items,
    _parse_catalog_entry,
    _parse_date_field,
    _parse_law_text,
    _parse_version,
)
from legalize_ch.cantonal import (
    CantonalFetcher,
    CantonalLawEntry,
    CantonalLawText,
    CantonalLawVersion,
    cantonal_law_to_markdown,
)


# ─── Helper: mock API responses ─────────────────────────────────────────────


def _make_catalog_response(count: int = 3) -> dict:
    """Build a mock ZHLex catalog API response."""
    items = []
    for i in range(1, count + 1):
        items.append({
            "id": 1000 + i,
            "lsNummer": f"131.{i}",
            "titel": f"Testgesetz Nr. {i}",
            "abkuerzung": f"TG{i}",
            "erlassDatum": f"200{i}-01-01",
            "inKraft": True,
        })
    return {
        "erlasse": items,
        "total": count,
    }


def _make_erlass_text_response() -> dict:
    """Build a mock ZHLex law text API response."""
    return {
        "erlasstext": {
            "titel": "Kantonsverfassung",
            "abkuerzung": "KV",
            "htmlContent": "<div><h1>Verfassung des Kantons Zürich</h1><p>Art. 1 Der Kanton Zürich ist ein Gliedstaat.</p></div>",
            "inkrafttretungsDatum": "2006-01-01",
        }
    }


def _make_versions_response() -> dict:
    """Build a mock ZHLex versions API response."""
    return {
        "versionen": [
            {
                "id": 5001,
                "titel": "Kantonsverfassung",
                "inkrafttretungsDatum": "2006-01-01",
                "abkuerzung": "KV",
            },
            {
                "id": 5002,
                "titel": "Kantonsverfassung",
                "inkrafttretungsDatum": "2010-07-01",
                "abkuerzung": "KV",
            },
            {
                "id": 5003,
                "titel": "Kantonsverfassung",
                "inkrafttretungsDatum": "2024-01-01",
                "abkuerzung": "KV",
            },
        ]
    }


# ─── Parsing helpers ─────────────────────────────────────────────────────────


class TestExtractItems:
    def test_bare_list(self):
        assert _extract_items([{"a": 1}]) == [{"a": 1}]

    def test_erlasse_key(self):
        assert _extract_items({"erlasse": [{"a": 1}]}) == [{"a": 1}]

    def test_items_key(self):
        assert _extract_items({"items": [{"a": 1}]}) == [{"a": 1}]

    def test_results_key(self):
        assert _extract_items({"results": [{"a": 1}]}) == [{"a": 1}]

    def test_empty_dict(self):
        assert _extract_items({}) == []

    def test_data_key(self):
        assert _extract_items({"data": [{"a": 1}]}) == [{"a": 1}]


class TestParseDateField:
    def test_iso_date(self):
        assert _parse_date_field("2024-01-15") == date(2024, 1, 15)

    def test_iso_datetime(self):
        assert _parse_date_field("2024-01-15T00:00:00") == date(2024, 1, 15)

    def test_swiss_format(self):
        assert _parse_date_field("15.01.2024") == date(2024, 1, 15)

    def test_empty_string(self):
        assert _parse_date_field("") is None

    def test_none(self):
        assert _parse_date_field(None) is None

    def test_date_object(self):
        d = date(2024, 6, 1)
        assert _parse_date_field(d) == d

    def test_invalid_string(self):
        assert _parse_date_field("not-a-date") is None

    def test_unix_timestamp_ms(self):
        # 2024-01-01 00:00:00 UTC = 1704067200000 ms
        result = _parse_date_field("1704067200000")
        assert result is not None
        assert result.year == 2024


class TestParseCatalogEntry:
    def test_basic_entry(self):
        item = {
            "id": 42,
            "lsNummer": "131.1",
            "titel": "Kantonsverfassung",
            "abkuerzung": "KV",
            "erlassDatum": "2005-02-27",
            "inKraft": True,
        }
        entry = _parse_catalog_entry(item)
        assert entry is not None
        assert entry.canton == "zh"
        assert entry.systematic_number == "131.1"
        assert entry.title == "Kantonsverfassung"
        assert entry.abbreviation == "KV"
        assert entry.enactment_date == date(2005, 2, 27)
        assert entry.is_active is True
        assert entry.lexfind_id == "42"

    def test_missing_number_returns_none(self):
        assert _parse_catalog_entry({"titel": "Test"}) is None

    def test_alternative_keys(self):
        item = {
            "systematicNumber": "700.1",
            "title": "Planungs- und Baugesetz",
            "abbreviation": "PBG",
            "erlassId": 99,
            "enactmentDate": "1975-09-07",
            "inForce": True,
        }
        entry = _parse_catalog_entry(item)
        assert entry is not None
        assert entry.systematic_number == "700.1"
        assert entry.title == "Planungs- und Baugesetz"
        assert entry.lexfind_id == "99"


class TestParseLawText:
    def test_basic_text(self):
        data = _make_erlass_text_response()
        text = _parse_law_text(data, "131.1", "de")
        assert text is not None
        assert text.canton == "zh"
        assert text.systematic_number == "131.1"
        assert text.title == "Kantonsverfassung"
        assert text.abbreviation == "KV"
        assert "Art. 1" in text.html_content
        assert text.version_date == date(2006, 1, 1)
        assert text.language == "de"

    def test_no_content_returns_none(self):
        data = {"erlasstext": {}}
        assert _parse_law_text(data, "999.1", "de") is None

    def test_flat_response(self):
        """Handle response without nesting."""
        data = {
            "titel": "Testgesetz",
            "htmlContent": "<p>Test</p>",
            "inkrafttretungsDatum": "2020-06-01",
        }
        text = _parse_law_text(data, "100.1", "de")
        assert text is not None
        assert text.title == "Testgesetz"

    def test_fallback_to_enactment_date(self):
        data = {
            "erlasstext": {
                "titel": "Test",
                "htmlContent": "<p>X</p>",
                "erlassDatum": "1990-01-01",
            }
        }
        text = _parse_law_text(data, "100.1", "de")
        assert text is not None
        assert text.version_date == date(1990, 1, 1)


class TestParseVersion:
    def test_basic_version(self):
        item = {
            "id": 5001,
            "titel": "Kantonsverfassung",
            "inkrafttretungsDatum": "2006-01-01",
            "abkuerzung": "KV",
        }
        v = _parse_version(item, "131.1")
        assert v is not None
        assert v.canton == "zh"
        assert v.systematic_number == "131.1"
        assert v.version_id == 5001
        assert v.title == "Kantonsverfassung"
        assert v.date_in_force == date(2006, 1, 1)

    def test_missing_id_returns_none(self):
        assert _parse_version({"titel": "Test"}, "100.1") is None


# ─── ZurichFetcher ───────────────────────────────────────────────────────────


class TestZurichFetcher:
    """Test ZurichFetcher with mocked HTTP."""

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_catalog(self, mock_get):
        mock_get.return_value = _make_catalog_response(3)
        fetcher = ZurichFetcher(rate_limit=0)
        entries = fetcher.fetch_catalog("de")

        assert len(entries) == 3
        assert entries[0].canton == "zh"
        assert entries[0].systematic_number == "131.1"
        assert entries[0].title == "Testgesetz Nr. 1"
        mock_get.assert_called()

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_catalog_pagination(self, mock_get):
        """Catalog should stop when items < page_size."""
        mock_get.return_value = _make_catalog_response(2)
        fetcher = ZurichFetcher(rate_limit=0)
        entries = fetcher.fetch_catalog("de")

        assert len(entries) == 2
        # Only one API call since 2 < 100 (page_size)
        assert mock_get.call_count == 1

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_catalog_empty(self, mock_get):
        mock_get.return_value = None
        fetcher = ZurichFetcher(rate_limit=0)
        entries = fetcher.fetch_catalog()
        assert entries == []

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_law_text_with_id(self, mock_get):
        mock_get.return_value = _make_erlass_text_response()
        fetcher = ZurichFetcher(rate_limit=0)
        text = fetcher.fetch_law_text("131.1", "de", erlass_id="42")

        assert text is not None
        assert text.title == "Kantonsverfassung"
        assert text.canton == "zh"
        assert "Art. 1" in text.html_content

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_law_text_resolves_id(self, mock_get):
        """When erlass_id is not given, fetcher searches catalog first."""
        catalog_response = {
            "erlasse": [{"id": 42, "lsNummer": "131.1", "titel": "KV"}],
            "total": 1,
        }
        text_response = _make_erlass_text_response()

        mock_get.side_effect = [catalog_response, text_response]
        fetcher = ZurichFetcher(rate_limit=0)
        text = fetcher.fetch_law_text("131.1", "de")

        assert text is not None
        assert text.title == "Kantonsverfassung"
        assert mock_get.call_count == 2

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_law_text_not_found(self, mock_get):
        mock_get.return_value = None
        fetcher = ZurichFetcher(rate_limit=0)
        text = fetcher.fetch_law_text("999.999", "de")
        assert text is None

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_versions(self, mock_get):
        # First call: resolve erlass_id, second: fetch versions
        catalog_response = {
            "erlasse": [{"id": 42, "lsNummer": "131.1", "titel": "KV"}],
            "total": 1,
        }
        mock_get.side_effect = [catalog_response, _make_versions_response()]
        fetcher = ZurichFetcher(rate_limit=0)
        versions = fetcher.fetch_versions("131.1")

        assert len(versions) == 3
        assert versions[0].date_in_force == date(2006, 1, 1)
        assert versions[1].date_in_force == date(2010, 7, 1)
        assert versions[2].date_in_force == date(2024, 1, 1)
        assert all(v.canton == "zh" for v in versions)

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_versions_with_id(self, mock_get):
        mock_get.return_value = _make_versions_response()
        fetcher = ZurichFetcher(rate_limit=0)
        versions = fetcher.fetch_versions("131.1", erlass_id="42")

        assert len(versions) == 3
        # Should only call versions endpoint (no catalog lookup)
        mock_get.assert_called_once()

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_versions_empty(self, mock_get):
        mock_get.return_value = None
        fetcher = ZurichFetcher(rate_limit=0)
        versions = fetcher.fetch_versions("131.1", erlass_id="42")
        assert versions == []

    @patch.object(ZurichFetcher, "_get")
    def test_fetch_version_text(self, mock_get):
        mock_get.return_value = _make_erlass_text_response()
        fetcher = ZurichFetcher(rate_limit=0)
        text = fetcher.fetch_version_text("131.1", 5001)

        assert text is not None
        assert text.systematic_number == "131.1"
        assert text.version_date == date(2006, 1, 1)


# ─── Integration: CantonalFetcher → ZurichFetcher ────────────────────────────


class TestCantonalFetcherZhIntegration:
    """Verify that CantonalFetcher delegates to ZurichFetcher for ZH."""

    @patch("legalize_ch.zurich_fetcher.ZurichFetcher")
    def test_fetch_law_text_delegates_to_zh(self, MockZH):
        """CantonalFetcher.fetch_law_text('zh', ...) uses ZurichFetcher."""
        mock_instance = MockZH.return_value
        mock_instance.fetch_law_text.return_value = CantonalLawText(
            canton="zh", systematic_number="131.1", title="KV",
            html_content="<p>Test</p>", language="de",
        )
        fetcher = CantonalFetcher(rate_limit=0)
        result = fetcher.fetch_law_text("zh", "131.1", "de")

        assert result is not None
        assert result.canton == "zh"
        mock_instance.fetch_law_text.assert_called_once_with(
            "131.1", "de", erlass_id=""
        )

    @patch("legalize_ch.zurich_fetcher.ZurichFetcher")
    def test_fetch_versions_delegates_to_zh(self, MockZH):
        """CantonalFetcher.fetch_versions('zh', ...) uses ZurichFetcher."""
        mock_instance = MockZH.return_value
        mock_instance.fetch_versions.return_value = [
            CantonalLawVersion(canton="zh", systematic_number="131.1",
                               version_id=1, title="KV",
                               date_in_force=date(2006, 1, 1))
        ]
        fetcher = CantonalFetcher(rate_limit=0)
        versions = fetcher.fetch_versions("zh", "131.1")

        assert len(versions) == 1
        mock_instance.fetch_versions.assert_called_once_with("131.1")

    @patch("legalize_ch.zurich_fetcher.ZurichFetcher")
    def test_fetch_catalog_delegates_to_zh(self, MockZH):
        """CantonalFetcher.fetch_lexwork_catalog('zh', ...) uses ZurichFetcher."""
        mock_instance = MockZH.return_value
        mock_instance.fetch_catalog.return_value = [
            CantonalLawEntry(canton="zh", systematic_number="131.1",
                             title="KV")
        ]
        fetcher = CantonalFetcher(rate_limit=0)
        entries = fetcher.fetch_lexwork_catalog("zh", "de")

        assert len(entries) == 1
        mock_instance.fetch_catalog.assert_called_once_with("de")


# ─── Markdown output ─────────────────────────────────────────────────────────


class TestZhMarkdownOutput:
    def test_zh_law_to_markdown(self):
        """ZH laws should have source: ZHLex in frontmatter."""
        text = CantonalLawText(
            canton="zh",
            systematic_number="131.1",
            title="Verfassung des Kantons Zürich",
            html_content="<p>Art. 1 Test</p>",
            language="de",
            version_date=date(2006, 1, 1),
            abbreviation="KV",
        )
        md = cantonal_law_to_markdown(text)
        assert "---" in md
        assert "canton: ZH" in md
        assert "source: ZHLex" in md
        assert "Verfassung des Kantons Zürich" in md
        assert "abbreviation: KV" in md
