from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings
from app.articles.router import router as articles_router
from app.db import make_engine, make_session_factory
from app.detection.router import router as detection_router
from app.sources.fetch import Fetcher, build_fetcher
from app.sources.router import router as sources_router


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
    app.state.engine = make_engine(settings)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.fetcher = fetcher or build_fetcher(settings)
    app.add_exception_handler(RequestValidationError, request_validation_error)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(detection_router)
    app.include_router(sources_router)
    app.include_router(articles_router)
    return app


app = create_app()
