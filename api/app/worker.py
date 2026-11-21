"""Pipeline worker. Usage: uv run python -m app.worker [--once]

Runs separately from the API (on Azure: a second Container App with no ingress). It is the only process
that builds an LLM client, so the API stays usable without credentials.
"""

import argparse
import logging
import signal
import sys
import time

from pydantic import ValidationError

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.llm.factory import LLMNotConfigured, build_llm_client
from app.pipeline.run import process_next_run

log = logging.getLogger("app.worker")


def _raise_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="Process queued runs until the queue is empty, then exit.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        settings = Settings()
    except ValidationError as error:
        fields = ", ".join(".".join(str(part) for part in issue["loc"]) for issue in error.errors())
        print(f"Worker cannot start: invalid settings ({fields}). Check api/.env.", file=sys.stderr)
        return 2
    try:
        llm = build_llm_client(settings)
    except LLMNotConfigured as error:
        print(f"Worker cannot start: {error}", file=sys.stderr)
        return 2
    # Azure stops revisions with SIGTERM: turn it into KeyboardInterrupt so an in-flight run is marked failed.
    signal.signal(signal.SIGTERM, _raise_interrupt)

    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    log.info("Worker started (provider=%s, relevance=%s)", llm.provider, settings.relevance_model)
    try:
        while True:
            run_id = process_next_run(session_factory, llm, settings)
            if run_id is not None:
                log.info("Run %s processed", run_id)
                continue
            if args.once:
                break
            time.sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        log.info("Worker stopping")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
