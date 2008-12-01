"""Tests for the English translation layer."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from legalize_ch.translator import (
    Translator,
    parse_frontmatter,
    build_en_frontmatter,
)


# --- Unit tests for frontmatter parsing ---

def test_parse_frontmatter_valid():
    """Parse valid YAML frontmatter."""
    content = """---
sr_number: '101'
title: Bundesverfassung
language: de
version_date: '1999-04-18'
source: https://fedlex.data.admin.ch
---

# Bundesverfassung

Art. 1 Die Schweizerische Eidgenossenschaft
"""
    meta, body = parse_frontmatter(content)
    assert meta["sr_number"] == "101"
    assert meta["title"] == "Bundesverfassung"
    assert meta["language"] == "de"
    assert "# Bundesverfassung" in body
    assert "Art. 1" in body


def test_parse_frontmatter_no_frontmatter():
    """Return empty dict if no frontmatter."""
    content = "# Just a heading\n\nSome text."
    meta, body = parse_frontmatter(content)
    assert meta == {}
    assert body == content


def test_parse_frontmatter_empty():
    """Handle empty content."""
    meta, body = parse_frontmatter("")
    assert meta == {}
    assert body == ""


def test_parse_frontmatter_invalid_yaml():
    """Handle invalid YAML gracefully."""
    content = "---\n[invalid: yaml: content\n---\n\nBody text"
    meta, body = parse_frontmatter(content)
    assert meta == {}


def test_build_en_frontmatter():
    """Build English frontmatter from original metadata."""
    meta = {
        "sr_number": "220",
        "title": "Obligationenrecht",
        "title_en": "Code of Obligations",
        "language": "de",
        "version_date": "2020-01-01",
        "source": "https://fedlex.data.admin.ch",
    }
    fm = build_en_frontmatter(meta, "de")
    assert "language: en" in fm
    assert "sr_number: '220'" in fm
    assert "translated_from: de" in fm
    assert "title: Code of Obligations" in fm


def test_build_en_frontmatter_no_title_en():
    """Fall back to original title if no English title available."""
    meta = {
        "sr_number": "101",
        "title": "Bundesverfassung",
        "version_date": "1999-04-18",
    }
    fm = build_en_frontmatter(meta, "de")
    assert "title: Bundesverfassung" in fm


# --- Unit tests for Translator class ---

@pytest.fixture
def mock_anthropic():
    """Mock the anthropic client."""
    with patch("legalize_ch.translator.Translator.client", new_callable=lambda: property(lambda self: MagicMock())) as mock:
        yield mock


@pytest.fixture
def sample_law_file(tmp_path):
    """Create a sample German law file."""
    ch_dir = tmp_path / "ch" / "101" / "de"
    ch_dir.mkdir(parents=True)
    content = """---
sr_number: '101'
title: Bundesverfassung der Schweizerischen Eidgenossenschaft
language: de
version_date: '1999-04-18'
source: https://fedlex.data.admin.ch
---

# Bundesverfassung der Schweizerischen Eidgenossenschaft

## 1. Titel: Allgemeine Bestimmungen

**Art. 1** Die Schweizerische Eidgenossenschaft

Das Schweizervolk und die Kantone bilden die Schweizerische Eidgenossenschaft.
"""
    (ch_dir / "101.md").write_text(content, encoding="utf-8")
    return tmp_path


def test_translator_init_no_key():
    """Translator initializes without API key (reads from env)."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-123"}):
        t = Translator()
        assert t.api_key == "test-key-123"


def test_translator_init_with_key():
    """Translator uses provided API key."""
    t = Translator(api_key="my-key")
    assert t.api_key == "my-key"


def test_translate_file_missing_source(tmp_path):
    """Return False when source file doesn't exist."""
    t = Translator(api_key="test")
    result = t.translate_file(
        tmp_path / "nonexistent.md",
        tmp_path / "output.md",
        "de",
    )
    assert result is False


def test_translate_file_empty_body(tmp_path):
    """Skip files with empty body."""
    source = tmp_path / "empty.md"
    source.write_text("---\nsr_number: '101'\n---\n\n", encoding="utf-8")
    t = Translator(api_key="test")
    result = t.translate_file(source, tmp_path / "out.md", "de")
    assert result is False


def test_translate_file_stub(tmp_path):
    """Skip stub files with no content available marker."""
    source = tmp_path / "stub.md"
    source.write_text(
        "---\nsr_number: '999'\ntitle: Test\n---\n\n"
        "# Test\n\n*No text content available for this version.*\n",
        encoding="utf-8",
    )
    t = Translator(api_key="test")
    result = t.translate_file(source, tmp_path / "out.md", "de")
    assert result is False


def test_translate_file_success(sample_law_file):
    """Successful translation writes the English file."""
    t = Translator(api_key="test-key")

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Federal Constitution of the Swiss Confederation")]
    mock_client.messages.create.return_value = mock_response

    with patch.object(type(t), "client", new_callable=lambda: property(lambda self: mock_client)):
        source = sample_law_file / "ch" / "101" / "de" / "101.md"
        target = sample_law_file / "ch" / "101" / "en" / "101.md"

        result = t.translate_file(source, target, "de")

    assert result is True
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "language: en" in content
    assert "translated_from: de" in content


def test_translate_sr(sample_law_file):
    """Translate by SR number."""
    t = Translator(api_key="test-key")

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Translated law text")]
    mock_client.messages.create.return_value = mock_response

    with patch.object(type(t), "client", new_callable=lambda: property(lambda self: mock_client)):
        result = t.translate_sr(sample_law_file, "101", "de")

    assert result is True
    target = sample_law_file / "ch" / "101" / "en" / "101.md"
    assert target.exists()


def test_translate_directory(sample_law_file):
    """Batch translation of directory."""
    t = Translator(api_key="test-key")

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Translated content")]
    mock_client.messages.create.return_value = mock_response

    with patch.object(type(t), "client", new_callable=lambda: property(lambda self: mock_client)):
        count = t.translate_directory(sample_law_file, source_lang="de")

    assert count == 1
    target = sample_law_file / "ch" / "101" / "en" / "101.md"
    assert target.exists()


def test_translate_directory_with_limit(sample_law_file):
    """Batch translation respects limit parameter."""
    # Create additional files
    ch_dir = sample_law_file / "ch" / "220" / "de"
    ch_dir.mkdir(parents=True)
    (ch_dir / "220.md").write_text(
        "---\nsr_number: '220'\ntitle: OR\nlanguage: de\nversion_date: '2020-01-01'\n---\n\n"
        "# Obligationenrecht\n\nArt. 1 ...\n",
        encoding="utf-8",
    )

    t = Translator(api_key="test-key")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Translated")]
    mock_client.messages.create.return_value = mock_response

    with patch.object(type(t), "client", new_callable=lambda: property(lambda self: mock_client)):
        count = t.translate_directory(sample_law_file, source_lang="de", limit=1)

    assert count == 1


def test_translate_directory_skip_existing(sample_law_file):
    """Skip files that already have English translations."""
    # Create existing English file
    en_dir = sample_law_file / "ch" / "101" / "en"
    en_dir.mkdir(parents=True)
    (en_dir / "101.md").write_text("existing translation", encoding="utf-8")

    t = Translator(api_key="test-key")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text="new translation")]
    )

    with patch.object(type(t), "client", new_callable=lambda: property(lambda self: mock_client)):
        count = t.translate_directory(sample_law_file, source_lang="de")

    # Should skip since already translated
    assert count == 0


def test_translate_directory_sr_filter(sample_law_file):
    """SR filter limits which files are translated."""
    t = Translator(api_key="test-key")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Translated")]
    mock_client.messages.create.return_value = mock_response

    with patch.object(type(t), "client", new_callable=lambda: property(lambda self: mock_client)):
        # Filter for SR 220 - should not match our SR 101 file
        count = t.translate_directory(sample_law_file, sr_filter="220", source_lang="de")

    assert count == 0


def test_translate_chunked_long_text(tmp_path):
    """Long texts are split into chunks for translation."""
    # Create a file with a very long body
    source = tmp_path / "long.md"
    body = "# Title\n\n" + ("## Section\n\nLorem ipsum dolor sit amet. " * 5000)
    source.write_text(
        f"---\nsr_number: '999'\ntitle: Long Law\nlanguage: de\nversion_date: '2020-01-01'\n---\n\n{body}",
        encoding="utf-8",
    )

    t = Translator(api_key="test-key")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Translated chunk")]
    mock_client.messages.create.return_value = mock_response

    target = tmp_path / "out.md"
    with patch.object(type(t), "client", new_callable=lambda: property(lambda self: mock_client)):
        result = t.translate_file(source, target, "de")

    assert result is True
    # Should have called the API multiple times for chunks
    assert mock_client.messages.create.call_count > 1
