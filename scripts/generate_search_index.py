#!/usr/bin/env python3
"""Generate a JSON search index from INDEX.md for the GitHub Pages site."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_MD = ROOT / "INDEX.md"
OUTPUT = ROOT / "docs" / "laws.json"


def parse_index():
    """Parse INDEX.md and extract law entries."""
    entries = []
    current_category = ""

    with open(INDEX_MD, "r", encoding="utf-8") as f:
        for line in f:
            # Detect category headers (## N – Title)
            cat_match = re.match(r"^## (\d+) [–—-] (.+)$", line.strip())
            if cat_match:
                current_category = f"{cat_match.group(1)} – {cat_match.group(2)}"
                continue

            # Detect table rows with law entries
            row_match = re.match(
                r"^\| \[([^\]]+)\]\(([^)]+)\) \| (.+) \|$", line.strip()
            )
            if row_match:
                sr_number = row_match.group(1)
                path = row_match.group(2)
                title = row_match.group(3).strip()
                entries.append(
                    {
                        "sr": sr_number,
                        "title": title,
                        "path": path,
                        "cat": current_category,
                    }
                )

    return entries


def main():
    entries = parse_index()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    print(f"Generated {len(entries)} entries in {OUTPUT}")


if __name__ == "__main__":
    main()
