"""CLI interface for the Swiss law pipeline."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from .pipeline import Pipeline


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def main(verbose: bool):
    """Swiss law pipeline — fetch, transform, commit."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@main.command()
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--limit", "-n", type=int, default=None, help="Max laws to process")
@click.option("--lang", "-l", multiple=True, default=["de", "fr", "it"], help="Languages")
@click.option("--sr", type=str, default=None, help="SR number prefix filter")
@click.option("--rate-limit", type=float, default=1.5, help="Seconds between requests")
@click.option("--latest-only", is_flag=True, help="Only fetch the latest version per law")
@click.option("--no-chronological", is_flag=True,
              help="Disable chronological sorting (commits grouped by law instead)")
def bootstrap(repo: str, limit: int | None, lang: tuple, sr: str | None, rate_limit: float,
              latest_only: bool, no_chronological: bool):
    """Full pipeline: fetch all laws and commit to git.

    By default, all revisions are sorted by date before committing so that
    the git history reflects the actual legal timeline (chronological order).
    Use --no-chronological to revert to the old behavior (grouped by law).
    """
    pipeline = Pipeline(repo_path=repo, rate_limit=rate_limit)
    total = pipeline.run(limit=limit, languages=list(lang), sr_filter=sr,
                         latest_only=latest_only, chronological=not no_chronological)
    click.echo(f"Done. {total} commits created.")


@main.command()
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--limit", "-n", type=int, default=None, help="Max laws to process")
@click.option("--lang", "-l", multiple=True, default=["de", "fr", "it"], help="Languages")
@click.option("--sr", type=str, default=None, help="SR number prefix filter")
@click.option("--rate-limit", type=float, default=1.5, help="Seconds between requests")
@click.option("--since", type=click.DateTime(formats=["%Y-%m-%d"]), default=None,
              help="Override last_run: only fetch versions since this date (YYYY-MM-DD)")
@click.option("--no-chronological", is_flag=True,
              help="Disable chronological sorting of commits")
def update(repo: str, limit: int | None, lang: tuple, sr: str | None, rate_limit: float,
           since, no_chronological: bool):
    """Incremental update: only fetch laws with new consolidation versions.

    Detects new versions by comparing Fedlex consolidation dates against
    the pipeline state. Only versions with dateApplicability >= since are
    fetched, and already-processed versions are skipped automatically.

    By default uses last_run from pipeline state. Use --since to override.
    Commits are sorted chronologically by default.
    """
    from datetime import date as date_type
    pipeline = Pipeline(repo_path=repo, rate_limit=rate_limit)
    since_date = since.date() if since else None
    total = pipeline.update(limit=limit, languages=list(lang), sr_filter=sr,
                            since_override=since_date,
                            chronological=not no_chronological)
    click.echo(f"Done. {total} commits created.")


@main.command()
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--limit", "-n", type=int, default=None, help="Max laws to fetch")
def catalog(repo: str, limit: int | None):
    """Fetch and display the law catalog."""
    from .fetcher import FedlexFetcher
    fetcher = FedlexFetcher()
    entries = fetcher.fetch_catalog(limit=limit)
    for e in entries:
        title = e.title_de or e.title_fr or e.title_it or "(no title)"
        click.echo(f"SR {e.sr_number:>12s}  {title[:80]}")
    click.echo(f"\nTotal: {len(entries)} laws")


@main.command("cantonal")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--canton", "-c", type=str, required=True, help="Canton abbreviation (e.g. bs, zh)")
@click.option("--number", "-n", type=str, default=None, help="Specific systematic number")
@click.option("--lang", "-l", default="de", help="Language (de/fr/it)")
@click.option("--rate-limit", type=float, default=1.0, help="Seconds between requests")
@click.option("--all-versions", is_flag=True, help="Fetch all versions (not just current)")
def cantonal(repo: str, canton: str, number: str | None, lang: str, rate_limit: float,
             all_versions: bool):
    """Fetch cantonal law: LexWork direct + LexFind fallback.

    Uses the LexWork JSON API for 14 cantons with direct portal access,
    falls back to LexFind for the remaining 12 cantons.
    """
    from pathlib import Path
    from .cantonal import (
        CantonalFetcher, LEXWORK_CANTONS, ALL_CANTONS,
        canton_to_path, cantonal_law_to_markdown,
    )
    from .committer import GitCommitter

    canton = canton.lower()
    if canton not in ALL_CANTONS:
        click.echo(f"Unknown canton: {canton}. Valid: {', '.join(ALL_CANTONS)}", err=True)
        raise SystemExit(1)

    fetcher = CantonalFetcher(rate_limit=rate_limit)
    committer = GitCommitter(repo)
    repo_path = Path(repo)
    commits = 0

    if number:
        # Fetch a specific law
        text = fetcher.fetch_law_text(canton, number, lang)
        if not text:
            click.echo(f"No text found for {canton.upper()} {number}")
            raise SystemExit(1)

        md = cantonal_law_to_markdown(text)
        rel_path = canton_to_path(canton, number, lang)
        abs_path = repo_path / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(md, encoding="utf-8")
        click.echo(f"Written: {rel_path}")

        if all_versions:
            versions = fetcher.fetch_versions(canton, number)
            for v in versions:
                vtext = fetcher.fetch_version_text(canton, number, v.version_id, lang)
                if vtext:
                    vmd = cantonal_law_to_markdown(vtext)
                    abs_path.write_text(vmd, encoding="utf-8")
                    click.echo(f"  Version {v.version_id}: {v.date_in_force or '?'}")
                    commits += 1
    else:
        # Fetch catalog and process all laws
        click.echo(f"Fetching catalog for {canton.upper()}...")
        if canton in LEXWORK_CANTONS:
            click.echo(f"  Source: LexWork ({LEXWORK_CANTONS[canton]})")
        else:
            click.echo(f"  Source: LexFind (fallback)")

        catalog = fetcher.fetch_lexwork_catalog(canton, lang)
        if not catalog:
            click.echo("No laws found in catalog. Try --number for specific law.")
            raise SystemExit(1)

        click.echo(f"  Found {len(catalog)} laws")
        for i, entry in enumerate(catalog):
            text = fetcher.fetch_law_text(canton, entry.systematic_number, lang,
                                          lexfind_id=entry.lexfind_id)
            if text:
                md = cantonal_law_to_markdown(text)
                rel_path = canton_to_path(canton, entry.systematic_number, lang)
                abs_path = repo_path / rel_path
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(md, encoding="utf-8")
                commits += 1
            if (i + 1) % 10 == 0:
                click.echo(f"  [{i+1}/{len(catalog)}] processed...")

    click.echo(f"Done. {commits} laws written.")


@main.command("cantonal-list")
@click.option("--canton", "-c", type=str, default=None, help="Specific canton")
def cantonal_list(canton: str | None):
    """List cantons and their data source (LexWork/LexFind)."""
    from .cantonal import LEXWORK_CANTONS, LEXFIND_ONLY_CANTONS

    click.echo("LexWork (direct API):")
    for c in sorted(LEXWORK_CANTONS.keys()):
        if canton and c != canton.lower():
            continue
        click.echo(f"  {c.upper():3s}  https://{LEXWORK_CANTONS[c]}/api/texts_of_law/")
    click.echo(f"\nLexFind (fallback):")
    for c in sorted(LEXFIND_ONLY_CANTONS):
        if canton and c != canton.lower():
            continue
        click.echo(f"  {c.upper():3s}  https://www.lexfind.ch/")
    click.echo(f"\nTotal: {len(LEXWORK_CANTONS)} LexWork + {len(LEXFIND_ONLY_CANTONS)} LexFind = 26 cantons")


if __name__ == "__main__":
    main()
