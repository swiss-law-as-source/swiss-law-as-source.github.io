"""Tests for the RSS/Atom feed generator."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from legalize_ch.rss_feed import (
    FeedEntry,
    _extract_sr_from_path,
    generate_atom_feed,
    generate_rss_feed,
)


@pytest.fixture
def sample_entries():
    """Create sample feed entries for testing."""
    return [
        FeedEntry(
            sr_number="210",
            language="de",
            title="SR 210 (de) updated",
            commit_hash="abc123def456",
            author_date=datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc),
            commit_message="Update ZGB Art. 1-10",
            diff_text="@@ -1,3 +1,4 @@\n+New paragraph added\n Old text",
            link="https://github.com/benjamin-arfa/swiss-law/commit/abc123def456",
        ),
        FeedEntry(
            sr_number="311.0",
            language="de",
            title="SR 311.0 (de) updated",
            commit_hash="def789abc012",
            author_date=datetime(2026, 4, 28, 14, 30, 0, tzinfo=timezone.utc),
            commit_message="Update StGB Art. 111",
            diff_text="@@ -5,2 +5,3 @@\n-Old penalty\n+New penalty provision",
            link="https://github.com/benjamin-arfa/swiss-law/commit/def789abc012",
        ),
    ]


class TestExtractSrFromPath:
    """Tests for _extract_sr_from_path helper."""

    def test_layout_lang_first(self):
        """ch/{lang}/{prefix}/{sr}.md layout."""
        assert _extract_sr_from_path("ch/de/520/520.151.md") == ("520.151", "de")

    def test_layout_prefix_first(self):
        """ch/{prefix}/{lang}/{sr}.md layout."""
        assert _extract_sr_from_path("ch/2/de/210.md") == ("210", "de")

    def test_valid_dotted_sr(self):
        assert _extract_sr_from_path("ch/fr/3/311.0.md") == ("311.0", "fr")

    def test_deep_path(self):
        assert _extract_sr_from_path("ch/it/1/101.md") == ("101", "it")

    def test_english(self):
        assert _extract_sr_from_path("ch/en/2/210.md") == ("210", "en")

    def test_invalid_no_ch_prefix(self):
        assert _extract_sr_from_path("kt/zh/de/100.md") is None

    def test_invalid_unknown_lang(self):
        assert _extract_sr_from_path("ch/1/xx/101.md") is None

    def test_invalid_short_path(self):
        assert _extract_sr_from_path("ch/1/de") is None


class TestFeedEntry:
    """Tests for FeedEntry dataclass."""

    def test_guid(self, sample_entries):
        entry = sample_entries[0]
        assert entry.guid == "urn:swiss-law:210:de:abc123def456"

    def test_guid_unique(self, sample_entries):
        guids = [e.guid for e in sample_entries]
        assert len(set(guids)) == len(guids)


class TestGenerateAtomFeed:
    """Tests for Atom feed generation."""

    def test_valid_xml(self, sample_entries):
        xml_str = generate_atom_feed(sample_entries)
        # Should parse without errors
        root = ET.fromstring(xml_str)
        assert root is not None

    def test_contains_entries(self, sample_entries):
        xml_str = generate_atom_feed(sample_entries)
        root = ET.fromstring(xml_str)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        assert len(entries) == 2

    def test_entry_has_title(self, sample_entries):
        xml_str = generate_atom_feed(sample_entries)
        root = ET.fromstring(xml_str)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        first_entry = root.find("atom:entry", ns)
        title = first_entry.find("atom:title", ns)
        assert title.text == "SR 210 (de) updated"

    def test_entry_has_diff_content(self, sample_entries):
        xml_str = generate_atom_feed(sample_entries)
        root = ET.fromstring(xml_str)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        first_entry = root.find("atom:entry", ns)
        content = first_entry.find("atom:content", ns)
        assert "New paragraph added" in content.text

    def test_custom_title(self, sample_entries):
        xml_str = generate_atom_feed(sample_entries, title="My Custom Feed")
        root = ET.fromstring(xml_str)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        title = root.find("atom:title", ns)
        assert title.text == "My Custom Feed"

    def test_empty_entries(self):
        xml_str = generate_atom_feed([])
        root = ET.fromstring(xml_str)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        assert len(entries) == 0

    def test_categories(self, sample_entries):
        xml_str = generate_atom_feed(sample_entries)
        root = ET.fromstring(xml_str)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        first_entry = root.find("atom:entry", ns)
        categories = first_entry.findall("atom:category", ns)
        terms = [c.get("term") for c in categories]
        assert "sr:210" in terms
        assert "lang:de" in terms


class TestGenerateRssFeed:
    """Tests for RSS 2.0 feed generation."""

    def test_valid_xml(self, sample_entries):
        xml_str = generate_rss_feed(sample_entries)
        root = ET.fromstring(xml_str)
        assert root.tag == "rss"
        assert root.get("version") == "2.0"

    def test_contains_items(self, sample_entries):
        xml_str = generate_rss_feed(sample_entries)
        root = ET.fromstring(xml_str)
        channel = root.find("channel")
        items = channel.findall("item")
        assert len(items) == 2

    def test_item_has_guid(self, sample_entries):
        xml_str = generate_rss_feed(sample_entries)
        root = ET.fromstring(xml_str)
        channel = root.find("channel")
        first_item = channel.find("item")
        guid = first_item.find("guid")
        assert "swiss-law" in guid.text

    def test_item_has_description_with_diff(self, sample_entries):
        xml_str = generate_rss_feed(sample_entries)
        root = ET.fromstring(xml_str)
        channel = root.find("channel")
        first_item = channel.find("item")
        desc = first_item.find("description")
        assert "New paragraph added" in desc.text

    def test_item_has_category(self, sample_entries):
        xml_str = generate_rss_feed(sample_entries)
        root = ET.fromstring(xml_str)
        channel = root.find("channel")
        first_item = channel.find("item")
        category = first_item.find("category")
        assert category.text == "SR 210"

    def test_empty_entries(self):
        xml_str = generate_rss_feed([])
        root = ET.fromstring(xml_str)
        channel = root.find("channel")
        items = channel.findall("item")
        assert len(items) == 0
