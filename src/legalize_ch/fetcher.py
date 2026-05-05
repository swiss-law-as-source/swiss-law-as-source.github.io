"""Fetch Swiss law from Fedlex SPARQL endpoint."""
from __future__ import annotations

import logging
import time
from datetime import date

import requests
from SPARQLWrapper import SPARQLWrapper, JSON

from .models import LawEntry, LawVersion, LawText

logger = logging.getLogger(__name__)

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
        try:
            results = self.sparql.query().convert()
            return results["results"]["bindings"]
        except Exception as e:
            logger.error("SPARQL query failed: %s", e)
            return []

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
        """Fetch all laws in the classified compilation."""
        query = CATALOG_QUERY
        if limit:
            query += f"\nLIMIT {limit}"
        logger.info("Fetching law catalog from Fedlex...")
        rows = self._query(query)
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
                try:
                    self._throttle()
                    resp = self.session.get(content_url, timeout=30)
                    resp.raise_for_status()
                    content = resp.text
                    is_xml = content.strip().startswith("<?xml") or "<akomaNtoso" in content[:500]
                except Exception as e:
                    logger.warning("Failed to fetch %s: %s", content_url, e)
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
