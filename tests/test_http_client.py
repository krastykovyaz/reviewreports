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


# ── beyond the basic ranges ───────────────────────────────────────

@pytest.mark.parametrize("addr", ["100.64.0.1", "100.127.255.254", "::ffff:10.0.0.1", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
                                  "fe80::1%eth0", "192.0.2.1", "198.51.100.7", "203.0.113.9", "240.0.0.1", "ff02::1"])
def test_unsafe_address_covers_shared_space_mapped_ipv6_and_documentation_ranges(addr):
    assert _is_unsafe_address(addr)


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_unsafe_address_still_allows_public_addresses_including_mapped_ones(addr):
    assert not _is_unsafe_address(addr)
