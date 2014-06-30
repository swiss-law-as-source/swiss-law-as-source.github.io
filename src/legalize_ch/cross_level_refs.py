"""Detect cross-level references between federal and cantonal laws.

Scans cantonal law markdown files for references to federal SR numbers,
and federal laws for references to cantonal laws. Produces a structured
JSON mapping of cross-level links (federal ↔ cantonal).

Detection strategies:
  1. Explicit SR number references (e.g. "SR 935.61", "SR [935.61](...)")
  2. LexWork/clex Bund URLs (e.g. db.clex.ch/link/Bund/935.61/de)
  3. Federal law abbreviations in cantonal text (e.g. BGFA, KVG, OR)
  4. "Einführungsgesetz" pattern — implementing law title detection
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Canton codes (lowercase)
CANTON_CODES = {
    "ag", "ai", "ar", "be", "bl", "bs", "fr", "ge", "gl", "gr",
    "ju", "lu", "ne", "nw", "ow", "sg", "sh", "so", "sz", "tg",
    "ti", "ur", "vd", "vs", "zg", "zh",
}

CANTON_NAMES = {
    "ag": "Aargau", "ai": "Appenzell Innerrhoden", "ar": "Appenzell Ausserrhoden",
    "be": "Bern", "bl": "Basel-Landschaft", "bs": "Basel-Stadt",
    "fr": "Fribourg", "ge": "Genève", "gl": "Glarus", "gr": "Graubünden",
    "ju": "Jura", "lu": "Luzern", "ne": "Neuchâtel", "nw": "Nidwalden",
    "ow": "Obwalden", "sg": "St. Gallen", "sh": "Schaffhausen",
    "so": "Solothurn", "sz": "Schwyz", "tg": "Thurgau", "ti": "Ticino",
    "ur": "Uri", "vd": "Vaud", "vs": "Valais", "zg": "Zug", "zh": "Zürich",
}


# ─── Frontmatter Parsing ─────────────────────────────────────────────────────

def _parse_frontmatter(path: Path) -> dict | None:
    """Extract YAML frontmatter fields from a markdown file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None

    fm: dict[str, str] = {}
    for line in text[4:end].split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            fm[key] = value
    return fm


def _read_body(path: Path) -> str:
    """Read the body (after frontmatter) of a markdown file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


# ─── Federal Abbreviation Map ────────────────────────────────────────────────

def build_abbreviation_map(ch_dir: Path) -> dict[str, str]:
    """Build a mapping of abbreviation → SR number from federal law frontmatter.

    Only includes abbreviations that are >= 2 characters and look like legal
    abbreviations (mostly uppercase or contain uppercase + lowercase).
    """
    abbr_map: dict[str, str] = {}

    for subdir in sorted(ch_dir.iterdir()):
        if not subdir.is_dir() or subdir.name in CANTON_CODES:
            continue
        lang_dir = subdir / "de"
        if not lang_dir.exists():
            continue
        for md_file in lang_dir.glob("*.md"):
            fm = _parse_frontmatter(md_file)
            if not fm:
                continue
            sr = fm.get("sr_number", "")
            abbr = fm.get("abbreviation", "")
            if sr and abbr and len(abbr) >= 2:
                # Filter out noisy abbreviations (pure numbers, etc.)
                if re.match(r"^[A-ZÄÖÜ]", abbr):
                    abbr_map[abbr] = sr

    return abbr_map


# ─── Reference Detection ─────────────────────────────────────────────────────

# Pattern for explicit SR references: "SR 220", "SR 935.61", "SR 0.101.02"
_SR_PATTERN = re.compile(
    r"\bSR\s+\[?(\d{1,3}(?:\.\d+)*)\]?"
)

# Pattern for clex Bund links: db.clex.ch/link/Bund/935.61/de
_CLEX_BUND_PATTERN = re.compile(
    r"db\.clex\.ch/link/Bund/(\d{1,3}(?:\.\d+)*)"
)

# Pattern for fedlex URIs containing SR numbers
_FEDLEX_SR_PATTERN = re.compile(
    r"fedlex\.data\.admin\.ch[^\s]*?/cc/(\d{1,3}(?:\.\d+)*)"
)


def _detect_sr_references(body: str) -> set[str]:
    """Detect explicit federal SR number references in law body text."""
    refs: set[str] = set()

    for m in _SR_PATTERN.finditer(body):
        refs.add(m.group(1))

    for m in _CLEX_BUND_PATTERN.finditer(body):
        refs.add(m.group(1))

    for m in _FEDLEX_SR_PATTERN.finditer(body):
        refs.add(m.group(1))

    return refs


def _detect_abbreviation_references(body: str, abbr_map: dict[str, str]) -> set[str]:
    """Detect federal law abbreviation references in law body text.

    Only matches abbreviations that appear as whole words (word boundaries).
    Filters out very short or ambiguous abbreviations.
    """
    refs: set[str] = set()

    # Only try abbreviations >= 3 chars to avoid false positives
    # For 2-char abbreviations, require them to be well-known
    well_known_2char = {"OR", "BV", "ZG"}  # common 2-char federal abbreviations

    for abbr, sr in abbr_map.items():
        if len(abbr) < 2:
            continue
        if len(abbr) == 2 and abbr not in well_known_2char:
            continue

        # Use word boundary matching
        pattern = r"\b" + re.escape(abbr) + r"\b"
        if re.search(pattern, body):
            refs.add(sr)

    return refs


def _detect_einfuehrungsgesetz(title: str, body: str) -> set[str]:
    """Detect if a cantonal law is an 'Einführungsgesetz' implementing a federal law.

    These laws explicitly implement federal statutes and have titles like:
    'Einführungsgesetz zum Bundesgesetz über die Freizügigkeit der Anwältinnen...'
    """
    refs: set[str] = set()

    # Check title and first ~500 chars of body for SR references after "Bundesgesetz"
    context = (title + " " + body[:1000]).lower()

    if "einführungsgesetz" in context or "einfuhrungsgesetz" in context:
        # Try to find an SR number nearby
        sr_matches = _SR_PATTERN.findall(body[:1000])
        refs.update(sr_matches)

    return refs


# ─── Collector Functions ──────────────────────────────────────────────────────

@dataclass
class CrossLevelRef:
    """A cross-level reference between a cantonal and a federal law."""
    canton: str
    cantonal_number: str
    cantonal_title: str
    federal_sr: str
    ref_type: str  # "explicit_sr", "abbreviation", "einfuehrungsgesetz", "url"

    def to_dict(self) -> dict:
        return {
            "canton": self.canton,
            "cantonal_number": self.cantonal_number,
            "cantonal_title": self.cantonal_title,
            "federal_sr": self.federal_sr,
            "ref_type": self.ref_type,
        }


def _collect_federal_sr_set(ch_dir: Path) -> set[str]:
    """Collect all known federal SR numbers."""
    sr_set: set[str] = set()
    for subdir in sorted(ch_dir.iterdir()):
        if not subdir.is_dir() or subdir.name in CANTON_CODES:
            continue
        lang_dir = subdir / "de"
        if not lang_dir.exists():
            continue
        for md_file in lang_dir.glob("*.md"):
            fm = _parse_frontmatter(md_file)
            if fm and fm.get("sr_number"):
                sr_set.add(fm["sr_number"])
    return sr_set


def scan_cantonal_to_federal(
    ch_dir: Path,
    abbr_map: dict[str, str],
    federal_srs: set[str],
) -> list[CrossLevelRef]:
    """Scan all cantonal law files for references to federal laws.

    Returns a list of CrossLevelRef objects.
    """
    refs: list[CrossLevelRef] = []

    for canton_dir in sorted(ch_dir.iterdir()):
        if not canton_dir.is_dir() or canton_dir.name not in CANTON_CODES:
            continue

        canton = canton_dir.name

        # Try de first, then fr, it
        for lang in ("de", "fr", "it"):
            lang_dir = canton_dir / lang
            if not lang_dir.exists():
                continue

            for md_file in sorted(lang_dir.glob("*.md")):
                fm = _parse_frontmatter(md_file)
                if not fm:
                    continue

                sys_num = fm.get("systematic_number", md_file.stem)
                title = fm.get("title", "")
                body = _read_body(md_file)

                seen: set[str] = set()

                # 1. Explicit SR references
                for sr in _detect_sr_references(body):
                    if sr in federal_srs and sr not in seen:
                        refs.append(CrossLevelRef(
                            canton=canton,
                            cantonal_number=sys_num,
                            cantonal_title=title,
                            federal_sr=sr,
                            ref_type="explicit_sr",
                        ))
                        seen.add(sr)

                # 2. Abbreviation references
                for sr in _detect_abbreviation_references(body, abbr_map):
                    if sr in federal_srs and sr not in seen:
                        refs.append(CrossLevelRef(
                            canton=canton,
                            cantonal_number=sys_num,
                            cantonal_title=title,
                            federal_sr=sr,
                            ref_type="abbreviation",
                        ))
                        seen.add(sr)

                # 3. Einführungsgesetz detection
                for sr in _detect_einfuehrungsgesetz(title, body):
                    if sr in federal_srs and sr not in seen:
                        refs.append(CrossLevelRef(
                            canton=canton,
                            cantonal_number=sys_num,
                            cantonal_title=title,
                            federal_sr=sr,
                            ref_type="einfuehrungsgesetz",
                        ))
                        seen.add(sr)

            break  # Only process one language per canton

    return refs


def scan_federal_to_cantonal(
    ch_dir: Path,
    cantonal_numbers: dict[str, set[str]],
) -> list[CrossLevelRef]:
    """Scan federal law files for references to cantonal law numbers.

    This is less common but can occur in annotations or footnotes.
    cantonal_numbers is a dict of canton -> set of systematic numbers.
    """
    refs: list[CrossLevelRef] = []

    # Build a pattern for each canton's law numbers
    canton_patterns: dict[str, re.Pattern] = {}
    for canton, numbers in cantonal_numbers.items():
        if not numbers:
            continue
        # Only match numbers that look like cantonal refs (e.g., "SAR 290.100")
        # Canton abbreviation prefixes used in law references
        prefix_map = {
            "ag": "SAR", "ai": "GS", "ar": "bGS", "be": "BSG", "bl": "SGS",
            "bs": "SG", "fr": "SGF", "ge": "RSG", "gl": "GS", "gr": "BR",
            "ju": "RSJU", "lu": "SRL", "ne": "RSN", "nw": "NG", "ow": "GDB",
            "sg": "sGS", "sh": "SHR", "so": "BGS", "sz": "SRSZ", "tg": "RB",
            "ti": "RL", "ur": "RB", "vd": "BLV", "vs": "SGS", "zg": "BGS",
            "zh": "LS",
        }
        prefix = prefix_map.get(canton, "")
        if prefix:
            # Match "SAR 290.100" etc.
            escaped_numbers = [re.escape(n) for n in sorted(numbers)]
            if len(escaped_numbers) <= 200:  # Only create pattern for manageable sets
                pattern_str = r"\b" + re.escape(prefix) + r"\s+(" + "|".join(escaped_numbers) + r")\b"
                try:
                    canton_patterns[canton] = re.compile(pattern_str)
                except re.error:
                    pass

    if not canton_patterns:
        return refs

    # Scan federal files
    for subdir in sorted(ch_dir.iterdir()):
        if not subdir.is_dir() or subdir.name in CANTON_CODES:
            continue
        lang_dir = subdir / "de"
        if not lang_dir.exists():
            continue
        for md_file in sorted(lang_dir.glob("*.md")):
            fm = _parse_frontmatter(md_file)
            if not fm:
                continue
            sr = fm.get("sr_number", "")
            body = _read_body(md_file)
            if not body:
                continue

            for canton, pattern in canton_patterns.items():
                for m in pattern.finditer(body):
                    cantonal_num = m.group(1)
                    refs.append(CrossLevelRef(
                        canton=canton,
                        cantonal_number=cantonal_num,
                        cantonal_title="",
                        federal_sr=sr,
                        ref_type="federal_cites_cantonal",
                    ))

    return refs


# ─── Main Entry Points ───────────────────────────────────────────────────────

@dataclass
class CrossLevelResult:
    """Result of cross-level reference analysis."""
    cantonal_to_federal: list[CrossLevelRef] = field(default_factory=list)
    federal_to_cantonal: list[CrossLevelRef] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cantonal_to_federal) + len(self.federal_to_cantonal)

    def to_dict(self) -> dict:
        """Convert to a serializable dict structure.

        Groups cantonal→federal refs by canton, then by cantonal law.
        Groups federal→cantonal refs by federal SR.
        Also generates reverse lookups for the HTML viewer.
        """
        # cantonal_to_federal: grouped by canton -> cantonal_number -> list of federal SRs
        c2f_by_canton: dict[str, dict[str, list[dict]]] = {}
        for ref in self.cantonal_to_federal:
            canton_data = c2f_by_canton.setdefault(ref.canton, {})
            law_refs = canton_data.setdefault(ref.cantonal_number, [])
            law_refs.append({
                "federal_sr": ref.federal_sr,
                "ref_type": ref.ref_type,
            })

        # federal cited by cantonal: federal_sr -> list of {canton, cantonal_number}
        federal_cited_by: dict[str, list[dict]] = {}
        for ref in self.cantonal_to_federal:
            entries = federal_cited_by.setdefault(ref.federal_sr, [])
            entry = {"canton": ref.canton, "cantonal_number": ref.cantonal_number,
                     "cantonal_title": ref.cantonal_title}
            if entry not in entries:
                entries.append(entry)

        # federal_to_cantonal: federal_sr -> list of {canton, cantonal_number}
        f2c: dict[str, list[dict]] = {}
        for ref in self.federal_to_cantonal:
            entries = f2c.setdefault(ref.federal_sr, [])
            entry = {"canton": ref.canton, "cantonal_number": ref.cantonal_number}
            if entry not in entries:
                entries.append(entry)

        # Summary stats
        cantons_with_refs = set(r.canton for r in self.cantonal_to_federal)
        federal_laws_referenced = set(r.federal_sr for r in self.cantonal_to_federal)
        cantonal_laws_referencing = set(
            (r.canton, r.cantonal_number) for r in self.cantonal_to_federal
        )

        return {
            "total_cross_level_references": self.total,
            "cantonal_to_federal_count": len(self.cantonal_to_federal),
            "federal_to_cantonal_count": len(self.federal_to_cantonal),
            "cantons_with_references": len(cantons_with_refs),
            "federal_laws_referenced": len(federal_laws_referenced),
            "cantonal_laws_referencing": len(cantonal_laws_referencing),
            "cantonal_to_federal": c2f_by_canton,
            "federal_cited_by_cantonal": federal_cited_by,
            "federal_to_cantonal": f2c,
        }


def analyze_cross_level_refs(repo_path: str = ".") -> CrossLevelResult:
    """Run the full cross-level reference analysis.

    Scans all cantonal and federal law files, detects references, and
    returns a CrossLevelResult with all detected links.
    """
    ch_dir = Path(repo_path) / "ch"
    if not ch_dir.exists():
        raise FileNotFoundError(f"Directory not found: {ch_dir}")

    logger.info("Building federal abbreviation map...")
    abbr_map = build_abbreviation_map(ch_dir)
    logger.info("  Found %d abbreviation mappings", len(abbr_map))

    logger.info("Collecting federal SR numbers...")
    federal_srs = _collect_federal_sr_set(ch_dir)
    logger.info("  Found %d federal SR numbers", len(federal_srs))

    logger.info("Scanning cantonal laws for federal references...")
    c2f = scan_cantonal_to_federal(ch_dir, abbr_map, federal_srs)
    logger.info("  Found %d cantonal→federal references", len(c2f))

    # Collect cantonal numbers for reverse scan
    cantonal_numbers: dict[str, set[str]] = {}
    for canton_dir in ch_dir.iterdir():
        if not canton_dir.is_dir() or canton_dir.name not in CANTON_CODES:
            continue
        canton = canton_dir.name
        nums: set[str] = set()
        for lang in ("de", "fr", "it"):
            lang_dir = canton_dir / lang
            if not lang_dir.exists():
                continue
            for md_file in lang_dir.glob("*.md"):
                fm = _parse_frontmatter(md_file)
                if fm and fm.get("systematic_number"):
                    nums.add(fm["systematic_number"])
            break
        if nums:
            cantonal_numbers[canton] = nums

    logger.info("Scanning federal laws for cantonal references...")
    f2c = scan_federal_to_cantonal(ch_dir, cantonal_numbers)
    logger.info("  Found %d federal→cantonal references", len(f2c))

    return CrossLevelResult(cantonal_to_federal=c2f, federal_to_cantonal=f2c)


def write_cross_level_json(repo_path: str = ".") -> Path:
    """Analyze cross-level references and write JSON output.

    Writes to docs/cross_level_refs.json.

    Returns:
        Path to the written JSON file.
    """
    result = analyze_cross_level_refs(repo_path)
    data = result.to_dict()

    out_path = Path(repo_path) / "docs" / "cross_level_refs.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Written: %s (%d refs)", out_path, result.total)
    return out_path


def write_cross_level_html(repo_path: str = ".") -> Path:
    """Generate the cross-level references HTML viewer page.

    Writes to docs/cross_level_refs.html.
    """
    out_path = Path(repo_path) / "docs" / "cross_level_refs.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_CROSS_LEVEL_HTML, encoding="utf-8")
    logger.info("Written: %s", out_path)
    return out_path


# ─── HTML Template ────────────────────────────────────────────────────────────

_CROSS_LEVEL_HTML = """\
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cross-Level References — Federal ↔ Cantonal Law</title>
    <style>
        :root {
            --red: #D52B1E;
            --dark: #1a1a1a;
            --gray: #f5f5f5;
            --border: #e0e0e0;
            --blue: #2563eb;
            --green: #16a34a;
            --orange: #ea580c;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            color: var(--dark);
            line-height: 1.6;
            background: #fff;
        }
        header {
            background: var(--red);
            color: white;
            padding: 1.5rem;
            text-align: center;
        }
        header h1 { font-size: 1.6rem; margin-bottom: 0.2rem; }
        header p { opacity: 0.9; font-size: 0.9rem; }
        nav {
            background: var(--dark);
            padding: 0.5rem 2rem;
            text-align: center;
        }
        nav a {
            color: #ccc;
            text-decoration: none;
            margin: 0 0.75rem;
            font-size: 0.9rem;
        }
        nav a:hover { color: white; }
        main {
            max-width: 1100px;
            margin: 2rem auto;
            padding: 0 1.5rem;
        }
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }
        .kpi {
            background: var(--gray);
            border-radius: 8px;
            padding: 1.2rem;
            text-align: center;
        }
        .kpi .value {
            font-size: 2rem;
            font-weight: 700;
            color: var(--red);
        }
        .kpi .label {
            font-size: 0.85rem;
            color: #666;
            margin-top: 0.3rem;
        }
        .tab-bar {
            display: flex;
            gap: 0;
            margin-bottom: 0;
            border-bottom: 2px solid var(--border);
        }
        .tab-btn {
            padding: 0.6rem 1.5rem;
            border: none;
            background: none;
            font-size: 0.95rem;
            cursor: pointer;
            color: #666;
            border-bottom: 3px solid transparent;
            margin-bottom: -2px;
        }
        .tab-btn.active {
            color: var(--red);
            border-bottom-color: var(--red);
            font-weight: 600;
        }
        .tab-panel { display: none; padding-top: 1.5rem; }
        .tab-panel.active { display: block; }
        .search-bar {
            width: 100%;
            padding: 0.6rem 1rem;
            border: 1px solid var(--border);
            border-radius: 6px;
            font-size: 0.95rem;
            margin-bottom: 1.5rem;
        }
        .canton-section {
            margin-bottom: 2rem;
        }
        .canton-header {
            font-size: 1.2rem;
            margin-bottom: 0.8rem;
            padding-bottom: 0.4rem;
            border-bottom: 2px solid var(--red);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .canton-header .badge {
            background: var(--red);
            color: white;
            border-radius: 10px;
            padding: 0.1rem 0.6rem;
            font-size: 0.8rem;
        }
        .ref-card {
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 0.8rem;
            background: #fff;
        }
        .ref-card h4 {
            font-size: 0.95rem;
            margin-bottom: 0.4rem;
        }
        .ref-card .ref-type {
            display: inline-block;
            font-size: 0.7rem;
            padding: 0.1rem 0.4rem;
            border-radius: 3px;
            margin-left: 0.5rem;
            font-weight: 500;
        }
        .ref-type-explicit_sr { background: #dbeafe; color: #1e40af; }
        .ref-type-abbreviation { background: #dcfce7; color: #166534; }
        .ref-type-einfuehrungsgesetz { background: #fef3c7; color: #92400e; }
        .ref-type-url { background: #ede9fe; color: #5b21b6; }
        .ref-type-federal_cites_cantonal { background: #fce7f3; color: #9d174d; }
        .ref-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
            margin-top: 0.5rem;
        }
        .ref-tag {
            background: var(--gray);
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 0.2rem 0.6rem;
            font-size: 0.8rem;
            font-family: "SF Mono", "Fira Code", monospace;
            text-decoration: none;
            color: var(--dark);
            transition: all 0.15s;
        }
        .ref-tag:hover {
            background: var(--red);
            color: white;
            border-color: var(--red);
        }
        .ref-tag.federal { border-left: 3px solid var(--blue); }
        .ref-tag.cantonal { border-left: 3px solid var(--orange); }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
            margin-bottom: 1.5rem;
        }
        th, td {
            padding: 0.5rem 0.75rem;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }
        th { background: var(--gray); font-weight: 600; }
        tr:hover { background: #fafafa; }
        footer {
            text-align: center;
            padding: 2rem;
            color: #666;
            font-size: 0.85rem;
            border-top: 1px solid var(--border);
            margin-top: 3rem;
        }
        footer a { color: var(--red); text-decoration: none; }
    </style>
</head>
<body>
    <header>
        <h1>Cross-Level References</h1>
        <p>Federal ↔ Cantonal Law Links — Swiss Legal System</p>
    </header>
    <nav>
        <a href="index.html">Search</a>
        <a href="crossrefs.html">Federal Cross-Refs</a>
        <a href="cross_level_refs.html"><strong>Federal ↔ Cantonal</strong></a>
        <a href="diff.html">Diff Viewer</a>
        <a href="stats.html">Statistics</a>
    </nav>
    <main>
        <div class="kpi-grid" id="kpis"></div>

        <div class="tab-bar">
            <button class="tab-btn active" data-tab="by-canton">By Canton</button>
            <button class="tab-btn" data-tab="by-federal">By Federal Law</button>
            <button class="tab-btn" data-tab="top-referenced">Most Referenced</button>
        </div>

        <div id="by-canton" class="tab-panel active">
            <input type="text" class="search-bar" id="search-canton"
                   placeholder="Search by canton, law number, or title...">
            <div id="canton-list"></div>
        </div>

        <div id="by-federal" class="tab-panel">
            <input type="text" class="search-bar" id="search-federal"
                   placeholder="Search by federal SR number...">
            <div id="federal-list"></div>
        </div>

        <div id="top-referenced" class="tab-panel">
            <h3 style="margin-bottom: 1rem;">Most Referenced Federal Laws</h3>
            <table id="top-table">
                <thead>
                    <tr><th>SR Number</th><th>Referenced by</th><th>Cantons</th></tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </main>
    <footer>
        <a href="https://github.com/benjamin-arfa/swiss-law">swiss-law</a> —
        Cross-level reference analysis generated automatically.
    </footer>

    <script>
    const CANTON_NAMES = {
        "ag":"Aargau","ai":"Appenzell I.","ar":"Appenzell A.","be":"Bern",
        "bl":"Basel-Land","bs":"Basel-Stadt","fr":"Fribourg","ge":"Genève",
        "gl":"Glarus","gr":"Graubünden","ju":"Jura","lu":"Luzern",
        "ne":"Neuchâtel","nw":"Nidwalden","ow":"Obwalden","sg":"St.Gallen",
        "sh":"Schaffhausen","so":"Solothurn","sz":"Schwyz","tg":"Thurgau",
        "ti":"Ticino","ur":"Uri","vd":"Vaud","vs":"Valais","zg":"Zug","zh":"Zürich"
    };

    const REF_TYPE_LABELS = {
        "explicit_sr": "SR ref",
        "abbreviation": "Abbreviation",
        "einfuehrungsgesetz": "Implementing law",
        "url": "URL",
        "federal_cites_cantonal": "Federal → Cantonal"
    };

    let DATA = null;

    async function loadData() {
        const resp = await fetch("cross_level_refs.json");
        DATA = await resp.json();
        renderKPIs();
        renderByCantonTab();
        renderByFederalTab();
        renderTopTable();
    }

    function renderKPIs() {
        const el = document.getElementById("kpis");
        el.innerHTML = `
            <div class="kpi"><div class="value">${DATA.total_cross_level_references}</div><div class="label">Total References</div></div>
            <div class="kpi"><div class="value">${DATA.cantonal_to_federal_count}</div><div class="label">Cantonal → Federal</div></div>
            <div class="kpi"><div class="value">${DATA.federal_to_cantonal_count}</div><div class="label">Federal → Cantonal</div></div>
            <div class="kpi"><div class="value">${DATA.cantons_with_references}</div><div class="label">Cantons with Refs</div></div>
            <div class="kpi"><div class="value">${DATA.federal_laws_referenced}</div><div class="label">Federal Laws Referenced</div></div>
            <div class="kpi"><div class="value">${DATA.cantonal_laws_referencing}</div><div class="label">Cantonal Laws Referencing</div></div>
        `;
    }

    function renderByCantonTab() {
        const container = document.getElementById("canton-list");
        const c2f = DATA.cantonal_to_federal;
        let html = "";
        for (const canton of Object.keys(c2f).sort()) {
            const laws = c2f[canton];
            const lawCount = Object.keys(laws).length;
            const refCount = Object.values(laws).reduce((s, a) => s + a.length, 0);
            html += `<div class="canton-section" data-canton="${canton}">`;
            html += `<div class="canton-header">
                <span>${canton.toUpperCase()} — ${CANTON_NAMES[canton] || canton}</span>
                <span class="badge">${lawCount} laws, ${refCount} refs</span>
            </div>`;
            for (const [lawNum, refs] of Object.entries(laws).sort()) {
                html += `<div class="ref-card" data-law="${lawNum}">`;
                html += `<h4>${canton.toUpperCase()} ${lawNum}`;
                const types = [...new Set(refs.map(r => r.ref_type))];
                for (const t of types) {
                    html += ` <span class="ref-type ref-type-${t}">${REF_TYPE_LABELS[t] || t}</span>`;
                }
                html += `</h4>`;
                html += `<div class="ref-tags">`;
                for (const ref of refs) {
                    const sr = ref.federal_sr;
                    const prefix = sr.split(".")[0];
                    html += `<a class="ref-tag federal" href="https://github.com/benjamin-arfa/swiss-law/blob/main/ch/${prefix}/de/${sr}.md" title="SR ${sr}">SR ${sr}</a>`;
                }
                html += `</div></div>`;
            }
            html += `</div>`;
        }
        container.innerHTML = html || "<p>No cantonal → federal references found.</p>";
    }

    function renderByFederalTab() {
        const container = document.getElementById("federal-list");
        const cited = DATA.federal_cited_by_cantonal;
        let html = "";
        for (const sr of Object.keys(cited).sort()) {
            const entries = cited[sr];
            const prefix = sr.split(".")[0];
            html += `<div class="ref-card" data-sr="${sr}">`;
            html += `<h4><a class="ref-tag federal" href="https://github.com/benjamin-arfa/swiss-law/blob/main/ch/${prefix}/de/${sr}.md">SR ${sr}</a>`;
            html += ` <span class="badge">${entries.length} cantonal ref(s)</span></h4>`;
            html += `<div class="ref-tags">`;
            for (const e of entries) {
                html += `<a class="ref-tag cantonal" title="${e.cantonal_title || ''}">${e.canton.toUpperCase()} ${e.cantonal_number}</a>`;
            }
            html += `</div></div>`;
        }
        container.innerHTML = html || "<p>No federal laws cited by cantonal laws found.</p>";
    }

    function renderTopTable() {
        const tbody = document.querySelector("#top-table tbody");
        const cited = DATA.federal_cited_by_cantonal;
        const rows = Object.entries(cited)
            .map(([sr, entries]) => ({sr, count: entries.length, cantons: [...new Set(entries.map(e => e.canton))]}))
            .sort((a, b) => b.count - a.count)
            .slice(0, 50);

        let html = "";
        for (const r of rows) {
            html += `<tr>
                <td style="font-family:monospace">SR ${r.sr}</td>
                <td>${r.count} cantonal law(s)</td>
                <td>${r.cantons.map(c => c.toUpperCase()).join(", ")}</td>
            </tr>`;
        }
        tbody.innerHTML = html;
    }

    // Tab switching
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            btn.classList.add("active");
            document.getElementById(btn.dataset.tab).classList.add("active");
        });
    });

    // Search filtering
    document.getElementById("search-canton")?.addEventListener("input", (e) => {
        const q = e.target.value.toLowerCase();
        document.querySelectorAll("#canton-list .canton-section").forEach(section => {
            const text = section.textContent.toLowerCase();
            section.style.display = text.includes(q) ? "" : "none";
        });
    });

    document.getElementById("search-federal")?.addEventListener("input", (e) => {
        const q = e.target.value.toLowerCase();
        document.querySelectorAll("#federal-list .ref-card").forEach(card => {
            const text = card.textContent.toLowerCase();
            card.style.display = text.includes(q) ? "" : "none";
        });
    });

    loadData();
    </script>
</body>
</html>
"""
