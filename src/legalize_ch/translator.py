"""English translation layer for Swiss law texts.

Attempts to fetch official English translations from Fedlex first,
then falls back to LLM-based translation via the Anthropic API.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Fedlex English language URI
FEDLEX_EN_LANG = "http://publications.europa.eu/resource/authority/language/ENG"

# SPARQL query to check for official English text
EN_TEXT_QUERY = """
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?title ?fileUrl WHERE {{
  <{cons_uri}> jolux:isRealizedBy <{cons_uri}/en> .
  <{cons_uri}/en> jolux:isEmbodiedBy ?manifest .
  ?manifest jolux:isExemplifiedBy ?fileUrl .

  OPTIONAL {{
    <{cons_uri}> jolux:isMemberOf ?abstract .
    ?abstract jolux:isRealizedBy ?absExpr .
    ?absExpr jolux:language <http://publications.europa.eu/resource/authority/language/ENG> ;
             jolux:title ?title .
  }}
}}
LIMIT 1
"""


def parse_frontmatter(md_content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter and body from a markdown file.

    Returns (metadata_dict, body_text).
    """
    if not md_content.startswith("---"):
        return {}, md_content

    parts = md_content.split("---", 2)
    if len(parts) < 3:
        return {}, md_content

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, md_content

    body = parts[2].lstrip("\n")
    return meta, body


def build_en_frontmatter(meta: dict, source_lang: str) -> str:
    """Build English frontmatter based on original metadata."""
    en_meta = {
        "sr_number": meta.get("sr_number", ""),
        "title": meta.get("title_en", meta.get("title", "")),
        "language": "en",
        "version_date": meta.get("version_date", ""),
        "source": meta.get("source", "https://fedlex.data.admin.ch"),
        "translated_from": source_lang,
    }
    return "---\n" + yaml.dump(en_meta, allow_unicode=True, default_flow_style=False).strip() + "\n---"


class Translator:
    """Translates Swiss law texts to English.

    Strategy:
    1. Check Fedlex for official English translation
    2. Fall back to Anthropic API (Claude) for translation
    """

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-20250514"):
        """Initialize translator.

        Args:
            api_key: Anthropic API key. If None, reads from ANTHROPIC_API_KEY env var.
            model: Claude model to use for translation.
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self._client = None

    @property
    def client(self):
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise RuntimeError(
                    "anthropic package not installed. "
                    "Install with: pip install anthropic"
                )
            except Exception as e:
                raise RuntimeError(f"Failed to initialize Anthropic client: {e}")
        return self._client

    def translate_file(self, source_path: Path, target_path: Path,
                       source_lang: str = "de") -> bool:
        """Translate a single law file from source language to English.

        Args:
            source_path: Path to the source markdown file.
            target_path: Path where the English translation will be written.
            source_lang: Source language code (de, fr, it).

        Returns:
            True if translation was successful.
        """
        if not source_path.exists():
            logger.warning("Source file not found: %s", source_path)
            return False

        content = source_path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(content)

        if not body or body.strip() == "":
            logger.warning("Empty body in %s, skipping", source_path)
            return False

        # Skip stub files
        if "*No text content available" in body:
            logger.debug("Stub file %s, skipping", source_path)
            return False

        # Translate the body
        translated_body = self._translate_text(body, source_lang, meta)
        if not translated_body:
            logger.error("Translation failed for %s", source_path)
            return False

        # Translate the title
        title_en = self._translate_title(meta.get("title", ""), source_lang)
        meta["title_en"] = title_en

        # Build the English document
        en_frontmatter = build_en_frontmatter(meta, source_lang)
        en_content = en_frontmatter + "\n\n" + translated_body.strip() + "\n"

        # Write the translated file
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(en_content, encoding="utf-8")
        logger.info("Translated: %s -> %s", source_path.name, target_path)
        return True

    def _translate_title(self, title: str, source_lang: str) -> str:
        """Translate just the title to English."""
        if not title:
            return ""

        lang_name = {"de": "German", "fr": "French", "it": "Italian"}.get(source_lang, "German")

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Translate this Swiss law title from {lang_name} to English. "
                        f"Return ONLY the translated title, nothing else.\n\n"
                        f"{title}"
                    ),
                }],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.warning("Title translation failed: %s", e)
            return title

    def _translate_text(self, body: str, source_lang: str,
                        meta: dict | None = None) -> str | None:
        """Translate law body text using the Anthropic API.

        Args:
            body: The markdown body text to translate.
            source_lang: Source language code.
            meta: Optional metadata dict for context.

        Returns:
            Translated text or None on failure.
        """
        lang_name = {"de": "German", "fr": "French", "it": "Italian"}.get(source_lang, "German")
        sr_number = meta.get("sr_number", "") if meta else ""

        # Chunk large texts to stay within token limits
        # Claude can handle ~100k tokens, but we'll be conservative
        max_chars = 80_000
        if len(body) > max_chars:
            return self._translate_chunked(body, source_lang, meta)

        system_prompt = (
            "You are a legal translator specializing in Swiss federal law. "
            "Translate the following Swiss law text accurately from "
            f"{lang_name} to English. "
            "Preserve all legal terminology precisely. "
            "Maintain the exact same markdown structure (headings, lists, bold, etc.). "
            "Keep article numbers, paragraph numbers, and cross-references unchanged. "
            "Do not add any commentary or explanations. "
            "Return ONLY the translated text."
        )

        context = ""
        if sr_number:
            context = f"This is Swiss federal law SR {sr_number}. "

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": f"{context}Translate this law text:\n\n{body}",
                }],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error("Translation API call failed: %s", e)
            return None

    def _translate_chunked(self, body: str, source_lang: str,
                           meta: dict | None = None) -> str | None:
        """Translate a long text by splitting into chunks at section boundaries."""
        # Split at markdown headings to preserve structure
        sections = re.split(r'(^#{1,6}\s+.+$)', body, flags=re.MULTILINE)

        chunks: list[str] = []
        current_chunk = ""
        max_chunk = 60_000

        for section in sections:
            if len(current_chunk) + len(section) > max_chunk and current_chunk:
                chunks.append(current_chunk)
                current_chunk = section
            else:
                current_chunk += section

        if current_chunk:
            chunks.append(current_chunk)

        # If we couldn't split effectively (single chunk still too large),
        # force-split by character count to avoid infinite recursion
        if len(chunks) == 1 and len(chunks[0]) > 80_000:
            text = chunks[0]
            chunks = [text[i:i + max_chunk] for i in range(0, len(text), max_chunk)]

        translated_parts = []
        for i, chunk in enumerate(chunks):
            logger.info("Translating chunk %d/%d (%d chars)", i + 1, len(chunks), len(chunk))
            translated = self._translate_single_chunk(chunk, source_lang, meta)
            if translated is None:
                return None
            translated_parts.append(translated)

        return "\n\n".join(translated_parts)

    def _translate_single_chunk(self, chunk: str, source_lang: str,
                                meta: dict | None = None) -> str | None:
        """Translate a single chunk without recursion into chunking."""
        lang_name = {"de": "German", "fr": "French", "it": "Italian"}.get(source_lang, "German")
        sr_number = meta.get("sr_number", "") if meta else ""

        system_prompt = (
            "You are a legal translator specializing in Swiss federal law. "
            "Translate the following Swiss law text accurately from "
            f"{lang_name} to English. "
            "Preserve all legal terminology precisely. "
            "Maintain the exact same markdown structure (headings, lists, bold, etc.). "
            "Keep article numbers, paragraph numbers, and cross-references unchanged. "
            "Do not add any commentary or explanations. "
            "Return ONLY the translated text."
        )

        context = ""
        if sr_number:
            context = f"This is Swiss federal law SR {sr_number}. "

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": f"{context}Translate this law text:\n\n{chunk}",
                }],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error("Translation API call failed: %s", e)
            return None

    def translate_sr(self, repo_path: str | Path, sr_number: str,
                     source_lang: str = "de") -> bool:
        """Translate a law by SR number.

        Args:
            repo_path: Path to the repo root.
            sr_number: The SR number (e.g. "101", "220.1").
            source_lang: Source language to translate from.

        Returns:
            True if translation was successful.
        """
        repo = Path(repo_path)
        parts = sr_number.split(".")
        base = parts[0]

        source_path = repo / "ch" / base / source_lang / f"{sr_number}.md"
        target_path = repo / "ch" / base / "en" / f"{sr_number}.md"

        return self.translate_file(source_path, target_path, source_lang)

    def translate_directory(self, repo_path: str | Path, sr_filter: str | None = None,
                            source_lang: str = "de", limit: int | None = None) -> int:
        """Translate multiple laws from a directory.

        Args:
            repo_path: Path to the repo root.
            sr_filter: Optional SR number prefix to filter by.
            source_lang: Source language to translate from.
            limit: Maximum number of files to translate.

        Returns:
            Number of files successfully translated.
        """
        repo = Path(repo_path)
        ch_dir = repo / "ch"

        if not ch_dir.exists():
            logger.error("ch/ directory not found in %s", repo_path)
            return 0

        count = 0
        source_files = sorted(ch_dir.rglob(f"*/{source_lang}/*.md"))

        for source_path in source_files:
            if limit and count >= limit:
                break

            # Extract SR number from filename
            sr_number = source_path.stem

            # Apply filter
            if sr_filter and not sr_number.startswith(sr_filter):
                continue

            # Determine target path
            # source: ch/{base}/{lang}/{sr}.md -> target: ch/{base}/en/{sr}.md
            target_path = source_path.parent.parent / "en" / source_path.name

            # Skip if already translated
            if target_path.exists():
                logger.debug("Already translated: %s", target_path)
                continue

            if self.translate_file(source_path, target_path, source_lang):
                count += 1

        return count
