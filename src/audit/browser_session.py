"""Thin wrapper around the repo's existing CDP browser stack for the website
audit. Reuses src.environment.browser directly rather than the agent/tool
plumbing, since a URL scan is a deterministic pipeline, not an agent task.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from src.audit.http_client import SSRFBlocked, assert_public_url
from src.environment.browser import Browser
from src.environment.browser.browser.session import DEFAULT_BROWSER_PROFILE


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
    browser = Browser(
        browser_profile=DEFAULT_BROWSER_PROFILE,
        headless=headless,
        viewport={"width": 1280, "height": 800},
        window_size={"width": 1280, "height": 800},
        highlight_elements=False,
    )
    await browser.start()
    try:
        page = await browser.get_current_page()
        await page.goto(url)
        yield page
    finally:
        await browser.stop()
