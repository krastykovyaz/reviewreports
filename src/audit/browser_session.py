"""Thin wrapper around the repo's existing CDP browser stack for the website
audit. Reuses src.environment.browser directly rather than the agent/tool
plumbing, since a URL scan is a deterministic pipeline, not an agent task.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from src.environment.browser import Browser
from src.environment.browser.browser.session import DEFAULT_BROWSER_PROFILE


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
