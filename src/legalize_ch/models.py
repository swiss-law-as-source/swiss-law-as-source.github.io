"""Domain models for Swiss law pipeline."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date


@dataclass
class LawEntry:
    """A law in the classified compilation (CC/SR)."""
    sr_number: str          # e.g. "101" for the Federal Constitution
    uri: str                # Fedlex URI
    title_de: str = ""
    title_fr: str = ""
    title_it: str = ""
    date_in_force: date | None = None
    date_document: date | None = None
    abbreviation_de: str = ""
    abbreviation_fr: str = ""
    abbreviation_it: str = ""


@dataclass
class LawVersion:
    """A point-in-time consolidation of a law."""
    sr_number: str
    version_uri: str
    date_applicable: date
    date_in_force: date | None = None
    date_document: date | None = None  # jolux:dateDocument on the consolidation


@dataclass
class LawText:
    """Full text of a law version in one language."""
    sr_number: str
    language: str           # "de", "fr", "it"
    version_date: date
    title: str
    html_content: str = ""
    xml_content: str = ""
    content_url: str = ""


@dataclass
class LawRevision:
    """A revision/reform of a law — becomes a git commit."""
    sr_number: str
    date: date
    title_de: str = ""
    title_fr: str = ""
    title_it: str = ""
    texts: dict[str, str] = field(default_factory=dict)  # lang -> markdown
    message: str = ""
