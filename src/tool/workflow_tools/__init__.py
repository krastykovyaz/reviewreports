"""
Agent Tools Module

This module contains tools that are specifically designed for agent workflows,
including browser automation and deep research capabilities.

Tools are resolved lazily on first attribute access (PEP 562) so importing
one of them — e.g. ``WebsiteAuditTool`` — doesn't import the rest, several
of which pull in heavy dependency trees (browser automation, deep research,
code/document review). ``from src.tool.workflow_tools import X`` still works.
"""
import importlib
import sys

_LAZY = {
    "BrowserTool": ".browser",
    "DeepResearcherTool": ".deep_researcher",
    "DeepAnalyzerTool": ".deep_analyzer",
    "ReportTool": ".report",
    "WebsiteAuditTool": ".website_audit",
    "CodeReviewTool": ".code_review",
    "DocumentReviewTool": ".document_review",
}

__all__ = list(_LAZY)


def __getattr__(name: str):
    try:
        module_name = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(module_name, __name__), name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))
