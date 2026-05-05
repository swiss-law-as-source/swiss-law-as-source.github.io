"""Tests for markdown validation — detect empty bodies, broken frontmatter (roadmap 3.4)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from legalize_ch.validator import (
    REQUIRED_FRONTMATTER_KEYS,
    ValidationResult,
    validate_directory,
    validate_file,
    validate_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GOOD_DOC = textwrap.dedent("""\
    ---
    sr_number: '101'
    title: Federal Constitution
    language: de
    version_date: '2024-01-01'
    source: https://fedlex.data.admin.ch
    ---

    # Federal Constitution

    **Art. 1** The Swiss Confederation

    The Swiss Confederation protects the liberty and rights of the people.
""")

STUB_DOC = textwrap.dedent("""\
    ---
    sr_number: '999.1'
    title: Placeholder Law
    language: fr
    version_date: '2020-06-15'
    source: https://fedlex.data.admin.ch
    ---

    # Placeholder Law

    *No text content available for this version.*
""")


# ---------------------------------------------------------------------------
# Valid documents
# ---------------------------------------------------------------------------

class TestValidDocuments:
    def test_good_document_passes(self):
        res = validate_markdown(GOOD_DOC)
        assert res.ok
        assert res.errors == []
        assert not res.is_stub

    def test_stub_document_passes_with_warning(self):
        res = validate_markdown(STUB_DOC)
        assert res.ok, f"stub should not produce errors: {res.errors}"
        assert res.is_stub
        assert any("stub" in w for w in res.warnings)

    def test_document_with_abbreviation(self):
        doc = textwrap.dedent("""\
            ---
            sr_number: '101'
            title: Federal Constitution
            language: de
            version_date: '2024-01-01'
            source: https://fedlex.data.admin.ch
            abbreviation: BV
            ---

            # Federal Constitution

            Some real content here.
        """)
        res = validate_markdown(doc)
        assert res.ok


# ---------------------------------------------------------------------------
# Empty / missing content
# ---------------------------------------------------------------------------

class TestEmptyContent:
    def test_completely_empty_file(self):
        res = validate_markdown("")
        assert not res.ok
        assert any("empty" in e for e in res.errors)

    def test_whitespace_only_file(self):
        res = validate_markdown("   \n  \n  ")
        assert not res.ok
        assert any("empty" in e for e in res.errors)

    def test_frontmatter_with_empty_body(self):
        doc = textwrap.dedent("""\
            ---
            sr_number: '101'
            title: Federal Constitution
            language: de
            version_date: '2024-01-01'
            source: https://fedlex.data.admin.ch
            ---
        """)
        res = validate_markdown(doc)
        assert not res.ok
        assert any("empty body" in e for e in res.errors)

    def test_frontmatter_with_only_whitespace_body(self):
        doc = "---\nsr_number: '101'\ntitle: Test\nlanguage: de\nversion_date: '2024-01-01'\nsource: https://fedlex.data.admin.ch\n---\n\n   \n\n"
        res = validate_markdown(doc)
        assert not res.ok
        assert any("empty body" in e for e in res.errors)

    def test_body_with_only_headings_warns(self):
        doc = textwrap.dedent("""\
            ---
            sr_number: '101'
            title: Test
            language: de
            version_date: '2024-01-01'
            source: https://fedlex.data.admin.ch
            ---

            # Just a heading
        """)
        res = validate_markdown(doc)
        assert res.ok  # not an error, just a warning
        assert any("only headings" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# Broken frontmatter
# ---------------------------------------------------------------------------

class TestBrokenFrontmatter:
    def test_missing_opening_delimiter(self):
        doc = "sr_number: '101'\ntitle: Test\n---\n\nBody text.\n"
        res = validate_markdown(doc)
        assert not res.ok
        assert any("opening frontmatter" in e for e in res.errors)

    def test_missing_closing_delimiter(self):
        doc = "---\nsr_number: '101'\ntitle: Test\n\nBody text without closing.\n"
        res = validate_markdown(doc)
        assert not res.ok
        assert any("closing frontmatter" in e for e in res.errors)

    def test_invalid_yaml(self):
        doc = "---\n: invalid: yaml: [unbalanced\n---\n\nBody.\n"
        res = validate_markdown(doc)
        assert not res.ok
        assert any("invalid YAML" in e or "did not parse" in e for e in res.errors)

    def test_frontmatter_not_a_mapping(self):
        doc = "---\n- just\n- a\n- list\n---\n\nBody.\n"
        res = validate_markdown(doc)
        assert not res.ok
        assert any("mapping" in e for e in res.errors)


# ---------------------------------------------------------------------------
# Missing / empty frontmatter keys
# ---------------------------------------------------------------------------

class TestMissingKeys:
    @pytest.mark.parametrize("missing_key", sorted(REQUIRED_FRONTMATTER_KEYS))
    def test_missing_required_key(self, missing_key):
        meta = {
            "sr_number": "101",
            "title": "Test Law",
            "language": "de",
            "version_date": "2024-01-01",
            "source": "https://fedlex.data.admin.ch",
        }
        del meta[missing_key]
        import yaml as _yaml
        fm = _yaml.dump(meta, default_flow_style=False).strip()
        doc = f"---\n{fm}\n---\n\n# Body\n\nSome text.\n"
        res = validate_markdown(doc)
        assert not res.ok
        assert any(missing_key in e for e in res.errors)

    def test_empty_title(self):
        doc = textwrap.dedent("""\
            ---
            sr_number: '101'
            title: ''
            language: de
            version_date: '2024-01-01'
            source: https://fedlex.data.admin.ch
            ---

            # Body

            Text.
        """)
        res = validate_markdown(doc)
        assert not res.ok
        assert any("title" in e for e in res.errors)

    def test_empty_sr_number(self):
        doc = textwrap.dedent("""\
            ---
            sr_number: ''
            title: Test
            language: de
            version_date: '2024-01-01'
            source: https://fedlex.data.admin.ch
            ---

            # Body

            Text.
        """)
        res = validate_markdown(doc)
        assert not res.ok
        assert any("sr_number" in e for e in res.errors)


# ---------------------------------------------------------------------------
# Language validation
# ---------------------------------------------------------------------------

class TestLanguageValidation:
    @pytest.mark.parametrize("lang", ["de", "fr", "it", "rm", "en"])
    def test_valid_languages(self, lang):
        doc = f"---\nsr_number: '101'\ntitle: Test\nlanguage: {lang}\nversion_date: '2024-01-01'\nsource: https://fedlex.data.admin.ch\n---\n\nBody text.\n"
        res = validate_markdown(doc)
        assert res.ok

    def test_invalid_language(self):
        doc = "---\nsr_number: '101'\ntitle: Test\nlanguage: xx\nversion_date: '2024-01-01'\nsource: https://fedlex.data.admin.ch\n---\n\nBody text.\n"
        res = validate_markdown(doc)
        assert not res.ok
        assert any("unknown language" in e for e in res.errors)


# ---------------------------------------------------------------------------
# Date validation
# ---------------------------------------------------------------------------

class TestDateValidation:
    def test_valid_date(self):
        doc = "---\nsr_number: '101'\ntitle: Test\nlanguage: de\nversion_date: '2024-01-01'\nsource: https://fedlex.data.admin.ch\n---\n\nBody.\n"
        res = validate_markdown(doc)
        assert res.ok

    def test_non_iso_date(self):
        doc = "---\nsr_number: '101'\ntitle: Test\nlanguage: de\nversion_date: '01/01/2024'\nsource: https://fedlex.data.admin.ch\n---\n\nBody.\n"
        res = validate_markdown(doc)
        assert not res.ok
        assert any("ISO" in e or "version_date" in e for e in res.errors)

    def test_date_parsed_as_date_object_still_works(self):
        """YAML may parse unquoted dates as date objects; validator should handle this."""
        doc = "---\nsr_number: '101'\ntitle: Test\nlanguage: de\nversion_date: 2024-01-01\nsource: https://fedlex.data.admin.ch\n---\n\nBody.\n"
        res = validate_markdown(doc)
        assert res.ok


# ---------------------------------------------------------------------------
# File-based validation
# ---------------------------------------------------------------------------

class TestFileValidation:
    def test_validate_nonexistent_file(self):
        res = validate_file("/tmp/does_not_exist_12345.md")
        assert not res.ok
        assert any("does not exist" in e for e in res.errors)

    def test_validate_real_file(self, tmp_path):
        p = tmp_path / "test.md"
        p.write_text(GOOD_DOC, encoding="utf-8")
        res = validate_file(p)
        assert res.ok

    def test_validate_directory(self, tmp_path):
        (tmp_path / "good.md").write_text(GOOD_DOC, encoding="utf-8")
        (tmp_path / "stub.md").write_text(STUB_DOC, encoding="utf-8")
        (tmp_path / "bad.md").write_text("not a law file", encoding="utf-8")

        results = validate_directory(tmp_path)
        assert len(results) == 3
        ok_count = sum(1 for r in results if r.ok)
        assert ok_count == 2  # good + stub pass; bad fails


# ---------------------------------------------------------------------------
# Spot-check real repo files (smoke test)
# ---------------------------------------------------------------------------

REPO_CH = Path("/home/ubuntu/swiss-law/ch")


@pytest.mark.skipif(not REPO_CH.exists(), reason="swiss-law repo not present")
class TestRepoSpotCheck:
    """Validate a sample of real files from the repository."""

    def _sample_files(self, n: int = 50) -> list[Path]:
        """Return up to *n* .md files spread across the repo."""
        import random
        all_files = list(REPO_CH.rglob("*.md"))
        random.seed(42)  # deterministic sample
        return random.sample(all_files, min(n, len(all_files)))

    def test_sample_files_have_valid_frontmatter(self):
        files = self._sample_files(50)
        failures = []
        for f in files:
            res = validate_file(f)
            if not res.ok:
                failures.append((str(f), res.errors))
        assert not failures, f"Validation failures:\n" + "\n".join(
            f"  {p}: {errs}" for p, errs in failures
        )

    def test_sample_files_have_nonempty_body(self):
        """All files must have *some* body — even stubs have placeholder text."""
        files = self._sample_files(50)
        empty = []
        for f in files:
            res = validate_file(f)
            if any("empty body" in e for e in res.errors):
                empty.append(str(f))
        assert not empty, f"Files with empty body:\n" + "\n".join(f"  {p}" for p in empty)

    def test_no_unknown_languages(self):
        files = self._sample_files(100)
        bad = []
        for f in files:
            res = validate_file(f)
            if any("unknown language" in e for e in res.errors):
                bad.append(str(f))
        assert not bad, f"Files with unknown language:\n" + "\n".join(f"  {p}" for p in bad)
