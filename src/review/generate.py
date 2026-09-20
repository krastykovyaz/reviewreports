"""Single entry point for report generation, kind-dispatched.

Every review kind (website audit, code review, resume/presentation/book
review, ...) registers a handler here rather than each caller (service, tool,
agent) branching on kind itself.

run_code_review and run_document_review are resolved lazily, on first actual
access, rather than imported at module load time. Both pull in heavy,
unrelated dependency chains (code_review -> src.logger -> rich;
document_review -> markitdown; and transitively, via src.utils,
torch/pandas/numpy/langchain/tiktoken/huggingface_hub/crawl4ai/firecrawl)
that a deployment using only website_audit (httpx/pydantic, nothing else)
has no reason to install. They remain importable/monkeypatchable as
src.review.generate.run_code_review / .run_document_review, same as if they
had been imported eagerly — see __getattr__ below.
"""

import sys
from functools import partial
from typing import Awaitable, Callable, Dict, Optional

from src.audit.website import run_website_audit
from src.report.schema import Report

_HANDLERS: Dict[str, Callable[..., Awaitable[Report]]] = {
    "website_audit": run_website_audit,
}

# kind -> (attribute name on this module, extra kwargs to bind via partial)
_LAZY_HANDLERS: Dict[str, tuple] = {
    "code_review": ("run_code_review", {}),
    "resume_review": ("run_document_review", {"flavor": "resume"}),
    "presentation_review": ("run_document_review", {"flavor": "presentation"}),
    "book_review": ("run_document_review", {"flavor": "book"}),
}

_LAZY_MODULES = {
    "run_code_review": "src.review.code_review",
    "run_document_review": "src.review.document",
}


def __getattr__(name):
    """PEP 562: resolve run_code_review/run_document_review on first access
    instead of at module-import time, and cache the result as a normal
    module global (so subsequent lookups — including a test's
    monkeypatch.setattr — behave exactly as if it had been imported eagerly;
    __getattr__ only fires when normal attribute lookup fails, so once
    cached or patched, this function is not consulted again)."""
    module_path = _LAZY_MODULES.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value


def supported_kinds() -> list:
    return list(_HANDLERS) + list(_LAZY_HANDLERS)


def _resolve_handler(kind: str) -> Optional[Callable[..., Awaitable[Report]]]:
    if kind in _HANDLERS:
        return _HANDLERS[kind]
    lazy = _LAZY_HANDLERS.get(kind)
    if lazy is None:
        return None
    attr_name, extra_kwargs = lazy
    func = getattr(sys.modules[__name__], attr_name)
    return partial(func, **extra_kwargs) if extra_kwargs else func


async def generate_report(kind: str, input: str, model_name: Optional[str] = None, lang: str = "en") -> Report:
    handler = _resolve_handler(kind)
    if handler is None:
        raise ValueError(f"Unsupported report kind: {kind!r}. Supported kinds: {supported_kinds()}")
    return await handler(input, model_name=model_name, lang=lang)
