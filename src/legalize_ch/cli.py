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
@click.option("--scope", type=click.Choice(["federal", "cantonal", "all"]),
              default="federal", help="Scope: federal, cantonal, or all (default: federal)")
@click.option("--canton", "-c", multiple=True, default=None,
              help="Canton(s) to process when scope includes cantonal (default: all 26)")
def bootstrap(repo: str, limit: int | None, lang: tuple, sr: str | None, rate_limit: float,
              latest_only: bool, no_chronological: bool, scope: str, canton: tuple):
    """Full pipeline: fetch all laws and commit to git.

    By default, all revisions are sorted by date before committing so that
    the git history reflects the actual legal timeline (chronological order).
    Use --no-chronological to revert to the old behavior (grouped by law).

    Use --scope to control what is fetched:
      --scope federal   (default) Only federal laws from Fedlex
      --scope cantonal  Only cantonal laws from LexWork/LexFind
      --scope all       Both federal and cantonal laws
    """
    total = 0

    if scope in ("federal", "all"):
        pipeline = Pipeline(repo_path=repo, rate_limit=rate_limit)
        federal_total = pipeline.run(limit=limit, languages=list(lang), sr_filter=sr,
                                     latest_only=latest_only,
                                     chronological=not no_chronological)
        total += federal_total
        click.echo(f"Federal: {federal_total} commits created.")

    if scope in ("cantonal", "all"):
        from .cantonal_pipeline import CantonalPipeline
        cantons_list = list(canton) if canton else None
        cantonal_pipe = CantonalPipeline(repo_path=repo, rate_limit=rate_limit)
        cantonal_total = cantonal_pipe.run(
            cantons=cantons_list, languages=list(lang), limit=limit,
        )
        total += cantonal_total
        click.echo(f"Cantonal: {cantonal_total} commits created.")

    click.echo(f"Done. {total} total commits created.")


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
@click.option("--scope", type=click.Choice(["federal", "cantonal", "all"]),
              default="federal", help="Scope: federal, cantonal, or all (default: federal)")
@click.option("--canton", "-c", multiple=True, default=None,
              help="Canton(s) to update when scope includes cantonal (default: all 26)")
def update(repo: str, limit: int | None, lang: tuple, sr: str | None, rate_limit: float,
           since, no_chronological: bool, scope: str, canton: tuple):
    """Incremental update: only fetch laws with new consolidation versions.

    Detects new versions by comparing Fedlex consolidation dates against
    the pipeline state. Only versions with dateApplicability >= since are
    fetched, and already-processed versions are skipped automatically.

    By default uses last_run from pipeline state. Use --since to override.
    Commits are sorted chronologically by default.

    Use --scope to control what is updated:
      --scope federal   (default) Only federal laws from Fedlex
      --scope cantonal  Only cantonal laws (re-scans catalogs, skips known)
      --scope all       Both federal and cantonal laws
    """
    total = 0

    if scope in ("federal", "all"):
        from datetime import date as date_type
        pipeline = Pipeline(repo_path=repo, rate_limit=rate_limit)
        since_date = since.date() if since else None
        federal_total = pipeline.update(limit=limit, languages=list(lang), sr_filter=sr,
                                        since_override=since_date,
                                        chronological=not no_chronological)
        total += federal_total
        click.echo(f"Federal: {federal_total} commits created.")

    if scope in ("cantonal", "all"):
        from .cantonal_pipeline import CantonalPipeline
        cantons_list = list(canton) if canton else None
        cantonal_pipe = CantonalPipeline(repo_path=repo, rate_limit=rate_limit)
        cantonal_total = cantonal_pipe.update(
            cantons=cantons_list, languages=list(lang), limit=limit,
        )
        total += cantonal_total
        click.echo(f"Cantonal: {cantonal_total} commits created.")

    click.echo(f"Done. {total} total commits created.")


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


@main.command("cantonal-rollout")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--batch-size", "-b", type=int, default=3,
              help="Number of cantons to process per batch (default: 3)")
@click.option("--limit", "-n", type=int, default=None,
              help="Max laws per canton (None = all)")
@click.option("--lang", "-l", multiple=True, default=["de"], help="Languages to fetch")
@click.option("--rate-limit", type=float, default=1.0, help="Seconds between requests")
@click.option("--dry-run", is_flag=True, help="Show what would be done without fetching")
@click.option("--status", "show_status", is_flag=True, help="Show rollout progress and exit")
@click.option("--reset", type=str, default=None,
              help="Reset a failed canton to pending (canton abbreviation)")
def cantonal_rollout(repo: str, batch_size: int, limit: int | None, lang: tuple,
                     rate_limit: float, dry_run: bool, show_status: bool, reset: str | None):
    """Incrementally roll out cantonal law fetching, prioritized by data availability.

    Cantons are processed in priority tiers:
      Tier 1: Dedicated API (ZH) — best data quality
      Tier 2: LexWork API (14 cantons) — direct JSON access
      Tier 3: LexFind fallback (11 cantons) — less structured

    Each invocation processes the next --batch-size cantons. State is persisted
    between runs, so you can call this repeatedly (e.g. via cron) to gradually
    roll out all cantons.

    Examples:
      legalize-ch cantonal-rollout --status           # Check progress
      legalize-ch cantonal-rollout --batch-size 5     # Process next 5
      legalize-ch cantonal-rollout --dry-run          # Preview next batch
      legalize-ch cantonal-rollout --reset ge         # Retry failed canton
    """
    from .canton_rollout import (
        load_rollout_state, save_rollout_state, run_rollout,
        reset_canton, get_tier, tier_label, ROLLOUT_ORDER,
    )

    if reset:
        reset_canton(repo, reset.lower())
        click.echo(f"Reset {reset.upper()} to pending.")
        return

    if show_status:
        state = load_rollout_state(repo)
        summary = state.summary()
        click.echo(f"Canton Rollout Progress: {summary['completed']}/{summary['total_cantons']} "
                   f"({summary['progress_pct']}%)")
        click.echo(f"  Total laws fetched: {summary['total_laws_fetched']}")
        click.echo(f"\n  Completed ({summary['completed']}): "
                   f"{', '.join(c.upper() for c in summary['completed_list']) or 'none'}")
        if summary['in_progress_list']:
            click.echo(f"  In progress: {', '.join(c.upper() for c in summary['in_progress_list'])}")
        if summary['failed_list']:
            click.echo(f"  Failed: {', '.join(c.upper() for c in summary['failed_list'])}")
        if summary['next_up']:
            click.echo(f"  Next up: {', '.join(c.upper() for c in summary['next_up'])}")

        click.echo(f"\nPriority order ({len(ROLLOUT_ORDER)} cantons):")
        for canton in ROLLOUT_ORDER:
            tier = get_tier(canton)
            status = state.get_status(canton)
            marker = {"completed": "[x]", "in_progress": "[~]", "failed": "[!]"}.get(status, "[ ]")
            click.echo(f"  {marker} {canton.upper():3s}  Tier {tier} ({tier_label(tier)}) — {status}")
        return

    result = run_rollout(
        repo_path=repo,
        batch_size=batch_size,
        limit_per_canton=limit,
        languages=list(lang),
        rate_limit=rate_limit,
        dry_run=dry_run,
    )

    if dry_run:
        click.echo("Dry run — next batch would be:")
        for canton, info in result.get("results", {}).items():
            click.echo(f"  {canton.upper():3s}  Tier {info['tier']} ({info['tier_label']}) "
                       f"[currently: {info['status']}]")
        return

    if result.get("status") == "all_complete":
        click.echo("All 26 cantons have been rolled out!")
        return

    click.echo(f"Batch complete: {', '.join(c.upper() for c in result['batch'])}")
    click.echo(f"Total commits this batch: {result['total_commits']}")
    for canton, info in result.get("results", {}).items():
        status = info["status"]
        if status == "completed":
            click.echo(f"  {canton.upper()}: {info['commits']} commits")
        else:
            click.echo(f"  {canton.upper()}: FAILED — {info.get('error', 'unknown')}")

    summary = result.get("summary", {})
    if summary:
        click.echo(f"\nOverall: {summary['completed']}/{summary['total_cantons']} cantons "
                   f"({summary['progress_pct']}%)")


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
@click.option("--json/--no-json", "write_json", default=True,
              help="Also write docs/laws.json for GitHub Pages (default: yes)")
def index(repo: str, lang: str, write_json: bool):
    """Generate INDEX.md and docs/laws.json (federal + cantonal)."""
    from .index_generator import write_index, write_laws_json

    out = write_index(repo_path=repo, lang=lang)
    click.echo(f"Generated: {out}")
    if write_json:
        json_out = write_laws_json(repo_path=repo, lang=lang)
        click.echo(f"Generated: {json_out}")


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


@main.command("feed")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--output", "-o", default=None, help="Output directory (default: docs/feeds/)")
@click.option("--sr", type=str, default=None, help="SR number prefix filter")
@click.option("--lang", "-l", default=None, help="Language filter (de/fr/it/en)")
@click.option("--limit", "-n", type=int, default=50, help="Max entries per feed (default: 50)")
@click.option("--since-days", type=int, default=90,
              help="Look back this many days (default: 90)")
def feed(repo: str, output: str | None, sr: str | None, lang: str | None,
         limit: int, since_days: int):
    """Generate RSS and Atom feeds of law changes (diffs).

    Creates feeds that allow subscribing to changes in specific laws.
    Feeds include unified diffs showing what changed in each revision.

    Filter by SR number prefix to track specific areas of law:
      legalize-ch feed --sr 210    # Track civil code changes
      legalize-ch feed --sr 311    # Track criminal code changes
      legalize-ch feed --lang de   # German changes only
    """
    from .rss_feed import write_feeds

    rss_path, atom_path = write_feeds(
        repo_path=repo,
        output_dir=output,
        sr_filter=sr,
        lang=lang,
        limit=limit,
        since_days=since_days,
    )
    click.echo(f"RSS feed:  {rss_path}")
    click.echo(f"Atom feed: {atom_path}")


@main.command("serve")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
@click.option("--port", "-p", type=int, default=8000, help="Bind port (default: 8000)")
@click.option("--reload", "do_reload", is_flag=True, help="Enable auto-reload for development")
def serve(repo: str, host: str, port: int, do_reload: bool):
    """Start the REST API server for querying law texts.

    Provides endpoints:
      GET /api/v1/laws/{sr_number}?lang=de&date=YYYY-MM-DD
      GET /api/v1/laws/{sr_number}/versions?lang=de
      GET /api/v1/search?q=...&lang=de
      GET /api/v1/health
    """
    import uvicorn
    from .api import create_app

    create_app(repo_path=repo)
    uvicorn.run(
        "legalize_ch.api:app",
        host=host,
        port=port,
        reload=do_reload,
    )


if __name__ == "__main__":
    main()
