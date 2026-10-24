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
    """A real CDPClient with its websocket send replaced by a recorder; events go through its real registry.
    fail: (method, session id) pairs whose command raises, as a real client does when Chrome answers with an error."""
    def __init__(self, fail_enable=False, fail=()):
        self.client = CDPClient("ws://unused")
        self.sent = []
        self.fail_enable = fail_enable
        self.fail = set(fail)

        async def send_raw(method, params=None, session_id=None):
            self.sent.append((method, params, session_id))
            if self.fail_enable and method == "Fetch.enable":
                raise RuntimeError("Fetch.enable: target closed")
            if (method, session_id) in self.fail:
                raise RuntimeError(f"{method}: target closed")
            return {}

        self.client.send_raw = send_raw

    def on_session(self, sid):
        return [m for m, p, s in self.sent if s == sid]

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


def _attached(sid, ttype, url="", waiting=True, target_id="T9", **info):
    # the shape of Chrome's Target.attachedToTarget (cdp_use.cdp.target.events.AttachedToTargetEvent) under flat auto-attach
    return {"sessionId": sid, "waitingForDebugger": waiting,
            "targetInfo": {"targetId": target_id, "type": ttype, "title": "", "url": url, "attached": True, "canAccessOpener": False,
                           "browserContextId": "CTX", **info}}


GUARD_STEPS = ["Fetch.enable", "Network.enable", "Network.emulateNetworkConditionsByRule", "Target.setAutoAttach", "Runtime.runIfWaitingForDebugger"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ttype, parent", [("iframe", "S1"), ("page", None)])
async def test_a_new_target_is_guarded_while_it_waits_and_only_then_resumed(ttype, parent):
    """An out-of-process iframe attaches to the PAGE's session (a cross-site frame's own requests start the moment it runs); a
    popup attaches at browser level. Both arrive waiting for the debugger and must have Fetch, the websocket rule and the
    auto-attach for their own children in place before Runtime.runIfWaitingForDebugger lets them execute."""
    wire = _Wire(); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    wire.sent.clear()
    await wire.client.emit_event("Target.attachedToTarget", _attached("CHILD-1", ttype, "https://ads.example/"), parent)
    await wire.settle()
    assert wire.on_session("CHILD-1") == GUARD_STEPS            # in this order, the resume last
    by_method = {m: p for m, p, s in wire.sent if s == "CHILD-1"}
    assert by_method["Fetch.enable"]["patterns"] == [{"urlPattern": "*", "requestStage": "Request"}]
    assert by_method["Target.setAutoAttach"] == {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}
    rules = by_method["Network.emulateNetworkConditionsByRule"]["matchedNetworkConditions"]
    assert [r["urlPattern"] for r in rules] == ["ws://*:*/*", "wss://*:*/*"] and all(r["offline"] is True for r in rules)


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["Fetch.enable", "Network.enable", "Network.emulateNetworkConditionsByRule", "Target.setAutoAttach"])
async def test_a_target_that_cannot_be_guarded_is_never_resumed(broken):
    wire = _Wire(fail=[(broken, "CHILD-1")]); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    wire.sent.clear()
    await wire.client.emit_event("Target.attachedToTarget", _attached("CHILD-1", "iframe", "https://ads.example/"), "S1")
    await wire.settle()
    assert "Runtime.runIfWaitingForDebugger" not in wire.on_session("CHILD-1")


WORKER_STEPS = ["Network.enable", "Network.emulateNetworkConditionsByRule", "Target.setAutoAttach", "Runtime.runIfWaitingForDebugger"]


@pytest.mark.asyncio
async def test_a_worker_created_by_a_main_frame_gets_the_websocket_rule_and_is_then_resumed():
    # (a dedicated worker has no Fetch domain: its requests are paused by the target that owns it.) parentFrameId == parentId
    # is how Chrome says "created by that target's own main frame"; the worker was attached to the page's session
    wire = _Wire(); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    wire.sent.clear()
    await wire.client.emit_event("Target.attachedToTarget", _attached("W-1", "worker", "https://example.com/w.js", parentId="T1", parentFrameId="T1"), "S1")
    await wire.settle()
    assert wire.on_session("W-1") == WORKER_STEPS


@pytest.mark.asyncio
async def test_a_worker_that_fails_to_be_guarded_is_never_resumed():
    wire = _Wire(fail=[("Network.emulateNetworkConditionsByRule", "W-1")]); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    wire.sent.clear()
    await wire.client.emit_event("Target.attachedToTarget", _attached("W-1", "worker", parentId="T1", parentFrameId="T1"), "S1")
    await wire.settle()
    assert "Runtime.runIfWaitingForDebugger" not in wire.on_session("W-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("ttype, info", [
    ("worker", {"parentId": "T1", "parentFrameId": "FRAME-OF-A-CROSS-ORIGIN-IFRAME"}),   # a worker of a same-process iframe: its WebSocket escaped the rule
    ("worker", {}),
    ("shared_worker", {}),
    ("service_worker", {}),
])
async def test_workers_that_cannot_be_filtered_are_left_paused(ttype, info):
    wire = _Wire(); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    wire.sent.clear()
    await wire.client.emit_event("Target.attachedToTarget", _attached("W-1", ttype, "https://example.com/w.js", **info), "S1")
    await wire.settle()
    assert wire.on_session("W-1") == []


@pytest.mark.asyncio
async def test_a_service_worker_script_is_refused_even_when_the_url_is_fine(monkeypatch):
    _dns(monkeypatch, {})
    wire = _Wire(); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    sw = _paused("R1", "https://example.com/sw.js", "Other"); sw["request"]["headers"] = {"Accept": "*/*", "Service-Worker": "script"}
    await wire.client.emit_event("Fetch.requestPaused", sw, "S1")
    await wire.client.emit_event("Fetch.requestPaused", _paused("R2", "https://example.com/app.js"), "S1")
    await wire.settle()
    assert sorted(wire.answers()) == [("Fetch.continueRequest", "R2", "S1"), ("Fetch.failRequest", "R1", "S1")]


@pytest.mark.asyncio
async def test_the_page_session_gets_fetch_the_websocket_rule_and_auto_attach_for_its_children():
    wire = _Wire(); browser = _session(wire, browser_request_allowed); await browser._setup_request_guard()
    assert wire.on_session("S1") == GUARD_STEPS[:4]             # nothing to resume: the page is not waiting
    auto = next(p for m, p, s in wire.sent if m == "Target.setAutoAttach")
    assert auto == {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["Network.enable", "Network.emulateNetworkConditionsByRule", "Target.setAutoAttach"])
async def test_the_page_without_the_websocket_rule_or_child_cover_is_fatal(broken):
    wire = _Wire(fail=[(broken, "S1")]); browser = _session(wire, browser_request_allowed)
    with pytest.raises(RuntimeError, match="request guard"):
        await browser._setup_request_guard()


@pytest.mark.asyncio
async def test_the_handlers_are_registered_after_anything_else_that_registers_them():
    # cdp_use keeps one handler per event: _setup_proxy_auth registers its own Fetch.requestPaused / attachedToTarget handlers
    # (which continue everything), so a guard registered before it would be silently replaced
    wire = _Wire(); browser = _session(wire, browser_request_allowed)
    browser._register_request_guard()
    wire.client.register.Fetch.requestPaused(lambda event, session_id=None: wire.sent.append(("proxy-handler", None, None)))
    await browser._setup_request_guard()
    await wire.client.emit_event("Fetch.requestPaused", _paused("R1", "file:///etc/passwd"), "S1")
    await wire.settle()
    assert ("Fetch.failRequest", "R1", "S1") in wire.answers() and not [c for c in wire.sent if c[0] == "proxy-handler"]


# ── connecting: nothing may run before the guard is on it ──

class _ChromeStub:
    """What Chrome answers the commands connect() sends (shapes as in the CDP protocol)."""
    def __init__(self):
        self.sent = []
        self.targets = [{"targetId": "T1", "type": "page", "title": "", "url": "about:blank", "attached": False, "canAccessOpener": False}]

    async def send_raw(self, method, params=None, session_id=None):
        self.sent.append((method, params, session_id))
        if method == "Target.getTargets":
            return {"targetInfos": self.targets}
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Target.getTargetInfo":
            return {"targetInfo": self.targets[0]}
        if method == "Runtime.evaluate":
            return {"result": {"type": "number", "value": 2, "description": "2"}}
        return {}

    def methods(self):
        return [m for m, p, s in self.sent]


async def _connect(monkeypatch, **profile):
    from src.environment.browser.browser import session as session_module
    chrome = _ChromeStub()

    class StubbedClient(CDPClient):
        async def start(self): pass
        async def send_raw(self, method, params=None, session_id=None): return await chrome.send_raw(method, params, session_id)
        async def stop(self): pass

    monkeypatch.setattr(session_module, "CDPClient", StubbedClient)
    browser = Browser(browser_profile=BrowserProfile(**profile), headless=True, cdp_url="ws://unused", is_local=False,
                      **({"request_guard": browser_request_allowed} if profile.pop("guarded", True) else {}))
    await browser.connect()
    await asyncio.sleep(0.2)            # the tab-created / focus events connect() dispatches run their handlers
    await browser.event_bus.stop(clear=True, timeout=1)
    return browser, chrome


@pytest.mark.asyncio
async def test_connect_makes_browser_level_auto_attach_wait_and_registers_the_handler_first(monkeypatch):
    from src.environment.browser.browser import session as session_module
    order = []
    real = session_module.BrowserSession._register_request_guard

    def spy(self):
        order.append("registered"); return real(self)

    monkeypatch.setattr(session_module.BrowserSession, "_register_request_guard", spy)
    browser, chrome = await _connect(monkeypatch)
    first_attach = next(i for i, (m, p, s) in enumerate(chrome.sent) if m == "Target.setAutoAttach" and s is None)
    assert order[0] == "registered" and not [m for m, p, s in chrome.sent[:first_attach]]   # before the first command of all
    assert chrome.sent[first_attach][1] == {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}
    page_steps = [m for m, p, s in chrome.sent if s == "S1" and m in GUARD_STEPS[:4]]
    assert page_steps == GUARD_STEPS[:4]


@pytest.mark.asyncio
async def test_without_a_guard_browser_level_auto_attach_does_not_wait(monkeypatch):
    browser, chrome = await _connect(monkeypatch, guarded=False)
    attach = next(p for m, p, s in chrome.sent if m == "Target.setAutoAttach")
    assert attach["waitForDebuggerOnStart"] is False
    assert not [m for m in chrome.methods() if m in ("Fetch.enable", "Network.emulateNetworkConditionsByRule")]


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
        crashed = False
        def __init__(self, **kw): seen.update(kw)
        def browser_process(self): return None
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


# ── a crashed target ends the audit's browser (abort_on_crash) ──

class _Proc:
    """The part of psutil.Process the session uses: kill() and children(recursive=True)."""
    def __init__(self, children=()):
        self._children, self.killed = list(children), False
    def children(self, recursive=False): return list(self._children)
    def kill(self): self.killed = True


def _crashed(target_id="T1"):
    # the shape of Target.targetCrashed (cdp_use.cdp.target.events.TargetCrashedEvent)
    return {"targetId": target_id, "status": "oom", "errorCode": 0}


@pytest.mark.asyncio
async def test_connect_listens_for_crashes_only_when_asked_to_abort_on_crash(monkeypatch):
    browser, chrome = await _connect(monkeypatch, abort_on_crash=True)
    assert ("Target.setDiscoverTargets", {"discover": True}, None) in chrome.sent       # crash events need discovery
    browser2, chrome2 = await _connect(monkeypatch)
    assert "Target.setDiscoverTargets" not in chrome2.methods()


@pytest.mark.asyncio
async def test_a_crashed_target_kills_the_browser_and_its_processes_and_is_remembered():
    from types import SimpleNamespace
    wire = _Wire(); browser = _session(wire, None)
    renderer, gpu, main = _Proc(), _Proc(), _Proc(children=[]); main._children = [renderer, gpu]
    browser._local_browser_watchdog = SimpleNamespace(_subprocess=main)
    wire.client.register.Target.targetCrashed(browser._on_target_crashed)
    assert browser.crashed is False
    await wire.client.emit_event("Target.targetCrashed", _crashed(), None)
    assert browser.crashed is True and main.killed and renderer.killed and gpu.killed


@pytest.mark.asyncio
async def test_a_crash_with_no_local_process_only_marks_the_session():
    wire = _Wire(); browser = _session(wire, None)
    browser._local_browser_watchdog = None
    wire.client.register.Target.targetCrashed(browser._on_target_crashed)
    await wire.client.emit_event("Target.targetCrashed", _crashed(), None)
    assert browser.crashed is True


# ── downloads are refused in the browser ──

@pytest.mark.asyncio
async def test_connect_denies_downloads_when_the_profile_does_not_accept_them(monkeypatch):
    browser, chrome = await _connect(monkeypatch, accept_downloads=False)
    assert ("Browser.setDownloadBehavior", {"behavior": "deny"}, None) in chrome.sent
    browser2, chrome2 = await _connect(monkeypatch)
    assert "Browser.setDownloadBehavior" not in chrome2.methods()           # the default is unchanged


async def _download_behavior(accept):
    from src.environment.browser.browser.watchdogs.downloads_watchdog import DownloadsWatchdog
    wire = _Wire()
    browser = Browser(browser_profile=BrowserProfile(accept_downloads=accept), headless=True)
    browser._cdp_client_root = wire.client
    browser.agent_focus = CDPSession.model_construct(cdp_client=wire.client, target_id="T1", session_id="S1")
    DownloadsWatchdog.model_rebuild()
    await DownloadsWatchdog(event_bus=browser.event_bus, browser_session=browser).attach_to_target("T1")
    await browser.event_bus.stop(clear=True, timeout=1)
    return [p for m, p, s in wire.sent if m == "Browser.setDownloadBehavior"], browser.browser_profile.downloads_path


@pytest.mark.asyncio
async def test_the_downloads_watchdog_does_not_turn_downloads_back_on():
    denied, _ = await _download_behavior(False)
    assert [d["behavior"] for d in denied] == ["deny"] and "downloadPath" not in denied[0]
    allowed, path = await _download_behavior(True)
    assert allowed[0]["behavior"] == "allow" and allowed[0]["downloadPath"] == str(path.resolve())
