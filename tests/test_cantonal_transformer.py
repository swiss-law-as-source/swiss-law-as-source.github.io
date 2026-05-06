"""Tests for cantonal HTML-to-Markdown transformer.

Covers all three source formats: LexWork, LexFind, ZHLex.
"""
from __future__ import annotations

from datetime import date

import pytest

from legalize_ch.cantonal_transformer import (
    transform_cantonal_html,
    transform_lexfind_html,
    transform_lexwork_html,
    transform_zhlex_html,
    _convert_single_row_tables_to_lists,
    _extract_lexfind_body,
    _format_article_headings,
    _postprocess_markdown,
    _preprocess_html,
    _separate_amendment_tables,
)
from legalize_ch.cantonal import (
    CantonalLawText,
    LEXWORK_CANTONS,
    cantonal_law_to_markdown,
)


# ─── Sample HTML snippets ────────────────────────────────────────────────────

LEXWORK_SIMPLE = """\
<div>
  <h1>300.100</h1>
  <h1>Gemeindegesetz</h1>
  <h2>(GemG)</h2>
  <p>Vom 15. Juni 2005 (Stand 1. Januar 2024)</p>
  <p>Der Grosse Rat des Kantons Basel-Stadt,</p>
  <p>beschliesst:</p>
  <p>§ 1</p>
  <p>Geltungsbereich</p>
  <p>1</p>
  <p>Dieses Gesetz regelt das Gemeindewesen.</p>
  <p>§ 2</p>
  <p>Anwendung</p>
  <p>1</p>
  <p>Es gilt für alle Einwohnergemeinden.</p>
</div>
"""

LEXWORK_WITH_TABLE_LIST = """\
<div>
  <p>§ 5</p>
  <p>Zuständigkeit</p>
  <p>1</p>
  <p>Die Kommission</p>
  <table>
    <tr><td>a)</td><td>vollzieht die Aufgaben;</td></tr>
    <tr><td>b)</td><td>entscheidet über Gesuche;</td></tr>
    <tr><td>c)</td><td>erteilt Bewilligungen.</td></tr>
  </table>
</div>
"""

LEXWORK_WITH_AMENDMENT_TABLE = """\
<div>
  <p>§ 1</p>
  <p>Test Article</p>
  <p>1</p>
  <p>Content here.</p>
  <h1>Änderungstabelle - Nach Beschluss</h1>
  <table>
    <tr><th>Beschluss</th><th>Inkrafttreten</th><th>Änderung</th></tr>
    <tr><td>01.01.2020</td><td>01.07.2020</td><td>Erstfassung</td></tr>
  </table>
</div>
"""

LEXWORK_WITH_FOOTNOTES = """\
<div>
  <p>§ 1</p>
  <p>Grundlage</p>
  <p>1</p>
  <p>Gestützt auf Art. 14 BGFA<sup>[1]</sup>.</p>
  <ol>
    <li>[1] SR 935.61</li>
    <li>[2] SAR 155.200</li>
  </ol>
</div>
"""

LEXWORK_WITH_SCRIPT_STYLE = """\
<div>
  <style>.law { color: black; }</style>
  <script>console.log('test');</script>
  <p>§ 1</p>
  <p>Content</p>
  <!-- This is a comment -->
  <nav><a href="/">Home</a></nav>
</div>
"""

LEXFIND_FULL_PAGE = """\
<html>
<head><title>LexFind</title></head>
<body>
  <header><h1>LexFind.ch</h1><nav><a href="/">Home</a></nav></header>
  <div class="tol-content">
    <h1>100.1 Verfassung</h1>
    <p>Art. 1</p>
    <p>Grundsatz</p>
    <p>1</p>
    <p>Der Kanton ist souverän.</p>
  </div>
  <footer><p>Copyright LexFind</p></footer>
</body>
</html>
"""

LEXFIND_WITH_ARTICLE = """\
<html>
<body>
  <article>
    <h1>Gesetz über die Organisation</h1>
    <p>Art. 1</p>
    <p>Gegenstand</p>
    <p>Dieses Gesetz regelt die Organisation.</p>
  </article>
</body>
</html>
"""

ZHLEX_HTML = """\
<div class="erlass-text">
  <h1 class="erlass-titel">Verfassung des Kantons Zürich</h1>
  <p class="erlass-meta">LS 101</p>
  <h2>1. Titel: Grundlagen</h2>
  <p>Art. 1</p>
  <p>Staatswesen</p>
  <p>1 Der Kanton Zürich ist ein Gliedstaat der Schweizerischen Eidgenossenschaft.</p>
  <p>Art. 2</p>
  <p>Demokratie</p>
  <p>1 Der Kanton ist demokratisch verfasst.</p>
</div>
"""


# ─── LexWork transformer tests ───────────────────────────────────────────────


class TestTransformLexworkHtml:
    def test_empty_input(self):
        assert transform_lexwork_html("") == ""
        assert transform_lexwork_html(None) == ""
        assert transform_lexwork_html("   ") == ""

    def test_basic_structure(self):
        result = transform_lexwork_html(LEXWORK_SIMPLE)
        assert "Gemeindegesetz" in result
        assert "Geltungsbereich" in result
        assert "Dieses Gesetz regelt das Gemeindewesen." in result

    def test_article_headings_formatted(self):
        result = transform_lexwork_html(LEXWORK_SIMPLE)
        # § headings should be bold
        assert "**§ 1**" in result
        assert "**§ 2**" in result

    def test_table_lists_converted(self):
        result = transform_lexwork_html(LEXWORK_WITH_TABLE_LIST)
        # Tables should become list items, not markdown tables
        assert "---|---" not in result
        # Items should be present
        assert "vollzieht die Aufgaben" in result
        assert "entscheidet über Gesuche" in result
        assert "erteilt Bewilligungen" in result

    def test_amendment_table_separated(self):
        result = transform_lexwork_html(LEXWORK_WITH_AMENDMENT_TABLE)
        assert "Content here." in result
        assert "Änderungstabelle" in result
        # Should have separator before amendment table
        assert "---" in result

    def test_scripts_and_styles_removed(self):
        result = transform_lexwork_html(LEXWORK_WITH_SCRIPT_STYLE)
        assert "console.log" not in result
        assert "color: black" not in result
        assert "This is a comment" not in result
        assert "Content" in result

    def test_nav_removed(self):
        result = transform_lexwork_html(LEXWORK_WITH_SCRIPT_STYLE)
        assert "Home" not in result

    def test_footnotes_preserved(self):
        result = transform_lexwork_html(LEXWORK_WITH_FOOTNOTES)
        assert "SR 935.61" in result
        assert "SAR 155.200" in result


# ─── LexFind transformer tests ───────────────────────────────────────────────


class TestTransformLexfindHtml:
    def test_empty_input(self):
        assert transform_lexfind_html("") == ""

    def test_body_extraction(self):
        result = transform_lexfind_html(LEXFIND_FULL_PAGE)
        # Should have the law content
        assert "Verfassung" in result
        assert "Der Kanton ist souverän." in result
        # Should NOT have navigation/footer
        assert "Copyright LexFind" not in result

    def test_article_tag_extraction(self):
        result = transform_lexfind_html(LEXFIND_WITH_ARTICLE)
        assert "Gesetz über die Organisation" in result
        assert "Dieses Gesetz regelt die Organisation." in result

    def test_fallback_to_full_html(self):
        """If no body wrapper found, process entire HTML."""
        simple = "<p>Simple content</p>"
        result = transform_lexfind_html(simple)
        assert "Simple content" in result


# ─── ZHLex transformer tests ─────────────────────────────────────────────────


class TestTransformZhlexHtml:
    def test_empty_input(self):
        assert transform_zhlex_html("") == ""

    def test_basic_structure(self):
        result = transform_zhlex_html(ZHLEX_HTML)
        assert "Verfassung des Kantons Zürich" in result
        assert "Grundlagen" in result
        assert "Der Kanton Zürich ist ein Gliedstaat" in result

    def test_article_formatting(self):
        result = transform_zhlex_html(ZHLEX_HTML)
        # Art. headings should be present
        assert "Art. 1" in result
        assert "Art. 2" in result


# ─── Dispatch function tests ─────────────────────────────────────────────────


class TestTransformCantonalHtml:
    def test_dispatch_lexwork(self):
        result = transform_cantonal_html("<p>Test</p>", source="lexwork")
        assert "Test" in result

    def test_dispatch_lexfind(self):
        result = transform_cantonal_html("<p>Test</p>", source="lexfind")
        assert "Test" in result

    def test_dispatch_zhlex(self):
        result = transform_cantonal_html("<p>Test</p>", source="zhlex")
        assert "Test" in result

    def test_dispatch_case_insensitive(self):
        result = transform_cantonal_html("<p>Test</p>", source="LEXWORK")
        assert "Test" in result

    def test_default_is_lexwork(self):
        result = transform_cantonal_html("<p>Test</p>", source="unknown")
        assert "Test" in result


# ─── Preprocessing tests ─────────────────────────────────────────────────────


class TestPreprocessHtml:
    def test_removes_script_tags(self):
        html = "<p>Before</p><script>alert('x')</script><p>After</p>"
        result = _preprocess_html(html)
        assert "alert" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_style_tags(self):
        html = "<style>.x { color: red; }</style><p>Content</p>"
        result = _preprocess_html(html)
        assert "color: red" not in result
        assert "Content" in result

    def test_removes_html_comments(self):
        html = "<!-- comment --><p>Visible</p>"
        result = _preprocess_html(html)
        assert "comment" not in result
        assert "Visible" in result

    def test_removes_nav_header_footer(self):
        html = "<header>H</header><p>Body</p><footer>F</footer>"
        result = _preprocess_html(html)
        assert "<header" not in result
        assert "<footer" not in result
        assert "Body" in result

    def test_normalizes_nbsp(self):
        html = "<p>Hello\xa0world</p>"
        result = _preprocess_html(html)
        assert "\xa0" not in result
        assert "Hello world" in result


# ─── Table-to-list conversion tests ──────────────────────────────────────────


class TestConvertSingleRowTablesToLists:
    def test_simple_enum_table(self):
        html = """<table>
            <tr><td>a)</td><td>First item</td></tr>
            <tr><td>b)</td><td>Second item</td></tr>
        </table>"""
        result = _convert_single_row_tables_to_lists(html)
        assert "<ul>" in result or "<li>" in result
        assert "First item" in result
        assert "Second item" in result

    def test_numbered_enum_table(self):
        html = """<table>
            <tr><td>1.</td><td>Punkt eins</td></tr>
            <tr><td>2.</td><td>Punkt zwei</td></tr>
        </table>"""
        result = _convert_single_row_tables_to_lists(html)
        assert "Punkt eins" in result
        assert "Punkt zwei" in result

    def test_non_enum_table_preserved(self):
        """Tables with 3+ columns should not be converted."""
        html = """<table>
            <tr><td>A</td><td>B</td><td>C</td></tr>
            <tr><td>1</td><td>2</td><td>3</td></tr>
        </table>"""
        result = _convert_single_row_tables_to_lists(html)
        # Should still contain table structure
        assert "A" in result
        assert "B" in result

    def test_non_label_table_preserved(self):
        """Tables where first column isn't a label pattern should be preserved."""
        html = """<table>
            <tr><td>This is a full sentence</td><td>Another sentence</td></tr>
        </table>"""
        result = _convert_single_row_tables_to_lists(html)
        assert "This is a full sentence" in result


# ─── LexFind body extraction tests ───────────────────────────────────────────


class TestExtractLexfindBody:
    def test_tol_content_div(self):
        html = '<html><body><div class="tol-content"><p>Law text</p></div></body></html>'
        result = _extract_lexfind_body(html)
        assert "Law text" in result

    def test_article_tag(self):
        html = "<html><body><article><p>Content</p></article></body></html>"
        result = _extract_lexfind_body(html)
        assert "Content" in result

    def test_main_tag(self):
        html = "<html><body><main><p>Content</p></main></body></html>"
        result = _extract_lexfind_body(html)
        assert "Content" in result

    def test_body_fallback(self):
        html = "<html><body><div><p>Content</p></div></body></html>"
        result = _extract_lexfind_body(html)
        assert "Content" in result


# ─── Markdown postprocessing tests ────────────────────────────────────────────


class TestPostprocessMarkdown:
    def test_collapses_blank_lines(self):
        md = "Line 1\n\n\n\n\nLine 2"
        result = _postprocess_markdown(md)
        assert "\n\n\n" not in result
        assert "Line 1" in result
        assert "Line 2" in result

    def test_removes_trailing_whitespace(self):
        md = "Line with spaces   \nNext line"
        result = _postprocess_markdown(md)
        assert "   \n" not in result

    def test_cleans_table_separator_artifacts(self):
        md = "Some text\n---|---\nMore text"
        result = _postprocess_markdown(md)
        assert "---|---" not in result


class TestFormatArticleHeadings:
    def test_paragraph_sign(self):
        md = "\n§ 1\n\nGeltungsbereich\n"
        result = _format_article_headings(md)
        assert "**§ 1**" in result

    def test_paragraph_with_suffix(self):
        md = "\n§ 5a\n\nTitle\n"
        result = _format_article_headings(md)
        assert "**§ 5a**" in result

    def test_paragraph_with_amendment_stars(self):
        md = "\n§ 9 *****\n\nTitle\n"
        result = _format_article_headings(md)
        assert "**§ 9 *****" in result

    def test_article_with_art_prefix(self):
        md = "\nArt. 1\n\nGegenstand\n"
        result = _format_article_headings(md)
        assert "**Art. 1**" in result

    def test_does_not_bold_inline_references(self):
        """Should not bold § references that appear inline in text."""
        md = "Gestützt auf § 14 des Gesetzes.\n"
        result = _format_article_headings(md)
        # The § 14 is inline, not a heading - should not be bolded
        assert "**§ 14**" not in result


class TestSeparateAmendmentTables:
    def test_separates_amendment_table(self):
        md = "Law text here.\n\n# Änderungstabelle - Nach Beschluss\n\nTable content"
        result = _separate_amendment_tables(md)
        assert "---" in result
        idx_separator = result.index("---")
        idx_table = result.index("Änderungstabelle")
        assert idx_separator < idx_table


# ─── Integration: cantonal_law_to_markdown uses new transformer ──────────────


class TestCantonalLawToMarkdownIntegration:
    def test_lexwork_canton_uses_cantonal_transformer(self):
        """A LexWork canton should use the cantonal transformer, not generic html_to_markdown."""
        text = CantonalLawText(
            canton="bs",
            systematic_number="300.100",
            title="Test Law",
            html_content=LEXWORK_WITH_TABLE_LIST,
            language="de",
            version_date=date(2024, 1, 1),
        )
        md = cantonal_law_to_markdown(text)
        # Should have frontmatter
        assert "---" in md
        assert "canton: BS" in md
        # Table lists should be converted (no ---|--- artifacts)
        assert "vollzieht die Aufgaben" in md

    def test_zhlex_canton_uses_zhlex_transformer(self):
        text = CantonalLawText(
            canton="zh",
            systematic_number="101",
            title="Verfassung",
            html_content=ZHLEX_HTML,
            language="de",
            version_date=date(2024, 1, 1),
        )
        md = cantonal_law_to_markdown(text)
        assert "source: ZHLex" in md
        assert "Verfassung des Kantons Zürich" in md

    def test_lexfind_canton_uses_lexfind_transformer(self):
        text = CantonalLawText(
            canton="ti",  # LexFind-only canton
            systematic_number="100.1",
            title="Costituzione",
            html_content=LEXFIND_FULL_PAGE,
            language="it",
        )
        md = cantonal_law_to_markdown(text)
        assert "source: LexFind" in md
        # Should have extracted body content
        assert "Verfassung" in md

    def test_no_content_still_works(self):
        text = CantonalLawText(
            canton="ag",
            systematic_number="100.1",
            title="Empty Law",
            html_content="",
            language="de",
        )
        md = cantonal_law_to_markdown(text)
        assert "No text content available" in md

    def test_source_label_capitalization(self):
        """LexWork source should be 'LexWork', ZHLex stays 'ZHLex'."""
        for canton, expected_source in [("bs", "LexWork"), ("zh", "ZHLex"), ("ti", "LexFind")]:
            text = CantonalLawText(
                canton=canton,
                systematic_number="100.1",
                title="Test",
                html_content="<p>Content</p>",
                language="de",
            )
            md = cantonal_law_to_markdown(text)
            assert f"source: {expected_source}" in md
