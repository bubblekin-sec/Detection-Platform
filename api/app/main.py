from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings
from app.detection.router import router as detection_router


async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Return every 422 as {"detail": {"errors": [str]}} so clients handle one shape."""
    errors = [f"{'.'.join(str(part) for part in error['loc'] if part != 'body')}: {error['msg']}" for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": {"errors": errors}})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title=settings.app_name)
    app.add_exception_handler(RequestValidationError, request_validation_error)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(detection_router)
    return app


app = create_app()
