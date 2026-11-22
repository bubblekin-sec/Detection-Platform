"""Operator commands. Usage: uv run python -m app.cli <command>"""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import Article, ArticleScore, DetectionRule, PipelineRun, ProfileVersion, Source
from app.detection.dedup import fingerprint
from app.pipeline.priority_report import priority_report
from app.pipeline.run import queue_run
from app.pipeline.scoring import score_articles
from app.profiles.huntable_keywords import load_huntable_priority
from app.profiles.seed import latest_version, seed_profiles
from app.rules.duplicates import recompute_all
from app.rules.reference import best_match, load_reference_rules
from app.rules.sync import SyncRefused, sync_rules
from app.sources.fetch import build_fetcher
from app.sources.ingest import SourceFetchFailed, ingest_source
from app.sources.seed import seed_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-sources", help="Add Huntable seed sources that are missing. Never changes existing rows.")
    fetch = commands.add_parser("fetch", help="Fetch feeds now and store new articles.")
    target = fetch.add_mutually_exclusive_group(required=True)
    target.add_argument("--source", action="append", metavar="SLUG", help="Source slug (repeatable).")
    target.add_argument("--all-active", action="store_true", help="Every active source.")
    commands.add_parser("seed-profiles", help="Add built-in profiles that are missing (detection). Never changes existing ones.")
    queue = commands.add_parser(
        "queue-runs", help="Queue pipeline runs for the newest articles that have none yet. The worker executes them."
    )
    queue.add_argument("--profile", default="detection")
    queue.add_argument("--source", metavar="SLUG", help="Only articles from this source.")
    queue.add_argument("--limit", type=int, default=20, help="At most this many articles (default 20).")
    queue.add_argument("--min-priority", type=float, metavar="SCORE", help="Only articles with at least this priority score (highest first).")
    queue.add_argument("--retry-failed", action="store_true", help="Also re-queue articles whose runs all failed (they cost money again).")
    report = commands.add_parser(
        "priority-report", help="Score every stored article with the Huntable-seeded priority config; writes nothing."
    )
    report.add_argument("--drop", action="append", default=[], metavar="TERM", help="Also drop this term (repeatable).")
    rescore = commands.add_parser("score-articles", help="Store priority scores for the profile's latest version. Never queues runs.")
    rescore.add_argument("--profile", default="detection")
    rescore.add_argument("--missing", action="store_true", help="Only articles with no current score for that version (unscored, or scored by an older scorer).")
    commands.add_parser(
        "dedup-rules", help="Recompute which rules duplicate an earlier one (same log source, near-identical values)."
    )
    sync = commands.add_parser(
        "sync-rules", help="Write approved rules (Sigma, Defender KQL, provenance) into the rules folder. Never commits."
    )
    sync.add_argument("--dir", type=Path, help="Rules folder (default RULES_DIR, ../rules from api/). Must exist.")
    sync.add_argument("--without-notes", action="store_true", help="Leave reviewer notes out of .meta.json (e.g. for a public repo).")
    crossref = commands.add_parser(
        "crossref-rules", help="Report the closest reference-ruleset rule for each platform rule. Writes nothing."
    )
    crossref.add_argument("--dir", type=Path, action="append", help="Reference folder (repeatable; default REFERENCE_RULE_DIRS).")
    crossref.add_argument("--status", choices=["pending_review", "approved", "rejected"], help="Only rules with this status.")
    crossref.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args(argv)

    settings = Settings()
    engine = make_engine(settings)
    exit_code = 0
    try:
        with make_session_factory(engine)() as session:
            if args.command == "seed-sources":
                result = seed_sources(session)
                print(f"Sources added: {result['added']}, already present: {result['existing']}")
                if result["skipped_user_owned"]:
                    print(f"Not added (slug used by a user source): {', '.join(result['skipped_user_owned'])}")
            elif args.command == "fetch":
                exit_code = _fetch(session, settings, args)
            elif args.command == "seed-profiles":
                result = seed_profiles(session)
                print(f"Profiles added: {result['added']}, new versions: {result['upgraded']}, already current: {result['existing']}")
            elif args.command == "queue-runs":
                exit_code = _queue_runs(session, args)
            elif args.command == "score-articles":
                exit_code = _score_articles(session, args)
            elif args.command == "dedup-rules":
                print(f"Rules flagged as duplicates: {recompute_all(session)}")
            elif args.command == "sync-rules":
                exit_code = _sync_rules(session, args.dir or settings.rules_dir, settings.telemetry_inventory,
                                        include_notes=not args.without_notes)
            elif args.command == "crossref-rules":
                exit_code = _crossref_rules(session, args.dir or settings.reference_folders, args.status, args.json)
            elif args.command == "priority-report":
                print(priority_report(session, load_huntable_priority(extra_dropped=set(args.drop))))
    finally:
        engine.dispose()
    return exit_code


def _crossref_rules(session, folders: list[Path], status: str | None, as_json: bool) -> int:
    """Exit 2 when a reference folder is missing: "no overlap" must never come from reading nothing."""
    references = load_reference_rules(folders)
    missing = [path for path, reason in references.skipped if reason == "not a folder"]
    query = select(DetectionRule).order_by(DetectionRule.created_at, DetectionRule.run_id, DetectionRule.position)
    if status:
        query = query.where(DetectionRule.status == status)
    rows = []
    for rule in session.scalars(query):
        match = best_match(fingerprint(rule.sigma_yaml), rule.id, references)
        rows.append({"rule_id": rule.id, "title": rule.title, "status": rule.status,
                     "match": vars(match) if match else None})
    if as_json:
        print(json.dumps({"reference_rules": len(references.rules), "skipped": references.skipped, "rules": rows}, indent=2))
        return 2 if missing else 0
    print(f"{len(references.rules)} reference rules from {', '.join(str(folder) for folder in folders)}; "
          f"{sum(row['match'] is not None for row in rows)} of {len(rows)} platform rules have a close match.")
    for row in rows:
        if row["match"]:
            match = row["match"]
            print(f"  {row['title']} ({row['status']}) ~ {match['path']} \"{match['title']}\" ({match['similarity']:.0%})")
    for path, reason in references.skipped:
        print(f"  skipped {path}: {reason}", file=sys.stderr)
    return 2 if missing else 0


def _sync_rules(session, root: Path, inventory: frozenset[str], *, include_notes: bool) -> int:
    try:
        result = sync_rules(session, root, inventory, include_notes=include_notes)
    except SyncRefused as error:
        print(error, file=sys.stderr)
        return 2
    print(f"Rules folder {root.resolve()}: {len(result.written)} written, {result.unchanged} unchanged, "
          f"{len(result.removed)} removed.")
    for path in result.written:
        print(f"  wrote {path}")
    for path in result.removed:
        print(f"  removed {path}")
    for rule_id in result.left_out:
        print(f"  left out {rule_id}: no longer validates or lost its article reference")
    for conflict in result.conflicts:
        print(f"  CONFLICT {conflict}", file=sys.stderr)
    return 1 if result.conflicts else 0


def _score_articles(session, args) -> int:
    version = latest_version(session, args.profile)
    if version is None or not version.definition.get("priority"):
        print(f"Profile '{args.profile}' is missing or has no priority scoring. Run seed-profiles first.", file=sys.stderr)
        return 2
    scored = score_articles(session, version, missing_only=args.missing)
    print(f"Scored {scored} article(s) for profile '{args.profile}' v{version.version}.")
    return 0


def _queue_runs(session, args) -> int:
    version = latest_version(session, args.profile)
    if version is None:
        print(f"Unknown profile '{args.profile}'. Run seed-profiles first.", file=sys.stderr)
        return 2
    if args.source and session.scalar(select(Source.id).where(Source.slug == args.source)) is None:
        print(f"Unknown source slug: {args.source}", file=sys.stderr)
        return 2
    # Articles never run under ANY version of this profile (a new version, e.g. v2 adding priority, must not re-pay for
    # articles already processed). With --retry-failed, articles whose runs all failed are eligible again.
    already_run = (
        select(PipelineRun.article_id)
        .join(ProfileVersion, ProfileVersion.id == PipelineRun.profile_version_id)
        .where(ProfileVersion.profile_id == version.profile_id)
    )
    if args.retry_failed:
        already_run = already_run.where(PipelineRun.status != "failed")
    query = select(Article.id).where(Article.id.not_in(already_run))
    if args.source:
        query = query.join(Source).where(Source.slug == args.source)
    if args.min_priority is not None:
        query = query.join(ArticleScore, (ArticleScore.article_id == Article.id) & (ArticleScore.profile_version_id == version.id))
        query = query.where(ArticleScore.score >= args.min_priority).order_by(ArticleScore.score.desc(), Article.id)
    else:
        query = query.order_by(Article.published_at.desc().nulls_last(), Article.id.desc())
    query = query.limit(max(1, args.limit))
    article_ids = list(session.scalars(query))
    for article_id in article_ids:
        queue_run(session, article_id, version, trigger="batch")
    print(f"Queued {len(article_ids)} run(s) for profile '{args.profile}' v{version.version}. Start the worker to process them.")
    return 0


def _fetch(session, settings: Settings, args) -> int:
    query = select(Source).order_by(Source.slug)
    query = query.where(Source.active) if args.all_active else query.where(Source.slug.in_(args.source))
    sources = list(session.scalars(query))
    session.expunge_all()  # ingest ends transactions between phases; keep these plain objects usable
    missing = set(args.source or []) - {source.slug for source in sources}
    if missing:
        print(f"Unknown source slug(s): {', '.join(sorted(missing))}", file=sys.stderr)
        return 2
    fetcher = build_fetcher(settings)
    failures = 0
    try:
        for source in sources:
            if not source.rss_url:
                print(f"{source.slug:32} skipped: no RSS feed")
                continue
            try:
                result = ingest_source(session, source, fetcher, settings=settings)
            except SourceFetchFailed as error:
                failures += 1
                print(f"{source.slug:32} FAILED: {error}")
                continue
            except Exception as error:  # keep going: one broken source must not stop --all-active
                session.rollback()
                failures += 1
                print(f"{source.slug:32} ERROR ({type(error).__name__}): {error}")
                continue
            print(
                f"{source.slug:32} new={result.new} duplicates={result.duplicates} too_short={result.too_short} "
                f"filtered={result.filtered} scored={result.scored} auto_queued={result.auto_queued} deferred={result.deferred}"
            )
    finally:
        fetcher.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
