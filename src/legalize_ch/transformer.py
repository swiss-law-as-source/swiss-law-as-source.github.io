"""Transform Akoma Ntoso XML / HTML law text to Markdown with YAML frontmatter."""
from __future__ import annotations

import re
from datetime import date

import html2text
import yaml
from lxml import etree


AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def akn_to_markdown(xml_text: str) -> str:
    """Convert Akoma Ntoso XML to Markdown."""
    if not xml_text:
        return ""
    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return html_to_markdown(xml_text)

    ns = {"akn": AKN_NS}
    lines = []

    # Extract the act body
    body = root.find(f".//{{{AKN_NS}}}body")
    if body is None:
        body = root.find(f".//{{{AKN_NS}}}mainBody")
    if body is None:
        # Fallback: just extract all text
        return _extract_text(root)

    _process_element(body, lines, ns, depth=0)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _process_element(elem, lines: list, ns: dict, depth: int):
    """Recursively process AKN elements into Markdown lines."""
    tag = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""

    if tag in ("title", "longTitle"):
        text = _get_text(elem).strip()
        if text:
            lines.append(f"# {text}")
            lines.append("")

    elif tag == "preface":
        text = _get_text(elem).strip()
        if text:
            lines.append(text)
            lines.append("")

    elif tag == "preamble":
        text = _get_text(elem).strip()
        if text:
            lines.append(f"*{text}*")
            lines.append("")

    elif tag == "book":
        heading = _find_heading(elem, ns)
        if heading:
            lines.append(f"# {heading}")
            lines.append("")
        for child in elem:
            _process_element(child, lines, ns, depth)

    elif tag == "part":
        heading = _find_heading(elem, ns)
        if heading:
            lines.append(f"## {heading}")
            lines.append("")
        for child in elem:
            _process_element(child, lines, ns, depth)

    elif tag in ("chapter", "title") and elem.getparent() is not None:
        heading = _find_heading(elem, ns)
        if heading:
            lines.append(f"### {heading}")
            lines.append("")
        for child in elem:
            _process_element(child, lines, ns, depth)

    elif tag == "section":
        heading = _find_heading(elem, ns)
        if heading:
            lines.append(f"#### {heading}")
            lines.append("")
        for child in elem:
            _process_element(child, lines, ns, depth)

    elif tag == "article":
        heading = _find_heading(elem, ns)
        num = elem.find(f"{{{AKN_NS}}}num")
        num_text = _get_text(num).strip() if num is not None else ""
        if num_text and heading:
            lines.append(f"**{num_text}** {heading}")
        elif num_text:
            lines.append(f"**{num_text}**")
        elif heading:
            lines.append(f"**{heading}**")
        lines.append("")
        for child in elem:
            if isinstance(child.tag, str):
                child_tag = etree.QName(child.tag).localname
                if child_tag not in ("num", "heading"):
                    _process_element(child, lines, ns, depth + 1)

    elif tag == "paragraph":
        num = elem.find(f"{{{AKN_NS}}}num")
        num_text = _get_text(num).strip() if num is not None else ""
        content_elem = elem.find(f"{{{AKN_NS}}}content")
        if content_elem is None:
            content_elem = elem.find(f"{{{AKN_NS}}}list")

        if content_elem is not None:
            text = _get_text(content_elem).strip()
            if num_text:
                lines.append(f"{num_text} {text}")
            elif text:
                lines.append(text)
        else:
            text = _get_direct_text(elem).strip()
            if num_text and text:
                lines.append(f"{num_text} {text}")
            elif text:
                lines.append(text)

        lines.append("")
        # Process sub-elements (lists, etc.)
        for child in elem:
            if isinstance(child.tag, str):
                child_tag = etree.QName(child.tag).localname
                if child_tag not in ("num", "content", "heading"):
                    _process_element(child, lines, ns, depth + 1)

    elif tag in ("point", "item", "indent"):
        num = elem.find(f"{{{AKN_NS}}}num")
        num_text = _get_text(num).strip() if num is not None else "-"
        content_elem = elem.find(f"{{{AKN_NS}}}content")
        text = _get_text(content_elem).strip() if content_elem is not None else _get_direct_text(elem).strip()
        prefix = "  " * depth
        if text:
            lines.append(f"{prefix}{num_text} {text}")

    elif tag in ("blockContainer", "container"):
        for child in elem:
            _process_element(child, lines, ns, depth)

    elif tag == "p":
        text = _get_text(elem).strip()
        if text:
            lines.append(text)
            lines.append("")

    elif tag == "list":
        for child in elem:
            _process_element(child, lines, ns, depth + 1)
        lines.append("")

    elif tag in ("division", "subdivision", "subpart", "subchapter", "subsection"):
        heading = _find_heading(elem, ns)
        if heading:
            hashes = "#" * min(depth + 2, 6)
            lines.append(f"{hashes} {heading}")
            lines.append("")
        for child in elem:
            _process_element(child, lines, ns, depth + 1)

    elif tag == "formula":
        text = _get_text(elem).strip()
        if text:
            lines.append(f"*{text}*")
            lines.append("")

    elif tag == "conclusions":
        text = _get_text(elem).strip()
        if text:
            lines.append("---")
            lines.append(text)
            lines.append("")

    else:
        # Generic: process children
        for child in elem:
            if isinstance(child.tag, str):
                _process_element(child, lines, ns, depth)


def _find_heading(elem, ns: dict) -> str:
    """Find heading text for a structural element."""
    heading = elem.find(f"{{{AKN_NS}}}heading")
    num = elem.find(f"{{{AKN_NS}}}num")
    parts = []
    if num is not None:
        parts.append(_get_text(num).strip())
    if heading is not None:
        parts.append(_get_text(heading).strip())
    return " ".join(parts)


def _get_text(elem) -> str:
    """Get all text content of an element including children."""
    if elem is None:
        return ""
    return "".join(elem.itertext())


def _get_direct_text(elem) -> str:
    """Get only the direct text content (not from children with specific tags)."""
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        if child.tail:
            parts.append(child.tail)
    return " ".join(parts)


def _extract_text(root) -> str:
    """Fallback: extract all text from XML."""
    return "\n".join(line.strip() for line in root.itertext() if line.strip())


def html_to_markdown(html: str) -> str:
    """Convert HTML to clean Markdown."""
    if not html:
        return ""
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_links = False
    h.ignore_images = True
    h.ignore_emphasis = False
    h.protect_links = True
    md = h.handle(html)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def build_frontmatter(
    sr_number: str,
    title: str,
    language: str,
    version_date: date,
    abbreviation: str = "",
    is_stub: bool = False,
) -> str:
    """Build YAML frontmatter for a law file."""
    meta = {
        "sr_number": sr_number,
        "title": title,
        "language": language,
        "version_date": version_date.isoformat(),
        "source": "https://fedlex.data.admin.ch",
    }
    if abbreviation:
        meta["abbreviation"] = abbreviation
    if is_stub:
        meta["stub"] = True
    return "---\n" + yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip() + "\n---"


def law_to_markdown(
    sr_number: str,
    title: str,
    xml_content: str,
    html_content: str,
    language: str,
    version_date: date,
    abbreviation: str = "",
    is_stub: bool = False,
) -> str:
    """Convert a law text to a full Markdown document with frontmatter.

    When *is_stub* is True the frontmatter includes ``stub: true`` so that
    downstream tooling can distinguish placeholder files from real content.
    """
    body = ""
    if xml_content:
        body = akn_to_markdown(xml_content)
    elif html_content:
        body = html_to_markdown(html_content)

    # If we still have no body text, mark as stub
    if not body:
        is_stub = True
        body = f"# {title}\n\n*No machine-readable text available on Fedlex for this law.*"

    fm = build_frontmatter(sr_number, title, language, version_date, abbreviation, is_stub=is_stub)
    return fm + "\n\n" + body + "\n"


def sr_to_path(sr_number: str, language: str) -> str:
    """Convert SR number to a file path.

    SR 101 -> ch/101/de/101.md
    SR 220.1 -> ch/220/de/220.1.md
    """
    parts = sr_number.split(".")
    base = parts[0]
    return f"ch/{base}/{language}/{sr_number}.md"
