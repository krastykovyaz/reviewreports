import json

import pytest

from src.audit.page_facts import PAGE_FACTS_JS, gather_page_facts, wait_for_settled


class SettlingPage:
    """Simulates an SPA: loading, then rendering more text/resources, then stable."""

    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.calls = 0

    async def evaluate(self, js, *args):
        self.calls += 1
        if js == PAGE_FACTS_JS:
            return json.dumps({"url": "https://x/", "wordCount": 42})
        snap = self._snapshots.pop(0) if len(self._snapshots) > 1 else self._snapshots[0]
        return json.dumps(snap)


@pytest.mark.asyncio
async def test_wait_for_settled_waits_for_complete_and_stable():
    page = SettlingPage([
        {"state": "loading", "resources": 0, "textLength": 0},
        {"state": "complete", "resources": 1, "textLength": 0},
        {"state": "complete", "resources": 2, "textLength": 350},
        {"state": "complete", "resources": 2, "textLength": 350},
    ])
    await wait_for_settled(page, timeout_s=5.0, settle_s=0.3)
    assert page.calls >= 4  # polled through the changing states, then held for the settle window


@pytest.mark.asyncio
async def test_wait_for_settled_gives_up_at_timeout():
    page = SettlingPage([{"state": "loading", "resources": 0, "textLength": 0}])
    await wait_for_settled(page, timeout_s=0.6, settle_s=0.3)  # must return, not hang


@pytest.mark.asyncio
async def test_gather_page_facts_returns_dict_after_settling():
    page = SettlingPage([{"state": "complete", "resources": 3, "textLength": 100}])
    facts = await gather_page_facts(page)
    assert facts["wordCount"] == 42


@pytest.mark.asyncio
async def test_gather_page_facts_rejects_non_object_result():
    class BrokenPage:
        async def evaluate(self, js, *args):
            return json.dumps({"state": "complete", "resources": 0, "textLength": 0}) if js != PAGE_FACTS_JS else "ReferenceError: q is not defined"

    with pytest.raises(RuntimeError, match="did not return an object"):
        await gather_page_facts(BrokenPage())


# ── the real script in a real browser ────────────────────────────────────────

def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            import os
            return os.path.exists(pw.chromium.executable_path)
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_available(), reason="needs a Playwright Chromium")
@pytest.mark.parametrize("sandboxed", [False, True])
def test_the_page_facts_script_runs_in_a_real_browser_even_on_a_sandboxed_document(sandboxed):
    """tsech.online serves every shared card with `Content-Security-Policy: sandbox`, where reading document.cookie throws a
    SecurityError. That used to fail the whole audit of such a page; the other facts must still come back."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from playwright.sync_api import sync_playwright

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if sandboxed:
                self.send_header("Content-Security-Policy", "sandbox allow-scripts")
            self.end_headers()
            self.wfile.write(b"<!doctype html><html><head><title>Card</title></head><body><h1>Hello there</h1><p>Some words on a page.</p></body></html>")
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{srv.server_port}/")
            facts = page.evaluate(PAGE_FACTS_JS)
            facts = json.loads(facts) if isinstance(facts, str) else facts
            browser.close()
    finally:
        srv.shutdown()
    assert facts["cookies"] == [] and facts["wordCount"] > 0
