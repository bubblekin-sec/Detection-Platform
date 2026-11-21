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
