"""Fetch Zürich cantonal law from the ZHLex API (zhlex.zh.ch).

Zürich provides a structured REST API at zhlex.zh.ch for accessing its
cantonal law collection (Zürcher Gesetzessammlung / Loseblattsammlung).

This module implements a dedicated fetcher that:
  1. Retrieves the full catalog of Zürich laws (Erlasse)
  2. Fetches individual law texts (Erlasstexte) as HTML
  3. Supports version history retrieval
  4. Integrates with the cantonal pipeline via CantonalLawEntry/Text models
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any

import requests

from .cantonal import CantonalLawEntry, CantonalLawText, CantonalLawVersion

logger = logging.getLogger(__name__)

# Retry configuration (mirrors cantonal.py)
MAX_RETRIES = 4
INITIAL_BACKOFF = 2.0
BACKOFF_FACTOR = 2.0
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

# ZHLex API base
ZHLEX_BASE = "https://www.zhlex.zh.ch"
ZHLEX_API = f"{ZHLEX_BASE}/api/zhlex/v1"

# Catalog endpoint — returns all Erlasse (enacted laws)
CATALOG_URL = f"{ZHLEX_API}/erlasse"
# Law text endpoint pattern — returns a specific Erlass with full text
ERLASS_URL = ZHLEX_API + "/erlasstexte/{erlass_id}"
# Versions endpoint pattern
VERSIONS_URL = ZHLEX_API + "/erlasse/{erlass_id}/versionen"


class ZurichFetcher:
    """Fetches Zürich cantonal law from the ZHLex REST API.

    The ZHLex API provides:
      - /api/zhlex/v1/erlasse — catalog of all laws
      - /api/zhlex/v1/erlasstexte/{id} — full text of a law
      - /api/zhlex/v1/erlasse/{id}/versionen — version history
    """

    def __init__(self, rate_limit: float = 1.0):
        self.rate_limit = rate_limit
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "legalize-ch/0.1 (swiss-law pipeline)",
            "Accept": "application/json",
        })

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()

    def _get(self, url: str, params: dict | None = None) -> dict | list | None:
        """HTTP GET with retry and exponential backoff."""
        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 404:
                    return None
                if resp.status_code in RETRYABLE_HTTP_CODES and attempt < MAX_RETRIES:
                    logger.warning(
                        "HTTP %d from %s (attempt %d) — retrying in %.1fs",
                        resp.status_code, url, attempt, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= BACKOFF_FACTOR
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES:
                    logger.warning("Failed %s (attempt %d): %s — retrying", url, attempt, e)
                    time.sleep(backoff)
                    backoff *= BACKOFF_FACTOR
                else:
                    logger.error("Failed %s after %d attempts: %s", url, MAX_RETRIES, e)
        return None

    def _get_html(self, url: str) -> str:
        """Fetch HTML content with retry."""
        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.get(
                    url, timeout=30,
                    headers={**self.session.headers, "Accept": "text/html"},
                )
                if resp.status_code in RETRYABLE_HTTP_CODES and attempt < MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= BACKOFF_FACTOR
                    continue
                resp.raise_for_status()
                return resp.text
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= BACKOFF_FACTOR
                else:
                    logger.error("Failed %s: %s", url, e)
        return ""

    # ─── Catalog ──────────────────────────────────────────────────────────────

    def fetch_catalog(self, lang: str = "de") -> list[CantonalLawEntry]:
        """Fetch the full catalog of Zürich cantonal laws.

        Returns a list of CantonalLawEntry objects with systematic numbers
        (LS-Nummern), titles, and internal IDs for text retrieval.
        """
        entries: list[CantonalLawEntry] = []
        offset = 0
        page_size = 100

        while True:
            params = {
                "offset": offset,
                "limit": page_size,
                "language": lang,
                "inForce": "true",
            }
            data = self._get(CATALOG_URL, params=params)
            if not data:
                break

            items = _extract_items(data)
            if not items:
                break

            for item in items:
                entry = _parse_catalog_entry(item)
                if entry:
                    entries.append(entry)

            # Check if there are more pages
            if isinstance(data, dict):
                total = data.get("total", data.get("totalCount", 0))
                if total and offset + page_size >= total:
                    break
            if len(items) < page_size:
                break
            offset += page_size

        logger.info("ZHLex catalog: fetched %d laws", len(entries))
        return entries

    # ─── Law text ─────────────────────────────────────────────────────────────

    def fetch_law_text(
        self, systematic_number: str, lang: str = "de",
        erlass_id: str = "",
    ) -> CantonalLawText | None:
        """Fetch the current text of a Zürich law by systematic number.

        Strategy:
        1. If erlass_id is provided, fetch directly by ID
        2. Otherwise, search the catalog for the systematic number
        3. Fetch the full HTML text via the erlasstexte endpoint
        """
        # Resolve erlass_id if not provided
        if not erlass_id:
            erlass_id = self._resolve_erlass_id(systematic_number, lang)
        if not erlass_id:
            logger.debug("Could not resolve erlass_id for ZH/%s", systematic_number)
            return None

        # Fetch the law text
        url = ERLASS_URL.format(erlass_id=erlass_id)
        data = self._get(url)
        if not data:
            return None

        return _parse_law_text(data, systematic_number, lang)

    def _resolve_erlass_id(self, systematic_number: str, lang: str = "de") -> str:
        """Search catalog for a law's internal ID by systematic number."""
        params = {
            "lsNummer": systematic_number,
            "language": lang,
            "limit": 5,
        }
        data = self._get(CATALOG_URL, params=params)
        if not data:
            return ""

        items = _extract_items(data)
        for item in items:
            item_nr = item.get("lsNummer", item.get("systematicNumber", ""))
            if item_nr == systematic_number:
                return str(item.get("id", item.get("erlassId", "")))

        # Fallback: return first match
        if items:
            return str(items[0].get("id", items[0].get("erlassId", "")))
        return ""

    # ─── Versions ─────────────────────────────────────────────────────────────

    def fetch_versions(
        self, systematic_number: str, erlass_id: str = "",
    ) -> list[CantonalLawVersion]:
        """Fetch all available versions of a Zürich law."""
        if not erlass_id:
            erlass_id = self._resolve_erlass_id(systematic_number)
        if not erlass_id:
            return []

        url = VERSIONS_URL.format(erlass_id=erlass_id)
        data = self._get(url)
        if not data:
            return []

        versions: list[CantonalLawVersion] = []
        items = data if isinstance(data, list) else data.get("versionen", data.get("items", []))

        for item in items:
            version = _parse_version(item, systematic_number)
            if version:
                versions.append(version)

        # Sort by date ascending
        versions.sort(key=lambda v: v.date_in_force or date.min)
        return versions

    def fetch_version_text(
        self, systematic_number: str, version_id: int | str,
        lang: str = "de",
    ) -> CantonalLawText | None:
        """Fetch a specific version's text."""
        url = ERLASS_URL.format(erlass_id=version_id)
        data = self._get(url)
        if not data:
            return None

        return _parse_law_text(data, systematic_number, lang)


# ─── Parsing helpers ──────────────────────────────────────────────────────────


def _extract_items(data: dict | list) -> list[dict]:
    """Extract the list of items from a ZHLex API response.

    The API may return a bare list or a wrapper object with various key names.
    """
    if isinstance(data, list):
        return data
    for key in ("erlasse", "items", "results", "data", "content"):
        if key in data and isinstance(data[key], list):
            return data[key]
    return []


def _parse_catalog_entry(item: dict) -> CantonalLawEntry | None:
    """Parse a single catalog item into a CantonalLawEntry."""
    systematic_number = item.get("lsNummer", item.get("systematicNumber", ""))
    if not systematic_number:
        return None

    title = (
        item.get("titel", "")
        or item.get("title", "")
        or item.get("erpiTitle", "")
    )
    abbreviation = item.get("abkuerzung", item.get("abbreviation", ""))
    erlass_id = str(item.get("id", item.get("erlassId", "")))

    # Parse enactment date
    enactment_date = _parse_date_field(
        item.get("erlassDatum", item.get("enactmentDate", ""))
    )

    # Active status
    is_active = item.get("inKraft", item.get("inForce", True))

    return CantonalLawEntry(
        canton="zh",
        systematic_number=systematic_number,
        title=title,
        abbreviation=abbreviation,
        enactment_date=enactment_date,
        is_active=bool(is_active),
        lexfind_id=erlass_id,  # store ZHLex ID in lexfind_id field for compat
    )


def _parse_law_text(data: dict, systematic_number: str, lang: str) -> CantonalLawText | None:
    """Parse a law text API response into a CantonalLawText."""
    # The response may have the text nested under various keys
    text_data = data
    if "erlasstext" in data:
        text_data = data["erlasstext"]
    elif "erlass" in data:
        text_data = data["erlass"]

    # Extract HTML content
    html_content = (
        text_data.get("htmlContent", "")
        or text_data.get("xhtml", "")
        or text_data.get("text", "")
        or text_data.get("inhalt", "")
    )

    title = (
        text_data.get("titel", "")
        or text_data.get("title", "")
        or data.get("titel", "")
        or data.get("title", "")
    )

    abbreviation = (
        text_data.get("abkuerzung", "")
        or text_data.get("abbreviation", "")
        or data.get("abkuerzung", "")
        or data.get("abbreviation", "")
    )

    # Parse version/effective date
    version_date = _parse_date_field(
        text_data.get("inkrafttretungsDatum", "")
        or text_data.get("inForceSince", "")
        or text_data.get("gueltigAb", "")
        or data.get("inkrafttretungsDatum", "")
        or data.get("inForceSince", "")
    )

    # Fallback: enactment date
    if not version_date:
        version_date = _parse_date_field(
            text_data.get("erlassDatum", "")
            or data.get("erlassDatum", "")
            or data.get("enactmentDate", "")
        )

    if not html_content and not title:
        return None

    return CantonalLawText(
        canton="zh",
        systematic_number=systematic_number,
        title=title,
        html_content=html_content,
        language=lang,
        version_date=version_date,
        abbreviation=abbreviation,
    )


def _parse_version(item: dict, systematic_number: str) -> CantonalLawVersion | None:
    """Parse a version history item into a CantonalLawVersion."""
    version_id = item.get("id", item.get("versionId", ""))
    if not version_id:
        return None

    title = item.get("titel", item.get("title", ""))

    date_in_force = _parse_date_field(
        item.get("inkrafttretungsDatum", "")
        or item.get("inForceSince", "")
        or item.get("gueltigAb", "")
    )

    abbreviation = item.get("abkuerzung", item.get("abbreviation", ""))

    return CantonalLawVersion(
        canton="zh",
        systematic_number=systematic_number,
        version_id=version_id,
        title=title,
        date_in_force=date_in_force,
        abbreviation=abbreviation,
    )


def _parse_date_field(value: Any) -> date | None:
    """Parse a date from various ZHLex date formats."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None

    # ISO format: 2024-01-01 or 2024-01-01T00:00:00
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, IndexError):
        pass

    # Swiss format: 01.01.2024
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # Unix timestamp in milliseconds
    if s.isdigit() and len(s) >= 10:
        try:
            from datetime import datetime
            ts = int(s) / 1000 if len(s) > 10 else int(s)
            return datetime.fromtimestamp(ts).date()
        except (ValueError, OSError):
            pass

    return None
