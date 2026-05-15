"""CLI interface for the Swiss law pipeline."""
from __future__ import annotations

import logging

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
    """
    total = 0

    if scope in ("federal", "all"):
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

    canton = canton.lower()
    if canton not in ALL_CANTONS:
        click.echo(f"Unknown canton: {canton}. Valid: {', '.join(ALL_CANTONS)}", err=True)
        raise SystemExit(1)

    fetcher = CantonalFetcher(rate_limit=rate_limit)
    repo_path = Path(repo)
    commits = 0

    if number:
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
        load_rollout_state, run_rollout,
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


@main.command("reindex")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--buffer-days", type=int, default=30,
              help="How many days before today to record as `last_run` "
                   "(buffer lets the first incremental update catch any "
                   "consolidations Fedlex added since the snapshot)")
def reindex(repo: str, buffer_days: int):
    """Seed data/pipeline_state.json from markdown frontmatter.

    Used after bootstrapping from an external data snapshot — the
    pipeline learns which (sr_number, version_date) pairs already
    exist on disk so subsequent `legalize-ch update` runs only fetch
    genuinely new versions.
    """
    from pathlib import Path
    from .reindex import reindex as do_reindex

    result = do_reindex(Path(repo), buffer_days=buffer_days)
    click.echo(
        f"Reindexed {result['processed_count']} (sr, version_date) entries "
        f"(skipped {result['skipped']})."
    )
    click.echo(f"  last_run = {result['last_run']}")
    click.echo(f"  written to {result['state_file']}")


@main.command("export")
@click.option("--repo", "-r", default=".", help="Path to the git repo")
@click.option("--output", "-o", default="api/v1/publications",
              help="Output directory (relative to --repo or absolute)")
def export(repo: str, output: str):
    """Export publications as static JSON for GitHub Pages.

    Writes one file per year that has at least one publication
    (including pre-1970 dates from markdown frontmatter), plus
    `index.json` and `today.json`.

    Run after every pipeline run; the JSON is committed to the
    repo so GitHub Pages serves it directly.
    """
    from pathlib import Path
    from .static_export import export_publications

    repo_path = Path(repo).resolve()
    out_path = Path(output)
    if not out_path.is_absolute():
        out_path = repo_path / out_path

    result = export_publications(repo_path, out_path)
    click.echo(
        f"Exported {result['publications']} publications across "
        f"{result['years']} years to {result['output_dir']}"
    )


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
      GET /api/v1/publications?date=YYYY[-MM[-DD]]
      GET /api/v1/publications/today
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
