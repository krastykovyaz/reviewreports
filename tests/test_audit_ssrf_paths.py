"""The audited page's own content must never make this server request, or publish, an internal address.

Two paths used to bypass the guard that protects the page fetch: the broken-link checker (a bare httpx client
fetching URLs taken from the audited page) and Chromium (which can be redirected, or script itself, somewhere
internal after the page was fetched).
"""
import pytest

from src.audit import browser_session
from src.audit.browser_session import ensure_public_url
from src.audit.collectors.links import collect_broken_links
from src.audit.http_client import SSRFBlocked
from src.report.schema import Status


def _like_the_real_page(js: str) -> None:
    """The framework's page.evaluate rejects anything that isn't a function expression. A test double that accepts any
    string let a bare `location.href` ship (every website audit then failed with 'JavaScript code must start with
    (...args) => ...'), so the fake enforces the same contract."""
    # Mirrors src/environment/browser/actor/page.py exactly: starts with "(" and contains "=>".
    if not (js.startswith("(") and "=>" in js):
        raise RuntimeError(f"JavaScript code must start with (...args) => format. Got: {js[:40]}...")


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


# ── the browser's final destination ───────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "http://127.0.0.1:8001/docs", "http://localhost/", "http://[::1]/", "http://169.254.169.254/latest/",
    "http://10.1.2.3/", "http://100.64.0.1/", "http://metadata.google.internal/",
    "file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)", "about:blank", "chrome://settings", "", "not a url",
])
async def test_the_final_page_must_be_public_http(bad):
    with pytest.raises(SSRFBlocked):
        await ensure_public_url(bad)


@pytest.mark.asyncio
async def test_a_public_page_passes(monkeypatch):
    monkeypatch.setattr(browser_session, "_resolve_all_sync", lambda host: ["93.184.216.34"])
    await ensure_public_url("https://example.com/some/path?x=1")


@pytest.mark.asyncio
async def test_a_hostname_that_resolves_to_an_internal_address_is_refused(monkeypatch):
    monkeypatch.setattr(browser_session, "_resolve_all_sync", lambda host: ["93.184.216.34", "10.0.0.7"])  # one bad answer is enough
    with pytest.raises(SSRFBlocked):
        await ensure_public_url("https://rebind.example/")


@pytest.mark.asyncio
async def test_audit_page_checks_where_the_browser_ended_up(monkeypatch):
    """A site that redirects Chromium to an internal page must abort the audit before any facts are read."""
    calls = []

    class FakePage:
        async def goto(self, url): calls.append(("goto", url))
        async def evaluate(self, js):
            _like_the_real_page(js)
            return "http://127.0.0.1:8001/docs"  # where the redirect landed

    class FakeBrowser:
        def __init__(self, **kw): pass
        async def start(self): pass
        async def stop(self): calls.append(("stop",))
        async def get_current_page(self): return FakePage()

    monkeypatch.setattr(browser_session, "Browser", FakeBrowser)
    monkeypatch.setattr(browser_session, "_resolve_all_sync", lambda host: ["93.184.216.34"] if host == "example.com" else ["127.0.0.1"])
    with pytest.raises(SSRFBlocked):
        async with browser_session.audit_page("https://example.com/"):
            calls.append(("facts read",))
    assert ("facts read",) not in calls and ("stop",) in calls  # nothing was read, and the browser was still shut down


@pytest.mark.asyncio
async def test_audit_page_refuses_an_internal_start_url_without_navigating(monkeypatch):
    calls = []

    class FakePage:
        async def goto(self, url): calls.append("goto")
        async def evaluate(self, js):
            _like_the_real_page(js)
            return url

    class FakeBrowser:
        def __init__(self, **kw): pass
        async def start(self): pass
        async def stop(self): pass
        async def get_current_page(self): return FakePage()

    monkeypatch.setattr(browser_session, "Browser", FakeBrowser)
    with pytest.raises(SSRFBlocked):
        async with browser_session.audit_page("http://127.0.0.1:8001/"):
            pass
    assert calls == []


@pytest.mark.asyncio
async def test_the_script_audit_page_evaluates_is_a_function_expression():
    """Pins the exact string sent to the real page, which the fakes above validate."""
    seen = []

    class FakePage:
        async def goto(self, url): pass
        async def evaluate(self, js):
            seen.append(js); _like_the_real_page(js); return "https://example.com/"

    class FakeBrowser:
        def __init__(self, **kw): pass
        async def start(self): pass
        async def stop(self): pass
        async def get_current_page(self): return FakePage()

    import src.audit.browser_session as bs
    orig_resolve, orig_browser = bs._resolve_all_sync, bs.Browser
    bs._resolve_all_sync = lambda host: ["93.184.216.34"]; bs.Browser = FakeBrowser
    try:
        async with bs.audit_page("https://example.com/"):
            pass
    finally:
        bs._resolve_all_sync, bs.Browser = orig_resolve, orig_browser
    assert seen and all(s.startswith("() =>") for s in seen)


def test_the_contract_check_itself_rejects_a_bare_expression():
    with pytest.raises(RuntimeError):
        _like_the_real_page("location.href")
    _like_the_real_page("() => location.href")
    _like_the_real_page("(() => { return 1 })()")
