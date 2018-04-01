"""Diff RSS/Atom feed generator for Swiss law changes.

Generates RSS 2.0 and Atom feeds from git history, showing what changed
in law texts. Supports filtering by SR number prefix to subscribe to
specific areas of law.
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FeedEntry:
    """A single change entry in the feed."""
    sr_number: str
    language: str
    title: str
    commit_hash: str
    author_date: datetime
    commit_message: str
    diff_text: str
    link: str = ""

    @property
    def guid(self) -> str:
        """Unique identifier for this entry."""
        return f"urn:swiss-law:{self.sr_number}:{self.language}:{self.commit_hash[:12]}"


def _run_git(args: list[str], repo_path: str | Path, timeout: int = 30) -> str | None:
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _extract_sr_from_path(file_path: str) -> tuple[str, str] | None:
    """Extract SR number and language from a file path.

    Supports both directory layouts:
      - ch/{lang}/{prefix}/{sr}.md  (e.g. ch/de/520/520.151.md)
      - ch/{prefix}/{lang}/{sr}.md  (e.g. ch/520/de/520.151.md)
    """
    parts = file_path.split("/")
    if len(parts) < 4 or parts[0] != "ch":
        return None

    # Determine which part is the language
    if parts[1] in ("de", "fr", "it", "en"):
        # Layout: ch/{lang}/{prefix}/{sr}.md
        lang = parts[1]
    elif len(parts) >= 4 and parts[2] in ("de", "fr", "it", "en"):
        # Layout: ch/{prefix}/{lang}/{sr}.md
        lang = parts[2]
    else:
        return None

    sr_number = parts[-1].removesuffix(".md")
    return sr_number, lang


def get_recent_changes(
    repo_path: str | Path,
    sr_filter: str | None = None,
    lang: str | None = None,
    limit: int = 50,
    since_days: int = 90,
) -> list[FeedEntry]:
    """Get recent law changes from git history.

    Args:
        repo_path: Path to the swiss-law git repository.
        sr_filter: Only include laws whose SR number starts with this prefix.
        lang: Only include changes in this language (de/fr/it/en).
        limit: Maximum number of entries to return.
        since_days: Only look at commits from the last N days.

    Returns:
        List of FeedEntry objects sorted by date (newest first).
    """
    repo_path = Path(repo_path)

    # Build git log command with path filter
    # Note: commits in this repo use author dates that reflect the legal timeline
    # (often years in the past), so --since may not work as expected. We use -N
    # to get enough commits and rely on limit for final count.
    git_args = [
        "log",
        "--format=%H|%aI|%s",
        "--name-only",
        f"-{limit * 5}",  # Fetch more commits than needed (multiple files per commit)
        "--diff-filter=AM",  # Only additions and modifications
        "--",
        "ch/",
    ]

    output = _run_git(git_args, repo_path)
    if not output:
        return []

    # Parse git log output (commit lines followed by file names)
    entries: list[FeedEntry] = []
    current_commit: tuple[str, datetime, str] | None = None

    for line in output.strip().split("\n"):
        if not line:
            continue

        if "|" in line and line.count("|") >= 2:
            # This is a commit header line
            parts = line.split("|", 2)
            commit_hash = parts[0]
            try:
                author_date = datetime.fromisoformat(parts[1])
            except ValueError:
                continue
            message = parts[2]
            current_commit = (commit_hash, author_date, message)
        elif current_commit and line.startswith("ch/") and line.endswith(".md"):
            # This is a file path
            parsed = _extract_sr_from_path(line)
            if not parsed:
                continue
            sr_number, file_lang = parsed

            # Apply filters
            if sr_filter and not sr_number.startswith(sr_filter):
                continue
            if lang and file_lang != lang:
                continue

            commit_hash, author_date, message = current_commit

            # Get diff for this file in this commit
            diff_text = _get_file_diff(repo_path, commit_hash, line)

            entry = FeedEntry(
                sr_number=sr_number,
                language=file_lang,
                title=f"SR {sr_number} ({file_lang}) updated",
                commit_hash=commit_hash,
                author_date=author_date,
                commit_message=message,
                diff_text=diff_text or "(no diff available)",
                link=f"https://github.com/benjamin-arfa/swiss-law/commit/{commit_hash}",
            )
            entries.append(entry)

            if len(entries) >= limit:
                break

    return entries


def _get_file_diff(repo_path: Path, commit_hash: str, file_path: str) -> str | None:
    """Get the unified diff for a specific file in a specific commit."""
    output = _run_git(
        ["diff", f"{commit_hash}~1", commit_hash, "--", file_path],
        repo_path,
        timeout=10,
    )
    if output:
        # Truncate very long diffs
        lines = output.split("\n")
        if len(lines) > 100:
            return "\n".join(lines[:100]) + f"\n... ({len(lines) - 100} more lines)"
        return output
    return None


def generate_atom_feed(
    entries: list[FeedEntry],
    title: str = "Swiss Law Changes",
    feed_url: str = "",
    site_url: str = "https://github.com/benjamin-arfa/swiss-law",
) -> str:
    """Generate an Atom 1.0 feed XML string from entries.

    Args:
        entries: List of FeedEntry objects.
        title: Feed title.
        feed_url: Self-link URL for the feed.
        site_url: Base URL of the project.

    Returns:
        XML string of the Atom feed.
    """
    ATOM_NS = "http://www.w3.org/2005/Atom"
    ET.register_namespace("", ATOM_NS)

    feed = ET.Element("{http://www.w3.org/2005/Atom}feed")

    ET.SubElement(feed, "title").text = title
    ET.SubElement(feed, "id").text = f"urn:swiss-law:feed:{hashlib.md5(title.encode()).hexdigest()[:8]}"

    # Updated timestamp
    if entries:
        updated = entries[0].author_date.isoformat()
    else:
        updated = datetime.now(timezone.utc).isoformat()
    ET.SubElement(feed, "updated").text = updated

    # Links
    link_self = ET.SubElement(feed, "link")
    link_self.set("rel", "self")
    link_self.set("href", feed_url or f"{site_url}/feeds/changes.atom")

    link_alt = ET.SubElement(feed, "link")
    link_alt.set("rel", "alternate")
    link_alt.set("href", site_url)

    # Author
    author = ET.SubElement(feed, "author")
    ET.SubElement(author, "name").text = "Swiss Law Pipeline (legalize-ch)"

    # Entries
    for entry in entries:
        atom_entry = ET.SubElement(feed, "entry")
        ET.SubElement(atom_entry, "title").text = entry.title
        ET.SubElement(atom_entry, "id").text = entry.guid

        entry_link = ET.SubElement(atom_entry, "link")
        entry_link.set("href", entry.link)

        ET.SubElement(atom_entry, "updated").text = entry.author_date.isoformat()
        ET.SubElement(atom_entry, "published").text = entry.author_date.isoformat()

        # Summary = commit message
        summary = ET.SubElement(atom_entry, "summary")
        summary.text = entry.commit_message

        # Content = diff
        content = ET.SubElement(atom_entry, "content")
        content.set("type", "text")
        content.text = entry.diff_text

        # Categories
        cat_sr = ET.SubElement(atom_entry, "category")
        cat_sr.set("term", f"sr:{entry.sr_number}")
        cat_sr.set("label", f"SR {entry.sr_number}")

        cat_lang = ET.SubElement(atom_entry, "category")
        cat_lang.set("term", f"lang:{entry.language}")
        cat_lang.set("label", entry.language.upper())

    # Serialize
    tree = ET.ElementTree(feed)
    ET.indent(tree, space="  ")

    import io
    buf = io.BytesIO()
    tree.write(buf, xml_declaration=True, encoding="utf-8")
    return buf.getvalue().decode("utf-8")


def generate_rss_feed(
    entries: list[FeedEntry],
    title: str = "Swiss Law Changes",
    feed_url: str = "",
    site_url: str = "https://github.com/benjamin-arfa/swiss-law",
) -> str:
    """Generate an RSS 2.0 feed XML string from entries.

    Args:
        entries: List of FeedEntry objects.
        title: Feed title.
        feed_url: Self-link URL for the feed.
        site_url: Base URL of the project.

    Returns:
        XML string of the RSS 2.0 feed.
    """
    ATOM_NS = "http://www.w3.org/2005/Atom"
    ET.register_namespace("atom", ATOM_NS)

    rss = ET.Element("rss")
    rss.set("version", "2.0")

    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = (
        "Track changes to Swiss federal legislation. "
        "Each entry represents a modification to a law text."
    )
    ET.SubElement(channel, "language").text = "de"
    ET.SubElement(channel, "generator").text = "legalize-ch RSS feed generator"

    if entries:
        ET.SubElement(channel, "lastBuildDate").text = (
            entries[0].author_date.strftime("%a, %d %b %Y %H:%M:%S %z")
        )

    # Self link (Atom namespace)
    atom_link = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")
    atom_link.set("href", feed_url or f"{site_url}/feeds/changes.rss")

    # Items
    for entry in entries:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = entry.title
        ET.SubElement(item, "link").text = entry.link
        ET.SubElement(item, "guid").text = entry.guid
        ET.SubElement(item, "pubDate").text = (
            entry.author_date.strftime("%a, %d %b %Y %H:%M:%S %z")
        )
        ET.SubElement(item, "description").text = (
            f"{entry.commit_message}\n\n"
            f"<pre>{_escape_xml(entry.diff_text)}</pre>"
        )
        ET.SubElement(item, "category").text = f"SR {entry.sr_number}"

    # Serialize
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")

    import io
    buf = io.BytesIO()
    tree.write(buf, xml_declaration=True, encoding="utf-8")
    return buf.getvalue().decode("utf-8")


def _escape_xml(text: str) -> str:
    """Escape text for safe XML embedding."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_feeds(
    repo_path: str | Path,
    output_dir: str | Path | None = None,
    sr_filter: str | None = None,
    lang: str | None = None,
    limit: int = 50,
    since_days: int = 90,
) -> tuple[Path, Path]:
    """Generate and write both RSS and Atom feeds to disk.

    Args:
        repo_path: Path to the swiss-law git repo.
        output_dir: Output directory (default: {repo}/docs/feeds/).
        sr_filter: Only include laws starting with this SR prefix.
        lang: Only include this language.
        limit: Max entries per feed.
        since_days: Look back this many days.

    Returns:
        Tuple of (rss_path, atom_path).
    """
    repo_path = Path(repo_path)
    if output_dir is None:
        output_dir = repo_path / "docs" / "feeds"
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    entries = get_recent_changes(
        repo_path=repo_path,
        sr_filter=sr_filter,
        lang=lang,
        limit=limit,
        since_days=since_days,
    )

    # Build title
    title_parts = ["Swiss Law Changes"]
    if sr_filter:
        title_parts.append(f"(SR {sr_filter}*)")
    if lang:
        title_parts.append(f"[{lang.upper()}]")
    title = " ".join(title_parts)

    # Determine filenames
    suffix = ""
    if sr_filter:
        suffix += f"_sr{sr_filter.replace('.', '-')}"
    if lang:
        suffix += f"_{lang}"

    rss_path = output_dir / f"changes{suffix}.rss"
    atom_path = output_dir / f"changes{suffix}.atom"

    # Generate feeds
    rss_xml = generate_rss_feed(entries, title=title)
    atom_xml = generate_atom_feed(entries, title=title)

    rss_path.write_text(rss_xml, encoding="utf-8")
    atom_path.write_text(atom_xml, encoding="utf-8")

    logger.info("Written %d entries to %s and %s", len(entries), rss_path, atom_path)
    return rss_path, atom_path
