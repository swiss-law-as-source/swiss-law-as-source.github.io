"""Validate markdown law files — detect empty bodies, broken frontmatter."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Required keys in every law file's frontmatter
REQUIRED_FRONTMATTER_KEYS = {"sr_number", "title", "language", "version_date", "source"}
VALID_LANGUAGES = {"de", "fr", "it", "rm", "en"}
# ISO-date pattern (YYYY-MM-DD)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Frontmatter delimiter
FM_DELIM = "---"
# Stub marker text produced by law_to_markdown when no content is available
STUB_MARKER = "No text content available for this version."


@dataclass
class ValidationResult:
    """Result of validating a single markdown file."""

    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_stub: bool = False

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def validate_markdown(text: str, path: str = "<string>") -> ValidationResult:
    """Validate a markdown law file's content.

    Checks performed:
    - Frontmatter is present and delimited by ``---``
    - Frontmatter is valid YAML
    - All required keys are present and non-empty
    - ``language`` value is one of the known set
    - ``version_date`` looks like an ISO date
    - Body (after frontmatter) is non-empty
    - Detects stub files (placeholder text only)
    """
    result = ValidationResult(path=path)

    if not text or not text.strip():
        result.errors.append("file is empty")
        return result

    # --- frontmatter parsing ---------------------------------------------------
    lines = text.split("\n")

    if lines[0].strip() != FM_DELIM:
        result.errors.append("missing opening frontmatter delimiter (---)")
        return result

    # Find closing delimiter
    closing_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == FM_DELIM:
            closing_idx = i
            break

    if closing_idx is None:
        result.errors.append("missing closing frontmatter delimiter (---)")
        return result

    fm_text = "\n".join(lines[1:closing_idx])
    try:
        meta = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        result.errors.append(f"invalid YAML in frontmatter: {exc}")
        return result

    if not isinstance(meta, dict):
        result.errors.append("frontmatter did not parse as a mapping")
        return result

    # --- required keys ---------------------------------------------------------
    for key in REQUIRED_FRONTMATTER_KEYS:
        val = meta.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            result.errors.append(f"missing or empty frontmatter key: {key}")

    # --- language check --------------------------------------------------------
    lang = meta.get("language")
    if isinstance(lang, str) and lang not in VALID_LANGUAGES:
        result.errors.append(f"unknown language: {lang!r}")

    # --- version_date format ---------------------------------------------------
    vd = meta.get("version_date")
    if vd is not None:
        vd_str = str(vd)
        if not DATE_RE.match(vd_str):
            result.errors.append(f"version_date is not ISO format: {vd_str!r}")

    # --- body ------------------------------------------------------------------
    body = "\n".join(lines[closing_idx + 1 :]).strip()

    if not body:
        result.errors.append("empty body after frontmatter")
    elif STUB_MARKER in body:
        result.is_stub = True
        result.warnings.append("file is a stub (no machine-readable text on Fedlex)")

    # Detect body that is *only* a heading with no real content after it
    body_lines = [l for l in body.split("\n") if l.strip()]
    if body_lines and all(l.startswith("#") for l in body_lines):
        result.warnings.append("body contains only headings, no paragraph text")

    return result


def validate_file(path: str | Path) -> ValidationResult:
    """Read and validate a markdown law file from disk."""
    p = Path(path)
    if not p.exists():
        res = ValidationResult(path=str(p))
        res.errors.append("file does not exist")
        return res
    text = p.read_text(encoding="utf-8")
    return validate_markdown(text, path=str(p))


def validate_directory(directory: str | Path) -> list[ValidationResult]:
    """Validate all ``*.md`` files under *directory* (recursive)."""
    results: list[ValidationResult] = []
    for md_file in sorted(Path(directory).rglob("*.md")):
        results.append(validate_file(md_file))
    return results
