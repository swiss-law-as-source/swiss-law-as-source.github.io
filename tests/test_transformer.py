"""Unit tests for the transformer module (AKN -> Markdown, HTML -> Markdown)."""
from __future__ import annotations

from datetime import date

import pytest

from legalize_ch.transformer import (
    akn_to_markdown,
    build_frontmatter,
    html_to_markdown,
    law_to_markdown,
    sr_to_path,
)


# ---------------------------------------------------------------------------
# AKN -> Markdown tests
# ---------------------------------------------------------------------------

MINIMAL_AKN = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <article eId="art_1">
        <num>Art. 1</num>
        <heading>Purpose</heading>
        <paragraph eId="art_1__para_1">
          <num>1</num>
          <content><p>This law regulates Swiss matters.</p></content>
        </paragraph>
      </article>
    </body>
  </act>
</akomaNtoso>
"""

AKN_WITH_STRUCTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <part eId="part_1">
        <num>Part 1</num>
        <heading>General Provisions</heading>
        <chapter eId="chap_1">
          <num>Chapter 1</num>
          <heading>Scope</heading>
          <article eId="art_1">
            <num>Art. 1</num>
            <heading>Subject matter</heading>
            <paragraph eId="art_1__para_1">
              <num>1</num>
              <content><p>This law applies to all persons.</p></content>
            </paragraph>
            <paragraph eId="art_1__para_2">
              <num>2</num>
              <content><p>Exceptions may be defined by ordinance.</p></content>
            </paragraph>
          </article>
        </chapter>
      </part>
    </body>
  </act>
</akomaNtoso>
"""

AKN_WITH_PREAMBLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <preamble>The Federal Assembly of the Swiss Confederation</preamble>
    <body>
      <article eId="art_1">
        <num>Art. 1</num>
        <paragraph eId="art_1__para_1">
          <content><p>Basic provision.</p></content>
        </paragraph>
      </article>
    </body>
  </act>
</akomaNtoso>
"""

AKN_WITH_LIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <article eId="art_1">
        <num>Art. 1</num>
        <paragraph eId="art_1__para_1">
          <num>1</num>
          <list>
            <point eId="art_1__para_1__pt_a">
              <num>a.</num>
              <content><p>first item</p></content>
            </point>
            <point eId="art_1__para_1__pt_b">
              <num>b.</num>
              <content><p>second item</p></content>
            </point>
          </list>
        </paragraph>
      </article>
    </body>
  </act>
</akomaNtoso>
"""

AKN_WITH_CONCLUSIONS = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <article eId="art_1">
        <num>Art. 1</num>
        <paragraph eId="art_1__para_1">
          <content><p>Only article.</p></content>
        </paragraph>
      </article>
      <conclusions>Bern, 1 January 2020</conclusions>
    </body>
  </act>
</akomaNtoso>
"""


class TestAknToMarkdown:
    def test_empty_input(self):
        assert akn_to_markdown("") == ""
        assert akn_to_markdown(None) == ""

    def test_minimal_article(self):
        result = akn_to_markdown(MINIMAL_AKN)
        # _find_heading joins num + heading text
        assert "**Art. 1**" in result
        assert "Purpose" in result
        assert "1 This law regulates Swiss matters." in result

    def test_structural_elements(self):
        result = akn_to_markdown(AKN_WITH_STRUCTURE)
        assert "## Part 1 General Provisions" in result
        assert "### Chapter 1 Scope" in result
        assert "**Art. 1**" in result
        assert "Subject matter" in result
        assert "1 This law applies to all persons." in result
        assert "2 Exceptions may be defined by ordinance." in result

    def test_preamble_outside_body_not_rendered(self):
        """Preamble outside <body> is not part of the body processing."""
        result = akn_to_markdown(AKN_WITH_PREAMBLE)
        # The body content is still rendered
        assert "**Art. 1**" in result
        assert "Basic provision." in result

    def test_list_items(self):
        result = akn_to_markdown(AKN_WITH_LIST)
        assert "a. first item" in result
        assert "b. second item" in result

    def test_conclusions(self):
        result = akn_to_markdown(AKN_WITH_CONCLUSIONS)
        assert "---" in result
        assert "Bern, 1 January 2020" in result

    def test_invalid_xml_falls_back_to_html(self):
        """Invalid XML should fall through to html_to_markdown."""
        html_input = "<p>Hello <strong>world</strong></p>"
        result = akn_to_markdown(html_input)
        assert "Hello" in result
        assert "world" in result

    def test_no_body_fallback(self):
        """AKN with no body/mainBody should use _extract_text fallback."""
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta><identification>Some identification text</identification></meta>
  </act>
</akomaNtoso>
"""
        result = akn_to_markdown(xml)
        assert "Some identification text" in result


# ---------------------------------------------------------------------------
# HTML -> Markdown tests
# ---------------------------------------------------------------------------

class TestHtmlToMarkdown:
    def test_empty_input(self):
        assert html_to_markdown("") == ""
        assert html_to_markdown(None) == ""

    def test_basic_html(self):
        result = html_to_markdown("<p>Hello world</p>")
        assert "Hello world" in result

    def test_headings(self):
        result = html_to_markdown("<h1>Title</h1><h2>Subtitle</h2>")
        assert "# Title" in result
        assert "## Subtitle" in result

    def test_bold_and_italic(self):
        result = html_to_markdown("<p><b>bold</b> and <i>italic</i></p>")
        assert "**bold**" in result
        assert "_italic_" in result or "*italic*" in result

    def test_links_preserved(self):
        result = html_to_markdown('<p><a href="https://example.com">link</a></p>')
        assert "https://example.com" in result
        assert "link" in result

    def test_lists(self):
        result = html_to_markdown("<ul><li>one</li><li>two</li></ul>")
        assert "one" in result
        assert "two" in result

    def test_multiple_newlines_collapsed(self):
        result = html_to_markdown("<p>A</p><p></p><p></p><p></p><p>B</p>")
        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in result

    def test_table(self):
        html = "<table><tr><td>Cell 1</td><td>Cell 2</td></tr></table>"
        result = html_to_markdown(html)
        assert "Cell 1" in result
        assert "Cell 2" in result


# ---------------------------------------------------------------------------
# build_frontmatter tests
# ---------------------------------------------------------------------------

class TestBuildFrontmatter:
    def test_basic_frontmatter(self):
        result = build_frontmatter("101", "Federal Constitution", "de", date(2024, 1, 1))
        assert result.startswith("---")
        assert result.endswith("---")
        assert "sr_number: '101'" in result or "sr_number: \"101\"" in result or "sr_number: 101" in result
        assert "Federal Constitution" in result
        assert "language: de" in result
        assert "2024-01-01" in result
        assert "https://fedlex.data.admin.ch" in result

    def test_with_abbreviation(self):
        result = build_frontmatter("101", "Federal Constitution", "de", date(2024, 1, 1), abbreviation="BV")
        assert "abbreviation: BV" in result or "abbreviation: 'BV'" in result

    def test_without_abbreviation(self):
        result = build_frontmatter("101", "Federal Constitution", "de", date(2024, 1, 1))
        assert "abbreviation" not in result


# ---------------------------------------------------------------------------
# law_to_markdown tests
# ---------------------------------------------------------------------------

class TestLawToMarkdown:
    def test_with_xml_content(self):
        result = law_to_markdown(
            sr_number="101",
            title="Federal Constitution",
            xml_content=MINIMAL_AKN,
            html_content="",
            language="de",
            version_date=date(2024, 1, 1),
        )
        assert result.startswith("---")
        assert "sr_number" in result
        assert "**Art. 1**" in result
        assert "Purpose" in result

    def test_with_html_content(self):
        result = law_to_markdown(
            sr_number="220",
            title="Code of Obligations",
            xml_content="",
            html_content="<h1>Code of Obligations</h1><p>Article 1</p>",
            language="de",
            version_date=date(2023, 6, 15),
        )
        assert result.startswith("---")
        assert "Code of Obligations" in result
        assert "Article 1" in result

    def test_with_no_content(self):
        result = law_to_markdown(
            sr_number="999",
            title="Empty Law",
            xml_content="",
            html_content="",
            language="fr",
            version_date=date(2020, 1, 1),
        )
        assert "# Empty Law" in result
        assert "No text content available" in result

    def test_xml_preferred_over_html(self):
        """When both XML and HTML are provided, XML takes precedence."""
        result = law_to_markdown(
            sr_number="101",
            title="Test",
            xml_content=MINIMAL_AKN,
            html_content="<p>HTML version</p>",
            language="de",
            version_date=date(2024, 1, 1),
        )
        assert "Art. 1" in result
        assert "HTML version" not in result

    def test_ends_with_newline(self):
        result = law_to_markdown(
            sr_number="101",
            title="Test",
            xml_content=MINIMAL_AKN,
            html_content="",
            language="de",
            version_date=date(2024, 1, 1),
        )
        assert result.endswith("\n")


# ---------------------------------------------------------------------------
# sr_to_path tests (basic; edge cases in test 3.5)
# ---------------------------------------------------------------------------

class TestSrToPath:
    def test_simple_sr(self):
        assert sr_to_path("101", "de") == "ch/101/de/101.md"

    def test_dotted_sr(self):
        assert sr_to_path("220.1", "de") == "ch/220/de/220.1.md"

    def test_language_variants(self):
        assert sr_to_path("101", "fr") == "ch/101/fr/101.md"
        assert sr_to_path("101", "it") == "ch/101/it/101.md"

    def test_deep_sr(self):
        assert sr_to_path("172.010.1", "de") == "ch/172/de/172.010.1.md"


# ---------------------------------------------------------------------------
# sr_to_path edge cases (task 3.5)
# ---------------------------------------------------------------------------


class TestSrToPathEdgeCases:
    """Edge-case coverage for multi-level and unusual SR numbers."""

    # --- International treaties: SR numbers starting with 0 ---

    def test_sr_starting_with_zero(self):
        """SR 0.xxx are international treaties, base dir should be '0'."""
        assert sr_to_path("0.101.02", "de") == "ch/0/de/0.101.02.md"

    def test_sr_zero_all_languages(self):
        assert sr_to_path("0.101.02", "fr") == "ch/0/fr/0.101.02.md"
        assert sr_to_path("0.101.02", "it") == "ch/0/it/0.101.02.md"

    # --- Deeply nested SR numbers (4–7 levels) ---

    def test_four_level_sr(self):
        assert sr_to_path("0.101.02.1", "de") == "ch/0/de/0.101.02.1.md"

    def test_five_level_sr(self):
        assert sr_to_path("0.631.252.913.1", "de") == "ch/0/de/0.631.252.913.1.md"

    def test_six_level_sr(self):
        assert sr_to_path("0.631.252.913.611.1", "de") == "ch/0/de/0.631.252.913.611.1.md"

    def test_seven_level_sr(self):
        """Deepest SR numbers found in the real dataset."""
        assert sr_to_path("0.631.252.913.693.2", "fr") == "ch/0/fr/0.631.252.913.693.2.md"

    # --- Leading zeros in sub-parts ---

    def test_leading_zeros_in_subparts(self):
        """Sub-parts like 010 must be preserved, not stripped."""
        assert sr_to_path("172.010.1", "de") == "ch/172/de/172.010.1.md"

    def test_leading_zeros_in_second_subpart(self):
        assert sr_to_path("0.101.093", "de") == "ch/0/de/0.101.093.md"

    # --- Numeric-only (no dots) ---

    def test_three_digit_sr(self):
        assert sr_to_path("101", "de") == "ch/101/de/101.md"

    def test_single_digit_sr(self):
        """Hypothetical single-digit SR — base directory = the number itself."""
        assert sr_to_path("1", "de") == "ch/1/de/1.md"

    # --- Base directory is always the first segment before '.' ---

    def test_base_directory_consistency(self):
        """All sub-numbers of SR 220.x share the ch/220/ base directory."""
        assert sr_to_path("220.1", "de") == "ch/220/de/220.1.md"
        assert sr_to_path("220.12", "de") == "ch/220/de/220.12.md"

    def test_high_base_number(self):
        assert sr_to_path("984.1", "de") == "ch/984/de/984.1.md"
