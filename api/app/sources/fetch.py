"""Outbound HTTP for feeds and article pages. The only module that fetches user-controlled URLs.

Every hop (including redirects) must resolve only to public addresses, so a source URL cannot be used to
reach the container's network, cloud metadata endpoints, or other internal services (SSRF).
Residual risk: DNS can change between our check and httpx's own lookup (rebinding). Enterprise egress
policy (firewall / proxy allow-list) is the backstop for that.
"""

import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import Settings

Resolver = Callable[[str, int], list[str]]
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class FetchError(Exception):
    """The URL could not be fetched. The message is safe to show to users and store in last_error."""


class BlockedDestination(FetchError):
    """The URL points somewhere the fetcher must never go."""


@dataclass(frozen=True)
class FetchResult:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes


class Fetcher(Protocol):
    def fetch(self, url: str, *, max_bytes: int | None = None) -> FetchResult: ...


def system_resolver(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip in NAT64_PREFIX:  # behind a NAT64 gateway this reaches the embedded IPv4 address
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif int(ip) >> 32 == 0:  # deprecated IPv4-compatible form (::a.b.c.d)
            return False
        elif ip.is_site_local:
            return False
    return ip.is_global and not (ip.is_multicast or ip.is_reserved)


def check_destination(url: str, resolver: Resolver) -> None:
    try:
        parts = urlsplit(url)
        scheme, host, port = parts.scheme, parts.hostname, parts.port
    except ValueError as error:  # e.g. "Invalid IPv6 URL", port out of range
        raise FetchError(f"Malformed URL: {error}") from error
    if scheme not in ("http", "https"):
        raise BlockedDestination(f"Only http and https URLs can be fetched (got '{scheme}').")
    if not host:
        raise BlockedDestination("URL has no host.")
    port = port or (443 if scheme == "https" else 80)
    try:
        addresses = [host] if _looks_like_ip(host) else resolver(host, port)
    except (OSError, UnicodeError) as error:  # UnicodeError: empty or over-long DNS label
        raise FetchError(f"Could not resolve {host}: {error}") from error
    if not addresses or not all(_is_public(address) for address in addresses):
        raise BlockedDestination(f"{host} resolves to a private or reserved address and will not be fetched.")


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class HttpxFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        user_agent: str,
        max_bytes: int,
        max_redirects: int = 5,
        resolver: Resolver = system_resolver,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._resolver = resolver
        self._clock = clock
        # httpx's timeout applies per connect/read; the deadline in fetch() bounds the whole request,
        # so a server that drips bytes slowly cannot hold a fetch open indefinitely.
        # trust_env lets an enterprise HTTPS_PROXY / NO_PROXY apply. Redirects are followed manually so
        # each hop passes check_destination.
        self._client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/html;q=0.9, */*;q=0.5"},
            transport=transport,
            trust_env=True,
        )

    def fetch(self, url: str, *, max_bytes: int | None = None) -> FetchResult:
        """Fetch with redirects, an overall deadline and a size cap. `max_bytes` can only tighten the default."""
        limit = min(max_bytes or self._max_bytes, self._max_bytes)
        deadline = self._clock() + self._timeout_seconds * 2
        for _ in range(self._max_redirects + 1):
            check_destination(url, self._resolver)
            try:
                with self._client.stream("GET", url) as response:
                    if response.status_code in REDIRECT_STATUSES and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        continue
                    return FetchResult(
                        url=url,
                        status=response.status_code,
                        content_type=response.headers.get("content-type", "").split(";")[0].strip(),
                        body=self._read_limited(response, limit, deadline),
                    )
            except (httpx.HTTPError, httpx.InvalidURL) as error:
                raise FetchError(f"Request to {_host_for_message(url)} failed: {error}") from error
        raise FetchError(f"Stopped after {self._max_redirects} redirects.")

    def _read_limited(self, response: httpx.Response, limit: int, deadline: float) -> bytes:
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise FetchError(f"Response is larger than {limit} bytes.")
            if self._clock() > deadline:
                raise FetchError(f"Response took longer than {self._timeout_seconds * 2:.0f} seconds.")
            chunks.append(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        self._client.close()


def build_fetcher(settings: Settings) -> HttpxFetcher:
    return HttpxFetcher(
        timeout_seconds=settings.fetch_timeout_seconds,
        user_agent=settings.fetch_user_agent,
        max_bytes=settings.fetch_max_bytes,
    )


def _host_for_message(url: str) -> str:
    try:
        return urlsplit(url).hostname or url[:100]
    except ValueError:
        return url[:100]
