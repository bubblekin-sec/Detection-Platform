"""Operator commands. Usage: uv run python -m app.cli <command>"""

import argparse
import sys

from sqlalchemy import select

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import Source
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
    finally:
        engine.dispose()
    return exit_code


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
                result = ingest_source(session, source, fetcher)
            except SourceFetchFailed as error:
                failures += 1
                print(f"{source.slug:32} FAILED: {error}")
                continue
            except Exception as error:  # keep going: one broken source must not stop --all-active
                session.rollback()
                failures += 1
                print(f"{source.slug:32} ERROR ({type(error).__name__}): {error}")
                continue
            print(f"{source.slug:32} new={result.new} duplicates={result.duplicates} too_short={result.too_short} filtered={result.filtered}")
    finally:
        fetcher.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
