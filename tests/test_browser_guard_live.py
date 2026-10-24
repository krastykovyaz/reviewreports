"""The request guard against a REAL Chromium (skipped where none is installed): what the mocked-CDP tests in
test_browser_request_guard.py assume about Chrome is checked here.

A page on a "public" name (site.test, mapped to 127.0.0.1 by the browser's own resolver rules, and judged public by the guard's
resolver) tries to reach a listener on a loopback port in every way it can: fetch, a WebSocket, a cross-site iframe (an
out-of-process one when site isolation is on), a popup, a worker, a service worker, a prefetch. The listener must never be hit.
The same page with no guard is the control: it does hit the listener, so the check is not vacuous."""
import asyncio
import ipaddress
import os
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.audit import browser_session, http_client

pytestmark = pytest.mark.skipif(browser_session._installed_chromium_path() is None, reason="no Chromium installed")

PUBLIC = "93.184.216.34"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _World:
    """Two local servers: the site (page, frame, popup, scripts) and the 'internal service' that must never be reached."""
    def __init__(self):
        self.site_port, self.internal_port = _free_port(), _free_port()
        self.hits = []
        site, internal, me = self.site_port, self.internal_port, self
        steal = f"http://127.0.0.1:{internal}"
        pages = {
            "/": ("text/html", f"""<!doctype html><title>before</title>
<link rel="prefetch" href="{steal}/prefetch">
<iframe src="http://other.test:{site}/frame"></iframe>
<script src="/ok.js"></script>
<script>
try {{ new WebSocket('ws://127.0.0.1:{internal}/ws-top'); }} catch (e) {{}}
try {{ new Worker('/worker.js'); }} catch (e) {{}}
try {{ navigator.serviceWorker.register('/sw.js'); }} catch (e) {{}}
fetch('{steal}/fetch-top', {{mode: 'no-cors'}}).catch(() => {{}});
window.open('http://other.test:{site}/popup');
for (let i = 0; i < 3; i++) {{
  const a = document.createElement('a'); a.download = 'big' + i + '.bin';
  a.href = URL.createObjectURL(new Blob([new Uint8Array(4 << 20)])); document.body.appendChild(a); a.click();
}}
</script>"""),
            "/frame": ("text/html", f"""<script>
fetch('{steal}/fetch-frame', {{mode: 'no-cors'}}).catch(() => {{}});
try {{ new WebSocket('ws://127.0.0.1:{internal}/ws-frame'); }} catch (e) {{}}
try {{ new Worker('/worker.js'); }} catch (e) {{}}</script>"""),
            "/popup": ("text/html", f"""<script>
fetch('{steal}/fetch-popup', {{mode: 'no-cors'}}).catch(() => {{}});
try {{ new WebSocket('ws://127.0.0.1:{internal}/ws-popup'); }} catch (e) {{}}</script>"""),
            "/bomb": ("text/html", "<h1>bomb</h1><script>setTimeout(() => { const a = []; for (;;) a.push(new Array(1e6).fill(Math.random())); }, 1000)</script>"),
            "/ok.js": ("text/javascript", "document.title = 'script-ran'"),
            "/worker.js": ("text/javascript", f"fetch('{steal}/fetch-worker', {{mode: 'no-cors'}}).catch(() => {{}});"
                                              f"try {{ new WebSocket('ws://127.0.0.1:{internal}/ws-worker-' + location.hostname); }} catch (e) {{}}"),
            "/sw.js": ("text/javascript", f"fetch('{steal}/fetch-sw', {{mode: 'no-cors'}}).catch(() => {{}});"
                                          f"try {{ new WebSocket('ws://127.0.0.1:{internal}/ws-sw'); }} catch (e) {{}}"),
        }

        class Site(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                ctype, body = pages.get(self.path, ("text/plain", ""))
                self.send_response(200); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(body.encode())

        class Internal(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                me.hits.append(self.path); self.send_response(404); self.end_headers()

        self.servers = [ThreadingHTTPServer(("127.0.0.1", site), Site), ThreadingHTTPServer(("127.0.0.1", internal), Internal)]
        for srv in self.servers:
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    def close(self):
        for srv in self.servers:
            srv.shutdown(); srv.server_close()


@pytest.fixture()
def world():
    w = _World()
    yield w
    w.close()


@pytest.fixture()
def tmp_root(tmp_path, monkeypatch):
    root = tmp_path / "systmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _resolve(host):
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        return [PUBLIC]            # every name here is a public one; only the literal 127.0.0.1 is internal


async def _audit(world, monkeypatch, tmp_root, *, guarded, site_isolation, settle=5):
    http_client._dns_cache.clear()
    monkeypatch.setattr(http_client, "_resolve_all_sync", _resolve)
    seen = []

    async def recording(url):
        seen.append(url)
        return await http_client.browser_request_allowed(url) if guarded else True

    async def public(url):
        return None

    monkeypatch.setattr(browser_session, "browser_request_allowed", recording)
    monkeypatch.setattr(browser_session, "assert_public_url", public)
    extra = ["--host-resolver-rules=MAP site.test 127.0.0.1, MAP other.test 127.0.0.1"]
    if site_isolation:
        extra.append("--site-per-process")
    monkeypatch.setattr(browser_session, "AUDIT_CHROMIUM_ARGS", [*browser_session.AUDIT_CHROMIUM_ARGS, *extra])

    real_browser = browser_session.Browser
    built = []

    def build(**kw):
        if not guarded:
            kw["request_guard"] = None          # the control: the same browser and page, with no guard
        built.append(real_browser(**kw))
        return built[-1]

    monkeypatch.setattr(browser_session, "Browser", build)
    result = {}
    async with browser_session.audit_page(f"http://site.test:{world.site_port}/") as page:
        await asyncio.sleep(settle)
        # (the unguarded control skips this: with popups and workers running free the page's main thread is busy for a minute)
        result["title"] = await page.evaluate("() => document.title") if guarded else None
        targets = (await built[0].cdp_client.send.Target.getTargets())["targetInfos"]
        result["target_types"] = sorted(t["type"] for t in targets)
        downloads = [d for d in os.listdir(tmp_root) if d.startswith("reviewreports-audit-downloads-")]
        result["downloaded"] = [f for d in downloads for f in os.listdir(tmp_root / d)]
    result["seen"] = seen
    result["profile_dirs_left"] = os.listdir(tmp_root)
    return result


def _internal(seen, world):
    return sorted(u.split(str(world.internal_port))[1] for u in seen if f":{world.internal_port}" in u)


@pytest.mark.asyncio
@pytest.mark.parametrize("site_isolation", [False, True], ids=["default-isolation", "out-of-process-iframes"])
async def test_nothing_reaches_a_loopback_listener_by_any_route(world, monkeypatch, tmp_root, site_isolation):
    r = await _audit(world, monkeypatch, tmp_root, guarded=True, site_isolation=site_isolation)
    assert world.hits == []                                              # no fetch, WebSocket, frame, popup, worker or prefetch got through
    assert r["title"] == "script-ran"                                    # a normal public page still loads and runs
    seen = _internal(r["seen"], world)
    assert {"/fetch-top", "/fetch-frame", "/fetch-popup", "/prefetch", "/fetch-worker"} <= set(seen)   # the guard really saw (and refused) them, in every target
    assert "/fetch-sw" not in seen                                       # no service worker is ever installed
    assert r["downloaded"] == []                                         # the three 4 MB downloads were refused by the browser
    assert r["profile_dirs_left"] == []                                  # ... and the profile and downloads directories are gone
    if site_isolation:
        assert "iframe" in r["target_types"]                             # the cross-site frame really was an out-of-process one


@pytest.mark.asyncio
async def test_the_control_without_a_guard_does_reach_the_listener(world, monkeypatch, tmp_root):
    r = await _audit(world, monkeypatch, tmp_root, guarded=False, site_isolation=True)
    assert {"/fetch-top", "/ws-top", "/fetch-frame", "/ws-frame"} <= set(world.hits)
    assert r["downloaded"] == []                                         # downloads are refused whether or not there is a guard


@pytest.mark.asyncio
async def test_a_page_that_runs_a_renderer_out_of_memory_fails_its_audit_at_once_and_leaves_nothing(world, monkeypatch, tmp_root):
    import time
    from src.audit.browser_session import BrowserCrashed

    http_client._dns_cache.clear()
    monkeypatch.setattr(http_client, "_resolve_all_sync", _resolve)

    async def public(url):
        return None

    monkeypatch.setattr(browser_session, "assert_public_url", public)
    monkeypatch.setattr(browser_session, "AUDIT_CHROMIUM_ARGS", [*browser_session.AUDIT_CHROMIUM_ARGS, "--host-resolver-rules=MAP site.test 127.0.0.1"])
    started = time.time()
    with pytest.raises(BrowserCrashed):
        async with browser_session.audit_page(f"http://site.test:{world.site_port}/bomb") as page:
            for _ in range(40):                  # a collector reading the page: it never gets an answer from a dead renderer
                await asyncio.wait_for(page.evaluate("() => document.title"), 20)
                await asyncio.sleep(0.5)
    assert time.time() - started < 40            # the heap cap (256 MB) is hit within seconds; not the job timeout
    assert os.listdir(tmp_root) == []
    import psutil
    assert not [p for p in psutil.Process().children(recursive=True) if "chrome" in (p.name() or "")]     # and no browser left running
