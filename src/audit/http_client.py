import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx


@dataclass
class FetchResult:
    status_code: int
    headers: Dict[str, str]
    text: str
    url: str


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
    ip = ipaddress.ip_address(raw_ip)
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


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
    hostname = request.url.host
    if hostname in _BLOCKED_HOSTNAMES:
        raise SSRFBlocked(f"Refusing to fetch a blocked host: {hostname}")
    addresses = await asyncio.to_thread(_resolve_all_sync, hostname)
    for addr in addresses:
        if _is_unsafe_address(addr):
            raise SSRFBlocked(f"Refusing to fetch a non-public address: {hostname} -> {addr}")


async def fetch(url: str, timeout: float = 15.0) -> FetchResult:
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, event_hooks={"request": [_guard_request]}
    ) as client:
        resp = await client.get(url)
        return FetchResult(status_code=resp.status_code, headers=dict(resp.headers), text=resp.text, url=str(resp.url))


async def try_fetch(url: str, timeout: float = 10.0) -> Optional[FetchResult]:
    try:
        return await fetch(url, timeout=timeout)
    except (httpx.HTTPError, SSRFBlocked):
        return None
