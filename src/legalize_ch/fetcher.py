"""Fetch Swiss law from Fedlex SPARQL endpoint."""
from __future__ import annotations

import logging
import time
from datetime import date

import requests
from SPARQLWrapper import SPARQLWrapper, JSON

from .models import LawEntry, LawVersion, LawText

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 5
INITIAL_BACKOFF = 2.0  # seconds
BACKOFF_FACTOR = 2.0
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

# Pagination configuration for large SPARQL result sets
PAGE_SIZE = 5000  # results per page

SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"

PREFIXES = """
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd:   <http://www.w3.org/2001/XMLSchema#>
PREFIX schema: <http://schema.org/>
"""

CATALOG_QUERY = PREFIXES + """
SELECT DISTINCT ?cc ?srNumber ?titleDe ?titleFr ?titleIt ?dateDoc ?dateForce ?abbrDe ?abbrFr ?abbrIt
WHERE {
  ?cc a jolux:ConsolidationAbstract ;
      jolux:classifiedByTaxonomyEntry ?tax .
  ?tax skos:notation ?srNumber .

  OPTIONAL {
    ?cc jolux:isRealizedBy ?exprDe .
    ?exprDe a jolux:Expression ;
            jolux:language <http://publications.europa.eu/resource/authority/language/DEU> ;
            jolux:title ?titleDe .
  }
  OPTIONAL {
    ?cc jolux:isRealizedBy ?exprFr .
    ?exprFr a jolux:Expression ;
            jolux:language <http://publications.europa.eu/resource/authority/language/FRA> ;
            jolux:title ?titleFr .
  }
  OPTIONAL {
    ?cc jolux:isRealizedBy ?exprIt .
    ?exprIt a jolux:Expression ;
            jolux:language <http://publications.europa.eu/resource/authority/language/ITA> ;
            jolux:title ?titleIt .
  }
  OPTIONAL { ?cc jolux:dateDocument ?dateDoc . }
  OPTIONAL { ?cc jolux:dateEntryInForce ?dateForce . }
  OPTIONAL {
    ?cc jolux:isRealizedBy ?exprAbbrDe .
    ?exprAbbrDe jolux:language <http://publications.europa.eu/resource/authority/language/DEU> ;
                jolux:titleShort ?abbrDe .
  }
  OPTIONAL {
    ?cc jolux:isRealizedBy ?exprAbbrFr .
    ?exprAbbrFr jolux:language <http://publications.europa.eu/resource/authority/language/FRA> ;
                jolux:titleShort ?abbrFr .
  }
  OPTIONAL {
    ?cc jolux:isRealizedBy ?exprAbbrIt .
    ?exprAbbrIt jolux:language <http://publications.europa.eu/resource/authority/language/ITA> ;
                jolux:titleShort ?abbrIt .
  }
}
ORDER BY ?srNumber
"""

# Consolidation versions via isMemberOf
VERSIONS_QUERY = PREFIXES + """
SELECT DISTINCT ?cons ?dateApp WHERE {{
  ?cons a jolux:Consolidation ;
        jolux:isMemberOf <{uri}> .
  ?cons jolux:dateApplicability ?dateApp .
}}
ORDER BY ?dateApp
"""

# Get XML filestore URL for a consolidation+language via isExemplifiedBy
TEXT_QUERY = PREFIXES + """
SELECT DISTINCT ?title ?fileUrl WHERE {{
  <{cons_uri}> jolux:isRealizedBy <{cons_uri}/{lang}> .
  <{cons_uri}/{lang}> jolux:isEmbodiedBy ?manifest .
  ?manifest jolux:format <http://publications.europa.eu/resource/authority/file-type/XML> ;
            jolux:isExemplifiedBy ?fileUrl .

  OPTIONAL {{
    <{cons_uri}> jolux:isMemberOf ?abstract .
    ?abstract jolux:isRealizedBy ?absExpr .
    ?absExpr jolux:language <http://publications.europa.eu/resource/authority/language/{lang_upper}> ;
             jolux:title ?title .
  }}
}}
LIMIT 1
"""

# Fallback: get HTML filestore URL
TEXT_HTML_QUERY = PREFIXES + """
SELECT DISTINCT ?title ?fileUrl WHERE {{
  <{cons_uri}> jolux:isRealizedBy <{cons_uri}/{lang}> .
  <{cons_uri}/{lang}> jolux:isEmbodiedBy ?manifest .
  ?manifest jolux:format <http://publications.europa.eu/resource/authority/file-type/HTML> ;
            jolux:isExemplifiedBy ?fileUrl .

  OPTIONAL {{
    <{cons_uri}> jolux:isMemberOf ?abstract .
    ?abstract jolux:isRealizedBy ?absExpr .
    ?absExpr jolux:language <http://publications.europa.eu/resource/authority/language/{lang_upper}> ;
             jolux:title ?title .
  }}
}}
LIMIT 1
"""

# Just get the title if no content is available
TITLE_QUERY = PREFIXES + """
SELECT DISTINCT ?title WHERE {{
  <{cons_uri}> jolux:isMemberOf ?abstract .
  ?abstract jolux:isRealizedBy ?absExpr .
  ?absExpr jolux:language <http://publications.europa.eu/resource/authority/language/{lang_upper}> ;
           jolux:title ?title .
}}
LIMIT 1
"""

# Fetch content directly from the ConsolidationAbstract (no consolidation version needed).
# Tries XML first, then HTML.
ABSTRACT_TEXT_XML_QUERY = PREFIXES + """
SELECT DISTINCT ?title ?fileUrl WHERE {{
  <{abstract_uri}> jolux:isRealizedBy ?expr .
  ?expr jolux:language <http://publications.europa.eu/resource/authority/language/{lang_upper}> .
  OPTIONAL {{ ?expr jolux:title ?title . }}
  ?expr jolux:isEmbodiedBy ?manifest .
  ?manifest jolux:format <http://publications.europa.eu/resource/authority/file-type/XML> ;
            jolux:isExemplifiedBy ?fileUrl .
}}
LIMIT 1
"""

ABSTRACT_TEXT_HTML_QUERY = PREFIXES + """
SELECT DISTINCT ?title ?fileUrl WHERE {{
  <{abstract_uri}> jolux:isRealizedBy ?expr .
  ?expr jolux:language <http://publications.europa.eu/resource/authority/language/{lang_upper}> .
  OPTIONAL {{ ?expr jolux:title ?title . }}
  ?expr jolux:isEmbodiedBy ?manifest .
  ?manifest jolux:format <http://publications.europa.eu/resource/authority/file-type/HTML> ;
            jolux:isExemplifiedBy ?fileUrl .
}}
LIMIT 1
"""

# Fetch laws with consolidation versions applicable since a given date
MODIFIED_SINCE_QUERY = PREFIXES + """
SELECT DISTINCT ?cc ?srNumber ?titleDe ?titleFr ?titleIt ?dateDoc ?dateForce ?abbrDe ?abbrFr ?abbrIt
WHERE {{
  ?cons a jolux:Consolidation ;
        jolux:isMemberOf ?cc ;
        jolux:dateApplicability ?dateApp .
  FILTER(?dateApp >= "{since_date}"^^xsd:date)

  ?cc a jolux:ConsolidationAbstract ;
      jolux:classifiedByTaxonomyEntry ?tax .
  ?tax skos:notation ?srNumber .

  OPTIONAL {{
    ?cc jolux:isRealizedBy ?exprDe .
    ?exprDe a jolux:Expression ;
            jolux:language <http://publications.europa.eu/resource/authority/language/DEU> ;
            jolux:title ?titleDe .
  }}
  OPTIONAL {{
    ?cc jolux:isRealizedBy ?exprFr .
    ?exprFr a jolux:Expression ;
            jolux:language <http://publications.europa.eu/resource/authority/language/FRA> ;
            jolux:title ?titleFr .
  }}
  OPTIONAL {{
    ?cc jolux:isRealizedBy ?exprIt .
    ?exprIt a jolux:Expression ;
            jolux:language <http://publications.europa.eu/resource/authority/language/ITA> ;
            jolux:title ?titleIt .
  }}
  OPTIONAL {{ ?cc jolux:dateDocument ?dateDoc . }}
  OPTIONAL {{ ?cc jolux:dateEntryInForce ?dateForce . }}
  OPTIONAL {{
    ?cc jolux:isRealizedBy ?exprAbbrDe .
    ?exprAbbrDe jolux:language <http://publications.europa.eu/resource/authority/language/DEU> ;
                jolux:titleShort ?abbrDe .
  }}
  OPTIONAL {{
    ?cc jolux:isRealizedBy ?exprAbbrFr .
    ?exprAbbrFr jolux:language <http://publications.europa.eu/resource/authority/language/FRA> ;
                jolux:titleShort ?abbrFr .
  }}
  OPTIONAL {{
    ?cc jolux:isRealizedBy ?exprAbbrIt .
    ?exprAbbrIt jolux:language <http://publications.europa.eu/resource/authority/language/ITA> ;
                jolux:titleShort ?abbrIt .
  }}
}}
ORDER BY ?srNumber
"""

LANG_MAP = {
    "de": ("de", "DEU"),
    "fr": ("fr", "FRA"),
    "it": ("it", "ITA"),
}


class FedlexFetcher:
    """Fetches Swiss law data from the Fedlex SPARQL endpoint."""

    def __init__(self, rate_limit: float = 1.0):
        self.sparql = SPARQLWrapper(SPARQL_ENDPOINT)
        self.sparql.setReturnFormat(JSON)
        self.sparql.addCustomHttpHeader("Accept", "application/sparql-results+json")
        self.rate_limit = rate_limit
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "legalize-ch/0.1 (swiss-law pipeline)"

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()

    def _query(self, sparql_text: str) -> list[dict]:
        self._throttle()
        self.sparql.setQuery(sparql_text)
        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                results = self.sparql.query().convert()
                return results["results"]["bindings"]
            except Exception as e:
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                # Also check urllib error codes embedded in the exception message
                is_retryable = (
                    status_code in RETRYABLE_HTTP_CODES
                    or "429" in str(e)
                    or "503" in str(e)
                    or "timeout" in str(e).lower()
                    or "connection" in str(e).lower()
                )
                if is_retryable and attempt < MAX_RETRIES:
                    logger.warning(
                        "SPARQL query failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt, MAX_RETRIES, e, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= BACKOFF_FACTOR
                    self._last_request = 0.0  # reset throttle after sleeping
                else:
                    logger.error("SPARQL query failed after %d attempts: %s", attempt, e)
                    return []
        return []

    def _query_paginated(self, sparql_text: str, page_size: int = PAGE_SIZE) -> list[dict]:
        """Execute a SPARQL query with LIMIT/OFFSET pagination.

        Fetches results in pages of ``page_size`` rows to avoid timeouts on
        large result sets.  Pages are fetched until a page returns fewer rows
        than ``page_size`` (i.e. the last page).
        """
        all_rows: list[dict] = []
        offset = 0
        while True:
            paginated = f"{sparql_text}\nLIMIT {page_size}\nOFFSET {offset}"
            rows = self._query(paginated)
            all_rows.extend(rows)
            logger.debug("Paginated query: offset=%d, got %d rows", offset, len(rows))
            if len(rows) < page_size:
                break  # last page
            offset += page_size
        return all_rows

    def _fetch_url(self, url: str) -> str:
        """Fetch a URL with exponential backoff retry on transient errors."""
        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code in RETRYABLE_HTTP_CODES and attempt < MAX_RETRIES:
                    logger.warning(
                        "HTTP %d fetching %s (attempt %d/%d) — retrying in %.1fs",
                        resp.status_code, url, attempt, MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= BACKOFF_FACTOR
                    continue
                resp.raise_for_status()
                return resp.text
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        "Failed to fetch %s (attempt %d/%d): %s — retrying in %.1fs",
                        url, attempt, MAX_RETRIES, e, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= BACKOFF_FACTOR
                else:
                    logger.error("Failed to fetch %s after %d attempts: %s", url, MAX_RETRIES, e)
        return ""

    def _parse_date(self, val: str | None) -> date | None:
        if not val:
            return None
        try:
            return date.fromisoformat(val[:10])
        except ValueError:
            return None

    def _get_val(self, row: dict, key: str) -> str:
        return row.get(key, {}).get("value", "")

    def fetch_catalog(self, limit: int | None = None) -> list[LawEntry]:
        """Fetch all laws in the classified compilation.

        Uses paginated SPARQL queries to avoid timeouts on large result sets.
        If *limit* is set, a single non-paginated query with LIMIT is used.
        """
        query = CATALOG_QUERY
        if limit:
            query += f"\nLIMIT {limit}"
            logger.info("Fetching law catalog from Fedlex (limit=%d)...", limit)
            rows = self._query(query)
        else:
            logger.info("Fetching law catalog from Fedlex (paginated, page_size=%d)...", PAGE_SIZE)
            rows = self._query_paginated(query)
        logger.info("Found %d raw catalog rows", len(rows))

        entries = []
        seen = set()
        for row in rows:
            sr = self._get_val(row, "srNumber")
            if sr in seen:
                continue
            seen.add(sr)
            entries.append(LawEntry(
                sr_number=sr,
                uri=self._get_val(row, "cc"),
                title_de=self._get_val(row, "titleDe"),
                title_fr=self._get_val(row, "titleFr"),
                title_it=self._get_val(row, "titleIt"),
                date_document=self._parse_date(self._get_val(row, "dateDoc")),
                date_in_force=self._parse_date(self._get_val(row, "dateForce")),
                abbreviation_de=self._get_val(row, "abbrDe"),
                abbreviation_fr=self._get_val(row, "abbrFr"),
                abbreviation_it=self._get_val(row, "abbrIt"),
            ))
        return entries

    def fetch_modified_since(self, since: date, limit: int | None = None) -> list[LawEntry]:
        """Fetch laws that have consolidation versions applicable since the given date.

        Uses paginated SPARQL queries to avoid timeouts on large result sets.
        If *limit* is set, a single non-paginated query with LIMIT is used.
        """
        query = MODIFIED_SINCE_QUERY.format(since_date=since.isoformat())
        if limit:
            query += f"\nLIMIT {limit}"
            logger.info("Fetching laws modified since %s (limit=%d)...", since.isoformat(), limit)
            rows = self._query(query)
        else:
            logger.info("Fetching laws modified since %s (paginated, page_size=%d)...",
                         since.isoformat(), PAGE_SIZE)
            rows = self._query_paginated(query)
        logger.info("Found %d raw rows for modified laws", len(rows))

        entries = []
        seen = set()
        for row in rows:
            sr = self._get_val(row, "srNumber")
            if sr in seen:
                continue
            seen.add(sr)
            entries.append(LawEntry(
                sr_number=sr,
                uri=self._get_val(row, "cc"),
                title_de=self._get_val(row, "titleDe"),
                title_fr=self._get_val(row, "titleFr"),
                title_it=self._get_val(row, "titleIt"),
                date_document=self._parse_date(self._get_val(row, "dateDoc")),
                date_in_force=self._parse_date(self._get_val(row, "dateForce")),
                abbreviation_de=self._get_val(row, "abbrDe"),
                abbreviation_fr=self._get_val(row, "abbrFr"),
                abbreviation_it=self._get_val(row, "abbrIt"),
            ))
        return entries

    def fetch_versions(self, law: LawEntry) -> list[LawVersion]:
        """Fetch all consolidation versions for a law."""
        query = VERSIONS_QUERY.format(uri=law.uri)
        rows = self._query(query)

        versions = []
        for row in rows:
            d = self._parse_date(self._get_val(row, "dateApp"))
            if not d:
                continue
            versions.append(LawVersion(
                sr_number=law.sr_number,
                version_uri=self._get_val(row, "cons"),
                date_applicable=d,
            ))
        return sorted(versions, key=lambda v: v.date_applicable)

    def fetch_text(self, version: LawVersion, lang: str) -> LawText | None:
        """Fetch text content for a specific version and language."""
        lang_code, lang_upper = LANG_MAP.get(lang, ("de", "DEU"))

        # Try XML first
        query = TEXT_QUERY.format(cons_uri=version.version_uri, lang=lang_code, lang_upper=lang_upper)
        rows = self._query(query)

        if not rows:
            # Fallback to HTML
            query = TEXT_HTML_QUERY.format(cons_uri=version.version_uri, lang=lang_code, lang_upper=lang_upper)
            rows = self._query(query)

        title = ""
        content = ""
        content_url = ""
        is_xml = False

        if rows:
            row = rows[0]
            title = self._get_val(row, "title")
            content_url = self._get_val(row, "fileUrl")
            if content_url:
                content = self._fetch_url(content_url)
                if content:
                    is_xml = content.strip().startswith("<?xml") or "<akomaNtoso" in content[:500]
        else:
            # Just get the title
            query = TITLE_QUERY.format(cons_uri=version.version_uri, lang_upper=lang_upper)
            rows = self._query(query)
            if rows:
                title = self._get_val(rows[0], "title")

        if not title and not content:
            return None

        return LawText(
            sr_number=version.sr_number,
            language=lang,
            version_date=version.date_applicable,
            title=title,
            html_content="" if is_xml else content,
            xml_content=content if is_xml else "",
            content_url=content_url,
        )

    def fetch_abstract_text(self, law: LawEntry, lang: str) -> LawText | None:
        """Try to fetch text directly from the ConsolidationAbstract URI.

        This is a fallback for laws that have no consolidation versions but
        may still have content attached at the abstract level.
        """
        lang_code, lang_upper = LANG_MAP.get(lang, ("de", "DEU"))

        # Try XML first
        query = ABSTRACT_TEXT_XML_QUERY.format(
            abstract_uri=law.uri, lang_upper=lang_upper,
        )
        rows = self._query(query)

        if not rows:
            # Fallback to HTML
            query = ABSTRACT_TEXT_HTML_QUERY.format(
                abstract_uri=law.uri, lang_upper=lang_upper,
            )
            rows = self._query(query)

        if not rows:
            return None

        row = rows[0]
        title = self._get_val(row, "title")
        content_url = self._get_val(row, "fileUrl")
        content = ""
        is_xml = False

        if content_url:
            content = self._fetch_url(content_url)
            if content:
                is_xml = content.strip().startswith("<?xml") or "<akomaNtoso" in content[:500]

        if not title and not content:
            return None

        version_date = law.date_in_force or law.date_document or date.today()
        return LawText(
            sr_number=law.sr_number,
            language=lang,
            version_date=version_date,
            title=title or "",
            html_content="" if is_xml else content,
            xml_content=content if is_xml else "",
            content_url=content_url,
        )
