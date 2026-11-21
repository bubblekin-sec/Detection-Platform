"""The fetcher is the only code that makes outbound requests to user-controlled URLs, so it owns the SSRF guard."""

import httpx
import pytest

from app.sources.fetch import BlockedDestination, FetchError, HttpxFetcher

PUBLIC_IP = "93.184.215.14"


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int) -> list[str]:
        return mapping[host]

    return resolve


def fetcher(handler, *, mapping=None, max_bytes=1_000_000, max_redirects=5) -> HttpxFetcher:
    return HttpxFetcher(
        timeout_seconds=5,
        user_agent="test-agent",
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        resolver=resolver_for(mapping or {"feeds.example.com": [PUBLIC_IP]}),
        transport=httpx.MockTransport(handler),
    )


def test_fetches_a_public_url_with_the_configured_user_agent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, content=b"<rss/>", headers={"content-type": "application/rss+xml"})

    result = fetcher(handler).fetch("https://feeds.example.com/rss")

    assert (result.status, result.body, result.content_type) == (200, b"<rss/>", "application/rss+xml")
    assert result.url == "https://feeds.example.com/rss"
    assert seen["ua"] == "test-agent"


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "172.16.0.5", "192.168.1.10", "169.254.169.254", "::1", "fd00::1", "0.0.0.0", "100.64.0.1"],
)
def test_refuses_hosts_that_resolve_to_non_public_addresses(address):
    def handler(request):
        raise AssertionError("must not send a request")

    with pytest.raises(BlockedDestination):
        fetcher(handler, mapping={"internal.example.com": [address]}).fetch("http://internal.example.com/feed")


def test_refuses_when_any_resolved_address_is_private():
    with pytest.raises(BlockedDestination):
        fetcher(lambda r: httpx.Response(200), mapping={"mixed.example.com": [PUBLIC_IP, "10.0.0.1"]}).fetch(
            "https://mixed.example.com/"
        )


@pytest.mark.parametrize("url", ["http://127.0.0.1/feed", "http://[::1]/feed", "http://169.254.169.254/latest/meta-data/"])
def test_refuses_literal_private_ip_urls(url):
    with pytest.raises(BlockedDestination):
        fetcher(lambda r: httpx.Response(200), mapping={}).fetch(url)


@pytest.mark.parametrize("url", ["ftp://feeds.example.com/x", "file:///etc/passwd", "gopher://feeds.example.com/"])
def test_refuses_non_http_schemes(url):
    with pytest.raises(BlockedDestination):
        fetcher(lambda r: httpx.Response(200)).fetch(url)


def test_follows_redirects_and_rechecks_each_hop():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://feeds.example.com/new"})
        return httpx.Response(200, content=b"ok")

    result = fetcher(handler).fetch("https://feeds.example.com/old")

    assert (result.url, result.body) == ("https://feeds.example.com/new", b"ok")


def test_refuses_a_redirect_to_an_internal_address():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    with pytest.raises(BlockedDestination):
        fetcher(handler).fetch("https://feeds.example.com/feed")


def test_stops_after_too_many_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://feeds.example.com/loop"})

    with pytest.raises(FetchError, match="redirect"):
        fetcher(handler, max_redirects=3).fetch("https://feeds.example.com/loop")


def test_refuses_oversized_bodies():
    with pytest.raises(FetchError, match="larger than"):
        fetcher(lambda r: httpx.Response(200, content=b"x" * 2048), max_bytes=1024).fetch("https://feeds.example.com/big")


def test_reports_http_errors_and_network_failures():
    assert fetcher(lambda r: httpx.Response(404)).fetch("https://feeds.example.com/missing").status == 404

    def boom(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(FetchError, match="timed out"):
        fetcher(boom).fetch("https://feeds.example.com/slow")


def test_unresolvable_host_is_a_fetch_error():
    def resolve(host, port):
        raise OSError("Name or service not known")

    f = HttpxFetcher(timeout_seconds=5, user_agent="t", max_bytes=1000, resolver=resolve, transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    with pytest.raises(FetchError, match="resolve"):
        f.fetch("https://nowhere.example.com/")


@pytest.mark.parametrize("address", ["::ffff:10.0.0.1", "fe80::1%eth0", "224.0.0.1", "::", "64:ff9b::a9fe:a9fe", "fec0::1", "::7f00:1"])
def test_refuses_mapped_scoped_multicast_nat64_and_legacy_ipv6(address):
    with pytest.raises(BlockedDestination):
        fetcher(lambda r: httpx.Response(200), mapping={"odd.example.com": [address]}).fetch("https://odd.example.com/")


@pytest.mark.parametrize("url", ["https://feeds.example.com:99999/", "http://[broken/x", "https://a..b.example.com/"])
def test_malformed_urls_are_fetch_errors_not_crashes(url):
    def resolve(host, port):
        if ".." in host:
            raise UnicodeError("label empty or too long")
        return [PUBLIC_IP]

    f = HttpxFetcher(timeout_seconds=5, user_agent="t", max_bytes=1000, resolver=resolve, transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    with pytest.raises(FetchError):
        f.fetch(url)


def test_size_cap_stops_reading_a_stream_early():
    chunks_sent = []

    def body():
        for _ in range(100):
            chunks_sent.append(1)
            yield b"x" * 512

    with pytest.raises(FetchError, match="larger than"):
        fetcher(lambda r: httpx.Response(200, content=body()), max_bytes=2048).fetch("https://feeds.example.com/big")

    assert len(chunks_sent) < 10


def test_total_time_limit_covers_slow_drip_bodies():
    now = [0.0]

    def body():
        for _ in range(10):
            now[0] += 14  # each chunk arrives just inside a per-read timeout
            yield b"x"

    f = HttpxFetcher(
        timeout_seconds=15,
        user_agent="t",
        max_bytes=1_000_000,
        resolver=resolver_for({"feeds.example.com": [PUBLIC_IP]}),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body())),
        clock=lambda: now[0],
    )

    with pytest.raises(FetchError, match="longer than"):
        f.fetch("https://feeds.example.com/slow")


def test_per_call_size_limit_is_tighter_than_the_default():
    with pytest.raises(FetchError, match="larger than 100"):
        fetcher(lambda r: httpx.Response(200, content=b"x" * 500)).fetch("https://feeds.example.com/page", max_bytes=100)
