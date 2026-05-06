"""Tests for SPARQL query pagination (TD.4)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest

from legalize_ch.fetcher import FedlexFetcher, PAGE_SIZE


def _binding(vals: dict[str, str]) -> dict:
    return {k: {"value": v} for k, v in vals.items()}


@pytest.fixture
def fetcher():
    return FedlexFetcher(rate_limit=0.0)


# ---------------------------------------------------------------------------
# _query_paginated
# ---------------------------------------------------------------------------

class TestQueryPaginated:
    def test_single_page(self, fetcher):
        """When results fit in one page, only one query is issued."""
        rows = [_binding({"x": f"v{i}"}) for i in range(10)]
        with patch.object(fetcher, "_query", return_value=rows) as mock_q:
            result = fetcher._query_paginated("SELECT ?x WHERE { ?x a ?y }", page_size=100)

        assert len(result) == 10
        assert mock_q.call_count == 1
        query_text = mock_q.call_args[0][0]
        assert "LIMIT 100" in query_text
        assert "OFFSET 0" in query_text

    def test_multiple_pages(self, fetcher):
        """When results span multiple pages, all pages are fetched."""
        page1 = [_binding({"x": f"v{i}"}) for i in range(100)]
        page2 = [_binding({"x": f"v{i}"}) for i in range(100, 200)]
        page3 = [_binding({"x": f"v{i}"}) for i in range(200, 250)]  # partial

        with patch.object(fetcher, "_query", side_effect=[page1, page2, page3]) as mock_q:
            result = fetcher._query_paginated("SELECT ?x WHERE { ?x a ?y }", page_size=100)

        assert len(result) == 250
        assert mock_q.call_count == 3

        # Verify offsets
        calls = mock_q.call_args_list
        assert "OFFSET 0" in calls[0][0][0]
        assert "OFFSET 100" in calls[1][0][0]
        assert "OFFSET 200" in calls[2][0][0]

    def test_exact_page_boundary(self, fetcher):
        """When results are exactly page_size, an extra empty page query is issued."""
        page1 = [_binding({"x": f"v{i}"}) for i in range(100)]
        page2 = []  # empty = end of results

        with patch.object(fetcher, "_query", side_effect=[page1, page2]) as mock_q:
            result = fetcher._query_paginated("SELECT ?x WHERE { ?x a ?y }", page_size=100)

        assert len(result) == 100
        assert mock_q.call_count == 2

    def test_empty_results(self, fetcher):
        """Empty result set returns empty list with one query."""
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            result = fetcher._query_paginated("SELECT ?x WHERE { ?x a ?y }", page_size=100)

        assert result == []
        assert mock_q.call_count == 1

    def test_default_page_size(self, fetcher):
        """Default page size from constant is used."""
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            fetcher._query_paginated("SELECT ?x WHERE { ?x a ?y }")
            query_text = mock_q.call_args[0][0]
            assert f"LIMIT {PAGE_SIZE}" in query_text


# ---------------------------------------------------------------------------
# fetch_catalog uses pagination
# ---------------------------------------------------------------------------

class TestFetchCatalogPagination:
    def test_catalog_without_limit_uses_pagination(self, fetcher):
        """fetch_catalog() without limit uses _query_paginated."""
        with patch.object(fetcher, "_query_paginated", return_value=[]) as mock_pq:
            fetcher.fetch_catalog()
        mock_pq.assert_called_once()

    def test_catalog_with_limit_uses_single_query(self, fetcher):
        """fetch_catalog(limit=N) uses _query (not paginated)."""
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            with patch.object(fetcher, "_query_paginated") as mock_pq:
                fetcher.fetch_catalog(limit=5)
        mock_q.assert_called_once()
        mock_pq.assert_not_called()


# ---------------------------------------------------------------------------
# fetch_modified_since uses pagination
# ---------------------------------------------------------------------------

class TestFetchModifiedSincePagination:
    def test_modified_since_without_limit_uses_pagination(self, fetcher):
        """fetch_modified_since() without limit uses _query_paginated."""
        with patch.object(fetcher, "_query_paginated", return_value=[]) as mock_pq:
            fetcher.fetch_modified_since(date(2024, 1, 1))
        mock_pq.assert_called_once()

    def test_modified_since_with_limit_uses_single_query(self, fetcher):
        """fetch_modified_since(limit=N) uses _query (not paginated)."""
        with patch.object(fetcher, "_query", return_value=[]) as mock_q:
            with patch.object(fetcher, "_query_paginated") as mock_pq:
                fetcher.fetch_modified_since(date(2024, 1, 1), limit=10)
        mock_q.assert_called_once()
        mock_pq.assert_not_called()
