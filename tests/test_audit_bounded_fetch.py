"""A page, robots.txt, sitemap or link target is chosen by whoever submitted the URL: reading it must be bounded in bytes
(a hard cap, then stop) and in time (an overall deadline, not just a per-read timeout), and link checks must not read
bodies at all. Servers here are real httpx MockTransport handlers returning real streamed responses."""
import asyncio

import httpx
import pytest

from src.audit import http_client
from src.audit.collectors import links as links_mod
from src.audit.collectors.links import collect_broken_links
from src.audit.http_client import FetchResult, SSRFBlocked, fetch, try_fetch
from tests.helpers import mock_http

MB = 1024 * 1024


class _Stream(httpx.AsyncByteStream):
    """A response body made of `n` chunks of `size` bytes, counting how many were actually pulled."""
    def __init__(self, n, size=64 * 1024, delay=0.0):
        self.n, self.size, self.delay, self.pulled, self.closed = n, size, delay, 0, False

    async def __aiter__(self):
        for _ in range(self.n):
            if self.delay:
                await asyncio.sleep(self.delay)
            self.pulled += 1
            yield b"x" * self.size

    async def aclose(self):
        self.closed = True


class _Forbidden(httpx.AsyncByteStream):
    """A body that fails the test if anybody reads it."""
    async def __aiter__(self):
        raise AssertionError("a body that must never be read was read")
        yield b""

    async def aclose(self):
        pass


# ── byte cap ──

@pytest.mark.asyncio
async def test_an_oversized_body_is_cut_at_the_cap_and_marked_truncated(monkeypatch):
    body = _Stream(n=400)  # 25 MB on offer
    mock_http(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html"}, stream=body))
    res = await fetch("https://example.com/")
    assert res.truncated is True and len(res.text) == http_client.MAX_BODY_BYTES == 3 * MB
    assert body.pulled * body.size <= http_client.MAX_BODY_BYTES + 2 * body.size  # it stopped asking, it did not read it all and trim
    assert body.closed


@pytest.mark.asyncio
async def test_a_small_page_is_returned_whole_with_status_headers_and_final_url(monkeypatch):
    def handler(req):
        if req.url.path == "/":
            return httpx.Response(301, headers={"location": "/home"}, stream=_Forbidden())  # a redirect's body is never read
        return httpx.Response(200, headers={"content-type": "text/html; charset=latin-1", "x-test": "1"}, content="caf\xe9".encode("latin-1"))

    mock_http(monkeypatch, handler)
    res = await fetch("https://example.com/")
    assert isinstance(res, FetchResult) and res.status_code == 200 and res.text == "caf\xe9" and res.truncated is False
    assert res.url == "https://example.com/home" and res.headers["x-test"] == "1"


@pytest.mark.asyncio
async def test_the_cap_is_a_parameter_for_robots_and_sitemap_callers(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, content=b"y" * 5000))
    res = await fetch("https://example.com/robots.txt", max_bytes=1000)
    assert len(res.text) == 1000 and res.truncated


# ── wall-clock deadline ──

@pytest.mark.asyncio
async def test_a_slow_drip_is_stopped_by_the_overall_deadline(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, stream=_Stream(n=400, size=10, delay=0.05)))  # every read is "fast": 20 s in all
    with pytest.raises(httpx.HTTPError):
        await asyncio.wait_for(fetch("https://example.com/", deadline=0.3), timeout=5)


@pytest.mark.asyncio
async def test_try_fetch_treats_the_deadline_like_any_failed_optional_fetch(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, stream=_Stream(n=400, size=10, delay=0.05)))
    monkeypatch.setattr(http_client, "FETCH_DEADLINE_SEC", 0.2)
    assert await asyncio.wait_for(try_fetch("https://example.com/robots.txt"), timeout=5) is None


# ── redirects ──

@pytest.mark.asyncio
async def test_a_redirect_loop_ends_in_an_http_error(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(302, headers={"location": "/again"}, stream=_Forbidden()))
    with pytest.raises(httpx.TooManyRedirects):
        await fetch("https://example.com/")


@pytest.mark.asyncio
async def test_a_redirect_to_an_internal_host_is_blocked_at_that_hop(monkeypatch):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": "http://10.0.0.5/admin"}, stream=_Forbidden())

    mock_http(monkeypatch, handler)
    with pytest.raises(SSRFBlocked):
        await fetch("https://example.com/")
    assert seen == ["https://example.com/"]  # the internal URL never reached the transport


# ── link checks never read a body ──

def _links(*hrefs):
    return {"links": [{"href": h, "external": True} for h in hrefs], "linksWithoutText": 0}


@pytest.mark.asyncio
async def test_link_checks_read_no_bodies_and_follow_redirects_unread(monkeypatch):
    def handler(req):
        path = req.url.path
        if path == "/big":
            return httpx.Response(200, stream=_Forbidden())                       # a multi-GB download
        if path == "/moved":
            return httpx.Response(301, headers={"location": "/big"}, stream=_Forbidden())
        if path == "/nohead":
            return httpx.Response(405 if req.method == "HEAD" else 200, stream=_Forbidden())
        return httpx.Response(404, stream=_Forbidden())

    mock_http(monkeypatch, handler)
    p = await collect_broken_links(_links("https://a.example/big", "https://a.example/moved", "https://a.example/nohead", "https://a.example/gone"))
    broken = {f.check: f for f in p.findings}["Broken links"]
    assert "gone → 404" in broken.evidence and "big" not in broken.evidence and "moved" not in broken.evidence and "nohead" not in broken.evidence
    assert p.score == 7.5  # three of four resolve


@pytest.mark.asyncio
async def test_link_checks_use_head_first_and_only_then_a_get(monkeypatch):
    methods = []

    def handler(req):
        methods.append((req.url.path, req.method))
        return httpx.Response(200 if req.method == "HEAD" or req.url.path == "/nohead" else 404, stream=_Forbidden())

    mock_http(monkeypatch, handler)
    await collect_broken_links(_links("https://a.example/ok"))
    assert methods == [("/ok", "HEAD")]
    methods.clear()
    mock_http(monkeypatch, lambda req: (methods.append((req.url.path, req.method)), httpx.Response(405 if req.method == "HEAD" else 200, stream=_Forbidden()))[1])
    await collect_broken_links(_links("https://a.example/nohead"))
    assert methods == [("/nohead", "HEAD"), ("/nohead", "GET")]


@pytest.mark.asyncio
async def test_a_link_that_never_answers_is_reported_broken_after_its_deadline(monkeypatch):
    async def handler(req):
        await asyncio.sleep(5)
        return httpx.Response(200)

    mock_http(monkeypatch, handler)
    monkeypatch.setattr(links_mod, "_CHECK_DEADLINE_SEC", 0.2)
    p = await asyncio.wait_for(collect_broken_links(_links("https://slow.example/")), timeout=4)
    assert "slow.example/ → Timeout" in {f.check: f for f in p.findings}["Broken links"].evidence


@pytest.mark.asyncio
async def test_a_link_redirecting_inward_is_neither_followed_nor_reported(monkeypatch):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"}, stream=_Forbidden())

    mock_http(monkeypatch, handler)
    p = await collect_broken_links(_links("https://a.example/x"))
    assert seen == ["https://a.example/x"]
    assert "169.254" not in " ".join((f.evidence or "") + (f.detail or "") for f in p.findings)
