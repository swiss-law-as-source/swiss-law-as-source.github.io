"""Fetch cantonal law from LexWork (direct) with LexFind fallback."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

import requests

from .transformer import html_to_markdown, build_frontmatter

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 4
INITIAL_BACKOFF = 2.0
BACKOFF_FACTOR = 2.0
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

# ─── Canton Registry ───────────────────────────────────────────────────────────

LEXWORK_CANTONS: dict[str, str] = {
    "ag": "gesetzessammlungen.ag.ch",
    "ar": "ar.clex.ch",
    "be": "www.belex.sites.be.ch",
    "bl": "bl.clex.ch",
    "bs": "www.gesetzessammlung.bs.ch",
    "fr": "bdlf.fr.ch",
    "gl": "gesetze.gl.ch",
    "gr": "www.gr-lex.gr.ch",
    "lu": "srl.lu.ch",
    "sg": "www.gesetzessammlung.sg.ch",
    "so": "bgs.so.ch",
    "tg": "www.rechtsbuch.tg.ch",
    "vs": "lex.vs.ch",
    "zg": "bgs.zg.ch",
}

LEXFIND_ONLY_CANTONS = [
    "ai", "ge", "ju", "ne", "nw", "ow", "sh", "sz", "ti", "ur", "vd", "zh",
]

ALL_CANTONS = sorted(list(LEXWORK_CANTONS.keys()) + LEXFIND_ONLY_CANTONS)


# ─── Models ────────────────────────────────────────────────────────────────────

@dataclass
class CantonalLawEntry:
    """A cantonal law entry from catalog."""
    canton: str
    systematic_number: str
    title: str
    abbreviation: str = ""
    enactment_date: date | None = None
    is_active: bool = True
    lexfind_id: str = ""  # LexFind TOL ID for fallback


@dataclass
class CantonalLawVersion:
    """A specific version of a cantonal law."""
    canton: str
    systematic_number: str
    version_id: int | str
    title: str
    date_in_force: date | None = None
    abbreviation: str = ""


@dataclass
class CantonalLawText:
    """Full text of a cantonal law version."""
    canton: str
    systematic_number: str
    title: str
    html_content: str = ""
    language: str = "de"
    version_date: date | None = None
    abbreviation: str = ""


# ─── Fetcher ───────────────────────────────────────────────────────────────────

class CantonalFetcher:
    """Fetches cantonal law from LexWork portals with LexFind fallback."""

    def __init__(self, rate_limit: float = 1.0):
        self.rate_limit = rate_limit
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "legalize-ch/0.1 (swiss-law pipeline)"

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()

    def _get_json(self, url: str) -> dict | None:
        """Fetch JSON with retry and backoff."""
        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 404:
                    return None
                if resp.status_code in RETRYABLE_HTTP_CODES and attempt < MAX_RETRIES:
                    logger.warning("HTTP %d from %s (attempt %d) — retrying in %.1fs",
                                   resp.status_code, url, attempt, backoff)
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
                resp = self.session.get(url, timeout=30)
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

    # ─── LexWork API ───────────────────────────────────────────────────────────

    def _lexwork_base(self, canton: str) -> str:
        """Get the LexWork API base URL for a canton."""
        host = LEXWORK_CANTONS[canton]
        return f"https://{host}/api"

    def fetch_lexwork_law(self, canton: str, number: str) -> dict | None:
        """Fetch a law from LexWork API. Returns raw JSON response."""
        base = self._lexwork_base(canton)
        url = f"{base}/texts_of_law/{number}"
        return self._get_json(url)

    def fetch_lexwork_version(self, canton: str, number: str, version_id: int) -> dict | None:
        """Fetch a specific version from LexWork."""
        base = self._lexwork_base(canton)
        url = f"{base}/texts_of_law/{number}/versions/{version_id}"
        return self._get_json(url)

    def fetch_lexwork_catalog(self, canton: str, lang: str = "de") -> list[CantonalLawEntry]:
        """Fetch full catalog from a LexWork canton via search endpoint."""
        # LexWork doesn't have a clean catalog API, but we can paginate through search
        # For now, use LexFind as the catalog source even for LexWork cantons
        return self.fetch_lexfind_catalog(canton, lang)

    # ─── LexFind API ───────────────────────────────────────────────────────────

    def fetch_lexfind_catalog(self, canton: str, lang: str = "de") -> list[CantonalLawEntry]:
        """Fetch catalog of laws for a canton from LexFind search."""
        # LexFind uses a search API at /fe/de/search
        # We'll use their internal API endpoint
        url = (
            f"https://www.lexfind.ch/fe/api/search?"
            f"canton={canton.upper()}&jurisdiction=cantonal&language={lang}&limit=50"
        )
        data = self._get_json(url)
        if not data:
            # Try alternative: scrape the tol list
            return self._fetch_lexfind_catalog_scrape(canton, lang)

        entries = []
        results = data if isinstance(data, list) else data.get("results", data.get("items", []))
        for item in results:
            sr = item.get("systematic_number", item.get("number", ""))
            title = item.get("title", item.get("title_de", ""))
            tol_id = str(item.get("tol_id", item.get("id", "")))
            if sr:
                entries.append(CantonalLawEntry(
                    canton=canton,
                    systematic_number=sr,
                    title=title,
                    lexfind_id=tol_id,
                ))
        return entries

    def _fetch_lexfind_catalog_scrape(self, canton: str, lang: str) -> list[CantonalLawEntry]:
        """Fallback: fetch canton catalog from LexFind systematic view."""
        url = f"https://www.lexfind.ch/fe/{lang}/search?canton={canton.upper()}"
        logger.debug("LexFind catalog scrape: %s", url)
        # This returns HTML rendered by JavaScript, so we can't easily scrape it.
        # Return empty and rely on the LexWork direct fetch or explicit lists.
        return []

    def fetch_lexfind_text(self, tol_id: str, lang: str = "de") -> str:
        """Fetch law text HTML from LexFind by TOL ID."""
        # LexFind serves content at /fe/{lang}/tol/{id}/{lang}
        url = f"https://www.lexfind.ch/fe/{lang}/tol/{tol_id}/{lang}"
        return self._get_html(url)

    # ─── Unified fetch methods ─────────────────────────────────────────────────

    def fetch_law_text(self, canton: str, number: str,
                       lang: str = "de",
                       lexfind_id: str = "") -> CantonalLawText | None:
        """Fetch current law text: LexWork direct, LexFind fallback.

        Strategy:
        1. If canton has LexWork portal → fetch from LexWork API
        2. Otherwise → fetch from LexFind
        """
        # Try LexWork first
        if canton in LEXWORK_CANTONS:
            text = self._fetch_from_lexwork(canton, number, lang)
            if text:
                return text
            logger.debug("LexWork failed for %s/%s, trying LexFind", canton, number)

        # LexFind fallback
        if lexfind_id:
            text = self._fetch_from_lexfind(canton, number, lexfind_id, lang)
            if text:
                return text

        return None

    def _fetch_from_lexwork(self, canton: str, number: str,
                            lang: str = "de") -> CantonalLawText | None:
        """Fetch law text from LexWork API."""
        data = self.fetch_lexwork_law(canton, number)
        if not data:
            return None

        tol = data.get("text_of_law", {})
        sv = tol.get("selected_version", {})
        xhtml = sv.get("xhtml_tol", "")
        if not xhtml:
            return None

        title = tol.get("title", "")
        abbr = tol.get("abbreviation", "")

        # publication_enactment = current version's effective date
        version_date = None
        pub_enact = tol.get("publication_enactment", "")
        if pub_enact:
            try:
                version_date = date.fromisoformat(pub_enact[:10])
            except ValueError:
                pass
        # Fallback: parse from version_dates_str
        if not version_date:
            import re
            vds = sv.get("version_dates_str", "")
            m = re.search(r"seit:\s*(\d{2})\.(\d{2})\.(\d{4})", vds)
            if m:
                try:
                    version_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                except ValueError:
                    pass
        # Last fallback: enactment (original law date)
        if not version_date:
            enactment = tol.get("enactment", "")
            if enactment:
                try:
                    version_date = date.fromisoformat(enactment[:10])
                except ValueError:
                    pass

        return CantonalLawText(
            canton=canton,
            systematic_number=number,
            title=title,
            html_content=xhtml,
            language=lang,
            version_date=version_date,
            abbreviation=abbr,
        )

    def _fetch_from_lexfind(self, canton: str, number: str,
                            tol_id: str, lang: str = "de") -> CantonalLawText | None:
        """Fetch law text from LexFind."""
        html = self.fetch_lexfind_text(tol_id, lang)
        if not html or "<html" not in html[:200].lower():
            return None

        return CantonalLawText(
            canton=canton,
            systematic_number=number,
            title="",  # Will be extracted from HTML
            html_content=html,
            language=lang,
        )

    def fetch_versions(self, canton: str, number: str) -> list[CantonalLawVersion]:
        """Fetch all available versions of a cantonal law."""
        if canton not in LEXWORK_CANTONS:
            return []

        data = self.fetch_lexwork_law(canton, number)
        if not data:
            return []

        tol = data.get("text_of_law", {})
        versions = []

        # Current version
        cv = tol.get("current_version", {})
        if cv:
            versions.append(CantonalLawVersion(
                canton=canton,
                systematic_number=number,
                version_id=cv.get("id", 0),
                title=cv.get("title", tol.get("title", "")),
                abbreviation=cv.get("abbreviation", ""),
            ))

        # Old versions
        for ov in tol.get("old_versions", []):
            import re
            vid = ov.get("id", 0)
            title = ov.get("title", "")
            # Parse date from version_dates_str
            vds = ov.get("version_dates_str", "")
            d = None
            m = re.search(r"seit:\s*(\d{2})\.(\d{2})\.(\d{4})", vds)
            if m:
                try:
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                except ValueError:
                    pass
            versions.append(CantonalLawVersion(
                canton=canton,
                systematic_number=number,
                version_id=vid,
                title=title,
                date_in_force=d,
                abbreviation=ov.get("abbreviation", ""),
            ))

        return versions

    def fetch_version_text(self, canton: str, number: str,
                           version_id: int, lang: str = "de") -> CantonalLawText | None:
        """Fetch a specific version's text from LexWork."""
        if canton not in LEXWORK_CANTONS:
            return None

        data = self.fetch_lexwork_version(canton, number, version_id)
        if not data:
            return None

        tol = data.get("text_of_law", {})
        sv = tol.get("selected_version", {})
        xhtml = sv.get("xhtml_tol", "")
        if not xhtml:
            return None

        import re
        version_date = None
        vds = sv.get("version_dates_str", "")
        m = re.search(r"seit:\s*(\d{2})\.(\d{2})\.(\d{4})", vds)
        if m:
            try:
                version_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass

        return CantonalLawText(
            canton=canton,
            systematic_number=number,
            title=sv.get("title", tol.get("title", "")),
            html_content=xhtml,
            language=lang,
            version_date=version_date,
            abbreviation=sv.get("abbreviation", ""),
        )


# ─── Path helpers ──────────────────────────────────────────────────────────────

def canton_to_path(canton: str, systematic_number: str, language: str) -> str:
    """Convert cantonal law identifiers to a file path.

    Structure: ch/{canton}/{lang}/{number}.md

    Examples:
        ch/bs/de/300.100.md
        ch/zh/de/131.1.md
        ch/ge/fr/A.2.05.md

    This mirrors the federal structure (ch/de/, ch/fr/, ch/it/) but scoped
    per canton, keeping language variants of the same law in separate dirs.
    """
    return f"ch/{canton}/{language}/{systematic_number}.md"


def cantonal_law_to_markdown(text: CantonalLawText) -> str:
    """Convert cantonal law text to Markdown with frontmatter."""
    meta = {
        "canton": text.canton.upper(),
        "systematic_number": text.systematic_number,
        "title": text.title,
        "language": text.language,
        "source": "LexWork" if text.canton in LEXWORK_CANTONS else "LexFind",
    }
    if text.version_date:
        meta["version_date"] = text.version_date.isoformat()
    if text.abbreviation:
        meta["abbreviation"] = text.abbreviation

    import yaml
    frontmatter = "---\n" + yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip() + "\n---"

    body = html_to_markdown(text.html_content) if text.html_content else ""
    if not body:
        body = f"# {text.title}\n\n*No text content available.*"

    return frontmatter + "\n\n" + body + "\n"
