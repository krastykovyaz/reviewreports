"""Thin wrapper around the repo's existing CDP browser stack for the website
audit. Reuses src.environment.browser directly rather than the agent/tool
plumbing, since a URL scan is a deterministic pipeline, not an agent task.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from src.audit.http_client import SSRFBlocked, assert_public_url
from src.environment.browser import Browser
from src.environment.browser.browser.profile import BrowserProfile


def _installed_chromium_path() -> Optional[str]:
    """Reuse whatever Chromium is already cached under ~/.cache/ms-playwright,
    instead of this framework's own fallback (which shells out to `uvx
    playwright install chrome --with-deps` when its hardcoded glob patterns
    don't match — its patterns look for chrome-linux/, but current Playwright
    releases use chrome-linux64/ on 64-bit Linux). Avoids requiring `uv`/`uvx`
    and avoids downloading a second copy of Chromium when one is already on
    disk. Plain filesystem glob rather than playwright.sync_api: this runs
    inside an already-running asyncio loop (uvicorn's), and Playwright's sync
    API raises when used from a thread with a running event loop."""
    import glob
    from pathlib import Path

    candidates = sorted(
        glob.glob(str(Path("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome").expanduser())),
        reverse=True,  # highest revision number first
    )
    for path in candidates:
        if Path(path).is_file():
            return path
    return None


async def assert_page_is_public(page) -> None:
    """Raise SSRFBlocked if the page ended up somewhere non-public.

    page.goto() is raw CDP navigation and does not go through the session's
    security watchdog (verified: a profile with block_ip_addresses=True still
    loaded a 127.0.0.1 page), and the httpx pre-flight only vets the URL the caller
    gave - not where HTTP, meta or JS redirects eventually led the browser.
    Callers run this before reading anything off the page, so internal
    content is never reflected into a report. (The request itself has already
    been made by then, so a blind side-effect request is still possible.)"""
    final_url = await page.get_url()
    if not final_url or final_url.startswith("about:"):
        # The browser's own policy (or a failed load) left the page blank.
        raise SSRFBlocked(f"Page did not load a public http(s) URL (got {final_url!r})")
    await assert_public_url(final_url)


@asynccontextmanager
async def audit_page(url: str, headless: bool = True) -> AsyncIterator:
    """Start a headless browser, navigate to `url`, yield the page, always stop after."""
    # A fresh profile per call, not a shared module-level singleton: the
    # underlying framework mutates browser_profile.user_data_dir in place on
    # retry (local_browser_watchdog._launch_browser), so a shared profile
    # object across sequential/concurrent audits stomps on the previous
    # audit's Chrome user-data-dir (SingletonLock contention) and causes the
    # browser to fail to open its CDP port. Confirmed: with the shared
    # DEFAULT_BROWSER_PROFILE, browser launch reliably timed out after 30s.
    browser = Browser(
        browser_profile=BrowserProfile(),
        headless=headless,
        viewport={"width": 1280, "height": 800},
        window_size={"width": 1280, "height": 800},
        highlight_elements=False,
        executable_path=_installed_chromium_path(),
        # chromium_sandbox defaults to `not CONFIG.IN_DOCKER`, i.e. True on
        # bare metal — but Chrome's sandbox unconditionally refuses to
        # initialize when the parent process is root, Docker or not:
        # "Running as root without --no-sandbox is not supported." This
        # service (like the rest of this host) runs as root, so explicitly
        # disable it rather than relying on Docker detection.
        chromium_sandbox=False,
    )
    await browser.start()
    try:
        page = await browser.get_current_page()
        await assert_public_url(url)  # never navigate to an internal start URL
        await page.goto(url)
        await assert_page_is_public(page)  # ... nor read anything from wherever a redirect/refresh/script landed
        yield page
    finally:
        await browser.stop()
