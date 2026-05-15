"""Fetch cantonal law from LexWork (direct) with LexFind fallback."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

import requests

from .cantonal_transformer import transform_cantonal_html
from .transformer import html_to_markdown, build_frontmatter

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 4
INITIAL_BACKOFF = 2.0
BACKOFF_FACTOR = 2.0
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

# ─── Canton Registry ───────────────────────────────────────────────────────────

# Cantonal portal hosts (LexWork-stack Angular apps, all served by Sitrox).
# Verified by Phase 1 discovery against the 2026 endpoints
# (`/api/{lang}/texts_of_law/lightweight_index` returns JSON 200 from each).
LEXWORK_CANTONS: dict[str, str] = {
    "ag": "gesetzessammlungen.ag.ch",
    "ai": "ai.clex.ch",
    "ar": "ar.clex.ch",
    "be": "www.belex.sites.be.ch",
    "bl": "bl.clex.ch",
    "bs": "www.gesetzessammlung.bs.ch",
    "fr": "bdlf.fr.ch",
    "gl": "gesetze.gl.ch",
    "gr": "www.gr-lex.gr.ch",
    "lu": "srl.lu.ch",
    "nw": "gesetze.nw.ch",
    "ow": "gdb.ow.ch",
    "sg": "www.gesetzessammlung.sg.ch",
    "sh": "rechtsbuch.sh.ch",
    "so": "bgs.so.ch",
    "tg": "www.rechtsbuch.tg.ch",
    "ur": "rechtsbuch.ur.ch",
    "vs": "lex.vs.ch",
    "zg": "bgs.zg.ch",
}

# Cantons that don't run their own LexWork portal; their data is only
# accessible via the federated LexFind index. ZH no longer publishes a
# standalone JSON API (the old `www.zhlex.zh.ch/api/zhlex/v1/...` is gone)
# and now ships through the same LexFind entity flow as the smaller
# cantons.
LEXFIND_ONLY_CANTONS = [
    "ge", "ju", "ne", "sz", "ti", "vd", "zh",
]

# Kept as a hook for any future canton that needs a fully custom fetcher
# (none today — ZHLex was retired and consolidated into LexFind).
DEDICATED_FETCHER_CANTONS: list[str] = []

ALL_CANTONS = sorted(
    list(LEXWORK_CANTONS.keys()) + LEXFIND_ONLY_CANTONS + DEDICATED_FETCHER_CANTONS
)


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


# LexFind entity IDs (numeric, found at https://www.lexfind.ch/fe/{lang}/entities/{id}).
# Verified by the redirect `https://{canton}.lexfind.ch/` → `/fe/{lang}/entities/{id}`
# observed during Phase 1 discovery.
LEXFIND_ENTITY_IDS: dict[str, int] = {
    "ai": 1,   # Appenzell Innerrhoden
    "ar": 2,   # Appenzell Ausserrhoden
    "ag": 3,   # Aargau
    "be": 4,   # Bern
    "bl": 5,   # Basel-Landschaft
    "bs": 6,   # Basel-Stadt
    "fr": 7,   # Fribourg
    "ge": 8,   # Genève
    "gl": 9,   # Glarus
    "gr": 10,  # Graubünden
    "ju": 11,  # Jura
    "lu": 12,  # Luzern
    "ne": 13,  # Neuchâtel
    "nw": 14,  # Nidwalden
    "ow": 15,  # Obwalden
    "sg": 16,  # St. Gallen
    "sh": 17,  # Schaffhausen
    "so": 18,  # Solothurn
    "sz": 19,  # Schwyz
    "tg": 20,  # Thurgau
    "ti": 21,  # Ticino
    "ur": 22,  # Uri
    "vd": 23,  # Vaud
    "vs": 24,  # Valais
    "zg": 25,  # Zug
    "zh": 26,  # Zürich
}


def _walk_lexwork_document(node: object, lang: str) -> str:
    """Recursively concatenate `html_content[lang]` from a LexWork document tree.

    The LexWork `show_as_json` response wraps the law's HTML in a nested
    tree of nodes (`{uid, type, number, html_content, text, children}`).
    Every node carries its own HTML fragment in `html_content[lang]` and
    may contain child nodes. We walk depth-first, emitting each node's
    HTML once, in document order.
    """
    chunks: list[str] = []

    def visit(n):
        if isinstance(n, dict):
            hc = n.get("html_content")
            if isinstance(hc, dict):
                piece = hc.get(lang) or hc.get("de") or ""
                if piece:
                    chunks.append(piece)
            for child in (n.get("children") or []):
                visit(child)
        elif isinstance(n, list):
            for item in n:
                visit(item)

    if isinstance(node, dict):
        for key in ("header", "content", "footer"):
            sub = node.get(key)
            if sub is not None:
                visit(sub)
        # `annex_documents` is a sibling list of annex trees with the same
        # node shape; walk them too so annexes appear in the markdown.
        annexes = node.get("annex_documents") or []
        if isinstance(annexes, list):
            for a in annexes:
                visit(a)
    else:
        visit(node)

    return "\n".join(chunks)


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

    # ─── LexWork API (re-mapped to 2026 endpoints) ─────────────────────────────
    #
    # Each LexWork canton's portal exposes a Sitrox-built JSON API directly on
    # the same hostname (no `decwork_hostname` proxy needed for public reads):
    #
    #   GET https://{host}/api/{lang}/texts_of_law/lightweight_index
    #     → {category_id: [{id, systematic_number, title, abrogated, ...}]}
    #
    #   GET https://{host}/api/{lang}/texts_of_law/{systematic_number}/show_as_json
    #     → {text_of_law: {systematic_number, title, abbreviation, enactment,
    #                      publication_enactment, current_version, selected_version}}
    #       selected_version.json_content.document = {header, content, footer,
    #                                                 annex_documents}
    #       Each node has html_content[lang], type, number, children (tree).
    #
    # The old `/api/texts_of_law/{number}` and `/versions/{id}` paths from the
    # legacy code no longer exist; the new `show_as_json` returns the full HTML
    # body inline so we don't need a separate /text fetch.

    def _lexwork_base(self, canton: str) -> str:
        """Get the LexWork API base URL for a canton."""
        host = LEXWORK_CANTONS[canton]
        return f"https://{host}/api"

    def fetch_lexwork_law(self, canton: str, number: str,
                          lang: str = "de") -> dict | None:
        """Fetch a law's full metadata + HTML body. Returns raw JSON response."""
        base = self._lexwork_base(canton)
        url = f"{base}/{lang}/texts_of_law/{number}/show_as_json"
        return self._get_json(url)

    def fetch_lexwork_version(self, canton: str, number: str, version_id: int) -> dict | None:
        """Fetch a specific historical version.

        The 2026 LexWork API doesn't expose per-version fetch on a stable
        path; `show_as_json` returns the *currently-selected* version.
        Callers that need a specific historical version should fetch via
        the PDF URL surfaced under `selected_version.pdf_link_tol` and
        rely on git history for older snapshots.
        """
        return None

    def fetch_lexwork_catalog(self, canton: str, lang: str = "de") -> list[CantonalLawEntry]:
        """Fetch the full catalog of laws for a LexWork canton.

        Uses `/api/{lang}/texts_of_law/lightweight_index`, which returns a
        compact mapping of category_id → list of laws. Categories are
        flattened; only the (systematic_number, title, abrogated) triple
        per law is preserved here — the rest is fetched lazily by
        `fetch_law_text`.
        """
        if canton not in LEXWORK_CANTONS:
            return self.fetch_lexfind_catalog(canton, lang)

        base = self._lexwork_base(canton)
        url = f"{base}/{lang}/texts_of_law/lightweight_index"
        data = self._get_json(url)
        if not data or not isinstance(data, dict):
            return []

        entries: list[CantonalLawEntry] = []
        seen: set[str] = set()
        for laws in data.values():
            if not isinstance(laws, list):
                continue
            for law in laws:
                sr = str(law.get("systematic_number") or "").strip()
                if not sr or sr in seen:
                    continue
                seen.add(sr)
                entries.append(CantonalLawEntry(
                    canton=canton,
                    systematic_number=sr,
                    title=str(law.get("title") or ""),
                    is_active=not law.get("abrogated", False),
                ))
        return entries

    # ─── LexFind API (re-mapped to 2026 endpoints) ─────────────────────────────
    #
    # LexFind exposes a JSON catalog of texts per canton-entity:
    #
    #   GET /api/fe/{lang}/entities/{entity_id}/extended
    #     → {id, abbreviation, name, status: {total_texts_of_law, ...}}
    #
    #   GET /api/fe/{lang}/entities/{entity_id}/recent-changes
    #     → {recent_changes: [{change_date, change_type, text_of_law, ...}]}
    #
    #   GET /api/fe/{lang}/texts-of-law/{tol_id}/with-version-groups
    #     → {id, systematic_number, dta_urls (PDF links to current text),
    #        families: [[{dtah_urls (per-version PDF), title, keywords,
    #                     info_badge, version_active_since, ...}]],
    #        entity: {id, abbreviation, name}}
    #
    # The bodies LexFind serves at `/tol/{id}/{lang}` and `/tolv/{ver_id}/{lang}`
    # are PDFs (not HTML), so the text path falls through to the canton's own
    # portal via `original_url`. For LexFind-only cantons (no LexWork host),
    # we capture the metadata + version dates from LexFind itself and store
    # an information-only markdown entry; the body link to the canton portal
    # is preserved in frontmatter.

    def fetch_lexfind_catalog(self, canton: str, lang: str = "de") -> list[CantonalLawEntry]:
        """Fetch the LexFind-known catalog of laws for a canton.

        Uses `/api/fe/{lang}/entities/{id}/recent-changes` as the working
        list. (LexFind no longer exposes a single full-catalog endpoint
        for an entity; recent-changes plus pagination covers what's
        published.) Each `text_of_law` block yields one entry.
        """
        entity_id = LEXFIND_ENTITY_IDS.get(canton)
        if entity_id is None:
            return []
        url = (
            f"https://www.lexfind.ch/api/fe/{lang}/entities/"
            f"{entity_id}/recent-changes"
        )
        data = self._get_json(url)
        if not isinstance(data, dict):
            return []

        entries: list[CantonalLawEntry] = []
        seen: set[str] = set()
        for change in data.get("recent_changes", []) or []:
            tol = change.get("text_of_law") or {}
            sr = str(tol.get("systematic_number") or "").strip()
            tol_id = tol.get("id")
            if not sr or sr in seen:
                continue
            seen.add(sr)
            entries.append(CantonalLawEntry(
                canton=canton,
                systematic_number=sr,
                title=str(tol.get("title") or ""),
                is_active=bool(tol.get("is_active", True)),
                lexfind_id=str(tol_id) if tol_id is not None else "",
            ))
        return entries

    def fetch_lexfind_law_metadata(self, tol_id: str,
                                   lang: str = "de") -> dict | None:
        """Fetch a LexFind TOL's metadata + version groups (JSON)."""
        url = (
            f"https://www.lexfind.ch/api/fe/{lang}/texts-of-law/"
            f"{tol_id}/with-version-groups"
        )
        return self._get_json(url)

    def fetch_lexfind_text(self, tol_id: str, lang: str = "de") -> str:
        """Return the canton-portal URL where the law text actually lives.

        LexFind's `/tol/{id}/{lang}` endpoint now serves the law as a
        PDF rather than HTML. The metadata response carries
        `dta_urls[*].original_url` pointing back at the source canton
        portal, which is where the rich (HTML) representation lives.
        Returning that URL lets the pipeline either embed the link in
        frontmatter or hop to the canton's own LexWork API.
        """
        meta = self.fetch_lexfind_law_metadata(tol_id, lang)
        if not isinstance(meta, dict):
            return ""
        for url_entry in meta.get("dta_urls", []) or []:
            if (url_entry.get("language") or "") == lang:
                return str(url_entry.get("original_url") or "")
        return ""

    # ─── Unified fetch methods ─────────────────────────────────────────────────

    def fetch_law_text(self, canton: str, number: str,
                       lang: str = "de",
                       lexfind_id: str = "") -> CantonalLawText | None:
        """Fetch current law text. Prefers the canton's LexWork portal (rich
        HTML body) and falls back to LexFind for cantons without one
        (metadata + PDF link only).
        """
        if canton in LEXWORK_CANTONS:
            text = self._fetch_from_lexwork(canton, number, lang)
            if text:
                return text
            logger.debug("LexWork failed for %s/%s, trying LexFind", canton, number)

        if lexfind_id:
            return self._fetch_from_lexfind(canton, number, lexfind_id, lang)
        return None

    def _fetch_from_lexwork(self, canton: str, number: str,
                            lang: str = "de") -> CantonalLawText | None:
        """Fetch law text from LexWork API."""
        data = self.fetch_lexwork_law(canton, number, lang)
        if not data:
            return None

        tol = data.get("text_of_law", {})
        sv = tol.get("selected_version", {})
        json_content = sv.get("json_content") or {}
        document = json_content.get("document") or {}
        if not document:
            return None

        html = _walk_lexwork_document(document, lang)
        if not html.strip():
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
            html_content=html,
            language=lang,
            version_date=version_date,
            abbreviation=abbr,
        )

    def _fetch_from_lexfind(self, canton: str, number: str,
                            tol_id: str, lang: str = "de") -> CantonalLawText | None:
        """Build a metadata-only law text from LexFind for cantons without
        their own LexWork portal.

        LexFind itself only serves the body as a PDF (`/tol/{id}/{lang}`),
        so the markdown body produced here is a short pointer block with
        the law's title, version date, and direct links to the PDF +
        canton-portal page. Users who want the full text follow the
        links. Once a LexFind-only canton ships an HTML API, this method
        can be upgraded to fetch a real body.
        """
        meta = self.fetch_lexfind_law_metadata(tol_id, lang)
        if not isinstance(meta, dict):
            return None

        title = ""
        version_date: date | None = None
        # Pick the version with `info_badge == "current"`. Walk all
        # family→chain→version triples; first-current wins. If none is
        # marked current, take the first version we see (oldest first
        # since families are ordered by recency in the API response).
        for group in meta.get("families") or []:
            for chain in (group or []):
                current = next(
                    (v for v in chain or [] if v.get("info_badge") == "current"),
                    None,
                )
                ver = current or (chain[0] if chain else None)
                if ver is None:
                    continue
                title = title or str(ver.get("title") or "")
                active = ver.get("version_active_since") or ""
                if active:
                    try:
                        day, month, year = active.split(".")
                        version_date = date(int(year), int(month), int(day))
                    except (ValueError, AttributeError):
                        pass
                if current is not None:
                    break
            if version_date is not None:
                break

        sr = str(meta.get("systematic_number") or number)
        pdf_url = ""
        original_url = ""
        for url_entry in meta.get("dta_urls") or []:
            if (url_entry.get("language") or "") == lang:
                pdf_url = f"https://www.lexfind.ch{url_entry.get('url') or ''}"
                original_url = str(url_entry.get("original_url") or "")
                break

        # Emit a minimal HTML stub: title + intro + link list. The cantonal
        # transformer converts it to markdown just like any other body, so
        # the result is consistent with LexWork laws downstream.
        html_parts: list[str] = []
        html_parts.append(f"<h1>{title or sr}</h1>")
        html_parts.append(
            "<p><em>Cantonal law surfaced via LexFind. The authoritative body "
            "is served as a PDF — follow the links below.</em></p><ul>"
        )
        if pdf_url:
            html_parts.append(f'<li>PDF: <a href="{pdf_url}">{pdf_url}</a></li>')
        if original_url:
            html_parts.append(
                f'<li>Source portal: <a href="{original_url}">{original_url}</a></li>'
            )
        html_parts.append("</ul>")

        return CantonalLawText(
            canton=canton,
            systematic_number=sr,
            title=title,
            html_content="".join(html_parts),
            language=lang,
            version_date=version_date,
        )

    def fetch_versions(self, canton: str, number: str) -> list[CantonalLawVersion]:
        """Fetch all available versions of a cantonal law (LexWork only)."""
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

    Structure: kt/{canton}/{lang}/{number}.md

    Examples:
        kt/bs/de/300.100.md
        kt/zh/de/131.1.md
        kt/ge/fr/A.2.05.md

    Cantonal law lives under `kt/` (Kantone) to keep it visually separate
    from the federal `ch/` (Confoederatio Helvetica) tree, which is keyed
    by SR number rather than canton code.
    """
    return f"kt/{canton}/{language}/{systematic_number}.md"


def cantonal_law_to_markdown(text: CantonalLawText) -> str:
    """Convert cantonal law text to Markdown with frontmatter.

    Uses the cantonal transformer which handles source-specific HTML formats:
    - LexWork XHTML: converts single-row tables to lists, formats § headings
    - LexFind HTML: extracts body from full-page HTML, strips navigation
    - ZHLex HTML: handles Zürich's semantic HTML structure
    """
    source = "lexwork" if text.canton in LEXWORK_CANTONS else "lexfind"
    meta = {
        "canton": text.canton.upper(),
        "systematic_number": text.systematic_number,
        "title": text.title,
        "language": text.language,
        "source": "LexWork" if source == "lexwork" else "LexFind",
    }
    if text.version_date:
        meta["version_date"] = text.version_date.isoformat()
    if text.abbreviation:
        meta["abbreviation"] = text.abbreviation

    import yaml
    frontmatter = "---\n" + yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip() + "\n---"

    body = transform_cantonal_html(text.html_content, source=source) if text.html_content else ""
    if not body:
        body = f"# {text.title}\n\n*No text content available.*"

    return frontmatter + "\n\n" + body + "\n"
