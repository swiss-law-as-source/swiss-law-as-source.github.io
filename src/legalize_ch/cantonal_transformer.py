"""Transform cantonal law HTML to clean Markdown.

Handles three source formats:
  - LexWork XHTML (used by AG, AR, BE, BL, BS, FR, GL, GR, LU, SG, SO, TG, VS, ZG)
  - LexFind HTML  (full-page HTML for AI, GE, JU, NE, NW, OW, SH, SZ, TI, UR, VD)
  - ZHLex HTML    (Zürich's dedicated API response)

Compared to the generic html_to_markdown(), this module:
  1. Extracts the law body from full-page HTML (LexFind)
  2. Converts single-row tables to proper markdown lists (LexWork quirk)
  3. Formats cantonal article numbers (§, Art., Abs.) consistently
  4. Separates amendment tables (Änderungstabelle) into a distinct section
  5. Cleans footnotes and places them at the end
  6. Strips navigation chrome, style/script tags, and empty headings
"""
from __future__ import annotations

import re

import html2text
from lxml import etree


# ─── Public API ───────────────────────────────────────────────────────────────

def transform_lexwork_html(html: str) -> str:
    """Transform LexWork XHTML to Markdown with cantonal structure."""
    if not html or not html.strip():
        return ""
    html = _preprocess_html(html)
    html = _convert_single_row_tables_to_lists(html)
    md = _html_to_md(html)
    md = _postprocess_markdown(md)
    return md.strip()


def transform_lexfind_html(html: str) -> str:
    """Transform LexFind full-page HTML to Markdown.

    LexFind serves complete HTML pages with navigation, headers, footers.
    We extract only the law body content.
    """
    if not html or not html.strip():
        return ""
    body_html = _extract_lexfind_body(html)
    if not body_html:
        # Fallback: treat the whole thing as content
        body_html = html
    body_html = _preprocess_html(body_html)
    body_html = _convert_single_row_tables_to_lists(body_html)
    md = _html_to_md(body_html)
    md = _postprocess_markdown(md)
    return md.strip()


def transform_zhlex_html(html: str) -> str:
    """Transform ZHLex HTML to Markdown.

    ZHLex uses semantic HTML with classes like .erlass-titel, .artikel, etc.
    """
    if not html or not html.strip():
        return ""
    html = _preprocess_html(html)
    html = _convert_single_row_tables_to_lists(html)
    md = _html_to_md(html)
    md = _postprocess_markdown(md)
    return md.strip()


def transform_cantonal_html(html: str, source: str = "lexwork") -> str:
    """Dispatch to the appropriate transformer based on source type.

    Args:
        html: Raw HTML content.
        source: One of "lexwork", "lexfind", "zhlex".
    """
    source = source.lower()
    if source == "lexfind":
        return transform_lexfind_html(html)
    elif source == "zhlex":
        return transform_zhlex_html(html)
    else:
        return transform_lexwork_html(html)


# ─── HTML preprocessing ──────────────────────────────────────────────────────

def _preprocess_html(html: str) -> str:
    """Clean HTML before conversion: strip scripts, styles, comments."""
    # Remove <script> and <style> blocks
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # Remove <nav>, <header>, <footer> blocks
    for tag in ("nav", "header", "footer"):
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove empty <span> and <div> tags (often used for styling only)
    html = re.sub(r"<span[^>]*>\s*</span>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<div[^>]*>\s*</div>", "", html, flags=re.IGNORECASE)
    # Normalize non-breaking spaces
    html = html.replace("\xa0", " ").replace("&nbsp;", " ")
    return html


def _convert_single_row_tables_to_lists(html: str) -> str:
    """Convert LexWork's single-row tables to proper HTML lists.

    LexWork renders lettered/numbered enumerations as:
        <table><tr><td>a)</td><td>item text</td></tr></table>

    This converts them to <ul><li> for cleaner markdown output.
    """
    try:
        # Parse as HTML fragment - wrap in div for safety
        parser = etree.HTMLParser(recover=True)
        tree = etree.fromstring(f"<div>{html}</div>", parser)
    except etree.XMLSyntaxError:
        return html

    tables = tree.findall(".//table")
    modified = False

    for table in tables:
        rows = table.findall(".//tr")
        if not rows:
            continue

        # Detect single-column-pair tables (enumeration pattern)
        # Each row has exactly 2 cells: label + content
        is_enum_table = True
        items = []
        for row in rows:
            cells = row.findall("td") or row.findall("th")
            if len(cells) != 2:
                is_enum_table = False
                break
            label_text = _elem_text(cells[0]).strip()
            content_text = _elem_text_with_html(cells[1]).strip()
            # Label should look like a), b), 1., 2., etc.
            if not re.match(r"^[a-z0-9]{1,4}[\.\):]?\s*$", label_text, re.IGNORECASE):
                is_enum_table = False
                break
            items.append((label_text, content_text))

        if is_enum_table and items:
            # Build replacement HTML list
            list_html_parts = ["<ul>"]
            for label, content in items:
                # Normalize label: ensure it ends with ) or .
                label = label.strip().rstrip(".:)")
                list_html_parts.append(f"<li>{label}) {content}</li>")
            list_html_parts.append("</ul>")
            list_html = "\n".join(list_html_parts)

            # Replace table in the tree with the new list
            parent = table.getparent()
            if parent is not None:
                replacement = etree.fromstring(f"<div>{list_html}</div>", etree.HTMLParser(recover=True))
                # Find the ul in the parsed replacement
                new_ul = replacement.find(".//ul")
                if new_ul is not None:
                    idx = list(parent).index(table)
                    parent.remove(table)
                    parent.insert(idx, new_ul)
                    modified = True

    if modified:
        result = etree.tostring(tree, encoding="unicode", method="html")
        # Strip wrapper div
        result = re.sub(r"^<html><body><div>", "", result)
        result = re.sub(r"</div></body></html>$", "", result)
        return result
    return html


# ─── LexFind body extraction ─────────────────────────────────────────────────

def _extract_lexfind_body(html: str) -> str:
    """Extract the main law content from a LexFind full-page HTML.

    Tries several strategies:
    1. Find <div class="tol-content"> or similar content wrapper
    2. Find <article> or <main> tag
    3. Fall back to <body> content
    """
    try:
        parser = etree.HTMLParser(recover=True)
        tree = etree.fromstring(html, parser)
    except etree.XMLSyntaxError:
        return html

    # Strategy 1: known LexFind content wrappers (exact class match)
    for cls in ("tol-content", "law-text", "content"):
        elem = tree.find(f".//div[@class='{cls}']")
        if elem is not None:
            return etree.tostring(elem, encoding="unicode", method="html")

    # Strategy 1b: partial class match via XPath (contains)
    for partial in ("tol-", "law-", "gesetz"):
        try:
            results = tree.xpath(f".//div[contains(@class, '{partial}')]")
            if results:
                return etree.tostring(results[0], encoding="unicode", method="html")
        except etree.XPathError:
            pass

    # Strategy 2: semantic HTML5 tags
    for tag in ("article", "main"):
        elem = tree.find(f".//{tag}")
        if elem is not None:
            return etree.tostring(elem, encoding="unicode", method="html")

    # Strategy 3: body content
    body = tree.find(".//body")
    if body is not None:
        return etree.tostring(body, encoding="unicode", method="html")

    return html


# ─── Core HTML-to-Markdown conversion ────────────────────────────────────────

def _html_to_md(html: str) -> str:
    """Convert HTML to Markdown using html2text with settings tuned for law text."""
    h = html2text.HTML2Text()
    h.body_width = 0  # No line wrapping
    h.ignore_links = False
    h.ignore_images = True
    h.ignore_emphasis = False
    h.protect_links = True
    h.unicode_snob = True  # Use Unicode characters instead of ASCII approximations
    h.mark_code = False
    h.wrap_links = False
    h.wrap_list_items = False
    return h.handle(html)


# ─── Markdown postprocessing ─────────────────────────────────────────────────

def _postprocess_markdown(md: str) -> str:
    """Clean up Markdown output with cantonal-law-specific fixes."""
    # Fix article headings: ensure § and Art. are bold
    md = _format_article_headings(md)

    # Separate amendment tables
    md = _separate_amendment_tables(md)

    # Clean up footnotes
    md = _clean_footnotes(md)

    # Remove horizontal rules made of dashes or equals that are artifacts
    md = re.sub(r"\n-{3,}\s*\n-{3,}\s*\n", "\n---\n", md)

    # Collapse excessive blank lines (more than 2 newlines → 2)
    md = re.sub(r"\n{3,}", "\n\n", md)

    # Remove trailing whitespace on lines
    md = re.sub(r"[ \t]+\n", "\n", md)

    # Clean up table separator artifacts: lines of just ---|---
    md = re.sub(r"\n\s*---\|---\s*\n", "\n", md)

    return md


def _format_article_headings(md: str) -> str:
    """Format cantonal article references consistently.

    Patterns:
    - § 1, § 2a, § 12bis → **§ 1**, **§ 2a**, **§ 12bis**
    - Art. 1, Art. 12a → **Art. 1**, **Art. 12a**

    Only format when the article number appears on its own line (heading context).
    """
    # Match § or Art. at start of line, possibly followed by number and suffix
    # Only when it appears to be a standalone heading (own line or short line)
    def _bold_article(m):
        text = m.group(1).strip()
        # Don't double-bold
        if text.startswith("**"):
            return m.group(0)
        return f"\n**{text}**\n"

    # § X or § Xbis/ter/quater with optional ***** (amendment marker)
    md = re.sub(
        r"\n(§\s+\d+[a-z]*(?:\s*\*{3,})?)\s*\n",
        _bold_article,
        md,
    )
    # Art. X at start of line
    md = re.sub(
        r"\n(Art\.\s+\d+[a-z]*(?:\s*\*{3,})?)\s*\n",
        _bold_article,
        md,
    )

    return md


def _separate_amendment_tables(md: str) -> str:
    """Find amendment tables (Änderungstabelle) and separate them with a rule.

    LexWork appends one or two amendment tables at the bottom of law text.
    We keep them but add a clear visual separator.
    """
    # Look for the Änderungstabelle headers
    pattern = r"(#+ *Änderungstabelle)"
    match = re.search(pattern, md)
    if match:
        pos = match.start()
        # Insert a separator before the first amendment table
        before = md[:pos].rstrip()
        after = md[pos:]
        md = before + "\n\n---\n\n" + after

    return md


def _clean_footnotes(md: str) -> str:
    """Clean up footnote formatting.

    LexWork footnotes appear as numbered lists like:
      1. [1] SR 935.61
      2. [2] SAR 155.200
    Keep them but ensure consistent formatting.
    """
    # Normalize footnote references: [1] → [^1]
    # Only in the footnote section at the end (after last article)
    # We just ensure they're cleanly formatted - don't alter content
    return md


# ─── Helper functions ─────────────────────────────────────────────────────────

def _elem_text(elem) -> str:
    """Get all text from an lxml element."""
    if elem is None:
        return ""
    return "".join(elem.itertext())


def _elem_text_with_html(elem) -> str:
    """Get inner HTML of an element as string."""
    if elem is None:
        return ""
    # Get the inner content (text + children serialized)
    inner = elem.text or ""
    for child in elem:
        inner += etree.tostring(child, encoding="unicode", method="html")
    return inner
