"""Chromium's own requests are filtered, not just the URL handed to goto(): subresources, iframes, form posts and the hops
of a redirect are each a paused request that is continued or failed (BlockedByClient) according to the SSRF rules.

The session half runs the real BrowserSession code on a real cdp_use CDPClient (real event registry, real command
wrappers) whose websocket send is replaced by a recorder, and feeds it events shaped like Chrome's Fetch.requestPaused."""
import asyncio

import pytest
from cdp_use import CDPClient

from src.audit import browser_session, http_client
from src.audit.http_client import SSRFBlocked, browser_request_allowed
from src.environment.browser import Browser
from src.environment.browser.browser.profile import BrowserProfile
from src.environment.browser.browser.session import CDPSession

PUBLIC = "93.184.216.34"


@pytest.fixture(autouse=True)
def _fresh_dns_cache():
    http_client._dns_cache.clear()
    http_client._dns_inflight.clear()
    yield
    http_client._dns_cache.clear()


def _is_literal(host):
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _dns(monkeypatch, table):
    """host -> list of addresses, or an exception to raise (as _resolve_all_sync does for an unknown name)."""
    calls = []

    def resolve(host):
        calls.append(host)
        answer = table.get(host, [host] if _is_literal(host) else [PUBLIC])  # literals resolve to themselves, as in getaddrinfo
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(http_client, "_resolve_all_sync", resolve)
    return calls


# ── the decision ──

@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://example.com/a.js", "http://example.com:8080/x?y=1", "data:text/html;base64,PGI+eDwvYj4=", "blob:https://example.com/3f2a", "about:blank",
    "HTTPS://Example.COM/",
])
async def test_ordinary_public_requests_are_allowed(monkeypatch, url):
    _dns(monkeypatch, {})
    assert await browser_request_allowed(url) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/x", "chrome://settings", "chrome-extension://abc/x.js", "javascript:alert(1)", "gopher://example.com/", "ws://example.com/",
    "", "http://", "not a url at all", "http://[::1",
])
async def test_other_schemes_and_junk_are_refused_and_never_raise(monkeypatch, url):
    _dns(monkeypatch, {})
    assert await browser_request_allowed(url) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8001/docs", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/", "http://192.168.1.1/", "http://100.64.0.1/",
    "http://[::1]/", "http://[::ffff:10.0.0.1]/", "http://[fe80::1]/", "http://localhost/", "http://metadata.google.internal/",
    "http://2130706433/", "http://0x7f.1/", "http://127.1/", "http://017700000001/",
])
async def test_internal_addresses_in_any_spelling_are_refused(url):
    # the real resolver here: literals resolve to themselves, no network involved
    assert await browser_request_allowed(url) is False


@pytest.mark.asyncio
async def test_a_name_that_resolves_to_any_internal_address_is_refused(monkeypatch):
    _dns(monkeypatch, {"rebind.example": [PUBLIC, "10.0.0.7"], "internal.example": ["127.0.0.1"], "v6.example": ["fd00::1"]})
    for host in ("rebind.example", "internal.example", "v6.example"):
        assert await browser_request_allowed(f"https://{host}/x") is False
    assert await browser_request_allowed("https://fine.example/x") is True


@pytest.mark.asyncio
async def test_dns_failure_is_open_for_public_looking_names_and_closed_for_address_shaped_hosts(monkeypatch):
    nx = SSRFBlocked("Could not resolve host")
    _dns(monkeypatch, {"nope.example": nx, "0x7f.1": nx, "2130706433": nx, "1.2.3": nx, "fe80::1": nx})
    assert await browser_request_allowed("https://nope.example/") is True      # it will fail on its own, nothing to protect
    for url in ("http://0x7f.1/", "http://2130706433/", "http://1.2.3/", "http://[fe80::1]/"):
        assert await browser_request_allowed(url) is False, url


@pytest.mark.asyncio
async def test_a_hung_resolver_does_not_hang_the_request(monkeypatch):
    import time
    monkeypatch.setattr(http_client, "_DNS_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(http_client, "_resolve_all_sync", lambda host: time.sleep(0.5) or [PUBLIC])
    t0 = asyncio.get_event_loop().time()
    assert await browser_request_allowed("https://slow.example/") is True   # treated like an unresolvable public name
    assert asyncio.get_event_loop().time() - t0 < 0.4


@pytest.mark.asyncio
async def test_lookups_are_cached_for_a_short_time_and_concurrent_ones_are_shared(monkeypatch):
    calls = _dns(monkeypatch, {})
    results = await asyncio.gather(*(browser_request_allowed(f"https://cdn.example/{i}.js") for i in range(20)))
    assert all(results) and calls == ["cdn.example"]
    assert await browser_request_allowed("https://cdn.example/again") is True and calls == ["cdn.example"]
    monkeypatch.setattr(http_client, "_DNS_TTL_SEC", 0.0)
    http_client._dns_cache.clear()
    await browser_request_allowed("https://cdn.example/x"); await browser_request_allowed("https://cdn.example/y")
    assert calls == ["cdn.example"] * 3  # expired entries are looked up again


# ── the session: every paused request gets an answer ──

def _paused(request_id, url, rtype="Script"):
    # the shape of a Fetch.requestPaused event (cdp_use.cdp.fetch.events.RequestPausedEvent)
    return {"requestId": request_id, "request": {"url": url, "method": "GET", "headers": {}, "initialPriority": "High", "referrerPolicy": "strict-origin-when-cross-origin"},
            "frameId": "F1", "resourceType": rtype}


class _Wire:
    """A real CDPClient with its websocket send replaced by a recorder; events go through its real registry."""
    def __init__(self, fail_enable=False):
        self.client = CDPClient("ws://unused")
        self.sent = []
        self.fail_enable = fail_enable

        async def send_raw(method, params=None, session_id=None):
            self.sent.append((method, params, session_id))
            if self.fail_enable and method == "Fetch.enable":
                raise RuntimeError("Fetch.enable: target closed")
            return {}

        self.client.send_raw = send_raw

    def answers(self):
        return [(m, p["requestId"], s) for m, p, s in self.sent if m in ("Fetch.continueRequest", "Fetch.failRequest")]

    async def settle(self):
        for _ in range(50):
            await asyncio.sleep(0.01)


def _session(wire, guard):
    browser = Browser(browser_profile=BrowserProfile(), headless=True, request_guard=guard)
    browser._cdp_client_root = wire.client
    browser.agent_focus = CDPSession.model_construct(cdp_client=wire.client, target_id="T1", session_id="S1")
    return browser


@pytest.mark.asyncio
async def test_the_page_session_pauses_every_request_and_answers_each_one(monkeypatch):
    _dns(monkeypatch, {})
    wire = _Wire()
    browser = _session(wire, browser_request_allowed)
    await browser._setup_request_guard()
    method, params, session = next(c for c in wire.sent if c[0] == "Fetch.enable")
    assert session == "S1" and params["patterns"] == [{"urlPattern": "*", "requestStage": "Request"}]
    for rid, url, rtype in [("R1", "https://example.com/app.js", "Script"), ("R2", "http://169.254.169.254/latest/meta-data/", "Fetch"),
                            ("R3", "http://127.0.0.1:8001/docs", "Document"), ("R4", "data:image/gif;base64,R0lGOD", "Image"),
                            ("R5", "file:///etc/passwd", "Document"), ("R6", "http://10.0.0.5/pixel.gif", "Image")]:
        await wire.client.emit_event("Fetch.requestPaused", _paused(rid, url, rtype), "S1")
    await wire.settle()
    assert sorted(wire.answers()) == [("Fetch.continueRequest", "R1", "S1"), ("Fetch.continueRequest", "R4", "S1"), ("Fetch.failRequest", "R2", "S1"),
                                      ("Fetch.failRequest", "R3", "S1"), ("Fetch.failRequest", "R5", "S1"), ("Fetch.failRequest", "R6", "S1")]
    fails = [p for m, p, s in wire.sent if m == "Fetch.failRequest"]
    assert fails and all(p["errorReason"] == "BlockedByClient" for p in fails)


@pytest.mark.asyncio
async def test_a_redirect_hop_is_a_new_paused_request_and_is_judged_on_its_own(monkeypatch):
    _dns(monkeypatch, {})
    wire = _Wire(); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    await wire.client.emit_event("Fetch.requestPaused", _paused("R1", "https://example.com/go", "Document"), "S1")
    hop = _paused("R2", "http://169.254.169.254/", "Document"); hop["redirectedRequestId"] = "R1"
    await wire.client.emit_event("Fetch.requestPaused", hop, "S1")
    await wire.settle()
    assert sorted(wire.answers()) == [("Fetch.continueRequest", "R1", "S1"), ("Fetch.failRequest", "R2", "S1")]


@pytest.mark.asyncio
async def test_answers_go_back_on_the_session_that_asked(monkeypatch):
    _dns(monkeypatch, {})
    wire = _Wire(); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    await wire.client.emit_event("Fetch.requestPaused", _paused("R1", "https://example.com/frame", "Document"), "CHILD-9")
    await wire.settle()
    assert wire.answers() == [("Fetch.continueRequest", "R1", "CHILD-9")]


@pytest.mark.asyncio
async def test_a_guard_that_raises_or_answers_oddly_blocks_the_request():
    async def boom(url): raise RuntimeError("guard bug")
    async def truthy_not_true(url): return "yes"
    for guard in (boom, truthy_not_true):
        wire = _Wire(); browser = _session(wire, guard); await browser._setup_request_guard()
        await wire.client.emit_event("Fetch.requestPaused", _paused("R1", "https://example.com/"), "S1")
        await wire.settle()
        assert wire.answers() == [("Fetch.failRequest", "R1", "S1")]


@pytest.mark.asyncio
async def test_child_targets_get_the_filter_when_they_attach(monkeypatch):
    wire = _Wire(); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    attached = {"sessionId": "OOPIF-3", "targetInfo": {"targetId": "T9", "type": "iframe", "title": "", "url": "https://ads.example/", "attached": True, "canAccessOpener": False},
                "waitingForDebugger": False}
    await wire.client.emit_event("Target.attachedToTarget", attached, None)
    await wire.settle()
    enables = [(p["patterns"], s) for m, p, s in wire.sent if m == "Fetch.enable"]
    assert ([{"urlPattern": "*", "requestStage": "Request"}], "OOPIF-3") in enables


@pytest.mark.asyncio
async def test_failing_to_install_the_filter_is_fatal_not_silent():
    wire = _Wire(fail_enable=True); browser = _session(wire, browser_request_allowed)
    with pytest.raises(RuntimeError, match="request guard"):
        await browser._setup_request_guard()


@pytest.mark.asyncio
async def test_without_a_guard_nothing_is_intercepted():
    wire = _Wire(); browser = _session(wire, None)
    await browser._setup_request_guard()
    assert wire.sent == []


# ── the audit wires it in ──

@pytest.mark.asyncio
async def test_audit_page_installs_the_guard_on_the_browser(monkeypatch):
    seen = {}

    class FakeBrowser:
        def __init__(self, **kw): seen.update(kw)
        async def start(self): pass
        async def stop(self): pass
        async def get_current_page(self):
            class P:
                async def goto(self, url): pass
            return P()

    monkeypatch.setattr(browser_session, "Browser", FakeBrowser)
    monkeypatch.setattr(browser_session, "assert_public_url", lambda url: asyncio.sleep(0))
    async with browser_session.audit_page("https://example.com/"):
        pass
    assert seen["request_guard"] is browser_request_allowed


def test_a_real_browser_object_carries_the_guard_into_its_profile():
    browser = Browser(browser_profile=BrowserProfile(), headless=True, request_guard=browser_request_allowed)
    assert browser.browser_profile.request_guard is browser_request_allowed
