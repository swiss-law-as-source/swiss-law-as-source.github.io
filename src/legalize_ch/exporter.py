"""Structured data export — JSON-LD and CSV of law metadata."""
from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Schema.org context for Swiss legislation
JSONLD_CONTEXT = {
    "@vocab": "https://schema.org/",
    "eli": "http://data.europa.eu/eli/ontology#",
    "sr": "https://fedlex.data.admin.ch/eli/cc/",
}

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

    frontmatter: dict[str, str] = {}
    for line in text[4:end].split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value:
                frontmatter[key] = value
    return frontmatter if "sr_number" in frontmatter else None


def collect_metadata(repo_path: str = ".", languages: list[str] | None = None,
                     sr_filter: str | None = None) -> list[dict]:
    """Collect metadata from all law markdown files.

    Args:
        repo_path: Path to the swiss-law repo root.
        languages: Languages to scan (default: de, fr, it).
        sr_filter: Only include SR numbers starting with this prefix.

    Returns:
        List of metadata dicts, one per law file.
    """
    if languages is None:
        languages = ["de", "fr", "it"]

    repo = Path(repo_path)
    ch_dir = repo / "ch"

    if not ch_dir.exists():
        raise FileNotFoundError(f"Directory not found: {ch_dir}")

    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (sr_number, language) dedup

    for lang in languages:
        for md_file in sorted(ch_dir.rglob(f"*/{lang}/*.md")):
            fm = _extract_frontmatter(md_file)
            if not fm or "sr_number" not in fm:
                continue

            sr = fm["sr_number"]
            file_lang = fm.get("language", lang)

            if sr_filter and not sr.startswith(sr_filter):
                continue

            key = (sr, file_lang)
            if key in seen:
                continue
            seen.add(key)

            entry = {
                "sr_number": sr,
                "language": file_lang,
                "title": fm.get("title", ""),
                "abbreviation": fm.get("abbreviation", ""),
                "version_date": fm.get("version_date", ""),
                "source": fm.get("source", "https://fedlex.data.admin.ch"),
            }

            # Derive category from SR number
            top = sr.split(".")[0]
            cat_key = top[0] if top else "0"
            entry["category"] = SR_CATEGORIES.get(cat_key, f"Kategorie {cat_key}")

            # Build Fedlex URI
            entry["fedlex_uri"] = f"https://fedlex.data.admin.ch/eli/cc/{sr}"

            # Relative file path
            sr_prefix = sr.split(".")[0]
            entry["file_path"] = f"ch/{sr_prefix}/{file_lang}/{sr}.md"

            entries.append(entry)

    logger.info(f"Collected metadata for {len(entries)} law files")
    return entries


def export_csv(entries: list[dict]) -> str:
    """Export law metadata as CSV string.

    Args:
        entries: List of metadata dicts from collect_metadata().

    Returns:
        CSV content as a string.
    """
    if not entries:
        return ""

    fieldnames = [
        "sr_number", "title", "abbreviation", "language",
        "version_date", "category", "fedlex_uri", "source", "file_path",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(entries)

    return output.getvalue()


def export_jsonld(entries: list[dict]) -> dict:
    """Export law metadata as a JSON-LD document.

    Uses schema.org Legislation type with ELI (European Legislation Identifier)
    properties for interoperability.

    Args:
        entries: List of metadata dicts from collect_metadata().

    Returns:
        JSON-LD document as a Python dict.
    """
    graph = []

    for entry in entries:
        node: dict = {
            "@type": "Legislation",
            "@id": entry["fedlex_uri"],
            "identifier": entry["sr_number"],
            "name": entry.get("title", ""),
            "inLanguage": entry.get("language", ""),
            "legislationIdentifier": entry["sr_number"],
            "isPartOf": {
                "@type": "LegislationObject",
                "name": "Systematische Rechtssammlung (SR)",
                "url": "https://www.fedlex.admin.ch/",
            },
        }

        if entry.get("abbreviation"):
            node["alternateName"] = entry["abbreviation"]

        if entry.get("version_date"):
            node["dateModified"] = entry["version_date"]
            node["temporalCoverage"] = entry["version_date"]

        if entry.get("category"):
            node["about"] = {
                "@type": "DefinedTerm",
                "name": entry["category"],
            }

        if entry.get("source"):
            node["sdDatePublished"] = entry.get("version_date", "")
            node["url"] = entry["fedlex_uri"]

        graph.append(node)

    doc = {
        "@context": JSONLD_CONTEXT,
        "@type": "Dataset",
        "name": "Swiss Federal Law (Systematische Rechtssammlung)",
        "description": "Machine-readable metadata for Swiss federal legislation from Fedlex",
        "url": "https://github.com/benjamin-arfa/swiss-law",
        "license": "https://creativecommons.org/publicdomain/zero/1.0/",
        "publisher": {
            "@type": "Organization",
            "name": "Fedlex (Swiss Federal Chancellery)",
            "url": "https://www.fedlex.admin.ch/",
        },
        "distribution": [
            {
                "@type": "DataDownload",
                "encodingFormat": "text/csv",
                "contentUrl": "exports/laws_metadata.csv",
            },
            {
                "@type": "DataDownload",
                "encodingFormat": "application/ld+json",
                "contentUrl": "exports/laws_metadata.jsonld",
            },
        ],
        "@graph": graph,
    }

    return doc


def write_csv(repo_path: str = ".", languages: list[str] | None = None,
              sr_filter: str | None = None) -> Path:
    """Collect metadata and write CSV file.

    Returns:
        Path to the written CSV file.
    """
    entries = collect_metadata(repo_path, languages, sr_filter)
    content = export_csv(entries)
    out_path = Path(repo_path) / "exports" / "laws_metadata.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    logger.info(f"Written CSV: {out_path} ({len(entries)} entries)")
    return out_path


def write_jsonld(repo_path: str = ".", languages: list[str] | None = None,
                 sr_filter: str | None = None) -> Path:
    """Collect metadata and write JSON-LD file.

    Returns:
        Path to the written JSON-LD file.
    """
    entries = collect_metadata(repo_path, languages, sr_filter)
    doc = export_jsonld(entries)
    out_path = Path(repo_path) / "exports" / "laws_metadata.jsonld"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(f"Written JSON-LD: {out_path} ({len(entries)} entries)")
    return out_path


def write_all(repo_path: str = ".", languages: list[str] | None = None,
              sr_filter: str | None = None) -> tuple[Path, Path]:
    """Generate both CSV and JSON-LD exports.

    Returns:
        Tuple of (csv_path, jsonld_path).
    """
    entries = collect_metadata(repo_path, languages, sr_filter)

    csv_content = export_csv(entries)
    csv_path = Path(repo_path) / "exports" / "laws_metadata.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(csv_content, encoding="utf-8")
    logger.info(f"Written CSV: {csv_path} ({len(entries)} entries)")

    jsonld_doc = export_jsonld(entries)
    jsonld_path = Path(repo_path) / "exports" / "laws_metadata.jsonld"
    jsonld_path.write_text(
        json.dumps(jsonld_doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(f"Written JSON-LD: {jsonld_path} ({len(entries)} entries)")

    return csv_path, jsonld_path
