"""The Gemini default model and its fallback chain live in ONE place
(src/model_defaults.py). These tests pin the rules that list has to satisfy."""

import importlib
import os
import re

import pytest

from src import model_defaults
from src.model.manager import ModelManager


def _version(bare):
    return float(re.search(r"gemini-(\d+(?:\.\d+)?)", bare).group(1))


def _tier(bare):
    return "flash-lite" if "lite" in bare else "flash"


def test_chain_has_no_retired_or_duplicate_models():
    chain = model_defaults.GEMINI_CHAIN
    assert len(set(chain)) == len(chain)
    assert not [m for m in chain if m.startswith("gemini-2.0")], "gemini-2.0-* is retired"
    assert "gemini-3.7-flash" not in chain, "3.7 is the same backend as 3.8 - not a real fallback"
    # Expires on OpenRouter 2026-10-20, and the chain is what the OpenRouter route uses.
    assert not [m for m in chain if m.startswith("gemini-2.5")]


def test_stable_models_come_first_and_the_only_preview_is_last():
    chain = model_defaults.GEMINI_CHAIN
    previews = [m for m in chain if "preview" in m]
    assert previews == [chain[-1]]
    assert "preview" not in chain[0], "the default must not be a preview model"


def test_chain_spans_more_than_one_model_family():
    chain = model_defaults.GEMINI_CHAIN
    assert len({_tier(m) for m in chain}) >= 2  # Flash and Flash-Lite
    assert len({_version(m) for m in chain}) >= 2  # more than one generation


def test_default_model_is_the_head_of_the_chain(monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    reloaded = importlib.reload(model_defaults)
    try:
        assert reloaded.DEFAULT_MODEL == f"openrouter/{reloaded.GEMINI_CHAIN[0]}"
        assert reloaded.DEFAULT_PLUGINS_MODEL == reloaded.DEFAULT_MODEL + "-plugins"
    finally:
        importlib.reload(model_defaults)


def test_preview_can_be_chosen_by_env_override(monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "openrouter/gemini-3-flash-preview")
    reloaded = importlib.reload(model_defaults)
    try:
        assert reloaded.DEFAULT_MODEL == "openrouter/gemini-3-flash-preview"
    finally:
        monkeypatch.delenv("DEFAULT_MODEL", raising=False)
        importlib.reload(model_defaults)


@pytest.mark.asyncio
async def test_registry_is_generated_from_the_chain_in_order():
    manager = ModelManager()
    await manager._initialize_openrouter_models()
    chain = model_defaults.GEMINI_CHAIN
    for i, bare in enumerate(chain):
        config = manager.models[f"openrouter/{bare}"]
        assert config.model_id == f"google/{bare}"
        expected = f"openrouter/{chain[i + 1]}" if i + 1 < len(chain) else None
        assert config.fallback_model == expected
        twin = manager.models[f"openrouter/{bare}-plugins"]
        assert twin.model_id == config.model_id and twin.plugins
        assert twin.fallback_model == (expected + "-plugins" if expected else None)


@pytest.mark.asyncio
async def test_default_and_every_fallback_target_is_a_registered_model():
    manager = ModelManager()
    await manager.initialize()
    assert model_defaults.DEFAULT_MODEL in manager.models
    assert model_defaults.DEFAULT_PLUGINS_MODEL in manager.models
    for name, config in manager.models.items():
        if "gemini" in name and config.fallback_model:
            assert config.fallback_model in manager.models, f"{name} falls back to unregistered {config.fallback_model}"
    for old, new in model_defaults.LEGACY_ALIASES.items():
        assert new in manager.models, f"alias {old} -> {new} points at an unregistered model"
        assert old not in manager.models, f"{old} is an alias, it should not also be registered"


@pytest.mark.asyncio
async def test_google_direct_route_uses_the_same_chain():
    manager = ModelManager()
    await manager._initialize_google_models()
    chain = model_defaults.GEMINI_CHAIN
    for i, bare in enumerate(chain):
        config = manager.models[f"google/{bare}"]
        assert config.model_id == bare
        assert config.fallback_model == (f"google/{chain[i + 1]}" if i + 1 < len(chain) else None)


def test_no_gemini_model_name_is_spelled_out_anywhere_else():
    """Rule 1: model names come from one place. A hardcoded name elsewhere is
    exactly how ~20 files ended up pinned to a preview model."""
    exempt_prefixes = (
        "src/model_defaults.py",
        "src/tool/esg_tools/lightrag",  # vendored third-party code
        "src/optimizer/textgrad",  # vendored third-party code
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "src")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel.startswith(exempt_prefixes):
                continue
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, 1):
                    if re.search(r"gemini-\d", line):
                        offenders.append(f"{rel}:{lineno}: {line.strip()[:90]}")
    assert not offenders, "Gemini model names must come from src/model_defaults.py:\n" + "\n".join(offenders)
