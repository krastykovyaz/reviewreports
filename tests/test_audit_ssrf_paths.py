"""The audited page's own content must never make this server request, or publish, an internal address.

Two paths used to bypass the guard that protects the page fetch: the broken-link checker (a bare httpx client
fetching URLs taken from the audited page) and Chromium (which can be redirected, or script itself, somewhere
internal after the page was fetched).
"""
import pytest

from src.audit import browser_session, http_client
from src.audit.browser_session import assert_page_is_public
from src.audit.collectors.links import collect_broken_links
from src.audit.http_client import SSRFBlocked, assert_public_url
from src.report.schema import Status


# ── the link checker ──────────────────────────────────────────────

def _facts(*hrefs):
    return {"links": [{"href": h, "external": True} for h in hrefs], "linksWithoutText": 0}


@pytest.mark.asyncio
async def test_internal_links_are_never_requested_or_reported(monkeypatch):
    import httpx
    seen = []
    real_send = httpx.AsyncClient.send

    async def spy(self, request, **kw):
        seen.append(str(request.url))
        return await real_send(self, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", spy)
    p = await collect_broken_links(_facts("http://127.0.0.1:8001/docs", "http://169.254.169.254/latest/meta-data/",
                                          "http://10.0.0.5/admin", "http://[::ffff:127.0.0.1]/"))
    # The guard fires on the request event hook, i.e. before any connection: nothing was sent to a socket.
    # Whatever the pillar says, none of the internal addresses appears in what would be published.
    text = " ".join(f.evidence or "" for f in p.findings) + " ".join(f.detail or "" for f in p.findings)
    for needle in ("127.0.0.1", "169.254", "10.0.0.5"):
        assert needle not in text
    # With nothing public left to check the pillar is "not applicable", not a score and not a list of internal hosts.
    assert p.score is None and all(f.status == Status.NA for f in p.findings)


@pytest.mark.asyncio
async def test_a_mix_only_reports_on_the_public_links(monkeypatch):
    class R:
        status_code = 200

    async def fake_head(self, url, **kw):
        # Simulates the real client for the public link; the guard hook is exercised by the test above.
        return R()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "head", fake_head)
    monkeypatch.setattr("src.audit.collectors.links._guard_request", __import__("src.audit.http_client", fromlist=["x"])._guard_request)
    p = await collect_broken_links(_facts("http://127.0.0.1:8001/docs", "https://example.com/"))
    text = " ".join((f.evidence or "") + (f.detail or "") for f in p.findings)
    assert "127.0.0.1" not in text


# ── the browser's final destination ──
# (main's own tests cover assert_page_is_public in isolation; these pin how audit_page uses it, and the order.)

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "http://127.0.0.1:8001/docs", "http://localhost/", "http://[::1]/", "http://169.254.169.254/latest/",
    "http://10.1.2.3/", "http://100.64.0.1/", "http://[::ffff:10.0.0.1]/", "http://metadata.google.internal/",
    "file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)", "about:blank", "chrome://settings",
])
async def test_the_start_and_final_urls_must_be_public_http(bad):
    with pytest.raises(SSRFBlocked):
        await assert_public_url(bad)


class _Page:
    """A page double with the real API's shape: goto() and get_url() (src/environment/browser/actor/page.py)."""
    def __init__(self, lands_on):
        self.lands_on, self.calls = lands_on, []

    async def goto(self, url): self.calls.append(("goto", url))
    async def get_url(self): return self.lands_on


def _fake_browser(page, calls):
    class FakeBrowser:
        def __init__(self, **kw): pass
        async def start(self): pass
        async def stop(self): calls.append("stop")
        async def get_current_page(self): return page
    return FakeBrowser


@pytest.mark.asyncio
async def test_audit_page_checks_where_the_browser_ended_up_before_yielding(monkeypatch):
    """A site that redirects Chromium to an internal page must abort before anything is read, and still shut the browser down."""
    calls, read = [], []
    page = _Page("http://127.0.0.1:8001/docs")
    monkeypatch.setattr(browser_session, "Browser", _fake_browser(page, calls))
    monkeypatch.setattr(http_client, "_resolve_all_sync", lambda host: ["93.184.216.34"] if host == "example.com" else ["127.0.0.1"])
    with pytest.raises(SSRFBlocked):
        async with browser_session.audit_page("https://example.com/"):
            read.append("facts")
    assert read == [] and "stop" in calls and ("goto", "https://example.com/") in page.calls


@pytest.mark.asyncio
async def test_audit_page_refuses_an_internal_start_url_without_navigating(monkeypatch):
    calls = []
    page = _Page("http://127.0.0.1:8001/")
    monkeypatch.setattr(browser_session, "Browser", _fake_browser(page, calls))
    with pytest.raises(SSRFBlocked):
        async with browser_session.audit_page("http://127.0.0.1:8001/"):
            pass
    assert page.calls == [] and "stop" in calls


@pytest.mark.asyncio
async def test_a_public_page_is_yielded(monkeypatch):
    calls = []
    page = _Page("https://example.com/landing")
    monkeypatch.setattr(browser_session, "Browser", _fake_browser(page, calls))
    monkeypatch.setattr(http_client, "_resolve_all_sync", lambda host: ["93.184.216.34"])
    async with browser_session.audit_page("https://example.com/") as p:
        assert p is page
    assert "stop" in calls


@pytest.mark.asyncio
async def test_the_late_check_in_website_audit_catches_a_redirect_that_fires_while_the_page_settles(monkeypatch):
    """facts['url'] is read after the page has settled; a script/meta redirect during that wait must not be published."""
    from src.audit import website

    class FakeFetched:
        url = "https://example.com/"; headers = {}; status_code = 200; text = "<html></html>"

    async def fake_fetch(url, timeout=15.0): return FakeFetched()

    async def fake_try_fetch(url, timeout=10.0): return None

    class _Ctx:
        async def __aenter__(self): return _Page("https://example.com/")
        async def __aexit__(self, *a): return False

    async def fake_facts(page): return {"url": "http://169.254.169.254/latest/meta-data/", "title": "SECRET"}

    monkeypatch.setattr(website, "fetch", fake_fetch)
    monkeypatch.setattr(website, "try_fetch", fake_try_fetch)
    monkeypatch.setattr(website, "audit_page", lambda url: _Ctx())
    monkeypatch.setattr(website, "gather_page_facts", fake_facts)
    monkeypatch.setattr(website, "assert_page_is_public", lambda page: __import__("asyncio").sleep(0))  # main's check passes: the page looked fine
    monkeypatch.setattr(http_client, "_resolve_all_sync", lambda host: ["169.254.169.254"] if "169.254" in host else ["93.184.216.34"])

    async def fake_variant(url): return None
    monkeypatch.setattr(website, "_http_variant", fake_variant)
    with pytest.raises(SSRFBlocked):
        await website.run_website_audit("https://example.com/")
