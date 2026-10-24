"""Child processes that handle untrusted content - Chromium on a visited site, the PDF rasteriser - run sandboxed where
the user allows it and never inherit this service's secrets (DEEPSEEK_API_KEY and friends)."""
import asyncio
import json
import os
import stat
import subprocess
import sys

import pytest

from src.audit import browser_session
from src.audit.browser_session import chromium_sandbox_enabled
from src.cv import preview_card
from src.environment.browser import Browser
from src.environment.browser.browser.profile import BrowserProfile
from src.environment.browser.browser.watchdogs.local_browser_watchdog import LocalBrowserWatchdog
from src.service.child_env import scrubbed_child_env
from tests.helpers import _cv_pdf

_SECRETS = {"DEEPSEEK_API_KEY": "sk-secret-1", "OPENAI_API_KEY": "sk-secret-2", "ANTHROPIC_API_KEY": "sk-secret-3", "DATABASE_URL": "postgres://u:p@h/db"}


# ── the environment builder ──

def test_only_the_allowlisted_variables_survive():
    env = scrubbed_child_env({**_SECRETS, "PATH": "/usr/bin", "HOME": "/home/x", "LANG": "C.UTF-8", "LC_ALL": "C", "TMPDIR": "/tmp",
                              "DISPLAY": ":0", "PLAYWRIGHT_BROWSERS_PATH": "/pw", "XDG_RUNTIME_DIR": "/run/user/1", "XDG_CACHE_HOME": "/c",
                              "LC_CTYPE": "x", "PYTHONPATH": "/evil", "LD_PRELOAD": "/evil.so", "HTTP_PROXY": "http://p"})
    assert env == {"PATH": "/usr/bin", "HOME": "/home/x", "LANG": "C.UTF-8", "LC_ALL": "C", "TMPDIR": "/tmp", "DISPLAY": ":0",
                   "PLAYWRIGHT_BROWSERS_PATH": "/pw", "XDG_RUNTIME_DIR": "/run/user/1", "XDG_CACHE_HOME": "/c"}


def test_it_reads_the_real_environment_by_default(monkeypatch):
    for k, v in _SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", "/usr/bin")
    env = scrubbed_child_env()
    assert env["PATH"] == "/usr/bin" and not set(_SECRETS) & set(env)


# ── sandbox choice ──

@pytest.mark.parametrize("euid,expected", [(0, False), (1000, True), (33, True)])
def test_the_sandbox_is_on_unless_running_as_root(monkeypatch, euid, expected):
    monkeypatch.delenv("REVIEWREPORTS_CHROMIUM_SANDBOX", raising=False)
    monkeypatch.setattr(browser_session.os, "geteuid", lambda: euid)
    assert chromium_sandbox_enabled() is expected


@pytest.mark.parametrize("value,euid,expected", [("on", 0, True), ("ON", 0, True), ("1", 0, True), ("off", 1000, False), ("Off", 1000, False),
                                                 ("0", 1000, False), ("", 0, False), ("junk", 1000, True), ("junk", 0, False)])
def test_the_env_override_wins_and_junk_falls_back_to_the_user_check(monkeypatch, value, euid, expected):
    monkeypatch.setenv("REVIEWREPORTS_CHROMIUM_SANDBOX", value)
    monkeypatch.setattr(browser_session.os, "geteuid", lambda: euid)
    assert chromium_sandbox_enabled() is expected


def _profile_args(sandbox):
    browser = Browser(browser_profile=BrowserProfile(), headless=True, chromium_sandbox=sandbox)
    return browser.browser_profile.get_args()


def test_the_launch_flags_follow_the_sandbox_choice():
    assert "--no-sandbox" in _profile_args(False)
    assert "--no-sandbox" not in _profile_args(True) and "--disable-setuid-sandbox" not in _profile_args(True)


# ── audit_page hands both to the browser ──

@pytest.mark.asyncio
async def test_audit_page_passes_the_sandbox_choice_and_a_scrubbed_env(monkeypatch):
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

    for k, v in _SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("REVIEWREPORTS_CHROMIUM_SANDBOX", "on")
    monkeypatch.setattr(browser_session, "Browser", FakeBrowser)
    monkeypatch.setattr(browser_session, "assert_public_url", lambda url: asyncio.sleep(0))
    async with browser_session.audit_page("https://example.com/"):
        pass
    assert seen["chromium_sandbox"] is True
    assert seen["env"] and not set(_SECRETS) & set(seen["env"]) and "PATH" in seen["env"]


# ── the real launch path: what the browser process actually receives ──

_STUB = """#!{python}
import http.server, json, os, sys
port = next(int(a.split("=")[1]) for a in sys.argv if a.startswith("--remote-debugging-port="))
json.dump({{"env": dict(os.environ), "argv": sys.argv[1:]}}, open({out!r}, "w"))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{}}")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


async def _launch_stub(tmp_path, monkeypatch, **browser_kw):
    """Runs the repo's real LocalBrowserWatchdog launch against a stand-in executable that records its environment and
    answers /json/version on the debugging port like Chrome does."""
    out = tmp_path / "seen.json"
    exe = tmp_path / "fake-chrome"
    exe.write_text(_STUB.format(python=sys.executable, out=str(out)))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    browser = Browser(browser_profile=BrowserProfile(), headless=True, executable_path=str(exe), user_data_dir=str(tmp_path / "ud"), **browser_kw)
    watchdog = LocalBrowserWatchdog(event_bus=browser.event_bus, browser_session=browser)
    process, _cdp = await watchdog._launch_browser(max_retries=1)
    try:
        for _ in range(50):
            if out.exists() and out.stat().st_size:
                break
            await asyncio.sleep(0.1)
        return json.loads(out.read_text())
    finally:
        process.kill()


@pytest.mark.asyncio
async def test_the_launched_browser_receives_only_the_given_environment(tmp_path, monkeypatch):
    for k, v in _SECRETS.items():
        monkeypatch.setenv(k, v)
    seen = await _launch_stub(tmp_path, monkeypatch, env=scrubbed_child_env(), chromium_sandbox=True)
    assert not set(_SECRETS) & set(seen["env"]) and "PATH" in seen["env"]
    assert "--no-sandbox" not in seen["argv"]


@pytest.mark.asyncio
async def test_without_an_env_the_launch_behaves_as_before_and_inherits(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_INHERITED_VAR", "yes")
    seen = await _launch_stub(tmp_path, monkeypatch, chromium_sandbox=False)
    assert seen["env"].get("SOME_INHERITED_VAR") == "yes" and "--no-sandbox" in seen["argv"]


# ── the raster worker ──

def test_the_raster_child_gets_a_scrubbed_env_and_still_renders(monkeypatch):
    for k, v in _SECRETS.items():
        monkeypatch.setenv(k, v)
    seen = {}
    real_run = subprocess.run

    def spy(args, **kw):
        seen.update(kw)
        return real_run(args, **kw)

    monkeypatch.setattr(preview_card.subprocess, "run", spy)
    png = preview_card.rasterize_first_page(_cv_pdf())
    assert png.startswith(b"\x89PNG")
    assert seen["env"] and not set(_SECRETS) & set(seen["env"]) and "PATH" in seen["env"]

