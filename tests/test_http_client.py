"""This service fetches a URL supplied by an unauthenticated public caller
(tsech's "Check site" form) from the server's own network position. Without
a guard, that URL could point at the server's own internal services
(http://127.0.0.1:8001/docs), a private-network host, or a cloud metadata
endpoint - and the result would be handed back at a public report link.
"""

import pytest

from src.audit.http_client import SSRFBlocked, _is_unsafe_address, fetch, try_fetch


def test_is_unsafe_address_classifies_common_ranges():
    assert _is_unsafe_address("127.0.0.1")  # loopback
    assert _is_unsafe_address("10.0.0.5")  # private
    assert _is_unsafe_address("192.168.1.1")  # private
    assert _is_unsafe_address("172.16.0.1")  # private
    assert _is_unsafe_address("169.254.169.254")  # link-local / cloud metadata
    assert _is_unsafe_address("0.0.0.0")  # unspecified
    assert _is_unsafe_address("::1")  # IPv6 loopback
    assert _is_unsafe_address("fc00::1")  # IPv6 unique local (private)


def test_is_unsafe_address_allows_public_addresses():
    assert not _is_unsafe_address("93.184.216.34")  # example.com's long-stable public IP
    assert not _is_unsafe_address("8.8.8.8")


@pytest.mark.asyncio
async def test_fetch_blocks_loopback_ip_literal_before_connecting():
    # Port 1 - nothing should ever be listening there; if the guard didn't
    # fire first, this would raise a connection error instead, not SSRFBlocked.
    with pytest.raises(SSRFBlocked):
        await fetch("http://127.0.0.1:1/")


@pytest.mark.asyncio
async def test_fetch_blocks_private_ip_literal():
    with pytest.raises(SSRFBlocked):
        await fetch("http://10.255.255.1/")


@pytest.mark.asyncio
async def test_fetch_blocks_cloud_metadata_ip():
    with pytest.raises(SSRFBlocked):
        await fetch("http://169.254.169.254/")


@pytest.mark.asyncio
async def test_fetch_blocks_metadata_hostname_by_name_without_dns():
    # metadata.google.internal only resolves inside GCP - blocked by name so
    # this doesn't depend on (or wait on) DNS resolution failing.
    with pytest.raises(SSRFBlocked):
        await fetch("http://metadata.google.internal/")


@pytest.mark.asyncio
async def test_try_fetch_returns_none_for_a_blocked_host_instead_of_raising():
    # try_fetch is used for the optional robots.txt/sitemap.xml/http-variant
    # fetches, which already tolerate a plain connection failure as None -
    # a blocked host should look the same to those callers, not crash them.
    assert await try_fetch("http://127.0.0.1:1/") is None


# ---- browser-side checks (assert_public_url / assert_page_is_public) ----------

import pytest  # noqa: E402

from src.audit import http_client  # noqa: E402
from src.audit.browser_session import assert_page_is_public  # noqa: E402
from src.audit.http_client import SSRFBlocked, assert_public_url  # noqa: E402


class _FakePage:
    def __init__(self, url):
        self._url = url

    async def get_url(self):
        return self._url


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "gopher://example.com"])
async def test_assert_public_url_rejects_non_http_schemes(url):
    with pytest.raises(SSRFBlocked):
        await assert_public_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1:8010/health", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/admin", "http://[::1]/"])
async def test_assert_public_url_rejects_internal_literals(url):
    with pytest.raises(SSRFBlocked):
        await assert_public_url(url)


@pytest.mark.asyncio
async def test_assert_public_url_allows_a_public_address(monkeypatch):
    monkeypatch.setattr(http_client, "_resolve_all_sync", lambda host: ["93.184.216.34"])
    await assert_public_url("https://example.com/")  # must not raise


@pytest.mark.asyncio
async def test_page_that_redirected_to_an_internal_host_is_rejected():
    # The scenario the pre-flight httpx check cannot see: the browser was sent
    # (by an HTTP/meta/JS redirect) somewhere other than the URL the caller gave.
    with pytest.raises(SSRFBlocked):
        await assert_page_is_public(_FakePage("http://127.0.0.1:8001/docs"))


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["", "about:blank"])
async def test_blank_page_is_rejected(url):
    with pytest.raises(SSRFBlocked):
        await assert_page_is_public(_FakePage(url))


@pytest.mark.asyncio
async def test_public_page_is_accepted(monkeypatch):
    monkeypatch.setattr(http_client, "_resolve_all_sync", lambda host: ["93.184.216.34"])
    await assert_page_is_public(_FakePage("https://example.com/final"))
