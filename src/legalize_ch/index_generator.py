"""Generate INDEX.md with all SR numbers, titles, and links (federal + cantonal)."""
from __future__ import annotations

import json
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

# Canton full names
CANTON_NAMES = {
    "ag": "Aargau",
    "ai": "Appenzell Innerrhoden",
    "ar": "Appenzell Ausserrhoden",
    "be": "Bern",
    "bl": "Basel-Landschaft",
    "bs": "Basel-Stadt",
    "fr": "Fribourg",
    "ge": "Genève",
    "gl": "Glarus",
    "gr": "Graubünden",
    "ju": "Jura",
    "lu": "Luzern",
    "ne": "Neuchâtel",
    "nw": "Nidwalden",
    "ow": "Obwalden",
    "sg": "St. Gallen",
    "sh": "Schaffhausen",
    "so": "Solothurn",
    "sz": "Schwyz",
    "tg": "Thurgau",
    "ti": "Ticino",
    "ur": "Uri",
    "vd": "Vaud",
    "vs": "Valais",
    "zg": "Zug",
    "zh": "Zürich",
}

# Known federal SR prefix directories (numeric)
_FEDERAL_PREFIXES = set(str(i) for i in range(10))


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
            if key in ("sr_number", "title", "language", "version_date",
                       "canton", "systematic_number", "abbreviation"):
                frontmatter[key] = value
    return frontmatter if ("sr_number" in frontmatter or "systematic_number" in frontmatter) else None


def _is_canton_dir(name: str) -> bool:
    """Check if a directory name is a canton code (not a federal SR prefix)."""
    return name in CANTON_NAMES


def _collect_federal_entries(ch_dir: Path, lang: str) -> dict[str, str]:
    """Collect federal law entries (sr_number -> title)."""
    entries: dict[str, str] = {}

    for subdir in sorted(ch_dir.iterdir()):
        if not subdir.is_dir():
            continue
        # Federal directories are numeric (0-9xx)
        if _is_canton_dir(subdir.name):
            continue
        # Look for language-specific files
        lang_dir = subdir / lang
        if not lang_dir.exists():
            continue
        for md_file in sorted(lang_dir.glob("*.md")):
            fm = _extract_frontmatter(md_file)
            if fm and fm.get("sr_number"):
                sr = fm["sr_number"]
                title = fm.get("title", "(kein Titel)")
                if sr not in entries:
                    entries[sr] = title

    return entries


def _collect_cantonal_entries(ch_dir: Path, lang: str = "de") -> dict[str, list[tuple[str, str]]]:
    """Collect cantonal law entries grouped by canton.

    Returns:
        dict: canton_code -> list of (systematic_number, title)
    """
    cantonal: dict[str, list[tuple[str, str]]] = {}

    for subdir in sorted(ch_dir.iterdir()):
        if not subdir.is_dir():
            continue
        if not _is_canton_dir(subdir.name):
            continue

        canton = subdir.name
        # Try the specified language first, fall back to de, then any available
        lang_dir = subdir / lang
        if not lang_dir.exists():
            lang_dir = subdir / "de"
        if not lang_dir.exists():
            # Try any language dir that has files
            for alt_lang in ("fr", "it"):
                alt_dir = subdir / alt_lang
                if alt_dir.exists() and any(alt_dir.glob("*.md")):
                    lang_dir = alt_dir
                    break
            else:
                continue

        entries = []
        for md_file in sorted(lang_dir.glob("*.md")):
            fm = _extract_frontmatter(md_file)
            if fm:
                sys_num = fm.get("systematic_number", md_file.stem)
                title = fm.get("title", "(kein Titel)")
                entries.append((sys_num, title))

        if entries:
            cantonal[canton] = entries

    return cantonal


def generate_index(repo_path: str = ".", lang: str = "de") -> str:
    """Generate INDEX.md content from all markdown files (federal + cantonal).

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

    # Collect federal entries
    entries = _collect_federal_entries(ch_dir, lang)
    # Collect cantonal entries
    cantonal = _collect_cantonal_entries(ch_dir, lang)

    total_cantonal = sum(len(v) for v in cantonal.values())
    logger.info(f"Found {len(entries)} federal + {total_cantonal} cantonal laws ({lang})")

    # Build INDEX.md
    lines: list[str] = []
    lines.append("# Index of Swiss Law (Systematische Rechtssammlung)")
    lines.append("")
    lines.append(f"Total: **{len(entries)}** federal laws, **{total_cantonal}** cantonal laws indexed")
    lines.append("")
    lines.append("---")
    lines.append("")

    # === Federal Laws ===
    lines.append("# Federal Laws (Bundesrecht)")
    lines.append("")

    # Group by top-level category
    categorized: dict[str, list[tuple[str, str]]] = {}
    for sr, title in sorted(entries.items(), key=lambda x: _sr_sort_key(x[0])):
        cat = sr.split(".")[0] if "." in sr else sr
        top = cat.split(".")[0]
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
            sr_prefix = sr.split(".")[0]
            link = f"ch/{sr_prefix}/{lang}/{sr}.md"
            display_title = title if len(title) <= 120 else title[:117] + "..."
            display_title = display_title.replace("|", "\\|")
            lines.append(f"| [{sr}]({link}) | {display_title} |")

        lines.append("")

    # === Cantonal Laws ===
    if cantonal:
        lines.append("---")
        lines.append("")
        lines.append("# Cantonal Laws (Kantonsrecht)")
        lines.append("")
        lines.append(f"*{total_cantonal} laws across {len(cantonal)} canton(s)*")
        lines.append("")

        for canton in sorted(cantonal.keys()):
            canton_name = CANTON_NAMES.get(canton, canton.upper())
            canton_entries = cantonal[canton]
            lines.append(f"## {canton.upper()} – {canton_name}")
            lines.append("")
            lines.append(f"*{len(canton_entries)} laws*")
            lines.append("")
            lines.append("| Systematic Number | Title |")
            lines.append("|-------------------|-------|")

            for sys_num, title in canton_entries:
                # Determine which lang dir was actually used
                link = f"ch/{canton}/{lang}/{sys_num}.md"
                display_title = title if len(title) <= 120 else title[:117] + "..."
                display_title = display_title.replace("|", "\\|")
                lines.append(f"| [{sys_num}]({link}) | {display_title} |")

            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Generated automatically by the swiss-law pipeline.*")
    lines.append("")

    return "\n".join(lines)


def generate_laws_json(repo_path: str = ".", lang: str = "de") -> list[dict]:
    """Generate a JSON array of all laws (federal + cantonal) for GitHub Pages.

    Returns:
        List of dicts with keys: sr, title, path, cat, scope, canton (optional)
    """
    repo = Path(repo_path)
    ch_dir = repo / "ch"

    if not ch_dir.exists():
        raise FileNotFoundError(f"Directory not found: {ch_dir}")

    laws: list[dict] = []

    # Federal
    entries = _collect_federal_entries(ch_dir, lang)
    for sr, title in sorted(entries.items(), key=lambda x: _sr_sort_key(x[0])):
        sr_prefix = sr.split(".")[0]
        top_cat = sr_prefix[0] if sr_prefix else "0"
        cat_name = SR_CATEGORIES.get(top_cat, f"Kategorie {top_cat}")
        laws.append({
            "sr": sr,
            "title": title,
            "path": f"ch/{sr_prefix}/{lang}/{sr}.md",
            "cat": f"{top_cat} – {cat_name}",
            "scope": "federal",
        })

    # Cantonal
    cantonal = _collect_cantonal_entries(ch_dir, lang)
    for canton in sorted(cantonal.keys()):
        canton_name = CANTON_NAMES.get(canton, canton.upper())
        for sys_num, title in cantonal[canton]:
            laws.append({
                "sr": sys_num,
                "title": title,
                "path": f"ch/{canton}/{lang}/{sys_num}.md",
                "cat": f"{canton.upper()} – {canton_name}",
                "scope": "cantonal",
                "canton": canton,
            })

    return laws


def _sr_sort_key(sr: str) -> tuple:
    """Convert SR number to a sortable tuple of numeric parts."""
    parts = sr.split(".")
    result = []
    for p in parts:
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


def write_laws_json(repo_path: str = ".", lang: str = "de") -> Path:
    """Generate and write docs/laws.json for GitHub Pages.

    Returns:
        Path to the written laws.json file.
    """
    laws = generate_laws_json(repo_path=repo_path, lang=lang)
    out_path = Path(repo_path) / "docs" / "laws.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(laws, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Written: {out_path} ({len(laws)} entries)")
    return out_path
