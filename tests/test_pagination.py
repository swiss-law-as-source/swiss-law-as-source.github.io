"""Tests for SPARQL query partitioning + pagination."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from legalize_ch.fetcher import FedlexFetcher, PAGE_SIZE


def _binding(vals: dict[str, str]) -> dict:
    return {k: {"value": v} for k, v in vals.items()}


@pytest.fixture
def fetcher():
    return FedlexFetcher(rate_limit=0.0)


# ---------------------------------------------------------------------------
# _query_paginated — kept for non-catalog queries that don't have an SR number
# ---------------------------------------------------------------------------

class TestQueryPaginated:
    def test_single_page(self, fetcher):
        rows = [_binding({"x": f"v{i}"}) for i in range(10)]
        with patch.object(fetcher, "_query", return_value=rows) as mock_q:
            result = fetcher._query_paginated("SELECT ?x WHERE { ?x a ?y }", page_size=100)

        assert len(result) == 10
        assert mock_q.call_count == 1
        assert "LIMIT 100" in mock_q.call_args[0][0]
        assert "OFFSET 0" in mock_q.call_args[0][0]

    def test_multiple_pages(self, fetcher):
        page1 = [_binding({"x": f"v{i}"}) for i in range(100)]
        page2 = [_binding({"x": f"v{i}"}) for i in range(100, 200)]
        page3 = [_binding({"x": f"v{i}"}) for i in range(200, 250)]

        with patch.object(fetcher, "_query", side_effect=[page1, page2, page3]) as mock_q:
            result = fetcher._query_paginated("SELECT ?x WHERE { ?x a ?y }", page_size=100)

        assert len(result) == 250
        assert mock_q.call_count == 3
        calls = mock_q.call_args_list
        assert "OFFSET 0" in calls[0][0][0]
        assert "OFFSET 100" in calls[1][0][0]
        assert "OFFSET 200" in calls[2][0][0]

    def test_empty_results(self, fetcher):
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            result = fetcher._query_paginated("SELECT ?x WHERE { ?x a ?y }", page_size=100)
        assert result == []
        assert mock_q.call_count == 1

    def test_default_page_size(self, fetcher):
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            fetcher._query_paginated("SELECT ?x WHERE { ?x a ?y }")
            assert f"LIMIT {PAGE_SIZE}" in mock_q.call_args[0][0]


# ---------------------------------------------------------------------------
# _query_by_sr_prefix — partitioned catalog scan
# ---------------------------------------------------------------------------

class TestQueryBySrPrefix:
    def test_runs_one_query_per_digit(self, fetcher):
        """Fedlex caps sorted result sets at 10000 rows; partitioning on the
        leading digit of ?srNumber stays under that limit. Ten queries fire,
        one per digit 0-9."""
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            fetcher._query_by_sr_prefix("SELECT ... WHERE { ... __SR_FILTER__ ... }")
        assert mock_q.call_count == 10

        sent = [c[0][0] for c in mock_q.call_args_list]
        # __SR_FILTER__ is fully replaced in every query.
        for q in sent:
            assert "__SR_FILTER__" not in q
        # Each query carries one digit's FILTER.
        for digit in "0123456789":
            assert any(
                f'FILTER(STRSTARTS(STR(?srNumber), "{digit}"))' in q for q in sent
            )

    def test_concats_rows_from_all_partitions(self, fetcher):
        sides = [[_binding({"srNumber": f"{d}10"})] for d in "0123456789"]
        with patch.object(fetcher, "_query", side_effect=sides):
            result = fetcher._query_by_sr_prefix(
                "SELECT ?srNumber WHERE { ... __SR_FILTER__ }"
            )
        assert len(result) == 10
        srs = {row["srNumber"]["value"] for row in result}
        assert srs == {f"{d}10" for d in "0123456789"}


# ---------------------------------------------------------------------------
# fetch_catalog routing
# ---------------------------------------------------------------------------

class TestFetchCatalogRouting:
    def test_without_limit_uses_sr_prefix_partitioning(self, fetcher):
        with patch.object(fetcher, "_query_by_sr_prefix", return_value=[]) as mock_p:
            fetcher.fetch_catalog()
        mock_p.assert_called_once()

    def test_with_limit_strips_sr_filter_and_uses_single_query(self, fetcher):
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            with patch.object(fetcher, "_query_by_sr_prefix") as mock_p:
                fetcher.fetch_catalog(limit=5)
        mock_q.assert_called_once()
        mock_p.assert_not_called()
        # The single query must not still contain the unfilled marker.
        assert "__SR_FILTER__" not in mock_q.call_args[0][0]
        assert "LIMIT 5" in mock_q.call_args[0][0]


class TestFetchModifiedSinceRouting:
    def test_without_limit_uses_sr_prefix_partitioning(self, fetcher):
        with patch.object(fetcher, "_query_by_sr_prefix", return_value=[]) as mock_p:
            fetcher.fetch_modified_since(date(2024, 1, 1))
        mock_p.assert_called_once()
        # since_date is interpolated before partitioning.
        assert "2024-01-01" in mock_p.call_args[0][0]

    def test_with_limit_uses_single_query(self, fetcher):
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            with patch.object(fetcher, "_query_by_sr_prefix") as mock_p:
                fetcher.fetch_modified_since(date(2024, 1, 1), limit=10)
        mock_q.assert_called_once()
        mock_p.assert_not_called()
        assert "__SR_FILTER__" not in mock_q.call_args[0][0]
