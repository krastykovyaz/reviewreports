"""Single source of truth for which Gemini models the project uses by default.

Every default model name and every Gemini fallback chain comes from here; no
other file should spell out a Gemini model name. Leaf module on purpose (stdlib
+ dotenv only, no `src.model` import) so any file can import it without
pulling in the model manager or risking an import cycle.

Facts this list rests on (checked 2026-10-11):
- OpenRouter's public /api/v1/models lists every id below with no expiration.
- OpenRouter lists google/gemini-2.5-flash and google/gemini-2.5-pro with
  expiration_date 2026-10-20, so 2.5 is NOT used on the OpenRouter route.
- gemini-2.0-* is no longer offered, and gemini-3.7-flash is deprecated and
  auto-redirected to 3.8 (the same backend, so it is not a real fallback).

Override without a code change: set DEFAULT_MODEL to any registered model name,
e.g. DEFAULT_MODEL=openrouter/gemini-3-flash-preview to opt back into the
preview model.
"""

import os
from typing import Dict, Tuple

from dotenv import load_dotenv

# Same as src/model/manager.py: this module can be imported before the manager
# is, and DEFAULT_MODEL below is read at import time.
load_dotenv()

# Bare Google model names, in fallback order: stable first, preview last. Two
# tiers (Flash, Flash-Lite) and three generations; each entry is a different
# model, so a single retirement cannot take the whole chain down.
GEMINI_CHAIN: Tuple[str, ...] = (
    "gemini-3.8-flash",  # primary: newest non-preview; deprecated versions redirect to it
    "gemini-3.6-flash",  # previous generation, same tier
    "gemini-3.5-flash-lite",  # different tier (Flash-Lite) and generation
    "gemini-3-flash-preview",  # preview: last resort only, or explicit opt-in via DEFAULT_MODEL
)

# Callable on Google's own API today but NOT part of any fallback chain, and
# only registered on the direct-Google route (the OpenRouter 2.5 ids expire 2026-10-20).
GOOGLE_DIRECT_EXTRA: Tuple[str, ...] = ("gemini-2.5-flash", "gemini-2.5-pro")


def openrouter_name(bare: str) -> str:
    """Registry name for the OpenRouter route, e.g. 'openrouter/gemini-3.8-flash'."""
    return f"openrouter/{bare}"


def google_name(bare: str) -> str:
    """Registry name for the direct Google route, e.g. 'google/gemini-3.8-flash'."""
    return f"google/{bare}"


def plugins_name(name: str) -> str:
    """Registry name of the OpenRouter variant that also sends the default plugins."""
    return f"{name}-plugins"


DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL") or openrouter_name(GEMINI_CHAIN[0])
DEFAULT_PLUGINS_MODEL: str = plugins_name(DEFAULT_MODEL)

# Names that used to be registered and are gone (retired, expiring on OpenRouter
# 2026-10-20, or never callable). A caller that still asks for one is routed to
# the current default with a deprecation WARNING instead of a "model not found".
_HEAD = openrouter_name(GEMINI_CHAIN[0])
LEGACY_ALIASES: Dict[str, str] = {
    "openrouter/gemini-2.5-flash": _HEAD,
    "openrouter/gemini-2.5-pro": _HEAD,
    "openrouter/gemini-3-pro-preview": _HEAD,  # id is not in OpenRouter's catalogue at all
    "openrouter/gemini-2.5-flash-plugins": plugins_name(_HEAD),
    "google/gemini-3-pro-preview": google_name(GEMINI_CHAIN[0]),  # not callable on Google's API either
}

# How long a model that answered 404 / NOT_FOUND is skipped before it is tried again.
NOT_FOUND_COOLDOWN_S: float = float(os.getenv("MODEL_NOT_FOUND_COOLDOWN_S", "3600"))
