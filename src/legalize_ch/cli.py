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


@main.command("codify")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--lang", "-l", default="de", help="Source language (default: de)")
@click.option("--sr", type=str, default=None, help="SR number prefix filter")
@click.option("--limit", "-n", type=int, default=None, help="Max law groups to process")
@click.option("--dry-run", is_flag=True, help="Only log, don't generate")
def codify(repo: str, lang: str, sr: str | None, limit: int | None, dry_run: bool):
    """Convert law texts to executable OpenFisca code using Claude CLI.

    Reads articles from ch/{number}/{lang}/*.md, generates OpenFisca
    Variable classes, and writes to ch/{number}/executable/*.py.
    """
    from .law_to_openfisca import run_pipeline

    count = run_pipeline(
        repo_path=repo,
        lang=lang,
        sr_filter=sr,
        limit=limit,
        dry_run=dry_run,
    )
    click.echo(f"Done. {count} OpenFisca variables generated.")


@main.command("translate")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--sr", type=str, default=None, help="Specific SR number to translate")
@click.option("--source-lang", "-s", default="de", help="Source language (default: de)")
@click.option("--limit", "-n", type=int, default=None, help="Max files to translate")
@click.option("--sr-filter", type=str, default=None, help="SR number prefix filter")
@click.option("--model", default="claude-sonnet-4-20250514", help="Claude model for translation")
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", default=None,
              help="Anthropic API key (or set ANTHROPIC_API_KEY)")
def translate(repo: str, sr: str | None, source_lang: str, limit: int | None,
              sr_filter: str | None, model: str, api_key: str | None):
    """Translate law texts to English using the Anthropic API.

    Translates Swiss law texts from the source language (default: German)
    to English. Translated files are written to ch/{number}/en/{sr}.md.

    Uses Claude for high-quality legal translation that preserves
    structure and terminology.
    """
    from .translator import Translator

    if not api_key:
        click.echo("Error: ANTHROPIC_API_KEY not set. Provide via --api-key or env var.", err=True)
        raise SystemExit(1)

    translator = Translator(api_key=api_key, model=model)

    if sr:
        # Translate a single law
        ok = translator.translate_sr(repo, sr, source_lang)
        if ok:
            click.echo(f"Translated SR {sr} to English.")
        else:
            click.echo(f"Failed to translate SR {sr}.", err=True)
            raise SystemExit(1)
    else:
        # Batch translation
        count = translator.translate_directory(
            repo, sr_filter=sr_filter, source_lang=source_lang, limit=limit
        )
        click.echo(f"Done. {count} files translated to English.")


@main.command("index")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--lang", "-l", default="de", help="Language for titles (default: de)")
def index(repo: str, lang: str):
    """Generate INDEX.md with all SR numbers, titles, and links."""
    from .index_generator import write_index

    out = write_index(repo_path=repo, lang=lang)
    click.echo(f"Generated: {out}")


@main.command("health-check")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--days", "-d", type=int, default=30,
              help="Alert if no commits for this many days (default: 30)")
@click.option("--always-notify", is_flag=True,
              help="Send notification even when healthy")
def health_check(repo: str, days: int, always_notify: bool):
    """Check repo health and alert if no new commits for N days.

    Sends a Telegram notification if the most recent commit is older
    than --days (default 30). Use --always-notify to send a message
    regardless of health status.
    """
    from .health_check import check_health, send_health_alert

    is_healthy, message = check_health(repo, stale_days=days)
    click.echo(message)

    if not is_healthy or always_notify:
        ok = send_health_alert(
            repo_path=repo,
            stale_days=days,
            always_notify=always_notify,
        )
        if ok:
            click.echo("Telegram alert sent.")
        else:
            click.echo("Failed to send Telegram alert.", err=True)
            if not is_healthy:
                raise SystemExit(1)
    else:
        click.echo("No alert needed.")


@main.command("export")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--format", "-f", "fmt", type=click.Choice(["all", "csv", "jsonld"]),
              default="all", help="Export format (default: all)")
@click.option("--lang", "-l", multiple=True, default=["de", "fr", "it"], help="Languages")
@click.option("--sr", type=str, default=None, help="SR number prefix filter")
def export(repo: str, fmt: str, lang: tuple, sr: str | None):
    """Export structured metadata as JSON-LD and/or CSV.

    Scans all law markdown files, extracts frontmatter metadata,
    and writes structured exports to data/laws_metadata.{csv,jsonld}.
    """
    from .exporter import write_all, write_csv, write_jsonld

    languages = list(lang)
    if fmt == "csv":
        path = write_csv(repo, languages, sr)
        click.echo(f"CSV written: {path}")
    elif fmt == "jsonld":
        path = write_jsonld(repo, languages, sr)
        click.echo(f"JSON-LD written: {path}")
    else:
        csv_path, jsonld_path = write_all(repo, languages, sr)
        click.echo(f"CSV written: {csv_path}")
        click.echo(f"JSON-LD written: {jsonld_path}")


@main.command("notify-test")
@click.option("--commits", type=int, default=0, help="Simulated commit count")
@click.option("--errors", type=int, default=0, help="Simulated error count")
def notify_test(commits: int, errors: int):
    """Send a test Telegram notification."""
    from .notify import PipelineResult, send_telegram

    result = PipelineResult(
        new_commits=commits,
        laws_checked=42,
        errors=[f"Test error #{i+1}" for i in range(errors)],
        mode="test",
    )
    ok = send_telegram(result)
    if ok:
        click.echo("Telegram notification sent.")
    else:
        click.echo("Failed to send notification — check logs.", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
