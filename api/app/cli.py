"""Operator commands. Usage: uv run python -m app.cli <command>"""

import argparse
import sys

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.sources.seed import seed_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-sources", help="Add Huntable seed sources that are missing. Never changes existing rows.")
    args = parser.parse_args(argv)

    engine = make_engine(Settings())
    try:
        with make_session_factory(engine)() as session:
            if args.command == "seed-sources":
                result = seed_sources(session)
                print(f"Sources added: {result['added']}, already present: {result['existing']}")
                if result["skipped_user_owned"]:
                    print(f"Not added (slug used by a user source): {', '.join(result['skipped_user_owned'])}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
