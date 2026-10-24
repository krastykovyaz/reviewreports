"""Thin wrapper around the repo's existing CDP browser stack for the website
audit. Reuses src.environment.browser directly rather than the agent/tool
plumbing, since a URL scan is a deterministic pipeline, not an agent task.
"""

import asyncio
import logging
import os
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from websockets.exceptions import ConnectionClosed

from src.audit.http_client import SSRFBlocked, assert_public_url, browser_request_allowed
from src.environment.browser import Browser
from src.service.child_env import scrubbed_child_env

logger = logging.getLogger(__name__)

# What a hostile page can cost the host, capped in the browser itself. The service unit shares one memory limit between
# uvicorn, up to two audit Chromiums and the CV raster worker (MemoryMax=1200M in the unit; an OOM kill of any one of them
# makes systemd stop the whole unit), so a page must run out of ITS OWN budget first: V8 aborts the renderer at ~256 MB of
# heap (a crashed tab, which fails that audit), and the renderer count is bounded. The disk and media caches are bounded
# too (a profile is deleted after each audit, but only after it has been written).
AUDIT_CHROMIUM_ARGS = [
    "--js-flags=--max-old-space-size=256",
    "--renderer-process-limit=2",
    "--disk-cache-size=33554432",
    "--media-cache-size=33554432",
]


class BrowserCrashed(RuntimeError):
    """The audit's Chromium (or one of its renderers) died while the page was being read: out of memory, killed, crashed.
    Not a network error, so the job reports the generic failure and not "we could not reach that address"."""


_PROFILE_DIR_PREFIX = "reviewreports-audit-profile-"
_DOWNLOADS_DIR_PREFIX = "reviewreports-audit-downloads-"
_BROWSER_EXIT_GRACE_SEC = 8.0


def _remove_own_dirs(paths: list[str]) -> None:
    """Delete the directories audit_page itself created (mkdtemp under the system temp dir), and nothing else. A path is only
    removed if it is a real directory (not a link) directly inside the temp dir whose name carries this module's prefix;
    shutil.rmtree does not follow links inside it. Never raises."""
    tmp_root = os.path.realpath(tempfile.gettempdir())
    for path in paths:
        try:
            if os.path.islink(path) or not os.path.isdir(path):
                continue
            real = os.path.realpath(path)
            name = os.path.basename(real)
            if os.path.dirname(real) != tmp_root or not name.startswith((_PROFILE_DIR_PREFIX, _DOWNLOADS_DIR_PREFIX)):
                logger.warning("not removing %r: it is not a directory this audit created", path)
                continue
            shutil.rmtree(real, ignore_errors=True)
        except Exception:  # noqa: BLE001 - cleanup must never mask the audit's own outcome
            logger.debug("could not remove %r", path, exc_info=True)


def _reap_and_remove(process, paths: list[str]) -> None:
    """Wait for the browser process to be gone, then delete the directories. Chromium is only ASKED to exit when stop() returns
    and keeps writing to its profile until it does, so deleting first leaves a half-deleted profile behind; one that has not
    exited after a few seconds is killed, with its children. Runs in a thread; never raises."""
    if process is not None:
        try:
            children = process.children(recursive=True)
        except Exception:  # noqa: BLE001
            children = []
        try:
            process.wait(timeout=_BROWSER_EXIT_GRACE_SEC)
        except Exception:  # noqa: BLE001 - still running (TimeoutExpired), or already gone
            for victim in [*children, process]:
                try:
                    victim.kill()
                except Exception:  # noqa: BLE001
                    pass
            try:
                process.wait(timeout=_BROWSER_EXIT_GRACE_SEC)
            except Exception:  # noqa: BLE001
                pass
    for _ in range(3):
        _remove_own_dirs(paths)
        if not any(os.path.lexists(p) for p in paths):
            return
        time.sleep(0.3)         # a renderer that outlived the browser may still have been writing


def chromium_sandbox_enabled() -> bool:
    """Whether Chromium's own sandbox is used. On by default for an unprivileged user; off as root, where Chrome refuses to
    start with it ("Running as root without --no-sandbox is not supported"). REVIEWREPORTS_CHROMIUM_SANDBOX=on|off forces
    either way (e.g. off in a container without user namespaces, on to make a root run fail loudly instead of unsandboxed)."""
    forced = os.getenv("REVIEWREPORTS_CHROMIUM_SANDBOX", "").strip().lower()
    if forced in ("on", "1", "true", "yes"):
        return True
    if forced in ("off", "0", "false", "no"):
        return False
    return getattr(os, "geteuid", lambda: 0)() != 0


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
    """Start a headless browser, navigate to `url`, yield the page, always stop after (and delete what it wrote to disk)."""
    # A fresh profile per call, not a shared module-level singleton: the
    # underlying framework mutates browser_profile.user_data_dir in place on
    # retry (local_browser_watchdog._launch_browser), so a shared profile
    # object across sequential/concurrent audits stomps on the previous
    # audit's Chrome user-data-dir (SingletonLock contention) and causes the
    # browser to fail to open its CDP port. Confirmed: with the shared
    # DEFAULT_BROWSER_PROFILE, browser launch reliably timed out after 30s.
    #
    # The profile and downloads directories are made here, not left to the framework's defaults (a mkdtemp that nothing
    # deletes, and /tmp/browser-use-downloads-*), so this function knows exactly what it has to remove.
    own_dirs: list[str] = []
    process = None
    try:
        profile_dir = tempfile.mkdtemp(prefix=_PROFILE_DIR_PREFIX)
        own_dirs.append(profile_dir)
        downloads_dir = tempfile.mkdtemp(prefix=_DOWNLOADS_DIR_PREFIX)
        own_dirs.append(downloads_dir)
        browser = Browser(
            user_data_dir=profile_dir,
            downloads_path=downloads_dir,
            headless=headless,
            viewport={"width": 1280, "height": 800},
            window_size={"width": 1280, "height": 800},
            highlight_elements=False,
            executable_path=_installed_chromium_path(),
            # chromium_sandbox defaults to `not CONFIG.IN_DOCKER`, i.e. True on
            # bare metal — but Chrome's sandbox unconditionally refuses to
            # initialize when the parent process is root, Docker or not:
            # "Running as root without --no-sandbox is not supported." Run as root
            # (as this host used to) it has to be off, so decide from the actual
            # user instead of relying on Docker detection: sandboxed whenever the
            # service runs unprivileged (see chromium_sandbox_enabled).
            chromium_sandbox=chromium_sandbox_enabled(),
            # The browser renders arbitrary sites: it gets an allowlisted
            # environment, never this process's API keys (see src/service/child_env.py).
            env=scrubbed_child_env(),
            # Not just the start URL: every request the page makes (subresources, iframes, form posts, redirect hops) is
            # checked against the same rules before it is sent (see browser_request_allowed).
            request_guard=browser_request_allowed,
            # A renderer that runs out of memory takes its commands with it and nothing answers them again: the browser is
            # killed at once so the audit fails now (BrowserCrashed) instead of at the job timeout, holding its slot.
            abort_on_crash=True,
            args=AUDIT_CHROMIUM_ARGS,
            # A page is read, never downloaded from: a download is refused in the browser (a page looping <a download> of big
            # blobs would otherwise fill the disk), and the PDF viewer is not auto-saved either.
            accept_downloads=False,
            auto_download_pdfs=False,
            # The default extensions (ad blocker, cookie banners) are fetched from the web, with no timeout and on the event
            # loop, on the first audit after every restart - and the audit reads the page as it is, banners included.
            enable_default_extensions=False,
        )
        try:
            await browser.start()
            page = await browser.get_current_page()
            await assert_public_url(url)  # never navigate to an internal start URL
            await page.goto(url)
            # Where the browser ended up is checked by the caller (assert_page_is_public) after the page has settled: right
            # after goto() the browser's target info has not caught up yet and get_url() is still empty.
            yield page
        except Exception as exc:
            # The browser's CDP connection dropping is how a killed Chromium shows up in whatever was waiting on the page.
            if isinstance(exc, ConnectionClosed) or browser.crashed:
                raise BrowserCrashed("the audit browser crashed") from exc
            raise
        finally:
            process = browser.browser_process()      # stop() forgets it
            await browser.stop()
    finally:
        # Also when the audit failed, timed out or was cancelled; shielded so a second cancellation cannot leave it half done.
        await asyncio.shield(asyncio.to_thread(_reap_and_remove, process, own_dirs))
