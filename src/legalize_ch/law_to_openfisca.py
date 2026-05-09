"""Convert Swiss law markdown texts into executable OpenFisca code.

Reads law files from ch/{number}/{lang}/*.md, transforms each article
into OpenFisca Variable classes using Claude CLI, and writes output to
ch/{number}/executable/*.py + parameters/*.yaml.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CLAUDE_CLI = Path.home() / ".local" / "bin" / "claude"

ARTICLE_RE = re.compile(
    r"^\*?\*?(?:Art\.?\s*\d+[a-z]?(?:bis|ter|quater|quinquies)?)"
    r".*?\*?\*?\s*$",
    re.MULTILINE,
)

SYSTEM_PROMPT = """\
You are a legal-to-code translator. Convert the given Swiss law article into \
an OpenFisca Variable class in Python. Output ONLY Python code, no explanations.

Rules:
- Start with imports: from openfisca_core.model_api import *
- from openfisca_core.periods import MONTH, YEAR
- from openfisca_core.entities import build_entity
- Person = build_entity(key='person', plural='persons', label='An individual', is_person=True)
- Each article becomes one or more Variable subclasses
- value_type = bool, float, int, or Enum
- definition_period = MONTH or YEAR (objects, not strings)
- entity = Person (object, not string)
- snake_case variable names
- Include label and reference
- Implement formula method capturing the legal logic
- If purely procedural, output a bool Variable for applicability
- Output ONLY valid Python code\
"""


@dataclass
class ArticleChunk:
    reference: str
    text: str
    sr_number: str
    article_num: str


def extract_articles(md_content: str, sr_number: str) -> list[ArticleChunk]:
    """Split a law markdown file into individual article chunks."""
    lines = md_content.split("\n")

    body_start = 0
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                body_start = i + 1
                break

    body = "\n".join(lines[body_start:])
    if not body.strip() or "No text content available" in body:
        return []

    matches = list(ARTICLE_RE.finditer(body))
    if not matches:
        return []

    articles = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[start:end].strip()
        if len(text) < 20:
            continue

        num_match = re.search(
            r"Art\.?\s*(\d+[a-z]?(?:bis|ter|quater|quinquies)?)", match.group()
        )
        art_num = num_match.group(1) if num_match else str(i + 1)

        articles.append(
            ArticleChunk(
                reference=f"SR {sr_number} Art. {art_num}",
                text=text,
                sr_number=sr_number,
                article_num=art_num,
            )
        )

    return articles


def sanitize_variable_name(sr_number: str, art_num: str) -> str:
    sr_clean = sr_number.replace(".", "_")
    art_clean = art_num.replace(".", "_")
    return f"sr_{sr_clean}_art_{art_clean}"


def _call_claude(article: ArticleChunk) -> dict | None:
    """Call Claude CLI to convert a law article to OpenFisca code."""
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Convert this Swiss law article to OpenFisca code.\n\n"
        f"Reference: {article.reference}\n\n"
        f"Article text:\n{article.text}"
    )

    try:
        result = subprocess.run(
            [str(CLAUDE_CLI), "-p", prompt, "--model", "claude-sonnet-4-6"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            logger.error("Claude CLI failed: %s", result.stderr[:200])
            return None

        code = result.stdout.strip()
        if not code:
            return None

        return {"code": code, "parameters": None}

    except subprocess.TimeoutExpired:
        logger.error("Claude CLI timed out for %s", article.reference)
        return None
    except Exception as e:
        logger.error("Claude CLI error for %s: %s", article.reference, e)
        return None


def _postprocess_code(code: str) -> str:
    """Fix common issues in generated OpenFisca code."""
    # Strip markdown code fences
    code = re.sub(r"^```\w*\n?|```$", "", code, flags=re.MULTILINE).strip()
    # Fix entity as string
    code = re.sub(r'entity\s*=\s*["\']Person["\']', "entity = Person", code)
    code = re.sub(r'entity\s*=\s*["\']Household["\']', "entity = Household", code)
    # Fix definition_period as string
    for period in ("YEAR", "MONTH"):
        code = re.sub(
            rf'definition_period\s*=\s*["\'](?i:{period})["\']',
            f"definition_period = {period}",
            code,
        )
    return code


def transform_law_group(
    repo_path: Path,
    sr_base: str,
    lang: str = "de",
    dry_run: bool = False,
) -> int:
    """Transform all articles in a law group to OpenFisca code.

    Reads from ch/{sr_base}/{lang}/*.md
    Writes to ch/{sr_base}/executable/*.py
    """
    lang_dir = repo_path / "ch" / sr_base / lang
    exec_dir = repo_path / "ch" / sr_base / "executable"

    if not lang_dir.exists():
        return 0

    md_files = sorted(lang_dir.glob("*.md"))
    if not md_files:
        return 0

    total_generated = 0
    all_variables = []

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        sr_number = md_file.stem

        articles = extract_articles(content, sr_number)
        if not articles:
            continue

        logger.info("SR %s: %d articles", sr_number, len(articles))

        for article in articles:
            var_name = sanitize_variable_name(sr_number, article.article_num)
            py_file = exec_dir / f"{var_name}.py"

            # Skip if already generated
            if py_file.exists():
                all_variables.append(var_name)
                total_generated += 1
                continue

            if dry_run:
                logger.info("Would transform: %s", article.reference)
                continue

            result = _call_claude(article)
            if not result or not result.get("code"):
                continue

            code = _postprocess_code(result["code"])
            if len(code) < 30:
                logger.warning("Too-short code for %s", article.reference)
                continue

            # Ensure imports
            if "from openfisca_core" not in code:
                code = (
                    "from openfisca_core.model_api import *\n"
                    "from openfisca_core.periods import MONTH, YEAR\n"
                    "from openfisca_core.entities import build_entity\n\n"
                    "Person = build_entity(key='person', plural='persons', "
                    "label='An individual', is_person=True)\n\n"
                    + code
                )

            header = (
                f'"""{article.reference}\n\n'
                f"Generated from: ch/{sr_base}/{lang}/{md_file.name}\n"
                f'"""\n\n'
            )
            code = header + code + "\n"

            exec_dir.mkdir(parents=True, exist_ok=True)
            py_file.write_text(code, encoding="utf-8")
            all_variables.append(var_name)
            total_generated += 1

            # Write parameter YAML if present
            param_yaml = result.get("parameters")
            if param_yaml and param_yaml not in ("None", "null", "N/A", "n/a"):
                params_dir = exec_dir / "parameters"
                params_dir.mkdir(parents=True, exist_ok=True)
                (params_dir / f"{var_name}.yaml").write_text(
                    str(param_yaml) + "\n", encoding="utf-8"
                )

            logger.info("Generated %s (%d chars)", py_file.name, len(code))

    # Write __init__.py
    if all_variables and not dry_run:
        init_content = f'"""OpenFisca variables for SR {sr_base}."""\n\n'
        for var in sorted(set(all_variables)):
            init_content += f"from .{var} import *  # noqa: F401,F403\n"
        (exec_dir / "__init__.py").write_text(init_content, encoding="utf-8")

    return total_generated


def run_pipeline(
    repo_path: str | Path = "/home/ubuntu/swiss-law",
    lang: str = "de",
    sr_filter: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> int:
    """Run the law-to-OpenFisca pipeline on all law groups."""
    repo_path = Path(repo_path)
    ch_dir = repo_path / "ch"

    if not ch_dir.exists():
        logger.error("ch/ directory not found in %s", repo_path)
        return 0

    sr_bases = sorted(
        d.name
        for d in ch_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

    if sr_filter:
        sr_bases = [b for b in sr_bases if b.startswith(sr_filter)]

    if limit:
        sr_bases = sr_bases[:limit]

    logger.info(
        "Processing %d law groups (lang=%s, dry_run=%s)",
        len(sr_bases), lang, dry_run,
    )

    total = 0
    errors = 0
    for i, sr_base in enumerate(sr_bases):
        logger.info("[%d/%d] SR base %s", i + 1, len(sr_bases), sr_base)
        try:
            count = transform_law_group(repo_path, sr_base, lang=lang, dry_run=dry_run)
            total += count
            if count:
                logger.info("  -> %d variables", count)
        except Exception as e:
            logger.error("  -> Error SR %s: %s", sr_base, e)
            errors += 1

    logger.info("Done: %d variables, %d errors", total, errors)
    return total


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    import argparse

    parser = argparse.ArgumentParser(description="Convert Swiss law to OpenFisca code")
    parser.add_argument("--lang", default="de", help="Source language (default: de)")
    parser.add_argument("--sr-filter", help="Only process SR bases starting with this")
    parser.add_argument("--limit", type=int, help="Max law groups to process")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    count = run_pipeline(
        lang=args.lang,
        sr_filter=args.sr_filter,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(f"\nTotal: {count} OpenFisca variables generated")
