#!/usr/bin/env python3
"""Repair existing generated OpenFisca files to make them importable.

Fixes:
- Bad imports (openfisca_switzerland, openfisca_core.common)
- Missing Person entity definition
- Enum value_type (unsupported without country package)
- Trailing explanation text
- Syntax errors (deletes unrecoverable files)
"""
import os
import py_compile
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

STANDARD_HEADER = """\
from openfisca_core.model_api import *
from openfisca_core.periods import MONTH, YEAR
from openfisca_core.entities import build_entity

Person = build_entity(key='person', plural='persons', label='An individual', is_person=True)
"""


def repair_file(filepath: Path) -> str:
    """Repair a single file. Returns status: 'ok', 'fixed', 'deleted'."""
    code = filepath.read_text(encoding="utf-8")
    original = code

    # Preserve docstring header
    header = ""
    if code.startswith('"""'):
        end = code.find('"""', 3)
        if end != -1:
            header = code[: end + 3] + "\n\n"
            code = code[end + 3 :].strip()

    # Remove bad imports
    code = re.sub(r"^from openfisca_switzerland.*$", "", code, flags=re.MULTILINE)
    code = re.sub(r"^from openfisca_core\.common.*$", "", code, flags=re.MULTILINE)
    code = re.sub(r"^import openfisca_switzerland.*$", "", code, flags=re.MULTILINE)

    # Remove all existing openfisca imports and Person definition (we'll add standard ones)
    code = re.sub(r"^from openfisca_core[^\n]*$", "", code, flags=re.MULTILINE)
    code = re.sub(r"^import openfisca_core[^\n]*$", "", code, flags=re.MULTILINE)
    code = re.sub(
        r"^Person\s*=\s*build_entity\(.*?\)\s*$", "", code, flags=re.MULTILINE
    )

    # Fix entity as string (any string value -> Person)
    code = re.sub(r"entity\s*=\s*[\"'][^\"']+[\"']", "entity = Person", code)

    # Fix definition_period as string
    for period in ("YEAR", "MONTH"):
        code = re.sub(
            rf'definition_period\s*=\s*["\'](?i:{period})["\']',
            f"definition_period = {period}",
            code,
        )

    # Fix Enum value_type
    code = re.sub(r"value_type\s*=\s*Enum", "value_type = bool", code)

    # Fix periods.year / periods.month references
    code = re.sub(r"definition_period\s*=\s*periods\.year", "definition_period = YEAR", code)
    code = re.sub(r"definition_period\s*=\s*periods\.month", "definition_period = MONTH", code)
    code = re.sub(r"definition_period\s*=\s*periods\.YEAR", "definition_period = YEAR", code)
    code = re.sub(r"definition_period\s*=\s*periods\.MONTH", "definition_period = MONTH", code)

    # Remove stray 'import numpy as np' (already available via model_api)
    code = re.sub(r"^import numpy.*$", "", code, flags=re.MULTILINE)

    # Remove trailing explanation text
    lines = code.strip().split("\n")
    clean_lines = []
    in_code = False
    for line in lines:
        if not line.strip():
            clean_lines.append(line)
            continue
        if line.startswith(("class ", "def ", "@")):
            in_code = True
        if in_code and line and not line[0].isspace() and not line.startswith(
            ("class ", "def ", "#", "@", "")
        ):
            stripped = line.strip()
            # Heuristic: if it starts with a letter and has spaces, it's prose
            if stripped[0].isalpha() and " " in stripped and "=" not in stripped:
                break
        clean_lines.append(line)

    code = "\n".join(clean_lines).strip()

    # Skip empty files
    if not code or "class " not in code:
        filepath.unlink()
        return "deleted"

    # Reassemble with standard imports
    final = header + STANDARD_HEADER + "\n" + code + "\n"

    # Check syntax
    try:
        compile(final, str(filepath), "exec")
    except SyntaxError:
        filepath.unlink()
        return "deleted"

    if final != original:
        filepath.write_text(final, encoding="utf-8")
        return "fixed"

    return "ok"


def main():
    stats = {"ok": 0, "fixed": 0, "deleted": 0}

    for root, _, files in os.walk(REPO / "ch"):
        for f in files:
            if not f.endswith(".py") or "executable" not in root or f == "__init__.py":
                continue
            fp = Path(root) / f
            status = repair_file(fp)
            stats[status] += 1

    print(f"Results: {stats['ok']} ok, {stats['fixed']} fixed, {stats['deleted']} deleted")
    print(f"Total remaining: {stats['ok'] + stats['fixed']}")


if __name__ == "__main__":
    main()
