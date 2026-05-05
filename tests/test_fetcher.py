"""Unit tests for the fetcher module (mock SPARQL responses)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from legalize_ch.fetcher import FedlexFetcher, LANG_MAP
from legalize_ch.models import LawEntry, LawVersion


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fetcher():
    """Create a FedlexFetcher with rate limiting disabled."""
    f = FedlexFetcher(rate_limit=0.0)
    return f


# ---------------------------------------------------------------------------
# Helper: mock SPARQL bindings
# ---------------------------------------------------------------------------

def _binding(vals: dict[str, str]) -> dict:
    """Build a SPARQL result binding from a simple dict."""
    return {k: {"value": v} for k, v in vals.items()}


CATALOG_ROW_1 = _binding({
    "cc": "https://fedlex.data.admin.ch/eli/cc/1999/404",
    "srNumber": "101",
    "titleDe": "Bundesverfassung der Schweizerischen Eidgenossenschaft",
    "titleFr": "Constitution f\u00e9d\u00e9rale de la Conf\u00e9d\u00e9ration suisse",
    "titleIt": "Costituzione federale della Confederazione Svizzera",
    "dateDoc": "1999-04-18",
    "dateForce": "2000-01-01",
    "abbrDe": "BV",
    "abbrFr": "Cst.",
    "abbrIt": "Cost.",
})

CATALOG_ROW_2 = _binding({
    "cc": "https://fedlex.data.admin.ch/eli/cc/27/317_321_377",
    "srNumber": "210",
    "titleDe": "Schweizerisches Zivilgesetzbuch",
    "titleFr": "Code civil suisse",
    "titleIt": "Codice civile svizzero",
    "dateDoc": "1907-12-10",
    "dateForce": "1912-01-01",
    "abbrDe": "ZGB",
    "abbrFr": "CC",
    "abbrIt": "CC",
})

VERSIONS_ROWS = [
    _binding({
        "cons": "https://fedlex.data.admin.ch/eli/cc/1999/404/20000101",
        "dateApp": "2000-01-01",
    }),
    _binding({
        "cons": "https://fedlex.data.admin.ch/eli/cc/1999/404/20140101",
        "dateApp": "2014-01-01",
    }),
    _binding({
        "cons": "https://fedlex.data.admin.ch/eli/cc/1999/404/20240101",
        "dateApp": "2024-01-01",
    }),
]

TEXT_ROW_XML = _binding({
    "title": "Bundesverfassung der Schweizerischen Eidgenossenschaft",
    "fileUrl": "https://fedlex.data.admin.ch/filestore/eli/cc/1999/404/20240101/de/xml/fedlex-data.xml",
})

TEXT_ROW_HTML = _binding({
    "title": "Bundesverfassung der Schweizerischen Eidgenossenschaft",
    "fileUrl": "https://fedlex.data.admin.ch/filestore/eli/cc/1999/404/20240101/de/html/fedlex-data.html",
})

TITLE_ONLY_ROW = _binding({
    "title": "Bundesverfassung der Schweizerischen Eidgenossenschaft",
})


# ---------------------------------------------------------------------------
# Tests: fetch_catalog
# ---------------------------------------------------------------------------

class TestFetchCatalog:
    def test_parses_catalog_rows(self, fetcher):
        with patch.object(fetcher, "_query", return_value=[CATALOG_ROW_1, CATALOG_ROW_2]):
            entries = fetcher.fetch_catalog()

        assert len(entries) == 2
        assert entries[0].sr_number == "101"
        assert entries[0].uri == "https://fedlex.data.admin.ch/eli/cc/1999/404"
        assert entries[0].title_de == "Bundesverfassung der Schweizerischen Eidgenossenschaft"
        assert entries[0].title_fr == "Constitution f\u00e9d\u00e9rale de la Conf\u00e9d\u00e9ration suisse"
        assert entries[0].title_it == "Costituzione federale della Confederazione Svizzera"
        assert entries[0].date_document == date(1999, 4, 18)
        assert entries[0].date_in_force == date(2000, 1, 1)
        assert entries[0].abbreviation_de == "BV"
        assert entries[1].sr_number == "210"
        assert entries[1].abbreviation_fr == "CC"

    def test_deduplicates_by_sr_number(self, fetcher):
        """Duplicate SR numbers should be collapsed to a single entry."""
        duplicate = _binding({
            "cc": "https://fedlex.data.admin.ch/eli/cc/1999/404",
            "srNumber": "101",
            "titleDe": "BV duplicate row",
        })
        with patch.object(fetcher, "_query", return_value=[CATALOG_ROW_1, duplicate]):
            entries = fetcher.fetch_catalog()

        assert len(entries) == 1
        assert entries[0].title_de == "Bundesverfassung der Schweizerischen Eidgenossenschaft"

    def test_empty_response(self, fetcher):
        with patch.object(fetcher, "_query", return_value=[]):
            entries = fetcher.fetch_catalog()
        assert entries == []

    def test_missing_optional_fields(self, fetcher):
        """Row with only required fields should still parse."""
        minimal = _binding({
            "cc": "https://fedlex.data.admin.ch/eli/cc/2020/123",
            "srNumber": "999.1",
        })
        with patch.object(fetcher, "_query", return_value=[minimal]):
            entries = fetcher.fetch_catalog()

        assert len(entries) == 1
        assert entries[0].sr_number == "999.1"
        assert entries[0].title_de == ""
        assert entries[0].date_document is None
        assert entries[0].date_in_force is None

    def test_limit_parameter(self, fetcher):
        """Limit parameter should be appended to query."""
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            fetcher.fetch_catalog(limit=5)
            query_text = mock_q.call_args[0][0]
            assert "LIMIT 5" in query_text


# ---------------------------------------------------------------------------
# Tests: fetch_modified_since
# ---------------------------------------------------------------------------

class TestFetchModifiedSince:
    def test_parses_modified_rows(self, fetcher):
        with patch.object(fetcher, "_query", return_value=[CATALOG_ROW_1]):
            entries = fetcher.fetch_modified_since(date(2024, 1, 1))

        assert len(entries) == 1
        assert entries[0].sr_number == "101"

    def test_since_date_in_query(self, fetcher):
        """The since_date should appear in the SPARQL query."""
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            fetcher.fetch_modified_since(date(2025, 3, 15))
            query_text = mock_q.call_args[0][0]
            assert "2025-03-15" in query_text

    def test_deduplicates(self, fetcher):
        rows = [CATALOG_ROW_1, CATALOG_ROW_1]
        with patch.object(fetcher, "_query", return_value=rows):
            entries = fetcher.fetch_modified_since(date(2020, 1, 1))
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# Tests: fetch_versions
# ---------------------------------------------------------------------------

class TestFetchVersions:
    def test_parses_versions(self, fetcher):
        law = LawEntry(
            sr_number="101",
            uri="https://fedlex.data.admin.ch/eli/cc/1999/404",
        )
        with patch.object(fetcher, "_query", return_value=VERSIONS_ROWS):
            versions = fetcher.fetch_versions(law)

        assert len(versions) == 3
        assert versions[0].date_applicable == date(2000, 1, 1)
        assert versions[1].date_applicable == date(2014, 1, 1)
        assert versions[2].date_applicable == date(2024, 1, 1)
        assert versions[0].sr_number == "101"
        assert "20000101" in versions[0].version_uri

    def test_sorted_by_date(self, fetcher):
        """Versions should be sorted by date even if input is unordered."""
        law = LawEntry(sr_number="101", uri="https://fedlex.data.admin.ch/eli/cc/1999/404")
        reversed_rows = list(reversed(VERSIONS_ROWS))
        with patch.object(fetcher, "_query", return_value=reversed_rows):
            versions = fetcher.fetch_versions(law)

        dates = [v.date_applicable for v in versions]
        assert dates == sorted(dates)

    def test_skips_invalid_dates(self, fetcher):
        """Rows with invalid/missing dates should be skipped."""
        law = LawEntry(sr_number="101", uri="https://fedlex.data.admin.ch/eli/cc/1999/404")
        rows = [
            _binding({"cons": "https://example.com/c1", "dateApp": "not-a-date"}),
            _binding({"cons": "https://example.com/c2"}),  # missing dateApp
            VERSIONS_ROWS[0],
        ]
        with patch.object(fetcher, "_query", return_value=rows):
            versions = fetcher.fetch_versions(law)

        assert len(versions) == 1
        assert versions[0].date_applicable == date(2000, 1, 1)

    def test_empty_versions(self, fetcher):
        law = LawEntry(sr_number="101", uri="https://fedlex.data.admin.ch/eli/cc/1999/404")
        with patch.object(fetcher, "_query", return_value=[]):
            versions = fetcher.fetch_versions(law)
        assert versions == []

    def test_uri_formatting(self, fetcher):
        """The law's URI should appear in the SPARQL query."""
        law = LawEntry(sr_number="210", uri="https://fedlex.data.admin.ch/eli/cc/27/317_321_377")
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            fetcher.fetch_versions(law)
            query_text = mock_q.call_args[0][0]
            assert "eli/cc/27/317_321_377" in query_text


# ---------------------------------------------------------------------------
# Tests: fetch_text
# ---------------------------------------------------------------------------

class TestFetchText:
    def test_xml_content(self, fetcher):
        """XML content is detected and placed in xml_content field."""
        version = LawVersion(
            sr_number="101",
            version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20240101",
            date_applicable=date(2024, 1, 1),
        )
        xml_body = '<?xml version="1.0"?><akomaNtoso><act><body><p>Test</p></body></act></akomaNtoso>'

        with patch.object(fetcher, "_query", return_value=[TEXT_ROW_XML]):
            with patch.object(fetcher, "_fetch_url", return_value=xml_body):
                result = fetcher.fetch_text(version, "de")

        assert result is not None
        assert result.sr_number == "101"
        assert result.language == "de"
        assert result.version_date == date(2024, 1, 1)
        assert result.title == "Bundesverfassung der Schweizerischen Eidgenossenschaft"
        assert result.xml_content == xml_body
        assert result.html_content == ""

    def test_html_content(self, fetcher):
        """HTML content is placed in html_content field."""
        version = LawVersion(
            sr_number="101",
            version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20240101",
            date_applicable=date(2024, 1, 1),
        )
        html_body = "<html><body><h1>Title</h1><p>Content</p></body></html>"

        with patch.object(fetcher, "_query", return_value=[TEXT_ROW_HTML]):
            with patch.object(fetcher, "_fetch_url", return_value=html_body):
                result = fetcher.fetch_text(version, "de")

        assert result is not None
        assert result.html_content == html_body
        assert result.xml_content == ""

    def test_xml_fallback_to_html_query(self, fetcher):
        """If XML query returns nothing, HTML query is attempted."""
        version = LawVersion(
            sr_number="101",
            version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20240101",
            date_applicable=date(2024, 1, 1),
        )
        html_body = "<p>HTML fallback content</p>"

        call_count = [0]
        def mock_query(query_text):
            call_count[0] += 1
            if call_count[0] == 1:
                return []  # XML query returns nothing
            return [TEXT_ROW_HTML]  # HTML query succeeds

        with patch.object(fetcher, "_query", side_effect=mock_query):
            with patch.object(fetcher, "_fetch_url", return_value=html_body):
                result = fetcher.fetch_text(version, "de")

        assert result is not None
        assert result.html_content == html_body
        assert call_count[0] == 2

    def test_title_only_fallback(self, fetcher):
        """If both XML and HTML queries fail, title-only query is used."""
        version = LawVersion(
            sr_number="101",
            version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20240101",
            date_applicable=date(2024, 1, 1),
        )

        call_count = [0]
        def mock_query(query_text):
            call_count[0] += 1
            if call_count[0] <= 2:
                return []  # XML and HTML queries return nothing
            return [TITLE_ONLY_ROW]  # Title query succeeds

        with patch.object(fetcher, "_query", side_effect=mock_query):
            result = fetcher.fetch_text(version, "de")

        assert result is not None
        assert result.title == "Bundesverfassung der Schweizerischen Eidgenossenschaft"
        assert result.html_content == ""
        assert result.xml_content == ""
        assert call_count[0] == 3

    def test_no_content_returns_none(self, fetcher):
        """If all queries return nothing, None is returned."""
        version = LawVersion(
            sr_number="101",
            version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20240101",
            date_applicable=date(2024, 1, 1),
        )

        with patch.object(fetcher, "_query", return_value=[]):
            result = fetcher.fetch_text(version, "de")

        assert result is None

    def test_language_mapping(self, fetcher):
        """Different languages use correct lang codes in query."""
        version = LawVersion(
            sr_number="101",
            version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20240101",
            date_applicable=date(2024, 1, 1),
        )

        for lang, (lang_code, lang_upper) in LANG_MAP.items():
            with patch.object(fetcher, "_query", return_value=[]) as mock_q:
                fetcher.fetch_text(version, lang)
                query_text = mock_q.call_args[0][0]
                assert lang_upper in query_text

    def test_content_url_stored(self, fetcher):
        """The content URL is stored in the result."""
        version = LawVersion(
            sr_number="101",
            version_uri="https://fedlex.data.admin.ch/eli/cc/1999/404/20240101",
            date_applicable=date(2024, 1, 1),
        )
        html_body = "<p>Content</p>"

        with patch.object(fetcher, "_query", return_value=[TEXT_ROW_HTML]):
            with patch.object(fetcher, "_fetch_url", return_value=html_body):
                result = fetcher.fetch_text(version, "de")

        assert result.content_url == TEXT_ROW_HTML["fileUrl"]["value"]


# ---------------------------------------------------------------------------
# Tests: _query retry logic
# ---------------------------------------------------------------------------

class TestQueryRetry:
    def test_retries_on_transient_error(self, fetcher):
        """Transient errors trigger retries with backoff."""
        call_count = [0]

        def mock_query_convert():
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("HTTP Error 503: Service Unavailable")
            return {"results": {"bindings": [CATALOG_ROW_1]}}

        fetcher.sparql.setQuery = MagicMock()
        mock_result = MagicMock()
        mock_result.convert = mock_query_convert
        fetcher.sparql.query = MagicMock(return_value=mock_result)

        with patch("legalize_ch.fetcher.time.sleep"):
            result = fetcher._query("SELECT ?x WHERE { ?x a ?y }")

        assert len(result) == 1
        assert call_count[0] == 3

    def test_non_retryable_error_fails_immediately(self, fetcher):
        """Non-retryable errors are not retried."""
        call_count = [0]

        def mock_query_convert():
            call_count[0] += 1
            raise Exception("HTTP Error 400: Bad Request")

        fetcher.sparql.setQuery = MagicMock()
        mock_result = MagicMock()
        mock_result.convert = mock_query_convert
        fetcher.sparql.query = MagicMock(return_value=mock_result)

        with patch("legalize_ch.fetcher.time.sleep"):
            result = fetcher._query("SELECT ?x WHERE { ?x a ?y }")

        assert result == []
        assert call_count[0] == 1

    def test_returns_empty_after_max_retries(self, fetcher):
        """After MAX_RETRIES, returns empty list."""
        def mock_query_convert():
            raise Exception("HTTP Error 429: Too Many Requests")

        fetcher.sparql.setQuery = MagicMock()
        mock_result = MagicMock()
        mock_result.convert = mock_query_convert
        fetcher.sparql.query = MagicMock(return_value=mock_result)

        with patch("legalize_ch.fetcher.time.sleep"):
            result = fetcher._query("SELECT ?x WHERE { ?x a ?y }")

        assert result == []


# ---------------------------------------------------------------------------
# Tests: _fetch_url retry logic
# ---------------------------------------------------------------------------

class TestFetchUrlRetry:
    def test_retries_on_503(self, fetcher):
        """HTTP 503 triggers retry."""
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 503

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.text = "<p>Success</p>"
        mock_resp_ok.raise_for_status = MagicMock()

        fetcher.session.get = MagicMock(side_effect=[mock_resp_fail, mock_resp_ok])

        with patch("legalize_ch.fetcher.time.sleep"):
            result = fetcher._fetch_url("https://example.com/test.xml")

        assert result == "<p>Success</p>"
        assert fetcher.session.get.call_count == 2

    def test_returns_empty_on_persistent_failure(self, fetcher):
        """Persistent failures return empty string."""
        import requests

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status = MagicMock(
            side_effect=requests.exceptions.HTTPError("500 Server Error")
        )

        fetcher.session.get = MagicMock(return_value=mock_resp)

        with patch("legalize_ch.fetcher.time.sleep"):
            result = fetcher._fetch_url("https://example.com/fail.xml")

        assert result == ""


# ---------------------------------------------------------------------------
# Tests: _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_valid_date(self, fetcher):
        assert fetcher._parse_date("2024-01-15") == date(2024, 1, 15)

    def test_date_with_time(self, fetcher):
        """Date string with time component still parses correctly."""
        assert fetcher._parse_date("2024-01-15T00:00:00Z") == date(2024, 1, 15)

    def test_none_input(self, fetcher):
        assert fetcher._parse_date(None) is None

    def test_empty_string(self, fetcher):
        assert fetcher._parse_date("") is None

    def test_invalid_date(self, fetcher):
        assert fetcher._parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# Tests: _get_val
# ---------------------------------------------------------------------------

class TestGetVal:
    def test_existing_key(self, fetcher):
        row = {"name": {"value": "hello"}}
        assert fetcher._get_val(row, "name") == "hello"

    def test_missing_key(self, fetcher):
        row = {"name": {"value": "hello"}}
        assert fetcher._get_val(row, "other") == ""

    def test_empty_row(self, fetcher):
        assert fetcher._get_val({}, "name") == ""


# ---------------------------------------------------------------------------
# Tests: throttle
# ---------------------------------------------------------------------------

class TestThrottle:
    def test_throttle_sleeps_when_too_fast(self):
        """Throttle should sleep if called too quickly."""
        f = FedlexFetcher(rate_limit=1.0)
        f._last_request = 9999999999.0  # far future

        with patch("legalize_ch.fetcher.time.sleep") as mock_sleep:
            with patch("legalize_ch.fetcher.time.time", return_value=9999999999.5):
                f._throttle()
                mock_sleep.assert_called_once_with(pytest.approx(0.5, abs=0.01))

    def test_no_throttle_when_enough_time_passed(self):
        """Throttle should not sleep if enough time has passed."""
        f = FedlexFetcher(rate_limit=1.0)
        f._last_request = 0.0

        with patch("legalize_ch.fetcher.time.sleep") as mock_sleep:
            with patch("legalize_ch.fetcher.time.time", return_value=100.0):
                f._throttle()
                mock_sleep.assert_not_called()
