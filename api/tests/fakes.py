from pathlib import Path

from app.sources.fetch import FetchError, FetchResult

FEED_FIXTURES = Path(__file__).parent / "fixtures" / "feeds"


def fixture_bytes(name: str) -> bytes:
    return (FEED_FIXTURES / name).read_bytes()


class FakeFetcher:
    """Serves canned responses by exact URL and records every URL requested."""

    def __init__(self, routes: dict[str, FetchResult | FetchError] | None = None) -> None:
        self.routes = routes or {}
        self.requested: list[str] = []

    def serve(self, url: str, body: bytes, *, status: int = 200, content_type: str = "application/rss+xml") -> None:
        self.routes[url] = FetchResult(url=url, status=status, content_type=content_type, body=body)

    def fail(self, url: str, error: FetchError) -> None:
        self.routes[url] = error

    def fetch(self, url: str, *, max_bytes: int | None = None) -> FetchResult:
        self.requested.append(url)
        result = self.routes.get(url)
        if result is None:
            raise FetchError(f"FakeFetcher has no route for {url}")
        if isinstance(result, FetchError):
            raise result
        return result


class ScriptedLLM:
    """Returns queued outputs (or raises queued errors) per schema; records every call for assertions."""

    provider = "scripted"

    def __init__(self) -> None:
        self.queued: dict[str, list] = {}
        self.calls: list[dict] = []

    def queue(self, schema: type | str, *outputs) -> None:
        name = schema if isinstance(schema, str) else schema.__name__
        self.queued.setdefault(name, []).extend(outputs)

    def complete_json(self, *, model, system, user, schema, effort=None, max_tokens=16000, fallbacks=False):
        from app.llm.client import LLMResult, LLMUsage

        self.calls.append(
            {"model": model, "system": system, "user": user, "schema": schema.__name__, "effort": effort, "fallbacks": fallbacks}
        )
        pending = self.queued.get(schema.__name__) or []
        if not pending:
            raise AssertionError(f"ScriptedLLM has no queued output for {schema.__name__}")
        output = pending.pop(0)
        if callable(output) and not isinstance(output, type):
            output = output()  # a callback can probe state mid-call (locks, run status) and return the output
        if isinstance(output, BaseException):
            raise output
        if isinstance(output, dict):  # dynamic schemas (extraction) are queued as plain data
            output = schema.model_validate(output)
        usage = LLMUsage(input_tokens=1200, output_tokens=150, cache_read_tokens=0)
        return LLMResult(output=output, model=model, usage=usage, request_id="req_test")
