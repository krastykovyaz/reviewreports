"""The environment handed to child processes that run untrusted content (Chromium on a visited site, the PDF rasteriser).

This service's own environment holds API keys (DEEPSEEK_API_KEY and friends, loaded from .env). A child that renders
attacker-controlled input - a browser exploit, a hostile PDF - must not be able to read them from /proc/self/environ,
so children get an allowlist, not a copy of everything. Standalone (no heavy imports) so both the audit and the CV code
can use it."""
import os
from typing import Dict, Mapping, Optional

_KEEP = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "DISPLAY", "PLAYWRIGHT_BROWSERS_PATH"})


def scrubbed_child_env(environ: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Only what a browser/renderer needs to start: the lookup path, a home for its profile/cache, locale, temp dir,
    display, the Playwright browser cache, and the XDG_* directories. Everything else - every secret - is left out."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items() if k in _KEEP or k.startswith("XDG_")}
