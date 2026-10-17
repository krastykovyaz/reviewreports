import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

MAX_BODY_BYTES = 3 * 1024 * 1024  # of any one page / robots.txt / sitemap: the rest of a bigger body is never read
FETCH_DEADLINE_SEC = 30.0         # wall clock for one whole fetch (the per-read timeout alone lets a slow drip run forever)
MAX_REDIRECTS = 10


@dataclass
class FetchResult:
    status_code: int
    headers: Dict[str, str]
    text: str
    url: str
    truncated: bool = False  # the body was longer than MAX_BODY_BYTES and `text` is only its beginning


class SSRFBlocked(Exception):
    """Raised instead of performing a request whose host resolves to a
    non-public address - this service takes a URL from an unauthenticated
    public caller (tsech's "Check site" form) and fetches it server-side,
    so without this a request for e.g. http://127.0.0.1:8001/docs or
    http://169.254.169.254/ (cloud metadata) would be fetched from the
    server's own network position and the result handed back at a public
    report link."""


# GCP's metadata hostname doesn't always resolve to a literal address an
# ipaddress check would catch on every network, so it's blocked by name too;
# its usual IP (169.254.169.254) is link-local and caught below regardless.
_BLOCKED_HOSTNAMES = {"metadata.google.internal"}


def _resolve_all_sync(hostname: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFBlocked(f"Could not resolve host: {hostname}") from exc
    return [info[4][0] for info in infos]


def _is_unsafe_address(raw_ip: str) -> bool:
    ip = ipaddress.ip_address(raw_ip.split("%")[0])  # drop an IPv6 zone id (fe80::1%eth0)
    # An IPv4 address written as IPv6 (::ffff:10.0.0.1) must be judged as the IPv4 address it really is.
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        # Anything not globally routable: also shared address space 100.64.0.0/10 (carrier-grade NAT, which is how some
        # clouds number internal hosts), documentation ranges, 192.0.0.0/24 ...
        or not ip.is_global
    )


async def assert_public_host(hostname: str) -> None:
    """Raise SSRFBlocked unless `hostname` resolves only to public addresses."""
    if hostname in _BLOCKED_HOSTNAMES:
        raise SSRFBlocked(f"Refusing to fetch a blocked host: {hostname}")
    addresses = await asyncio.to_thread(_resolve_all_sync, hostname)
    for addr in addresses:
        if _is_unsafe_address(addr):
            raise SSRFBlocked(f"Refusing to fetch a non-public address: {hostname} -> {addr}")


async def assert_public_url(url: str) -> None:
    """Scheme + host check for any URL a browser or client is about to load."""
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFBlocked(f"Refusing to fetch a non-http(s) URL: {url!r}")
    await assert_public_host(parsed.host)


async def _guard_request(request: httpx.Request) -> None:
    """An httpx `request` event hook: fires before every request the client
    sends, including each hop of a redirect chain - so a public URL that
    redirects to an internal one is caught at the hop that actually points
    there, not just checked against the URL the caller originally gave.

    Resolve-then-check has a known residual gap (DNS rebinding: the same
    hostname could resolve differently between this check and the actual
    connect a moment later) - accepted for now as a large improvement over
    no check at all, rather than building a custom transport that pins the
    resolved IP for the connection."""
    await assert_public_host(request.url.host)


async def open_response(client: httpx.AsyncClient, method: str, url: str, max_redirects: int = MAX_REDIRECTS) -> httpx.Response:
    """Sends the request and follows redirects by hand WITHOUT reading any body; returns the final response with its body
    still unread (headers only) - the caller reads what it wants and must aclose() it.

    httpx's own redirect following reads every intermediate response in full (`await response.aread()`), so a hostile
    server could answer a redirect with an endless body. Here each redirect response is closed unread. `client` must have
    follow_redirects=False; every hop still goes through the client's request hooks (the SSRF guard)."""
    request = client.build_request(method, url)
    for _ in range(max_redirects + 1):
        response = await client.send(request, stream=True)
        if response.next_request is None:
            return response
        request = response.next_request
        await response.aclose()
    raise httpx.TooManyRedirects(f"More than {max_redirects} redirects", request=request)


async def _read_capped(response: httpx.Response, max_bytes: int) -> tuple:
    """(body bytes, truncated): stops reading one byte past `max_bytes` instead of buffering whatever the server sends.
    (Content-Encoding is decoded as it streams, so the cap is on decoded bytes; a hostile gzip can still inflate one
    network chunk - 64 KB - to some tens of MB before the cap is seen, which is bounded, unlike the whole body.)"""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > max_bytes:
            return bytes(body[:max_bytes]), True
    return bytes(body), False


async def _fetch(url: str, timeout: float, max_bytes: int) -> FetchResult:
    # Identity encoding: an honest server then sends the plain bytes the cap counts.
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout, headers={"Accept-Encoding": "identity"}, event_hooks={"request": [_guard_request]}
    ) as client:
        resp = await open_response(client, "GET", url)
        try:
            body, truncated = await _read_capped(resp, max_bytes)
            text = body.decode(resp.encoding or "utf-8", errors="replace")
            return FetchResult(status_code=resp.status_code, headers=dict(resp.headers), text=text, url=str(resp.url), truncated=truncated)
        finally:
            await resp.aclose()


async def fetch(url: str, timeout: float = 15.0, max_bytes: int = MAX_BODY_BYTES, deadline: Optional[float] = None) -> FetchResult:
    """GET `url`, following redirects (each hop SSRF-checked), reading at most `max_bytes` of the body and taking at
    most `deadline` seconds in all (default FETCH_DEADLINE_SEC). Raises httpx.HTTPError (a timeout is an httpx.ReadTimeout)
    or SSRFBlocked."""
    deadline = FETCH_DEADLINE_SEC if deadline is None else deadline
    try:
        return await asyncio.wait_for(_fetch(url, timeout, max_bytes), timeout=deadline)
    except asyncio.TimeoutError:
        raise httpx.ReadTimeout(f"Fetching {url} took longer than {deadline:g} seconds")


async def try_fetch(url: str, timeout: float = 10.0) -> Optional[FetchResult]:
    try:
        return await fetch(url, timeout=timeout)
    except (httpx.HTTPError, SSRFBlocked):
        return None
