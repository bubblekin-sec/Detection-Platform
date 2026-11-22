import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.articles.router import router as articles_router
from app.dashboard.router import router as dashboard_router
from app.db import make_engine, make_session_factory
from app.detection.router import router as detection_router
from app.profiles.router import router as profiles_router
from app.rules.router import router as rules_router
from app.runs.router import router as runs_router
from app.sources.fetch import Fetcher, build_fetcher
from app.sources.router import router as sources_router

log = logging.getLogger(__name__)


async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Return every 422 as {"detail": {"errors": ["field: message"]}} so clients handle one shape."""
    errors = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"] if part != "body")
        # Pydantic prefixes messages from our own validators with "Value error, ".
        message = error["msg"].removeprefix("Value error, ")
        errors.append(f"{field}: {message}" if field else message)
    return JSONResponse(status_code=422, content={"detail": {"errors": errors}})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    app.state.engine.dispose()
    close = getattr(app.state.fetcher, "close", None)
    if close:
        close()


def create_app(settings: Settings | None = None, *, fetcher: Fetcher | None = None) -> FastAPI:
    """Build the app. Tests pass a fake `fetcher`; otherwise outbound HTTP uses build_fetcher(settings)."""
    settings = settings or Settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    # Creating the engine does not connect; the first request that needs the database does.
    app.state.settings = settings
    app.state.engine = make_engine(settings)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.fetcher = fetcher or build_fetcher(settings)
    app.add_exception_handler(RequestValidationError, request_validation_error)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness: the process answers. Never touches dependencies, so a database outage doesn't restart it."""
        return {"status": "ok"}

    @app.get("/api/ready", responses={503: {"description": "The database is unreachable."}})
    def ready() -> JSONResponse:
        """Readiness: every other endpoint needs the database. The error never echoes the URL or the driver's text."""
        try:
            with app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            log.warning("Readiness check: the database is unreachable")
            return JSONResponse(status_code=503, content={"detail": {"errors": ["database: unreachable"]}})
        return JSONResponse({"status": "ok"})

    app.include_router(detection_router)
    app.include_router(sources_router)
    app.include_router(articles_router)
    app.include_router(runs_router)
    app.include_router(rules_router)
    app.include_router(profiles_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
