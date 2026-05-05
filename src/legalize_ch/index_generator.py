"""Generate INDEX.md with all SR numbers, titles, and links."""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# SR category names (top-level classification)
SR_CATEGORIES = {
    "0": "Systematische Sammlung des Bundesrechts (Völkerrecht)",
    "1": "Staat – Volk – Behörden",
    "2": "Privatrecht – Zivilrechtspflege – Vollstreckung",
    "3": "Strafrecht – Strafrechtspflege – Strafvollzug",
    "4": "Schule – Wissenschaft – Kultur",
    "5": "Landesverteidigung",
    "6": "Finanzen",
    "7": "Öffentliche Werke – Energie – Verkehr",
    "8": "Gesundheit – Arbeit – Soziale Sicherheit",
    "9": "Wirtschaft – Technische Zusammenarbeit",
}


def _extract_frontmatter(path: Path) -> dict | None:
    """Extract YAML frontmatter from a markdown file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    if not text.startswith("---"):
        return None

    end = text.find("\n---", 3)
    if end == -1:
        return None

    frontmatter = {}
    for line in text[4:end].split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key in ("sr_number", "title", "language", "version_date"):
                frontmatter[key] = value
    return frontmatter if "sr_number" in frontmatter else None


def generate_index(repo_path: str = ".", lang: str = "de") -> str:
    """Generate INDEX.md content from all markdown files.

    Args:
        repo_path: Path to the swiss-law repo root.
        lang: Language to use for titles (de, fr, it).

    Returns:
        The full INDEX.md content as a string.
    """
    repo = Path(repo_path)
    ch_dir = repo / "ch"

    if not ch_dir.exists():
        raise FileNotFoundError(f"Directory not found: {ch_dir}")

    # Collect all SR entries from the specified language
    entries: dict[str, str] = {}  # sr_number -> title

    for md_file in sorted(ch_dir.rglob(f"*/{lang}/*.md")):
        fm = _extract_frontmatter(md_file)
        if fm and fm.get("sr_number"):
            sr = fm["sr_number"]
            title = fm.get("title", "(kein Titel)")
            # Keep the first title we find (files are sorted, so earliest SR)
            if sr not in entries:
                entries[sr] = title

    logger.info(f"Found {len(entries)} unique SR numbers ({lang})")

    # Build INDEX.md
    lines: list[str] = []
    lines.append("# Index of Swiss Federal Law (Systematische Rechtssammlung)")
    lines.append("")
    lines.append(f"Total: **{len(entries)}** laws indexed")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group by top-level category
    categorized: dict[str, list[tuple[str, str]]] = {}
    for sr, title in sorted(entries.items(), key=lambda x: _sr_sort_key(x[0])):
        cat = sr.split(".")[0] if "." in sr else sr
        # Top-level category is the first digit(s) before the first dot
        top = cat.split(".")[0]
        # Map to broader category
        top_cat = top[0] if top else "0"
        categorized.setdefault(top_cat, []).append((sr, title))

    for cat_num in sorted(categorized.keys()):
        cat_name = SR_CATEGORIES.get(cat_num, f"Kategorie {cat_num}")
        cat_entries = categorized[cat_num]
        lines.append(f"## {cat_num} – {cat_name}")
        lines.append("")
        lines.append(f"*{len(cat_entries)} laws*")
        lines.append("")
        lines.append("| SR Number | Title |")
        lines.append("|-----------|-------|")

        for sr, title in cat_entries:
            # Create relative link to the file
            sr_prefix = sr.split(".")[0]
            link = f"ch/{sr_prefix}/{lang}/{sr}.md"
            # Truncate very long titles
            display_title = title if len(title) <= 120 else title[:117] + "..."
            # Escape pipe characters in title
            display_title = display_title.replace("|", "\\|")
            lines.append(f"| [{sr}]({link}) | {display_title} |")

        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Generated automatically by the swiss-law pipeline.*")
    lines.append("")

    return "\n".join(lines)


def _sr_sort_key(sr: str) -> tuple:
    """Convert SR number to a sortable tuple of numeric parts.

    Handles cases like 0.101.02 vs 0.101.1 correctly by treating
    each dotted segment as an integer for sorting.
    """
    parts = sr.split(".")
    result = []
    for p in parts:
        # Handle sub-parts with hyphens (e.g., "101-1")
        sub_parts = p.split("-")
        for sp in sub_parts:
            try:
                result.append(int(sp))
            except ValueError:
                result.append(0)
    return tuple(result)


def write_index(repo_path: str = ".", lang: str = "de") -> Path:
    """Generate and write INDEX.md to the repo root.

    Returns:
        Path to the written INDEX.md file.
    """
    content = generate_index(repo_path=repo_path, lang=lang)
    out_path = Path(repo_path) / "INDEX.md"
    out_path.write_text(content, encoding="utf-8")
    logger.info(f"Written: {out_path} ({len(content)} bytes)")
    return out_path
